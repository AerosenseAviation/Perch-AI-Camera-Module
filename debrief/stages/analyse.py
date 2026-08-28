"""Stage 7 — analyse.

One model call per enabled module per phase, with the phase's frames split into
batches. Every call carries the same context: frames with timestamps, the
transcript for that range, the telemetry slice, the viewpoint, and the module
instruction.

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
    Phases,
    ProbeResult,
    StageRecord,
    Transcript,
    Viewpoint,
)
from ..modules import BY_NAME, phases_for
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
    lines = [
        f"{r['time']:.0f},{r['rms']:.4f},{r['spectral_centroid']:.0f}" for r in sampled
    ]
    return (
        "Audio features for this time range (time_s, rms, spectral_centroid_hz):\n"
        + "\n".join(lines)
    )


def batch_frames(frames: list[FrameRef], size: int) -> list[list[FrameRef]]:
    return [frames[i : i + size] for i in range(0, len(frames), size)] or []


# --- the per-call work -------------------------------------------------------


def analyse_batch(
    ctx: RunContext,
    *,
    module: str,
    phase: str,
    batch: list[FrameRef],
    span: tuple[float, float],
    viewpoint: Viewpoint,
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
        TextPart(
            "Camera viewpoint descriptor:\n"
            + json.dumps(viewpoint.model_dump(), indent=2)
        ),
    ]

    for ref in batch:
        parts.append(TextPart(f"Frame at t={ref.t:.1f}s:"))
        parts.append(ImagePart(ctx.frames_dir / ref.file, width=ctx.config.sample.long_edge))

    block = transcript_block(transcript, start, end, cfg.max_transcript_chars)
    if block:
        parts.append(TextPart(block))

    tele = telemetry_block(telemetry_rows, start, end, cfg.telemetry_summary_rows)
    parts.append(
        TextPart(
            tele
            or "Telemetry: none was recorded for this flight. Do not state any "
               "measured airspeed or altitude."
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
    probe = ctx.read_json("probe.json", ProbeResult)
    phases = ctx.read_json("phases.json", Phases)
    modules = ctx.read_json("modules.json", Modules)
    viewpoint = ctx.read_json("viewpoint.json", Viewpoint)
    transcript = ctx.try_read_json("transcript.json", Transcript) or Transcript()
    telemetry_rows = telemetry_stage.read_csv(ctx.path("telemetry.csv"))
    feature_rows = audio_stage.read_features(ctx.path("audio_features.csv"))
    frames = sample_stage.load_frames(ctx.frames_dir)

    index = ctx.try_read_json("frames.json", FrameIndex)
    interval = index.interval if index else ctx.config.sample.interval_seconds

    present = phases.present()
    proposed: list[Observation] = []
    calls = 0
    failures: list[str] = []

    for module in modules.enabled:
        if module not in BY_NAME:
            continue
        for phase in phases_for(module, present):
            spans = phases.spans_for(phase)
            phase_frames = [
                f for span in spans for f in sample_stage.frames_between(frames, span.start, span.end)
            ]
            phase_frames.sort(key=lambda f: f.t)
            if not phase_frames:
                continue

            for batch in batch_frames(phase_frames, ctx.config.analyse.frames_per_batch):
                # Slice the transcript and telemetry to the batch, not to the
                # phase: a circuit flown three times has spans scattered across
                # the flight, and the whole span of them is not this batch.
                span_range = (batch[0].t, batch[-1].t + interval)
                try:
                    proposed.extend(
                        analyse_batch(
                            ctx,
                            module=module,
                            phase=phase,
                            batch=batch,
                            span=span_range,
                            viewpoint=viewpoint,
                            transcript=transcript,
                            telemetry_rows=telemetry_rows,
                            feature_rows=feature_rows,
                        )
                    )
                    calls += 1
                except LLMError as exc:
                    # One bad module-phase must not lose the whole flight.
                    failures.append(f"{module}/{phase}: {exc}")

    # Numbers are allowed only when the panel module actually ran. A --modules
    # filter that leaves panel out therefore also blocks numeric claims, which
    # is the stricter and safer reading of the rule.
    validation = ValidationContext(
        duration=probe.duration,
        panel_enabled="panel" in modules.enabled,
        has_telemetry=len(telemetry_rows) >= 10,
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
