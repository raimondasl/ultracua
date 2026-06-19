"""Write-safety classification (#12): a DOM-structural mutation classifier that judges a click by
whether it submits a form and with what METHOD — replacing the keyword-only heuristic that both
missed icon-only / bland-intent submits and false-fired on reads like "submit the search".
"""

from __future__ import annotations

from pathlib import Path

from ultracua.browser import BrowserSession
from ultracua.cache import FlowCache, flow_key
from ultracua.flow import run_cached
from ultracua.providers.scripted import ScriptedProvider
from ultracua.safety import classify_mutation, is_mutating
from ultracua.snapshot import mutation_context

_FORMS_URL = (Path(__file__).parents[1] / "benchmarks" / "fixtures" / "forms.html").resolve().as_uri()

_FORMS = """<!doctype html><html><body>
  <form method="post" action="/save"><button id="post" type="submit" aria-label="save"></button></form>
  <form method="get" action="/search"><input name="q"><button id="get" type="submit">Submit</button></form>
  <button id="js-write" type="button" onclick="x()">Delete account</button>
  <button id="js-read" type="button">Toggle panel</button>
</body></html>"""


# --- pure classifier (structural ctx passed directly) -----------------------------------------
def test_post_form_submit_is_a_write_even_with_a_bland_intent() -> None:
    ctx = {"submit": True, "form_method": "post"}
    assert classify_mutation("click", intent="continue", name="", ctx=ctx) is True   # icon-only submit


def test_get_form_submit_is_not_a_write_even_with_a_submit_keyword() -> None:
    ctx = {"submit": True, "form_method": "get"}
    # "submit the search" trips the keyword list, but a GET form is an idempotent read -> NOT a write.
    assert classify_mutation("click", intent="submit the search", name="Search", ctx=ctx) is False


def test_non_idempotent_methods_are_writes() -> None:
    for m in ("post", "put", "delete", "patch"):
        assert classify_mutation("click", ctx={"submit": True, "form_method": m}) is True


def test_no_form_falls_back_to_keywords() -> None:
    no_form = {"submit": False, "form_method": ""}
    assert classify_mutation("click", intent="delete account", name="", ctx=no_form) is True   # keyword
    assert classify_mutation("click", intent="toggle the panel", name="", ctx=no_form) is False
    assert classify_mutation("click", intent="continue", name="", ctx=None) is False             # no ctx


def test_type_is_never_mutating_press_uses_keywords() -> None:
    assert classify_mutation("type", intent="submit the form", ctx={"submit": True, "form_method": "post"}) is False
    assert classify_mutation("press", intent="press enter to submit") is True   # Enter can submit
    assert classify_mutation("press", intent="press the down arrow") is False


def test_is_mutating_backcompat_is_keyword_only() -> None:
    assert is_mutating("click", "place the order") is True     # keyword, no DOM context
    assert is_mutating("click", "open the page") is False


# --- the DOM probe against real forms ---------------------------------------------------------
async def test_mutation_context_reads_form_method_and_submit() -> None:
    session = await BrowserSession(headless=True).start()
    try:
        await session.page.set_content(_FORMS)
        post = await mutation_context(session.page.locator("#post"))
        get = await mutation_context(session.page.locator("#get"))
        js_write = await mutation_context(session.page.locator("#js-write"))

        assert post == {"submit": True, "form_method": "post"}
        assert get == {"submit": True, "form_method": "get"}
        assert js_write == {"submit": False, "form_method": ""}   # type=button, no form

        # End-to-end: the icon-only POST submit is a write; the GET "Submit" button is not.
        assert classify_mutation("click", intent="continue", name="", ctx=post) is True
        assert classify_mutation("click", intent="submit the search", name="Submit", ctx=get) is False
        assert classify_mutation("click", intent="delete account", name="", ctx=js_write) is True
    finally:
        await session.close()


# --- end-to-end: the classification reaches the cached step during a learn ---------------------
async def _learn_click(tmp_path, goal, name, intent):
    cache = FlowCache(root=tmp_path / "cache")
    steps = [{"action": "click", "role": "button", "name": name, "intent": intent},
             {"action": "done", "intent": "done"}]
    report = await run_cached(_FORMS_URL, goal, ScriptedProvider(steps), cache, mode="learn", headless=True)
    assert report.success
    flow = cache.get(flow_key(goal, _FORMS_URL))
    assert flow is not None and len(flow.steps) == 1
    return flow.steps[0]


async def test_learn_marks_icon_only_post_submit_as_mutating(tmp_path) -> None:
    # Bland intent, no keyword, no visible text — only the POST form method reveals it's a write.
    step = await _learn_click(tmp_path, "save the changes", "save changes", "click the control")
    assert step.mutating is True   # caught structurally (the keyword heuristic would have missed it)


async def test_learn_does_not_flag_get_search_submit_as_mutating(tmp_path) -> None:
    # Intent says "submit" (a keyword), but the GET form is an idempotent read.
    step = await _learn_click(tmp_path, "run the search", "run search", "submit the search")
    assert step.mutating is False  # GET form overrides the keyword false-positive
