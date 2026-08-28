"""Stage 1 — probe.

Run ffprobe on every stream and record what the containers hold. Then establish
the run timeline.

The scene stream defines t=0. Two independent cameras start at different
moments, so the panel stream carries an offset into scene time. That offset can
be given by hand, but it is better measured: both cameras hear the same engine
and the same radio, so cross-correlating their audio recovers the true
alignment. Everything downstream cites timestamps, so getting this wrong
corrupts the whole debrief.
"""

from __future__ import annotations

import json
import subprocess
import time
from pathlib import Path
from typing import Any, Optional

import numpy as np

from ..config import SyncConfig
from ..models import (
    AudioStreamInfo,
    Probe,
    ProbeResult,
    StageRecord,
    SyncResult,
)
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


def probe_file(video: Path, *, role: str = "scene", offset: float = 0.0) -> ProbeResult:
    """Probe one video file. Used directly by ``debrief probe``."""
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
        role=role,  # type: ignore[arg-type]
        offset=offset,
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


# --- audio alignment ---------------------------------------------------------


def _decimated_audio(video: Path, rate: int, seconds: Optional[float] = None) -> np.ndarray:
    """Pull a mono, low-rate float track for correlation only."""
    binary = require_binary("ffmpeg")
    cmd = [binary, "-v", "error", "-i", str(video), "-vn", "-ac", "1", "-ar", str(rate)]
    if seconds:
        cmd += ["-t", f"{seconds:.3f}"]
    cmd += ["-f", "s16le", "-"]
    proc = subprocess.run(cmd, capture_output=True)
    if proc.returncode != 0 or not proc.stdout:
        return np.zeros(0, dtype=np.float32)
    return np.frombuffer(proc.stdout, dtype=np.int16).astype(np.float32) / 32768.0


def _envelope(samples: np.ndarray, rate: int) -> np.ndarray:
    """Short-term energy envelope, which survives codec and mic differences.

    Two cameras hear the same events with very different frequency response, so
    correlating raw waveforms is unreliable. The loudness envelope — engine
    surges, radio calls, the gear thumping — matches far better.
    """
    window = max(1, rate // 50)  # 20 ms
    if samples.size < window * 4:
        return np.zeros(0, dtype=np.float32)
    usable = samples.size - (samples.size % window)
    frames = samples[:usable].reshape(-1, window)
    energy = np.sqrt(np.mean(np.square(frames), axis=1))
    energy = np.log1p(energy * 100.0)

    # High-pass the envelope by subtracting a ~5 s rolling mean. Engine power
    # drifts over minutes, and that slow component dominates the correlation and
    # smears the peak across tens of seconds. What actually pins the alignment
    # is the transients — radio calls, a power change, the gear thumping — so
    # remove the drift and let them do the work.
    env_rate = rate / window
    k = max(3, int(5.0 * env_rate))
    if energy.size > k:
        # Divide by the count of contributing samples rather than by k, so the
        # first and last half-window get a true local mean. A zero-padded edge
        # leaves a large artefact of identical shape in both envelopes, and the
        # two artefacts then correlate almost perfectly at zero lag — which
        # silently beats the real alignment.
        kernel = np.ones(k)
        counts = np.convolve(np.ones_like(energy), kernel, mode="same")
        baseline = np.convolve(energy, kernel, mode="same") / counts
        energy = energy - baseline

    energy -= energy.mean()
    std = energy.std()
    return (energy / std) if std > 1e-9 else np.zeros_like(energy)


def _pearson(a: np.ndarray, b: np.ndarray, lag: int) -> float:
    """Correlation coefficient of the two envelopes shifted by ``lag`` samples."""
    if lag >= 0:
        x, y = a[lag:], b[: a.size - lag]
    else:
        x, y = a[: b.size + lag], b[-lag:]
    n = min(x.size, y.size)
    if n < 10:
        return 0.0
    x, y = x[:n] - x[:n].mean(), y[:n] - y[:n].mean()
    denominator = float(np.sqrt((x * x).sum() * (y * y).sum()))
    return float((x * y).sum() / denominator) if denominator > 1e-12 else 0.0


def align_audio(scene: Path, panel: Path, cfg: SyncConfig) -> SyncResult:
    """Recover the panel stream's offset into scene time from the two audio tracks.

    The offset is the number of seconds to add to a panel-file timestamp to get
    scene time. If the panel camera was started after the scene camera it is
    positive; if it was rolling first, negative.

    Derivation of the sign: for an event at scene time ``m`` and panel time
    ``n`` we have ``a[n + D] == b[n]`` with ``D = offset * rate``, and
    ``np.correlate(a, b, "full")`` peaks where ``lags == D``.
    """
    scene_samples = _decimated_audio(scene, cfg.sample_rate, cfg.window_seconds)
    panel_samples = _decimated_audio(panel, cfg.sample_rate, cfg.window_seconds)
    if scene_samples.size == 0 or panel_samples.size == 0:
        return SyncResult(method="assumed", offset=0.0, note="one stream has no usable audio")

    a = _envelope(scene_samples, cfg.sample_rate)
    b = _envelope(panel_samples, cfg.sample_rate)
    if a.size < 50 or b.size < 50:
        return SyncResult(method="assumed", offset=0.0, note="not enough audio to correlate")

    env_rate = cfg.sample_rate / max(1, cfg.sample_rate // 50)
    correlation = np.correlate(a, b, mode="full")
    lags = np.arange(-(b.size - 1), a.size)

    # Two lags are worthless: those beyond the plausible offset window, and
    # those where the series barely overlap. A one-sample overlap correlates
    # perfectly with anything, so without a floor the extreme lags always win.
    overlap = min(a.size, b.size) - np.abs(lags)
    min_overlap = max(int(10 * env_rate), int(0.25 * min(a.size, b.size)))
    keep = (np.abs(lags) <= int(cfg.max_offset_seconds * env_rate)) & (overlap >= min_overlap)
    if not keep.any():
        return SyncResult(
            method="assumed",
            offset=0.0,
            note="the two recordings are too short, or too far apart, to overlap usefully",
        )
    correlation, lags, overlap = correlation[keep], lags[keep], overlap[keep]

    peak = int(np.argmax(correlation / overlap))
    offset = float(lags[peak] / env_rate)

    # Score the winner with the exact correlation coefficient of the overlapping
    # region. That is directly interpretable and needs no tuned heuristic:
    # measured, a true alignment scores above 0.9 and unrelated audio about 0.12.
    confidence = _pearson(a, b, int(lags[peak]))
    if confidence < cfg.min_confidence:
        return SyncResult(
            method="assumed",
            offset=0.0,
            confidence=round(confidence, 4),
            note="audio correlation too weak to trust; assuming the cameras started together",
        )
    return SyncResult(
        method="audio",
        offset=round(offset, 3),
        confidence=round(confidence, 4),
        note=f"aligned on {min(a.size, b.size) / env_rate:.0f}s of audio envelope",
    )


# --- stage entry point -------------------------------------------------------


def run(ctx: RunContext) -> StageRecord:
    started = time.time()

    scene = probe_file(ctx.video, role="scene")
    streams = [scene]
    sync: Optional[SyncResult] = None

    if ctx.panel_video:
        if ctx.panel_offset is not None:
            sync = SyncResult(
                method="manual", offset=ctx.panel_offset, note="offset given on the command line"
            )
        elif ctx.auto_sync and ctx.config.sync.enabled:
            panel_probe = probe_file(ctx.panel_video, role="panel")
            if scene.has_audio and panel_probe.has_audio:
                sync = align_audio(ctx.video, ctx.panel_video, ctx.config.sync)
            else:
                sync = SyncResult(
                    method="assumed",
                    offset=0.0,
                    note="one stream has no audio track to align on",
                )
        else:
            sync = SyncResult(
                method="assumed", offset=0.0, note="auto-sync disabled; assuming a common start"
            )
        streams.append(probe_file(ctx.panel_video, role="panel", offset=sync.offset))

    probe = Probe(streams=streams, duration=scene.duration, sync=sync)
    ctx.write_json("probe.json", probe)

    ctx.say(
        f"  probe: scene {scene.duration:.0f}s  {scene.width}x{scene.height} @ {scene.fps:.2f}fps"
        f"  audio={'yes' if scene.has_audio else 'no'}"
        f"  telemetry={'yes' if probe.has_telemetry else 'no'}"
    )
    if probe.panel:
        panel = probe.panel
        ctx.say(
            f"         panel {panel.duration:.0f}s  {panel.width}x{panel.height}"
            f"  offset {panel.offset:+.2f}s via {sync.method if sync else 'assumed'}"
            + (
                f" (confidence {sync.confidence:.2f})"
                if sync and sync.confidence is not None
                else ""
            )
        )
        if sync and sync.method == "assumed":
            ctx.say(f"         warning: {sync.note}")

    detail = f"{scene.duration:.0f}s, {len(streams)} stream(s)"
    return StageRecord(
        name=NAME, status="ok", seconds=round(time.time() - started, 3), detail=detail
    )
