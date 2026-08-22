"""reshape-plan step 1.7 — what each DOOR into the engine permits, printed and committed.

A "door" is a place that calls `flow.run_cached`. There are eight, and they do NOT permit the same
things — some go through `flows.replay()`'s safety lifecycle (approval gate, write gate, idempotency
keys, shape drift) and some reach the engine raw. That difference has always been true and has never
been written down anywhere a reader could check it, so it was discovered one door at a time.

THE COLUMN THIS EXISTS FOR IS `may_reauthor_a_write`. In `mode="auto"`, a replay that fails
irrecoverably falls through to a full re-author — and re-authoring a write flow PERFORMS THE WRITE
AGAIN. Three doors can reach that, and they are exactly the three with no flow-level gates. It is a
decision somebody made, not a refactor side effect, and this table is where it is visible.

WHAT IS DERIVED AND WHAT IS TYPED. The door SET is derived from the source, so a ninth door added
tomorrow fails here rather than being quietly absent. `may_reauthor_a_write` is DERIVED from each
door's mode set, never typed — it is the consequence, and a consequence you type by hand is a
consequence that can disagree with its own premise. Each row's `modes` is typed and then
cross-checked against whatever constrains it in the source: an argparse `choices=`, the engine's own
`MODES`, or the literal at the call site.
"""

from __future__ import annotations

import ast
import inspect
from dataclasses import dataclass

import pytest

import ultracua.cli as cli_mod
import ultracua.daemon.server as daemon_mod
import ultracua.flow as flow_mod
import ultracua.flows as flows_mod
import ultracua.parallel as parallel_mod
from ultracua.flow import MODES
from ultracua.providers import PROVIDERS

DOOR_MODULES = (cli_mod, daemon_mod, flows_mod, parallel_mod)

# The modes that reach a re-author. `learn` goes there directly; `auto` falls through to it when a
# cached replay fails irrecoverably (`flow.py`'s "auto-mode replay failed irrecoverably -> fall
# through to a fresh learn run"). `replay` and `repair` return the failure instead.
REAUTHORING_MODES = frozenset({"auto", "learn"})

# A door whose mode is not constrained at all: it forwards whatever its caller put in a dict.
ANY_MODE = "<caller's choice>"


@dataclass(frozen=True)
class Policy:
    """What ONE door permits. `may_reauthor_a_write` is derived, not stored — see `reauthors`."""

    door: str
    site: str                 # module::enclosing function
    modes: frozenset          # the run_cached modes reachable through this door
    llm: str                  # "caller" | "fixed" | "never"
    flow_gates: bool          # approval + write gate + idempotency + shape drift?
    why: str

    @property
    def reauthors(self) -> bool:
        """DERIVED. Can a run through this door re-perform a write by re-authoring the flow?"""
        return self.modes == ANY_MODE or bool(self.modes & REAUTHORING_MODES)


def _p(door, site, modes, llm, flow_gates, why):
    return Policy(door, site, modes if modes == ANY_MODE else frozenset(modes), llm,
                  flow_gates, why)


POLICY = {p.door: p for p in [
    # ---- THE RAW DOORS. No approval gate, no write gate, no idempotency key. --------------------
    _p("cli_root", "cli.py::_amain", ("auto", "learn", "replay"), "caller", False,
       "`ultracua <url> <goal>` — the raw engine on the command line. No FlowSpec exists, so there "
       "is nothing to approve and no declared write; `--mode` is argparse-constrained and notably "
       "cannot reach `repair`."),
    _p("daemon_run", "daemon/server.py::_run", MODES, "caller", False,
       "the JSON-RPC `run` method. Its own docstring calls it the raw ENGINE surface and says it "
       "bypasses everything `flows.replay()` enforces. Since 1.7 it refuses an out-of-set "
       "mode/provider/grounding BEFORE building a client rather than after."),
    _p("run_many", "parallel.py::_one", ANY_MODE, "caller", False,
       "`run_cached(**kwargs)` — each task dict is forwarded verbatim, so this door permits "
       "whatever its caller wrote, including a mode the CLI cannot express."),

    # ---- THE GATED DOORS. Everything here is reached through the `flows` lifecycle. -------------
    _p("flows_learn", "flows.py::_learn_once", ("learn",), "fixed", True,
       "authoring. A write fires here ONCE by design — that is how a write flow is discovered at "
       "all — and R3.13's terminal refusal is what stops runs 2..N re-firing it."),
    _p("flows_replay", "flows.py::_attempt_replay_body", ("replay", "repair"), "caller", True,
       "the gated replay. `repair` is reached only by `replay()`'s own third attempt; neither mode "
       "falls through to a re-author, which is why this door cannot re-perform a write."),
    _p("flows_dry_run", "flows.py::dry_run", ("replay",), "never", True,
       "the preview. `provider=None` at the call site, so it is structurally 0-LLM."),
    _p("flows_record", "flows.py::record", ("replay",), "never", True,
       "the recorder's post-demo verification pass. `provider=None`, `mode='replay'`."),
    _p("mcp_tool", "flows.py::_attempt_replay_body", ("replay", "repair"), "caller", True,
       "the MCP surface does not reach the engine itself — `call_flow_tool` goes through "
       "`flows.replay()`, so it inherits that door's policy exactly. Listed because a reader "
       "looking for 'what may MCP do' must find an answer here rather than infer one."),
]}


# ---------------------------------------------------------------------------------------------------
# 1. The door SET is derived, so a ninth door cannot be quietly absent.

def _call_sites() -> dict:
    """{module::innermost enclosing function: the `run_cached` Call node} for every door in src/."""
    out: dict = {}

    def visit(node, owner, short):
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                visit(child, child.name, short)
                continue
            if isinstance(child, ast.Call) and _name_of(child.func) == "run_cached":
                out[f"{short}::{owner}"] = child
            visit(child, owner, short)

    for mod in DOOR_MODULES:
        short = mod.__name__.split(".")[-1] + ".py"
        if mod is daemon_mod:
            short = "daemon/server.py"
        visit(ast.parse(inspect.getsource(mod)), "<module>", short)
    return out


def _name_of(func) -> str:
    return getattr(func, "id", None) or getattr(func, "attr", "")


def test_every_door_into_the_engine_has_a_policy_row() -> None:
    derived = set(_call_sites())
    # `mcp_tool` reaches the engine THROUGH `flows_replay`, so it shares that site rather than
    # having one of its own. Excluded from the site comparison and asserted separately below.
    claimed = {p.site for p in POLICY.values() if p.door != "mcp_tool"}
    assert derived == claimed, (
        f"the doors into `run_cached` and the policy table disagree.\n"
        f"  in src/ but not in POLICY: {sorted(derived - claimed)}\n"
        f"  in POLICY but not in src/: {sorted(claimed - derived)}\n"
        f"A new door is a new answer to 'what may this permit', and it must be written down.")
    assert POLICY["mcp_tool"].site == POLICY["flows_replay"].site, (
        "mcp_tool no longer shares flows_replay's site — it has grown its own way into the engine "
        "and needs a row of its own rather than an inherited one.")
    print(f"{len(derived)} distinct call site(s) into run_cached, {len(POLICY)} policy row(s)")


def test_the_printed_policy_table() -> None:
    """The artifact this step exists to produce. Read it; it is short on purpose."""
    print(f"\n{'door':<14} {'gates':<6} {'llm':<7} {'re-authors a write':<19} modes")
    for name in sorted(POLICY, key=lambda n: (POLICY[n].flow_gates, n)):
        p = POLICY[name]
        modes = p.modes if p.modes == ANY_MODE else " ".join(sorted(p.modes))
        print(f"{p.door:<14} {str(p.flow_gates):<6} {p.llm:<7} "
              f"{('YES' if p.reauthors else 'no'):<19} {modes}")
    ungated = sorted(n for n, p in POLICY.items() if p.reauthors)
    assert ungated == ["cli_root", "daemon_run", "flows_learn", "run_many"], ungated


# ---------------------------------------------------------------------------------------------------
# 2. `may_reauthor_a_write` is DERIVED, and the three raw doors are exactly the ones that can.

def test_a_door_that_can_re_author_is_derived_from_its_modes_not_typed() -> None:
    for p in POLICY.values():
        expect = p.modes == ANY_MODE or bool(set(p.modes) & REAUTHORING_MODES)
        assert p.reauthors is expect
    assert {n for n, p in POLICY.items() if p.reauthors and not p.flow_gates} == {
        "cli_root", "daemon_run", "run_many"}, (
        "the set of doors that can re-perform a write WITHOUT the flow-level gates has changed. "
        "That set is the whole reason this table exists; a change to it is a change to what an "
        "un-gated caller can cause.")
    assert {n for n, p in POLICY.items() if p.reauthors and p.flow_gates} == {"flows_learn"}, (
        "a GATED door acquired the ability to re-author. Only authoring should have it, and only "
        "because that is how a write flow is discovered — R3.13's terminal refusal is what bounds it.")


def test_the_fall_through_that_makes_auto_re_author_is_still_where_this_table_says() -> None:
    """The table's central claim rests on ONE branch in `run_cached`. Pin it, or the table becomes
    a description of code that has moved on."""
    src = inspect.getsource(flow_mod.run_cached)
    assert 'if report.success or mode in ("replay", "repair") or report.mode == "escalate":' in src, (
        "`run_cached`'s post-replay branch has changed shape. Which modes fall through to a "
        "re-author is what every `may_reauthor_a_write` cell in POLICY is derived from.")
    assert "fall through to a fresh learn run" in src


# ---------------------------------------------------------------------------------------------------
# 3. Each row's `modes` cross-checked against whatever constrains it in the source.

def test_the_cli_root_modes_are_the_argparse_choices() -> None:
    """Derived from `main()`'s own `add_argument`, by AST rather than by building the parser: the
    root parser is constructed INLINE inside `main()` and there is no function that returns it."""
    tree = ast.parse(inspect.getsource(cli_mod.main))
    choices = None
    for n in ast.walk(tree):
        if (isinstance(n, ast.Call) and _name_of(n.func) == "add_argument"
                and any(isinstance(a, ast.Constant) and a.value == "--mode" for a in n.args)):
            for kw in n.keywords:
                if kw.arg == "choices":
                    choices = frozenset(e.value for e in kw.value.elts)
    assert choices, "no `--mode` with `choices=` in `cli.main` — this cross-check is inert"
    assert choices == POLICY["cli_root"].modes, (sorted(choices), sorted(POLICY["cli_root"].modes))
    assert choices < MODES, (
        "the CLI root now reaches every engine mode. The table says it cannot reach `repair`, and "
        "that asymmetry is a fact about this door worth keeping true or worth updating here.")


def test_the_daemon_modes_are_the_engines_own_closed_set() -> None:
    assert POLICY["daemon_run"].modes == MODES
    assert daemon_mod.MODES is MODES, "the daemon keeps its own copy of the mode set"


@pytest.mark.parametrize("door", ["flows_dry_run", "flows_record", "flows_learn"])
def test_a_door_with_a_literal_mode_says_the_literal(door: str) -> None:
    """These three constrain themselves at the call site, so the table can be checked exactly."""
    call = _call_sites()[POLICY[door].site]
    literal = next((k.value.value for k in call.keywords
                    if k.arg == "mode" and isinstance(k.value, ast.Constant)), None)
    assert literal is not None, f"{door}'s call site no longer passes a literal mode"
    assert POLICY[door].modes == frozenset({literal}), (literal, POLICY[door].modes)


def test_run_many_really_does_forward_whatever_it_is_given() -> None:
    """`ANY_MODE` is the strongest claim in the table — that a door constrains nothing — so it is
    the one most worth deriving rather than believing."""
    call = _call_sites()[POLICY["run_many"].site]
    assert not call.keywords or all(k.arg is None for k in call.keywords), (
        "run_many's call now names arguments; if it constrains `mode` the table must say so")
    assert any(k.arg is None for k in call.keywords), "expected a `**kwargs` splat"


# ---------------------------------------------------------------------------------------------------
# 4. The daemon's closed sets, refused BEFORE anything is built.

@pytest.mark.parametrize("params,word", [
    ({"mode": "REPLAY"}, "unknown mode"),
    ({"mode": "bogus"}, "unknown mode"),
    ({"provider": "clod"}, "unknown provider"),
    ({"grounding": "anthropicc"}, "unknown grounding"),
    ({"grounding": ""}, "unknown grounding"),
], ids=["a case typo", "a bad mode", "a bad provider", "a typo'd grounding", "an empty grounding"])
def test_the_daemon_refuses_an_out_of_set_value(params: dict, word: str) -> None:
    with pytest.raises(ValueError, match=word):
        daemon_mod._validate_run({"url": "http://h/", "goal": "g", **params})


@pytest.mark.parametrize("params", [
    {}, {"mode": "auto"}, {"mode": "repair"}, {"provider": "mock"},
    {"grounding": "anthropic"}, {"provider": "mock", "grounding": "anthropic", "mode": "learn"},
], ids=["defaults", "auto", "repair", "mock", "grounding", "all three"])
def test_the_daemon_permits_every_in_set_value(params: dict) -> None:
    """THE QUIET DIRECTION, pinned as hard as the loud one. A validator that refuses everything
    passes every cell above — that is D0's shape, and it would take the daemon offline."""
    daemon_mod._validate_run({"url": "http://h/", "goal": "g", **params})


def test_nothing_is_built_before_the_validation() -> None:
    """The ORDER is what 1.7 buys here; the checking existed already, at the far end of `_run`.
    `run_cached` has refused an unknown mode since R4.31 — after `get_provider` built a real Router
    and `AnthropicGrounding()` an SDK client."""
    src = inspect.getsource(daemon_mod._run)
    body = src.split("\n")
    call = next(i for i, l in enumerate(body) if "_validate_run(params)" in l)
    for later in ("get_provider(", "AnthropicGrounding(", "run_cached("):
        at = next(i for i, l in enumerate(body) if later in l)
        assert call < at, f"{later} runs before the validation — a refused request pays for it anyway"


def test_the_grounding_set_is_not_a_single_equality_test() -> None:
    """The shape that made an unknown grounding SILENT. `== "anthropic"` accepts one value and
    turns every other into `None`, so the caller asked for grounding, ran without it, and was told
    nothing — inviolable #2, in one operator."""
    src = inspect.getsource(daemon_mod._validate_run)
    assert "in GROUNDINGS" in src, "the grounding check is no longer a set membership test"
    assert daemon_mod.GROUNDINGS, "the grounding set is empty — every value would be refused"
