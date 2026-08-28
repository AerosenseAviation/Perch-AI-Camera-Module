"""Cost estimation and accounting.

Two jobs: predict what a run will cost before it starts, and record what it
actually cost. The estimate is deliberately conservative — it assumes every
frame is sent and nothing is served from cache.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Optional

from .config import Config
from .models import ModelSpend


class CostLimitExceeded(RuntimeError):
    """Raised when a run would cross its --max-cost ceiling."""


def image_tokens(width: int, height: int) -> int:
    """Anthropic bills an image at roughly (width x height) / 750 tokens."""
    return max(1, math.ceil((width * height) / 750))


def frame_tokens(long_edge: int, aspect: float = 16 / 9) -> int:
    """Token cost of one sampled frame scaled to ``long_edge`` on the long side."""
    if aspect >= 1:
        width, height = long_edge, max(1, round(long_edge / aspect))
    else:
        width, height = max(1, round(long_edge * aspect)), long_edge
    return image_tokens(width, height)


@dataclass
class Estimate:
    total: float = 0.0
    lines: list[tuple[str, float]] = field(default_factory=list)

    def add(self, label: str, dollars: float) -> None:
        self.lines.append((label, dollars))
        self.total += dollars

    def render(self) -> str:
        width = max((len(label) for label, _ in self.lines), default=10)
        rows = [f"  {label:<{width}}  ${dollars:7.4f}" for label, dollars in self.lines]
        rows.append(f"  {'total':<{width}}  ${self.total:7.4f}")
        return "\n".join(rows)


def _dollars(cfg: Config, model: str, input_tokens: int, output_tokens: int) -> float:
    price = cfg.price(model)
    return (input_tokens / 1_000_000) * price.input + (
        output_tokens / 1_000_000
    ) * price.output


def estimate_run(
    cfg: Config,
    *,
    frame_count: int,
    module_count: int,
    phase_count: int,
    has_telemetry: bool,
    transcript_chars: int = 0,
) -> Estimate:
    """Predict the cost of a full run.

    Assumes: one capability call over ``capability.frame_count`` frames; one
    segment call per batch of frames sampled every ``vision_interval_seconds``
    when telemetry is absent; and, for stage 7, one call per module per phase
    with the phase's frames split into batches.
    """
    est = Estimate()
    per_frame = frame_tokens(cfg.sample.long_edge)
    prose = 1500 + transcript_chars // 4  # instructions plus context, in tokens

    # Stage 5 — segment fallback, only when telemetry is missing.
    if not has_telemetry and frame_count:
        seg_frames = max(1, frame_count // max(1, int(cfg.segment.vision_interval_seconds / 3)))
        seg_calls = max(1, math.ceil(seg_frames / cfg.segment.max_frames_per_call))
        seg_in = seg_frames * per_frame + seg_calls * 600
        est.add("segment", _dollars(cfg, cfg.models.fast, seg_in, seg_calls * 800))

    # Stage 6 — capability.
    cap_in = cfg.capability.frame_count * per_frame + 900
    est.add("capability", _dollars(cfg, cfg.models.fast, cap_in, cfg.capability.max_tokens // 4))

    # Stage 7 — analyse: module x phase, frames batched.
    if module_count and phase_count and frame_count:
        frames_per_phase = max(1, frame_count // max(1, phase_count))
        batches_per_phase = max(1, math.ceil(frames_per_phase / cfg.analyse.frames_per_batch))
        calls = module_count * phase_count * batches_per_phase
        per_call_in = min(frames_per_phase, cfg.analyse.frames_per_batch) * per_frame + prose
        est.add(
            "analyse",
            _dollars(cfg, cfg.models.strong, calls * per_call_in, calls * 1200),
        )

    # Stage 8 — compose: text only, one call.
    est.add("compose", _dollars(cfg, cfg.models.strong, 8000, cfg.compose.max_tokens // 2))
    return est


class CostTracker:
    """Accumulates real spend and enforces the ceiling."""

    def __init__(self, cfg: Config, max_cost: Optional[float] = None) -> None:
        self.cfg = cfg
        self.max_cost = max_cost
        self._spend: dict[str, ModelSpend] = {}

    def _entry(self, model: str) -> ModelSpend:
        if model not in self._spend:
            self._spend[model] = ModelSpend(model=model)
        return self._spend[model]

    def record(
        self,
        model: str,
        input_tokens: int,
        output_tokens: int,
        *,
        cached: bool = False,
    ) -> float:
        entry = self._entry(model)
        entry.calls += 1
        if cached:
            entry.cached_calls += 1
            return 0.0
        entry.input_tokens += input_tokens
        entry.output_tokens += output_tokens
        cost = _dollars(self.cfg, model, input_tokens, output_tokens)
        entry.cost += cost
        return cost

    def would_exceed(self, projected: float = 0.0) -> bool:
        return self.max_cost is not None and (self.total + projected) > self.max_cost

    def check(self, projected: float = 0.0, *, where: str = "run") -> None:
        if self.would_exceed(projected):
            raise CostLimitExceeded(
                f"{where}: spend ${self.total:.4f} plus ${projected:.4f} would cross "
                f"the --max-cost ceiling of ${self.max_cost:.4f}"
            )

    @property
    def total(self) -> float:
        return sum(entry.cost for entry in self._spend.values())

    def spend(self) -> list[ModelSpend]:
        return sorted(self._spend.values(), key=lambda s: s.model)

    def render(self) -> str:
        if not self._spend:
            return "  no model calls"
        rows = []
        for entry in self.spend():
            rows.append(
                f"  {entry.model:<22} {entry.calls:>4} calls "
                f"({entry.cached_calls} cached)  "
                f"{entry.input_tokens:>8} in  {entry.output_tokens:>7} out  "
                f"${entry.cost:7.4f}"
            )
        rows.append(f"  {'total':<22} ${self.total:.4f}")
        return "\n".join(rows)
