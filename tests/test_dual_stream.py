"""The two-sensor rig: alignment, per-stream sampling, aim check, pairing."""

from __future__ import annotations

import numpy as np
import pytest

from perch.config import SyncConfig
from perch.models import FrameIndex, FrameRef, PanelAim, Probe
from perch.pipeline import build_context
from perch.stages import probe as probe_stage
from perch.stages import sample as sample_stage

from .conftest import needs_ffmpeg


@pytest.fixture
def dual_ctx(clip, panel_clip, cfg, tmp_path):
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    return build_context(clip, run_dir, cfg, panel_video=panel_clip, verbose=False)


# --- alignment ---------------------------------------------------------------


def _tone_file(path, *, rate=4000, seconds=30.0, lead=0.0):
    """Write a WAV whose envelope has distinctive bursts, offset by ``lead``."""
    import wave

    n = int(rate * seconds)
    signal = np.zeros(n, dtype=np.float32)
    rng = np.random.default_rng(7)
    # Bursts at irregular intervals give the correlation something to lock onto.
    for mark in (3.0, 7.5, 11.0, 18.25, 23.0):
        start = int((mark + lead) * rate)
        end = min(n, start + int(0.4 * rate))
        if 0 <= start < n:
            signal[start:end] = rng.normal(0, 0.6, end - start)
    signal += rng.normal(0, 0.01, n).astype(np.float32)

    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(rate)
        wf.writeframes((np.clip(signal, -1, 1) * 32767).astype(np.int16).tobytes())
    return path


@needs_ffmpeg
@pytest.mark.parametrize("lead", [0.0, 5.0, -4.0])
def test_audio_alignment_recovers_a_known_offset(tmp_path, lead):
    """The panel camera started ``lead`` seconds later than the scene camera.

    An event at scene time t is then at panel time t - lead, so the offset the
    pipeline needs (panel time -> scene time) is +lead.
    """
    scene = _tone_file(tmp_path / "scene.wav", lead=0.0)
    panel = _tone_file(tmp_path / "panel.wav", lead=-lead)

    result = probe_stage.align_audio(scene, panel, SyncConfig(max_offset_seconds=15.0))

    assert result.method == "audio"
    assert result.offset == pytest.approx(lead, abs=0.15)


@needs_ffmpeg
def test_alignment_refuses_to_guess_from_unrelated_audio(tmp_path):
    import wave

    rate = 4000
    rng = np.random.default_rng(1)
    for name in ("a.wav", "b.wav"):
        with wave.open(str(tmp_path / name), "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(rate)
            noise = rng.normal(0, 0.2, rate * 20)
            wf.writeframes((np.clip(noise, -1, 1) * 32767).astype(np.int16).tobytes())

    # The default threshold, not a contrived one: unrelated audio must be
    # rejected by the setting that ships.
    result = probe_stage.align_audio(tmp_path / "a.wav", tmp_path / "b.wav", SyncConfig())
    assert result.method == "assumed"
    assert result.offset == 0.0
    assert "too weak" in (result.note or "")


@needs_ffmpeg
def test_a_true_alignment_clears_the_default_threshold(tmp_path):
    """The shipped threshold has to accept real alignments, not just reject noise."""
    scene = _tone_file(tmp_path / "s.wav", lead=0.0)
    panel = _tone_file(tmp_path / "p.wav", lead=-6.0)

    result = probe_stage.align_audio(scene, panel, SyncConfig())
    assert result.method == "audio"
    assert result.offset == pytest.approx(6.0, abs=0.15)
    assert result.confidence > SyncConfig().min_confidence


@needs_ffmpeg
def test_a_manual_offset_skips_the_audio_pass(clip, panel_clip, cfg, tmp_path):
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    ctx = build_context(
        clip, run_dir, cfg, panel_video=panel_clip, panel_offset=7.5, verbose=False
    )
    probe_stage.run(ctx)

    probe = ctx.read_json("probe.json", Probe)
    assert probe.sync and probe.sync.method == "manual"
    assert probe.panel.offset == 7.5


# --- probe -------------------------------------------------------------------


@needs_ffmpeg
def test_probe_records_both_streams(dual_ctx):
    probe_stage.run(dual_ctx)
    probe = dual_ctx.read_json("probe.json", Probe)

    assert probe.has_panel
    assert {s.role for s in probe.streams} == {"scene", "panel"}
    assert probe.scene.offset == 0.0
    assert probe.duration == pytest.approx(probe.scene.duration)
    # The scene camera defines the timeline and is preferred for audio.
    assert probe.audio_stream().role == "scene"


# --- sampling ----------------------------------------------------------------


@needs_ffmpeg
def test_each_stream_is_sampled_into_its_own_folder(dual_ctx):
    probe_stage.run(dual_ctx)
    record = sample_stage.run(dual_ctx)
    assert record.status == "ok"

    index = dual_ctx.read_json("frames.json", FrameIndex)
    assert {s.role for s in index.streams} == {"scene", "panel"}

    scene = sample_stage.load_frames(dual_ctx.frames_dir, "scene")
    panel = sample_stage.load_frames(dual_ctx.frames_dir, "panel")
    assert scene and panel
    assert (dual_ctx.stream_dir("scene") / scene[0].file).is_file()
    assert (dual_ctx.stream_dir("panel") / panel[0].file).is_file()


@needs_ffmpeg
def test_the_panel_stream_is_kept_sharper_than_the_scene(dual_ctx):
    """A needle has to survive JPEG, so the instrument sensor gets more pixels."""
    import json
    import subprocess

    dual_ctx.config.sample.panel.long_edge = 1024
    probe_stage.run(dual_ctx)
    sample_stage.run(dual_ctx)

    def long_edge(role: str) -> int:
        frame = sorted(dual_ctx.stream_dir(role).glob("f_*.jpg"))[0]
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-show_streams", "-print_format", "json", str(frame)],
            capture_output=True,
            text=True,
            check=True,
        )
        stream = json.loads(out.stdout)["streams"][0]
        return max(stream["width"], stream["height"])

    assert long_edge("panel") > long_edge("scene")


@needs_ffmpeg
def test_an_offset_moves_panel_frames_onto_the_run_timeline(
    clip, panel_clip, cfg, tmp_path
):
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    ctx = build_context(
        clip, run_dir, cfg, panel_video=panel_clip, panel_offset=6.0, verbose=False
    )
    probe_stage.run(ctx)
    sample_stage.run(ctx)

    panel = sample_stage.load_frames(ctx.frames_dir, "panel")
    assert panel
    # The panel camera started 6s into the flight, so its first frame is at 6s.
    assert panel[0].t == pytest.approx(6.0)
    # And nothing may sit outside the timeline the scene stream defines.
    assert all(0 <= f.t <= ctx.read_json("probe.json", Probe).duration for f in panel)


@needs_ffmpeg
def test_frames_before_the_timeline_start_are_dropped(clip, panel_clip, cfg, tmp_path):
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    ctx = build_context(
        clip, run_dir, cfg, panel_video=panel_clip, panel_offset=-9.0, verbose=False
    )
    probe_stage.run(ctx)
    sample_stage.run(ctx)

    panel = sample_stage.load_frames(ctx.frames_dir, "panel")
    assert panel, "the overlapping part of the panel stream should survive"
    assert all(f.t >= 0 for f in panel)


def test_negative_frame_names_round_trip():
    from pathlib import Path

    name = sample_stage.frame_name(-3.5)
    assert sample_stage.frame_time(Path(name)) == -3.5


# --- pairing -----------------------------------------------------------------


def test_pairing_matches_the_nearest_panel_frame():
    scene = [FrameRef(t=t, file=f"s{t}.jpg") for t in (0.0, 3.0, 6.0, 9.0)]
    panel = [FrameRef(t=t, file=f"p{t}.jpg") for t in (0.4, 3.2, 8.9)]

    pairs = sample_stage.pair_streams(scene, panel, tolerance=1.0)
    assert [(s.t, p.t) for s, p in pairs] == [(0.0, 0.4), (3.0, 3.2), (9.0, 8.9)]
    # t=6.0 has no panel frame within tolerance, so it is not paired at all.


def test_pairing_drops_everything_when_the_streams_do_not_overlap():
    scene = [FrameRef(t=t, file="s.jpg") for t in (0.0, 3.0)]
    panel = [FrameRef(t=t, file="p.jpg") for t in (600.0, 603.0)]
    assert sample_stage.pair_streams(scene, panel, tolerance=2.0) == []


def test_nearest_frame_respects_the_tolerance():
    frames = [FrameRef(t=10.0, file="a.jpg")]
    assert sample_stage.nearest_frame(frames, 12.0, within=5.0) is not None
    assert sample_stage.nearest_frame(frames, 30.0, within=5.0) is None


# --- the aim check -----------------------------------------------------------


def test_panel_aim_usable_only_when_in_frame_and_legible():
    assert PanelAim(in_frame="full", legible="clear").usable is True
    assert PanelAim(in_frame="partial", legible="marginal").usable is True
    assert PanelAim(in_frame="none", legible="clear").usable is False
    assert PanelAim(in_frame="full", legible="illegible").usable is False
    assert PanelAim().usable is False  # the default is "not proven readable"


def test_the_envelope_high_pass_leaves_no_edge_artefact():
    """Regression: a zero-padded rolling mean creates a false zero-lag match.

    The high-pass that removes slow engine drift is a rolling-mean subtraction.
    Computed with zero padding, it leaves a large artefact of identical shape at
    both ends of every envelope — and those artefacts correlate almost perfectly
    at zero lag, silently beating the true alignment.
    """
    from perch.stages.probe import _envelope

    rate = 4000
    rng = np.random.default_rng(11)
    # A steady hum: after a correct high-pass this is near-flat everywhere,
    # including at the edges.
    t = np.arange(rate * 30) / rate
    hum = (0.3 * np.sin(2 * np.pi * 95 * t) + 0.01 * rng.normal(0, 1, t.size)).astype(np.float32)

    env = _envelope(hum, rate)
    assert env.size > 100

    edge = int(2.0 * (rate / max(1, rate // 50)))  # two seconds of envelope
    middle_spread = float(np.std(env[edge:-edge]))
    edge_spread = float(np.std(np.concatenate([env[:edge], env[-edge:]])))

    # The edges must not be dramatically louder than the middle.
    assert edge_spread < middle_spread * 4, (
        f"edge artefact: edges vary {edge_spread:.2f} vs {middle_spread:.2f} in the middle"
    )


def test_unrelated_steady_audio_is_not_aligned_at_zero_lag(tmp_path):
    """Two independent hums must not be declared aligned just because both are hums."""
    import wave

    rate = 4000
    rng = np.random.default_rng(5)
    for name, freq in (("h1.wav", 95.0), ("h2.wav", 110.0)):
        t = np.arange(rate * 30) / rate
        sig = 0.3 * np.sin(2 * np.pi * freq * t) + 0.02 * rng.normal(0, 1, t.size)
        with wave.open(str(tmp_path / name), "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(rate)
            wf.writeframes((np.clip(sig, -1, 1) * 32767).astype(np.int16).tobytes())

    result = probe_stage.align_audio(tmp_path / "h1.wav", tmp_path / "h2.wav", SyncConfig())
    assert result.method == "assumed"


def test_confidence_is_a_correlation_coefficient(tmp_path):
    """A true alignment scores near 1; the number is interpretable, not a heuristic."""
    scene = _tone_file(tmp_path / "s.wav", lead=0.0)
    panel = _tone_file(tmp_path / "p.wav", lead=-3.0)

    result = probe_stage.align_audio(scene, panel, SyncConfig())
    assert result.offset == pytest.approx(3.0, abs=0.15)
    assert 0.8 <= result.confidence <= 1.0
