"""Stage 3 — sample.

Extract frames at a fixed interval, scaled down to the long edge the vision
models want. The frame budget is the main cost lever in the whole pipeline, so
the interval stretches on long flights rather than the cap being exceeded.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import time
from pathlib import Path

from ..models import FrameIndex, FrameRef, ProbeResult, StageRecord
from ..runs import RunContext, require_binary

NAME = "sample"

FRAME_PATTERN = re.compile(r"^f_(\d+\.\d+)\.jpg$")


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
    return f"f_{t:09.2f}.jpg"


def frame_time(path: Path) -> float:
    match = FRAME_PATTERN.match(path.name)
    if not match:
        raise ValueError(f"not a sampled frame: {path.name}")
    return float(match.group(1))


def load_frames(frames_dir: Path) -> list[FrameRef]:
    """Read the frame set straight off disk, so the folder is the source of truth."""
    if not frames_dir.is_dir():
        return []
    refs: list[FrameRef] = []
    for path in frames_dir.iterdir():
        if FRAME_PATTERN.match(path.name):
            refs.append(FrameRef(t=frame_time(path), file=path.name))
    refs.sort(key=lambda r: r.t)
    return refs


def frames_between(frames: list[FrameRef], start: float, end: float) -> list[FrameRef]:
    return [f for f in frames if start <= f.t < end]


def nearest_frame(frames: list[FrameRef], t: float) -> FrameRef | None:
    if not frames:
        return None
    return min(frames, key=lambda f: abs(f.t - t))


def extract(
    video: Path,
    frames_dir: Path,
    *,
    interval: float,
    long_edge: int,
    quality: int,
) -> list[FrameRef]:
    binary = require_binary("ffmpeg")
    if frames_dir.exists():
        shutil.rmtree(frames_dir)
    frames_dir.mkdir(parents=True)

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
        str(frames_dir / "seq_%06d.jpg"),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg frame extraction failed: {proc.stderr.strip()}")

    # The fps filter emits frame k at source time k * interval; rename so the
    # timestamp is carried by the filename and nothing downstream needs an index.
    refs: list[FrameRef] = []
    for path in sorted(frames_dir.glob("seq_*.jpg")):
        index = int(path.stem.split("_")[1]) - 1
        t = round(index * interval, 2)
        target = frames_dir / frame_name(t)
        path.rename(target)
        refs.append(FrameRef(t=t, file=target.name))
    return refs


def run(ctx: RunContext) -> StageRecord:
    started = time.time()
    probe = ctx.read_json("probe.json", ProbeResult)
    cfg = ctx.config.sample

    interval = choose_interval(probe.duration, cfg.interval_seconds, cfg.max_frames)
    refs = extract(
        ctx.video,
        ctx.frames_dir,
        interval=interval,
        long_edge=cfg.long_edge,
        quality=cfg.jpeg_quality,
    )

    index = FrameIndex(
        interval=round(interval, 4),
        long_edge=cfg.long_edge,
        jpeg_quality=cfg.jpeg_quality,
        count=len(refs),
        frames=refs,
    )
    ctx.write_json("frames.json", index)

    stretched = " (stretched to stay under the cap)" if interval > cfg.interval_seconds else ""
    ctx.say(f"  sample: {len(refs)} frames every {interval:.1f}s{stretched}")
    return StageRecord(
        name=NAME,
        status="ok",
        seconds=round(time.time() - started, 3),
        detail=f"{len(refs)} frames at {interval:.1f}s",
    )
