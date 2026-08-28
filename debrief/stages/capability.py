"""Stage 6 — capability.

The core of the product. Eight frames spread across the flight tell the fast
vision model where the camera is and what it can see. That descriptor decides
which analyses are allowed to run at all.

The result is cached against a hash of the eight frames, so a second flight from
the same mount costs nothing.
"""

from __future__ import annotations

import time

from ..cache import hash_files
from ..llm import ImagePart, TextPart, load_prompt
from ..models import (
    FrameRef,
    ProbeResult,
    StageRecord,
    Transcript,
    Viewpoint,
)
from ..modules import ViewpointContext, decide
from ..runs import RunContext
from . import sample as sample_stage
from . import telemetry as telemetry_stage

NAME = "capability"


def pick_frames(frames: list[FrameRef], count: int) -> list[FrameRef]:
    """Spread ``count`` picks across the flight, skipping the very ends.

    The first and last few seconds are often a lens cap, a hand, or the ground
    going past, none of which describe the mount.
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


def probe_viewpoint(ctx: RunContext, picks: list[FrameRef]) -> Viewpoint:
    """One vision call, keyed on the frame bytes so a repeat mount is free."""
    paths = [ctx.frames_dir / ref.file for ref in picks]
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


def run(ctx: RunContext) -> StageRecord:
    started = time.time()
    probe = ctx.read_json("probe.json", ProbeResult)
    frames = sample_stage.load_frames(ctx.frames_dir)
    transcript = ctx.try_read_json("transcript.json", Transcript) or Transcript()
    telemetry_rows = telemetry_stage.read_csv(ctx.path("telemetry.csv"))

    picks = pick_frames(frames, ctx.config.capability.frame_count)
    if not picks:
        raise RuntimeError("no frames to probe — run the sample stage first")

    viewpoint = probe_viewpoint(ctx, picks)
    ctx.write_json("viewpoint.json", viewpoint)

    vp_ctx = ViewpointContext(
        has_audio=bool(probe.has_audio and not ctx.no_audio),
        has_transcript=bool(transcript.available and transcript.segments),
        has_telemetry=len(telemetry_rows) >= 10,
    )
    modules = decide(viewpoint, vp_ctx, only=ctx.module_filter)
    ctx.write_json("modules.json", modules)

    ctx.say(
        f"  capability: mount={viewpoint.mount}  panel={viewpoint.visible.instrument_panel}  "
        f"lighting={viewpoint.quality.lighting}"
    )
    ctx.say(f"              enabled: {', '.join(modules.enabled) or 'none'}")
    if modules.disabled:
        ctx.say(f"              blocked: {', '.join(modules.disabled)}")

    if ctx.manifest:
        ctx.manifest.mount = viewpoint.mount
        ctx.manifest.enabled_modules = modules.enabled

    return StageRecord(
        name=NAME,
        status="ok",
        seconds=round(time.time() - started, 3),
        detail=f"mount={viewpoint.mount}, {len(modules.enabled)} modules enabled",
    )
