"""An unnamed control renders as `button: ` and the agent cannot tell one from another (R4.131).

MEASURED ACROSS THE CORPUS before this shipped: **106 of 1036** observed interactables carry no
accessible name -- Gitea 0-4%, Odoo **19-22%** -- and on an Odoo list page that includes a toolbar of
SEVEN anonymous buttons, one of which opens the search panel. That is the control PR #232 wrongly
blamed on R4.102: it is not below the fold, it is at y=54, rank 11 of 80, and it is nameless.

WHY THIS IS A NEW FIELD RATHER THAN A BETTER `nameOf`, which is the whole design and is derived, not
preferred: `_ACCNAME_JS` is SHARED by three call sites --

    SNAPSHOT_JS   what the agent sees            (the thing we want to improve)
    DESCRIBE_JS   the cached locator's `name`    (replay binds through get_by_role(name=...))
    SCOPE_JS      the scope fingerprint          (every recipe's precond_fingerprint)

-- so widening it would invent names Playwright's accname never computes, breaking replay binds, and
would change the stored fingerprint of every cached flow in every deployment. `hint` is
observation-only: absent from the fingerprint basis (`role/name/tag + url`), absent from the locator
spec, and unreachable as a target because `Action` has no `name` field at all.

COVERAGE AND COST, measured on the same 14 corpus pages: **58/106 (55%) named**, for **+75 chars per
turn (~19 tokens), 2.3% of the element block**. The 48 it does not name are row-selection checkboxes
-- 15 of 15 sampled -- which legitimately have no label.
"""

from __future__ import annotations

import http.server
import inspect
import threading

import pytest

from ultracua import snapshot as snap
from ultracua.browser import BrowserSession
from ultracua.providers import llm_agent

PAGE = """
<h1>Toolbar</h1>
<button id="named" data-tooltip="Ignored">Save</button>
<button id="tip" data-tooltip="List"><i class="oi oi-view-list"></i></button>
<button id="desc"><i class="fa fa-caret-down" title="Toggle Search Panel"></i></button>
<button id="icon"><i class="fa fa-cog"></i></button>
<button id="modifier"><i class="oi oi-fw oi-settings-adjust"></i></button>
<button id="bare"><span></span></button>
<input id="row" type="checkbox">
"""


def _serve(body: str):
    payload = body.encode()

    class _H(http.server.BaseHTTPRequestHandler):
        def log_message(self, *a) -> None:
            pass

        def do_GET(self) -> None:  # noqa: N802
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

    h = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _H)
    threading.Thread(target=h.serve_forever, daemon=True).start()
    return h, f"http://127.0.0.1:{h.server_port}/"


async def _observe(body: str = PAGE, **kw):
    httpd, url = _serve(body)
    try:
        async with BrowserSession(headless=True, **kw) as sess:
            await sess.goto(url)
            obs = await sess.snapshot()
            return obs, {e.ref: e for e in obs.elements}
    finally:
        httpd.shutdown()
        httpd.server_close()


def _by_text(elements, needle: str):
    """Find the element whose hint carries `needle` — refs are positional, names are empty."""
    return [e for e in elements if needle in (e.hint or "")]


async def test_a_tooltip_names_an_unnamed_control() -> None:
    """Odoo's whole view switcher is this shape: `data-tooltip` and nothing else."""
    obs, _ = await _observe()
    hit = _by_text(obs.elements, "tooltip: List")
    print(f"    {[e.hint for e in obs.elements if e.hint]}")
    assert hit, "a control whose only label is `data-tooltip` got no hint"
    assert not hit[0].name, "premise lost: that control acquired an accessible name"


async def test_a_descendant_title_names_an_icon_only_button() -> None:
    """THE CONTROL THIS FINDING IS ABOUT. Odoo's search toggler is
    `<button><i class="fa fa-caret-down" title="Toggle Search Panel"></i></button>` -- real AccName
    stops at the element for `title`, so `nameOf` correctly sees nothing."""
    obs, _ = await _observe()
    hit = _by_text(obs.elements, "Toggle Search Panel")
    assert hit, "an icon-only button whose inner <i> carries the label got no hint"
    assert hit[0].hint.startswith("labelled:"), f"wrong source: {hit[0].hint!r}"


async def test_an_icon_class_is_the_last_resort() -> None:
    obs, _ = await _observe()
    assert _by_text(obs.elements, "icon: cog"), "an icon-only button with no label got no hint"


async def test_an_icon_modifier_class_is_not_a_glyph_name() -> None:
    """MEASURED, not anticipated: `oi oi-fw oi-settings-adjust` rendered `(icon: fw)` -- font
    awesome's FIXED-WIDTH modifier, presented in the same shape as a real glyph. That is worse than
    no hint, because the agent cannot tell them apart and may reason about it."""
    obs, _ = await _observe()
    assert not _by_text(obs.elements, "icon: fw"), "a modifier class was rendered as a glyph name"
    assert _by_text(obs.elements, "settings-adjust"), "the real glyph was skipped with the modifier"


async def test_a_named_control_gets_no_hint() -> None:
    """TOKEN ECONOMY AND THE PRECEDENCE RULE IN ONE. The `Save` button carries a `data-tooltip` that
    must never appear: it has an accessible name, so the hint is both unnecessary and misleading, and
    computing it would cost prompt tokens on every named control on every turn."""
    obs, _ = await _observe()
    named = [e for e in obs.elements if e.name == "Save"]
    assert named, "premise lost: the named button is missing"
    assert named[0].hint is None, (
        f"a NAMED control carries hint {named[0].hint!r}. The hint exists only for controls the "
        f"agent cannot otherwise identify; on a named one it is noise that costs tokens every turn.")
    assert not _by_text(obs.elements, "Ignored")


async def test_a_control_with_nothing_to_go_on_gets_no_hint() -> None:
    """The floor: a bare button and a row checkbox stay unhinted rather than inventing something."""
    obs, _ = await _observe()
    unnamed = [e for e in obs.elements if not (e.name or "").strip()]
    assert unnamed, "premise lost: nothing unnamed on the fixture"
    assert any(e.hint is None for e in unnamed), "every unnamed control got a hint; that is a guess"


# --- the three things the hint must NOT touch -----------------------------------------------------

async def test_the_hint_is_not_in_the_observation_fingerprint() -> None:
    """A fingerprint change invalidates every cached recipe's `precond_fingerprint`. The basis is
    `sorted([role, name, tag]) + url`, so acquiring a hint must be invisible to it.

    ONE PAGE, ONE URL, THE ATTRIBUTE ADDED IN PLACE. A first draft served two bodies from two
    `_serve()` calls and compared the fingerprints -- which differed, because each server binds a
    fresh EPHEMERAL PORT and `url` is in the basis. That is R4.114's harness bug (a probe defeated by
    an ephemeral port) in a test, and it would have been read as the feature breaking the gate.
    """
    httpd, url = _serve('<button id="a"><i class="fa fa-star"></i></button>')
    try:
        async with BrowserSession(headless=True) as sess:
            await sess.goto(url)
            before = await sess.snapshot()
            await sess.page.evaluate(
                "() => document.getElementById('a').setAttribute('data-tooltip', 'Kanban')")
            after = await sess.snapshot()
    finally:
        httpd.shutdown()
        httpd.server_close()
    print(f"    {before.fingerprint} -> {after.fingerprint}")
    assert any("Kanban" in (e.hint or "") for e in after.elements), (
        "premise lost: the tooltip produced no hint, so this cell asserts nothing")
    assert before.fingerprint == after.fingerprint, (
        "acquiring a hint changed the observation fingerprint. Every cached flow's stored "
        "fingerprint would be invalidated by this release, and the mutation gate would read drift "
        "on every page it helps.")


def test_the_shared_accname_helper_was_not_widened() -> None:
    """THE STRUCTURAL PIN, and the reason this field exists at all.

    `_ACCNAME_JS` is concatenated into SNAPSHOT_JS, DESCRIBE_JS and SCOPE_JS. If a later change moves
    the hint sources into it "to simplify", cached locators start binding on names `get_by_role`
    cannot compute and every scope fingerprint shifts. Asserted on the SOURCE because that is the
    shared artifact; the fingerprint cell above covers the behaviour.
    """
    acc = snap._ACCNAME_JS
    # Only HINT-SPECIFIC tokens. A first draft also forbade `querySelectorAll`, which the helper
    # legitimately uses for the wrapping-<label> rule -- a guard that fires on correct code is the
    # over-refusal shape D0 was blocked for, wearing a scan.
    for forbidden in ("data-tooltip", "data-original-title", "hintOf"):
        assert forbidden not in acc, (
            f"`{forbidden}` appeared in the SHARED accessible-name helper. That helper feeds the "
            f"cached locator and the scope fingerprint; widening it breaks replay binds and "
            f"invalidates every stored recipe. Hints belong in SNAPSHOT_JS only.")
    assert "hintOf" in snap.SNAPSHOT_JS, "the hint derivation left SNAPSHOT_JS"


def test_the_scope_fingerprint_js_knows_nothing_about_hints() -> None:
    """The gate's scope hash is `[role, name, tag]` triples. A hint there would change the recorded
    scope of every cached step."""
    assert "hint" not in snap.SCOPE_JS, "SCOPE_JS references hints; recipes would invalidate"


async def test_the_locator_spec_carries_no_hint() -> None:
    """Replay binds on the LocatorSpec. A hint there would be a name Playwright cannot match.

    ASSERTED AS A PROPERTY, NOT A GREP. A first draft checked `"hint" not in DESCRIBE_JS` and went
    red on that file's own prose -- its comments discuss "a hint about which candidate is likely
    best". That is the sixth time a scan in this repository has matched the text explaining it, and
    the rule this project already wrote down is to stop scanning text and assert the property: run
    the real `describe()` against a real hinted control and look at what comes back.
    """
    from ultracua import locators

    assert "hint" not in set(locators.LocatorSpec.model_fields), (
        "LocatorSpec grew a `hint` field; replay would bind on a name get_by_role cannot compute")
    httpd, url = _serve(PAGE)
    try:
        async with BrowserSession(headless=True) as sess:
            await sess.goto(url)
            obs = await sess.snapshot()
            hinted = [e for e in obs.elements if e.hint and "Toggle Search Panel" in e.hint]
            assert hinted, "premise lost: the fixture produced no hinted control to describe"
            spec = await locators.describe(sess.page, hinted[0].ref)
    finally:
        httpd.shutdown()
        httpd.server_close()
    assert spec is not None, "describe() returned nothing for a real element"
    dumped = spec.model_dump()
    print(f"    LocatorSpec.name={dumped.get('name')!r}  role={dumped.get('role')!r}")
    assert "hint" not in dumped
    assert "Toggle Search Panel" not in str(dumped.get("name") or ""), (
        "the hint leaked into the cached locator's NAME. Playwright's accname does not read a "
        "descendant title, so `get_by_role(name=...)` could never match it and replay would fail "
        "loud on exactly the pages this feature exists to help.")


# --- it has to reach the agent, and it has to be safe --------------------------------------------

async def test_the_renderer_shows_the_hint_with_its_source() -> None:
    """A hint the prompt does not render is inert. The SOURCE is rendered with it because a tooltip
    and an icon glyph are not equally good evidence."""
    obs, _ = await _observe()
    text = llm_agent._render(obs, "do the thing", [])
    print("\n".join(ln for ln in text.splitlines() if "(" in ln and "button" in ln))
    assert "(tooltip: List)" in text
    assert "(labelled: Toggle Search Panel)" in text
    assert "(icon: cog)" in text
    assert "Save" in text and "(tooltip: Ignored)" not in text


@pytest.mark.parametrize("secret", ["sk-live-DEADBEEF12345678"])
async def test_a_secret_in_a_tooltip_is_redacted(secret: str) -> None:
    """`hint` is a SIXTH page-derived field rendered into the prompt, and a `data-tooltip` can carry a
    token exactly as an accessible name can ("Copy sk-live-..."). Partial coverage is how R9 shipped
    scrubbing 2 of 5 Observation fields and had to be reopened."""
    body = f'<button data-tooltip="Copy {secret}"><i class="fa fa-copy"></i></button>'
    httpd, url = _serve(body)
    try:
        async with BrowserSession(headless=True) as sess:
            await sess.goto(url)
            obs = await snap.capture(sess.page, 80, redact=(secret,))
    finally:
        httpd.shutdown()
        httpd.server_close()
    hints = [e.hint for e in obs.elements if e.hint]
    print(f"    {hints}")
    assert hints, "premise lost: no hint was produced, so redaction is untested"
    assert all(secret not in (h or "") for h in hints), (
        "a secret survived in `hint`, which is rendered into every prompt and would be written to "
        "the transcript.")
