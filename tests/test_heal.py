"""Self-heal hardening: a heal that produces no observable effect must NOT persist its
(likely-wrong) locator into the cache; a heal that changes state persists the corrected locator."""

from __future__ import annotations

from ultracua.browser import BrowserSession
from ultracua.cache import CachedStep
from ultracua.flow import _maybe_heal
from ultracua.locators import LocatorSpec
from ultracua.providers.scripted import ScriptedProvider
from ultracua.timing import StepTrace

_GOAL = "click the control"


async def test_heal_does_not_persist_a_no_effect_click() -> None:
    # an inert button: clicking it changes nothing (no nav, no DOM change)
    html = "<!doctype html><html><body><h1>Page</h1><button>Inert</button></body></html>"
    session = await BrowserSession(headless=True).start()
    try:
        await session.page.set_content(html)
        broken = LocatorSpec(role="button", name="Gone", tag="button")  # original cached locator
        step = CachedStep(intent="click the control", action="click", locator=broken)
        provider = ScriptedProvider([
            {"action": "click", "role": "button", "name": "Inert", "intent": "click the control"}
        ])
        ok, note, did_heal = await _maybe_heal(
            session, step, provider, StepTrace(index=0), _GOAL, "locator unresolved (drift)"
        )
        assert did_heal is True
        assert ok is False and "no effect" in note   # the click did nothing -> not trusted
        assert step.locator is broken                 # the (wrong) locator was NOT overwritten
    finally:
        await session.close()


async def test_heal_persists_when_click_changes_state() -> None:
    # clicking renames the button (an interactable name change -> the fingerprint changes)
    html = ("<!doctype html><html><body>"
            "<button onclick=\"this.textContent='Done'\">Click me</button></body></html>")
    session = await BrowserSession(headless=True).start()
    try:
        await session.page.set_content(html)
        step = CachedStep(intent="click the control", action="click",
                          locator=LocatorSpec(role="button", name="Gone", tag="button"))
        provider = ScriptedProvider([
            {"action": "click", "role": "button", "name": "Click me", "intent": "click the control"}
        ])
        ok, note, did_heal = await _maybe_heal(
            session, step, provider, StepTrace(index=0), _GOAL, "locator unresolved (drift)"
        )
        assert ok is True and did_heal is True
        assert step.locator is not None and step.locator.name == "Click me"  # corrected + persisted
    finally:
        await session.close()
