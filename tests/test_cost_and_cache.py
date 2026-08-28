"""Cost control and the response cache."""

from __future__ import annotations

import pytest

from perch.cache import ResponseCache, hash_files
from perch.config import load_config
from perch.cost import CostLimitExceeded, CostTracker, estimate_run, frame_tokens, image_tokens
from perch.llm import LLMClient, LLMDisabled, TextPart, extract_json, set_stub
from perch.models import Viewpoint


# --- cost --------------------------------------------------------------------


def test_image_tokens_follow_the_published_ratio():
    assert image_tokens(768, 432) == pytest.approx(331776 / 750, abs=1)
    assert frame_tokens(768, 16 / 9) == image_tokens(768, 432)
    assert frame_tokens(768, 9 / 16) == frame_tokens(768, 16 / 9)


def test_tracker_accumulates_and_prices_by_model():
    cfg = load_config()
    tracker = CostTracker(cfg)
    tracker.record("claude-haiku-4-5", 1_000_000, 0)
    assert tracker.total == pytest.approx(1.0)
    tracker.record("claude-opus-5", 0, 1_000_000)
    assert tracker.total == pytest.approx(26.0)
    assert [s.model for s in tracker.spend()] == ["claude-haiku-4-5", "claude-opus-5"]


def test_a_cached_call_costs_nothing_but_is_still_counted():
    tracker = CostTracker(load_config())
    tracker.record("claude-opus-5", 500_000, 500_000, cached=True)
    assert tracker.total == 0.0
    assert tracker.spend()[0].calls == 1
    assert tracker.spend()[0].cached_calls == 1


def test_the_ceiling_stops_a_run_before_it_overspends():
    tracker = CostTracker(load_config(), max_cost=0.10)
    tracker.record("claude-haiku-4-5", 50_000, 0)  # $0.05
    tracker.check(0.02)  # still inside the ceiling
    with pytest.raises(CostLimitExceeded, match="max-cost"):
        tracker.check(0.30)


def test_an_unknown_model_is_priced_at_the_top_tier():
    cfg = load_config()
    price = cfg.price("some-model-released-next-year")
    assert price.input >= max(p.input for p in cfg.pricing.values())


def test_the_estimate_grows_with_frames_and_modules():
    cfg = load_config()
    small = estimate_run(cfg, frame_count=50, module_count=2, phase_count=3, has_telemetry=True)
    large = estimate_run(cfg, frame_count=400, module_count=8, phase_count=6, has_telemetry=True)
    assert 0 < small.total < large.total
    assert "total" in small.render()


def test_the_segment_pass_is_only_estimated_without_telemetry():
    cfg = load_config()
    tracked = estimate_run(cfg, frame_count=200, module_count=4, phase_count=4, has_telemetry=True)
    untracked = estimate_run(cfg, frame_count=200, module_count=4, phase_count=4, has_telemetry=False)
    assert "segment" not in dict(tracked.lines)
    assert "segment" in dict(untracked.lines)
    assert untracked.total > tracked.total


# --- cache -------------------------------------------------------------------


def test_cache_round_trips(tmp_path):
    cache = ResponseCache(tmp_path)
    key = cache.key({"model": "x", "parts": ["a"]})
    assert cache.get("ns", key) is None
    cache.put("ns", key, {"data": {"mount": "wing"}})
    assert cache.get("ns", key)["data"]["mount"] == "wing"
    assert cache.stats() == {"hits": 1, "misses": 1}


def test_cache_keys_are_order_insensitive_for_dicts_but_not_for_lists(tmp_path):
    cache = ResponseCache(tmp_path)
    assert cache.key({"a": 1, "b": 2}) == cache.key({"b": 2, "a": 1})
    assert cache.key({"parts": ["a", "b"]}) != cache.key({"parts": ["b", "a"]})


def test_a_corrupt_entry_is_a_miss_not_a_crash(tmp_path):
    cache = ResponseCache(tmp_path)
    key = cache.key({"x": 1})
    cache.put("ns", key, {"data": {}})
    path = next(tmp_path.rglob("*.json"))
    path.write_text("{ not json")
    assert cache.get("ns", key) is None


def test_frame_hash_is_stable_and_order_independent(tmp_path):
    a, b = tmp_path / "a.jpg", tmp_path / "b.jpg"
    a.write_bytes(b"one")
    b.write_bytes(b"two")
    assert hash_files([a, b]) == hash_files([b, a])
    b.write_bytes(b"three")
    assert hash_files([a, b]) != hash_files([a, a])


# --- llm client --------------------------------------------------------------


def test_a_disabled_client_refuses_to_call(tmp_path):
    cfg = load_config()
    client = LLMClient(cfg, ResponseCache(tmp_path), CostTracker(cfg), enabled=False)
    with pytest.raises(LLMDisabled):
        client.complete_json(
            model="claude-haiku-4-5", system="s", parts=[TextPart("hi")], schema=Viewpoint
        )


def test_the_stub_result_is_cached_so_the_second_call_is_free(tmp_path):
    cfg = load_config()
    cache = ResponseCache(tmp_path)
    tracker = CostTracker(cfg)
    client = LLMClient(cfg, cache, tracker)

    calls = {"n": 0}

    def stub(**kwargs):
        calls["n"] += 1
        return {"mount": "wing", "notes": "on the strut"}

    set_stub(stub)
    args = dict(
        model="claude-haiku-4-5", system="s", parts=[TextPart("hi")], schema=Viewpoint
    )
    first = client.complete_json(**args)
    second = client.complete_json(**args)

    assert first.mount == second.mount == "wing"
    assert calls["n"] == 1, "the second call should come from the cache"
    assert tracker.spend()[0].cached_calls == 1


def test_changing_the_prompt_invalidates_the_cache(tmp_path):
    cfg = load_config()
    client = LLMClient(cfg, ResponseCache(tmp_path), CostTracker(cfg))
    calls = {"n": 0}

    def stub(**kwargs):
        calls["n"] += 1
        return {"mount": "wing"}

    set_stub(stub)
    client.complete_json(model="m", system="v1", parts=[TextPart("hi")], schema=Viewpoint)
    client.complete_json(model="m", system="v2", parts=[TextPart("hi")], schema=Viewpoint)
    assert calls["n"] == 2


# --- json recovery -----------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        '{"mount": "wing"}',
        'Here you go:\n```json\n{"mount": "wing"}\n```\nHope that helps.',
        'Sure. {"mount": "wing"} — that is my reading.',
        '{"notes": "a } brace inside a string", "mount": "wing"}',
    ],
)
def test_json_is_recovered_from_prose(text):
    assert extract_json(text)["mount"] == "wing"


def test_json_recovery_fails_loudly():
    from perch.llm import LLMError

    with pytest.raises(LLMError):
        extract_json("I would rather not answer.")
