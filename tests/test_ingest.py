"""Stages 1 to 5 — the local ingest. No model calls."""

from __future__ import annotations

import csv
from pathlib import Path

import numpy as np
import pytest

from debrief.models import FrameIndex, Phases, Probe, Transcript
from debrief.pipeline import build_context
from debrief.stages import audio as audio_stage
from debrief.stages import probe as probe_stage
from debrief.stages import sample as sample_stage
from debrief.stages import segment as segment_stage
from debrief.stages import telemetry as telemetry_stage

from .conftest import needs_ffmpeg


@pytest.fixture
def ctx(clip, cfg, tmp_path):
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    return build_context(clip, run_dir, cfg, verbose=False)


# --- stage 1 -----------------------------------------------------------------


@needs_ffmpeg
def test_probe_reads_the_container(clip):
    result = probe_stage.probe_file(clip)
    assert 20 < result.duration < 30
    assert (result.width, result.height) == (640, 360)
    assert result.fps == pytest.approx(10.0, abs=0.5)
    assert result.has_audio is True
    assert result.audio and result.audio.channels == 1 or result.audio.channels == 2
    assert result.has_telemetry is False


@needs_ffmpeg
def test_probe_handles_a_file_with_no_audio(silent_clip):
    result = probe_stage.probe_file(silent_clip)
    assert result.has_audio is False
    assert result.audio is None


def test_probe_rejects_a_missing_file(tmp_path):
    with pytest.raises(FileNotFoundError):
        probe_stage.probe_file(tmp_path / "nope.mp4")


# --- stage 2 -----------------------------------------------------------------


def test_probe_stage_writes_a_single_stream_run(ctx):
    probe_stage.run(ctx)
    probe = ctx.read_json("probe.json", Probe)
    assert len(probe.streams) == 1
    assert probe.scene.role == "scene"
    assert probe.panel is None
    assert probe.has_panel is False
    assert probe.duration == pytest.approx(probe.scene.duration)


def test_telemetry_is_empty_but_valid_without_a_gpmd_stream(ctx):
    probe_stage.run(ctx)
    record = telemetry_stage.run(ctx)
    assert record.status == "skipped"
    rows = list(csv.DictReader(ctx.path("telemetry.csv").open()))
    assert rows == []
    assert ctx.path("telemetry.csv").read_text().startswith("time,latitude")


def test_telemetry_groups_exiftool_documents():
    payload = [
        {
            "SourceFile": "a.mp4",
            "Main:Duration": 12.0,
            "Doc1:SampleTime": 0.0,
            "Doc1:GPSLatitude": -33.9,
            "Doc1:GPSLongitude": 18.4,
            "Doc1:GPSAltitude": 120.0,
            "Doc1:GPSSpeed": 30.0,
            "Doc1:Accelerometer": "0.1 9.8 0.2 0.3 9.6 0.1",
            "Doc2:SampleTime": 1.0,
            "Doc2:GPSLatitude": -33.901,
            "Doc2:GPSLongitude": 18.401,
            "Doc2:GPSAltitude": 128.0,
            "Doc2:GPSSpeed": 33.0,
        }
    ]
    rows = telemetry_stage.to_series(telemetry_stage.group_documents(payload), 12.0)
    assert [r["time"] for r in rows] == [0.0, 1.0]
    assert rows[0]["latitude"] == pytest.approx(-33.9)
    assert rows[0]["accel_y"] == pytest.approx(9.7, abs=0.01)
    assert rows[1]["ground_speed"] == 33.0


def test_telemetry_spreads_samples_when_sample_time_is_absent():
    payload = [
        {
            f"Doc{i}:GPSLatitude": -33.0 + i * 0.001,
            f"Doc{i}:GPSLongitude": 18.0,
            f"Doc{i}:GPSSpeed": 25.0,
        }
        for i in range(1, 5)
    ]
    merged = {k: v for record in payload for k, v in record.items()}
    rows = telemetry_stage.to_series(telemetry_stage.group_documents([merged]), 8.0)
    assert [r["time"] for r in rows] == [0.0, 2.0, 4.0, 6.0]


def test_telemetry_csv_round_trips(tmp_path):
    path = tmp_path / "t.csv"
    telemetry_stage.write_csv(
        path,
        [{"time": 1.0, "latitude": -33.9, "longitude": 18.4, "altitude": None,
          "ground_speed": 12.0, "accel_x": None, "accel_y": None, "accel_z": None}],
    )
    rows = telemetry_stage.read_csv(path)
    assert rows == [{"time": 1.0, "latitude": -33.9, "longitude": 18.4, "ground_speed": 12.0}]


# --- stage 3 -----------------------------------------------------------------


def test_interval_stretches_to_hold_the_frame_cap():
    # A three-hour flight at three seconds a frame would be 3600 frames.
    assert sample_stage.choose_interval(10800, 3.0, 400) == pytest.approx(27.0)
    # A short flight keeps the configured rate.
    assert sample_stage.choose_interval(600, 3.0, 400) == 3.0


def test_jpeg_qscale_maps_quality_to_ffmpeg_scale():
    assert sample_stage.jpeg_qscale(100) == 2
    assert sample_stage.jpeg_qscale(1) == 31
    assert 5 <= sample_stage.jpeg_qscale(80) <= 9


def test_frame_name_round_trips():
    name = sample_stage.frame_name(123.45)
    assert name == "f_000123.45.jpg"
    assert sample_stage.frame_time(Path(name)) == 123.45


@needs_ffmpeg
def test_sample_writes_timestamped_frames_under_the_cap(ctx):
    probe_stage.run(ctx)
    record = sample_stage.run(ctx)
    assert record.status == "ok"

    index = ctx.read_json("frames.json", FrameIndex)
    scene = index.by_role("scene")
    assert scene is not None
    assert 0 < scene.count <= ctx.config.sample.max_frames
    assert scene.frames[0].t == 0.0
    assert scene.frames[1].t == pytest.approx(scene.interval)

    on_disk = sample_stage.load_frames(ctx.frames_dir, "scene")
    assert [f.file for f in on_disk] == [f.file for f in scene.frames]
    assert all((ctx.stream_dir("scene") / f.file).stat().st_size > 0 for f in on_disk)


@needs_ffmpeg
def test_sampled_frames_are_scaled_to_the_long_edge(ctx):
    import json
    import subprocess

    probe_stage.run(ctx)
    sample_stage.run(ctx)
    frame = next(iter(sorted(ctx.stream_dir("scene").glob("f_*.jpg"))))
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_streams", "-print_format", "json", str(frame)],
        capture_output=True,
        text=True,
        check=True,
    )
    stream = json.loads(out.stdout)["streams"][0]
    assert max(stream["width"], stream["height"]) == ctx.config.sample.long_edge


def test_frames_between_and_nearest():
    frames = [sample_stage.FrameRef(t=t, file=f"f_{t}.jpg") for t in (0.0, 3.0, 6.0, 9.0)]
    assert [f.t for f in sample_stage.frames_between(frames, 3.0, 9.0)] == [3.0, 6.0]
    assert sample_stage.nearest_frame(frames, 5.0).t == 6.0
    assert sample_stage.nearest_frame([], 5.0) is None


# --- stage 4 -----------------------------------------------------------------


def test_audio_features_track_level_and_brightness():
    rate = 16000
    t = np.arange(rate * 4) / rate
    quiet = 0.05 * np.sin(2 * np.pi * 200 * t[: rate * 2])
    loud = 0.5 * np.sin(2 * np.pi * 2000 * t[: rate * 2])
    rows = audio_stage.audio_features(np.concatenate([quiet, loud]), rate, hz=1.0)

    assert len(rows) == 4
    assert rows[0]["rms"] < rows[3]["rms"]
    assert rows[0]["spectral_centroid"] < rows[3]["spectral_centroid"]
    assert rows[3]["spectral_centroid"] == pytest.approx(2000, rel=0.15)


@needs_ffmpeg
def test_audio_stage_writes_features_and_a_transcript_stub(ctx):
    probe_stage.run(ctx)
    record = audio_stage.run(ctx)
    assert record.status == "ok"

    rows = audio_stage.read_features(ctx.path("audio_features.csv"))
    assert len(rows) > 10
    assert rows[1]["time"] - rows[0]["time"] == pytest.approx(1.0)

    transcript = ctx.read_json("transcript.json", Transcript)
    assert transcript.available is False  # transcription is off in the test config


@needs_ffmpeg
def test_audio_stage_skips_a_silent_file(silent_clip, cfg, tmp_path):
    run_dir = tmp_path / "silent-run"
    run_dir.mkdir()
    ctx = build_context(silent_clip, run_dir, cfg, verbose=False)
    probe_stage.run(ctx)
    record = audio_stage.run(ctx)
    assert record.status == "skipped"
    assert ctx.path("audio_features.csv").is_file()
    assert ctx.read_json("transcript.json", Transcript).available is False


@needs_ffmpeg
def test_no_audio_flag_skips_extraction(clip, cfg, tmp_path):
    run_dir = tmp_path / "noaudio-run"
    run_dir.mkdir()
    ctx = build_context(clip, run_dir, cfg, no_audio=True, verbose=False)
    probe_stage.run(ctx)
    assert audio_stage.run(ctx).status == "skipped"
    assert not ctx.path("audio.wav").exists()


# --- stage 5 -----------------------------------------------------------------


def test_median_filter_removes_a_single_frame_spike():
    labels = ["cruise"] * 4 + ["landing"] + ["cruise"] * 4
    assert segment_stage.median_filter(labels, 5) == ["cruise"] * 9


def test_median_filter_keeps_a_real_transition():
    labels = ["climb"] * 5 + ["cruise"] * 5
    filtered = segment_stage.median_filter(labels, 3)
    assert filtered[0] == "climb" and filtered[-1] == "cruise"
    assert "climb" in filtered and "cruise" in filtered


def test_to_spans_merges_consecutive_labels():
    spans = segment_stage.to_spans([0.0, 1.0, 2.0, 3.0], ["taxi", "taxi", "takeoff", "takeoff"], 4.0)
    assert [(s.phase, s.start, s.end) for s in spans] == [
        ("taxi", 0.0, 2.0),
        ("takeoff", 2.0, 4.0),
    ]


def test_shutdown_is_marked_only_after_a_flight():
    flown = segment_stage.to_spans([0, 1, 2], ["taxi", "cruise", "ground"], 3)
    assert segment_stage.mark_shutdown(flown)[-1].phase == "shutdown"

    never_flew = segment_stage.to_spans([0, 1], ["taxi", "ground"], 2)
    assert segment_stage.mark_shutdown(never_flew)[-1].phase == "ground"


def test_phases_from_telemetry_finds_a_full_flight():
    rows = []
    for t in range(0, 600):
        if t < 60:            # stationary
            speed, alt = 0.0, 100.0
        elif t < 120:         # taxi
            speed, alt = 8.0, 100.0
        elif t < 150:         # takeoff roll and lift-off
            speed, alt = 30.0, 100.0 + max(0, t - 140) * 3
        elif t < 300:         # climb
            speed, alt = 45.0, 130.0 + (t - 150) * 5
        elif t < 420:         # cruise
            speed, alt = 50.0, 880.0
        elif t < 540:         # descent to the circuit
            speed, alt = 40.0, 880.0 - (t - 420) * 6
        else:                 # on the ground again
            speed, alt = 0.0, 100.0
        rows.append({"time": float(t), "ground_speed": speed, "altitude": alt})

    phases = segment_stage.phases_from_telemetry(rows, 600.0, 5)
    present = phases.present()
    assert phases.source == "telemetry"
    assert "ground" in present and "taxi" in present
    assert "climb" in present and "cruise" in present
    assert present[-1] == "shutdown"
    assert phases.spans[0].start == 0.0
    assert phases.spans[-1].end == 600.0


@needs_ffmpeg
def test_segment_falls_back_honestly_without_telemetry_or_a_model(clip, cfg, tmp_path):
    run_dir = tmp_path / "dry-run"
    run_dir.mkdir()
    ctx = build_context(clip, run_dir, cfg, dry_run=True, verbose=False)
    probe_stage.run(ctx)
    telemetry_stage.run(ctx)
    sample_stage.run(ctx)
    record = segment_stage.run(ctx)

    phases = ctx.read_json("phases.json", Phases)
    assert record.status == "skipped"
    assert phases.source == "fallback"
    assert len(phases.spans) == 1
