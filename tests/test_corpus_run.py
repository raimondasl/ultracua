"""The corpus batch: the loop, the record and the gate (B5).

BROWSER-FREE AND KEY-FREE. `score_one` is replaced by a scripted stand-in, so every path through
the batch runs in the fast tier — including the ones that only happen when a scenario dies, which
is the half a live run is least likely to exercise and most likely to need.
"""
from __future__ import annotations

import json

import pytest

from benchmarks import corpus, corpus_run, outcomes as O
from benchmarks.scored_run import _Record


def _ok_row(entry, outcome=O.OK):
    """What `score_one` hands back for a scenario that completed."""
    run = _Record(True, "", True, authored=True, recipe_steps=2, learn_found=True,
                  scenario=entry.scenario.name, substrate=entry.scenario.substrate)
    run.llm_calls, run.input_tokens, run.output_tokens = 4, 4000, 250
    # `per_model` IS LOAD-BEARING, and leaving it out failed the first draft of these cells with
    # `spend was recorded against a model with no price entry`. That is 1.3's rule working: tokens
    # with no priceable model is an UNKNOWN bill, and `_cost_of` refuses rather than publishing a
    # confident zero. The tuple is (input, output, cache_read, cache_write, calls).
    run.per_model = {"claude-opus-5": (4000, 250, 0, 0, 4)}
    verdict = O.Verdict(outcome=outcome, reason=f"scripted {outcome}", code="", family="",
                        evidence={})
    out = {"scenario": entry.scenario.name, "outcome": outcome, "steps": 2, "cost_usd": 0.05}
    return out, O.Scored(truth=entry.truth, run=run, verdict=verdict)


def _script(monkeypatch, behaviour):
    """Replace `score_one` with `behaviour(entry) -> (out, scored) | raises`."""
    entries = {e.scenario.name: e for e in corpus.CORPORA["gitea"]}
    seen = []

    async def fake(name, **kw):
        seen.append(name)
        return behaviour(entries[name])

    monkeypatch.setattr(corpus_run, "score_one", fake)
    return seen


def test_every_corpus_scenario_is_driven_once(monkeypatch) -> None:
    seen = _script(monkeypatch, _ok_row)
    import asyncio

    scored, rows = asyncio.run(corpus_run.run_corpus("gitea"))
    assert seen == [e.scenario.name for e in corpus.CORPORA["gitea"]]
    assert len(scored) == len(rows) == len(corpus.CORPORA["gitea"])


def test_a_scenario_that_raises_becomes_a_harness_row_not_a_missing_one(monkeypatch) -> None:
    """THE PROPERTY THIS FILE EXISTS FOR. A dropped row shrinks the denominator, and the mean goes
    UP — R4.96 one level higher, where the cause is a dead container rather than a failed learn."""
    import asyncio

    boom = "gitea-comment"

    def behaviour(entry):
        if entry.scenario.name == boom:
            raise RuntimeError("the substrate went away")
        return _ok_row(entry)

    _script(monkeypatch, behaviour)
    scored, rows = asyncio.run(corpus_run.run_corpus("gitea"))
    assert len(scored) == len(corpus.CORPORA["gitea"]), "the dead row must still be present"
    dead = next(s for s in scored if s.truth.name == boom)
    # `reason`, not `family`: a family comes from a refusal CODE and a dead run has none. The
    # `harness_error` clause is a separate, earlier door into `unscored` — checked here because
    # asserting the family would pass only by accident on a row that happened to carry a code.
    assert dead.verdict.outcome == "unscored"
    assert dead.verdict.reason == "harness_error"
    assert "the substrate went away" in dead.verdict.evidence.get("detail", "")


def test_a_row_with_no_verdict_is_a_harness_row_too(monkeypatch) -> None:
    """`score_one` returns `None` when nothing could be adjudicated. Inventing a verdict for it is
    precisely what B3 forbids, so it takes the same route as a raise."""
    import asyncio

    def behaviour(entry):
        if entry.scenario.name == "gitea-search":
            return {"scenario": entry.scenario.name}, None
        return _ok_row(entry)

    _script(monkeypatch, behaviour)
    scored, _ = asyncio.run(corpus_run.run_corpus("gitea"))
    row = next(s for s in scored if s.truth.name == "gitea-search")
    assert row.verdict.outcome == "unscored" and row.verdict.reason == "harness_error"


def test_the_record_and_the_gate_are_built_over_the_whole_corpus(monkeypatch) -> None:
    import asyncio

    _script(monkeypatch, _ok_row)
    scored, _ = asyncio.run(corpus_run.run_corpus("gitea"))
    rec = O.build_bench_record(scored, bench="t", provider="p", timestamp="2026-08-25T00:00:00Z")
    n = len(corpus.CORPORA["gitea"])
    assert len(rec["scenarios"]) == n
    assert rec["metrics"]["availability_rate"]["n"] == n
    assert O.gate_bench_record(rec)["ok"] is True


def test_a_dead_row_fails_the_gate_until_it_is_acknowledged(monkeypatch) -> None:
    """Channel 0's whole point, reached from the batch: a run where a scenario never executed must
    not gate green just because the survivors did."""
    import asyncio

    def behaviour(entry):
        if entry.scenario.name == "gitea-comment":
            raise RuntimeError("the substrate went away")
        return _ok_row(entry)

    _script(monkeypatch, behaviour)
    scored, _ = asyncio.run(corpus_run.run_corpus("gitea"))
    rec = O.build_bench_record(scored, bench="t", provider="p", timestamp="2026-08-25T00:00:00Z")
    assert O.gate_bench_record(rec)["ok"] is False
    row = next(r for r in rec["unscored"] if r["scenario"] == "gitea-comment")
    signed = O.gate_bench_record(rec, acknowledged=((row["scenario"], row["reason"]),))
    assert signed["ok"] is True


def test_only_selects_a_subset(monkeypatch) -> None:
    import asyncio

    seen = _script(monkeypatch, _ok_row)
    asyncio.run(corpus_run.run_corpus("gitea", only=("gitea-search", "gitea-comment")))
    assert sorted(seen) == ["gitea-comment", "gitea-search"]


def test_the_runner_does_not_write_a_baseline() -> None:
    """A BASELINE IS A HUMAN ACT: one pass per scenario cannot separate a regression from a flake,
    which is the same reason B3 leaves `FLIP_IS_GATED` False.

    ASSERTED AS A PROPERTY, NOT SCANNED AS TEXT — the fifth time in this project that a scan has
    matched its own prose, and the first draft of this very cell did it on the module docstring that
    EXPLAINS the rule. The property is that every file this module opens for writing is the caller's
    `--out`, read from the AST rather than grepped for a path.
    """
    import ast
    import inspect

    tree = ast.parse(inspect.getsource(corpus_run))
    targets = [ast.unparse(n.args[0]) if n.args else "<none>"
               for n in ast.walk(tree)
               if isinstance(n, ast.Call) and getattr(n.func, "id", None) == "open"]
    assert targets == ["args.out"], (
        f"this module opens {targets} for writing; the only permitted destination is the caller's "
        f"--out. Promoting a record into baselines/ is a reviewed human act, not a flag.")


@pytest.mark.parametrize("substrate", sorted(corpus.CORPORA))
def test_every_substrate_is_runnable(substrate: str) -> None:
    """The CLI's choices come from `corpus.CORPORA`, so a substrate added tomorrow is runnable
    without touching this module."""
    assert corpus.CORPORA[substrate], f"{substrate} has no scenarios"


def test_every_attribute_read_off_an_adjudicate_result_exists_on_Scored() -> None:
    """THE BUG THIS SLICE SHIPPED INTO A PAID RUN, and the reason a stub could not catch it.

    `score_one` was changed from `classify` (which returns a `Verdict`) to `adjudicate` (which
    returns a `Scored` wrapping one) and kept reading `.outcome` / `.reason` off the wrapper. Every
    scenario in the first corpus run raised `AttributeError` — AFTER its learn had been paid for,
    because that read is the last statement in the function. Roughly $0.60 of learns were discarded.

    The batch's other cells script `score_one`, so they exercised the SHAPE this test file expects
    rather than the shape the module produces: a green property is worth exactly what its stub is
    worth. This one reads the real source and the real class, so no stub sits between them.

    DERIVED, not a list: any attribute read off an `adjudicate` result must exist on `Scored`.
    """
    import ast
    import dataclasses
    import inspect
    import textwrap

    from benchmarks import outcomes as O
    from benchmarks import scored_run as SR

    valid = {f.name for f in dataclasses.fields(O.Scored)} | {
        n for n in dir(O.Scored) if not n.startswith("_")}

    for mod in (SR, corpus_run):
        tree = ast.parse(textwrap.dedent(inspect.getsource(mod)))
        # Names bound to an `<outcomes>.adjudicate(...)` call — RECEIVER AND ALL.
        #
        # TWO DIFFERENT `adjudicate`s ARE IN SCOPE and the first draft of this guard conflated them:
        # `oracle.adjudicate()` returns an `oracles.Verdict`, whose `.satisfied` is perfectly real,
        # and matching on the method NAME alone reported it as the very bug being guarded against.
        # A guard that cannot name which object it is talking about is a guard that cries wolf.
        def _is_outcomes_adjudicate(call) -> bool:
            f = getattr(call, "func", None)
            return (isinstance(f, ast.Attribute) and f.attr == "adjudicate"
                    and isinstance(f.value, ast.Name) and f.value.id in {"outcomes", "O"})

        bound = {t.id for n in ast.walk(tree) if isinstance(n, ast.Assign)
                 for t in n.targets if isinstance(t, ast.Name)
                 if isinstance(n.value, ast.Call) and _is_outcomes_adjudicate(n.value)}
        for n in ast.walk(tree):
            if (isinstance(n, ast.Attribute) and isinstance(n.value, ast.Name)
                    and n.value.id in bound):
                assert n.attr in valid, (
                    f"{mod.__name__} reads `{n.value.id}.{n.attr}` off an `adjudicate` result, but "
                    f"`Scored` has no such attribute — the verdict's own fields live on "
                    f"`.verdict`. This is the AttributeError that cost a paid corpus run.")


# ---------------------------------------------------------------------------------------------------
# THE BASELINE COMPARISON (2.4). `--baseline` is what turns on three of `gate_bench_record`'s five
# channels; without it the gate runs the ABSOLUTE ones only and prints PASS having compared against
# nothing. So the flag's REFUSALS matter as much as its happy path: every one of them is a way the
# weekly run could report a confident pass over a comparison nobody made.


def _real_baseline() -> dict:
    import json as _json
    from pathlib import Path as _Path
    root = _Path(__file__).resolve().parents[1]
    return _json.loads((root / "baselines" / "customer_v1_gitea.json").read_text(encoding="utf-8"))


def test_the_committed_baseline_is_accepted_for_its_own_substrate() -> None:
    """Anti-vacuity, first: every refusal below is satisfied by a checker that refuses everything."""
    corpus_run._require_a_comparable_baseline(_real_baseline(), "gitea")


def test_a_baseline_for_another_substrate_is_refused() -> None:
    """THE SILENT, FLATTERING ONE. The scenario sets are disjoint, so `_flip_findings` finds nothing
    to flip and `_rate_findings` compares two different corpora's `availability_rate` as one
    population. The verdict would be a PASS built on a comparison nobody made."""
    with pytest.raises(SystemExit, match="customer-odoo"):
        corpus_run._require_a_comparable_baseline(_real_baseline(), "odoo")


def test_a_single_pass_promoted_by_hand_is_refused() -> None:
    """`kind` separates an aggregate from one run. A single pass carries n=7, and the Wilson bound
    the gate computes off it would read as three times the evidence it is."""
    b = dict(_real_baseline(), kind="bench-record")
    with pytest.raises(SystemExit, match="corpus-baseline"):
        corpus_run._require_a_comparable_baseline(b, "gitea")


def test_a_baseline_cut_before_the_corpus_moved_is_refused() -> None:
    """Both directions of the scenario set, because they are different events: a scenario ADDED is
    unmeasured by the baseline, and one GONE means the baseline is describing a corpus that no
    longer exists. `baselines/README.md`'s standing rule is that a rate measured under one
    configuration is never compared against another."""
    b = _real_baseline()
    shrunk = dict(b, scenarios={k: v for k, v in list(b["scenarios"].items())[:4]})
    with pytest.raises(SystemExit, match="added"):
        corpus_run._require_a_comparable_baseline(shrunk, "gitea")
    grown = dict(b, scenarios={**b["scenarios"], "gitea-invented": {"outcome": "ok"}})
    with pytest.raises(SystemExit, match="gone"):
        corpus_run._require_a_comparable_baseline(grown, "gitea")


def test_the_gate_actually_receives_the_baseline(monkeypatch, tmp_path) -> None:
    """THE WIRING, not the checker. A `--baseline` that is validated and then not threaded would
    pass every cell above while leaving the three comparative channels off -- which is precisely
    the failure the flag exists to prevent, one layer in.

    Driven through `main` so the argparse -> load -> validate -> gate path is the one exercised.
    """
    seen = {}
    real_gate = O.gate_bench_record

    def spy(record, **kw):
        seen.update(kw)
        return real_gate(record, **kw)

    # THROUGH THE MODULE BINDING. `corpus_run` does `from benchmarks import outcomes`, so it calls
    # `outcomes.gate_bench_record` — patching only the `O` alias this file imported would bind a
    # different name and the spy would never fire. That is S14's `from .providers import
    # build_router` lesson, and 0.133.0 hit it again from the test side with four mutations that all
    # survived because the cell held the original function object.
    monkeypatch.setattr(corpus_run.outcomes, "gate_bench_record", spy)
    _script(monkeypatch, _ok_row)

    path = tmp_path / "b.json"
    path.write_text(json.dumps(_real_baseline()), encoding="utf-8")
    corpus_run.main(["--substrate", "gitea", "--baseline", str(path)])
    assert seen.get("baseline") is not None, (
        "`--baseline` was accepted and never reached `gate_bench_record`, so the cost, rate and "
        "flip channels stayed off and the run would print PASS having compared against nothing"
    )
    assert seen["baseline"]["bench"] == "customer-gitea"
