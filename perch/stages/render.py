"""Stage 9 — render.

Produce a single self-contained HTML file. CSS is inline, frames are embedded as
base64 data URLs, and nothing is loaded from the network — the debrief has to
open years from now on an aircraft with no signal.

An observation that states a number is shown with the instrument frame it was
read from. That is the whole point of the second sensor: the pilot can check the
number against the picture it came from without opening the video.
"""

from __future__ import annotations

import base64
import json
import time
from pathlib import Path
from typing import Any, Optional

from jinja2 import Environment, FileSystemLoader, select_autoescape

from ..config import TEMPLATES_DIR
from ..evaluate import clock
from ..models import (
    Debrief,
    Modules,
    Observations,
    PanelAim,
    Phases,
    Probe,
    Rejections,
    StageRecord,
    Transcript,
    Viewpoint,
)
from ..runs import RunContext
from ..validate import mentions_measured_quantity
from . import sample as sample_stage
from . import telemetry as telemetry_stage

NAME = "render"

MAX_EMBEDDED_BYTES = 700_000
"""A frame larger than this is dropped rather than bloating the file."""


def _environment() -> Environment:
    return Environment(
        loader=FileSystemLoader(str(TEMPLATES_DIR)),
        autoescape=select_autoescape(["html", "j2"]),
        trim_blocks=True,
        lstrip_blocks=True,
    )


def data_url(path: Path) -> Optional[str]:
    if not path.is_file():
        return None
    raw = path.read_bytes()
    if len(raw) > MAX_EMBEDDED_BYTES:
        return None
    return "data:image/jpeg;base64," + base64.standard_b64encode(raw).decode("ascii")


def build_context(run_dir: Path) -> dict[str, Any]:
    """Assemble the template context from the artifacts on disk."""
    run_dir = Path(run_dir)

    def load(name: str, schema):
        path = run_dir / name
        return schema.model_validate(json.loads(path.read_text())) if path.is_file() else None

    probe: Optional[Probe] = load("probe.json", Probe)
    if probe is None:
        raise FileNotFoundError(f"{run_dir} has no probe.json")
    debrief: Debrief = load("debrief.json", Debrief) or Debrief()
    viewpoint: Viewpoint = load("viewpoint.json", Viewpoint) or Viewpoint()
    modules: Modules = load("modules.json", Modules) or Modules()
    phases: Phases = load("phases.json", Phases) or Phases(source="fallback")
    observations: Observations = load("observations.json", Observations) or Observations()
    rejections: Rejections = load("rejected.json", Rejections) or Rejections()
    transcript: Transcript = load("transcript.json", Transcript) or Transcript()
    panel_aim: Optional[PanelAim] = load("panel_aim.json", PanelAim)

    frames = {
        role: sample_stage.load_frames(run_dir / "frames", role) for role in ("scene", "panel")
    }
    telemetry_rows = telemetry_stage.read_csv(run_dir / "telemetry.csv")

    def frame_url(role: str, t: float, within: Optional[float] = None) -> Optional[str]:
        ref = sample_stage.nearest_frame(frames[role], t, within=within)
        return data_url(run_dir / "frames" / role / ref.file) if ref else None

    highlights = [
        {
            "timestamp": h.timestamp,
            "clock": clock(h.timestamp),
            "title": h.title,
            "text": h.text,
            "image": frame_url("scene", h.timestamp),
        }
        for h in debrief.highlights
    ]

    rows = []
    for obs in sorted(
        observations.observations, key=lambda o: o.timestamps[0] if o.timestamps else 0.0
    ):
        t = obs.timestamps[0] if obs.timestamps else 0.0
        # Show the instrument frame beside any claim that states a reading, so
        # the number can be checked against the glass it came from.
        panel_image = (
            frame_url("panel", t, within=5.0)
            if mentions_measured_quantity(obs.claim)
            else None
        )
        rows.append(
            {
                "clock": clock(t),
                "module": obs.module,
                "phase": obs.phase,
                "claim": obs.claim,
                "provenance": obs.provenance,
                "confidence": obs.confidence,
                "interest": obs.interest,
                "stream": obs.stream or "scene",
                "panel_image": panel_image,
            }
        )

    return {
        "probe": probe,
        "scene": probe.scene,
        "debrief": {
            "flight_story": debrief.flight_story,
            "highlights": highlights,
            "takeaways": debrief.takeaways,
            "could_not_see": debrief.could_not_see,
            "next_time": debrief.next_time,
        },
        "viewpoint": viewpoint,
        "panel_aim": panel_aim,
        "observations": rows,
        "duration_clock": clock(probe.duration),
        "phase_list": ", ".join(phases.present()),
        "frame_count": sum(len(v) for v in frames.values()),
        "panel_frame_count": len(frames["panel"]),
        "has_panel": probe.has_panel,
        "sync": probe.sync,
        "has_telemetry": len(telemetry_rows) >= 10,
        "has_transcript": transcript.available and bool(transcript.segments),
        "enabled_modules": ", ".join(modules.enabled) or "none",
        "rejected_count": len(rejections.rejected),
    }


def render_html(run_dir: Path) -> str:
    template = _environment().get_template("debrief.html.j2")
    return template.render(**build_context(run_dir))


def run(ctx: RunContext) -> StageRecord:
    started = time.time()
    html = render_html(ctx.run_dir)
    out = ctx.path("debrief.html")
    out.write_text(html)
    ctx.say(f"  render: {out} ({len(html) / 1024:.0f} KB)")
    return StageRecord(
        name=NAME,
        status="ok",
        seconds=round(time.time() - started, 3),
        detail=f"{len(html) // 1024} KB",
    )
