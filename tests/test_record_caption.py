"""Intent caption (Phase-I recorder) — a best-effort, OFF-replay-path LLM pass that relabels each recorded
step's placeholder intent with a concise, goal-grounded one. The caption feeds self-heal hints, the inspect
output, and (WRITE flows only) the keyword side of `classify_mutation`; it never touches the replay locator,
so replay stays 0-LLM. These tests inject a FAKE captioner (no real LLM) so the suite stays key-less.
"""

from __future__ import annotations

import http.server
import threading

from ultracua.cache import FlowCache, flow_key
from ultracua.flows import FlowSpec, MutateSpec, record
from ultracua.llm.types import LLMResponse, ToolUseBlock
from ultracua.recorder import caption_intents

PAGES = {
    "/read": "<a href='/done'>Continue</a>",
    "/bland": "<h1>Account</h1><button id=x>Manage</button>",   # a bland button, no handler, no wire write
    "/done": "<h1>done</h1>",
}


def _serve():
    class _H(http.server.BaseHTTPRequestHandler):
        def log_message(self, *a) -> None:
            pass

        def do_GET(self) -> None:  # noqa: N802
            body = PAGES.get(self.path.split("?")[0], "<h1>nf</h1>")
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(body.encode())

    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _H)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd, f"http://127.0.0.1:{httpd.server_address[1]}"


def _fake_caption(intents, sink=None):
    async def _c(goal, steps):
        if sink is not None:
            sink["goal"], sink["steps"] = goal, steps
        return intents
    return _c


async def _read_demo(page) -> None:
    await page.get_by_role("link", name="Continue").click()


async def _bland_demo(page) -> None:
    await page.get_by_role("button", name="Manage").click()


async def test_caption_relabels_step_intents(tmp_path) -> None:
    httpd, base = _serve()
    try:
        cache = FlowCache(root=tmp_path)
        spec = FlowSpec(name="r", start_url=f"{base}/read", goal="continue to done")
        sink: dict = {}
        res = await record(spec, demo=_read_demo, headless=True, cache=cache,
                           caption=_fake_caption(["open the done page"], sink))
        assert res.cached is True
        # the captioner saw the real goal + step summary (action / accessible name / text)...
        assert sink["goal"] == "continue to done"
        assert sink["steps"] == [{"action": "click", "name": "Continue", "text": None}]
        # ...and its label replaced the placeholder intent in the cached flow.
        flow = cache.get(flow_key(spec.goal, spec.start_url, spec.scope))
        assert flow.steps[0].intent == "open the done page"
    finally:
        httpd.shutdown()
        httpd.server_close()


async def test_caption_upgrades_a_client_side_write_in_a_write_flow(tmp_path) -> None:
    # A bland-named commit ("Manage") that fires NO wire write (so no attribution marker) would be classified
    # non-mutating. In a DECLARED write flow, a caption with a mutating verb ("delete the account") upgrades
    # the keyword classification — the step becomes a GATED mutating step (the spike's intent-caption backstop).
    httpd, base = _serve()
    try:
        cache = FlowCache(root=tmp_path)
        spec = FlowSpec(name="w", start_url=f"{base}/bland", goal="manage the account",
                        mutate=MutateSpec(confirm_text_contains="done"))
        res = await record(spec, demo=_bland_demo, headless=True, cache=cache,
                           caption=_fake_caption(["delete the account"]))
        assert res.is_write is True and res.cached is True
        flow = cache.get(flow_key(spec.goal, spec.start_url, spec.scope))
        step = flow.steps[0]
        assert step.action == "click" and step.intent == "delete the account"
        assert step.mutating is True and step.precond_scope   # upgraded + gated on its own scope
    finally:
        httpd.shutdown()
        httpd.server_close()


async def test_caption_does_not_reclassify_a_read_flow(tmp_path) -> None:
    # A caption that invents a mutating verb on a READ flow must NOT re-classify (else it would false-refuse a
    # benign read). The intent text is relabeled, but the step stays non-mutating and the read flow caches.
    httpd, base = _serve()
    try:
        cache = FlowCache(root=tmp_path)
        spec = FlowSpec(name="r2", start_url=f"{base}/read", goal="continue to done")
        res = await record(spec, demo=_read_demo, headless=True, cache=cache,
                           caption=_fake_caption(["submit the order"]))
        assert res.cached is True                      # NOT refused despite the mutating-keyword caption
        flow = cache.get(flow_key(spec.goal, spec.start_url, spec.scope))
        assert flow.steps[0].intent == "submit the order" and flow.steps[0].mutating is False
    finally:
        httpd.shutdown()
        httpd.server_close()


# --- caption_intents: the LLM-call helper, with a stub router (no real LLM) -----------------------------
class _StubRouter:
    def __init__(self, intents) -> None:
        self._intents = intents
        self.reqs: list = []

    async def complete(self, req, tier: str = "strong"):
        self.reqs.append((req, tier))
        return LLMResponse(blocks=[ToolUseBlock(id="t1", name="caption", input={"intents": self._intents})])


async def test_caption_intents_maps_one_per_step() -> None:
    r = _StubRouter(["click alpha", "type a note"])
    steps = [{"action": "click", "name": "alpha", "text": None},
             {"action": "type", "name": "note", "text": "hi"}]
    out = await caption_intents(r, "do the thing", steps)
    assert out == ["click alpha", "type a note"]
    req, tier = r.reqs[0]
    assert req.force_tool == "caption" and "do the thing" in req.messages[0].content[0].text


async def test_caption_intents_is_defensive() -> None:
    # count mismatch -> [] (never misalign intents to steps)
    assert await caption_intents(_StubRouter(["only one"]),
                                 "g", [{"action": "click"}, {"action": "click"}]) == []
    # empty steps -> []
    assert await caption_intents(_StubRouter(["x"]), "g", []) == []

    # any failure -> [] (caption is best-effort; an outage must never break recording)
    class _Boom:
        async def complete(self, req, tier="strong"):
            raise RuntimeError("no API key")

    assert await caption_intents(_Boom(), "g", [{"action": "click"}]) == []
