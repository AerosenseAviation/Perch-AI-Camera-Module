from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from debrief.config import load_config
from debrief.llm import set_stub

HAS_FFMPEG = shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None

needs_ffmpeg = pytest.mark.skipif(not HAS_FFMPEG, reason="ffmpeg/ffprobe not installed")


def _encode(
    path: Path,
    *,
    seconds: int,
    size: str,
    with_audio: bool,
    pattern: str = "testsrc2",
    start: float = 0.0,
    tone: int = 330,
) -> Path:
    """Render a short clip.

    ``start`` trims the leading seconds, which is how the two-camera fixtures
    simulate one camera having been started later than the other.
    """
    cmd = ["ffmpeg", "-v", "error", "-y"]
    cmd += ["-f", "lavfi", "-i", f"{pattern}=size={size}:rate=10:duration={seconds}"]
    if with_audio:
        cmd += ["-f", "lavfi", "-i", f"sine=frequency={tone}:duration={seconds}"]
    if start:
        cmd += ["-ss", f"{start:.3f}"]
    cmd += ["-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p"]
    cmd += ["-c:a", "aac", "-shortest"] if with_audio else ["-an"]
    cmd += [str(path)]
    subprocess.run(cmd, check=True, capture_output=True)
    return path


@pytest.fixture(scope="session")
def clip_dir(tmp_path_factory) -> Path:
    return tmp_path_factory.mktemp("clips")


@pytest.fixture(scope="session")
def clip(clip_dir: Path) -> Path:
    """A short scene clip with audio."""
    if not HAS_FFMPEG:
        pytest.skip("ffmpeg not installed")
    return _encode(clip_dir / "clip.mp4", seconds=24, size="640x360", with_audio=True)


@pytest.fixture(scope="session")
def panel_clip(clip_dir: Path) -> Path:
    """An instrument-sensor clip covering the same flight, started together."""
    if not HAS_FFMPEG:
        pytest.skip("ffmpeg not installed")
    return _encode(
        clip_dir / "clip-panel.mp4",
        seconds=24,
        size="480x480",
        with_audio=True,
        pattern="smptebars",
        tone=440,
    )


@pytest.fixture(scope="session")
def silent_clip(clip_dir: Path) -> Path:
    """A short clip with no audio stream."""
    if not HAS_FFMPEG:
        pytest.skip("ffmpeg not installed")
    return _encode(clip_dir / "silent.mp4", seconds=18, size="480x270", with_audio=False)


@pytest.fixture
def cfg(tmp_path: Path):
    """A config pointed entirely at the test's tmp directory."""
    config = load_config()
    config.paths.runs_root = str(tmp_path / "runs")
    config.paths.cache_dir = str(tmp_path / "cache")
    config.sample.interval_seconds = 3.0
    config.sample.max_frames = 40
    config.sample.panel.interval_seconds = 3.0
    config.sample.panel.max_frames = 40
    config.segment.vision_interval_seconds = 6.0  # the test clips are seconds long
    config.audio.transcribe = False  # faster-whisper is optional; never in unit tests
    return config


@pytest.fixture(autouse=True)
def _clear_stub():
    yield
    set_stub(None)
