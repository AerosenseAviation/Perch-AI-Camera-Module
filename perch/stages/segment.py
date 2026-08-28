"""Stage 5 — segment.

Divide the flight into phases. Telemetry gives the better answer, so it wins
when present. Without it the fast vision model labels one frame every 30
seconds and a median filter removes the single-frame spikes.

The Perch rig has no GPS — mounted inside, usually against the headliner, it
has no sky view worth relying on. So this works from what the rig does have:
a barometer for altitude and vertical speed, and an IMU for bank, turn rate and
the very distinctive vertical thump of a touchdown. Ground speed is used when a
GPS-bearing camera supplies it, and inferred from vibration when it does not.
"""

from __future__ import annotations

import math
import statistics
import time
from typing import Optional

from ..llm import ImagePart, LLMDisabled, LLMError, TextPart, load_prompt
from ..models import (
    FrameLabels,
    FrameRef,
    Phases,
    PhaseSpan,
    Probe,
    StageRecord,
)
from ..runs import RunContext
from . import sample as sample_stage
from . import telemetry as telemetry_stage

NAME = "segment"

# Ordinal order used by the median filter. It runs roughly in flight order, so
# the median of a window is always a plausible neighbouring phase.
PHASE_ORDER = (
    "ground",
    "taxi",
    "takeoff",
    "climb",
    "cruise",
    "manoeuvre",
    "circuit",
    "approach",
    "landing",
    "shutdown",
)
_INDEX = {p: i for i, p in enumerate(PHASE_ORDER)}

MS_TO_KT = 1.94384
MS_TO_FPM = 196.850


# --- shared helpers ----------------------------------------------------------


def median_filter(labels: list[str], window: int) -> list[str]:
    """Median filter over the ordinal phase index. ``window`` is forced odd."""
    if window < 3 or len(labels) < 3:
        return list(labels)
    if window % 2 == 0:
        window += 1
    # A window wider than the series smooths a short flight into one phase.
    window = min(window, len(labels) if len(labels) % 2 else len(labels) - 1)
    if window < 3:
        return list(labels)
    half = window // 2
    ordinals = [_INDEX.get(label, 0) for label in labels]
    out: list[str] = []
    for i in range(len(ordinals)):
        lo = max(0, i - half)
        hi = min(len(ordinals), i + half + 1)
        out.append(PHASE_ORDER[statistics.median_low(ordinals[lo:hi])])
    return out


def to_spans(times: list[float], labels: list[str], end: float) -> list[PhaseSpan]:
    """Collapse a label-per-sample sequence into contiguous spans."""
    if not times:
        return []
    spans: list[PhaseSpan] = []
    start = times[0]
    current = labels[0]
    for i in range(1, len(times)):
        if labels[i] != current:
            spans.append(PhaseSpan(start=round(start, 2), end=round(times[i], 2), phase=current))
            start = times[i]
            current = labels[i]
    spans.append(PhaseSpan(start=round(start, 2), end=round(end, 2), phase=current))
    return [s for s in spans if s.end > s.start]


def mark_shutdown(spans: list[PhaseSpan]) -> list[PhaseSpan]:
    """Relabel the final stationary span as shutdown once the flight has flown."""
    airborne = {"takeoff", "climb", "cruise", "manoeuvre", "circuit", "approach", "landing"}
    if not any(s.phase in airborne for s in spans):
        return spans
    for span in reversed(spans):
        if span.phase in ("ground", "taxi"):
            if span.phase == "ground":
                span.phase = "shutdown"
            break
        if span.phase in airborne:
            break
    return spans


# --- telemetry path ----------------------------------------------------------


def _interpolate(rows: list[dict[str, float]], key: str, grid: list[float]) -> list[Optional[float]]:
    points = [(r["time"], r[key]) for r in rows if key in r]
    if not points:
        return [None] * len(grid)
    times = [p[0] for p in points]
    values = [p[1] for p in points]
    out: list[Optional[float]] = []
    for t in grid:
        if t <= times[0]:
            out.append(values[0])
        elif t >= times[-1]:
            out.append(values[-1])
        else:
            i = 0
            while i + 1 < len(times) and times[i + 1] < t:
                i += 1
            span = times[i + 1] - times[i]
            frac = (t - times[i]) / span if span else 0.0
            out.append(values[i] + frac * (values[i + 1] - values[i]))
    return out


def _track_degrees(rows: list[dict[str, float]], grid: list[float]) -> list[Optional[float]]:
    """Ground track from successive GPS fixes, in degrees."""
    fixes = [r for r in rows if "latitude" in r and "longitude" in r]
    if len(fixes) < 2:
        return [None] * len(grid)
    tracks: list[dict[str, float]] = []
    for a, b in zip(fixes, fixes[1:]):
        dlat = b["latitude"] - a["latitude"]
        dlon = (b["longitude"] - a["longitude"]) * math.cos(math.radians(a["latitude"]))
        if abs(dlat) < 1e-9 and abs(dlon) < 1e-9:
            continue
        bearing = math.degrees(math.atan2(dlon, dlat)) % 360
        tracks.append({"time": b["time"], "track": bearing})
    if len(tracks) < 2:
        return [None] * len(grid)
    # Unwrap so interpolation does not jump across the 360/0 boundary.
    unwrapped = [tracks[0]["track"]]
    for prev, cur in zip(tracks, tracks[1:]):
        delta = ((cur["track"] - prev["track"] + 180) % 360) - 180
        unwrapped.append(unwrapped[-1] + delta)
    rows2 = [{"time": t["time"], "track": u} for t, u in zip(tracks, unwrapped)]
    return _interpolate(rows2, "track", grid)


def _motion_from_vibration(rows: list[dict[str, float]], grid: list[float]) -> list[float]:
    """A 0-1 "something is happening" signal from accelerometer variance.

    Without GPS there is no ground speed, so stationary-versus-rolling has to
    come from the airframe itself. A parked aircraft with the engine running
    still vibrates, but taxiing over ground adds a great deal more.
    """
    axes = [_interpolate(rows, key, grid) for key in ("accel_x", "accel_y", "accel_z")]
    if not any(any(v is not None for v in axis) for axis in axes):
        return [0.0] * len(grid)

    magnitude = [
        math.sqrt(sum((axis[i] or 0.0) ** 2 for axis in axes)) for i in range(len(grid))
    ]
    half = 5
    energy: list[float] = []
    for i in range(len(magnitude)):
        lo, hi = max(0, i - half), min(len(magnitude), i + half + 1)
        chunk = magnitude[lo:hi]
        mean = sum(chunk) / len(chunk)
        energy.append(math.sqrt(sum((v - mean) ** 2 for v in chunk) / len(chunk)))

    ceiling = max(energy) or 1.0
    return [min(1.0, v / ceiling) for v in energy]


def _touchdown(
    alts: list[Optional[float]], accel_z: list[Optional[float]], grid: list[float], field: float
) -> Optional[int]:
    """Index of the landing thump: the biggest vertical spike near the ground.

    The most reliable single event an IMU gives you, and it anchors the highest
    value phase in the debrief.
    """
    best, best_index = 0.0, None
    for i, t in enumerate(grid):
        agl = (alts[i] - field) if alts[i] is not None else 0.0
        if agl > 40 or accel_z[i] is None:
            continue
        # Deviation from 1g, in whatever units the sensor reports.
        rest = 9.81 if abs(accel_z[i]) > 5 else 1.0
        spike = abs(abs(accel_z[i]) - rest) / rest
        if spike > best:
            best, best_index = spike, i
    return best_index if best > 0.35 else None


def phases_from_telemetry(
    rows: list[dict[str, float]], duration: float, window: int
) -> Phases:
    step = 1.0
    grid = [round(t, 2) for t in _frange(0.0, duration, step)]
    if not grid:
        return Phases(source="telemetry", spans=[], note="telemetry too short to segment")

    speeds = _interpolate(rows, "ground_speed", grid)
    alts = _interpolate(rows, "altitude", grid)
    tracks = _track_degrees(rows, grid)
    banks = _interpolate(rows, "bank", grid)
    turn_rates = _interpolate(rows, "turn_rate", grid)
    accel_z = _interpolate(rows, "accel_z", grid)

    has_speed = any(v is not None for v in speeds)
    motion = [0.0] * len(grid) if has_speed else _motion_from_vibration(rows, grid)

    known_alts = [a for a in alts if a is not None]
    field = statistics.quantiles(known_alts, n=10)[0] if len(known_alts) >= 10 else (
        min(known_alts) if known_alts else 0.0
    )

    labels: list[str] = []
    for i, _t in enumerate(grid):
        agl = (alts[i] - field) if alts[i] is not None else 0.0
        fpm = _rate(alts, i, step) * MS_TO_FPM

        # Rolling: from GPS when there is one, otherwise from vibration.
        if has_speed:
            kt = (speeds[i] or 0.0) * MS_TO_KT
            stationary, rolling = kt < 1.5, kt < 30
        else:
            stationary, rolling = motion[i] < 0.15, motion[i] < 0.55

        # Turning: the gyro is better than a differentiated GPS track, and it
        # is the only source when there is no GPS at all.
        if turn_rates[i] is not None:
            turn = abs(turn_rates[i])
        elif banks[i] is not None:
            turn = abs(banks[i]) / 3.0  # a rough rate-per-degree-of-bank stand-in
        else:
            turn = abs(_rate(tracks, i, step))

        if agl < 15 and stationary:
            labels.append("ground")
        elif agl < 15 and rolling:
            labels.append("taxi")
        elif agl < 60:
            labels.append("takeoff" if fpm > -100 else "landing")
        elif fpm > 300:
            labels.append("climb")
        elif turn > 4.0 and agl > 300:
            labels.append("manoeuvre")
        elif fpm < -300 and agl < 1500:
            labels.append("approach")
        else:
            labels.append("cruise")

    labels = median_filter(labels, window)

    # The touchdown thump is the single most reliable event the IMU gives, so
    # let it override the smoothed guess around the moment it happened.
    touch = _touchdown(alts, accel_z, grid, field)
    if touch is not None:
        for i in range(max(0, touch - 5), min(len(labels), touch + 10)):
            labels[i] = "landing"
    labels = _mark_circuits(labels, tracks, alts, field, step)
    spans = mark_shutdown(to_spans(grid, labels, duration))
    sources = "GPS" if has_speed else "barometer and IMU"
    return Phases(
        source="telemetry",
        spans=spans,
        note=f"derived from {len(rows)} samples via {sources}"
        + (", touchdown detected" if touch is not None else ""),
    )


def _mark_circuits(
    labels: list[str],
    tracks: list[Optional[float]],
    alts: list[Optional[float]],
    field: float,
    step: float,
) -> list[str]:
    """Relabel low-level cruise as circuit when the track keeps turning.

    A circuit is four turns in the same direction inside a few minutes at
    pattern altitude. Cumulative heading change over a rolling five-minute
    window separates that from a straight cross-country leg.
    """
    if not any(t is not None for t in tracks):
        return labels
    window = int(300 / step)
    out = list(labels)
    for i, label in enumerate(labels):
        if label != "cruise":
            continue
        agl = (alts[i] - field) if alts[i] is not None else 0.0
        if not (60 <= agl <= 1500):
            continue
        lo, hi = max(0, i - window // 2), min(len(tracks), i + window // 2)
        segment = [t for t in tracks[lo:hi] if t is not None]
        if len(segment) < 2:
            continue
        turned = sum(abs(b - a) for a, b in zip(segment, segment[1:]))
        if turned >= 300:  # most of a circuit's worth of turning
            out[i] = "circuit"
    return out


def _rate(series: list[Optional[float]], i: int, step: float, half: int = 5) -> float:
    lo, hi = max(0, i - half), min(len(series) - 1, i + half)
    a, b = series[lo], series[hi]
    if a is None or b is None or hi == lo:
        return 0.0
    return (b - a) / ((hi - lo) * step)


def _frange(start: float, stop: float, step: float) -> list[float]:
    out: list[float] = []
    t = start
    while t < stop:
        out.append(t)
        t += step
    return out


# --- vision path -------------------------------------------------------------


def phases_from_vision(ctx: RunContext, frames: list[FrameRef], duration: float) -> Phases:
    cfg = ctx.config.segment
    picks = _pick_every(frames, cfg.vision_interval_seconds)
    if not picks:
        return _fallback(duration, "no frames to label")

    system = load_prompt("segment.md")
    labelled: dict[float, str] = {}
    batch_size = max(1, cfg.max_frames_per_call)

    for start in range(0, len(picks), batch_size):
        batch = picks[start : start + batch_size]
        parts: list = [
            TextPart(
                "Label the flight phase shown in each frame. "
                f"There are {len(batch)} frames, each preceded by its timestamp."
            )
        ]
        for ref in batch:
            parts.append(TextPart(f"Frame at t={ref.t:.1f}s:"))
            parts.append(
                ImagePart(
                    ctx.stream_dir("scene") / ref.file,
                    width=ctx.config.sample.long_edge,
                )
            )
        parts.append(
            TextPart(
                "Return one label per frame, echoing each timestamp exactly as given."
            )
        )
        result = ctx.llm.complete_json(
            model=ctx.config.models.fast,
            system=system,
            parts=parts,
            schema=FrameLabels,
            max_tokens=2000,
            namespace="segment",
        )
        for label in result.labels:
            nearest = min(batch, key=lambda r: abs(r.t - label.t))
            labelled[nearest.t] = label.phase

    if not labelled:
        return _fallback(duration, "the vision model returned no labels")

    times = sorted(labelled)
    labels = median_filter([labelled[t] for t in times], cfg.median_filter_window)
    spans = mark_shutdown(to_spans(times, labels, duration))
    return Phases(
        source="vision",
        spans=spans,
        note=f"{len(labelled)} frames labelled by {ctx.config.models.fast}",
    )


def _pick_every(frames: list[FrameRef], interval: float) -> list[FrameRef]:
    """One frame per interval, choosing the frame nearest each mark."""
    if not frames:
        return []
    end = frames[-1].t
    picks: list[FrameRef] = []
    t = 0.0
    while t <= end:
        ref = min(frames, key=lambda f: abs(f.t - t))
        if not picks or picks[-1].t != ref.t:
            picks.append(ref)
        t += interval
    return picks


def _fallback(duration: float, note: str) -> Phases:
    """A single honest span when nothing better is available.

    Better to say "the whole clip is unsegmented" than to invent takeoff and
    landing times the pipeline never observed.
    """
    return Phases(
        source="fallback",
        spans=[PhaseSpan(start=0.0, end=round(duration, 2), phase="cruise")],
        note=note,
    )


# --- stage entry point -------------------------------------------------------


def run(ctx: RunContext) -> StageRecord:
    started = time.time()
    probe = ctx.read_json("probe.json", Probe)
    rows = telemetry_stage.read_csv(ctx.path("telemetry.csv"))
    # Phases come from the wide view: the panel sensor cannot see the ground.
    frames = sample_stage.load_frames(ctx.frames_dir, "scene")

    channels = telemetry_stage.channels(rows)
    usable = [r for r in rows if "altitude" in r or "ground_speed" in r]
    if channels.can_segment and len(usable) >= 10:
        phases = phases_from_telemetry(
            usable, probe.duration, ctx.config.segment.median_filter_window
        )
    elif ctx.llm.enabled:
        try:
            phases = phases_from_vision(ctx, frames, probe.duration)
        except (LLMDisabled, LLMError) as exc:
            phases = _fallback(probe.duration, f"vision segmentation unavailable: {exc}")
    else:
        phases = _fallback(probe.duration, "dry run — no model calls")

    ctx.write_json("phases.json", phases)
    summary = ", ".join(phases.present()) or "none"
    ctx.say(f"  segment: {len(phases.spans)} spans via {phases.source} [{summary}]")
    return StageRecord(
        name=NAME,
        status="ok" if phases.source != "fallback" else "skipped",
        seconds=round(time.time() - started, 3),
        detail=f"{len(phases.spans)} spans via {phases.source}",
    )
