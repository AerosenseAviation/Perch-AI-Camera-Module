"""The command line surface."""

from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from debrief.cli import app
from debrief.llm import set_stub

from .conftest import needs_ffmpeg
from .test_pipeline import WING_CAM, make_stub

runner = CliRunner()


@pytest.fixture
def config_file(tmp_path) -> Path:
    """A config that keeps every artifact inside the test's tmp directory."""
    path = tmp_path / "debrief.toml"
    path.write_text(
        "[paths]\n"
        f'runs_root = "{tmp_path / "runs"}"\n'
        f'cache_dir = "{tmp_path / "cache"}"\n'
        "\n[audio]\ntranscribe = false\n"
        "\n[segment]\nvision_interval_seconds = 6.0\n"
    )
    return path


def invoke(*args):
    return runner.invoke(app, [str(a) for a in args])


def test_version():
    result = invoke("--version")
    assert result.exit_code == 0
    assert "flight-debrief-camera" in result.output


def test_bare_invocation_shows_help():
    result = invoke()
    assert result.exit_code == 0
    assert "Turn cockpit camera footage into a post-flight debrief." in result.output


@needs_ffmpeg
def test_probe_prints_a_summary(clip):
    result = invoke("probe", clip)
    assert result.exit_code == 0
    assert "duration" in result.output
    assert "resolution" in result.output
    assert "telemetry   none" in result.output


@needs_ffmpeg
def test_probe_json_is_machine_readable(clip):
    result = invoke("probe", clip, "--json")
    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["has_audio"] is True
    assert payload["duration"] > 0


def test_probe_rejects_a_missing_file(tmp_path):
    result = invoke("probe", tmp_path / "nope.mp4")
    assert result.exit_code != 0


@needs_ffmpeg
def test_run_dry_run_writes_a_run_directory(clip, config_file, tmp_path):
    result = invoke("run", clip, "--dry-run", "--config", config_file)
    assert result.exit_code == 0, result.output

    runs = list((tmp_path / "runs").iterdir())
    assert len(runs) == 1
    assert (runs[0] / "frames.json").is_file()
    assert (runs[0] / "run.json").is_file()
    assert "dry run" in result.output


@needs_ffmpeg
def test_run_rejects_an_unknown_module(clip, config_file):
    result = invoke("run", clip, "--modules", "nosuch", "--config", config_file)
    assert result.exit_code != 0
    assert "unknown module" in result.output


def test_stage_rejects_an_unknown_name(tmp_path):
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    result = invoke("stage", "nosuchstage", run_dir)
    assert result.exit_code != 0
    assert "unknown stage" in result.output


@needs_ffmpeg
def test_stage_reruns_one_step_against_an_existing_run(clip, config_file, tmp_path):
    invoke("run", clip, "--dry-run", "--config", config_file)
    run_dir = next(iter((tmp_path / "runs").iterdir()))

    frames_before = sorted(p.name for p in (run_dir / "frames").iterdir())
    result = invoke("stage", "sample", run_dir, "--config", config_file)

    assert result.exit_code == 0, result.output
    assert sorted(p.name for p in (run_dir / "frames").iterdir()) == frames_before


@needs_ffmpeg
def test_batch_walks_a_folder(clip, silent_clip, config_file, tmp_path):
    folder = tmp_path / "library"
    folder.mkdir()
    for source in (clip, silent_clip):
        (folder / source.name).write_bytes(source.read_bytes())

    result = invoke("batch", folder, "--dry-run", "--config", config_file)
    assert result.exit_code == 0, result.output
    assert "2 video(s)" in result.output
    assert "2 run(s) completed, 0 failed." in result.output
    assert len(list((tmp_path / "runs").iterdir())) == 2


def test_batch_on_an_empty_folder_exits_nonzero(tmp_path, config_file):
    empty = tmp_path / "empty"
    empty.mkdir()
    result = invoke("batch", empty, "--config", config_file)
    assert result.exit_code == 1
    assert "No .mp4" in result.output


@needs_ffmpeg
def test_eval_export_then_report(clip, config_file, tmp_path):
    set_stub(make_stub(viewpoint=WING_CAM, claims=[{"claim": "The wing holds steady."}]))
    assert invoke("run", clip, "--config", config_file).exit_code == 0
    run_dir = next(iter((tmp_path / "runs").iterdir()))

    grades = tmp_path / "grades.csv"
    export = invoke("eval", "export", run_dir, "-o", grades)
    assert export.exit_code == 0
    assert "Fill the `verdict` column" in export.output

    rows = list(csv.DictReader(grades.open()))
    assert rows
    for row in rows:
        row["verdict"] = "useful"
    with grades.open("w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=rows[0].keys())
        writer.writeheader()
        writer.writerows(rows)

    report = invoke("eval", "report", grades)
    assert report.exit_code == 0
    assert "By mount" in report.output
    assert "100.0%" in report.output


@needs_ffmpeg
def test_eval_rejections_reports_the_validator_rate(clip, config_file, tmp_path):
    set_stub(
        make_stub(
            viewpoint=WING_CAM,
            claims=[
                {"claim": "The wing holds steady."},
                {"claim": "It crosses the fence at 65 knots."},
            ],
        )
    )
    assert invoke("run", clip, "--config", config_file).exit_code == 0
    run_dir = next(iter((tmp_path / "runs").iterdir()))

    result = invoke("eval", "rejections", run_dir)
    assert result.exit_code == 0
    assert "unsupported_number" in result.output


# --- the two-sensor rig ------------------------------------------------------


@needs_ffmpeg
def test_probe_reports_the_measured_offset_between_two_files(clip, panel_clip, config_file):
    result = invoke("probe", clip, "--panel", panel_clip, "--config", config_file)
    assert result.exit_code == 0, result.output
    assert "instrument sensor" in result.output
    assert "offset" in result.output


@needs_ffmpeg
def test_run_accepts_a_panel_stream_and_a_rig(clip, panel_clip, config_file, tmp_path):
    result = invoke(
        "run", clip,
        "--panel", panel_clip,
        "--panel-offset", "0",
        "--rig", "cockpit_dual",
        "--dry-run",
        "--config", config_file,
    )
    assert result.exit_code == 0, result.output

    run_dir = next(iter((tmp_path / "runs").iterdir()))
    assert (run_dir / "frames" / "scene").is_dir()
    assert (run_dir / "frames" / "panel").is_dir()
    assert any((run_dir / "frames" / "panel").glob("f_*.jpg"))


@needs_ffmpeg
def test_run_rejects_an_unknown_rig(clip, config_file):
    result = invoke("run", clip, "--rig", "nosuchrig", "--config", config_file)
    assert result.exit_code != 0
    assert "unknown rig" in result.output


@needs_ffmpeg
def test_batch_pairs_scene_and_panel_files_by_name(clip, panel_clip, config_file, tmp_path):
    folder = tmp_path / "library"
    folder.mkdir()
    (folder / "flight01.mp4").write_bytes(clip.read_bytes())
    (folder / "flight01-panel.mp4").write_bytes(panel_clip.read_bytes())

    result = invoke("batch", folder, "--dry-run", "--config", config_file)
    assert result.exit_code == 0, result.output
    # The panel file is an input, not a flight of its own.
    assert "1 video(s)" in result.output
    assert "1 run(s) completed, 0 failed." in result.output

    run_dir = next(iter((tmp_path / "runs").iterdir()))
    assert any((run_dir / "frames" / "panel").glob("f_*.jpg"))
