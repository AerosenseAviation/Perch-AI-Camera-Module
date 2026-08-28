"""Anthropic client wrapper: caching, budget enforcement, JSON parsing.

Every model call in the pipeline goes through :meth:`LLMClient.complete_json`,
which returns a validated pydantic object. That gives one place to cache, one
place to count tokens against ``--max-cost``, and one place to enforce the
"never trust a raw string" rule.

Tests inject deterministic answers with :func:`set_stub` instead of hitting the
API.
"""

from __future__ import annotations

import base64
import json
import logging
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Optional, Sequence, Type, TypeVar

from pydantic import BaseModel, ValidationError

from .cache import ResponseCache
from .config import Config
from .cost import CostTracker, image_tokens

log = logging.getLogger("debrief.llm")

T = TypeVar("T", bound=BaseModel)


class LLMError(RuntimeError):
    """The model call failed or returned something unusable."""


class LLMDisabled(RuntimeError):
    """A model call was attempted while the client is disabled (--dry-run)."""


# --- content blocks ----------------------------------------------------------


@dataclass
class TextPart:
    text: str

    def api_block(self) -> dict[str, Any]:
        return {"type": "text", "text": self.text}

    def digest(self) -> Any:
        return ["text", self.text]

    def input_tokens(self) -> int:
        return max(1, len(self.text) // 4)


@dataclass
class ImagePart:
    path: Path
    width: int = 768
    height: int = 432

    def api_block(self) -> dict[str, Any]:
        data = base64.standard_b64encode(self.path.read_bytes()).decode("ascii")
        return {
            "type": "image",
            "source": {"type": "base64", "media_type": "image/jpeg", "data": data},
        }

    def digest(self) -> Any:
        import hashlib

        return ["image", hashlib.sha256(self.path.read_bytes()).hexdigest()]

    def input_tokens(self) -> int:
        return image_tokens(self.width, self.height)


Part = TextPart | ImagePart


# --- test stub ---------------------------------------------------------------

_STUB: Optional[Callable[..., Any]] = None


def set_stub(fn: Optional[Callable[..., Any]]) -> None:
    """Install a callable used in place of the API.

    The callable receives ``(model, system, parts, schema)`` and must return a
    dict, a JSON string, or an instance of ``schema``.
    """
    global _STUB
    _STUB = fn


# --- JSON recovery -----------------------------------------------------------

_FENCE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL)


def extract_json(text: str) -> dict[str, Any]:
    """Pull the first JSON object out of a free-text response."""
    candidates: list[str] = []
    fenced = _FENCE.search(text)
    if fenced:
        candidates.append(fenced.group(1))
    candidates.append(text)

    for candidate in candidates:
        candidate = candidate.strip()
        start = candidate.find("{")
        if start == -1:
            continue
        depth = 0
        in_string = False
        escape = False
        for i in range(start, len(candidate)):
            ch = candidate[i]
            if in_string:
                if escape:
                    escape = False
                elif ch == "\\":
                    escape = True
                elif ch == '"':
                    in_string = False
                continue
            if ch == '"':
                in_string = True
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(candidate[start : i + 1])
                    except json.JSONDecodeError:
                        break
    raise LLMError("no JSON object found in the model response")


# --- client ------------------------------------------------------------------


class LLMClient:
    def __init__(
        self,
        cfg: Config,
        cache: ResponseCache,
        tracker: CostTracker,
        *,
        enabled: bool = True,
    ) -> None:
        self.cfg = cfg
        self.cache = cache
        self.tracker = tracker
        self.enabled = enabled
        self._client: Any = None
        self._structured_output_ok = True

    # -- plumbing ------------------------------------------------------------

    @property
    def client(self) -> Any:
        if self._client is None:
            import anthropic

            if not os.environ.get("ANTHROPIC_API_KEY"):
                # The SDK also accepts an `ant auth login` profile, so an unset
                # key is not by itself a failure.
                log.info("ANTHROPIC_API_KEY is not set; relying on an SDK-resolved credential")
            try:
                self._client = anthropic.Anthropic()
            except Exception as exc:
                # A missing credential must reach the stage as an LLMError so the
                # run degrades instead of dumping an SDK traceback.
                raise LLMError(
                    "no Anthropic credential is available. Set ANTHROPIC_API_KEY, "
                    f"or run with --dry-run to skip every model call. ({exc})"
                ) from exc
        return self._client

    def _cache_key(
        self, model: str, system: str, parts: Sequence[Part], schema: Type[T], max_tokens: int
    ) -> str:
        return self.cache.key(
            {
                "model": model,
                "system": system,
                "max_tokens": max_tokens,
                "schema": schema.model_json_schema(),
                "parts": [p.digest() for p in parts],
            }
        )

    @staticmethod
    def _estimate_input_tokens(system: str, parts: Sequence[Part]) -> int:
        return max(1, len(system) // 4) + sum(p.input_tokens() for p in parts)

    # -- the one call ---------------------------------------------------------

    def complete_json(
        self,
        *,
        model: str,
        system: str,
        parts: Sequence[Part],
        schema: Type[T],
        max_tokens: int = 4000,
        namespace: str = "messages",
        cache_key: Optional[str] = None,
    ) -> T:
        """Call the model and return a validated ``schema`` instance."""
        if not self.enabled and _STUB is None:
            raise LLMDisabled("model calls are disabled for this run")

        key = cache_key or self._cache_key(model, system, parts, schema, max_tokens)
        cached = self.cache.get(namespace, key)
        if cached is not None:
            try:
                value = schema.model_validate(cached["data"])
            except ValidationError:
                log.warning("stale cache entry for %s/%s — refetching", namespace, key[:8])
            else:
                self.tracker.record(
                    model,
                    cached.get("input_tokens", 0),
                    cached.get("output_tokens", 0),
                    cached=True,
                )
                return value

        if _STUB is not None:
            result = _STUB(model=model, system=system, parts=list(parts), schema=schema)
            value = self._coerce(result, schema)
            self.cache.put(
                namespace,
                key,
                {"data": value.model_dump(), "input_tokens": 0, "output_tokens": 0},
            )
            self.tracker.record(model, 0, 0, cached=False)
            return value

        projected_in = self._estimate_input_tokens(system, parts)
        price = self.cfg.price(model)
        projected = (projected_in / 1e6) * price.input + (max_tokens / 1e6) * price.output
        self.tracker.check(projected, where=namespace)

        text, usage = self._request(model, system, parts, schema, max_tokens)
        self.tracker.record(model, usage[0], usage[1])
        value = self._coerce(text, schema)
        self.cache.put(
            namespace,
            key,
            {"data": value.model_dump(), "input_tokens": usage[0], "output_tokens": usage[1]},
        )
        return value

    def _coerce(self, result: Any, schema: Type[T]) -> T:
        if isinstance(result, schema):
            return result
        if isinstance(result, BaseModel):
            result = result.model_dump()
        if isinstance(result, str):
            result = extract_json(result)
        if not isinstance(result, dict):
            raise LLMError(f"cannot coerce {type(result).__name__} into {schema.__name__}")
        try:
            return schema.model_validate(result)
        except ValidationError as exc:
            raise LLMError(f"model output failed {schema.__name__} validation: {exc}") from exc

    def _request(
        self,
        model: str,
        system: str,
        parts: Sequence[Part],
        schema: Type[T],
        max_tokens: int,
    ) -> tuple[Any, tuple[int, int]]:
        import anthropic

        content = [p.api_block() for p in parts]
        messages = [{"role": "user", "content": content}]
        client = self.client  # may raise LLMError when no credential is available

        if self._structured_output_ok and _sdk_takes_output_config(client):
            try:
                response = self._create(
                    client,
                    model=model,
                    max_tokens=max_tokens,
                    system=system,
                    messages=messages,
                    output_config={
                        "format": {
                            "type": "json_schema",
                            "schema": schema.model_json_schema(),
                        }
                    },
                )
            except anthropic.BadRequestError as exc:
                # This model does not accept a JSON-schema output format. Fall
                # back to prompted JSON for the rest of the run.
                log.info("structured output unavailable (%s); using text parsing", exc)
                self._structured_output_ok = False
            else:
                return self._unpack(response)

        response = self._create(
            client,
            model=model,
            max_tokens=max_tokens,
            system=system + "\n\nReply with a single JSON object and nothing else.",
            messages=messages,
        )
        return self._unpack(response)

    @staticmethod
    def _create(client: Any, **kwargs: Any) -> Any:
        """One place to turn SDK failures into an LLMError a stage can survive."""
        import anthropic

        try:
            return client.messages.create(**kwargs)
        except anthropic.BadRequestError:
            raise
        except anthropic.APIError as exc:
            raise LLMError(f"the API call failed: {exc}") from exc
        except TypeError as exc:
            # The SDK raises this for an unresolvable credential.
            raise LLMError(
                f"the API call could not be made: {exc}. Set ANTHROPIC_API_KEY, "
                "or use --dry-run to skip every model call."
            ) from exc

    @staticmethod
    def _unpack(response: Any) -> tuple[Any, tuple[int, int]]:
        usage = getattr(response, "usage", None)
        tokens = (
            int(getattr(usage, "input_tokens", 0) or 0),
            int(getattr(usage, "output_tokens", 0) or 0),
        )
        if getattr(response, "stop_reason", None) == "refusal":
            details = getattr(response, "stop_details", None)
            raise LLMError(
                "the model declined this request"
                + (f" ({getattr(details, 'category', None)})" if details else "")
            )
        parsed = getattr(response, "parsed_output", None)
        if parsed is not None:
            return parsed, tokens
        chunks = [
            block.text
            for block in getattr(response, "content", [])
            if getattr(block, "type", None) == "text"
        ]
        if not chunks:
            raise LLMError("the model returned no text content")
        return "\n".join(chunks), tokens


def _sdk_takes_output_config(client: Any) -> bool:
    """Whether this SDK build accepts a JSON-schema ``output_config``.

    Checked by signature rather than by catching TypeError, which would also
    swallow the SDK's missing-credential error.
    """
    import inspect

    try:
        params = inspect.signature(client.messages.create).parameters
    except (TypeError, ValueError):
        return True
    return "output_config" in params or any(
        p.kind is inspect.Parameter.VAR_KEYWORD for p in params.values()
    )


def load_prompt(name: str, **substitutions: str) -> str:
    """Read a prompt file from ``perch/prompts`` and fill ``{placeholders}``."""
    from .config import PROMPTS_DIR

    path = PROMPTS_DIR / name
    if not path.is_file():
        raise FileNotFoundError(f"prompt not found: {path}")
    text = path.read_text()
    for key, value in substitutions.items():
        text = text.replace("{" + key + "}", value)
    return text
