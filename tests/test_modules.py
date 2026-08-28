"""The module table: a module runs only when the rig supports it."""

from __future__ import annotations

import pytest

from perch.models import PanelAim, Viewpoint, ViewpointVisible
from perch.modules import MODULE_NAMES, RigContext, decide, phases_for, streams_for


def viewpoint(mount="panel", **visible) -> Viewpoint:
    return Viewpoint(mount=mount, visible=ViewpointVisible(**visible))


NO_AUDIO = RigContext(has_audio=False, has_transcript=False, has_telemetry=False)
WITH_AUDIO = RigContext(has_audio=True, has_transcript=True, has_telemetry=False)
UNTRANSCRIBED = RigContext(has_audio=True, has_transcript=False, has_telemetry=False)

READABLE = PanelAim(in_frame="full", legible="clear", panel_type="analog")
UNREADABLE = PanelAim(in_frame="full", legible="illegible", glare=True)
MISAIMED = PanelAim(in_frame="none", legible="illegible")


def dual(panel: PanelAim, **kwargs) -> RigContext:
    """A two-sensor rig with a given aim-check result."""
    return RigContext(has_panel_stream=True, panel=panel, **kwargs)


def test_always_on_modules_survive_a_blind_camera():
    blind = viewpoint(mount="unknown")
    modules = decide(blind, NO_AUDIO)
    assert set(modules.enabled) == {"highlights", "story"}
    assert "panel" in modules.disabled
    assert "landing" in modules.disabled


def test_a_clear_panel_enables_the_panel_module_on_a_single_camera_rig():
    modules = decide(viewpoint(instrument_panel="clear"), NO_AUDIO)
    assert "panel" in modules.enabled
    # But with only one sensor there is nothing to cross-reference against.
    assert "crosscheck" in modules.disabled


@pytest.mark.parametrize("state", ["partial", "none"])
def test_a_partial_panel_does_not_enable_the_panel_module(state):
    modules = decide(viewpoint(instrument_panel=state), NO_AUDIO)
    assert "panel" in modules.disabled
    assert state in modules.decision("panel").reason


def test_landing_needs_both_a_forward_mount_and_a_visible_runway():
    forward_with_runway = viewpoint(mount="forward", runway_on_approach=True)
    assert "landing" in decide(forward_with_runway, NO_AUDIO).enabled

    forward_no_runway = viewpoint(mount="forward", runway_on_approach=False)
    assert "landing" in decide(forward_no_runway, NO_AUDIO).disabled

    tail_with_runway = viewpoint(mount="tail", runway_on_approach=True)
    assert "landing" in decide(tail_with_runway, NO_AUDIO).disabled


def test_audio_modules_follow_the_audio_track():
    silent = decide(viewpoint(), NO_AUDIO)
    assert {"radio", "callouts", "engine"} <= set(silent.disabled)

    heard = decide(viewpoint(), WITH_AUDIO)
    assert {"radio", "callouts", "engine"} <= set(heard.enabled)


def test_the_word_modules_need_a_transcript_but_engine_does_not():
    modules = decide(viewpoint(), UNTRANSCRIBED)
    assert "engine" in modules.enabled
    assert {"radio", "callouts"} <= set(modules.disabled)
    assert "not transcribed" in modules.decision("radio").reason


def test_pattern_accepts_either_terrain_or_telemetry():
    terrain = viewpoint(mount="wing", outside_terrain=True)
    assert "pattern" in decide(terrain, NO_AUDIO).enabled

    blind_but_tracked = viewpoint(mount="panel", outside_terrain=False)
    with_gps = RigContext(has_audio=False, has_telemetry=True)
    assert "pattern" in decide(blind_but_tracked, with_gps).enabled

    assert "pattern" in decide(blind_but_tracked, NO_AUDIO).disabled


def test_every_module_gets_a_decision_with_a_reason():
    modules = decide(viewpoint(), WITH_AUDIO)
    assert {d.module for d in modules.decisions} == set(MODULE_NAMES)
    assert all(d.reason for d in modules.decisions)


def test_disabled_modules_carry_a_camera_tip():
    modules = decide(viewpoint(mount="tail"), NO_AUDIO)
    tipped = [d for d in modules.decisions if not d.enabled and d.tip]
    assert tipped, "a blocked module should suggest how to unlock it"


def test_module_filter_narrows_but_never_widens():
    full = viewpoint(mount="panel", instrument_panel="clear", horizon=True)
    narrowed = decide(full, WITH_AUDIO, only=["panel", "landing"])
    assert narrowed.enabled == ["panel"]  # landing has no runway in view
    assert "radio" in narrowed.disabled
    assert "not selected" in narrowed.decision("attitude").reason


def test_unknown_module_in_the_filter_is_an_error():
    with pytest.raises(ValueError, match="unknown module"):
        decide(viewpoint(), NO_AUDIO, only=["nosuchmodule"])


def test_phases_for_intersects_with_the_phases_actually_flown():
    assert phases_for("landing", ["ground", "taxi", "approach", "landing"]) == [
        "approach",
        "landing",
    ]
    assert phases_for("landing", ["ground", "taxi"]) == []
    assert phases_for("nosuch", ["ground"]) == []


# --- the two-sensor rig ------------------------------------------------------


def test_a_legible_instrument_sensor_unlocks_panel_and_crosscheck():
    modules = decide(viewpoint(mount="panel", instrument_panel="none"), dual(READABLE))
    assert "panel" in modules.enabled
    assert "crosscheck" in modules.enabled


def test_an_illegible_instrument_sensor_blocks_both():
    modules = decide(viewpoint(instrument_panel="none"), dual(UNREADABLE))
    assert "panel" in modules.disabled
    assert "crosscheck" in modules.disabled
    assert "cannot be read" in modules.decision("panel").reason
    assert "glare" in modules.decision("panel").reason


def test_a_misaimed_sensor_says_so_plainly():
    modules = decide(viewpoint(), dual(MISAIMED))
    assert "not pointing at the panel" in modules.decision("panel").reason


def test_an_unchecked_sensor_fails_closed():
    """No aim check means no permission to read instruments."""
    modules = decide(viewpoint(), RigContext(has_panel_stream=True, panel=None))
    assert "panel" in modules.disabled
    assert "never checked" in modules.decision("panel").reason


def test_crosscheck_needs_a_second_sensor_not_just_a_clear_panel():
    single = decide(viewpoint(instrument_panel="clear"), NO_AUDIO)
    assert "crosscheck" in single.disabled
    assert "only one sensor" in single.decision("crosscheck").reason


def test_modules_declare_the_stream_they_read():
    assert streams_for("panel") == ("panel",)
    assert streams_for("crosscheck") == ("scene", "panel")
    assert streams_for("environment") == ("scene",)
    assert streams_for("nosuch") == ("scene",)

    modules = decide(viewpoint(), dual(READABLE))
    assert modules.decision("panel").streams == ["panel"]
    assert modules.decision("crosscheck").streams == ["scene", "panel"]


def test_the_aim_hint_survives_into_the_decision_tip():
    modules = decide(viewpoint(), dual(UNREADABLE))
    assert "narrow sensor" in (modules.decision("panel").tip or "")
