"""Stage 6 — capability.

Two jobs, and on a known rig the second matters more than the first.

**What can the wide sensor see?** On an unknown camera this is inferred from
eight frames. On a named rig the answer is already known, so the profile is used
and the model call is skipped entirely — that is a free, exact answer instead of
a paid, approximate one.

**Is the instrument sensor actually aimed and readable?** This one always costs
a call, because it is a property of *this* installation on *this* flight. It
produces the aim feedback the app shows the pilot, and it decides whether the
debrief is allowed to state a number at all. A panel sensor pointing at the
glareshield is the most likely way this product fails in the field, and it fails
silently unless something checks.
"""

from __future__ import annotations

import time
from typing import Optional

from ..cache import hash_files
from ..config import RigProfile
from ..llm import ImagePart, LLMError, TextPart, load_prompt
from ..models import (
    FrameRef,
    PanelAim,
    Probe,
    StageRecord,
    Transcript,
    Viewpoint,
    ViewpointQuality,
    ViewpointVisible,
)
from ..modules import RigContext, decide
from ..runs import RunContext
from . import sample as sample_stage
from . import telemetry as telemetry_stage

NAME = "capability"


def pick_frames(frames: list[FrameRef], count: int) -> list[FrameRef]:
    """Spread ``count`` picks across the flight, skipping the very ends.

    The first and last few seconds are often a hand over the lens or the ground
    going past, neither of which describes the installation.
    """
    if not frames:
        return []
    if len(frames) <= count:
        return list(frames)
    lo, hi = 0.04, 0.96
    picks: list[FrameRef] = []
    for i in range(count):
        frac = lo + (hi - lo) * (i / (count - 1) if count > 1 else 0.5)
        index = min(len(frames) - 1, max(0, round(frac * (len(frames) - 1))))
        ref = frames[index]
        if not picks or picks[-1].t != ref.t:
            picks.append(ref)
    return picks


def viewpoint_from_profile(name: str, profile: RigProfile) -> Viewpoint:
    """A known installation needs no guessing."""
    return Viewpoint(
        mount=profile.mount,  # type: ignore[arg-type]
        visible=ViewpointVisible(
            # A rig with a dedicated instrument sensor leaves the wide view's
            # own panel visibility unclaimed; the aim check answers that.
            instrument_panel="none",
            horizon=profile.horizon,
            runway_on_approach=profile.runway_on_approach,
            pilot_hands=profile.pilot_hands,
            pilot_face=profile.pilot_face,
            wing_or_airframe=profile.wing_or_airframe,
            outside_terrain=profile.outside_terrain,
            other_occupants=profile.other_occupants,
        ),
        quality=ViewpointQuality(),
        notes=profile.description,
        source="profile",
        rig=name,
    )


def probe_viewpoint(ctx: RunContext, picks: list[FrameRef]) -> Viewpoint:
    """One vision call, keyed on the frame bytes so a repeat mount is free."""
    paths = [ctx.stream_dir("scene") / ref.file for ref in picks]
    frame_hash = hash_files(paths)

    parts: list = [
        TextPart(
            f"Here are {len(picks)} frames spread across one flight, in time order. "
            "Describe the camera's viewpoint."
        )
    ]
    for ref, path in zip(picks, paths):
        parts.append(TextPart(f"Frame at t={ref.t:.1f}s:"))
        parts.append(ImagePart(path, width=ctx.config.sample.long_edge))

    # The cache key is the frame hash plus the model and prompt, so editing the
    # prompt correctly invalidates the entry.
    key = ctx.cache.key(
        {
            "frames": frame_hash,
            "model": ctx.config.models.fast,
            "prompt": load_prompt("capability.md"),
            "schema": Viewpoint.model_json_schema(),
        }
    )

    return ctx.llm.complete_json(
        model=ctx.config.models.fast,
        system=load_prompt("capability.md"),
        parts=parts,
        schema=Viewpoint,
        max_tokens=ctx.config.capability.max_tokens,
        namespace="viewpoint",
        cache_key=key,
    )


def check_panel_aim(ctx: RunContext, picks: list[FrameRef]) -> PanelAim:
    """Install QA on the narrow sensor. Never cached across flights.

    Aim is a property of one installation on one day — the mount gets knocked,
    the sun moves, a different aircraft has a different panel — so this is keyed
    on the actual frames rather than reused from a previous run of the same rig.
    """
    paths = [ctx.stream_dir("panel") / ref.file for ref in picks]

    parts: list = [
        TextPart(
            f"Here are {len(picks)} frames from the instrument sensor, spread across one "
            "flight, in time order. Judge whether the instruments can be read."
        )
    ]
    for ref, path in zip(picks, paths):
        parts.append(TextPart(f"Frame at t={ref.t:.1f}s:"))
        parts.append(ImagePart(path, width=ctx.config.sample.panel.long_edge))

    return ctx.llm.complete_json(
        model=ctx.config.models.fast,
        system=load_prompt("panel_aim.md"),
        parts=parts,
        schema=PanelAim,
        max_tokens=ctx.config.capability.max_tokens,
        namespace="panel_aim",
    )


def run(ctx: RunContext) -> StageRecord:
    started = time.time()
    probe = ctx.read_json("probe.json", Probe)
    transcript = ctx.try_read_json("transcript.json", Transcript) or Transcript()
    telemetry_rows = telemetry_stage.read_csv(ctx.path("telemetry.csv"))

    scene_frames = sample_stage.load_frames(ctx.frames_dir, "scene")
    panel_frames = sample_stage.load_frames(ctx.frames_dir, "panel")
    if not scene_frames:
        raise RuntimeError("no scene frames to work from — run the sample stage first")

    # --- what the wide sensor sees ---------------------------------------
    rig_name = ctx.rig or ctx.config.rig.default
    profile = ctx.config.rig.profile(rig_name)
    if profile is not None:
        viewpoint = viewpoint_from_profile(rig_name, profile)
    else:
        viewpoint = probe_viewpoint(ctx, pick_frames(scene_frames, ctx.config.capability.frame_count))
    ctx.write_json("viewpoint.json", viewpoint)

    # --- whether the instruments are readable ----------------------------
    aim: Optional[PanelAim] = None
    if panel_frames:
        picks = pick_frames(panel_frames, ctx.config.capability.panel_frame_count)
        try:
            aim = check_panel_aim(ctx, picks)
        except LLMError as exc:
            # An unchecked panel is an unreadable panel. Failing closed here is
            # what keeps an unverified number out of the debrief.
            aim = PanelAim(
                in_frame="none",
                legible="illegible",
                notes=f"the aim check could not be run: {exc}",
            )
        ctx.write_json("panel_aim.json", aim)

    vp_ctx = RigContext(
        has_audio=bool(probe.has_audio and not ctx.no_audio),
        has_transcript=bool(transcript.available and transcript.segments),
        has_telemetry=len(telemetry_rows) >= 10,
        has_panel_stream=bool(panel_frames),
        panel=aim,
    )
    modules = decide(viewpoint, vp_ctx, only=ctx.module_filter)
    ctx.write_json("modules.json", modules)

    source = "rig profile" if profile is not None else "probed from frames"
    ctx.say(f"  capability: mount={viewpoint.mount} ({source})")
    if aim is not None:
        ctx.say(
            f"              panel: {aim.in_frame} in frame, {aim.legible}, "
            f"{aim.panel_type}"
            + (f" — {', '.join(aim.instruments[:5])}" if aim.instruments else "")
        )
        if aim.aim_hint:
            ctx.say(f"              aim: {aim.aim_hint}")
    ctx.say(f"              enabled: {', '.join(modules.enabled) or 'none'}")
    if modules.disabled:
        ctx.say(f"              blocked: {', '.join(modules.disabled)}")

    if ctx.manifest:
        ctx.manifest.mount = viewpoint.mount
        ctx.manifest.rig = rig_name if profile is not None else None
        ctx.manifest.panel_legible = aim.legible if aim else None
        ctx.manifest.enabled_modules = modules.enabled

    detail = f"mount={viewpoint.mount}, {len(modules.enabled)} modules enabled"
    if aim is not None:
        detail += f", panel {aim.legible}"
    return StageRecord(
        name=NAME, status="ok", seconds=round(time.time() - started, 3), detail=detail
    )
