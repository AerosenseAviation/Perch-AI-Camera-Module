"""The nine pipeline stages.

Each stage exposes ``run(ctx) -> StageRecord`` and is addressable by name from
``debrief stage <name> <run-dir>``. Stages read only from disk, so any one of
them can be re-run alone.
"""

from __future__ import annotations

from typing import Callable

from ..models import StageRecord
from ..runs import RunContext

from . import analyse, audio, capability, compose, probe, render, sample, segment, telemetry

STAGES: dict[str, Callable[[RunContext], StageRecord]] = {
    "probe": probe.run,
    "telemetry": telemetry.run,
    "sample": sample.run,
    "audio": audio.run,
    "segment": segment.run,
    "capability": capability.run,
    "analyse": analyse.run,
    "compose": compose.run,
    "render": render.run,
}

STAGE_ORDER: tuple[str, ...] = (
    "probe",
    "telemetry",
    "sample",
    "audio",
    "segment",
    "capability",
    "analyse",
    "compose",
    "render",
)

LOCAL_STAGES: frozenset[str] = frozenset(
    {"probe", "telemetry", "sample", "audio", "segment"}
)
"""Stages --dry-run still runs.

The first four never call a model. ``segment`` does when there is no telemetry,
but it degrades to a single honest span instead, and with telemetry it is fully
local — so a dry run still produces real phases on a GoPro file.
"""

__all__ = ["STAGES", "STAGE_ORDER", "LOCAL_STAGES"]
