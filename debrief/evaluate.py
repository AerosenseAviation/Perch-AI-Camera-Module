"""The evaluation harness.

This is the instrument that decides whether the product works. Export one row
per observation, grade the rows by hand, then read the useful rate back by mount
and by module. A module whose useful rate is low does not survive.
"""

from __future__ import annotations

import csv
import json
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path

from .models import Observations, Rejections, RunManifest, Viewpoint

VERDICTS = ("useful", "obvious", "wrong")

COLUMNS = [
    "flight_id",
    "mount",
    "phase",
    "module",
    "provenance",
    "confidence",
    "interest",
    "timestamp",
    "clock",
    "claim",
    "verdict",
    "notes",
]


def clock(seconds: float) -> str:
    seconds = max(0.0, seconds)
    h, rem = divmod(int(seconds), 3600)
    m, s = divmod(rem, 60)
    return f"{h:d}:{m:02d}:{s:02d}" if h else f"{m:d}:{s:02d}"


def is_run_dir(path: Path) -> bool:
    return (path / "observations.json").is_file() or (path / "probe.json").is_file()


def find_run_dirs(target: Path) -> list[Path]:
    """Accept a single run directory or a folder holding many of them."""
    target = Path(target)
    if not target.is_dir():
        raise FileNotFoundError(f"no such directory: {target}")
    if is_run_dir(target):
        return [target]
    children = sorted(p for p in target.iterdir() if p.is_dir() and is_run_dir(p))
    if not children:
        raise FileNotFoundError(f"{target} holds no run directories")
    return children


@dataclass
class RunRows:
    flight_id: str
    mount: str
    rows: list[dict[str, str]]


def rows_for_run(run_dir: Path) -> RunRows:
    run_dir = Path(run_dir)
    obs_path = run_dir / "observations.json"
    if not obs_path.is_file():
        return RunRows(flight_id=run_dir.name, mount="unknown", rows=[])

    observations = Observations.model_validate(json.loads(obs_path.read_text()))

    mount = "unknown"
    vp_path = run_dir / "viewpoint.json"
    if vp_path.is_file():
        mount = Viewpoint.model_validate(json.loads(vp_path.read_text())).mount

    flight_id = run_dir.name
    manifest_path = run_dir / "run.json"
    if manifest_path.is_file():
        manifest = RunManifest.model_validate(json.loads(manifest_path.read_text()))
        flight_id = manifest.flight_id

    rows: list[dict[str, str]] = []
    for obs in observations.observations:
        t = obs.timestamps[0] if obs.timestamps else 0.0
        rows.append(
            {
                "flight_id": flight_id,
                "mount": mount,
                "phase": obs.phase,
                "module": obs.module,
                "provenance": obs.provenance,
                "confidence": obs.confidence,
                "interest": obs.interest,
                "timestamp": f"{t:.1f}",
                "clock": clock(t),
                "claim": obs.claim,
                "verdict": "",
                "notes": "",
            }
        )
    return RunRows(flight_id=flight_id, mount=mount, rows=rows)


def export(target: Path, out: Path) -> tuple[int, int]:
    """Write the grading sheet. Returns (runs, rows)."""
    run_dirs = find_run_dirs(target)
    all_rows: list[dict[str, str]] = []
    for run_dir in run_dirs:
        all_rows.extend(rows_for_run(run_dir).rows)

    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=COLUMNS)
        writer.writeheader()
        writer.writerows(all_rows)
    return len(run_dirs), len(all_rows)


# --- reporting ---------------------------------------------------------------


def _pct(part: int, whole: int) -> str:
    return f"{(100.0 * part / whole):5.1f}%" if whole else "    — "


def _table(title: str, groups: dict[str, Counter], key_label: str) -> list[str]:
    lines = [f"\n{title}", f"  {key_label:<14}{'n':>5}  " + "  ".join(f"{v:>14}" for v in VERDICTS)]
    for key in sorted(groups, key=lambda k: -sum(groups[k].values())):
        counts = groups[key]
        graded = sum(counts[v] for v in VERDICTS)
        cells = []
        for verdict in VERDICTS:
            cells.append(f"{counts[verdict]:>4} {_pct(counts[verdict], graded)}")
        lines.append(f"  {key:<14}{graded:>5}  " + "  ".join(f"{c:>14}" for c in cells))
    return lines


def report(grades_csv: Path) -> str:
    path = Path(grades_csv)
    if not path.is_file():
        raise FileNotFoundError(f"no such grades file: {path}")

    with path.open(newline="") as fh:
        rows = list(csv.DictReader(fh))

    overall = Counter()
    ungraded = 0
    unknown_verdicts: Counter = Counter()
    by_mount: dict[str, Counter] = defaultdict(Counter)
    by_module: dict[str, Counter] = defaultdict(Counter)
    by_phase: dict[str, Counter] = defaultdict(Counter)
    by_flight: set[str] = set()

    for row in rows:
        verdict = (row.get("verdict") or "").strip().lower()
        by_flight.add(row.get("flight_id", ""))
        if not verdict:
            ungraded += 1
            continue
        if verdict not in VERDICTS:
            unknown_verdicts[verdict] += 1
            continue
        overall[verdict] += 1
        by_mount[row.get("mount") or "unknown"][verdict] += 1
        by_module[row.get("module") or "unknown"][verdict] += 1
        by_phase[row.get("phase") or "unknown"][verdict] += 1

    graded = sum(overall.values())
    lines = [
        f"Graded {graded} of {len(rows)} observations across {len(by_flight)} flights.",
    ]
    if ungraded:
        lines.append(f"{ungraded} rows have no verdict yet.")
    if unknown_verdicts:
        bad = ", ".join(f"{k} ({v})" for k, v in unknown_verdicts.most_common())
        lines.append(f"Ignored unrecognised verdicts: {bad}. Use: {', '.join(VERDICTS)}.")

    lines.append("\nOverall")
    for verdict in VERDICTS:
        lines.append(f"  {verdict:<10}{overall[verdict]:>5}  {_pct(overall[verdict], graded)}")

    lines += _table("By mount", by_mount, "mount")
    lines += _table("By module", by_module, "module")
    lines += _table("By phase", by_phase, "phase")

    if graded:
        lines.append(
            f"\nUseful rate is the go/no-go number: {_pct(overall['useful'], graded).strip()} overall."
        )
    return "\n".join(lines)


def rejection_summary(target: Path) -> str:
    """Rejection rate across runs — the other half of the quality picture."""
    run_dirs = find_run_dirs(target)
    total_proposed = 0
    total_rejected = 0
    by_rule: Counter = Counter()
    for run_dir in run_dirs:
        path = run_dir / "rejected.json"
        if not path.is_file():
            continue
        rejections = Rejections.model_validate(json.loads(path.read_text()))
        total_proposed += rejections.total_proposed
        total_rejected += len(rejections.rejected)
        for entry in rejections.rejected:
            for rule in entry.rules:
                by_rule[rule] += 1

    if not total_proposed:
        return "No proposed observations recorded."
    lines = [
        f"Validator rejected {total_rejected} of {total_proposed} proposed observations "
        f"({100.0 * total_rejected / total_proposed:.1f}%)."
    ]
    for rule, count in by_rule.most_common():
        lines.append(f"  {rule:<26}{count:>5}")
    return "\n".join(lines)
