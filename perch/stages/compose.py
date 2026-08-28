"""Stage 8 — compose.

One call to the strong model turns the validated observations into the debrief
the pilot reads. The model is given the blocked-module list explicitly, because
"What I could not see" has to be accurate rather than modest.
"""

from __future__ import annotations

import json
import time

from ..llm import LLMError, TextPart, load_prompt
from ..models import (
    Debrief,
    Modules,
    Observations,
    PanelAim,
    Phases,
    Probe,
    StageRecord,
    Viewpoint,
)
from ..modules import BY_NAME
from ..runs import RunContext

NAME = "compose"


def system_prompt() -> str:
    return load_prompt("compose.md", rules=load_prompt("rules.md").strip())


def blocked_summary(modules: Modules) -> str:
    lines = []
    for name in modules.disabled:
        decision = modules.decision(name)
        spec = BY_NAME.get(name)
        reports = spec.reports if spec else name
        reason = decision.reason if decision else "not supported by this viewpoint"
        tip = f" Camera tip: {decision.tip}" if decision and decision.tip else ""
        lines.append(f"- {name} ({reports}): {reason}.{tip}")
    return "\n".join(lines) or "- nothing was blocked; every module ran."


def phases_summary(phases: Phases) -> str:
    return "\n".join(
        f"- {span.phase}: {span.start:.0f}s to {span.end:.0f}s "
        f"({span.duration:.0f}s)"
        for span in phases.spans
    ) or "- the flight was not segmented into phases."


def fallback_debrief(modules: Modules, note: str) -> Debrief:
    """What to write when there is nothing to write.

    Silence is the honest answer, so say it plainly rather than manufacturing a
    story out of nothing.
    """
    return Debrief(
        flight_story=note,
        highlights=[],
        takeaways=[],
        could_not_see=[
            f"{name}: {(modules.decision(name).reason if modules.decision(name) else 'not supported')}"
            for name in modules.disabled
        ],
        next_time=next(
            (
                d.tip
                for name in modules.disabled
                if (d := modules.decision(name)) and d.tip
            ),
            "",
        ),
    )


def run(ctx: RunContext) -> StageRecord:
    started = time.time()
    probe = ctx.read_json("probe.json", Probe)
    phases = ctx.read_json("phases.json", Phases)
    modules = ctx.read_json("modules.json", Modules)
    viewpoint = ctx.read_json("viewpoint.json", Viewpoint)
    observations = ctx.read_json("observations.json", Observations)

    kept = observations.observations[: ctx.config.compose.max_observations]

    if not kept:
        debrief = fallback_debrief(
            modules,
            "The analysis produced no observations this camera position could support, "
            "so there is no story to tell from this footage.",
        )
        ctx.write_json("debrief.json", debrief)
        ctx.say("  compose: no observations — wrote an empty debrief")
        return StageRecord(
            name=NAME,
            status="skipped",
            seconds=round(time.time() - started, 3),
            detail="no observations",
        )

    panel_aim = ctx.try_read_json("panel_aim.json", PanelAim)
    rig = viewpoint.rig or f"{viewpoint.mount} mount"
    sensors = "wide sensor" + (" and instrument sensor" if probe.has_panel else " only")

    parts = [
        TextPart(
            f"Flight: {probe.scene.filename}, {probe.duration:.0f} seconds long.\n"
            f"Rig: {rig} — {sensors}.\n"
            f"Lighting: {viewpoint.quality.lighting}."
        ),
        TextPart("Phases of the flight:\n" + phases_summary(phases)),
        TextPart("Camera rig:\n" + json.dumps(viewpoint.model_dump(), indent=2)),
        TextPart(
            "Instrument sensor check for this flight:\n"
            + json.dumps(panel_aim.model_dump(), indent=2)
            if panel_aim is not None
            else "This rig had no dedicated instrument sensor, so no instrument "
            "readings were available."
        ),
        TextPart(
            "Analyses this viewpoint blocked, with the reason and the camera tip "
            "that would unlock each:\n" + blocked_summary(modules)
        ),
        TextPart(
            f"All {len(kept)} validated observations from this flight:\n"
            + json.dumps([o.model_dump() for o in kept], indent=2)
        ),
        TextPart("Write the debrief."),
    ]

    try:
        debrief = ctx.llm.complete_json(
            model=ctx.config.models.strong,
            system=system_prompt(),
            parts=parts,
            schema=Debrief,
            max_tokens=ctx.config.compose.max_tokens,
            namespace="compose",
        )
    except LLMError as exc:
        debrief = fallback_debrief(
            modules, f"The debrief could not be composed from this flight ({exc})."
        )
        ctx.write_json("debrief.json", debrief)
        ctx.say(f"  compose: failed — {exc}")
        return StageRecord(
            name=NAME, status="failed", seconds=round(time.time() - started, 3), detail=str(exc)
        )

    ctx.write_json("debrief.json", debrief)
    ctx.say(
        f"  compose: {len(debrief.highlights)} highlights, "
        f"{len(debrief.takeaways)} takeaways"
    )
    return StageRecord(
        name=NAME,
        status="ok",
        seconds=round(time.time() - started, 3),
        detail=f"{len(debrief.highlights)} highlights",
    )
