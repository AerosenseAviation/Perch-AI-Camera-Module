"""Stage 3 — sample.

Extract frames from every stream at its own rate, scaled to the long edge the
vision model wants, into ``frames/<role>/``.

The two streams are sampled differently on purpose. The scene changes slowly —
terrain, weather, attitude — so three seconds is plenty. The panel changes in a
moment: a switch moves, a needle swings, a light comes on. It is sampled faster
and kept sharper, because a needle has to survive JPEG to be readable.

Frame filenames carry the timestamp **on the run timeline**, not in the source
file, so a panel frame and a scene frame with the same number are the same
moment in the flight.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import time
from pathlib import Path
from typing import Optional

from ..models import FrameIndex, FrameRef, Probe, ProbeResult, StageRecord, StreamFrames
from ..runs import RunContext, require_binary

NAME = "sample"

FRAME_PATTERN = re.compile(r"^f_(-?\d+\.\d+)\.jpg$")


def choose_interval(duration: float, base_interval: float, max_frames: int) -> float:
    """Return the sampling interval that keeps the frame count under the cap."""
    if duration <= 0 or max_frames <= 0:
        return base_interval
    required = duration / max_frames
    return max(base_interval, required)


def jpeg_qscale(quality: int) -> int:
    """Map a 0-100 quality to ffmpeg's inverted 2-31 mjpeg scale."""
    quality = max(1, min(100, quality))
    return max(2, min(31, round(2 + (100 - quality) * (31 - 2) / 99)))


def frame_name(t: float) -> str:
    return f"f_{t:09.2f}.jpg" if t >= 0 else f"f_{t:010.2f}.jpg"


def frame_time(path: Path) -> float:
    match = FRAME_PATTERN.match(path.name)
    if not match:
        raise ValueError(f"not a sampled frame: {path.name}")
    return float(match.group(1))


def load_frames(frames_dir: Path, role: str = "scene") -> list[FrameRef]:
    """Read one stream's frames straight off disk, so the folder is the truth."""
    stream_dir = Path(frames_dir) / role
    if not stream_dir.is_dir():
        return []
    refs = [
        FrameRef(t=frame_time(path), file=path.name)
        for path in stream_dir.iterdir()
        if FRAME_PATTERN.match(path.name)
    ]
    refs.sort(key=lambda r: r.t)
    return refs


def frames_between(frames: list[FrameRef], start: float, end: float) -> list[FrameRef]:
    return [f for f in frames if start <= f.t < end]


def nearest_frame(
    frames: list[FrameRef], t: float, *, within: Optional[float] = None
) -> Optional[FrameRef]:
    if not frames:
        return None
    best = min(frames, key=lambda f: abs(f.t - t))
    if within is not None and abs(best.t - t) > within:
        return None
    return best


def pair_streams(
    scene: list[FrameRef], panel: list[FrameRef], *, tolerance: float
) -> list[tuple[FrameRef, FrameRef]]:
    """Match each scene frame to the panel frame nearest it in time."""
    pairs: list[tuple[FrameRef, FrameRef]] = []
    for ref in scene:
        mate = nearest_frame(panel, ref.t, within=tolerance)
        if mate is not None:
            pairs.append((ref, mate))
    return pairs


def extract(
    video: Path,
    out_dir: Path,
    *,
    interval: float,
    long_edge: int,
    quality: int,
    offset: float = 0.0,
    duration: Optional[float] = None,
) -> list[FrameRef]:
    """Cut frames from one file, naming them in run-timeline seconds."""
    binary = require_binary("ffmpeg")
    if out_dir.exists():
        shutil.rmtree(out_dir)
    out_dir.mkdir(parents=True)

    # `gte(iw,ih)` picks the long edge after ffmpeg has applied any rotation
    # from the display matrix; -2 keeps the other edge even.
    scale = f"scale=w='if(gte(iw,ih),{long_edge},-2)':h='if(gte(iw,ih),-2,{long_edge})'"
    cmd = [
        binary,
        "-v",
        "error",
        "-i",
        str(video),
        "-vf",
        f"fps=1/{interval:.6f},{scale}",
        "-vsync",
        "0",
        "-q:v",
        str(jpeg_qscale(quality)),
        "-f",
        "image2",
        str(out_dir / "seq_%06d.jpg"),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg frame extraction failed: {proc.stderr.strip()}")

    # The fps filter emits frame k at source time k * interval. Adding the
    # stream offset puts every filename on the shared run timeline.
    refs: list[FrameRef] = []
    for path in sorted(out_dir.glob("seq_*.jpg")):
        index = int(path.stem.split("_")[1]) - 1
        t = round(index * interval + offset, 2)
        # A frame from before the run timeline starts, or after it ends, has
        # nothing to line up against — drop it rather than cite it.
        if t < 0 or (duration is not None and t > duration):
            path.unlink()
            continue
        target = out_dir / frame_name(t)
        path.rename(target)
        refs.append(FrameRef(t=t, file=target.name))
    refs.sort(key=lambda r: r.t)
    return refs


def sample_stream(ctx: RunContext, stream: ProbeResult, timeline: float) -> StreamFrames:
    settings = ctx.config.sample.for_role(stream.role)
    interval = choose_interval(
        stream.duration, settings.interval_seconds, settings.max_frames
    )
    refs = extract(
        ctx.source_for(stream.role),
        ctx.stream_dir(stream.role),
        interval=interval,
        long_edge=settings.long_edge,
        quality=settings.jpeg_quality,
        offset=stream.offset,
        duration=timeline,
    )
    return StreamFrames(
        role=stream.role,
        interval=round(interval, 4),
        long_edge=settings.long_edge,
        jpeg_quality=settings.jpeg_quality,
        count=len(refs),
        offset=stream.offset,
        frames=refs,
    )


def run(ctx: RunContext) -> StageRecord:
    started = time.time()
    probe = ctx.read_json("probe.json", Probe)

    index = FrameIndex(
        streams=[sample_stream(ctx, stream, probe.duration) for stream in probe.streams]
    )
    ctx.write_json("frames.json", index)

    for stream in index.streams:
        settings = ctx.config.sample.for_role(stream.role)
        stretched = (
            " (stretched to stay under the cap)"
            if stream.interval > settings.interval_seconds
            else ""
        )
        ctx.say(
            f"  sample: {stream.count} {stream.role} frames every "
            f"{stream.interval:.1f}s at {stream.long_edge}px{stretched}"
        )

    detail = ", ".join(f"{s.count} {s.role}" for s in index.streams)
    return StageRecord(
        name=NAME, status="ok", seconds=round(time.time() - started, 3), detail=detail
    )
