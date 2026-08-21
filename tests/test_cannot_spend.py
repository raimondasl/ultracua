"""1.3 — the third state: an owner that CANNOT spend, told apart from one nobody is watching.

R4.53, both halves. Browser-free, key-less.

WHAT THE DEFECT WAS. `RouterWatch` classified an owner by whether a `UsageTotals` was reachable, and
anything without one was an unwatched SPENDER. Measured before the fix: `ScriptedProvider`,
`MockProvider`, `MockGrounding` and the oracle teacher — the entire population the key-less suite and
`drift_bench` are built from — each reported `cost_usd: None, unobserved_llm_path: True`, while
`flow.py`'s own comment said the opposite. Separately, `accounting_failed` was a sticky bool on a
long-lived object, so one failure answered a run-scoped question with "yes" for the rest of the
process.

WHAT MUST NOT MOVE, and both are asserted here rather than hoped for:

  * the residual. An owner that really could spend and exposes no totals must STAY unobserved —
    otherwise the fix is "always say zero", which is the confident wrong number the tri-state exists
    to prevent, arrived at from the other side.
  * inviolable #1. The declaration is read through the two attributes the watch already reads, and
    nothing else. Commit `00888b4` added a probe for a third and tripped the tripwire; a replay does
    not poke at a provider.
"""

from __future__ import annotations

import ast
import inspect
import textwrap
from pathlib import Path

import pytest

from ultracua.obs import RouterWatch, UsageTotals
from ultracua.providers.mock import MockProvider
from ultracua.providers.scripted import ScriptedProvider
from ultracua.vision import MockGrounding

ROOT = Path(__file__).resolve().parents[1]


class _Spender:
    """An LLM-capable owner with no `totals` — what the vision grounding object looked like before
    it grew one. THE RESIDUAL, and the only thing that may still make a run unobserved."""


class _Exploding:
    """The inviolable tripwire, copied here so this file's own cells face it too.

    `tests/test_inviolable_properties.py` owns the real one. This is not a second derivation of a
    fact — it is the same one-line stub, and its point here is that the classification below must
    reach its verdict without touching anything but `router`.
    """

    def __init__(self) -> None:
        self.router = None

    def __getattr__(self, name):
        raise AssertionError(f"the accounting probed provider.{name} — inviolable #1, commit 00888b4")


# ---------------------------------------------------------------------------------------------
# The third state
# ---------------------------------------------------------------------------------------------

KEY_LESS = [
    ("ScriptedProvider", lambda: ScriptedProvider([])),
    ("MockProvider", MockProvider),
    ("MockGrounding", lambda: MockGrounding([])),
]


@pytest.mark.parametrize("label,make", KEY_LESS, ids=[c[0] for c in KEY_LESS])
def test_a_key_less_owner_reports_a_real_zero_not_an_unknown(label, make) -> None:
    """`flow.py`'s comment, made true: "a key-less learn (scripted teacher, no router) must SAY it
    spent nothing rather than stay silent about it"."""
    w = UsageTotals.observe(make())
    assert w.observed is True, f"{label} is still classified as a spender nobody watched"
    d = w.as_dict("claude-opus-4-6")
    assert d["cost_usd"] == 0.0
    assert "unobserved_llm_path" not in d
    print(f"{label:18} -> observed={w.observed} cost={d['cost_usd']}")


def test_the_oracle_teacher_declares_too() -> None:
    """The benchmark's own teacher, which `drift_bench` is entirely built from — imported here
    rather than listed in `KEY_LESS` because it lives outside `src/`."""
    from benchmarks.oracle_provider import OracleProvider

    w = UsageTotals.observe(OracleProvider([]))
    assert w.observed is True and w.as_dict("claude-opus-4-6")["cost_usd"] == 0.0
    print(f"OracleProvider     -> observed={w.observed}")


def test_an_owner_that_could_spend_and_exposes_nothing_is_STILL_unobserved() -> None:
    """THE CONTROL. Without it "declare zero everywhere" passes every cell above and destroys the
    only claim this tri-state exists to make."""
    w = UsageTotals.observe(_Spender())
    assert w.observed is False
    d = w.as_dict("claude-opus-4-6")
    assert d["cost_usd"] is None and d["unobserved_llm_path"] is True
    print(f"_Spender           -> observed={w.observed} cost={d['cost_usd']!r}")


def test_a_declared_zero_and_a_measured_zero_report_identically() -> None:
    """Two of the three states, one report — on purpose. Inventing a third word for the record
    would be a distinction with no consumer, and the record is read by operators."""
    from ultracua.llm.base import Router

    real = Router.__new__(Router)
    real.totals = UsageTotals()

    class _Provider:
        def __init__(self, router):
            self.router = router

    measured = UsageTotals.observe(_Provider(real)).as_dict("claude-opus-4-6")
    declared = UsageTotals.observe(ScriptedProvider([])).as_dict("claude-opus-4-6")
    assert measured == declared, f"{measured} != {declared}"
    print(f"measured zero == declared zero: {measured}")


def test_cannot_spend_is_a_named_declaration_and_not_just_an_empty_object() -> None:
    """It returns a plain `UsageTotals()`, so the value is not the point — the NAME is. A key-less
    owner writing `self.totals = UsageTotals()` reads as bookkeeping; writing
    `UsageTotals.cannot_spend()` states a fact about itself that the next reader can grep for."""
    t = UsageTotals.cannot_spend()
    assert isinstance(t, UsageTotals) and t.calls == 0 and t.cost_usd("claude-opus-4-6") == 0.0
    for label, make in KEY_LESS:
        src = textwrap.dedent(inspect.getsource(type(make()).__init__))
        assert "cannot_spend()" in src, (
            f"{label} sets up its totals some other way — the declaration is meant to be one "
            f"greppable phrase, not four spellings of an empty object")
    print("three key-less owners, one phrase: UsageTotals.cannot_spend()")


# ---------------------------------------------------------------------------------------------
# Inviolable #1 — the declaration is offered, never extracted
# ---------------------------------------------------------------------------------------------

def test_classifying_an_owner_touches_only_router_and_totals() -> None:
    """DERIVED from `RouterWatch.__init__`'s own source, because commit `00888b4` is what happens
    when this is left to review: it added a `getattr(o, "accounting_failed", False)` probe, and the
    inviolable tripwire went red on the repair path.

    Two attribute names, and the test names them — a scan asserting "few attributes" would pass a
    probe for the wrong one.
    """
    tree = ast.parse(textwrap.dedent(inspect.getsource(RouterWatch.__init__)))
    probed = {n.args[1].value for n in ast.walk(tree)
              if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == "getattr"
              and len(n.args) >= 2 and isinstance(n.args[1], ast.Constant)}
    assert probed, "the scan found no getattr at all — it has gone vacuous"
    assert probed == {"router", "totals"}, (
        f"RouterWatch.__init__ probes {sorted(probed)}. Only `router` and `totals` are permitted: "
        f"`_Exploding` raises on everything else, and inviolable #1's broader reading is that a "
        f"replay does not poke at a provider.")
    print(f"RouterWatch.__init__ probes exactly {sorted(probed)}")


def test_the_tripwire_survives_the_classification_end_to_end() -> None:
    """The structural scan above is a negative; this is the behaviour. A provider that raises on any
    unpermitted attribute must be classifiable without raising."""
    w = UsageTotals.observe(_Exploding())          # must not raise
    assert w.observed is False, (
        "a provider whose router is None exposes no totals, so it remains the residual — this cell "
        "is not asserting it became free")
    print(f"_Exploding classified without a probe -> observed={w.observed}")


# ---------------------------------------------------------------------------------------------
# Run-scoped accounting failures
# ---------------------------------------------------------------------------------------------

class _Grounding:
    def __init__(self) -> None:
        self.totals = UsageTotals()


def test_an_accounting_failure_belongs_to_the_run_it_happened_in() -> None:
    """R4.53's second half. The flag rode on a long-lived object, so one failure answered a
    run-scoped question for the rest of the process.

    Four states asserted in sequence, because the interesting one is the third: a fresh watch over
    an owner that has failed BEFORE must be clean.
    """
    g = _Grounding()
    first = UsageTotals.observe(g)
    assert first.observed is True

    g.totals.accounting_failures += 1
    assert first.observed is False, "a failure during the run must still make it unobserved"

    later = UsageTotals.observe(g)
    assert later.observed is True, (
        "a LATER run reports unobserved on the strength of an earlier run's failure — that is the "
        "stickiness R4.53 names")

    g.totals.accounting_failures += 1
    assert later.observed is False, (
        "a SECOND consecutive failure went unnoticed — which is why the fix is a counter and not a "
        "snapshot of a boolean: a bool is already True, so there is no transition to see")
    print("run1 fail -> unobserved | run2 clean -> observed | run2 fail -> unobserved")


def test_accounting_failed_is_readable_and_not_writable() -> None:
    """Every existing READER keeps working; the writers had to say `+= 1` deliberately. A settable
    property translating `= True` into `+= 1` would be one token meaning two things."""
    t = UsageTotals()
    assert t.accounting_failed is False
    t.accounting_failures += 1
    assert t.accounting_failed is True
    with pytest.raises(AttributeError):
        t.accounting_failed = True                  # type: ignore[misc]
    print("accounting_failed reads; accounting_failures is what writers move")


def test_the_delta_of_a_run_carries_only_that_runs_failures() -> None:
    t = UsageTotals()
    t.accounting_failures += 1
    snap = t.snapshot()
    assert t.since(snap).accounting_failures == 0
    t.accounting_failures += 2
    assert t.since(snap).accounting_failures == 2
    print("since(): 1 before the snapshot -> 0; 2 after -> 2")


def test_vision_is_the_only_writer_in_src_and_it_counts() -> None:
    """The one production writer, pinned by name. If a second appears it should be a deliberate
    diff, because every one of them is a claim that a spend went unrecorded.

    BY FILE, NOT BY LINE. A line number is a fact about today's whitespace — this very cell went red
    on its own slice when a declaration three functions up added six lines, which is a pin failing
    for a reason nobody cares about and the shape `scripts/ratchets.py` was built to escape.
    """
    hits = []
    for path in sorted((ROOT / "src").rglob("*.py")):
        for line in path.read_text(encoding="utf-8").splitlines():
            code = line.split("#")[0]
            if "accounting_failures" in code and "+=" in code:
                hits.append(path.relative_to(ROOT).as_posix())
    assert hits == ["src/ultracua/vision.py"], (
        f"the set of writers moved: {hits}. Every one is a claim that a spend went unrecorded, so a "
        f"second should be a deliberate diff.")
    print(f"the only src writer is {hits[0]}")


# ---------------------------------------------------------------------------------------------
# The readers — unknown never sums to 0.0
# ---------------------------------------------------------------------------------------------

def test_an_unknown_cost_is_absorbing_in_both_summing_readers() -> None:
    """`variance._cost` did `... or 0.0`, and `drift_bench` did `sum(x or 0.0) or None` — which was
    wrong in BOTH directions at once: an unknown row summed as free, and a genuine total of exactly
    zero came back as None because 0.0 is falsy. A key-less bench run produces exactly that total,
    so the one number the bench can state with certainty was published as unknown."""
    from benchmarks import drift_bench, variance

    # ONE DEFINITION, asserted by identity rather than by two sets of equal-looking assertions.
    # This shipped as two copies for one afternoon and they disagreed on their FIRST non-trivial
    # input — `[0.1, 0.2]` gave 0.30000000000000004 and 0.3, because only one rounded. Comparing
    # behaviour would have caught that; comparing identity makes it unable to recur.
    assert variance.sum_cost is drift_bench.sum_cost, (
        "the two bench readers hold different `sum_cost` objects again — two derivations of one "
        "rule is how the rule drifts, and this one drifted before either copy had a second caller")

    s = variance.sum_cost
    assert s([0.0, 0.0]) == 0.0, "a real zero became something else"
    assert s([1.0, 2.0]) == 3.0
    assert s([0.1, 0.2]) == 0.3, "the rounding that separated the two copies is gone"
    assert s([1.0, None]) is None, "an unknown summed as free"
    assert s([None]) is None
    assert s([]) == 0.0, "an empty corpus spent nothing, and that is a measurement"
    print("one sum_cost, shared by both readers: unknown absorbs, zero is zero")


class _Report:
    def __init__(self, usage):
        self.extra = {"usage": usage}


READER_CELLS = [
    ("a priced zero", {"cost_usd": 0.0}, 0.0),
    ("a real cost", {"cost_usd": 1.25}, 1.25),
    ("an unpriced/unobserved run", {"cost_usd": None}, None),
    ("no usage key at all", {}, None),
]


@pytest.mark.parametrize("label,usage,expected", READER_CELLS, ids=[c[0] for c in READER_CELLS])
def test_variance_cost_never_turns_an_unknown_into_free(label, usage, expected) -> None:
    from benchmarks import variance

    assert variance._cost(_Report(usage)) == expected or (
        variance._cost(_Report(usage)) is None and expected is None)
    print(f"{label:28} -> {variance._cost(_Report(usage))!r}")


def test_the_money_formatter_keeps_a_shrug_and_a_claim_apart() -> None:
    from benchmarks import variance

    assert variance._money(0.0) == "$0.0000"
    assert variance._money(None) == "UNKNOWN"
    print(f"0.0 -> {variance._money(0.0)} | None -> {variance._money(None)}")


def test_a_record_whose_cost_is_unknown_says_so_and_the_gate_is_loud() -> None:
    """A run that cannot account for its own spend has proved nothing about cost. Reporting it as
    a pass would let the 0-LLM claim this whole accounting exists to make go unfalsifiable."""
    from benchmarks import variance

    rec = variance.build_record("b", "p", 1, "t", {"replay_success_rate": [1.0]}, cost_usd=None)
    assert rec["cost_usd"] is None

    known = variance.build_record("b", "p", 1, "t", {"replay_success_rate": [1.0]}, cost_usd=1.0)
    unknown_now = [f for f in variance.compare_records(known, rec)["findings"]
                   if f["metric"] == "cost_usd"][0]
    assert unknown_now["regressed"] is True, "an unaccountable run gated green"

    unknown_baseline = [f for f in variance.compare_records(rec, known)["findings"]
                        if f["metric"] == "cost_usd"][0]
    assert unknown_baseline["regressed"] is False and "baseline" in unknown_baseline["detail"], (
        "an unknown BASELINE is not this run's fault — it is a reason to re-record, not to fail")
    print(f"current unknown -> regressed={unknown_now['regressed']}; "
          f"baseline unknown -> regressed={unknown_baseline['regressed']}")


# ---------------------------------------------------------------------------------------------
# Arming the reader guards — `prove_red` cannot reach `benchmarks/` (R4.77)
#
# The `src/` half of this slice is proved red by `tests/mutations/cannot_spend.py`, which is the
# stronger instrument: it runs three whole test files against each mutant rather than one named
# cell. These four are the residue that harness structurally cannot install.
# ---------------------------------------------------------------------------------------------

def test_the_absorbing_sum_pin_notices_an_unknown_summing_as_free(monkeypatch) -> None:
    """`or 0.0` — the exact text that was there before 1.3."""
    from benchmarks import variance
    from tests import _arming

    _arming.mutate_function(monkeypatch, variance, "sum_cost",
                            "        if x is None:\n            return None\n"
                            "        total += float(x)",
                            "        total += float(x or 0.0)")
    print(_arming.assert_red(test_an_unknown_cost_is_absorbing_in_both_summing_readers))


def test_the_absorbing_sum_pin_notices_a_real_zero_becoming_unknown(monkeypatch) -> None:
    """The other direction, and the half `drift_bench` actually shipped: `... or None` turned a
    genuine total of exactly zero into "unknown", because 0.0 is falsy."""
    from benchmarks import variance
    from tests import _arming

    _arming.mutate_function(monkeypatch, variance, "sum_cost",
                            "    return round(total, 6)",
                            "    return round(total, 6) or None")
    print(_arming.assert_red(test_an_unknown_cost_is_absorbing_in_both_summing_readers))


def test_the_one_definition_pin_notices_a_second_copy(monkeypatch) -> None:
    """The identity assertion is what makes the drift unable to recur, so it needs arming too.

    The stand-in is BEHAVIOURALLY identical, asserted before the guard runs — otherwise this cell
    would go red for the copy being wrong rather than for it being a copy, which is the weaker
    thing and the one that would still pass if identity stopped being checked."""
    from benchmarks import drift_bench, variance
    from tests import _arming

    def _a_second_copy(xs):
        total = 0.0
        for x in xs:
            if x is None:
                return None
            total += float(x)
        return round(total, 6)

    monkeypatch.setattr(drift_bench, "sum_cost", _a_second_copy)
    for probe in ([0.1, 0.2], [1.0, None], [], [0.0, 0.0]):
        assert drift_bench.sum_cost(probe) == variance.sum_cost(probe), probe
    print(_arming.assert_red(test_an_unknown_cost_is_absorbing_in_both_summing_readers))


def test_the_reader_pin_notices_the_old_or_zero(monkeypatch) -> None:
    from benchmarks import variance
    from tests import _arming

    _arming.mutate_function(monkeypatch, variance, "_cost",
                            '    usage = report.extra.get("usage") or {}',
                            '    return float((report.extra.get("usage") or {}).get("cost_usd") or 0.0)\n'
                            '    usage = report.extra.get("usage") or {}')
    print(_arming.assert_red(test_variance_cost_never_turns_an_unknown_into_free,
                             *READER_CELLS[2]))


def test_the_money_pin_notices_a_shrug_formatted_as_a_claim(monkeypatch) -> None:
    from benchmarks import variance
    from tests import _arming

    _arming.mutate_function(monkeypatch, variance, "_money",
                            '    return "UNKNOWN" if x is None else f"${x:.4f}"',
                            '    return f"${x or 0.0:.4f}"')
    print(_arming.assert_red(test_the_money_formatter_keeps_a_shrug_and_a_claim_apart))


def test_the_gate_pin_notices_an_unaccountable_run_passing(monkeypatch) -> None:
    from benchmarks import variance
    from tests import _arming

    # The VERDICT, not the branch. `if cc is None: -> if False:` falls through to `float(None)` and
    # dies with a TypeError, which `assert_red` refuses as a kill because the guard never ran — a
    # mutation has to state its defect rather than break something on the way to it.
    _arming.mutate_function(monkeypatch, variance, "compare_records",
                            '"gated": True, "regressed": True,\n'
                            '                         "baseline": bc, "current": None,',
                            '"gated": True, "regressed": False,\n'
                            '                         "baseline": bc, "current": None,')
    print(_arming.assert_red(test_a_record_whose_cost_is_unknown_says_so_and_the_gate_is_loud))


# ---------------------------------------------------------------------------------------------
# The early return that carried no accounting at all
# ---------------------------------------------------------------------------------------------

def test_every_escalate_report_carries_its_usage() -> None:
    """`flows.py`'s contract is that `usage` is ALWAYS populated and always carries `cost_usd`.
    Both escalate paths were the two places that quietly were not — an early return, before the line
    every other exit passes through.

    The consequence was visible and had been sitting in a published artifact: `drift_bench` reported
    `cost_usd: null` for a wholly key-less run. After 1.3 the rows moved to 368 x 0.0 and 2 x None,
    which made it obvious the total was unknown because of the escalated pair, not because anything
    had been spent. All 370 read 0.0 now and the bench states its own total.

    DERIVED over `flow.py`'s own AST, and counted, so it cannot go vacuous if a path is deleted.

    READ THROUGH `inspect.getsource` OF THE IMPORTED MODULE, not from `src/` by path — and the
    difference is not cosmetic. `scripts/prove_red.py` installs its mutant as a copy first on
    `PYTHONPATH`, so a cell that opens the repo file parses PRISTINE source and can never contribute
    a kill. Measured on this very cell: its registered mutation was reported as a SURVIVOR while the
    guard was fine. That is R4.75, and `prove_red`'s own docstring names this as the fix.
    """
    import ultracua.flow as flow_module

    src = inspect.getsource(flow_module)
    found = []
    for node in ast.walk(ast.parse(src)):
        if not (isinstance(node, ast.Call) and getattr(node.func, "id", None) == "FlowReport"):
            continue
        kw = {k.arg: k.value for k in node.keywords}
        mode = kw.get("mode")
        if not (isinstance(mode, ast.Constant) and mode.value == "escalate"):
            continue
        extra = kw.get("extra")
        keys = ([k.value for k in extra.keys if isinstance(k, ast.Constant)]
                if isinstance(extra, ast.Dict) else [])
        found.append((node.lineno, keys))

    assert len(found) == 2, f"expected 2 escalate returns in flow.py, found {found}"
    for lineno, keys in found:
        assert "usage" in keys, (
            f"flow.py:{lineno} returns an escalate report with extra keys {keys} and no `usage`. "
            f"Every reader then renders the run's cost as UNKNOWN — for a run that escalated before "
            f"it could spend anything.")
    print(f"both escalate returns carry usage: {[l for l, _ in found]}")


# ---------------------------------------------------------------------------------------------
# The LOUD channel has to survive being printed
# ---------------------------------------------------------------------------------------------

def _record(cost, tmp_path, name):
    """A record built through the production aggregation, written where `_gate` reads baselines."""
    import json

    from benchmarks import variance

    rec = variance.build_record("demo", "p", 1, "t", {"replay_success_rate": [1.0]}, cost_usd=cost)
    path = tmp_path / name
    path.write_text(json.dumps(rec), encoding="utf-8")
    return rec, path


GATE_CELLS = [
    # (label, baseline cost, current cost, expected ok, a phrase the operator must actually SEE)
    ("current unknown -> loud FAIL", 0.01, None, False, "could not account for its own spend"),
    ("baseline unknown -> PASS, with the reason", None, 0.01, True, "re-record it"),
    ("both known, no regression", 0.01, 0.01, True, None),
]


@pytest.mark.parametrize("label,base,cur,ok,phrase", GATE_CELLS, ids=[c[0] for c in GATE_CELLS])
def test_the_cost_gate_prints_its_verdict_instead_of_crashing_on_it(label, base, cur, ok, phrase,
                                                                    tmp_path, capsys) -> None:
    """DRIVES `_gate`, WHICH IS THE FUNCTION THAT DECIDES THE EXIT CODE.

    The first version of this slice's gate cell asserted on `compare_records` and never called
    `_gate` — so the channel it existed to prove was loud was verified everywhere except at the
    place that speaks. Measured: `_gate` formatted both numbers with `f"{x:.4g}"`, which raises
    `TypeError` on None, so a correctly-computed `regressed: True` died BEFORE printing the `[FAIL]`
    row, its detail, or `== REGRESSION ==`. The inverse is worse: a baseline whose cost is unknown
    is designed to PASS and exited 1 instead.

    `_money` had been written for exactly this and applied to two of the three print sites.
    """
    from benchmarks import variance

    _, base_path = _record(base, tmp_path, "baseline.json")
    current, _ = _record(cur, tmp_path, "current.json")

    assert variance._gate(base_path, current) is ok
    out = capsys.readouterr().out
    assert "REGRESSION" in out if not ok else "PASS" in out
    assert "UNKNOWN" in out or (base is not None and cur is not None), (
        "an unknown cost was rendered as a number, which is the claim/shrug collapse again")
    if phrase:
        assert phrase in out, f"the operator is never told why: {out!r}"
    print(f"{label:34} -> ok={ok}; the verdict printed")


def test_the_number_formatter_renders_an_unknown_rather_than_raising() -> None:
    """The shape fix, asserted on its own: a formatter that cannot render an unknown will meet one.

    Both directions — a real number must still print as a number, or "render everything as UNKNOWN"
    satisfies the cell above and destroys the report.
    """
    from benchmarks import variance

    assert variance._g(None) == "UNKNOWN"
    assert variance._g(0.0) == "0"
    assert variance._g(0.012345) == "0.01234" or variance._g(0.012345) == "0.01235"
    assert variance._money(None) == "UNKNOWN" and variance._money(0.0) == "$0.0000"
    print(f"_g: None -> {variance._g(None)}, 0.01 -> {variance._g(0.01)}")


def test_the_gate_pin_notices_the_formatter_going_back_to_a_bare_format(monkeypatch, tmp_path,
                                                                       capsys) -> None:
    from benchmarks import variance
    from tests import _arming

    _arming.mutate_function(monkeypatch, variance, "_g",
                            '    return "UNKNOWN" if x is None else f"{x:.4g}"',
                            '    return f"{x:.4g}"')
    # The guard takes fixtures, so this cell must take them too and pass them through — an
    # arming harness that calls a cell with the wrong arity proves nothing about the cell.
    print(_arming.assert_red(test_the_cost_gate_prints_its_verdict_instead_of_crashing_on_it,
                             *GATE_CELLS[0], tmp_path, capsys))


def test_the_gate_pin_notices_the_detail_never_being_printed(monkeypatch, tmp_path,
                                                            capsys) -> None:
    """The numbers beside an unknown-cost verdict are both UNKNOWN, so the `detail` IS the content:
    "this run could not account for its own spend" and "the baseline's cost is unknown" are opposite
    instructions."""
    from benchmarks import variance
    from tests import _arming

    _arming.mutate_function(monkeypatch, variance, "_gate",
                            '        if f.get("detail"):',
                            "        if False:")
    # The guard takes fixtures, so this cell must take them too and pass them through — an
    # arming harness that calls a cell with the wrong arity proves nothing about the cell.
    print(_arming.assert_red(test_the_cost_gate_prints_its_verdict_instead_of_crashing_on_it,
                             *GATE_CELLS[0], tmp_path, capsys))


def test_the_arming_harness_tells_a_bad_call_from_a_real_crash() -> None:
    """`assert_red` refuses a TypeError raised BEFORE the guard runs and accepts one raised inside.

    Both halves matter and both were live in this slice. The refusal is S14's trap — a cell invoked
    with the wrong arity raising `TypeError`, which an earlier harness read as a legitimate
    complaint. The acceptance is 1.3's own `_gate` mutation, where `f"{None:.4g}"` raising IS the
    defect being proved; refusing it would have forced a mutation that states something else.

    The discriminator is the traceback depth, not the message — a message check would be a
    substring classifier, which this repo has measured at 28% false positives elsewhere.
    """
    from tests import _arming

    def needs_two_arguments(a, b):
        raise AssertionError("never reached")

    with pytest.raises(AssertionError, match="before it started running"):
        _arming.assert_red(needs_two_arguments, 1)          # arity: refused

    def raises_inside():
        return f"{None:.4g}"

    msg = _arming.assert_red(raises_inside)                 # internal: accepted
    assert msg.startswith("TypeError:")
    print(f"arity -> refused | internal -> accepted as {msg[:60]!r}")
