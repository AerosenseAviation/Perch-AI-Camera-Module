"""Stage 2 — telemetry.

GoPro cameras write a GPMF (``gpmd``) data track. When one is present, exiftool
can decode it. Telemetry is a bonus: the pipeline gives a full debrief without
it, so every failure here degrades to an empty file rather than stopping the
run.

``telemetry.csv`` columns and units:

    time          seconds from the start of the video
    latitude      decimal degrees
    longitude     decimal degrees
    altitude      metres (GPS, above the WGS-84 ellipsoid)
    ground_speed  metres per second
    accel_x/y/z   metres per second squared, camera axes
"""

from __future__ import annotations

import csv
import json
import re
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any, Optional

from ..models import Probe, StageRecord, TelemetryChannels
from ..runs import RunContext

NAME = "telemetry"

COLUMNS = [
    "time",
    "latitude",
    "longitude",
    "altitude",
    "ground_speed",
    "accel_x",
    "accel_y",
    "accel_z",
    "bank",
    "pitch",
    "g_load",
    "turn_rate",
]

# Which columns prove which capability. Position needs GPS; the Perch rig has
# none, so it fills altitude (barometer) and attitude (IMU) instead.
POSITION_COLUMNS = ("latitude", "longitude", "ground_speed")
ALTITUDE_COLUMNS = ("altitude",)
ATTITUDE_COLUMNS = ("bank", "pitch")
ACCELERATION_COLUMNS = ("accel_x", "accel_y", "accel_z", "g_load", "turn_rate")

_DOC_KEY = re.compile(r"^(?:Doc(\d+)|Main|Track\d+)\s*:\s*(.+)$")
_NUMBER = re.compile(r"[-+]?\d*\.?\d+(?:[eE][-+]?\d+)?")


def _as_float(value: Any) -> Optional[float]:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, (list, tuple)):
        return _as_float(value[0]) if value else None
    if isinstance(value, str):
        match = _NUMBER.search(value)
        return float(match.group()) if match else None
    return None


def _triplet_mean(value: Any) -> tuple[Optional[float], Optional[float], Optional[float]]:
    """Average an accelerometer field that may hold many stacked triplets."""
    if isinstance(value, (list, tuple)):
        numbers = [n for n in (_as_float(v) for v in value) if n is not None]
    elif isinstance(value, str):
        numbers = [float(m) for m in _NUMBER.findall(value)]
    else:
        return (None, None, None)
    if len(numbers) < 3:
        return (None, None, None)
    usable = len(numbers) - (len(numbers) % 3)
    axes = [numbers[i:usable:3] for i in range(3)]
    return tuple(sum(a) / len(a) if a else None for a in axes)  # type: ignore[return-value]


def run_exiftool(video: Path) -> list[dict[str, Any]]:
    """Return exiftool's ``-ee -G3 -n -json`` output, or [] when unavailable."""
    binary = shutil.which("exiftool")
    if not binary:
        return []
    proc = subprocess.run(
        [binary, "-ee", "-G3", "-n", "-json", str(video)],
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0 or not proc.stdout.strip():
        return []
    try:
        data = json.loads(proc.stdout)
    except json.JSONDecodeError:
        return []
    return data if isinstance(data, list) else [data]


def group_documents(payload: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Fold exiftool's flat ``Doc<N>:Tag`` keys into one dict per sample."""
    docs: dict[int, dict[str, Any]] = {}
    for record in payload:
        for key, value in record.items():
            match = _DOC_KEY.match(key)
            if not match:
                continue
            index = int(match.group(1)) if match.group(1) else 0
            docs.setdefault(index, {})[match.group(2)] = value
    return [docs[i] for i in sorted(docs) if i > 0] or (
        [docs[0]] if 0 in docs and _has_gps(docs[0]) else []
    )


def _has_gps(doc: dict[str, Any]) -> bool:
    return "GPSLatitude" in doc and "GPSLongitude" in doc


def _doc_time(doc: dict[str, Any]) -> Optional[float]:
    for key in ("SampleTime", "SampleTimeStamp", "TimeStamp"):
        if key in doc:
            value = _as_float(doc[key])
            if value is not None:
                return value
    return None


def to_series(docs: list[dict[str, Any]], duration: float) -> list[dict[str, Any]]:
    """Convert grouped documents into an ordered, timestamped series."""
    rows: list[dict[str, Any]] = []
    for doc in docs:
        if not _has_gps(doc) and "Accelerometer" not in doc and "AccelerometerX" not in doc:
            continue
        ax, ay, az = (None, None, None)
        if "Accelerometer" in doc:
            ax, ay, az = _triplet_mean(doc["Accelerometer"])
        else:
            ax = _as_float(doc.get("AccelerometerX"))
            ay = _as_float(doc.get("AccelerometerY"))
            az = _as_float(doc.get("AccelerometerZ"))

        speed = _as_float(doc.get("GPSSpeed"))
        if speed is None:
            speed = _as_float(doc.get("GPSSpeed3D"))

        rows.append(
            {
                "time": _doc_time(doc),
                "latitude": _as_float(doc.get("GPSLatitude")),
                "longitude": _as_float(doc.get("GPSLongitude")),
                "altitude": _as_float(doc.get("GPSAltitude")),
                "ground_speed": speed,
                "accel_x": ax,
                "accel_y": ay,
                "accel_z": az,
            }
        )

    if not rows:
        return []

    # Some GPMF builds omit SampleTime. Spreading the samples evenly across the
    # clip is a fair approximation for GPMF, which is written at a steady rate.
    if all(row["time"] is None for row in rows) and duration > 0:
        step = duration / len(rows)
        for i, row in enumerate(rows):
            row["time"] = round(i * step, 3)
    else:
        last = 0.0
        for row in rows:
            if row["time"] is None:
                row["time"] = last
            else:
                last = float(row["time"])

    rows.sort(key=lambda r: r["time"])
    return rows


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    with path.open("w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=COLUMNS)
        writer.writeheader()
        for row in rows:
            writer.writerow({k: ("" if row.get(k) is None else row.get(k)) for k in COLUMNS})


def read_csv(path: Path) -> list[dict[str, float]]:
    """Read telemetry.csv back into floats. Returns [] for a header-only file."""
    if not path.is_file():
        return []
    out: list[dict[str, float]] = []
    with path.open(newline="") as fh:
        for raw in csv.DictReader(fh):
            row: dict[str, float] = {}
            for key in COLUMNS:
                value = (raw.get(key) or "").strip()
                if value:
                    try:
                        row[key] = float(value)
                    except ValueError:
                        continue
            if "time" in row:
                out.append(row)
    return out


def channels(rows: list[dict[str, float]], source: str = "gpmf") -> TelemetryChannels:
    """Derive what this telemetry file can actually vouch for."""
    if not rows:
        return TelemetryChannels()
    present = {key for row in rows for key in row}

    def has(names: tuple[str, ...]) -> bool:
        return any(n in present for n in names)

    return TelemetryChannels(
        source=source,  # type: ignore[arg-type]
        position=has(POSITION_COLUMNS),
        altitude=has(ALTITUDE_COLUMNS),
        attitude=has(ATTITUDE_COLUMNS),
        acceleration=has(ACCELERATION_COLUMNS),
    )


def describe(ch: TelemetryChannels) -> str:
    parts = [
        name
        for name, on in (
            ("position", ch.position),
            ("altitude", ch.altitude),
            ("attitude", ch.attitude),
            ("acceleration", ch.acceleration),
        )
        if on
    ]
    return ", ".join(parts) or "nothing"


def run(ctx: RunContext) -> StageRecord:
    started = time.time()
    probe = ctx.read_json("probe.json", Probe)
    out = ctx.path("telemetry.csv")

    # Either camera may carry a gpmd track; prefer the scene camera, which sees
    # the world and so is the one usually worth having GPS from.
    source = next(
        (s for s in probe.streams if s.role == "scene" and s.has_telemetry),
        next((s for s in probe.streams if s.has_telemetry), None),
    )

    if source is None:
        write_csv(out, [])
        ctx.say("  telemetry: no gpmd stream — continuing without it")
        return StageRecord(
            name=NAME,
            status="skipped",
            seconds=round(time.time() - started, 3),
            detail="no gpmd stream",
        )

    if not shutil.which("exiftool"):
        write_csv(out, [])
        ctx.say("  telemetry: gpmd stream present but exiftool is not installed")
        return StageRecord(
            name=NAME,
            status="skipped",
            seconds=round(time.time() - started, 3),
            detail="exiftool not installed",
        )

    rows = to_series(
        group_documents(run_exiftool(Path(source.path))), source.duration
    )
    # Carry the samples onto the run timeline, as the frames are.
    if source.offset:
        for row in rows:
            row["time"] = round(row["time"] + source.offset, 3)
        rows = [r for r in rows if 0 <= r["time"] <= probe.duration]
    write_csv(out, rows)
    if not rows:
        ctx.say("  telemetry: gpmd stream present but exiftool decoded no samples")
        return StageRecord(
            name=NAME,
            status="skipped",
            seconds=round(time.time() - started, 3),
            detail="no samples decoded",
        )

    ch = channels(rows, source="gpmf")
    ctx.say(
        f"  telemetry: {len(rows)} samples over {rows[-1]['time']:.0f}s "
        f"[{describe(ch)}]"
    )
    return StageRecord(
        name=NAME,
        status="ok",
        seconds=round(time.time() - started, 3),
        detail=f"{len(rows)} samples ({describe(ch)})",
    )
