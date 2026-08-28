"""Stage 4 — audio.

Extract a mono 16 kHz WAV, transcribe it if faster-whisper is installed, and
compute a 1 Hz feature track. RMS and spectral centroid are cheap proxies for
engine speed changes and alert tones, and they work even when transcription is
switched off.

``audio_features.csv`` columns:

    time              seconds from the start of the video
    rms               0-1, linear amplitude
    spectral_centroid hertz, the brightness of the spectrum in that second
"""

from __future__ import annotations

import csv
import subprocess
import time
import wave
from pathlib import Path

import numpy as np

from ..models import ProbeResult, StageRecord, Transcript, TranscriptSegment
from ..runs import RunContext, require_binary

NAME = "audio"

FEATURE_COLUMNS = ["time", "rms", "spectral_centroid"]


def extract_wav(video: Path, out: Path, sample_rate: int) -> bool:
    binary = require_binary("ffmpeg")
    cmd = [
        binary,
        "-v",
        "error",
        "-y",
        "-i",
        str(video),
        "-vn",
        "-ac",
        "1",
        "-ar",
        str(sample_rate),
        "-c:a",
        "pcm_s16le",
        str(out),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    return proc.returncode == 0 and out.is_file() and out.stat().st_size > 44


def read_wav(path: Path) -> tuple[np.ndarray, int]:
    with wave.open(str(path), "rb") as wf:
        rate = wf.getframerate()
        width = wf.getsampwidth()
        channels = wf.getnchannels()
        raw = wf.readframes(wf.getnframes())
    dtype = {1: np.int8, 2: np.int16, 4: np.int32}.get(width)
    if dtype is None:
        raise RuntimeError(f"unsupported WAV sample width: {width}")
    samples = np.frombuffer(raw, dtype=dtype).astype(np.float32)
    if channels > 1:
        samples = samples.reshape(-1, channels).mean(axis=1)
    peak = float(np.iinfo(dtype).max)
    return samples / peak, rate


def audio_features(samples: np.ndarray, rate: int, hz: float = 1.0) -> list[dict[str, float]]:
    """RMS level and spectral centroid, one row per 1/hz seconds."""
    window = max(1, int(rate / max(hz, 1e-6)))
    rows: list[dict[str, float]] = []
    freqs = np.fft.rfftfreq(window, d=1.0 / rate)
    for start in range(0, len(samples) - window + 1, window):
        chunk = samples[start : start + window]
        rms = float(np.sqrt(np.mean(np.square(chunk))))
        magnitude = np.abs(np.fft.rfft(chunk * np.hanning(window)))
        total = float(magnitude.sum())
        centroid = float((freqs * magnitude).sum() / total) if total > 1e-9 else 0.0
        rows.append(
            {
                "time": round(start / rate, 3),
                "rms": round(rms, 6),
                "spectral_centroid": round(centroid, 2),
            }
        )
    return rows


def write_features(path: Path, rows: list[dict[str, float]]) -> None:
    with path.open("w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=FEATURE_COLUMNS)
        writer.writeheader()
        writer.writerows(rows)


def read_features(path: Path) -> list[dict[str, float]]:
    if not path.is_file():
        return []
    rows: list[dict[str, float]] = []
    with path.open(newline="") as fh:
        for raw in csv.DictReader(fh):
            try:
                rows.append({k: float(raw[k]) for k in FEATURE_COLUMNS})
            except (KeyError, TypeError, ValueError):
                continue
    return rows


def transcribe(wav: Path, model_name: str, compute_type: str) -> Transcript:
    """Transcribe locally with faster-whisper. Optional by design."""
    try:
        from faster_whisper import WhisperModel
    except ImportError:
        return Transcript(
            available=False,
            note="faster-whisper is not installed; run `pip install 'flight-debrief[transcribe]'`",
        )

    model = WhisperModel(model_name, device="cpu", compute_type=compute_type)
    segments, info = model.transcribe(str(wav), vad_filter=True)
    out = [
        TranscriptSegment(start=round(s.start, 2), end=round(s.end, 2), text=s.text.strip())
        for s in segments
        if s.text and s.text.strip()
    ]
    return Transcript(
        available=True,
        language=getattr(info, "language", None),
        model=model_name,
        segments=out,
    )


def segments_between(
    transcript: Transcript, start: float, end: float
) -> list[TranscriptSegment]:
    return [s for s in transcript.segments if s.end > start and s.start < end]


def run(ctx: RunContext) -> StageRecord:
    started = time.time()
    probe = ctx.read_json("probe.json", ProbeResult)
    cfg = ctx.config.audio

    def finish(status: str, detail: str, transcript: Transcript) -> StageRecord:
        ctx.write_json("transcript.json", transcript)
        return StageRecord(
            name=NAME, status=status, seconds=round(time.time() - started, 3), detail=detail
        )

    features_path = ctx.path("audio_features.csv")

    if ctx.no_audio or not cfg.enabled:
        write_features(features_path, [])
        ctx.say("  audio: skipped by request")
        return finish("skipped", "disabled", Transcript(available=False, note="audio disabled"))

    if not probe.has_audio:
        write_features(features_path, [])
        ctx.say("  audio: the file has no audio stream")
        return finish("skipped", "no audio stream", Transcript(available=False, note="no audio stream"))

    wav = ctx.path("audio.wav")
    if not extract_wav(ctx.video, wav, cfg.sample_rate):
        write_features(features_path, [])
        ctx.say("  audio: could not extract a WAV")
        return finish(
            "skipped", "wav extraction failed", Transcript(available=False, note="wav extraction failed")
        )

    samples, rate = read_wav(wav)
    rows = audio_features(samples, rate, cfg.feature_hz)
    write_features(features_path, rows)

    if cfg.transcribe:
        transcript = transcribe(wav, cfg.whisper_model, cfg.whisper_compute_type)
    else:
        transcript = Transcript(available=False, note="transcription disabled in config")

    detail = f"{len(rows)} feature rows, {len(transcript.segments)} transcript segments"
    ctx.say(f"  audio: {detail}")
    if not transcript.available and transcript.note:
        ctx.say(f"         {transcript.note}")
    return finish("ok", detail, transcript)
