"""Inviolables #1 and #2 as PROPERTIES, not as point assertions on happy paths (S14 / H4).

Inviolable #3 has had a property file since S1. #1 and #2 had ten files asserting `llm_calls == 0`
somewhere — all on paths that were already succeeding — and nothing anywhere saying that a replay CANNOT
reach a provider, or that a failure always carries a reason. The census that found this (H4) also named
where such a gap would live: "the recovery paths (heal / suffix-replan / auth-refresh / drift ladder)".

FIRST, THE INVARIANT HAD TO BE STATED CORRECTLY, because the obvious phrasing is false. "Replay never
calls an LLM" is not what this system does: `flows.replay` takes `provider=` and `router=`, an unpinned
extraction builds a router by design, and `flow.py`'s own comment says "a pure replay makes no LLM call
and reports zeros; a HEAL or suffix-replan does". The real claim, from `replay`'s docstring, is 0-LLM
**navigation** — and the shapes it names: navigate-only reads, PINNED reads, and writes whose confirm is
selector/url/text based. A blanket "no LLM in replay" property would have gone red for correct behaviour,
which is the failure mode this file exists to avoid on other people's behalf.

WHAT THE PROVIDER STUB DOES, AND WHY IT HAS AN ALLOWLIST. `_Exploding.decide` raises, and constructing a
provider or router raises too (`build_router` / `get_provider` are patched), so an LLM is unreachable in
BOTH directions — call and construction. `.router` is allowed to read as None because `flow.py` reads it
for token accounting on every run; exploding there would fire on bookkeeping rather than on an LLM call,
and a probe that cannot tell those apart proves nothing.
"""
from __future__ import annotations

import http.server
import threading
import time
from pathlib import Path

import pytest

from ultracua.cache import CachedFlow, CachedStep, FlowCache, flow_key
from ultracua.flow import run_cached
from ultracua.locators import LocatorSpec

pytestmark = pytest.mark.asyncio


class LLMWasReached(AssertionError):
    """Raised from the stub, so a violation names itself instead of surfacing as a TypeError."""


class _Exploding:
    """A provider that cannot be used without saying so."""

    def __init__(self) -> None:
        self.router = None          # read by flow.py for token accounting; see the module docstring

    async def decide(self, *a, **kw):
        raise LLMWasReached("a replay reached provider.decide() — inviolable #1")

    def __getattr__(self, name):
        raise LLMWasReached(f"a replay touched provider.{name} — inviolable #1")


# THE DERIVATION LIVES IN ONE PLACE — `benchmarks/boundary_ledger.py` — and is imported here rather
# than kept as a second copy. Two derivations of one fact is how the fact drifts, and this fact has
# already drifted twice: S14's first fixture missed `flows.build_router` (the binding `replay()`
# actually calls) and its second missed `llm.build_client` (the factory the other two CALL), which let
# a replay construct 105 real Anthropic clients while 25 cells stayed green and a corpus cell printed
# "0 reached an LLM".
#
# The bench needs the identical set for a different purpose — it COUNTS crossings where this file
# REFUSES them — so the shared module owns the scan and each consumer owns its policy. The root
# `conftest.py` makes `benchmarks` importable from a test precisely so shared machinery can live there.
from benchmarks.boundary_ledger import FACTORIES as _FACTORIES  # noqa: E402
from benchmarks.boundary_ledger import provider_bindings as _provider_bindings  # noqa: E402


@pytest.fixture()
def no_llm(monkeypatch: pytest.MonkeyPatch):
    """Make an LLM unreachable by CONSTRUCTION as well as by call, on EVERY binding."""

    def _boom(*a, **kw):
        raise LLMWasReached(f"a replay constructed an LLM client: {a!r}")

    bindings = _provider_bindings()
    # A COUNT FLOOR, not merely non-empty. `provider_bindings` gained an `ensure_imported` default at
    # step 2.1, and a default is a thing that can move: MEASURED, flipping it to False leaves this
    # whole file at 27 passed while the bindings it blocks drop from 7 to 5 — `cli.get_provider` and
    # `daemon.server.get_provider` fall out, because a process that has not imported the CLI does not
    # have them in `sys.modules`. Truthiness cannot see that; a floor can. Raise it deliberately if
    # the real number grows, and never lower it to make a red go away.
    assert len(bindings) >= 7, (
        f"only {len(bindings)} provider bindings blocked, expected >= 7. Either a consumer module "
        f"stopped being imported before the scan (check `provider_bindings(ensure_imported=...)`) or "
        f"a binding disappeared — both make this fixture quietly cover less while every cell here "
        f"stays green, which is exactly how S14 shipped twice: {sorted(f'{m.__name__}.{a}' for m, a in bindings)}")
    for mod, attr in bindings:
        monkeypatch.setattr(mod, attr, _boom, raising=False)
    return _Exploding()


async def test_the_no_llm_fixture_actually_intercepts_every_binding(no_llm) -> None:
    """ANTI-VACUITY ON THE INSTRUMENT ITSELF — this file's cells are only worth what this fixture is.

    Two drafts of the write-safety matrix looked thorough while testing nothing, and the `no_llm` patch
    shipped inert on the `flows.py` path for several releases. A property whose stub can be bypassed is
    a green test that proves nothing, so the stub gets a test.
    """
    import ultracua.flows
    import ultracua.llm
    import ultracua.providers

    for mod, attr in _provider_bindings():
        fn = getattr(mod, attr)
        with pytest.raises(LLMWasReached):
            fn("anthropic")

    # THE LOOP ABOVE CANNOT FAIL ON ITS OWN — it patches from `_provider_bindings()` and then asserts
    # `_provider_bindings()` is patched, which is a tautology, and it is blind to any binding the scan
    # never knew about. That blindness is not hypothetical: `llm.build_client` was missing from
    # `_FACTORIES` and a replay was made to construct 105 real Anthropic clients with every cell green.
    # So the bindings that have actually leaked are named here, by hand, where a shrunken `_FACTORIES`
    # goes red instead of quietly covering less.
    for label, fn in (("flows.build_router", ultracua.flows.build_router),        # flows.py:2721
                      ("providers.build_router", ultracua.providers.build_router),
                      ("llm.build_client", ultracua.llm.build_client)):           # what both call
        try:
            fn("anthropic")
        except LLMWasReached:
            continue
        pytest.fail(f"{label} was NOT intercepted — an LLM is constructible during a replay and every "
                    f"cell in this file would still pass")


# The SDK constructors that actually open a client. Patching factory NAMES can only ever be as good as
# the list of names; this closes the class instead — if every direct SDK construction lives in a known
# leaf module, then `build_client` provably IS the choke point and patching it is sufficient.
# `Client` added at 0.4a (reshape-plan 0.5, taken early), and it closes a LIVE hole rather than a
# hypothetical one. `GenerativeModel` names the OLD google-generativeai entry point and matches nothing
# in src/ today; the adapter migrated to `genai.Client()` (llm/gemini.py:101), so gemini construction
# was outside this pin's coverage entirely — the hand-written-list failure mode this test's own
# docstring indicts, one SDK over. Verified before widening: that call is the only `Client(` in src/ and
# it sits in an allowed leaf, so the pin tightens without moving the allowlist. `GenerativeModel` stays,
# dead, so a return to the old API is still covered.
_SDK_CTORS = ("AsyncAnthropic", "AsyncOpenAI", "OpenAI", "GenerativeModel", "Client")
_SDK_ALLOWED = ("src/ultracua/llm/anthropic.py", "src/ultracua/llm/openai.py",
                "src/ultracua/llm/gemini.py", "src/ultracua/vision.py")


async def test_llm_client_construction_has_a_single_choke_point() -> None:
    """The residual the audit found, closed as a CLASS rather than by lengthening a list.

    `_FACTORIES` is a hand-written list of names, and a hand-written list is exactly what leaked:
    `build_client` was missing, so a replay could build a real `AnthropicClient` with every cell green.
    Adding the name fixes the instance. This fixes the class — no module outside the LLM leaf adapters
    may construct an SDK client directly, so all construction funnels through `llm.build_client`, which
    the fixture does patch. A new backend added tomorrow either lands in an allowed leaf or fails here.
    """
    import ast

    offenders = []
    for py in sorted(Path("src/ultracua").rglob("*.py")):
        rel = str(py).replace("\\", "/")
        if rel in _SDK_ALLOWED:
            continue
        tree = ast.parse(py.read_text(encoding="utf-8"), filename=str(py))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            fn = node.func
            nm = fn.id if isinstance(fn, ast.Name) else (fn.attr if isinstance(fn, ast.Attribute) else "")
            if nm in _SDK_CTORS:
                offenders.append(f"{rel}:{node.lineno} constructs {nm}")
    # ANTI-VACUITY: the allowlisted leaves must actually contain the constructions, or this scan is
    # looking for a pattern that does not exist and would pass however the code changed.
    found_in_leaves = 0
    for rel in _SDK_ALLOWED:
        p = Path(rel)
        if not p.exists():
            continue
        tree = ast.parse(p.read_text(encoding="utf-8"), filename=rel)
        for node in ast.walk(tree):
            fn = getattr(node, "func", None)
            nm = getattr(fn, "id", "") or getattr(fn, "attr", "")
            if isinstance(node, ast.Call) and nm in _SDK_CTORS:
                found_in_leaves += 1
    assert found_in_leaves >= 4, (
        f"only {found_in_leaves} SDK construction(s) found in the allowed leaves (>=4 since `Client` "
        f"joined _SDK_CTORS at 0.4a) — the scan is broken, "
        f"so the assertion below would pass vacuously")
    print(f"  {found_in_leaves} SDK constructions, all inside {len(_SDK_ALLOWED)} allowed leaf modules")
    assert not offenders, (
        "these construct an LLM SDK client OUTSIDE the leaf adapters, so `llm.build_client` is not the "
        "choke point and patching it does not make an LLM unreachable:\n  " + "\n  ".join(offenders))


_PAGE = ("<h1>Panel</h1>"
         "<button id='c' type='button'>Continue</button>"
         "<a href='#x' id='l'>Daily report</a>")


def _serve(page: str = _PAGE):
    class _H(http.server.BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *a) -> None:
            pass

        def do_GET(self) -> None:  # noqa: N802
            b = page.encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.send_header("Content-Length", str(len(b)))
            self.end_headers()
            self.wfile.write(b)

    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _H)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd, f"http://127.0.0.1:{httpd.server_address[1]}"


def _seed(cache: FlowCache, base: str, goal: str, *, name: str, missing: bool = False) -> str:
    key = flow_key(goal, f"{base}/", "default")
    cache.put(CachedFlow(
        key=key, goal=goal, start_url=f"{base}/", created_ts=time.time(),
        steps=[CachedStep(intent=f"click {name}", action="click",
                          locator=LocatorSpec(role="button" if not missing else "button",
                                              name=name, tag="button"))]))
    return key


# ==================== inviolable #1 ====================


@pytest.mark.parametrize("mode", ["replay", "repair"])
async def test_a_navigate_only_replay_never_reaches_a_provider(
        mode: str, tmp_path: Path, no_llm) -> None:
    """#1a. The shapes the docstring calls 0-LLM must not touch a provider even when one is HANDED to
    them. `repair` is included deliberately: it is an undocumented public mode that keeps the heal
    provider, so it is exactly where an accidental call would hide."""
    httpd, base = _serve()
    cache = FlowCache(root=tmp_path / "c")
    try:
        _seed(cache, base, "work the panel", name="Continue")
        report = await run_cached(url=f"{base}/", goal="work the panel", provider=no_llm,
                                  cache=cache, mode=mode, headless=True)
        assert report.llm_calls == 0, f"{mode}: a 0-LLM shape reported {report.llm_calls} LLM call(s)"
    finally:
        httpd.shutdown()
        httpd.server_close()


async def test_a_DRIFTED_replay_fails_loud_rather_than_reaching_for_an_llm(
        tmp_path: Path, no_llm) -> None:
    """#1b. Drift is where the recovery ladder lives, and the ladder is the one part of replay that may
    legitimately use an LLM. With `mode="replay"` it may not: it must fail, loudly, with its reason."""
    httpd, base = _serve()
    cache = FlowCache(root=tmp_path / "c")
    try:
        _seed(cache, base, "work the panel", name="Nonexistent", missing=True)
        report = await run_cached(url=f"{base}/", goal="work the panel", provider=no_llm,
                                  cache=cache, mode="replay", headless=True)
        assert report.success is False, "a step whose locator is absent must not report success"
        assert report.llm_calls == 0
    finally:
        httpd.shutdown()
        httpd.server_close()


# The documented set. `flow.run_cached`'s signature comment says "auto" | "learn" | "replay"; the body
# also accepts "repair". Anything else is a caller mistake — a typo, a stale client, a wire value.
_UNRECOGNISED = ["bogus", "REPLAY", "Replay", "replay ", "", "reply", "auto2"]


@pytest.mark.parametrize("mode", _UNRECOGNISED)
async def test_an_unrecognised_mode_is_REFUSED_and_never_silently_becomes_a_learn(
        mode: str, tmp_path: Path, no_llm) -> None:
    """THE PROPERTY THAT FOUND R4.31, and it breaks all three inviolables at once.

    `run_cached`'s control flow tests `mode in ("auto", "replay", "repair")` and then
    `mode in ("replay", "repair")`; an unrecognised string matches NEITHER and falls through to the
    learn path. Measured with a provider present — the daemon's normal state, and the daemon passes
    `params.get("mode", "auto")` straight from JSON-RPC without validating it:

        mode="bogus"   -> report.mode='learn', llm_calls=2, and the cached flow's write FIRED AGAIN
        mode="REPLAY"  -> identical. A case typo re-authors the flow and re-places the order.

    So a caller asking to replay gets: an LLM call (#1), something other than what it asked for and no
    reason given (#2), and a repeated write (#3). The refusal must be positive — an unknown mode is a
    caller error, and guessing which mode they meant is how this shipped in the first place.
    """
    httpd, base = _serve()
    cache = FlowCache(root=tmp_path / "c")
    try:
        _seed(cache, base, "work the panel", name="Continue")
        try:
            report = await run_cached(url=f"{base}/", goal="work the panel", provider=no_llm,
                                      cache=cache, mode=mode, headless=True)
        except (ValueError, LLMWasReached) as exc:
            if isinstance(exc, LLMWasReached):
                pytest.fail(f"mode={mode!r} reached an LLM: {exc}")
            assert mode in str(exc) or "mode" in str(exc).lower(), (
                f"a refusal must name the bad input; got {exc}")
            return                                  # refusing loudly is the correct outcome
        assert report.mode != "learn", (
            f"mode={mode!r} silently became a LEARN (llm_calls={report.llm_calls}). A caller that asked "
            f"for something unrecognised must be told, not re-authored — and for a write flow this "
            f"re-performs the commit (R4.31).")
        assert report.note, f"mode={mode!r} failed with an EMPTY note"
    finally:
        httpd.shutdown()
        httpd.server_close()


# ==================== inviolable #2 ====================


async def test_every_failed_report_carries_a_REASON(tmp_path: Path, no_llm) -> None:
    """#2 on the engine surface. `FlowReport.note` is the documented reason field; a caller that checks
    it on a failure currently gets `''` while the real cause sits in `traces[-1].meta['note']`
    ("locator unresolved or ambiguous (drift)"). Loud-but-unexplained is better than silent, and worse
    than the contract this project states.
    """
    httpd, base = _serve()
    cache = FlowCache(root=tmp_path / "c")
    try:
        _seed(cache, base, "work the panel", name="Nonexistent", missing=True)
        report = await run_cached(url=f"{base}/", goal="work the panel", provider=no_llm,
                                  cache=cache, mode="replay", headless=True)
        assert report.success is False, "premise: this fixture must fail, or the cell proves nothing"
        assert report.note, (
            "a failed FlowReport carries no reason. The cause exists — "
            f"traces[-1].meta={[t.meta for t in report.traces][-1:]!r} — but `report.note`, the field a "
            f"caller reads, is empty.")
    finally:
        httpd.shutdown()
        httpd.server_close()


async def test_a_replay_with_no_cached_flow_says_so(tmp_path: Path, no_llm) -> None:
    """The control for the cell above: this failure shape already carries its reason, so a fix for the
    empty note must not be 'set a note everywhere' — it must preserve the ones that are already true."""
    httpd, base = _serve()
    cache = FlowCache(root=tmp_path / "c")
    try:
        report = await run_cached(url=f"{base}/", goal="never learned", provider=no_llm,
                                  cache=cache, mode="replay", headless=True)
        assert report.success is False and report.note, "a cache miss must explain itself"
        assert "cached" in report.note.lower()
    finally:
        httpd.shutdown()
        httpd.server_close()


# ==================== inviolable #1, ACROSS EVERY REPLAY-MODE ENTRY POINT ====================
#
# What landed for S14 swept ONE bespoke fixture through ONE door (`flow.run_cached`). H4's own
# disposition asked for more: "a provider stub that raises on ANY call, threaded through every
# replay-mode entry point incl. heal and recovery ladder cells". The plan bullet asked for the drift
# corpus. Neither breadth arrived, and the bullet was never marked done, so nothing said so.
#
# This matters more than an ordinary coverage gap because of what this register keeps measuring: the
# defects here are "a guard that already exists on a sibling path and was never applied to the
# mechanism". A property that visits one door is a per-path assertion wearing a property's clothes.


def _seed_for(cache: FlowCache, spec, *, name: str = "Continue", mutating: bool = False) -> str:
    """A NAVIGATE-ONLY recipe for `spec` — no `extract`, so it is one of the shapes `replay`'s docstring
    calls 0-LLM. Seeding directly (rather than learning) keeps the cell about the door, not about a
    learn run that would legitimately use an LLM."""
    key = flow_key(spec.goal, spec.start_url, spec.scope)
    cache.put(CachedFlow(
        key=key, goal=spec.goal, start_url=spec.start_url, created_ts=time.time(),
        steps=[CachedStep(intent=f"click {name}", action="click", mutating=mutating,
                          locator=LocatorSpec(role="button", name=name, tag="button"))]))
    return key


async def _door_replay(spec, cache):
    from ultracua.flows import replay

    return await replay(spec, cache=cache)


async def _door_run_batch(spec, cache):
    from ultracua.flows import run_batch

    run = await run_batch(spec, [{}], require_approved=False, cache=cache)
    # `run_batch` CATCHES per-row exceptions into `BatchRowResult`, so an LLMWasReached raised inside a
    # row would never propagate and this door could not fail for the thing it exists to check. Measured
    # by the pre-merge audit: replay and run_all propagate, run_batch swallows. Re-raise it.
    for row in getattr(run, "rows", []) or []:
        err = str(getattr(row, "error", "") or "")
        if "inviolable #1" in err or "LLMWasReached" in err:
            raise LLMWasReached(f"run_batch swallowed an LLM violation into a row result: {err}")
    return run


async def _door_run_all(spec, cache):
    from ultracua.flows import run_all

    return await run_all(names=[spec.name], approved_only=False, cache=cache, allow_empty=True)


async def _door_canary(spec, cache):
    from ultracua.flows import canary

    return await canary(spec, cache=cache)


async def _door_canary_all(spec, cache):
    from ultracua.flows import canary_all

    return await canary_all(names=[spec.name], cache=cache, allow_empty=True)


async def _door_preflight_keys(spec, cache):
    from ultracua.flows import preflight_keys

    return preflight_keys(spec, None, cache=cache, require_approved=False)


async def _door_run_cached(spec, cache):
    from ultracua.cache import flow_key as _fk

    _ = _fk  # the key is derived by run_cached from (goal, url, scope) — kept aligned with _seed_for
    return await run_cached(url=spec.start_url, goal=spec.goal, provider=None, cache=cache,
                            mode="replay", headless=True, scope=spec.scope)


# Every public door onto a cached recipe. HOW MUCH EACH ONE ACTUALLY REPLAYS DIFFERS, and the pre-merge
# audit measured it rather than letting the list imply parity — `run_cached` calls per door:
#
#   replay 1 | run_batch 1 | run_all 1 | run_cached 1        <- these genuinely replay
#   canary 0 | canary_all 0                                  <- opens a browser, resolves ONE locator
#   preflight_keys 0                                         <- sync, browserless, no page at all
#
# The three that do not replay are kept deliberately: they are public doors onto a cached recipe and a
# future change could give them one, at which point this sweep is already watching. But the property's
# enforcing power comes from the first four, and saying otherwise would overstate it.
#
# `run_cached` is listed EXPLICITLY because the coverage guard below cannot see it: it is re-exported
# into `flows` (`flows.py:41`) so its `__module__` is `ultracua.flow`, and it is the raw engine surface
# `daemon/server.py` drives — documented as deliberately bypassing everything `flows.replay` enforces.
#
# `audit_flows` is DELIBERATELY absent and that is not an oversight: it is the H9 judge, its whole job
# is to ask an LLM, and it "opens NO browser, never calls `replay`, and is never reached from the replay
# path" (its docstring). Listing it would assert the opposite of its contract. `learn`/`record` are
# absent for the same reason in the other direction — they are the paths that MAY use an LLM.
_DOORS = [
    ("flows.replay", _door_replay),
    ("flows.run_batch", _door_run_batch),
    ("flows.run_all", _door_run_all),
    ("flow.run_cached", _door_run_cached),
    ("flows.canary", _door_canary),
    ("flows.canary_all", _door_canary_all),
    ("flows.preflight_keys", _door_preflight_keys),
]


@pytest.mark.parametrize("label,door", _DOORS, ids=[d[0] for d in _DOORS])
async def test_no_replay_entry_point_can_reach_an_llm(
    label, door, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, no_llm,
) -> None:
    """#1c. THE SIBLING PROPERTY. Every public door onto a cached recipe, driven on a shape whose own
    docstring calls it 0-LLM, with a provider that raises on any call AND every construction binding
    patched. A door that builds a router or calls `decide` fails here by name.

    The outcome of the run is deliberately NOT asserted — drift, refusal and success are all legitimate
    depending on the door. The only claim is that no path reached an LLM to decide it.
    """
    from ultracua.flows import FlowSpec, save_spec

    monkeypatch.setenv("ULTRACUA_HOME", str(tmp_path / "home"))
    httpd, base = _serve()
    cache = FlowCache(root=tmp_path / "c")
    try:
        spec = FlowSpec(name="panel", start_url=f"{base}/", goal="work the panel", headless=True)
        save_spec(spec)
        _seed_for(cache, spec)
        try:
            out = await door(spec, cache)
            # PRINT what the cell exercised. A door that refuses at pre-flight never reaches the replay
            # path and would satisfy "no LLM" vacuously; this is what makes that visible rather than
            # inferred. Two drafts of the write-safety matrix looked thorough while testing nothing.
            print(f"  {label:24s} -> returned {type(out).__name__}: {str(out)[:90]}")
        except LLMWasReached:
            raise
        except TypeError as exc:
            # A HARNESS bug, not a refusal — and it would otherwise pass as one. Caught when the
            # `run_cached` door was first added with a bad kwarg: the door raised TypeError, exercised
            # nothing, and the cell went green. "Any exception is an acceptable outcome" is too wide.
            pytest.fail(f"{label} was called incorrectly, so this cell exercised nothing: {exc}")
        except Exception as exc:  # noqa: BLE001 — a loud refusal is a fine outcome; an LLM call is not
            assert not isinstance(exc, LLMWasReached)
            assert str(exc), f"{label} failed with an EMPTY message (inviolable #2)"
            print(f"  {label:24s} -> refused {type(exc).__name__}: {str(exc)[:90]}")
    finally:
        httpd.shutdown()
        httpd.server_close()


async def test_the_door_list_covers_every_public_replay_verb(tmp_path: Path) -> None:
    """ANTI-VACUITY, and the thing that makes the sweep above a property rather than six tests: a verb
    added to `flows.py` tomorrow must either appear in `_DOORS` or be named in the exemption list with
    a reason. Otherwise the next door onto a cached recipe is simply not visited and nothing says so.
    """
    import inspect

    import ultracua.flows as flows_mod

    # Verbs that legitimately MAY use an LLM, each with the reason it is exempt.
    exempt = {
        "learn": "authoring — the LLM is the point",
        "record": "authoring from a human demo; may caption via LLM",
        "audit_flows": "the H9 judge; opens no browser and never calls replay",
        "refresh_auth": "re-login only; drives no cached recipe",
        "serve_mcp": "a server loop, not a replay verb",
        "dry_run": "covered by its own cell below, which needs a write spec",
    }
    # NOTE the limit of this guard, stated because the audit found it: the `__module__` filter below
    # skips names RE-EXPORTED into `flows` (e.g. `run_cached`, whose `__module__` is `ultracua.flow`).
    # Such a door can only be covered by being listed in `_DOORS` explicitly, which `run_cached` now is.
    covered = {label.split(".", 1)[1] for label, _ in _DOORS}
    missing, considered = [], []
    for name, fn in vars(flows_mod).items():
        if name.startswith("_") or not inspect.iscoroutinefunction(fn):
            continue
        if getattr(fn, "__module__", "") != flows_mod.__name__:
            continue
        considered.append(name)
        if name in covered or name in exempt:
            continue
        missing.append(name)
    print(f"  public async verbs in flows.py: {sorted(considered)}")
    # ANTI-VACUITY on the scan itself: if the filter matched nothing, `missing` is trivially empty and
    # this test would pass while covering no door at all.
    assert len(considered) >= len(_DOORS), (
        f"the scan found only {considered} — the module/coroutine filter is broken, so the assertion "
        f"below would pass vacuously")
    assert not missing, (
        f"these public async verbs in flows.py are neither swept by _DOORS nor exempted with a "
        f"reason: {sorted(missing)}. A door onto a cached recipe that nothing visits is exactly the "
        f"sibling-path gap this file exists to close.")


async def test_dry_run_never_reaches_an_llm(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, no_llm) -> None:
    """#1c, the WRITE door. `dry_run` replays a write flow with the arbiter armed; a write whose confirm
    is text-based is one of the 0-LLM shapes, so nothing here may construct a provider either.

    THE FIRST DRAFT OF THIS CELL WAS VACUOUS AND GREEN, which the pre-merge audit measured: the seeded
    step lacked `mutating=True`, so `dry_run` aborted at `_preflight_row` with `unkeyed_write` ("declared
    a WRITE, but no recorded step is classified as mutating") and never opened a browser. Its only
    assertion was `rep is not None`, which an aborted report satisfies. That is the same
    green-while-exercising-nothing shape this slice credits itself with finding in the auth-refresh cell
    — twice in one slice, which is why the premise is now COUNTED rather than assumed.
    """
    import ultracua.flows as flows_mod
    from ultracua.flows import FlowSpec, MutateSpec, dry_run, save_spec

    monkeypatch.setenv("ULTRACUA_HOME", str(tmp_path / "home"))
    seen: dict = {"replays": 0}
    # Patch the binding `flows.py` HOLDS (`from .flow import run_cached`, line 41) — not
    # `ultracua.flow.run_cached`. Patching the source module leaves this one untouched, which is the
    # very defect this slice fixed for `build_router`; the counter caught me repeating it here.
    real = flows_mod.run_cached

    async def _counting(*a, **kw):
        seen["replays"] += 1
        return await real(*a, **kw)

    monkeypatch.setattr(flows_mod, "run_cached", _counting)

    httpd, base = _serve()
    cache = FlowCache(root=tmp_path / "c")
    try:
        spec = FlowSpec(name="panelw", start_url=f"{base}/", goal="work the panel", headless=True,
                        mutate=MutateSpec(confirm_text_contains="Panel"))
        save_spec(spec)
        _seed_for(cache, spec, mutating=True)      # without this the flow is refused before it replays
        rep = await dry_run(spec, cache=cache)
        assert rep is not None
        assert rep.aborted != "unkeyed_write", (
            f"premise lost: dry_run refused before replaying ({rep.aborted}: {rep.abort_detail})")
    finally:
        httpd.shutdown()
        httpd.server_close()
    assert seen["replays"] >= 1, (
        "premise lost: `dry_run` never reached `run_cached`, so this cell exercised no replay at all "
        "and its 'no LLM' result is vacuous")


# ==================== inviolable #1, ACROSS THE DRIFT CORPUS ====================


async def test_no_corpus_row_can_reach_an_llm_in_replay_mode(capsys, no_llm) -> None:
    """#1d. The plan bullet's explicit ask, and the half that reaches the RECOVERY LADDER.

    The corpus is the only place in this repo where drift is produced systematically — and drift is
    what invokes the ladder (heal / suffix-replan / the drift gate), which H4 named as exactly where a
    stray provider call would hide. Every other cell in this file drives a hand-made fixture.

    **WHAT THIS PROVES, CORRECTED AFTER THE PRE-MERGE AUDIT REFUTED THE FIRST VERSION.** The first
    draft argued it beat `drift_bench` by handing the engine a provider that RAISES where the bench
    passes `provider=None`. That is false, and the audit measured it: `flow.py:171` is
    `heal_provider = provider if mode in ("auto", "repair") else None`, so in `mode="replay"` the
    engine receives None whatever the caller passed. Reproduced directly —

        mode=replay   -> _replay received provider of type ['NoneType']
        mode=repair   -> _replay received provider of type ['_Exploding']

    — so the raising stub was inert here and the cell was provider-identical to the bench, which is
    exactly the tautology its own docstring claimed to escape.

    So the claim is restated to what the sweep actually establishes, over 97 drifting rows:
      1. no path CONSTRUCTS an LLM client — `no_llm` is now requested (the first draft did not take
         the fixture at all, so construction was unpatched too), and `_FACTORIES` now includes
         `build_client`, without which a real `AnthropicClient` could be built with every cell green;
      2. `flow.py:171`'s nulling actually holds for every row, asserted directly below rather than
         assumed — that structural fact is what makes replay 0-LLM, so it is the thing worth pinning;
      3. `report.llm_calls` stays 0 across the corpus.

    ONLY the `replay` arm is swept. `repair` may legitimately call an LLM on a drifted row; asserting
    otherwise would go red for correct behaviour, the failure mode this file's docstring warns about.
    """
    import shutil
    from tempfile import TemporaryDirectory

    from benchmarks.drift_bench import build_corpus
    from benchmarks.drift_fixtures import SCENARIOS, SCENARIOS_BY_NAME, FixtureServer
    from ultracua.browser import BrowserSession
    from ultracua.providers.scripted import ScriptedProvider

    # per_k=1 rather than the bench's 3. STATED, because a silent cap reads as full coverage: this drops
    # only GENERATED COSMETIC compositions (144 -> 56) and keeps every scenario and every non-cosmetic
    # row — all 20 semantic, both conflict, escalate, both residual-hole, ambiguous, route, and all 14
    # calibration. The dropped kind is the one that survives at 0-LLM and never reaches the ladder.
    rows = build_corpus(7, per_k=1)
    srv = FixtureServer().start()
    reached: list = []
    ran = 0

    # Pin the STRUCTURAL fact this property actually rests on (see the docstring): in mode="replay"
    # the engine is handed no provider at all. If that ever changes, every row below silently starts
    # relying on the stub instead — and the stub is the part that was measured inert.
    import ultracua.flow as _flow_mod
    real_replay = _flow_mod._replay
    engine_saw: list = []

    async def _spy(url, **kw):
        # KEYWORD-ONLY since step 1.1, and this stub is better for it: it used to name the first five
        # parameters POSITIONALLY, so it silently depended on their order as well as on their names.
        engine_saw.append(type(kw.get("provider")).__name__)
        return await real_replay(url, **kw)

    _flow_mod._replay = _spy
    try:
        shared = await BrowserSession(headless=True).start()
        try:
            with TemporaryDirectory() as td:
                golden = Path(td) / "golden"
                learned: dict = {}
                for sc in SCENARIOS:
                    srv.set_mutation("", "")
                    srv.reset_orders()
                    root = golden / sc["name"]
                    rep = await run_cached(
                        srv.base + sc["path"], sc["goal"], ScriptedProvider(list(sc["steps"])),
                        FlowCache(root=root), mode="learn", headless=True, browser=shared.browser)
                    # PREMISE: an unlearned scenario would make every one of its rows a no-op that
                    # trivially reaches no LLM.
                    assert rep.success, f"{sc['name']}: pristine learn failed — its rows would prove nothing"
                    learned[sc["name"]] = root

                engine_saw.clear()          # learn's verify-by-replay is not what is being pinned
                for i, row in enumerate(rows):
                    sc = SCENARIOS_BY_NAME[row["scenario"]]
                    if sc["name"] not in learned:
                        continue
                    srv.reset_orders()
                    srv.set_mutation(row["js"], row.get("target_sel_override") or sc["target_sel"])
                    root = Path(td) / f"r{i}"
                    shutil.copytree(learned[sc["name"]], root)
                    try:
                        rep = await run_cached(
                            srv.base + sc["path"], sc["goal"], _Exploding(), FlowCache(root=root),
                            mode="replay", headless=True, browser=shared.browser)
                    except LLMWasReached as exc:
                        reached.append((row["row_id"], str(exc)))
                        continue
                    ran += 1
                    if rep.llm_calls:
                        reached.append((row["row_id"], f"llm_calls={rep.llm_calls}"))
        finally:
            await shared.close()
    finally:
        srv.stop()
        _flow_mod._replay = real_replay

    with capsys.disabled():
        print(f"\n  swept {ran}/{len(rows)} corpus rows in mode=replay; {len(reached)} reached an LLM; "
              f"engine was handed {sorted(set(engine_saw))} (construction blocked on "
              f"{len(_provider_bindings())} bindings)")
    # ORDER MATTERS, and the first draft got it wrong: with `ran >= 90` first, a real violation reported
    # "only 62 rows actually ran" and never named the rows that reached an LLM. The finding goes first.
    assert not reached, (
        "these corpus rows reached an LLM during a mode='replay' run — inviolable #1:\n  " +
        "\n  ".join(f"{rid}: {why}" for rid, why in reached))
    assert ran >= 90, f"only {ran} rows actually ran — the sweep is not covering the corpus"
    # The structural fact (see the docstring): replay hands the engine NO provider. If this flips, the
    # sweep silently starts depending on the stub, which was measured inert on this path.
    assert set(engine_saw) == {"NoneType"}, (
        f"mode='replay' handed the engine {sorted(set(engine_saw))} — `flow.py:171` used to null it, "
        f"and every 0-LLM guarantee on this path rests on that")


# ==================== inviolable #2, ACROSS EVERY FAILURE SHAPE ====================
#
# What landed for S14 asserted #2 on TWO hand-picked failures. The plan bullet asked for "every failure
# shape in the API map, a typed error or nonempty note — never a bare False/log-only". The two
# properties below derive their subjects instead of listing them, so a failure shape added tomorrow is
# covered the day it is written — the same move `test_register_count.py` makes for the register.


def _failure_report_sites() -> list:
    """Every `FlowReport(...)` constructed with `success=False`, via AST — `[(file, line, has_note)]`.

    AST rather than a regex, deliberately: S2's enforcement test was rebuilt this way after the regex
    version "was shown to be theatre". A regex for `success=False` also matches comments and prose, of
    which this codebase has a great deal on exactly this subject.

    KNOWN EVASIONS, stated rather than implied away — the scan matches a literal `FlowReport(...)` call
    with a constant `success=False`, so it would miss an aliased import, `FlowReport(**kw)`, a helper
    factory, `dataclasses.replace`, a subclass, a non-constant `success=`, or a post-hoc
    `rep.success = False`. The pre-merge audit verified that NONE of those shapes exists in `src/`
    today, so the scan is complete against the current tree and would need extending the day one
    appears. It is a floor, not a proof.
    """
    import ast

    out = []
    for py in sorted(Path("src/ultracua").rglob("*.py")):
        tree = ast.parse(py.read_text(encoding="utf-8"), filename=str(py))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            fn = node.func
            if not (isinstance(fn, ast.Name) and fn.id == "FlowReport"):
                continue
            kw = {k.arg: k.value for k in node.keywords if k.arg}
            success = kw.get("success")
            if not (isinstance(success, ast.Constant) and success.value is False):
                continue
            note = kw.get("note")
            has_note = note is not None and not (
                isinstance(note, ast.Constant) and not str(note.value or "").strip())
            out.append((str(py).replace("\\", "/"), node.lineno, has_note))
    return out


async def test_no_failure_report_is_constructed_without_a_reason() -> None:
    """#2a. `note` is the field a caller reads (`cli.py:75`: "a bare success=False with no reason is
    exactly what this slice exists to stop"). A construction site that omits it ships a failure the
    caller cannot explain — loud but unexplained, which this project states is not its contract.
    """
    sites = _failure_report_sites()
    # ANTI-VACUITY: this file's own history is a patch that looked thorough while matching nothing.
    assert len(sites) >= 5, (
        f"the AST scan found only {len(sites)} failure-report construction(s) — it is broken, so the "
        f"assertion below would pass vacuously")
    print(f"  scanned {len(sites)} FlowReport(success=False) construction sites")
    bare = [f"{f}:{ln}" for f, ln, has_note in sites if not has_note]
    assert not bare, (
        "these construct a FAILED FlowReport with no `note`, so a caller gets `success=False` and no "
        f"reason at all:\n  " + "\n  ".join(bare))


def _typed_errors() -> list:
    """Every concrete `FlowReplayError` subclass DEFINED IN `ultracua`, derived by walking the tree.

    THE PACKAGE FILTER IS LOAD-BEARING, and it was added because the 1.4a audit made this cell red.
    `__subclasses__()` is a live view maintained by the interpreter: a class defined anywhere — a test
    probe, a downstream user's own subclass — appears in it, and CPython registers a class in its
    bases' subclass lists BEFORE `__init_subclass__` runs, so even a class the hook REFUSED is in
    there. `tests/test_refusal_codes.py` defines eight such probes inside `pytest.raises`, and the
    derived-equality assertion below then reported `walk-only=['_Probe', '_ProbeOk']` whenever that
    file ran first. Measured: green in file order, red in the reverse — a latent flake that
    `pytest-randomly`, a different shard split, or any hand-run subset would surface.

    THE MECHANISM, corrected — the first version of this note named the wrong one, and it matters
    because it would have licensed the wrong fix. It said the ghosts survive an explicit
    `gc.collect()`. They do not: in a plain interpreter the count goes 28 -> 31 -> 28 across a
    `gc.collect()`. What actually holds them is `pytest.raises`' `ExceptionInfo` and the traceback it
    carries, which reference the half-built classes for the LIFETIME OF THE TEST ITEM — which is why a
    narrow selection fails deterministically while running both whole files passes, enough intervening
    work having dropped the references. So "call `gc.collect()` in a fixture" would have been an
    unreliable patch that appeared to work.

    Filtering on the module is right regardless of the mechanism, which is the point: it does not
    depend on WHEN a ghost is collected. And the property being asserted is about the SHIPPED taxonomy
    — a downstream user subclassing `FlowReplayError` in their own package must not be able to fail our
    invariant test.
    """
    import ultracua.flows  # noqa: F401 — ensure the subclasses are imported before walking
    from ultracua.flows import FlowReplayError

    seen, stack = [], [FlowReplayError]
    while stack:
        cls = stack.pop()
        for sub in cls.__subclasses__():
            if sub not in seen:
                seen.append(sub)
                stack.append(sub)
    ours = [c for c in seen if c.__module__.split(".")[0] == "ultracua"]
    assert ours, "the package filter removed the entire taxonomy — it is wrong, not the tree empty"
    return ours


async def test_every_typed_error_carries_a_DISTINCT_machine_readable_code() -> None:
    """#2b. The taxonomy's whole promise is that a caller can "react to a failure by KIND without
    string-parsing the message" (`FlowReplayError`'s docstring), and the MCP write surface does exactly
    that. Two classes sharing a `code` silently collapse two remedies into one — the R3.3 finding
    ("the two questions are DIFFERENT — collapsing them was the sixth pass's finding") in the error
    taxonomy rather than in the ledger.
    """
    from ultracua.flows import REGISTRY, RESERVED_CODES

    errs = _typed_errors()
    # DERIVED EQUALITY, not a typed floor. This said `>= 10` while there were twelve and, after 1.4,
    # twenty-seven — a number that only ever gets further from the truth, and a walk that silently lost
    # half the family would still have cleared it. `REGISTRY` is populated by `__init_subclass__`, so
    # the two derivations are independent: the class-creation hook and a `__subclasses__` walk have to
    # agree, and a class defined but never registered (or registered and then removed) is red here.
    assert {c.__name__ for c in errs} == {c.__name__ for c in REGISTRY.values()}, (
        f"the subclass walk and REGISTRY disagree about the taxonomy: "
        f"walk-only={sorted({c.__name__ for c in errs} - {c.__name__ for c in REGISTRY.values()})}, "
        f"registry-only={sorted({c.__name__ for c in REGISTRY.values()} - {c.__name__ for c in errs})}")
    assert len(errs) >= 28, (
        f"only {len(errs)} typed errors found — the walk is broken, not the taxonomy small: {errs}")

    by_code: dict = {}
    for cls in errs:
        code = getattr(cls, "code", None)
        assert isinstance(code, str) and code.strip(), f"{cls.__name__} has no machine-readable code"
        assert isinstance(getattr(cls, "retryable", None), bool), (
            f"{cls.__name__}.retryable must be a bool — a caller branches on it")
        assert isinstance(getattr(cls, "landed", None), bool), (
            f"{cls.__name__}.landed must be a bool — it arms the retry-dedupe ledger")
        assert isinstance(getattr(cls, "can_follow_actuation", None), bool), (
            f"{cls.__name__}.can_follow_actuation must be a bool — it is what licenses the other two")
        by_code.setdefault(code, []).append(cls.__name__)
    print(f"  {len(errs)} typed errors, codes: {sorted(by_code)}")
    dupes = {c: names for c, names in by_code.items() if len(names) > 1}
    assert not dupes, (
        f"these typed errors share a `code`, so a caller cannot tell them apart by kind: {dupes}")

    # ...and no code may collide with a vocabulary that shares a FIELD with this one. `ToolOutcome.code`
    # carries five codes `mcpserver` mints itself and `SkippedFlow.code` eight; B3 buckets on those
    # fields, so one slug meaning two things is R3.7's overloaded token in a code space.
    collisions = sorted(set(by_code) & RESERVED_CODES)
    assert not collisions, (
        f"{collisions} are taxonomy codes AND reserved for another vocabulary on the same field")


async def test_no_typed_error_is_both_retryable_and_post_actuation() -> None:
    """#2c. INVIOLABLE #3, as a property of the taxonomy rather than of a call path.

    `retryable=True` tells an autonomous agent to re-run. If the class can be raised from a position
    where this run's write may already have committed, that instruction double-submits — which is not
    hypothetical: R4.18's fix shipped `MetaUnwritableError.retryable = True`, copied from its PRE-write
    twin without regard to position, and an MCP agent honouring it re-fired a commit that had actuated.

    Since 1.4 the rule is enforced at CLASS CREATION (`__init_subclass__`), so this cell is the
    behavioural witness for it rather than the enforcement. Both are wanted: the hook cannot fire for a
    class whose flags are mutated after definition, and this can.
    """
    from ultracua.flows import REGISTRY

    retryable = sorted(c.__name__ for c in REGISTRY.values() if c.retryable)
    post_act = sorted(c.__name__ for c in REGISTRY.values() if c.can_follow_actuation)
    print(f"  retryable: {retryable}")
    print(f"  can follow an actuation: {post_act}")
    assert post_act, "no class can follow an actuation — the axis has been cleared wholesale"

    # THE STATE OF `retryable` AFTER 1.4a, STATED RATHER THAN DISCOVERED. `AuthExpiredError` is the
    # only retryable class left and it is DEFINED BUT NEVER RAISED in `src/` — the heuristic auth path
    # cannot attribute a drift to expiry confidently, so it raises `DriftError`. `MetaUnreadableError`
    # was the last raisable one and 1.4a's audit showed its flag was the R4.18 mistake on the read
    # twin, one release later.
    #
    # So: EVERY refusal a caller can actually receive today is non-retryable. That is a fact B3 has to
    # be told, because a bucket keyed on a flag that never varies is a bucket that measures nothing —
    # and it is a fact that should CHANGE, by splitting the pre-write and post-write halves of
    # `meta_unreadable` rather than by relaxing a flag. Asserted here so the day it changes is loud.
    assert retryable == ["AuthExpiredError"], (
        f"the retryable set moved to {retryable}. If a class became retryable, prove it is raised only "
        f"from positions where nothing can have actuated — that is what `can_follow_actuation` is for, "
        f"and getting it wrong double-submits (R4.18, and again on the read twin at 1.4a).")
    raise_sites = (Path(__file__).parents[1] / "src" / "ultracua" / "flows.py").read_text(
        encoding="utf-8")
    assert "raise AuthExpiredError(" not in raise_sites, (
        "AuthExpiredError is now RAISED, so `retryable=True` is reachable for the first time. Re-check "
        "its `can_follow_actuation=False` against the new raise site before believing this cell.")

    bad = sorted(c.__name__ for c in REGISTRY.values() if c.retryable and c.can_follow_actuation)
    assert not bad, (
        f"{bad} tell an autonomous agent to re-run a flow that may already have committed. Direction "
        f"of error decides this: a missed auto-retry costs one manual re-run; a wrong one "
        f"double-submits (inviolable #3).")
    # A `landed=True` claim needs the same licence: a class that cannot follow an actuation cannot have
    # observed a landed write, and a false arm SKIPS a row that was never paid.
    liars = sorted(c.__name__ for c in REGISTRY.values() if c.landed and not c.can_follow_actuation)
    assert not liars, f"{liars} claim a landed write from a position where none could have fired"


async def test_the_AUTH_REFRESH_retry_never_reaches_an_llm(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, no_llm,
) -> None:
    """#1e. H4 named the recovery paths — "heal / suffix-replan / auth-refresh / drift ladder" — as
    where a stray provider call would hide, and auth-refresh is the one none of the other cells reach:
    it fires only on DRIFT, only when `spec.login` is set, and it RETRIES the whole replay. A retry is
    a second pass through the shape logic, which is exactly the sort of place a provider gets built the
    second time and not the first.

    The flow drifts by construction (its locator names a control the page does not have), so the
    refresh path is entered, `_form_login` runs against a page with no login form, and the replay
    fails. Every one of those steps must happen without an LLM.
    """
    import ultracua.flows as flows_mod
    from ultracua.flows import FlowReplayError, FlowSpec, LoginSpec, replay, save_spec

    monkeypatch.setenv("ULTRACUA_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("ULTRACUA_USERNAME", "u")
    monkeypatch.setenv("ULTRACUA_PASSWORD", "p")

    # PIN THE PREMISE. A DriftError is also what you get when the refresh path is never entered at all,
    # so the error type alone does not prove this cell exercised auth-refresh. Count the actual call.
    seen: dict = {"logins": 0}
    real_login = flows_mod._form_login

    async def _counting_login(page, login):
        seen["logins"] += 1
        return await real_login(page, login)

    monkeypatch.setattr(flows_mod, "_form_login", _counting_login)
    httpd, base = _serve()
    cache = FlowCache(root=tmp_path / "c")
    try:
        # `storage_state` is REQUIRED for the refresh to run at all — without it the path refuses
        # early ("set `storage_state` (a path) so refreshed cookies can be saved") and `_form_login`
        # is never reached. The first draft of this cell omitted it, passed, and exercised nothing;
        # the premise counter below is what caught that.
        state = tmp_path / "state.json"
        state.write_text('{"cookies": [], "origins": []}', encoding="utf-8")  # Playwright LOADS it
        spec = FlowSpec(name="panelauth", start_url=f"{base}/", goal="work the panel", headless=True,
                        storage_state=str(state), login=LoginSpec(url=f"{base}/"))
        save_spec(spec)
        _seed_for(cache, spec, name="Nonexistent")     # guarantees drift -> the refresh path
        try:
            await replay(spec, cache=cache, auth_refresh=True)
        except LLMWasReached:
            raise
        except FlowReplayError as exc:
            # PREMISE: this cell is only meaningful if the run actually FAILED its way into the
            # recovery path. A success here would mean the fixture stopped drifting.
            assert str(exc), "a drift failure must carry its reason (inviolable #2)"
            print(f"  auth-refresh ran {seen['logins']}x, then failed loudly: "
                  f"{type(exc).__name__}: {str(exc)[:70]}")
        else:
            pytest.fail("premise lost: the seeded locator was expected to drift, so no refresh ran")
    finally:
        httpd.shutdown()
        httpd.server_close()
    assert seen["logins"] >= 1, (
        "premise lost: `_form_login` was never called, so this cell never entered the auth-refresh "
        "path and its 'no LLM' result is vacuous")
