"""The module table.

A module is a question the tool can ask of the footage. It is enabled only when
the viewpoint supports the question, which is what stops the pipeline from
inventing panel readings on a wing-cam flight.

Each entry carries a human reason for both answers, which becomes the "What I
could not see" section, and a camera tip that becomes "Next time".
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Optional

from .models import ModuleDecision, Modules, Viewpoint

# A mount whose lens points along the flight path can see the runway on final.
FORWARD_MOUNTS = frozenset({"panel", "forward", "chest", "head", "wing", "unknown"})

# Phases that can carry an airborne observation at all.
AIRBORNE = ("takeoff", "climb", "cruise", "manoeuvre", "circuit", "approach", "landing")
ALL_PHASES = ("ground", "taxi") + AIRBORNE + ("shutdown",)


@dataclass(frozen=True)
class ModuleSpec:
    name: str
    requires: str
    reports: str
    tip: str
    phases: tuple[str, ...]
    test: Callable[[Viewpoint, "ViewpointContext"], bool]


@dataclass(frozen=True)
class ViewpointContext:
    """Everything outside the viewpoint that gates a module.

    "Audio present" in the specification's table splits in two here. `engine`
    reads the waveform and needs only an audio track. `radio` and `callouts`
    read words and need a transcript as well — enabling them without one buys
    a round of calls that can only come back empty.
    """

    has_audio: bool = False
    has_transcript: bool = False
    has_telemetry: bool = False


def _always(_vp: Viewpoint, _ctx: ViewpointContext) -> bool:
    return True


MODULE_SPECS: tuple[ModuleSpec, ...] = (
    ModuleSpec(
        name="attitude",
        requires="horizon visible",
        reports="pitch and bank changes, steepest bank, wings-level quality",
        tip="Aim the camera so the horizon stays in frame through the turns.",
        phases=AIRBORNE,
        test=lambda vp, _c: vp.visible.horizon,
    ),
    ModuleSpec(
        name="pattern",
        requires="outside terrain or telemetry",
        reports="circuit shape, leg lengths, turn consistency",
        tip="A camera with a view of the ground, or a GoPro with GPS on, gives the circuit shape.",
        phases=("circuit", "approach", "manoeuvre", "cruise", "takeoff", "landing"),
        test=lambda vp, c: vp.visible.outside_terrain or c.has_telemetry,
    ),
    ModuleSpec(
        name="landing",
        requires="forward view and runway visible",
        reports="flare, float, drift, bounce, touchdown character",
        tip="Point the camera forward down the nose so the runway fills the frame on final.",
        phases=("approach", "landing"),
        test=lambda vp, _c: vp.visible.runway_on_approach and vp.mount in FORWARD_MOUNTS,
    ),
    ModuleSpec(
        name="panel",
        requires="instrument panel clear",
        reports="readings, configuration changes, warning lights",
        tip="Move the camera back or up so the whole panel is legible, not just the top of it.",
        phases=ALL_PHASES,
        test=lambda vp, _c: vp.visible.instrument_panel == "clear",
    ),
    ModuleSpec(
        name="hands",
        requires="pilot hands visible",
        reports="control inputs, throttle, flap and gear selections",
        tip="A camera over the shoulder catches the hands on the throttle and the flap lever.",
        phases=ALL_PHASES,
        test=lambda vp, _c: vp.visible.pilot_hands,
    ),
    ModuleSpec(
        name="scan",
        requires="pilot face visible",
        reports="lookout pattern, head movement, instrument dwell",
        tip="A camera facing back at the pilot shows where the eyes and head go.",
        phases=AIRBORNE,
        test=lambda vp, _c: vp.visible.pilot_face,
    ),
    ModuleSpec(
        name="radio",
        requires="audio present and transcribed",
        reports="calls made, phraseology, readbacks",
        tip="Record intercom or headset audio so the radio calls are on the tape.",
        phases=ALL_PHASES,
        test=lambda _vp, c: c.has_audio and c.has_transcript,
    ),
    ModuleSpec(
        name="callouts",
        requires="audio present and transcribed",
        reports="verbal callouts, checklist discipline",
        tip="Record intercom audio so spoken checklists and callouts are captured.",
        phases=ALL_PHASES,
        test=lambda _vp, c: c.has_audio and c.has_transcript,
    ),
    ModuleSpec(
        name="engine",
        requires="audio present",
        reports="power changes by engine note, alert tones",
        tip="Leave the microphone unmuted; the engine note carries the power changes.",
        phases=ALL_PHASES,
        test=lambda _vp, c: c.has_audio,
    ),
    ModuleSpec(
        name="environment",
        requires="outside terrain visible",
        reports="weather, cloud, light, terrain, visible traffic",
        tip="Give the camera a clear view out of the aircraft, not just the cabin.",
        phases=("climb", "cruise", "manoeuvre", "circuit", "approach", "takeoff", "landing"),
        test=lambda vp, _c: vp.visible.outside_terrain,
    ),
    ModuleSpec(
        name="highlights",
        requires="always",
        reports="the best frames of the flight",
        tip="",
        phases=ALL_PHASES,
        test=_always,
    ),
    ModuleSpec(
        name="story",
        requires="always",
        reports="the narrative",
        tip="",
        phases=ALL_PHASES,
        test=_always,
    ),
)

MODULE_NAMES: tuple[str, ...] = tuple(spec.name for spec in MODULE_SPECS)
BY_NAME: dict[str, ModuleSpec] = {spec.name: spec for spec in MODULE_SPECS}

# Modules whose output would carry a number the footage cannot support unless
# the panel is readable. The validator uses this set.
NUMERIC_SOURCES = frozenset({"panel"})


def _reason(spec: ModuleSpec, viewpoint: Viewpoint, ctx: ViewpointContext, enabled: bool) -> str:
    if enabled:
        return f"{spec.requires} — enabled"
    if spec.name in ("radio", "callouts"):
        if not ctx.has_audio:
            return "the file has no audio track"
        return "the audio was not transcribed, so nothing spoken could be read"
    if spec.name == "engine":
        return "the file has no audio track"
    if spec.name == "panel":
        state = viewpoint.visible.instrument_panel
        return f"the instrument panel is {state} in this viewpoint, not clear"
    if spec.name == "landing":
        if not viewpoint.visible.runway_on_approach:
            return "the runway is not in frame on approach"
        return f"a {viewpoint.mount} mount does not give a forward view of the runway"
    if spec.name == "attitude":
        return "the horizon is not in frame"
    if spec.name == "hands":
        return "the pilot's hands are not in frame"
    if spec.name == "scan":
        return "the pilot's face is not in frame"
    if spec.name == "environment":
        return "there is no view of the outside world"
    if spec.name == "pattern":
        return "there is neither a view of the ground nor GPS telemetry"
    return f"requires {spec.requires}"


def decide(
    viewpoint: Viewpoint,
    ctx: ViewpointContext,
    *,
    only: Optional[list[str]] = None,
) -> Modules:
    """Map a viewpoint to the modules it supports.

    ``only`` restricts the result to a user-requested subset (``--modules``). It
    can never enable a module the viewpoint does not support.
    """
    requested = {m.strip() for m in only} if only else None
    if requested:
        unknown = requested - set(MODULE_NAMES)
        if unknown:
            raise ValueError(
                f"unknown module(s): {', '.join(sorted(unknown))}. "
                f"Known modules: {', '.join(MODULE_NAMES)}"
            )

    decisions: list[ModuleDecision] = []
    for spec in MODULE_SPECS:
        supported = bool(spec.test(viewpoint, ctx))
        enabled = supported
        reason = _reason(spec, viewpoint, ctx, supported)
        if requested is not None and spec.name not in requested:
            enabled = False
            if supported:
                reason = "supported by this viewpoint but not selected with --modules"
        decisions.append(
            ModuleDecision(
                module=spec.name,
                enabled=enabled,
                reason=reason,
                tip=spec.tip or None,
            )
        )

    return Modules(
        enabled=[d.module for d in decisions if d.enabled],
        disabled=[d.module for d in decisions if not d.enabled],
        decisions=decisions,
    )


def phases_for(module: str, present: list[str]) -> list[str]:
    """Phases a module should be asked about, limited to those in this flight."""
    spec = BY_NAME.get(module)
    if not spec:
        return []
    return [p for p in spec.phases if p in present]
