"""The secret scrub has no minimum-term-length floor, so a short secret shreds the page (R3.10).

Three places scrub `_secret_values(spec)` out of page content before it goes somewhere. They were built
to be the same thing and one of them is not:

    audit._redact                    artifact -> DISK             `if val and len(str(val)) >= 4`
    flows._redacted_body_text        page text -> the EXTRACTOR   no floor
    snapshot.capture's five-field    url/title/text/name/value    no floor
                                      -> EVERY prompt

`_redacted_body_text`'s own docstring cites the audit sibling by name — "the guard already existed 500
lines away in this same file" — and then does not carry the guard it cites. The terms include the LOGIN
USERNAME and any secret slot's value, and usernames are short and ordinary words in a way passwords are
not.

MEASURED against main @ 0.83.0, on `str.replace` with no floor:

    term='1'      Inbox: [REDACTED]2 unread. Open tickets: [REDACTED]2345.
    term='bo'     In[REDACTED]x: 12 unread. ... Re[REDACTED]ot at 3pm.

That text is what the strong-tier extractor reads to answer the flow's question, and what the agent reads
to choose its next action. `[REDACTED]` is indistinguishable from a value that is simply absent, so the
extractor either returns `found=False` — loud, survivable — or reads a number out of a mangled span and
returns it as a clean success. The register files this under `confidentiality (no inviolable)`; the
second outcome is inviolable #2, returning WRONG silently.

WHAT THE FLOOR DOES NOT FIX, measured the same way, which is why it is a mitigation and not a cure:

    term='1234'   Open tickets: 12345  ->  Open tickets: [REDACTED]5
    term='smith'  Blacksmith Ltd       ->  Black[REDACTED] Ltd

Both clear a `>= 4` floor and still corrupt the page. No string rule separates "the secret appearing as a
secret" from "the same characters appearing legitimately" — the keyword classifier's problem one surface
over, and the reason the residual is pinned below rather than papered over.

THE FLOOR ALSO COSTS SOMETHING, and it is a deliberate trade rather than a free win: a secret shorter
than 4 characters stops being redacted on the channel that reaches the MODEL. `audit._redact` already
made that call for the disk channel; this extends it. A 4-digit PIN still redacts.
"""

from __future__ import annotations

import http.server
import threading
from pathlib import Path

import pytest

from ultracua.cache import FlowCache
from ultracua.flows import FlowSpec, SlotSpec, learn
from ultracua.llm.base import LLMRequest, LLMResponse, Router, Tier
from ultracua.llm.mock import MockClient
from ultracua.providers.scripted import ScriptedProvider
from ultracua.snapshot import REDACTED

SECRET_ENV = "ULTRACUA_TEST_SECRET"

# "bo" appears inside ordinary words on this page ("Inbox", "Reboot"), which is the whole point: a short
# secret is a substring of ordinary copy. The value the flow must extract is deliberately NOT adjacent to
# any secret, so a mangled extraction cannot be blamed on the fixture.
PAGE = """<h1>Support</h1>
<p>Inbox: 12 unread. Reboot at 3pm.</p>
<p>Open tickets: 47</p>"""


class _Recorder(MockClient):
    """A MockClient that keeps EVERY request, not just the last — the extractor call is not the only one."""

    def __init__(self, *a, **kw) -> None:
        super().__init__(*a, **kw)
        self.seen: list[str] = []

    async def complete(self, req: LLMRequest) -> LLMResponse:
        self.seen.append("\n".join(str(getattr(m, "content", m)) for m in (req.messages or [])))
        return await super().complete(req)


def _serve(page: str):
    class _H(http.server.BaseHTTPRequestHandler):
        def log_message(self, *a) -> None:
            pass

        def do_GET(self) -> None:  # noqa: N802
            body = page.encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _H)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd, f"http://127.0.0.1:{httpd.server_address[1]}"


async def test_a_short_secret_does_not_shred_the_page_the_extractor_reads(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """THE FINDING, end to end through a real flow rather than at the string helper.

    The flow declares a secret slot whose env value is `bo`. Nothing on this page is that secret — it is
    a coincidence of ordinary English — and every occurrence is excised before the extractor sees it.
    """
    monkeypatch.setenv(SECRET_ENV, "bo")
    httpd, base = _serve(PAGE)
    cache = FlowCache(root=tmp_path / "c")
    try:
        spec = FlowSpec(
            name="tickets", start_url=f"{base}/", goal="read the open ticket count",
            extract="the number of open tickets", headless=True,
            slots={"pin": SlotSpec(secret=True, secret_env=SECRET_ENV)},
        )
        mc = _Recorder(actions=[{"found": True, "data": 47}], tool_name="submit")
        router = Router(fast=Tier(mc, "m"), strong=Tier(mc, "m"))

        # BOTH a provider AND a router, and that is not a detail. `learn()` computes
        # `fixed = provider is not None and router is not None`, and when it is False it builds a REAL
        # provider from `settings.provider` for the agent loop. Passing `provider=None` here made this
        # test construct a live Anthropic client: green locally, where `config` loads `.env` into the
        # environment, and a hard TypeError on CI, which is key-less by design — and worse, locally it
        # was driving the agent with real API calls. The suite is key-less; a test that needs a key is a
        # defect in the test.
        #
        # The answer is on the start page, so the agent has nothing to do but finish.
        provider = ScriptedProvider([{"action": "done", "intent": "the count is on this page"}])
        await learn(spec, provider=provider, router=router, cache=cache, verify_replay=False)

        assert mc.seen, "premise: the extractor must have been called, or this measures nothing"
        blob = "\n".join(mc.seen)
        assert "Open tickets: 47" in blob, "premise: the fixture's answer must reach the extractor at all"

        assert REDACTED not in blob, (
            f"a short secret was scrubbed out of ordinary page copy before the extractor read it — "
            f"'Inbox' and 'Reboot' contain the secret 'bo' by coincidence. Excerpt: "
            f"{blob[blob.find(REDACTED) - 60:blob.find(REDACTED) + 60]!r}")
        assert "Inbox" in blob and "Reboot" in blob, "the ordinary words must survive intact"
    finally:
        httpd.shutdown()
        httpd.server_close()


def test_the_floor_is_the_sibling_s_floor(tmp_path: Path) -> None:
    """One definition, not three. The three scrubs are the same rule applied to different channels, and
    the finding is that two of them drifted from the third — so the fix is that there is ONE place where
    the floor lives, and a fourth channel added tomorrow inherits it rather than re-deriving it.
    """
    from ultracua import audit
    from ultracua.snapshot import redact_terms

    assert redact_terms(("bo", "1")) == (), "a term below the floor must be dropped, not applied"
    assert redact_terms(("1234", "")) == ("1234",), "at the floor it still applies; empties are dropped"
    # and the sibling agrees, because it is the same predicate
    assert audit._redact("Reboot", ("bo",)) == "Reboot"
    assert audit._redact("Open 1234", ("1234",)) == f"Open {REDACTED}"


def test_what_the_floor_COSTS_is_pinned_as_a_decision(tmp_path: Path) -> None:
    """The other direction, and it is a real loss, not a technicality: a secret shorter than the floor is
    no longer scrubbed on the channel that reaches the MODEL. It previously was.

    Pinned so it is a decision someone made rather than a side effect nobody noticed — the same reason
    the quiet direction is pinned in the fleet-skip work. The argument for paying it: `audit._redact` has
    always made this trade on the disk channel for the identical terms, a 1-3 character secret is not
    meaningfully a secret, and the damage it does when scrubbed is severe, silent and measured (it shreds
    ordinary page copy into an extractor that cannot tell `[REDACTED]` from "not present"). A 4-digit PIN
    is at the floor and still redacts, which is the case worth protecting.

    If this trade is ever judged wrong, the fix is NOT to drop the floor — that reinstates R3.10 — it is
    to stop putting short non-secrets (the login USERNAME) in the term list.
    """
    from ultracua.snapshot import apply_redactions, redact_terms

    assert redact_terms(("abc",)) == (), "a 3-char secret is deliberately NOT scrubbed any more"
    assert apply_redactions("my pin is abc", ("abc",)) == "my pin is abc"
    assert redact_terms(("1234",)) == ("1234",), "a 4-digit PIN is at the floor and still redacts"


def test_the_residual_the_floor_does_not_close_is_pinned(tmp_path: Path) -> None:
    """PINNED, NOT FIXED. A term at or above the floor is still an unconditional substring replace, so a
    legitimate occurrence is still excised. This test DEMONSTRATES the surviving damage rather than
    asserting it away, so that a future change in either direction — closing it, or widening it — has to
    come here and say so.

    Nothing string-based separates these two cases; it is the keyword classifier's problem one surface
    over (`docs/open-defects.md`, MUTATING_KEYWORDS), and the same conclusion applies: the rule is a
    guess, so keep its blast radius small rather than pretending it is precise.
    """
    from ultracua.snapshot import apply_redactions

    assert apply_redactions("Open tickets: 12345", ("1234",)) == f"Open tickets: {REDACTED}5"
    assert apply_redactions("Blacksmith Ltd", ("smith",)) == f"Black{REDACTED} Ltd"
