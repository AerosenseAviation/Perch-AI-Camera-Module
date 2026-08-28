"""Pydantic schemas for every artifact the pipeline writes.

Each stage writes exactly one of these to disk as JSON. A later stage reads the
file back through the same schema, so a hand-edited artifact still validates.
"""

from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field

# --- controlled vocabularies -------------------------------------------------

Mount = Literal["panel", "forward", "chest", "head", "wing", "tail", "unknown"]
Visibility = Literal["clear", "partial", "none"]
Lighting = Literal["good", "backlit", "night", "mixed"]
Vibration = Literal["low", "medium", "high"]
Obstruction = Literal["none", "partial", "severe"]

Phase = Literal[
    "ground",
    "taxi",
    "takeoff",
    "climb",
    "cruise",
    "manoeuvre",
    "circuit",
    "approach",
    "landing",
    "shutdown",
]

PHASES: tuple[str, ...] = (
    "ground",
    "taxi",
    "takeoff",
    "climb",
    "cruise",
    "manoeuvre",
    "circuit",
    "approach",
    "landing",
    "shutdown",
)

Provenance = Literal["visual", "audio", "telemetry", "inferred"]
Confidence = Literal["high", "medium", "low"]
Interest = Literal["safety", "skill", "character", "trivia"]
PhaseSource = Literal["telemetry", "vision", "fallback"]


# --- stage 1: probe ----------------------------------------------------------


class AudioStreamInfo(BaseModel):
    index: int
    codec: Optional[str] = None
    channels: Optional[int] = None
    sample_rate: Optional[int] = None
    duration: Optional[float] = None


class ProbeResult(BaseModel):
    path: str
    filename: str
    size_bytes: int
    container: Optional[str] = None
    duration: float
    width: int
    height: int
    fps: float
    rotation: int = 0
    has_audio: bool = False
    audio: Optional[AudioStreamInfo] = None
    has_telemetry: bool = False
    telemetry_stream_index: Optional[int] = None
    telemetry_handler: Optional[str] = None

    @property
    def display_width(self) -> int:
        return self.height if self.rotation in (90, 270) else self.width

    @property
    def display_height(self) -> int:
        return self.width if self.rotation in (90, 270) else self.height


# --- stage 3: sample ---------------------------------------------------------


class FrameRef(BaseModel):
    t: float = Field(description="Seconds from the start of the video.")
    file: str = Field(description="Filename inside frames/.")


class FrameIndex(BaseModel):
    interval: float
    long_edge: int
    jpeg_quality: int
    count: int
    frames: list[FrameRef] = Field(default_factory=list)


# --- stage 4: audio ----------------------------------------------------------


class TranscriptSegment(BaseModel):
    start: float
    end: float
    text: str


class Transcript(BaseModel):
    available: bool = False
    language: Optional[str] = None
    model: Optional[str] = None
    note: Optional[str] = None
    segments: list[TranscriptSegment] = Field(default_factory=list)


# --- stage 5: segment --------------------------------------------------------


class PhaseSpan(BaseModel):
    start: float
    end: float
    phase: Phase

    @property
    def duration(self) -> float:
        return max(0.0, self.end - self.start)


class Phases(BaseModel):
    source: PhaseSource
    spans: list[PhaseSpan] = Field(default_factory=list)
    note: Optional[str] = None

    def phase_at(self, t: float) -> Optional[str]:
        for span in self.spans:
            if span.start <= t < span.end:
                return span.phase
        return self.spans[-1].phase if self.spans else None

    def spans_for(self, phase: str) -> list[PhaseSpan]:
        return [s for s in self.spans if s.phase == phase]

    def present(self) -> list[str]:
        seen: list[str] = []
        for span in self.spans:
            if span.phase not in seen:
                seen.append(span.phase)
        return seen


class FrameLabel(BaseModel):
    """One vision-model phase vote for a single frame."""

    t: float
    phase: Phase


class FrameLabels(BaseModel):
    labels: list[FrameLabel] = Field(default_factory=list)


# --- stage 6: capability -----------------------------------------------------


class ViewpointVisible(BaseModel):
    instrument_panel: Visibility = "none"
    horizon: bool = False
    runway_on_approach: bool = False
    pilot_hands: bool = False
    pilot_face: bool = False
    wing_or_airframe: bool = False
    outside_terrain: bool = False
    other_occupants: bool = False


class ViewpointQuality(BaseModel):
    lighting: Lighting = "good"
    glare: bool = False
    vibration: Vibration = "low"
    obstruction: Obstruction = "none"


class Viewpoint(BaseModel):
    mount: Mount = "unknown"
    visible: ViewpointVisible = Field(default_factory=ViewpointVisible)
    quality: ViewpointQuality = Field(default_factory=ViewpointQuality)
    notes: str = ""


class ModuleDecision(BaseModel):
    module: str
    enabled: bool
    reason: str
    tip: Optional[str] = Field(
        default=None,
        description="Camera-position change that would unlock this module.",
    )


class Modules(BaseModel):
    enabled: list[str] = Field(default_factory=list)
    disabled: list[str] = Field(default_factory=list)
    decisions: list[ModuleDecision] = Field(default_factory=list)

    def decision(self, module: str) -> Optional[ModuleDecision]:
        for d in self.decisions:
            if d.module == module:
                return d
        return None


# --- stage 7: analyse --------------------------------------------------------


class Observation(BaseModel):
    module: str
    phase: Phase
    timestamps: list[float] = Field(default_factory=list)
    claim: str
    provenance: Provenance
    confidence: Confidence
    interest: Interest


class ObservationList(BaseModel):
    """Wrapper so the model returns a single JSON object, not a bare array."""

    observations: list[Observation] = Field(default_factory=list)


class RejectedObservation(BaseModel):
    observation: Observation
    rules: list[str]
    detail: str


class Observations(BaseModel):
    observations: list[Observation] = Field(default_factory=list)
    accepted: int = 0
    rejected: int = 0


class Rejections(BaseModel):
    rejected: list[RejectedObservation] = Field(default_factory=list)
    total_proposed: int = 0
    rejection_rate: float = 0.0


# --- stage 8: compose --------------------------------------------------------


class Highlight(BaseModel):
    timestamp: float
    title: str
    text: str


class Debrief(BaseModel):
    flight_story: str = ""
    highlights: list[Highlight] = Field(default_factory=list)
    takeaways: list[str] = Field(default_factory=list)
    could_not_see: list[str] = Field(default_factory=list)
    next_time: str = ""


# --- run manifest ------------------------------------------------------------


class StageRecord(BaseModel):
    name: str
    status: Literal["ok", "skipped", "failed"]
    seconds: float = 0.0
    detail: Optional[str] = None


class ModelSpend(BaseModel):
    model: str
    calls: int = 0
    cached_calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cost: float = 0.0


class RunManifest(BaseModel):
    flight_id: str
    video: str
    created: str
    duration: float = 0.0
    dry_run: bool = False
    estimated_cost: float = 0.0
    actual_cost: float = 0.0
    max_cost: Optional[float] = None
    spend: list[ModelSpend] = Field(default_factory=list)
    stages: list[StageRecord] = Field(default_factory=list)
    mount: Optional[str] = None
    enabled_modules: list[str] = Field(default_factory=list)
    observation_count: int = 0
    rejection_rate: float = 0.0

    def record(self, rec: StageRecord) -> None:
        self.stages = [s for s in self.stages if s.name != rec.name] + [rec]
