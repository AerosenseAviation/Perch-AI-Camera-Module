"""Configuration loading.

Model names, sampling rates and prices live in TOML, never in code, so a run can
be re-tuned without a redeploy. Lookup order, first hit wins:

    $DEBRIEF_CONFIG
    ./debrief.toml
    ~/.config/debrief/debrief.toml
    the packaged debrief.default.toml

A user file is merged over the packaged defaults, so it only needs the keys it
changes.
"""

from __future__ import annotations

import os
import tomllib
from pathlib import Path
from typing import Any, Optional

from pydantic import BaseModel, Field

PACKAGE_DIR = Path(__file__).resolve().parent
DEFAULT_CONFIG_PATH = PACKAGE_DIR / "debrief.default.toml"
PROMPTS_DIR = PACKAGE_DIR / "prompts"
TEMPLATES_DIR = PACKAGE_DIR / "templates"


class ModelsConfig(BaseModel):
    fast: str = "claude-haiku-4-5"
    strong: str = "claude-opus-5"


class PathsConfig(BaseModel):
    runs_root: str = "runs"
    cache_dir: str = "runs/.cache"


class SampleConfig(BaseModel):
    interval_seconds: float = 3.0
    max_frames: int = 400
    long_edge: int = 768
    jpeg_quality: int = 80


class AudioConfig(BaseModel):
    enabled: bool = True
    transcribe: bool = True
    sample_rate: int = 16000
    whisper_model: str = "base"
    whisper_compute_type: str = "int8"
    feature_hz: float = 1.0


class SegmentConfig(BaseModel):
    vision_interval_seconds: float = 30.0
    median_filter_window: int = 5
    max_frames_per_call: int = 20


class CapabilityConfig(BaseModel):
    frame_count: int = 8
    max_tokens: int = 2000


class AnalyseConfig(BaseModel):
    frames_per_batch: int = 20
    max_tokens: int = 8000
    max_transcript_chars: int = 6000
    telemetry_summary_rows: int = 24


class ComposeConfig(BaseModel):
    max_tokens: int = 8000
    max_observations: int = 400


class Price(BaseModel):
    input: float
    output: float


class Config(BaseModel):
    models: ModelsConfig = Field(default_factory=ModelsConfig)
    paths: PathsConfig = Field(default_factory=PathsConfig)
    sample: SampleConfig = Field(default_factory=SampleConfig)
    audio: AudioConfig = Field(default_factory=AudioConfig)
    segment: SegmentConfig = Field(default_factory=SegmentConfig)
    capability: CapabilityConfig = Field(default_factory=CapabilityConfig)
    analyse: AnalyseConfig = Field(default_factory=AnalyseConfig)
    compose: ComposeConfig = Field(default_factory=ComposeConfig)
    pricing: dict[str, Price] = Field(default_factory=dict)
    source: Optional[str] = None

    def price(self, model: str) -> Price:
        if model in self.pricing:
            return self.pricing[model]
        # An unknown model must not silently cost zero — assume the top tier so
        # a --max-cost guard errs toward stopping rather than overspending.
        return Price(input=10.0, output=50.0)

    @property
    def runs_root(self) -> Path:
        return Path(self.paths.runs_root)

    @property
    def cache_dir(self) -> Path:
        return Path(self.paths.cache_dir)


def _deep_merge(base: dict[str, Any], overlay: dict[str, Any]) -> dict[str, Any]:
    out = dict(base)
    for key, value in overlay.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge(out[key], value)
        else:
            out[key] = value
    return out


def candidate_paths(explicit: Optional[Path] = None) -> list[Path]:
    paths: list[Path] = []
    if explicit:
        paths.append(Path(explicit))
    env = os.environ.get("DEBRIEF_CONFIG")
    if env:
        paths.append(Path(env))
    paths.append(Path.cwd() / "debrief.toml")
    paths.append(Path.home() / ".config" / "debrief" / "debrief.toml")
    return paths


def load_config(explicit: Optional[Path] = None) -> Config:
    with DEFAULT_CONFIG_PATH.open("rb") as fh:
        data = tomllib.load(fh)
    source: Optional[str] = None
    for path in candidate_paths(explicit):
        if path.is_file():
            with path.open("rb") as fh:
                data = _deep_merge(data, tomllib.load(fh))
            source = str(path)
            break
    cfg = Config.model_validate(data)
    cfg.source = source
    return cfg
