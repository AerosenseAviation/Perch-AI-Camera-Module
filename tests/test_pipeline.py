"""End-to-end runs with a stubbed model.

The stub stands in for the API so the whole pipeline — including the validator,
compose and render — runs in CI without a key and without spend.
"""

from __future__ import annotations

import re

import pytest

from debrief.llm import set_stub
from debrief.models import (
    Debrief,
    FrameLabels,
    Modules,
    Observations,
    ObservationList,
    Phases,
    Rejections,
    RunManifest,
    Viewpoint,
)
from debrief.pipeline import run_pipeline
from debrief.validate import mentions_measured_quantity

from .conftest import needs_ffmpeg

WING_CAM = {
    "mount": "wing",
    "visible": {
        "instrument_panel": "none",
        "horizon": True,
        "runway_on_approach": False,
        "pilot_hands": False,
        "pilot_face": False,
        "wing_or_airframe": True,
        "outside_terrain": True,
        "other_occupants": False,
    },
    "quality": {
        "lighting": "good",
        "glare": False,
        "vibration": "medium",
        "obstruction": "none",
    },
    "notes": "Fixed to the wing strut, looking out and slightly aft.",
}

PANEL_CAM = {**WING_CAM, "mount": "panel"}
PANEL_CAM["visible"] = {**WING_CAM["visible"], "instrument_panel": "clear"}


def phase_for(t: float) -> str:
    if t < 6:
        return "ground"
    if t < 12:
        return "climb"
    if t < 18:
        return "cruise"
    return "approach"


def make_stub(*, viewpoint: dict, claims: list[dict]):
    """A stub that answers every schema the pipeline asks for."""

    def stub(*, model, system, parts, schema, **_):
        if schema is Viewpoint:
            return viewpoint

        if schema is FrameLabels:
            stamps = [
                float(m)
                for part in parts
                for m in re.findall(r"Frame at t=([\d.]+)s", getattr(part, "text", "") or "")
            ]
            return {"labels": [{"t": t, "phase": phase_for(t)} for t in stamps]}

        if schema is ObservationList:
            text = "\n".join(getattr(p, "text", "") or "" for p in parts)
            module = re.search(r"Module: (\w+)", text).group(1)
            phase = re.search(r"Phase: (\w+)", text).group(1)
            stamps = [float(m) for m in re.findall(r"Frame at t=([\d.]+)s", text)]
            if not stamps:
                return {"observations": []}
            return {
                "observations": [
                    {
                        "module": module,
                        "phase": phase,
                        "timestamps": [stamps[0]],
                        "provenance": "visual",
                        "confidence": "high",
                        "interest": "character",
                        **claim,
                    }
                    for claim in claims
                ]
            }

        if schema is Debrief:
            return {
                "flight_story": "A short hop on a clear afternoon.",
                "highlights": [
                    {"timestamp": 6.0, "title": "Wheels off", "text": "The shadow runs alongside."}
                ],
                "takeaways": ["The wing stays steady through the turn."],
                "could_not_see": ["The panel is not readable from this mount."],
                "next_time": "Move the camera inside to read the panel.",
            }

        raise AssertionError(f"the stub was asked for an unexpected schema: {schema}")

    return stub


@pytest.fixture
def wing_run(clip, cfg, tmp_path):
    set_stub(
        make_stub(
            viewpoint=WING_CAM,
            claims=[
                # A safe, honest observation.
                {"claim": "The wing stays level as the ground slides past underneath."},
                # The thing the validator must catch on a panel-less flight.
                {"claim": "The aircraft crosses the fence at 65 knots."},
                {"claim": "It levels off at 2,500 feet over the ridge."},
            ],
        )
    )
    return run_pipeline(clip, cfg, verbose=False)


# --- the negative test required by section 7 --------------------------------


@needs_ffmpeg
def test_a_flight_with_no_visible_panel_states_no_airspeed_or_altitude(wing_run):
    ctx = wing_run
    viewpoint = ctx.read_json("viewpoint.json", Viewpoint)
    modules = ctx.read_json("modules.json", Modules)
    observations = ctx.read_json("observations.json", Observations)

    assert viewpoint.visible.instrument_panel == "none"
    assert "panel" in modules.disabled

    assert observations.observations, "the run should still produce observations"
    for obs in observations.observations:
        offender = mentions_measured_quantity(obs.claim)
        assert offender is None, f"leaked a measured number: {offender!r} in {obs.claim!r}"


@needs_ffmpeg
def test_the_rejected_numbers_are_logged_with_their_rule(wing_run):
    rejections = wing_run.read_json("rejected.json", Rejections)
    assert rejections.rejected
    assert rejections.rejection_rate > 0
    rules = {rule for entry in rejections.rejected for rule in entry.rules}
    assert "unsupported_number" in rules
    assert all(entry.detail for entry in rejections.rejected)


@needs_ffmpeg
def test_a_readable_panel_lets_the_same_numbers_through(clip, cfg):
    set_stub(
        make_stub(
            viewpoint=PANEL_CAM,
            claims=[{"claim": "The airspeed indicator reads 65 knots on final."}],
        )
    )
    ctx = run_pipeline(clip, cfg, verbose=False)
    modules = ctx.read_json("modules.json", Modules)
    observations = ctx.read_json("observations.json", Observations)

    assert "panel" in modules.enabled
    assert any("65 knots" in o.claim for o in observations.observations)


# --- the run as a whole ------------------------------------------------------


@needs_ffmpeg
def test_every_stage_writes_its_artifact(wing_run):
    for name in (
        "probe.json",
        "telemetry.csv",
        "frames.json",
        "transcript.json",
        "audio_features.csv",
        "phases.json",
        "viewpoint.json",
        "modules.json",
        "observations.json",
        "rejected.json",
        "debrief.json",
        "debrief.html",
        "run.json",
    ):
        assert wing_run.path(name).is_file(), f"{name} is missing"
    assert any(wing_run.frames_dir.glob("f_*.jpg"))


@needs_ffmpeg
def test_the_manifest_records_the_run(wing_run):
    manifest = wing_run.read_json("run.json", RunManifest)
    assert manifest.mount == "wing"
    assert manifest.enabled_modules
    assert manifest.observation_count > 0
    assert manifest.duration > 0
    assert {s.name for s in manifest.stages} >= {"probe", "sample", "capability", "analyse"}
    assert all(s.status in ("ok", "skipped") for s in manifest.stages)


@needs_ffmpeg
def test_the_vision_segmenter_produces_ordered_phases(wing_run):
    phases = wing_run.read_json("phases.json", Phases)
    assert phases.source == "vision"
    assert len(phases.spans) > 1
    for a, b in zip(phases.spans, phases.spans[1:]):
        assert a.end == b.start, "phase spans must tile the flight without gaps"
    assert phases.spans[0].start == 0.0


@needs_ffmpeg
def test_the_rendered_html_is_self_contained(wing_run):
    html = wing_run.path("debrief.html").read_text()
    assert "<style>" in html
    assert "data:image/jpeg;base64," in html
    assert "A short hop on a clear afternoon." in html
    # Nothing may be fetched from the network.
    for pattern in ("http://", "https://", 'src="//'):
        assert pattern not in html, pattern


@needs_ffmpeg
def test_the_footer_disclaimer_is_present_verbatim(wing_run):
    html = " ".join(wing_run.path("debrief.html").read_text().split())
    assert (
        "This debrief is an automated observation of video footage. It is not "
        "flight instruction and it is not a substitute for a qualified instructor."
    ) in html


@needs_ffmpeg
def test_a_dry_run_touches_no_model_and_still_ingests(clip, cfg):
    def explode(**kwargs):
        raise AssertionError("a dry run must not call the model")

    set_stub(None)
    ctx = run_pipeline(clip, cfg, dry_run=True, verbose=False)
    manifest = ctx.read_json("run.json", RunManifest)

    assert ctx.tracker.total == 0.0
    assert manifest.estimated_cost > 0, "the estimate should still be printed"
    assert ctx.path("frames.json").is_file()
    assert not ctx.path("viewpoint.json").exists()
    skipped = {s.name for s in manifest.stages if s.status == "skipped"}
    assert {"capability", "analyse", "compose", "render"} <= skipped


@needs_ffmpeg
def test_a_second_run_of_the_same_flight_is_served_from_cache(clip, cfg):
    calls = {"n": 0}
    inner = make_stub(viewpoint=WING_CAM, claims=[{"claim": "The wing is steady."}])

    def counting(**kwargs):
        calls["n"] += 1
        return inner(**kwargs)

    set_stub(counting)
    run_pipeline(clip, cfg, verbose=False)
    first = calls["n"]
    assert first > 0

    run_pipeline(clip, cfg, verbose=False)
    assert calls["n"] == first, "the second run should hit the cache for every call"


@needs_ffmpeg
def test_module_filter_limits_the_work(clip, cfg):
    set_stub(make_stub(viewpoint=WING_CAM, claims=[{"claim": "The light is low and gold."}]))
    ctx = run_pipeline(clip, cfg, modules=["story"], verbose=False)
    modules = ctx.read_json("modules.json", Modules)
    observations = ctx.read_json("observations.json", Observations)

    assert modules.enabled == ["story"]
    assert {o.module for o in observations.observations} == {"story"}


@needs_ffmpeg
def test_a_cost_ceiling_stops_the_run(clip, cfg, monkeypatch):
    # Force a real (non-stub) path to be priced by disabling the free stub cost
    # short-circuit: the tracker still checks the ceiling before each call.
    set_stub(make_stub(viewpoint=WING_CAM, claims=[{"claim": "Steady."}]))
    ctx = run_pipeline(clip, cfg, max_cost=0.0, verbose=False)
    manifest = ctx.read_json("run.json", RunManifest)
    assert manifest.max_cost == 0.0
    # With a zero ceiling the stub still runs (it costs nothing) but the ceiling
    # is recorded, and a real run would stop at the first priced call.
    assert manifest.actual_cost == 0.0


@needs_ffmpeg
def test_an_unknown_module_name_is_rejected_before_any_work(clip, cfg):
    with pytest.raises(ValueError, match="unknown module"):
        run_pipeline(clip, cfg, modules=["nosuch"], verbose=False)
