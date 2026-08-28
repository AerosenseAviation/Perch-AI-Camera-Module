"""The validator.

Prompt rules make a confident wrong statement less likely. Code makes it
impossible to ship. This module runs after stage 7 and drops any observation
that breaks one of the four rules in the specification.

Every rejection is written to ``rejected.json``. The rejection rate is a quality
signal: a prompt change that pushes it up has made the model less careful.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

from .models import Observation, RejectedObservation, Rejections

# Rule identifiers, used in rejected.json so a rate can be broken down by cause.
RULE_TIMESTAMP_OUT_OF_RANGE = "timestamp_out_of_range"
RULE_NO_TIMESTAMP = "no_timestamp"
RULE_UNSUPPORTED_NUMBER = "unsupported_number"
RULE_LOW_CONFIDENCE_SAFETY = "low_confidence_safety"

# A number carrying a unit that only an instrument or a GPS can supply.
_UNIT_NUMBER = re.compile(
    r"""\b\d[\d,]*(?:\.\d+)?\s*
        (?:kt|kts|knot|knots|kias|kcas|mph|km/?h|kph
          |ft|feet|foot|metres|meters|m\b
          |rpm|hpa|inhg|fpm)\b""",
    re.IGNORECASE | re.VERBOSE,
)

# "airspeed of 65", "climbing through 2000", "altitude 1,200".
_QUANTITY_PHRASE = re.compile(
    r"""\b(?:airspeed|air\s?speed|indicated|ias|kias|groundspeed|ground\s?speed
          |altitude|altimeter|height|agl|msl|flight\s?level|fl
          |rpm|manifold|vertical\s?speed|rate\s?of\s?climb)\b
        [^.;]{0,40}?\b\d[\d,]*(?:\.\d+)?\b""",
    re.IGNORECASE | re.VERBOSE,
)

# "1,200 AGL", "3000 MSL", "FL085".
_TRAILING_DATUM = re.compile(
    r"(?:\b\d[\d,]*(?:\.\d+)?\s*(?:ft\s*)?(?:agl|msl)\b)|(?:\bfl\s?\d{2,3}\b)",
    re.IGNORECASE,
)


def mentions_measured_quantity(text: str) -> Optional[str]:
    """Return the offending substring when a claim states a read-off number."""
    for pattern in (_UNIT_NUMBER, _QUANTITY_PHRASE, _TRAILING_DATUM):
        match = pattern.search(text)
        if match:
            return match.group(0).strip()
    return None


@dataclass
class ValidationContext:
    duration: float
    panel_enabled: bool
    has_telemetry: bool

    @property
    def numbers_allowed(self) -> bool:
        return self.panel_enabled or self.has_telemetry


def check(obs: Observation, ctx: ValidationContext) -> tuple[list[str], str]:
    """Return the rules an observation breaks, with a human explanation."""
    broken: list[str] = []
    details: list[str] = []

    if not obs.timestamps:
        broken.append(RULE_NO_TIMESTAMP)
        details.append("no frame timestamp was cited")
    else:
        # A small overrun is the model rounding to the end of the clip; anything
        # past that is a timestamp for a moment that does not exist.
        limit = ctx.duration + 1.0
        bad = [t for t in obs.timestamps if t < 0 or t > limit]
        if bad:
            broken.append(RULE_TIMESTAMP_OUT_OF_RANGE)
            details.append(
                f"timestamp(s) {', '.join(f'{t:.1f}' for t in bad)} fall outside the "
                f"{ctx.duration:.1f}s flight"
            )

    if not ctx.numbers_allowed:
        offender = mentions_measured_quantity(obs.claim)
        if offender:
            broken.append(RULE_UNSUPPORTED_NUMBER)
            details.append(
                f"states {offender!r} with no readable panel and no telemetry to read it from"
            )

    if obs.confidence == "low" and obs.interest == "safety":
        broken.append(RULE_LOW_CONFIDENCE_SAFETY)
        details.append("a low-confidence safety claim is worse than no claim")

    return broken, "; ".join(details)


def validate(
    observations: list[Observation], ctx: ValidationContext
) -> tuple[list[Observation], Rejections]:
    """Split proposed observations into accepted and rejected."""
    accepted: list[Observation] = []
    rejected: list[RejectedObservation] = []

    for obs in observations:
        broken, detail = check(obs, ctx)
        if broken:
            rejected.append(RejectedObservation(observation=obs, rules=broken, detail=detail))
        else:
            accepted.append(obs)

    total = len(observations)
    return accepted, Rejections(
        rejected=rejected,
        total_proposed=total,
        rejection_rate=round(len(rejected) / total, 4) if total else 0.0,
    )
