"""The four rejection rules from section 7 of the specification."""

from __future__ import annotations

import pytest

from debrief.models import Observation
from debrief.validate import (
    RULE_LOW_CONFIDENCE_SAFETY,
    RULE_NO_TIMESTAMP,
    RULE_NUMBER_WITHOUT_PANEL_FRAME,
    RULE_TIMESTAMP_OUT_OF_RANGE,
    RULE_UNSUPPORTED_NUMBER,
    ValidationContext,
    mentions_measured_quantity,
    validate,
)


def obs(**overrides) -> Observation:
    base = dict(
        module="landing",
        phase="landing",
        timestamps=[100.0],
        claim="The footage shows the aircraft drifting right of the centreline.",
        provenance="visual",
        confidence="high",
        interest="skill",
    )
    base.update(overrides)
    return Observation(**base)


NO_INSTRUMENTS = ValidationContext(duration=600.0, panel_enabled=False, has_telemetry=False)
PANEL_READABLE = ValidationContext(duration=600.0, panel_enabled=True, has_telemetry=False)
WITH_TELEMETRY = ValidationContext(duration=600.0, panel_enabled=False, has_telemetry=True)

# A two-sensor rig: the instruments are readable, and the sensor produced frames
# every 3 seconds from 90s to 120s and nowhere else.
PANEL_SENSOR = ValidationContext(
    duration=600.0,
    panel_enabled=True,
    has_telemetry=False,
    panel_frame_times=tuple(float(t) for t in range(90, 121, 3)),
    panel_frame_tolerance=5.0,
)


def rules_for(observation, ctx=NO_INSTRUMENTS) -> list[str]:
    _, rejections = validate([observation], ctx)
    return rejections.rejected[0].rules if rejections.rejected else []


# --- rule 1: a timestamp outside the flight ---------------------------------


def test_rejects_a_timestamp_past_the_end_of_the_flight():
    assert RULE_TIMESTAMP_OUT_OF_RANGE in rules_for(obs(timestamps=[900.0]))


def test_rejects_a_negative_timestamp():
    assert RULE_TIMESTAMP_OUT_OF_RANGE in rules_for(obs(timestamps=[-2.0]))


def test_allows_a_timestamp_rounded_to_the_final_second():
    assert rules_for(obs(timestamps=[600.4])) == []


# --- rule 2: no timestamp ----------------------------------------------------


def test_rejects_an_observation_with_no_timestamp():
    assert RULE_NO_TIMESTAMP in rules_for(obs(timestamps=[]))


# --- rule 3: an unsupported number ------------------------------------------


@pytest.mark.parametrize(
    "claim",
    [
        "The aircraft crosses the threshold at 65 knots.",
        "It touches down at 62kt.",
        "The aircraft levels off at 2,500 feet.",
        "Passing 1200 ft on the climb out.",
        "The altimeter reads 3000 MSL.",
        "The footage shows 500 ft AGL over the ridge.",
        "Airspeed of 70 on short final.",
        "Cruising at FL085.",
        "The engine settles at 2400 rpm.",
        "Ground speed 90 mph down the valley.",
        "It drifts 5 m right of the centreline.",
        "A rate of climb of 700 fpm.",
    ],
)
def test_rejects_a_read_off_number_with_no_panel_and_no_telemetry(claim):
    assert RULE_UNSUPPORTED_NUMBER in rules_for(obs(claim=claim))


@pytest.mark.parametrize(
    "claim",
    [
        "The aircraft drifts right of the centreline before the wheels touch.",
        "The engine note drops noticeably as the power comes back.",
        "The pilot selects flap in two stages on final.",
        "This is the third circuit of the session.",
        "The aircraft lines up on runway 27.",
        "The horizon rolls to roughly a third of the way to vertical.",
        "The flare is one smooth movement rather than several.",
    ],
)
def test_allows_a_claim_that_states_no_instrument_reading(claim):
    assert rules_for(obs(claim=claim)) == []


def test_allows_numbers_once_the_panel_is_readable():
    numeric = obs(claim="The airspeed indicator reads 65 knots on short final.")
    assert rules_for(numeric, PANEL_READABLE) == []


def test_allows_numbers_once_telemetry_exists():
    numeric = obs(claim="Ground speed is 45 metres per second across the ridge.")
    assert rules_for(numeric, WITH_TELEMETRY) == []


def test_detector_returns_the_offending_text():
    assert mentions_measured_quantity("It crosses at 65 knots.") == "65 knots"
    assert mentions_measured_quantity("A tidy landing.") is None


# --- rule 4: a low-confidence safety claim ----------------------------------


def test_rejects_a_low_confidence_safety_claim():
    risky = obs(confidence="low", interest="safety")
    assert RULE_LOW_CONFIDENCE_SAFETY in rules_for(risky)


def test_allows_a_low_confidence_claim_that_is_not_about_safety():
    assert rules_for(obs(confidence="low", interest="character")) == []


def test_allows_a_high_confidence_safety_claim():
    assert rules_for(obs(confidence="high", interest="safety")) == []


# --- bookkeeping -------------------------------------------------------------


def test_rejection_rate_is_recorded():
    proposed = [
        obs(),
        obs(timestamps=[]),
        obs(claim="It crosses the fence at 70 knots."),
        obs(confidence="low", interest="safety"),
    ]
    accepted, rejections = validate(proposed, NO_INSTRUMENTS)
    assert len(accepted) == 1
    assert rejections.total_proposed == 4
    assert rejections.rejection_rate == 0.75
    assert all(entry.detail for entry in rejections.rejected)


def test_one_observation_can_break_several_rules():
    bad = obs(timestamps=[], claim="At 65 knots.", confidence="low", interest="safety")
    broken = rules_for(bad)
    assert set(broken) == {
        RULE_NO_TIMESTAMP,
        RULE_UNSUPPORTED_NUMBER,
        RULE_LOW_CONFIDENCE_SAFETY,
    }


# --- rule 5: a number from a moment the instrument sensor did not cover ------


def test_a_number_is_allowed_where_the_instrument_sensor_was_looking():
    reading = obs(timestamps=[102.0], claim="The airspeed indicator reads 65 knots.")
    assert rules_for(reading, PANEL_SENSOR) == []


def test_a_number_is_rejected_from_a_moment_the_sensor_did_not_cover():
    # Readable instruments in general, but nothing was captured at t=400.
    reading = obs(timestamps=[400.0], claim="The airspeed indicator reads 65 knots.")
    broken = rules_for(reading, PANEL_SENSOR)
    assert RULE_NUMBER_WITHOUT_PANEL_FRAME in broken
    assert RULE_UNSUPPORTED_NUMBER not in broken


def test_the_tolerance_is_honoured_at_the_edge():
    just_inside = obs(timestamps=[124.0], claim="The tachometer reads 2300 rpm.")
    just_outside = obs(timestamps=[130.0], claim="The tachometer reads 2300 rpm.")
    assert rules_for(just_inside, PANEL_SENSOR) == []
    assert RULE_NUMBER_WITHOUT_PANEL_FRAME in rules_for(just_outside, PANEL_SENSOR)


def test_a_claim_with_no_number_is_untouched_by_the_panel_frame_rule():
    plain = obs(timestamps=[400.0], claim="The flap lever moves to the first stage.")
    assert rules_for(plain, PANEL_SENSOR) == []


def test_telemetry_exempts_a_number_from_needing_a_panel_frame():
    """GPS covers the whole flight, so it does not need an instrument frame."""
    tracked = ValidationContext(
        duration=600.0,
        panel_enabled=True,
        has_telemetry=True,
        panel_frame_times=PANEL_SENSOR.panel_frame_times,
    )
    reading = obs(timestamps=[400.0], claim="Ground speed is 45 metres per second.")
    assert rules_for(reading, tracked) == []


def test_a_single_camera_rig_is_not_subject_to_the_panel_frame_rule():
    """With no second sensor there are no panel frames to demand."""
    reading = obs(timestamps=[400.0], claim="The airspeed indicator reads 65 knots.")
    assert rules_for(reading, PANEL_READABLE) == []


def test_panel_coverage_helper():
    assert PANEL_SENSOR.has_panel_stream is True
    assert PANEL_SENSOR.panel_covers([100.0]) is True
    assert PANEL_SENSOR.panel_covers([500.0]) is False
    # Any one cited timestamp being covered is enough.
    assert PANEL_SENSOR.panel_covers([500.0, 100.0]) is True
    assert NO_INSTRUMENTS.has_panel_stream is False
