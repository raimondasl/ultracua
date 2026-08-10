"""A `mutating` mark must record WHICH SIGNAL set it — D0's lever (ii), and the primitive S18 needs.

`step.mutating` is one bit standing for four very different claims, and until this slice the codebase's
own neon sign (`safety.py`) said so: the cache stored the mark but NOT whether a keyword GUESS, the DOM
form METHOD, the WIRE, or a blanket precaution set it. Everything downstream then treats them alike — `is_write_flow`
counts them the same, `run_all` skips on them the same, MCP hides a tool on them the same — which is
why R4.27's 12/12 GraphQL reads are indistinguishable from real commits, and why D0 is blocked
indefinitely: a refusal keyed off `mutating` cannot tell a guess from evidence, so it must refuse both.

This slice records the provenance and nothing else. It changes NO gate, refuses NOTHING new, and is
deliberately inert: a strictly additive field, so the worst case is a field nobody reads. The verb that
lets a human CORRECT a mark is a separate slice with its own audit, because a human demotion is the
first change in this arc that could make write safety WORSE rather than more conservative.

THE LOAD-BEARING TEST IS THE PROPERTY, NOT THE CASES. This codebase's signature defect is a guard
applied to one path and not its sibling, and `mutating` is set at SEVEN sites across two authoring
front-ends. Enumerating the six I know of would reproduce exactly that shape one level down, so the
property — *every* freshly-marked step names its source — is what actually holds the line.
"""

from __future__ import annotations

import http.server
import threading
from pathlib import Path

from ultracua.cache import FlowCache, flow_key
from ultracua.flows import FlowSpec, _learn_once
from ultracua.providers.scripted import ScriptedProvider


class _Site:
    def __init__(self, page: str) -> None:
        self.page = page
        self.posts: list[str] = []

    def serve(self):
        site = self

        class _H(http.server.BaseHTTPRequestHandler):
            def log_message(self, *a) -> None:
                pass

            def _send(self, body: str, ctype: str = "text/html") -> None:
                self.send_response(200)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(body.encode())))
                self.end_headers()
                self.wfile.write(body.encode())

            def do_GET(self) -> None:  # noqa: N802
                self._send(site.page if self.path.split("?")[0] == "/" else "{}")

            def do_POST(self) -> None:  # noqa: N802
                self.rfile.read(int(self.headers.get("Content-Length") or 0))
                site.posts.append(self.path.split("?")[0])
                self._send("{}", "application/json")

        httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _H)
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        return httpd, f"http://127.0.0.1:{httpd.server_address[1]}"


# The keyword GUESS, alone: no form, no wire write. "Delete account" trips `MUTATING_KEYWORDS`, and
# nothing else here says anything at all — so a mark on this step is a guess and must say so.
_KEYWORD_ONLY = """<h1>Panel</h1>
<button id='d' type='button'>Delete account</button>
<script>document.getElementById('d').addEventListener('click',
  function(){ document.querySelector('h1').textContent = 'gone'; });</script>"""

# The DOM form METHOD — the one pre-act signal that is evidence rather than a guess.
_FORM_POST = """<h1>Panel</h1>
<form method='post' action='/save'><button id='s' type='submit'>Save</button></form>"""

# The WIRE: a formless POST the classifier cannot see, caught only after the request is made.
_WIRE_ONLY = """<h1>Panel</h1>
<button id='c' type='button' onclick="fetch('/api/commit',{method:'POST',body:'x=1'})">Continue</button>
<script>document.getElementById('c').addEventListener('click',
  function(){ document.querySelector('h1').textContent = 'done'; });</script>"""


def _prov(*names_and_intents) -> ScriptedProvider:
    return ScriptedProvider(
        [{"action": "click", "role": "button", "name": n, "intent": i} for n, i in names_and_intents]
        + [{"action": "done", "intent": "done"}])


async def _learn(page: str, tmp_path: Path, *names_and_intents):
    site = _Site(page)
    httpd, base = site.serve()
    cache = FlowCache(root=tmp_path / "c")
    try:
        spec = FlowSpec(name="p", goal="work the panel", start_url=f"{base}/", headless=True)
        res = await _learn_once(spec, provider=_prov(*names_and_intents), router=None,
                                cache=cache, verify_replay=False)
        return site, res, cache.get(flow_key(spec.goal, spec.start_url, spec.scope))
    finally:
        httpd.shutdown()
        httpd.server_close()


# ==================== the property ====================


async def test_every_freshly_marked_step_names_the_signal_that_marked_it(tmp_path: Path) -> None:
    """THE invariant. Not "these three fixtures record a source" — *every* mark a fresh authoring run
    produces does, whichever of the seven sites set it. A new mark site that forgets provenance fails
    here rather than being discovered a release later on the one path nobody enumerated."""
    for name, page, script in (
        ("keyword", _KEYWORD_ONLY, (("Delete account", "delete the account"),)),
        ("form_method", _FORM_POST, (("Save", "save it"),)),
        ("wire", _WIRE_ONLY, (("Continue", "continue"),)),
    ):
        _site, _res, flow = await _learn(page, tmp_path / name, *script)
        if flow is None:
            continue                        # a refusal is a legitimate outcome; it caches no marks
        marked = [(i, s) for i, s in enumerate(flow.steps) if s.mutating]
        assert marked, f"[{name}] the fixture produced no mutating step; this proves nothing"
        for i, s in marked:
            assert s.mutating_sources, (
                f"[{name}] step {i} ({s.intent!r}) is marked mutating but records NO source, so "
                f"nothing downstream can tell a keyword guess from wire evidence — which is the whole "
                f"point of the field. sources={s.mutating_sources!r}")


# ==================== the sources are the RIGHT ones, not merely present ====================


async def test_a_keyword_guess_says_it_is_a_guess(tmp_path: Path) -> None:
    """The population D0 is blocked for. A mark from `MUTATING_KEYWORDS` alone must be distinguishable
    from one backed by evidence — 28% of these are false positives on ordinary reads."""
    _site, _res, flow = await _learn(_KEYWORD_ONLY, tmp_path, ("Delete account", "delete the account"))
    assert flow is not None
    step = next(s for s in flow.steps if s.mutating)
    assert step.mutating_sources == ["keyword"], (
        f"expected a keyword-only guess, got {step.mutating_sources!r}")


async def test_a_form_method_mark_is_evidence_and_says_so(tmp_path: Path) -> None:
    """The one PRE-ACT signal that is not a guess: the form's own declared method."""
    _site, _res, flow = await _learn(_FORM_POST, tmp_path, ("Save", "save it"))
    assert flow is not None
    step = next(s for s in flow.steps if s.mutating)
    assert "form_method" in step.mutating_sources, (
        f"a POST form submit is evidence, not a guess; got {step.mutating_sources!r}")


async def test_a_wire_promotion_says_the_wire_set_it(tmp_path: Path) -> None:
    """The formless commit the classifier is blind to — and the source R4.27 needs to be visible, since
    this is also what files ordinary GraphQL reads as writes."""
    site, _res, flow = await _learn(_WIRE_ONLY, tmp_path, ("Continue", "continue"))
    assert site.posts == ["/api/commit"], "the fixture did not POST; this proves nothing"
    assert flow is not None
    step = next(s for s in flow.steps if s.mutating)
    assert "wire" in step.mutating_sources, (
        f"expected the wire promotion to record itself; got {step.mutating_sources!r}")


# ==================== additive and inert, which is the safety argument for this slice ====================


def test_the_field_is_absent_not_empty_on_a_flow_authored_before_this_slice() -> None:
    """Not retroactive: `mutating` is persisted and never recomputed, so an already-cached flow has no
    provenance and must load cleanly saying so. `None` (never recorded) and `[]` (marked, source lost)
    are DIFFERENT answers — collapsing them is this register's absent-vs-unreadable trap, three times
    filed."""
    from ultracua.cache import CachedFlow

    old = ('{"key":"k","goal":"g","start_url":"http://x/","created_ts":0,"schema_version":4,'
           '"steps":[{"intent":"pay","action":"click","mutating":true}]}')
    flow = CachedFlow.model_validate_json(old)
    assert flow.steps[0].mutating is True
    assert flow.steps[0].mutating_sources is None, (
        "a pre-slice flow must read as 'never recorded', not as 'recorded, and empty'")


def test_provenance_does_not_re_gate_an_already_approved_flow() -> None:
    """The constraint D0's text names — and it names it for the wrong reason, which is worth pinning.
    `_canon` omits any field still at its declared default, so a newly added defaulted field changes no
    existing digest whether it is hashed or not. The field is classified UNHASHED by choice (the census
    in `test_cache.py` forces that choice to be explicit); this asserts the outcome that actually
    matters: an approved flow authored before this slice keeps its digest."""
    from ultracua.cache import CachedFlow, steps_hash

    base = ('{"key":"k","goal":"g","start_url":"http://x/","created_ts":0,"schema_version":4,'
            '"steps":[{"intent":"pay","action":"click","mutating":true}]}')
    before = steps_hash(CachedFlow.model_validate_json(base))
    after = CachedFlow.model_validate_json(base)
    after.steps[0].mutating_sources = ["keyword"]
    assert steps_hash(after) == before, (
        "recording provenance must not un-approve a fleet; it is inert by construction")


# ==================== the SIBLING front-end, which is where this codebase's defects live ====================

_REC_FORM_POST = """<h1>Panel</h1>
<form method='post' action='/save'><button id='s' type='submit'>Place order</button></form>
<script>document.querySelector('form').addEventListener('submit', function(e){
  e.preventDefault(); document.querySelector('h1').textContent = 'Saved'; });</script>"""

_REC_WIRE = """<h1>Panel</h1>
<button id='c' type='button'>Place order</button>
<script>document.getElementById('c').addEventListener('click', function(){
  document.querySelector('h1').textContent = 'Saved';
  fetch('/api/commit', {method:'POST', body:'x=1'}); });</script>"""


async def test_the_recorder_records_provenance_too(tmp_path: Path) -> None:
    """`mutating` is set at SEVEN sites across TWO authoring front-ends. Asserting the property on the
    learn path alone would reproduce this register's most-repeated shape — a guard applied to one path
    and not its sibling — inside the very slice added to make that shape visible."""
    from ultracua.flows import MutateSpec, record

    for name, page in (("form_method", _REC_FORM_POST), ("wire", _REC_WIRE)):
        site = _Site(page)
        httpd, base = site.serve()
        cache = FlowCache(root=tmp_path / name)
        try:
            spec = FlowSpec(name=f"r{name}", goal="place the order", start_url=f"{base}/",
                            mutate=MutateSpec(confirm_text_contains="Saved"))

            async def _demo(pg) -> None:
                await pg.get_by_role("button", name="Place order").click()
                await pg.wait_for_timeout(500)

            res = await record(spec, demo=_demo, headless=True, cache=cache)
            flow = cache.get(flow_key(spec.goal, spec.start_url, spec.scope))
            if flow is None or not res.cached:
                continue                     # a refusal caches no marks; that is a legitimate outcome
            marked = [(i, s) for i, s in enumerate(flow.steps) if s.mutating]
            assert marked, f"[{name}] the recorded flow gated nothing; this proves nothing"
            for i, s in marked:
                assert s.mutating_sources, (
                    f"[{name}] recorded step {i} ({s.intent!r}) is marked mutating with NO source — the "
                    f"learn path records provenance and its sibling does not, which is the exact defect "
                    f"shape this slice exists to make visible")
        finally:
            httpd.shutdown()
            httpd.server_close()


def test_every_site_that_can_set_the_mark_also_records_a_source() -> None:
    """The structural guard, because the behavioural ones can only cover the sites I thought of.

    THE FIRST VERSION OF THIS TEST WAS THEATRE, and the way it failed is the reason it is worth
    writing down. It walked the AST and then did a raw SUBSTRING match for the literal text
    `"mutating": True` on each call's source segment — so it recognised exactly one syntactic shape.
    It was blind to `upd.update(mutating=True, ...)` followed by `model_copy(update=upd)`, which is
    the shape `recorder.py`'s caption site ALREADY uses. A guard written against 5 of the 6 sites it
    exists to cover, missing the one shape a future site is most likely to be copied from: this
    register's signature defect, reproduced inside the slice added to expose it.

    It now decides from the AST, at STATEMENT granularity: any statement that writes a truthy
    `mutating` — as a `CachedStep(...)`/`.update(...)` keyword, or as a `"mutating": True` dict key in
    either quote style — must mention `mutating_sources` in the same statement. Statement scope is
    deliberate: coarser than the expression (so a legitimate `if/else` pair that sets the gate in one
    branch and the sources in both still reads correctly) and far finer than the function (so a new
    forgetful site cannot hide inside a function that happens to mention the field elsewhere).
    """
    import ast

    def _writes_mark(node: ast.AST) -> bool:
        """Does this node set `mutating` to something truthy, in any of the shapes in use?"""
        if isinstance(node, ast.keyword) and node.arg == "mutating":
            return not (isinstance(node.value, ast.Constant) and not node.value.value)
        if isinstance(node, ast.Dict):
            for k, v in zip(node.keys, node.values):
                if isinstance(k, ast.Constant) and k.value == "mutating":
                    if not (isinstance(v, ast.Constant) and not v.value):
                        return True
        return False

    def _own_nodes(stmt: ast.stmt):
        """Nodes belonging to THIS statement, stopping at any nested statement's boundary.

        Attribution must be to the INNERMOST statement. A plain `ast.walk` counts every enclosing
        `if`/`with`/`for` as a site too, which both inflates the census the premise pin below reads
        and reports one forgetful line six times.
        """
        stack = list(ast.iter_child_nodes(stmt))
        while stack:
            n = stack.pop()
            if isinstance(n, ast.stmt):     # a nested statement owns its own subtree
                continue
            yield n
            stack.extend(ast.iter_child_nodes(n))

    def _mentions_source(stmt: ast.stmt) -> bool:
        return any(getattr(n, "id", None) == "mutating_sources"
                   or getattr(n, "attr", None) == "mutating_sources"
                   or getattr(n, "arg", None) == "mutating_sources"
                   or (isinstance(n, ast.Constant) and n.value == "mutating_sources")
                   for n in _own_nodes(stmt))

    offenders: list[str] = []
    sites = 0
    for mod in ("flow.py", "recorder.py"):
        path = Path("src/ultracua") / mod
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for stmt in ast.walk(tree):
            if not isinstance(stmt, ast.stmt):
                continue
            if not any(_writes_mark(n) for n in _own_nodes(stmt)):
                continue
            sites += 1
            if not _mentions_source(stmt):
                offenders.append(f"{mod}:{stmt.lineno} sets `mutating` without recording a source")

    # PIN THE PREMISE. With a relative path and a text-free matcher, the realistic failure is that this
    # scan resolves NOTHING and passes vacuously — which is how the first version would have "held"
    # even after the field was deleted outright.
    assert sites >= 6, (
        f"only {sites} mark-setting statements found across flow.py and recorder.py; the scanner is "
        f"broken or the files moved, so the assertion below means nothing")
    assert not offenders, (
        "these sites set the mark but record no provenance, so a step marked there is "
        f"indistinguishable from a keyword guess: {offenders}")


# ==================== "present" is not "correct" ====================

# A step that is BOTH a keyword hit and a demonstrable wire write. Byte-identical to `_WIRE_ONLY`
# except the button LABEL — the only thing the keyword classifier reads.
_KEYWORD_AND_WIRE = """<h1>Panel</h1>
<button id='c' type='button' onclick="fetch('/api/commit',{method:'POST',body:'x=1'})">Place order</button>
<script>document.getElementById('c').addEventListener('click',
  function(){ document.querySelector('h1').textContent = 'done'; });</script>"""


async def test_the_wire_is_recorded_even_when_a_keyword_already_marked_the_step(tmp_path: Path) -> None:
    """THE case the first draft of this slice got backwards, and which every truthiness assertion above
    passed straight through — which is the lesson: a source being PRESENT is not a source being RIGHT.

    The wire merge sat behind the guard that stops a re-gate of an already-marked step, so the evidence
    was dropped whenever a guess got there first. Measured, that made provenance track the control's
    NAME rather than the strongest signal:

        button 'Continue'    + POST  ->  ['wire']        (evidence)
        button 'Place order' + POST  ->  ['keyword']     (a 28%-FP guess)   <- the REAL commit

    So a genuine commit looked like a guess while a bland-named GraphQL read looked like evidence —
    inverting the one distinction the field exists to carry, in the direction that matters for whichever
    consumer relies on it next. `merge_marks` is a union precisely so this cannot happen.
    """
    site, _res, flow = await _learn(_KEYWORD_AND_WIRE, tmp_path, ("Place order", "place the order"))
    assert site.posts == ["/api/commit"], "the fixture did not POST; this proves nothing"
    assert flow is not None
    step = next(s for s in flow.steps if s.mutating)
    assert step.mutating_sources == ["keyword", "wire"], (
        f"a step the keyword GUESSED at and the wire CONFIRMED must carry both — dropping the evidence "
        f"files a proven commit as a guess. got {step.mutating_sources!r}")


async def test_the_recorder_also_unions_the_wire_onto_an_existing_mark(tmp_path: Path) -> None:
    """The sibling front-end, where the same guard suppressed the same evidence."""
    from ultracua.flows import MutateSpec, record

    page = """<h1>Panel</h1>
<button id='c' type='button'>Place order</button>
<script>document.getElementById('c').addEventListener('click', function(){
  document.querySelector('h1').textContent = 'Saved';
  fetch('/api/commit', {method:'POST', body:'x=1'}); });</script>"""
    site = _Site(page)
    httpd, base = site.serve()
    cache = FlowCache(root=tmp_path / "r")
    try:
        spec = FlowSpec(name="ru", goal="place the order", start_url=f"{base}/",
                        mutate=MutateSpec(confirm_text_contains="Saved"))

        async def _demo(pg) -> None:
            await pg.get_by_role("button", name="Place order").click()
            await pg.wait_for_timeout(500)

        res = await record(spec, demo=_demo, headless=True, cache=cache)
        assert site.posts == ["/api/commit"], "the demo did not POST; this proves nothing"
        flow = cache.get(flow_key(spec.goal, spec.start_url, spec.scope))
        assert flow is not None and res.cached
        step = next(s for s in flow.steps if s.mutating)
        assert "wire" in step.mutating_sources and "keyword" in step.mutating_sources, (
            f"the recorded commit is named like a write AND wrote on the wire; both must be recorded. "
            f"got {step.mutating_sources!r}")
    finally:
        httpd.shutdown()
        httpd.server_close()
