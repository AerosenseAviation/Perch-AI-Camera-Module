"""Stage 1 — probe.

Run ffprobe and record what the container holds: duration, resolution, frame
rate, rotation, the audio stream, and whether there is a GoPro ``gpmd``
telemetry track.
"""

from __future__ import annotations

import json
import subprocess
import time
from pathlib import Path
from typing import Any, Optional

from ..models import AudioStreamInfo, ProbeResult, StageRecord
from ..runs import RunContext, require_binary

NAME = "probe"


def _ffprobe(video: Path) -> dict[str, Any]:
    binary = require_binary("ffprobe")
    cmd = [
        binary,
        "-v",
        "error",
        "-show_format",
        "-show_streams",
        "-print_format",
        "json",
        str(video),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(f"ffprobe failed on {video}: {proc.stderr.strip()}")
    return json.loads(proc.stdout)


def _fraction(value: Optional[str], default: float = 0.0) -> float:
    if not value:
        return default
    if "/" in value:
        num, _, den = value.partition("/")
        try:
            n, d = float(num), float(den)
        except ValueError:
            return default
        return n / d if d else default
    try:
        return float(value)
    except ValueError:
        return default


def _rotation(stream: dict[str, Any]) -> int:
    for side in stream.get("side_data_list", []) or []:
        if "rotation" in side:
            try:
                return int(round(float(side["rotation"]))) % 360
            except (TypeError, ValueError):
                pass
    tag = (stream.get("tags") or {}).get("rotate")
    if tag is not None:
        try:
            return int(round(float(tag))) % 360
        except (TypeError, ValueError):
            pass
    return 0


def _is_telemetry(stream: dict[str, Any]) -> bool:
    if stream.get("codec_type") != "data":
        return False
    handler = ((stream.get("tags") or {}).get("handler_name") or "").lower()
    codec_tag = (stream.get("codec_tag_string") or "").lower()
    return "gpmd" in handler or "gpmf" in handler or codec_tag == "gpmd"


def probe_file(video: Path) -> ProbeResult:
    """Probe a video without needing a run directory. Used by ``debrief probe``."""
    video = Path(video)
    if not video.is_file():
        raise FileNotFoundError(f"no such video: {video}")

    data = _ffprobe(video)
    fmt = data.get("format", {})
    streams = data.get("streams", [])

    video_streams = [s for s in streams if s.get("codec_type") == "video"]
    if not video_streams:
        raise RuntimeError(f"{video} has no video stream")
    v = video_streams[0]

    duration = _fraction(fmt.get("duration")) or _fraction(v.get("duration"))
    if duration <= 0:
        # Some MOV files omit the format duration; fall back to frames / rate.
        frames = _fraction(v.get("nb_frames"))
        rate = _fraction(v.get("avg_frame_rate"))
        duration = frames / rate if frames and rate else 0.0

    audio_streams = [s for s in streams if s.get("codec_type") == "audio"]
    audio: Optional[AudioStreamInfo] = None
    if audio_streams:
        a = audio_streams[0]
        audio = AudioStreamInfo(
            index=int(a.get("index", 0)),
            codec=a.get("codec_name"),
            channels=a.get("channels"),
            sample_rate=int(a["sample_rate"]) if a.get("sample_rate") else None,
            duration=_fraction(a.get("duration")) or None,
        )

    telemetry_streams = [s for s in streams if _is_telemetry(s)]

    return ProbeResult(
        path=str(video.resolve()),
        filename=video.name,
        size_bytes=int(fmt.get("size") or video.stat().st_size),
        container=fmt.get("format_name"),
        duration=round(duration, 3),
        width=int(v.get("width") or 0),
        height=int(v.get("height") or 0),
        fps=round(_fraction(v.get("avg_frame_rate")) or _fraction(v.get("r_frame_rate")), 4),
        rotation=_rotation(v),
        has_audio=bool(audio_streams),
        audio=audio,
        has_telemetry=bool(telemetry_streams),
        telemetry_stream_index=(
            int(telemetry_streams[0].get("index", 0)) if telemetry_streams else None
        ),
        telemetry_handler=(
            (telemetry_streams[0].get("tags") or {}).get("handler_name")
            if telemetry_streams
            else None
        ),
    )


def run(ctx: RunContext) -> StageRecord:
    started = time.time()
    result = probe_file(ctx.video)
    ctx.write_json("probe.json", result)
    ctx.say(
        f"  probe: {result.duration:.0f}s  {result.width}x{result.height} @ {result.fps:.2f}fps"
        f"  audio={'yes' if result.has_audio else 'no'}"
        f"  telemetry={'yes' if result.has_telemetry else 'no'}"
    )
    return StageRecord(
        name=NAME,
        status="ok",
        seconds=round(time.time() - started, 3),
        detail=f"{result.duration:.0f}s, {result.width}x{result.height}",
    )
