"""Content-addressed cache for model responses.

Prompt iteration is the main work of this project, so the same frames must never
be paid for twice. The key is a SHA-256 over everything that could change the
answer: model, system prompt, max_tokens, the schema, and the bytes of every
image. Entries are plain JSON files, so a bad entry can be deleted by hand.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path
from typing import Any, Optional


def hash_bytes(*chunks: bytes) -> str:
    h = hashlib.sha256()
    for chunk in chunks:
        h.update(len(chunk).to_bytes(8, "big"))
        h.update(chunk)
    return h.hexdigest()


def hash_files(paths: list[Path]) -> str:
    """Stable hash of a set of files, order-independent."""
    digests = sorted(
        hashlib.sha256(p.read_bytes()).hexdigest() for p in paths
    )
    return hash_bytes(*(d.encode() for d in digests))


def canonical(payload: Any) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode()


class ResponseCache:
    """A two-level directory of JSON entries under ``root/<namespace>/<key>.json``."""

    def __init__(self, root: Path, enabled: bool = True) -> None:
        self.root = Path(root)
        self.enabled = enabled
        self.hits = 0
        self.misses = 0

    def key(self, payload: Any) -> str:
        return hashlib.sha256(canonical(payload)).hexdigest()

    def _path(self, namespace: str, key: str) -> Path:
        return self.root / namespace / key[:2] / f"{key}.json"

    def get(self, namespace: str, key: str) -> Optional[dict[str, Any]]:
        if not self.enabled:
            return None
        path = self._path(namespace, key)
        if not path.is_file():
            self.misses += 1
            return None
        try:
            value = json.loads(path.read_text())
        except (json.JSONDecodeError, OSError):
            self.misses += 1
            return None
        self.hits += 1
        return value

    def put(self, namespace: str, key: str, value: dict[str, Any]) -> None:
        if not self.enabled:
            return
        path = self._path(namespace, key)
        path.parent.mkdir(parents=True, exist_ok=True)
        # Write through a temp file so a crash never leaves a half-written entry
        # that would later parse as a cache hit.
        fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
        try:
            with os.fdopen(fd, "w") as fh:
                json.dump(value, fh)
            os.replace(tmp, path)
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise

    def stats(self) -> dict[str, int]:
        return {"hits": self.hits, "misses": self.misses}
