"""The module table.

A module is a question the tool can ask of the footage. It is enabled only when
the rig supports the question, which is what stops the pipeline from inventing
panel readings the camera never resolved.

Each module also declares which stream it reads. `panel` reads the narrow
sensor. `environment` reads the wide one. `crosscheck` reads both, paired in
time, and is the module that only exists because there are two.

Each entry carries a human reason for both answers, which becomes the "What I
could not see" section, and a tip that becomes "Next time".
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Optional

from .models import ModuleDecision, Modules, PanelAim, Viewpoint

# A mount whose lens points along the flight path can see the runway on final.
FORWARD_MOUNTS = frozenset({"panel", "forward", "chest", "head", "wing", "unknown"})

# Phases that can carry an airborne observation at all.
AIRBORNE = ("takeoff", "climb", "cruise", "manoeuvre", "circuit", "approach", "landing")
ALL_PHASES = ("ground", "taxi") + AIRBORNE + ("shutdown",)


@dataclass(frozen=True)
class RigContext:
    """Everything outside the wide view that gates a module."""

    has_audio: bool = False
    has_transcript: bool = False
    has_telemetry: bool = False
    has_panel_stream: bool = False
    panel: Optional[PanelAim] = None

    @property
    def panel_readable(self) -> bool:
        """True when instruments can actually be read this flight.

        Either the narrow sensor is aimed and legible, or — on a single-camera
        rig — the wide view happens to resolve the panel.
        """
        return bool(self.panel and self.panel.usable)


# Kept as an alias so existing callers and tests keep working after the rename.
ViewpointContext = RigContext


@dataclass(frozen=True)
class ModuleSpec:
    name: str
    requires: str
    reports: str
    tip: str
    phases: tuple[str, ...]
    test: Callable[[Viewpoint, RigContext], bool]
    streams: tuple[str, ...] = ("scene",)


def _always(_vp: Viewpoint, _ctx: RigContext) -> bool:
    return True


def _panel_readable(vp: Viewpoint, ctx: RigContext) -> bool:
    if ctx.has_panel_stream:
        return ctx.panel_readable
    # Single-camera fallback: the wide view must resolve the instruments itself.
    return vp.visible.instrument_panel == "clear"


MODULE_SPECS: tuple[ModuleSpec, ...] = (
    ModuleSpec(
        name="attitude",
        requires="horizon visible",
        reports="pitch and bank changes, steepest bank, wings-level quality",
        tip="Aim the wide sensor so the horizon stays in frame through the turns.",
        phases=AIRBORNE,
        test=lambda vp, _c: vp.visible.horizon,
    ),
    ModuleSpec(
        name="pattern",
        requires="outside terrain or telemetry",
        reports="circuit shape, leg lengths, turn consistency",
        tip="Give the wide sensor a view of the ground, or record GPS telemetry.",
        phases=("circuit", "approach", "manoeuvre", "cruise", "takeoff", "landing"),
        test=lambda vp, c: vp.visible.outside_terrain or c.has_telemetry,
    ),
    ModuleSpec(
        name="landing",
        requires="forward view and runway visible",
        reports="flare, float, drift, bounce, touchdown character",
        tip="Point the wide sensor forward down the nose so the runway fills the frame on final.",
        phases=("approach", "landing"),
        test=lambda vp, _c: vp.visible.runway_on_approach and vp.mount in FORWARD_MOUNTS,
    ),
    ModuleSpec(
        name="panel",
        requires="instruments in frame and legible",
        reports="readings, configuration changes, warning lights",
        tip="Aim the narrow sensor squarely at the instrument panel and check it in the app before you fly.",
        phases=ALL_PHASES,
        test=_panel_readable,
        streams=("panel",),
    ),
    ModuleSpec(
        name="crosscheck",
        requires="both streams, with legible instruments",
        reports="what the instruments said against what the aircraft and the world were doing",
        tip="A second sensor framed on the panel is what makes this possible at all.",
        phases=AIRBORNE,
        test=lambda vp, c: c.has_panel_stream and c.panel_readable,
        streams=("scene", "panel"),
    ),
    ModuleSpec(
        name="hands",
        requires="pilot hands visible",
        reports="control inputs, throttle, flap and gear selections",
        tip="Mount the wide sensor high enough to catch the hands on the throttle and the flap lever.",
        phases=ALL_PHASES,
        test=lambda vp, _c: vp.visible.pilot_hands,
    ),
    ModuleSpec(
        name="scan",
        requires="pilot face visible",
        reports="lookout pattern, head movement, instrument dwell",
        tip="A sensor facing back at the pilot shows where the eyes and head go.",
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
        tip="Give the wide sensor a clear view out of the aircraft, not just the cabin.",
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

# Modules whose observations are allowed to carry an instrument reading.
NUMERIC_SOURCES = frozenset({"panel", "crosscheck"})


def _panel_reason(ctx: RigContext, viewpoint: Viewpoint) -> str:
    if not ctx.has_panel_stream:
        state = viewpoint.visible.instrument_panel
        return (
            f"there is no instrument sensor on this rig and the wide view shows the panel "
            f"as {state}, not clear"
        )
    aim = ctx.panel
    if aim is None:
        return "the instrument sensor was never checked"
    if aim.in_frame == "none":
        return "the instrument sensor is not pointing at the panel"
    if aim.legible == "illegible":
        reason = "the instruments are in frame but cannot be read"
        return reason + (" — glare on the glass" if aim.glare else "")
    return "the instrument sensor is aimed but the reading was not usable"


def _reason(spec: ModuleSpec, viewpoint: Viewpoint, ctx: RigContext, enabled: bool) -> str:
    if enabled:
        return f"{spec.requires} — enabled"
    if spec.name in ("radio", "callouts"):
        if not ctx.has_audio:
            return "the file has no audio track"
        return "the audio was not transcribed, so nothing spoken could be read"
    if spec.name == "engine":
        return "the file has no audio track"
    if spec.name == "panel":
        return _panel_reason(ctx, viewpoint)
    if spec.name == "crosscheck":
        if not ctx.has_panel_stream:
            return "this rig has only one sensor, so there is nothing to cross-reference"
        return _panel_reason(ctx, viewpoint)
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
    ctx: RigContext,
    *,
    only: Optional[list[str]] = None,
) -> Modules:
    """Map a rig and its install check to the modules it supports.

    ``only`` restricts the result to a user-requested subset (``--modules``). It
    can never enable a module the rig does not support.
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
                reason = "supported by this rig but not selected with --modules"
        decisions.append(
            ModuleDecision(
                module=spec.name,
                enabled=enabled,
                reason=reason,
                tip=spec.tip or None,
                streams=list(spec.streams),
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


def streams_for(module: str) -> tuple[str, ...]:
    spec = BY_NAME.get(module)
    return spec.streams if spec else ("scene",)
