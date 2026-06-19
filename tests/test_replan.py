"""Phase F — suffix re-planning heal.

When a cached step breaks on replay and the single-step heal can't fix it, the engine re-authors
ONLY the broken tail from the current page, preserves the working prefix, splices, and re-caches.
These tests drive it with the scripted teacher over the demo-shop fixture (no real LLM).
"""

from __future__ import annotations

from pathlib import Path

from ultracua.cache import FlowCache, flow_key
from ultracua.flow import run_cached
from ultracua.locators import LocatorSpec
from ultracua.providers.scripted import ScriptedProvider

from benchmarks.shop_flow import GOAL, STEPS, SUCCESS_TEXT, index_url

# index 2 of the learned flow = "open the widget detail page" (the click that leaves the results page).
_BROKEN = 2


async def _learn_then_break(cache: FlowCache, url: str) -> None:
    """Learn the 4-step demo-shop flow, then corrupt step 2's locator so it can't resolve."""
    learn = await run_cached(url, GOAL, ScriptedProvider(list(STEPS)), cache, mode="learn", headless=True)
    assert learn.success
    cached = cache.get(flow_key(GOAL, url))
    assert cached is not None and len(cached.steps) == 4
    cached.steps[_BROKEN].locator = LocatorSpec(role="link", name="this result is gone", tag="a")
    cache.put(cached)


async def test_suffix_replan_repairs_broken_tail_and_preserves_prefix(tmp_path: Path) -> None:
    cache = FlowCache(root=tmp_path / "cache")
    url = index_url()
    await _learn_then_break(cache, url)

    # AUTO replay with a provider that DECLINES the single-step heal (the changed path needs more
    # than one corrective action), then re-authors the tail: open the result, add to cart, done.
    heal_then_replan = ScriptedProvider([
        {"action": "give_up", "intent": "single-step heal can't fix the changed path"},
        {"action": "click", "role": "link", "name": "open widget x",
         "intent": "open the widget detail page"},
        {"action": "click", "role": "button", "name": "add to cart",
         "intent": "add the widget to the cart"},
        {"action": "done", "intent": "reached the added-to-cart state"},
    ])
    replay = await run_cached(url, GOAL, heal_then_replan, cache, mode="auto", headless=True)

    assert replay.success
    assert replay.mode == "replay+replan"
    assert SUCCESS_TEXT.lower() in replay.final_text.lower()   # actually reached the goal state

    repaired = cache.get(flow_key(GOAL, url))
    assert repaired is not None
    # The working prefix (the two steps before the break) is preserved verbatim...
    assert repaired.steps[0].intent == "enter the search query"
    assert repaired.steps[1].intent == "submit the search"
    # ...and the broken tail was re-authored from the current page with a fresh, resolvable locator.
    assert repaired.steps[_BROKEN].intent == "open the widget detail page"
    assert repaired.steps[_BROKEN].locator is not None
    assert repaired.steps[_BROKEN].locator.name != "this result is gone"


async def test_repaired_flow_replays_at_zero_llm(tmp_path: Path) -> None:
    # After a suffix-replan repair sticks, the flow must replay deterministically again — 0-LLM.
    cache = FlowCache(root=tmp_path / "cache")
    url = index_url()
    await _learn_then_break(cache, url)
    heal_then_replan = ScriptedProvider([
        {"action": "give_up", "intent": "decline the single-step heal"},
        {"action": "click", "role": "link", "name": "open widget x",
         "intent": "open the widget detail page"},
        {"action": "click", "role": "button", "name": "add to cart",
         "intent": "add the widget to the cart"},
        {"action": "done", "intent": "reached the added-to-cart state"},
    ])
    assert (await run_cached(url, GOAL, heal_then_replan, cache, mode="auto", headless=True)).success

    again = await run_cached(url, GOAL, None, cache, mode="replay", headless=True)
    assert again.success
    assert again.llm_calls == 0 and again.healed_steps == 0
    assert SUCCESS_TEXT.lower() in again.final_text.lower()


async def test_suffix_replan_refuses_to_perform_a_new_write(tmp_path: Path) -> None:
    # WRITE SAFETY: a replay-triggered re-author must NEVER perform a mutating action (it isn't
    # approved and could double-submit). If reaching the goal needs a write, the replan ABORTS and the
    # replay fails loud — the write must not fire, and no write-containing flow may be cached.
    cache = FlowCache(root=tmp_path / "cache")
    url = index_url()
    await _learn_then_break(cache, url)

    # The replan declines the heal, opens the result page, then tries a CLICK with a mutating intent.
    tries_a_write = ScriptedProvider([
        {"action": "give_up", "intent": "decline the single-step heal"},
        {"action": "click", "role": "link", "name": "open widget x",
         "intent": "open the widget detail page"},
        {"action": "click", "role": "button", "name": "add to cart",
         "intent": "submit the purchase order"},   # mutating intent -> must be blocked, never clicked
    ])
    # mode="repair" isolates the replan (no fall-through to a full re-learn).
    replay = await run_cached(url, GOAL, tries_a_write, cache, mode="repair", headless=True)

    assert replay.success is False                               # couldn't reach the goal without a write
    assert SUCCESS_TEXT.lower() not in replay.final_text.lower()  # the write never fired
    # No write-containing repaired flow was cached — the original (still-broken) flow is untouched.
    cached = cache.get(flow_key(GOAL, url))
    assert cached is not None and len(cached.steps) == 4
    assert cached.steps[_BROKEN].locator is not None
    assert cached.steps[_BROKEN].locator.name == "this result is gone"  # unchanged (not re-cached)


async def test_pure_replay_never_replans_without_a_provider(tmp_path: Path) -> None:
    # The 0-LLM guarantee: mode="replay" (no heal provider) must NOT silently re-plan on drift —
    # it fails loud so the caller can alert, never sneaking in surprise model calls.
    cache = FlowCache(root=tmp_path / "cache")
    url = index_url()
    await _learn_then_break(cache, url)

    replay = await run_cached(url, GOAL, None, cache, mode="replay", headless=True)
    assert replay.success is False
    assert replay.llm_calls == 0
    assert replay.mode == "replay"
