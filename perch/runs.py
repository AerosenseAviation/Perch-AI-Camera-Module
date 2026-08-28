"""Run directories and the context object every stage receives.

A run directory is the entire state of a flight analysis. Every stage reads its
inputs from the directory and writes its output back, so ``debrief stage
analyse runs/foo-20260828-101500`` re-runs one step against the artifacts
already on disk.
"""

from __future__ import annotations

import json
import re
import shutil
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Type, TypeVar

from pydantic import BaseModel

from .cache import ResponseCache
from .config import Config
from .cost import CostTracker
from .llm import LLMClient
from .models import RunManifest

T = TypeVar("T", bound=BaseModel)

VIDEO_SUFFIXES = frozenset({".mp4", ".mov", ".m4v"})
"""Compared case-insensitively — GoPro writes .MP4, phones write .mov."""


def slugify(text: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9._-]+", "-", text).strip("-")
    return slug or "flight"


def new_run_dir(video: Path, runs_root: Path, *, now: Optional[datetime] = None) -> Path:
    now = now or datetime.now()
    stamp = now.strftime("%Y%m%d-%H%M%S")
    base = runs_root / f"{slugify(video.stem)}-{stamp}"
    run_dir = base
    # Two short clips in a batch can start inside the same second.
    suffix = 2
    while run_dir.exists():
        run_dir = base.with_name(f"{base.name}-{suffix}")
        suffix += 1
    run_dir.mkdir(parents=True, exist_ok=False)
    return run_dir


class MissingArtifact(FileNotFoundError):
    """A stage needs an artifact an earlier stage has not written."""


@dataclass
class RunContext:
    run_dir: Path
    video: Path
    config: Config
    llm: LLMClient
    tracker: CostTracker
    cache: ResponseCache
    panel_video: Optional[Path] = None
    panel_offset: Optional[float] = None
    auto_sync: bool = True
    rig: Optional[str] = None
    dry_run: bool = False
    no_audio: bool = False
    module_filter: Optional[list[str]] = None
    manifest: RunManifest = field(default=None)  # type: ignore[assignment]
    verbose: bool = True

    # -- paths ---------------------------------------------------------------

    def path(self, name: str) -> Path:
        return self.run_dir / name

    @property
    def frames_dir(self) -> Path:
        return self.run_dir / "frames"

    def stream_dir(self, role: str) -> Path:
        return self.frames_dir / role

    def source_for(self, role: str) -> Path:
        """The video file backing a stream role."""
        if role == "panel":
            if self.panel_video is None:
                raise MissingArtifact("this run has no panel stream")
            return self.panel_video
        return self.video

    # -- artifact IO ----------------------------------------------------------

    def write_json(self, name: str, obj: BaseModel | dict | list) -> Path:
        path = self.path(name)
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = obj.model_dump() if isinstance(obj, BaseModel) else obj
        path.write_text(json.dumps(payload, indent=2, default=str) + "\n")
        return path

    def read_json(self, name: str, schema: Type[T]) -> T:
        path = self.path(name)
        if not path.is_file():
            raise MissingArtifact(
                f"{name} is missing from {self.run_dir}. Run the earlier stage first."
            )
        return schema.model_validate(json.loads(path.read_text()))

    def try_read_json(self, name: str, schema: Type[T]) -> Optional[T]:
        try:
            return self.read_json(name, schema)
        except (MissingArtifact, json.JSONDecodeError):
            return None

    def has(self, name: str) -> bool:
        return self.path(name).exists()

    # -- reporting ------------------------------------------------------------

    def say(self, message: str) -> None:
        if self.verbose:
            print(message)

    def save_manifest(self) -> None:
        if self.manifest is None:
            return
        self.manifest.actual_cost = round(self.tracker.total, 6)
        self.manifest.spend = self.tracker.spend()
        self.write_json("run.json", self.manifest)


def load_manifest(run_dir: Path) -> Optional[RunManifest]:
    path = run_dir / "run.json"
    if not path.is_file():
        return None
    return RunManifest.model_validate(json.loads(path.read_text()))


def resolve_sources(run_dir: Path) -> tuple[Path, Optional[Path]]:
    """Recover (scene, panel) source videos for an existing run.

    The manifest is authoritative; probe.json is the fallback so a run whose
    manifest was lost can still have a single stage re-run against it.
    """
    manifest = load_manifest(run_dir)
    if manifest and manifest.video:
        panel = Path(manifest.panel_video) if manifest.panel_video else None
        return Path(manifest.video), panel

    probe = run_dir / "probe.json"
    if probe.is_file():
        data = json.loads(probe.read_text())
        streams = data.get("streams") or []
        scene = next((s for s in streams if s.get("role") == "scene"), None)
        panel = next((s for s in streams if s.get("role") == "panel"), None)
        if scene:
            return Path(scene["path"]), (Path(panel["path"]) if panel else None)
    raise MissingArtifact(f"cannot tell which video {run_dir} came from")


def find_videos(folder: Path) -> list[Path]:
    return sorted(
        p for p in folder.rglob("*") if p.is_file() and p.suffix.lower() in VIDEO_SUFFIXES
    )


def find_panel_for(scene: Path, suffix: str = "-panel") -> Optional[Path]:
    """Pair a scene file with its instrument file by filename.

    ``flight12.mp4`` picks up ``flight12-panel.mp4`` beside it, in any of the
    known video extensions — the two cameras rarely write the same container.
    """
    if not suffix or scene.stem.endswith(suffix):
        return None
    for extension in (scene.suffix, *VIDEO_SUFFIXES):
        for candidate in {
            scene.with_name(f"{scene.stem}{suffix}{extension}"),
            scene.with_name(f"{scene.stem}{suffix}{extension.upper()}"),
        }:
            if candidate.is_file():
                return candidate
    return None


def require_binary(name: str) -> str:
    path = shutil.which(name)
    if not path:
        raise RuntimeError(
            f"{name} is not on PATH. Install it and try again "
            f"(ffmpeg and ffprobe are required; exiftool is optional)."
        )
    return path


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")
