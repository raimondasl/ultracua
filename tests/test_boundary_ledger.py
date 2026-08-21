"""The bench's cost instrument: it must see every boundary, and must not refuse a legitimate one.

The customer benchmark reports cost beside its availability numbers, and the whole value of that
column depends on where the number comes from. `RunRecord.usage` is the wrong place — it is a REPORT
assembled by the code under test, so a bench reading it grades the subject on its own answer sheet.
`benchmarks/boundary_ledger.py` reads spend where `Router.complete` meters it and counts construction
where it happens.

Two failure directions, and this repo has shipped the first one twice:

  * TOO NARROW — a binding the scan never knew about. S14's `no_llm` fixture claimed an LLM was
    unreachable in both directions while `flows.build_router` ran the real code; its successor still
    missed `llm.build_client`, the factory the other two CALL, and a replay built 105 real Anthropic
    clients with 25 cells green.
  * TOO BROAD — refusing construction outright. A data replay legitimately extracts once, so a
    blanket refusal buckets a real capability as a violation. That is D0's shape inside an
    instrument, and `docs/reshape-plan.md` records the correction before the code existed.
"""

from __future__ import annotations

import sys

import pytest

from benchmarks.boundary_ledger import FACTORIES, BoundaryLedger, provider_bindings


def test_the_scan_finds_the_bindings_that_have_actually_leaked() -> None:
    """Named by hand, because the loop that derives them cannot fail on its own.

    Asserting "every derived binding is wrapped" is a tautology over whatever the scan happened to
    find. These three are the ones that have MEASURABLY been missed — `flows.build_router` by S14's
    first fixture, `llm.build_client` by its second — so a scan that quietly stops covering them goes
    red here rather than reporting a smaller world confidently.
    """
    found = {f"{mod.__name__}.{attr}" for mod, attr in provider_bindings()}
    for required in ("ultracua.flows.build_router",       # S14, attempt 1
                     "ultracua.llm.build_client",          # S14, attempt 2 — what both others call
                     "ultracua.providers.build_router"):
        assert required in found, (
            f"{required} is not in the derived binding set, so a bench run through it would be "
            f"invisible and the ledger would report a confident zero. Found: {sorted(found)}")
    assert set(FACTORIES) == {"build_router", "get_provider", "build_client"}, (
        "the factory tuple changed; `test_llm_client_construction_has_a_single_choke_point` is what "
        "makes `build_client` provably sufficient, so re-check it before shrinking this")


def test_the_scan_does_not_depend_on_what_the_caller_imported_first() -> None:
    """`ensure_imported` is load-bearing, and the first draft of the module left it out.

    A scan of `sys.modules` sees only what is ALREADY loaded. MEASURED: with `ultracua.flows`,
    `.providers` and `.llm` imported beforehand the scan returns 5 bindings; from a cold graph, with
    the eager import, it returns 7 — `cli.get_provider` and `daemon.server.get_provider` as well. So
    without it a bench that never touched the CLI would wrap five of seven boundaries and report a
    zero it had not earned.
    """
    eager = {f"{m.__name__}.{a}" for m, a in provider_bindings(ensure_imported=True)}
    assert "ultracua.cli.get_provider" in eager, (
        "the eager import no longer reaches the CLI's binding, so the scan's answer is again a "
        "function of what the caller happened to import")
    assert len(eager) >= 7, f"only {len(eager)} bindings; the eager import list has shrunk"

    # ...and the lazy form is a strict subset, which is what makes the eager one worth having.
    lazy = {f"{m.__name__}.{a}" for m, a in provider_bindings(ensure_imported=False)}
    assert lazy <= eager


def test_the_ledger_wraps_and_unwraps_every_binding() -> None:
    """A wrapper left installed would meter every LATER run in the same process — including the
    suite's own — so the restore is asserted identity-by-identity rather than by count."""
    before = {(id(m), a): getattr(m, a) for m, a in provider_bindings()}
    with BoundaryLedger():
        during = {(id(m), a): getattr(m, a) for m, a in provider_bindings()}
        assert during, "no bindings were wrapped"
        for key, fn in during.items():
            assert getattr(fn, "__wrapped__", None) is before[key], f"{key} was not wrapped"
    after = {(id(m), a): getattr(m, a) for m, a in provider_bindings()}
    assert after == before, "a binding was not restored; every later run would be metered by a ghost"


def test_a_construction_is_counted_per_site_and_never_refused(monkeypatch) -> None:
    """THE CORRECTION `docs/reshape-plan.md` MADE BEFORE THIS CODE EXISTED.

    The instinctive instrument makes construction RAISE, so "0 LLM calls" is true by construction.
    That is over-refusal inside the measuring device: a data replay legitimately extracts once, and a
    blanket refusal reports that real capability as a violation. So the ledger counts, per site, and
    lets the scenario say what it expected.
    """
    import ultracua.providers as providers

    # The stub goes in BEFORE the ledger, so the ledger wraps IT — reassigning `wrapper.__wrapped__`
    # afterwards does not redirect anything, because the wrapper closed over the original. (Learned by
    # writing it the other way: the real factory ran and returned a live `LLMAgentProvider`.)
    sentinel = object()
    monkeypatch.setattr(providers, "get_provider", lambda name: sentinel)

    with BoundaryLedger() as ledger:
        got = providers.get_provider("anthropic")

    assert got is sentinel, "the wrapper must return what the factory returned, unchanged"
    assert ledger.by_site() == {"ultracua.providers.get_provider": 1}, (
        f"expected one crossing at the patched site; got {ledger.by_site()}")
    assert ledger.crossings[0].argument == "anthropic", "the ledger must record WHAT was asked for"
    assert ledger.reached_an_llm() is False, (
        "constructing a provider is not spending; only a router's own totals are")


def test_construction_without_spend_is_not_reaching_an_llm() -> None:
    """The direction that makes this an instrument rather than a gate.

    `mode="replay"` nulls the provider at `flow.py:171`, so a router can legitimately be built and
    never called. Counting construction as "reached an LLM" would report every such replay as a
    violation of inviolable #1 — which is exactly the false alarm the bench must not raise.
    """
    ledger = BoundaryLedger()
    assert ledger.reached_an_llm() is False, "an empty ledger claims a boundary crossing"
    assert ledger.usage().calls == 0


def test_spend_is_summed_from_the_routers_not_from_any_record() -> None:
    """Cost comes from where `Router.complete` meters it (`llm/base.py:75`), never from `RunRecord`.

    A `RunRecord` is assembled by the code under test; a bench that read its cost from there would be
    grading the subject on its own answer sheet. This drives real `UsageTotals` objects through the
    summation, including the `accounting_failed` flag — which must survive, because a partial
    accounting reported as a clean zero is the silent-wrong direction.
    """
    from ultracua.obs import UsageTotals

    class _FakeRouter:
        def __init__(self, totals):
            self.totals = totals

    a, b = UsageTotals(), UsageTotals()
    a.input_tokens, a.output_tokens, a.calls = 100, 10, 1
    a.per_model["fast"] = (100, 10, 0, 0, 1)
    b.input_tokens, b.output_tokens, b.calls = 200, 20, 2
    b.per_model["fast"] = (200, 20, 0, 0, 2)
    b.per_model["strong"] = (5, 1, 0, 0, 1)
    b.accounting_failed = True

    ledger = BoundaryLedger()
    ledger.routers.extend([_FakeRouter(a), _FakeRouter(b)])
    total = ledger.usage()

    assert (total.input_tokens, total.output_tokens, total.calls) == (300, 30, 3)
    assert total.per_model["fast"] == (300, 30, 0, 0, 3), "per-model must sum, not overwrite"
    assert total.per_model["strong"] == (5, 1, 0, 0, 1)
    assert total.accounting_failed is True, (
        "one router could not account for its own spend and the sum reported clean — a partial "
        "accounting must never present as a zero")
    assert ledger.reached_an_llm() is True

    # ...and the ledger must not have mutated the routers it read.
    assert a.input_tokens == 100 and b.calls == 2


def test_nesting_is_refused_rather_than_leaking_a_wrapper() -> None:
    """Two ledgers over one call would each capture the other's wrapper as "the binding", and
    unwinding them out of order leaves a wrapper installed for the life of the process."""
    with BoundaryLedger() as outer:
        with pytest.raises(RuntimeError, match="already active"):
            outer.__enter__()


def test_an_unimported_world_refuses_instead_of_reporting_zero(monkeypatch) -> None:
    """The confident-zero case, armed.

    If nothing is wrapped, every view still answers — `by_site()` returns `{}` and
    `reached_an_llm()` returns False — which reads exactly like a clean 0-LLM run. So an empty
    binding set must refuse at ENTRY rather than produce that answer.
    """
    monkeypatch.setattr("benchmarks.boundary_ledger.provider_bindings", lambda **_kw: [])
    with pytest.raises(RuntimeError, match="no provider factory bindings"):
        with BoundaryLedger():
            pass


def _router_with(**totals):
    """A real `Router` with only `.totals` populated — the only field the ledger reads."""
    from ultracua.llm.base import Router
    from ultracua.obs import UsageTotals
    r = Router.__new__(Router)
    r.totals = UsageTotals()
    for k, v in totals.items():
        setattr(r.totals, k, v)
    return r


def test_a_zero_the_ledger_could_not_see_is_UNKNOWN_not_false() -> None:
    """THE DEFECT THIS SLICE SHIPPED IN ITS FIRST DRAFT, and the reason the answer is a tri-state.

    MEASURED before the fix: a `Router` built BEFORE `__enter__` and awaited inside reported
    `router.totals.calls == 1, input_tokens == 1000` while the ledger reported `calls=0`,
    `input_tokens=0` and `reached_an_llm() == False`. `usage()` sums `self.routers`, which `_wrap`
    only populates for a router a WRAPPED FACTORY returned — so a router from anywhere else is
    invisible, and the module's own docstring asserted the opposite as a design fact.

    `docs/reshape-plan.md:266` forbids it in the document this implements: "Do not delete the explicit
    Unknown accounting state or claim 'zero recorded = zero spent' from an ambient ledger while the
    SDK choke-point pin leaks." `RouterWatch` calls the alternative "a CONFIDENT WRONG NUMBER".

    So: False means "I could see everything and there was nothing". None means "I could not see".
    """
    clean = BoundaryLedger()
    assert clean.reached_an_llm() is False, "a ledger that CAN see everything may say zero"
    assert clean.observed is True

    declared = BoundaryLedger()
    declared.mark_unobserved("vision drives the SDK directly (R4.41)")
    assert declared.observed is False
    assert declared.reached_an_llm() is None, (
        "an unwatched LLM-capable path reported as a confident False — the exact shape "
        "docs/reshape-plan.md:266 forbids")
    assert "vision" in declared.unobserved[0], "the reason must survive, or nobody can act on it"


def test_a_router_that_could_not_account_for_itself_makes_the_answer_unknown() -> None:
    """`accounting_failed` is set MID-RUN by a spender that could not record its own usage
    (`vision.py`), so `observed` is evaluated ON READ rather than latched.

    `RouterWatch`'s docstring records why in its own words: the first two attempts "got the timing
    wrong in the same way" — the check ran in `__init__`, before the flag could be set, so the guard
    was inert while its commit message claimed it had a caller.
    """
    ledger = BoundaryLedger()
    r = _router_with(accounting_failed=False)
    ledger.routers.append(r)
    assert ledger.observed is True and ledger.reached_an_llm() is False

    r.totals.accounting_failed = True          # set AFTER the ledger saw the router, as vision does
    assert ledger.observed is False, "the flag was latched at construction and could never fire"
    assert ledger.reached_an_llm() is None


def test_spend_still_reads_as_reached_even_when_unobserved() -> None:
    """Unknown is for the ZERO, never for a positive. If the ledger SAW spend, it saw spend —
    downgrading that to None would lose a fact it actually has."""
    ledger = BoundaryLedger()
    ledger.routers.append(_router_with(calls=3, input_tokens=10))
    ledger.mark_unobserved("some other path is unwatched")
    assert ledger.reached_an_llm() is True
