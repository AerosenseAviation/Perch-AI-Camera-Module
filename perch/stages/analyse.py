"""Stage 7 — analyse.

One model call per enabled module per phase, with that phase's frames split into
batches. Each module gets the stream it needs: `panel` sees only the instrument
sensor, most modules see only the wide view, and `crosscheck` sees both — the
same moment from two sensors, side by side.

Every call carries the same context: frames with timestamps, the transcript for
that range, the telemetry slice, the rig descriptor, and the module instruction.

Everything the model returns goes through the validator before it is written.
"""

from __future__ import annotations

import json
import time
from typing import Optional

from ..llm import ImagePart, LLMError, TextPart, load_prompt
from ..models import (
    FrameIndex,
    FrameRef,
    Modules,
    Observation,
    ObservationList,
    Observations,
    PanelAim,
    Phases,
    Probe,
    StageRecord,
    Transcript,
    Viewpoint,
)
from ..modules import BY_NAME, phases_for, streams_for
from ..runs import RunContext
from ..validate import ValidationContext, validate
from . import audio as audio_stage
from . import sample as sample_stage
from . import telemetry as telemetry_stage

NAME = "analyse"


def system_prompt() -> str:
    return load_prompt("system_analyse.md", rules=load_prompt("rules.md").strip())


def module_prompt(module: str) -> str:
    return load_prompt(f"modules/{module}.md")


# --- context builders --------------------------------------------------------


def transcript_block(
    transcript: Transcript, start: float, end: float, limit: int
) -> Optional[str]:
    segments = audio_stage.segments_between(transcript, start, end)
    if not segments:
        if not transcript.available:
            return "Transcript: none available for this flight."
        return "Transcript: nothing was said during this phase."
    lines = [f"[{s.start:.1f}-{s.end:.1f}] {s.text}" for s in segments]
    text = "\n".join(lines)
    if len(text) > limit:
        text = text[:limit] + "\n… (transcript truncated)"
    return "Transcript for this time range:\n" + text


def telemetry_block(
    rows: list[dict[str, float]], start: float, end: float, max_rows: int
) -> Optional[str]:
    slice_ = [r for r in rows if start <= r.get("time", -1) < end]
    if not slice_:
        return None
    step = max(1, len(slice_) // max_rows)
    sampled = slice_[::step][:max_rows]
    header = "time_s,lat,lon,alt_m,ground_speed_ms"
    lines = [
        ",".join(
            f"{r.get(k, float('nan')):.5f}" if k in ("latitude", "longitude")
            else f"{r.get(k, float('nan')):.1f}"
            for k in ("time", "latitude", "longitude", "altitude", "ground_speed")
        )
        for r in sampled
    ]
    return (
        "Telemetry for this time range (GPS; altitude in metres, ground speed in "
        "metres per second). You may quote these numbers:\n" + header + "\n" + "\n".join(lines)
    )


def audio_feature_block(
    rows: list[dict[str, float]], start: float, end: float, max_rows: int = 60
) -> Optional[str]:
    slice_ = [r for r in rows if start <= r["time"] < end]
    if not slice_:
        return None
    step = max(1, len(slice_) // max_rows)
    sampled = slice_[::step][:max_rows]
    lines = [f"{r['time']:.0f},{r['rms']:.4f},{r['spectral_centroid']:.0f}" for r in sampled]
    return (
        "Audio features for this time range (time_s, rms, spectral_centroid_hz):\n"
        + "\n".join(lines)
    )


def batch_frames(frames: list, size: int) -> list[list]:
    return [frames[i : i + size] for i in range(0, len(frames), size)] or []


# --- the per-call work -------------------------------------------------------


def _frame_parts(ctx: RunContext, role: str, batch: list[FrameRef], long_edge: int) -> list:
    parts: list = []
    label = "Instrument sensor" if role == "panel" else "Frame"
    for ref in batch:
        parts.append(TextPart(f"{label} at t={ref.t:.1f}s:"))
        parts.append(ImagePart(ctx.stream_dir(role) / ref.file, width=long_edge))
    return parts


def _pair_parts(
    ctx: RunContext, pairs: list[tuple[FrameRef, FrameRef]]
) -> list:
    """Scene and panel for the same moment, adjacent, so they can be compared."""
    parts: list = []
    for scene_ref, panel_ref in pairs:
        parts.append(TextPart(f"--- t={scene_ref.t:.1f}s ---\nWide view:"))
        parts.append(
            ImagePart(ctx.stream_dir("scene") / scene_ref.file, width=ctx.config.sample.long_edge)
        )
        parts.append(
            TextPart(f"Instruments at the same moment (t={panel_ref.t:.1f}s):")
        )
        parts.append(
            ImagePart(
                ctx.stream_dir("panel") / panel_ref.file,
                width=ctx.config.sample.panel.long_edge,
            )
        )
    return parts


def analyse_batch(
    ctx: RunContext,
    *,
    module: str,
    phase: str,
    frame_parts: list,
    span: tuple[float, float],
    viewpoint: Viewpoint,
    panel_aim: Optional[PanelAim],
    transcript: Transcript,
    telemetry_rows: list[dict[str, float]],
    feature_rows: list[dict[str, float]],
) -> list[Observation]:
    cfg = ctx.config.analyse
    start, end = span

    header = f"Module: {module}\nPhase: {phase}\nTime range: {start:.1f}s to {end:.1f}s"
    if ctx.manifest and ctx.manifest.duration:
        header += f" of a {ctx.manifest.duration:.0f}s flight"

    parts: list = [
        TextPart(header + "."),
        TextPart("Camera rig:\n" + json.dumps(viewpoint.model_dump(), indent=2)),
    ]
    if panel_aim is not None:
        parts.append(
            TextPart(
                "Instrument sensor check for this flight:\n"
                + json.dumps(panel_aim.model_dump(), indent=2)
            )
        )
    parts.extend(frame_parts)

    block = transcript_block(transcript, start, end, cfg.max_transcript_chars)
    if block:
        parts.append(TextPart(block))

    tele = telemetry_block(telemetry_rows, start, end, cfg.telemetry_summary_rows)
    if tele:
        parts.append(TextPart(tele))
    elif module not in ("panel", "crosscheck"):
        parts.append(
            TextPart(
                "Telemetry: none was recorded for this flight. Any airspeed or altitude "
                "must be read from an instrument frame, not estimated."
            )
        )

    if module == "engine":
        features = audio_feature_block(feature_rows, start, end)
        if features:
            parts.append(TextPart(features))

    parts.append(TextPart("Module instruction:\n\n" + module_prompt(module)))
    parts.append(
        TextPart(
            f"Return observations with module set to {module!r} and phase set to "
            f"{phase!r}. Cite timestamps from the frames above."
        )
    )

    result = ctx.llm.complete_json(
        model=ctx.config.models.strong,
        system=system_prompt(),
        parts=parts,
        schema=ObservationList,
        max_tokens=cfg.max_tokens,
        namespace=f"analyse.{module}",
    )

    # The model occasionally relabels; the caller's intent wins.
    for obs in result.observations:
        obs.module = module
        obs.phase = phase  # type: ignore[assignment]
    return result.observations


def run(ctx: RunContext) -> StageRecord:
    started = time.time()
    probe = ctx.read_json("probe.json", Probe)
    phases = ctx.read_json("phases.json", Phases)
    modules = ctx.read_json("modules.json", Modules)
    viewpoint = ctx.read_json("viewpoint.json", Viewpoint)
    panel_aim = ctx.try_read_json("panel_aim.json", PanelAim)
    transcript = ctx.try_read_json("transcript.json", Transcript) or Transcript()
    telemetry_rows = telemetry_stage.read_csv(ctx.path("telemetry.csv"))
    feature_rows = audio_stage.read_features(ctx.path("audio_features.csv"))

    frames = {
        role: sample_stage.load_frames(ctx.frames_dir, role) for role in ("scene", "panel")
    }
    index = ctx.try_read_json("frames.json", FrameIndex)
    panel_stream = index.by_role("panel") if index else None
    pair_tolerance = max(
        ctx.config.sample.panel.interval_seconds,
        panel_stream.interval if panel_stream else 0.0,
    )

    present = phases.present()
    proposed: list[Observation] = []
    calls = 0
    failures: list[str] = []

    for module in modules.enabled:
        if module not in BY_NAME:
            continue
        streams = streams_for(module)
        primary = streams[-1] if len(streams) == 1 else "scene"

        for phase in phases_for(module, present):
            spans = phases.spans_for(phase)

            if len(streams) == 1:
                pool = [
                    f
                    for span in spans
                    for f in sample_stage.frames_between(frames[primary], span.start, span.end)
                ]
                pool.sort(key=lambda f: f.t)
                if not pool:
                    continue
                batches = [
                    (
                        _frame_parts(
                            ctx,
                            primary,
                            batch,
                            ctx.config.sample.for_role(primary).long_edge,
                        ),
                        (batch[0].t, batch[-1].t),
                        primary,
                    )
                    for batch in batch_frames(pool, ctx.config.analyse.batch_for(primary))
                ]
            else:
                scene_pool = [
                    f
                    for span in spans
                    for f in sample_stage.frames_between(frames["scene"], span.start, span.end)
                ]
                scene_pool.sort(key=lambda f: f.t)
                pairs = sample_stage.pair_streams(
                    scene_pool, frames["panel"], tolerance=pair_tolerance
                )
                if not pairs:
                    continue
                batches = [
                    (
                        _pair_parts(ctx, batch),
                        (batch[0][0].t, batch[-1][0].t),
                        "both",
                    )
                    for batch in batch_frames(pairs, ctx.config.analyse.pairs_per_batch)
                ]

            for frame_parts, (lo, hi), stream_label in batches:
                try:
                    found = analyse_batch(
                        ctx,
                        module=module,
                        phase=phase,
                        frame_parts=frame_parts,
                        span=(lo, hi + pair_tolerance),
                        viewpoint=viewpoint,
                        panel_aim=panel_aim,
                        transcript=transcript,
                        telemetry_rows=telemetry_rows,
                        feature_rows=feature_rows,
                    )
                except LLMError as exc:
                    # One bad module-phase must not lose the whole flight.
                    failures.append(f"{module}/{phase}: {exc}")
                    continue
                for obs in found:
                    obs.stream = stream_label
                proposed.extend(found)
                calls += 1

    validation = ValidationContext(
        duration=probe.duration,
        # Numbers are allowed only when the module that reads instruments
        # actually ran. A --modules filter that leaves panel out therefore also
        # blocks numeric claims, which is the safer reading.
        panel_enabled=bool({"panel", "crosscheck"} & set(modules.enabled)),
        has_telemetry=len(telemetry_rows) >= 10,
        panel_frame_times=tuple(f.t for f in frames["panel"]),
        panel_frame_tolerance=ctx.config.validate_.panel_frame_tolerance,
    )
    accepted, rejections = validate(proposed, validation)

    ctx.write_json(
        "observations.json",
        Observations(
            observations=accepted,
            accepted=len(accepted),
            rejected=len(rejections.rejected),
        ),
    )
    ctx.write_json("rejected.json", rejections)

    if ctx.manifest:
        ctx.manifest.observation_count = len(accepted)
        ctx.manifest.rejection_rate = rejections.rejection_rate

    ctx.say(
        f"  analyse: {calls} calls, {len(proposed)} proposed, {len(accepted)} kept, "
        f"{len(rejections.rejected)} rejected ({rejections.rejection_rate:.0%})"
    )
    for failure in failures:
        ctx.say(f"           failed: {failure}")

    return StageRecord(
        name=NAME,
        status="ok" if accepted or not failures else "failed",
        seconds=round(time.time() - started, 3),
        detail=f"{len(accepted)} observations, {len(rejections.rejected)} rejected",
    )
