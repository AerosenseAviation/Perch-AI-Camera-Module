"""Driving the nine stages.

The CLI is a thin shell over this module so that a run can also be driven from a
test or a script.
"""

from __future__ import annotations

import traceback
from pathlib import Path
from typing import Optional

from .cache import ResponseCache
from .config import Config
from .cost import CostLimitExceeded, CostTracker, estimate_run
from .llm import LLMClient
from .models import ProbeResult, RunManifest, StageRecord
from .modules import MODULE_NAMES
from .runs import RunContext, new_run_dir, utcnow
from .stages import LOCAL_STAGES, STAGE_ORDER, STAGES
from .stages import sample as sample_stage


def build_context(
    video: Path,
    run_dir: Path,
    cfg: Config,
    *,
    dry_run: bool = False,
    no_audio: bool = False,
    modules: Optional[list[str]] = None,
    max_cost: Optional[float] = None,
    verbose: bool = True,
    manifest: Optional[RunManifest] = None,
) -> RunContext:
    cache = ResponseCache(cfg.cache_dir)
    tracker = CostTracker(cfg, max_cost=max_cost)
    llm = LLMClient(cfg, cache, tracker, enabled=not dry_run)
    return RunContext(
        run_dir=run_dir,
        video=video,
        config=cfg,
        llm=llm,
        tracker=tracker,
        cache=cache,
        dry_run=dry_run,
        no_audio=no_audio,
        module_filter=modules,
        manifest=manifest,
        verbose=verbose,
    )


def estimate_for(ctx: RunContext, probe: ProbeResult) -> float:
    """Predict the run cost from the probe alone, before any frame is cut."""
    cfg = ctx.config
    interval = sample_stage.choose_interval(
        probe.duration, cfg.sample.interval_seconds, cfg.sample.max_frames
    )
    frame_count = int(probe.duration / interval) + 1 if interval > 0 else 0

    # Assume a middling viewpoint: roughly half the modules, and the phases a
    # typical flight passes through.
    module_count = 6 if not ctx.module_filter else len(ctx.module_filter)
    estimate = estimate_run(
        cfg,
        frame_count=frame_count,
        module_count=module_count,
        phase_count=5,
        has_telemetry=probe.has_telemetry,
        transcript_chars=int(probe.duration * 4) if probe.has_audio and not ctx.no_audio else 0,
    )
    return estimate.total


def run_pipeline(
    video: Path,
    cfg: Config,
    *,
    dry_run: bool = False,
    no_audio: bool = False,
    modules: Optional[list[str]] = None,
    max_cost: Optional[float] = None,
    verbose: bool = True,
    run_dir: Optional[Path] = None,
    tracker: Optional[CostTracker] = None,
) -> RunContext:
    """Run every stage. Returns the context, with the manifest filled in."""
    video = Path(video)
    if modules:
        unknown = set(modules) - set(MODULE_NAMES)
        if unknown:
            raise ValueError(
                f"unknown module(s): {', '.join(sorted(unknown))}. "
                f"Known modules: {', '.join(MODULE_NAMES)}"
            )

    cfg.runs_root.mkdir(parents=True, exist_ok=True)
    run_dir = run_dir or new_run_dir(video, cfg.runs_root)

    manifest = RunManifest(
        flight_id=run_dir.name,
        video=str(video.resolve()),
        created=utcnow(),
        dry_run=dry_run,
        max_cost=max_cost,
    )
    ctx = build_context(
        video,
        run_dir,
        cfg,
        dry_run=dry_run,
        no_audio=no_audio,
        modules=modules,
        max_cost=max_cost,
        verbose=verbose,
        manifest=manifest,
    )
    if tracker is not None:
        # A batch run shares one tracker so --max-cost-total is enforced across
        # the whole folder.
        ctx.tracker = tracker
        ctx.llm.tracker = tracker

    ctx.say(f"\n{video.name} → {run_dir}")

    for name in STAGE_ORDER:
        if dry_run and name not in LOCAL_STAGES:
            manifest.record(StageRecord(name=name, status="skipped", detail="dry run"))
            continue
        try:
            record = STAGES[name](ctx)
        except CostLimitExceeded as exc:
            manifest.record(StageRecord(name=name, status="failed", detail=str(exc)))
            ctx.say(f"  {name}: stopped — {exc}")
            break
        except Exception as exc:  # a broken stage must not lose the earlier ones
            manifest.record(StageRecord(name=name, status="failed", detail=str(exc)))
            ctx.say(f"  {name}: failed — {exc}")
            if verbose:
                traceback.print_exc()
            break
        manifest.record(record)

        if name == "probe":
            probe = ctx.read_json("probe.json", ProbeResult)
            manifest.duration = probe.duration
            manifest.estimated_cost = round(estimate_for(ctx, probe), 6)
            if not dry_run:
                ctx.say(f"  estimated model cost: ${manifest.estimated_cost:.4f}")
                if max_cost is not None and manifest.estimated_cost > max_cost:
                    ctx.say(
                        f"  the estimate is above the --max-cost ceiling of "
                        f"${max_cost:.4f}; stages will stop when the ceiling is reached"
                    )

    ctx.save_manifest()
    return ctx


def run_stage(name: str, ctx: RunContext) -> StageRecord:
    if name not in STAGES:
        raise ValueError(
            f"unknown stage {name!r}. Stages: {', '.join(STAGE_ORDER)}"
        )
    return STAGES[name](ctx)
