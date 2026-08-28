"""Pydantic schemas for every artifact the pipeline writes.

Each stage writes exactly one of these to disk as JSON. A later stage reads the
file back through the same schema, so a hand-edited artifact still validates.

The rig captures two video streams. The **scene** stream is wide — the cockpit,
the pilots' hands, and the world through the windscreen. The **panel** stream is
narrow and framed on the instruments. The scene stream defines the run timeline;
every panel timestamp is carried into scene time by its offset.
"""

from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field

# --- controlled vocabularies -------------------------------------------------

StreamRole = Literal["scene", "panel"]
STREAM_ROLES: tuple[str, ...] = ("scene", "panel")

Mount = Literal["panel", "forward", "chest", "head", "wing", "tail", "unknown"]
Visibility = Literal["clear", "partial", "none"]
Lighting = Literal["good", "backlit", "night", "mixed"]
Vibration = Literal["low", "medium", "high"]
Obstruction = Literal["none", "partial", "severe"]

InFrame = Literal["full", "partial", "none"]
Legibility = Literal["clear", "marginal", "illegible"]
PanelType = Literal["analog", "glass", "mixed", "unknown"]

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
    """One video file."""

    role: StreamRole = "scene"
    offset: float = Field(
        default=0.0,
        description="Seconds to add to this file's timestamps to reach scene time.",
    )
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

    @property
    def aspect(self) -> float:
        h = self.display_height or 1
        return self.display_width / h


class TelemetryChannels(BaseModel):
    """What a telemetry file actually measured.

    "There are rows" is not a capability. A GPS gives position and groundspeed;
    an IMU gives attitude and g; a barometer gives altitude. Each authorises a
    different kind of claim, and conflating them is how an IMU ends up
    licensing an airspeed.
    """

    source: Literal["none", "gpmf", "perch"] = "none"
    position: bool = False       # latitude, longitude, ground speed — GPS only
    altitude: bool = False       # barometric or GPS altitude
    attitude: bool = False       # bank and pitch, from a fused IMU
    acceleration: bool = False   # g load and turn rate

    @property
    def any(self) -> bool:
        return self.position or self.altitude or self.attitude or self.acceleration

    @property
    def can_segment(self) -> bool:
        """Enough to derive phases locally, without the vision fallback."""
        return self.altitude

    @property
    def authorises_speed(self) -> bool:
        """Only GPS can supply a speed the panel did not show."""
        return self.position


class SyncResult(BaseModel):
    """How the panel stream was aligned to the scene stream."""

    method: Literal["manual", "audio", "assumed"] = "assumed"
    offset: float = 0.0
    confidence: Optional[float] = Field(
        default=None, description="Normalised cross-correlation peak, 0-1, for audio sync."
    )
    note: Optional[str] = None


class Probe(BaseModel):
    """Every stream in one run, plus the timeline they share."""

    streams: list[ProbeResult] = Field(default_factory=list)
    duration: float = 0.0
    sync: Optional[SyncResult] = None

    def by_role(self, role: str) -> Optional[ProbeResult]:
        for stream in self.streams:
            if stream.role == role:
                return stream
        return None

    @property
    def scene(self) -> ProbeResult:
        stream = self.by_role("scene")
        if stream is None:
            raise ValueError("the run has no scene stream")
        return stream

    @property
    def panel(self) -> Optional[ProbeResult]:
        return self.by_role("panel")

    @property
    def has_panel(self) -> bool:
        return self.panel is not None

    @property
    def has_audio(self) -> bool:
        return any(s.has_audio for s in self.streams)

    @property
    def has_telemetry(self) -> bool:
        return any(s.has_telemetry for s in self.streams)

    def audio_stream(self) -> Optional[ProbeResult]:
        """The stream to transcribe. The scene camera hears the cockpit best."""
        scene = self.by_role("scene")
        if scene is not None and scene.has_audio:
            return scene
        return next((s for s in self.streams if s.has_audio), None)


# --- stage 3: sample ---------------------------------------------------------


class FrameRef(BaseModel):
    t: float = Field(description="Seconds on the run timeline (scene time).")
    file: str = Field(description="Filename inside frames/<role>/.")


class StreamFrames(BaseModel):
    role: StreamRole
    interval: float
    long_edge: int
    jpeg_quality: int
    count: int
    offset: float = 0.0
    frames: list[FrameRef] = Field(default_factory=list)


class FrameIndex(BaseModel):
    streams: list[StreamFrames] = Field(default_factory=list)

    def by_role(self, role: str) -> Optional[StreamFrames]:
        for stream in self.streams:
            if stream.role == role:
                return stream
        return None

    @property
    def total(self) -> int:
        return sum(s.count for s in self.streams)


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
    source: Literal["probed", "profile"] = "probed"
    rig: Optional[str] = None


class PanelAim(BaseModel):
    """Install QA for the narrow sensor. Drives the aim feedback in the app."""

    in_frame: InFrame = "none"
    legible: Legibility = "illegible"
    panel_type: PanelType = "unknown"
    instruments: list[str] = Field(
        default_factory=list,
        description="Instruments the model could name and read, e.g. airspeed, altimeter, tachometer, PFD.",
    )
    aim_hint: str = Field(
        default="",
        description="One short instruction to the pilot, e.g. 'tilt down slightly'. Empty when the aim is good.",
    )
    glare: bool = False
    notes: str = ""

    @property
    def usable(self) -> bool:
        return self.in_frame != "none" and self.legible != "illegible"


class ModuleDecision(BaseModel):
    module: str
    enabled: bool
    reason: str
    tip: Optional[str] = Field(
        default=None,
        description="Camera or rig change that would unlock this module.",
    )
    streams: list[str] = Field(default_factory=list)


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
    stream: Optional[str] = Field(
        default=None,
        description="Which stream the frames came from. Set by the pipeline, not the model.",
    )


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
    panel_video: Optional[str] = None
    rig: Optional[str] = None
    created: str
    duration: float = 0.0
    dry_run: bool = False
    estimated_cost: float = 0.0
    actual_cost: float = 0.0
    max_cost: Optional[float] = None
    spend: list[ModelSpend] = Field(default_factory=list)
    stages: list[StageRecord] = Field(default_factory=list)
    mount: Optional[str] = None
    panel_legible: Optional[str] = None
    panel_offset: Optional[float] = None
    enabled_modules: list[str] = Field(default_factory=list)
    observation_count: int = 0
    rejection_rate: float = 0.0

    def record(self, rec: StageRecord) -> None:
        self.stages = [s for s in self.stages if s.name != rec.name] + [rec]
