"""The grading harness: export, report, and the rejection summary."""

from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest

from debrief import evaluate
from debrief.models import (
    Observation,
    Observations,
    Rejections,
    RejectedObservation,
    RunManifest,
    Viewpoint,
)


def make_run(root: Path, name: str, *, mount: str, claims: list[tuple[str, str, str]]) -> Path:
    run_dir = root / name
    run_dir.mkdir(parents=True)
    (run_dir / "probe.json").write_text("{}")
    (run_dir / "viewpoint.json").write_text(
        json.dumps(Viewpoint(mount=mount).model_dump())
    )
    (run_dir / "run.json").write_text(
        json.dumps(
            RunManifest(flight_id=name, video=f"{name}.mp4", created="now").model_dump()
        )
    )
    observations = [
        Observation(
            module=module,
            phase=phase,
            timestamps=[float(i * 60)],
            claim=claim,
            provenance="visual",
            confidence="high",
            interest="skill",
        )
        for i, (module, phase, claim) in enumerate(claims)
    ]
    (run_dir / "observations.json").write_text(
        json.dumps(Observations(observations=observations, accepted=len(observations)).model_dump())
    )
    return run_dir


@pytest.fixture
def two_runs(tmp_path) -> Path:
    root = tmp_path / "runs"
    make_run(
        root,
        "wing-flight",
        mount="wing",
        claims=[
            ("landing", "landing", "The wing stays level through the flare."),
            ("environment", "cruise", "Low sun across the ridge."),
        ],
    )
    make_run(
        root,
        "panel-flight",
        mount="panel",
        claims=[("panel", "climb", "The flap lever moves to the first stage.")],
    )
    return root


def test_export_writes_one_row_per_observation(two_runs, tmp_path):
    out = tmp_path / "grades.csv"
    runs, rows = evaluate.export(two_runs, out)
    assert (runs, rows) == (2, 3)

    written = list(csv.DictReader(out.open()))
    assert len(written) == 3
    assert set(written[0]) == set(evaluate.COLUMNS)
    assert all(row["verdict"] == "" and row["notes"] == "" for row in written)
    assert {row["mount"] for row in written} == {"wing", "panel"}
    assert {row["flight_id"] for row in written} == {"wing-flight", "panel-flight"}


def test_export_accepts_a_single_run_directory(two_runs, tmp_path):
    out = tmp_path / "one.csv"
    runs, rows = evaluate.export(two_runs / "panel-flight", out)
    assert (runs, rows) == (1, 1)


def test_export_carries_a_readable_clock_for_the_grader(two_runs, tmp_path):
    out = tmp_path / "grades.csv"
    evaluate.export(two_runs, out)
    rows = list(csv.DictReader(out.open()))
    assert {row["clock"] for row in rows} >= {"0:00", "1:00"}


def test_clock_formats_hours_only_when_needed():
    assert evaluate.clock(0) == "0:00"
    assert evaluate.clock(95) == "1:35"
    assert evaluate.clock(3725) == "1:02:05"


def test_report_breaks_the_verdicts_down_three_ways(two_runs, tmp_path):
    out = tmp_path / "grades.csv"
    evaluate.export(two_runs, out)

    rows = list(csv.DictReader(out.open()))
    for row, verdict in zip(rows, ["useful", "obvious", "wrong"]):
        row["verdict"] = verdict
    with out.open("w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=evaluate.COLUMNS)
        writer.writeheader()
        writer.writerows(rows)

    text = evaluate.report(out)
    assert "Graded 3 of 3 observations across 2 flights." in text
    assert "By mount" in text and "By module" in text and "By phase" in text
    assert "Useful rate is the go/no-go number" in text


def test_report_counts_ungraded_rows_separately(two_runs, tmp_path):
    out = tmp_path / "grades.csv"
    evaluate.export(two_runs, out)
    text = evaluate.report(out)
    assert "Graded 0 of 3" in text
    assert "3 rows have no verdict yet." in text


def test_report_flags_an_unrecognised_verdict(two_runs, tmp_path):
    out = tmp_path / "grades.csv"
    evaluate.export(two_runs, out)
    rows = list(csv.DictReader(out.open()))
    rows[0]["verdict"] = "brilliant"
    rows[1]["verdict"] = "useful"
    with out.open("w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=evaluate.COLUMNS)
        writer.writeheader()
        writer.writerows(rows)

    text = evaluate.report(out)
    assert "Ignored unrecognised verdicts: brilliant (1)" in text
    assert "Graded 1 of 3" in text


def test_report_is_case_insensitive(two_runs, tmp_path):
    out = tmp_path / "grades.csv"
    evaluate.export(two_runs, out)
    rows = list(csv.DictReader(out.open()))
    rows[0]["verdict"] = " Useful "
    with out.open("w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=evaluate.COLUMNS)
        writer.writeheader()
        writer.writerows(rows)
    assert "Graded 1 of 3" in evaluate.report(out)


def test_rejection_summary_aggregates_across_runs(two_runs):
    rejected = Rejections(
        rejected=[
            RejectedObservation(
                observation=Observation(
                    module="panel",
                    phase="cruise",
                    timestamps=[10.0],
                    claim="At 65 knots.",
                    provenance="visual",
                    confidence="high",
                    interest="skill",
                ),
                rules=["unsupported_number"],
                detail="states '65 knots'",
            )
        ],
        total_proposed=4,
        rejection_rate=0.25,
    )
    (two_runs / "wing-flight" / "rejected.json").write_text(json.dumps(rejected.model_dump()))

    text = evaluate.rejection_summary(two_runs)
    assert "rejected 1 of 4" in text
    assert "unsupported_number" in text


def test_finding_run_directories_fails_clearly(tmp_path):
    empty = tmp_path / "empty"
    empty.mkdir()
    with pytest.raises(FileNotFoundError, match="no run directories"):
        evaluate.find_run_dirs(empty)
    with pytest.raises(FileNotFoundError, match="no such directory"):
        evaluate.find_run_dirs(tmp_path / "missing")


# --- grading the second sensor ----------------------------------------------


def make_dual_run(root: Path, name: str) -> Path:
    """A run whose observations came from both sensors."""
    run_dir = root / name
    run_dir.mkdir(parents=True)
    (run_dir / "probe.json").write_text("{}")
    (run_dir / "viewpoint.json").write_text(
        json.dumps(Viewpoint(mount="panel", source="profile", rig="cockpit_dual").model_dump())
    )
    (run_dir / "run.json").write_text(
        json.dumps(
            RunManifest(flight_id=name, video=f"{name}.mp4", created="now").model_dump()
        )
    )
    observations = [
        Observation(
            module=module,
            phase="cruise",
            timestamps=[10.0],
            claim=claim,
            provenance="visual",
            confidence="high",
            interest="skill",
            stream=stream,
        )
        for module, stream, claim in [
            ("panel", "panel", "The airspeed indicator reads 95 knots."),
            ("crosscheck", "both", "Power comes back and the nose drops."),
            ("environment", "scene", "Low cloud along the far ridge."),
        ]
    ]
    (run_dir / "observations.json").write_text(
        json.dumps(Observations(observations=observations, accepted=3).model_dump())
    )
    return run_dir


def _grade(path: Path, verdicts: list[str]) -> None:
    rows = list(csv.DictReader(path.open()))
    for row, verdict in zip(rows, verdicts):
        row["verdict"] = verdict
    with path.open("w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=evaluate.COLUMNS)
        writer.writeheader()
        writer.writerows(rows)


def test_export_records_the_rig_and_the_stream(tmp_path):
    root = tmp_path / "runs"
    make_dual_run(root, "dual-flight")
    out = tmp_path / "grades.csv"
    evaluate.export(root, out)

    rows = list(csv.DictReader(out.open()))
    assert {row["rig"] for row in rows} == {"cockpit_dual"}
    assert {row["stream"] for row in rows} == {"panel", "both", "scene"}


def test_report_breaks_verdicts_down_by_stream(tmp_path):
    root = tmp_path / "runs"
    make_dual_run(root, "dual-flight")
    out = tmp_path / "grades.csv"
    evaluate.export(root, out)
    _grade(out, ["useful", "useful", "obvious"])

    text = evaluate.report(out)
    assert "By stream" in text
    assert "panel" in text


def test_report_calls_out_wrong_instrument_readings(tmp_path):
    """A wrong number is the failure that loses a pilot, so it gets its own line."""
    root = tmp_path / "runs"
    make_dual_run(root, "dual-flight")
    out = tmp_path / "grades.csv"
    evaluate.export(root, out)
    _grade(out, ["wrong", "useful", "useful"])

    text = evaluate.report(out)
    assert "Instrument reading (panel + crosscheck): 1 wrong of 2 graded" in text


def test_no_instrument_line_when_nothing_instrument_based_was_graded(tmp_path):
    root = tmp_path / "runs"
    make_run(
        root,
        "wing-only",
        mount="wing",
        claims=[
            ("environment", "cruise", "Low sun across the ridge."),
            ("landing", "landing", "The wing stays level through the flare."),
        ],
    )
    out = tmp_path / "grades.csv"
    evaluate.export(root, out)
    _grade(out, ["useful", "useful"])
    assert "Instrument reading" not in evaluate.report(out)
