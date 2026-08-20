"""Every LLM boundary crossing a bench run makes, COUNTED PER SITE, with cost read where it is metered.

WHAT THIS IS FOR. The customer benchmark's headline is a pair of availability numbers with cost beside
them, and "cost" has to come from somewhere trustworthy. The tempting source is `RunRecord.usage` —
and it is the wrong one: the record is a REPORT, assembled by the code under test, so reading cost
from it means the benchmark's cost column is only as honest as the thing it is benchmarking. Cost is
metered in exactly one place, `Router.complete` (`src/ultracua/llm/base.py:75`), which accumulates each
response's usage into `Router.totals` priced by the model that ANSWERED. This ledger reads it there.

WHY IT COUNTS RATHER THAN REFUSES, and this correction is already in `docs/reshape-plan.md`. The
obvious instrument is a `refuse` mode that makes any provider construction raise, so a replay proving
"0 LLM calls" is proved by construction. That is over-refusal INSIDE the instrument: a data replay
legitimately extracts once, so a blanket refusal buckets every extracting read as `llm_reached` and
the bench would report a real capability as a violation. So the ledger counts, per call SITE, and lets
the scenario say what it expects. `tests/test_inviolable_properties.py` keeps the refusing fixture for
the inviolable itself, which is a different question asked of different runs.

WHY THE BINDINGS ARE DERIVED. `flows.py` does `from .providers import build_router, get_provider`,
which binds those names at import time — so wrapping `ultracua.providers.build_router` does NOT reach
`ultracua.flows.build_router`, the one `replay()` actually calls. That is not hypothetical: S14's
`no_llm` fixture claimed to make an LLM unreachable in both directions and did not, and a later
version of the same fixture still missed `llm.build_client` — the factory the other two CALL — while
25 cells passed and a corpus cell printed "0 reached an LLM" over 105 real Anthropic clients.

So the binding set is computed from the live import graph, and this module is where that computation
lives ONCE. `tests/test_inviolable_properties.py` imports it rather than keeping a second copy: two
derivations of one fact is how the fact drifts, and the root `conftest.py` makes `benchmarks`
importable from a test precisely so shared machinery can live on this side.

ZERO IS NOT THE SAME AS "NOTHING SPENT", AND THE FIRST DRAFT OF THIS FILE CLAIMED IT WAS. It said a
router built outside the ledger was "invisible here — which is correct for 'did this run reach an
LLM', because such a router's spend still lands in the `totals` this ledger reads". That sentence was
FALSE and it was the whole hazard in one line. MEASURED: a `Router` built before `__enter__` and
awaited inside reports `router.totals.calls == 1, input_tokens == 1000` while the ledger reported
`calls=0, input_tokens=0, reached_an_llm() == False`. `usage()` sums `self.routers`, and `_wrap` only
ever adds a router a WRAPPED FACTORY returned.

`docs/reshape-plan.md:266` forbids exactly that, in the document this module implements: *"Do not
delete the explicit Unknown accounting state or claim 'zero recorded = zero spent' from an ambient
ledger while the SDK choke-point pin leaks."* It leaks in two named places — `vision.py`'s
`AnthropicGrounding` drives `AsyncAnthropic` directly, and `llm/gemini.py`'s `genai.Client()` is not
matched by `_SDK_CTORS` at all.

So this ledger reports a TRI-STATE, copied from `RouterWatch` (`src/ultracua/obs.py:214`), which
solved the same problem for the run record and whose docstring says it plainly: reporting a flat zero
when some spending router was never observed "would be a CONFIDENT WRONG NUMBER". `observed` is
evaluated ON READ, never latched at construction, because a spender can set `accounting_failed`
mid-run. `reached_an_llm()` returns True, False, or **None** — and only a run that could see every
router it could have used is allowed to say False.

WHAT IT STILL DOES NOT CLAIM. `by_site()` is a map of CONSTRUCTION and `usage()` a map of SPEND; they
answer different questions and the bench reports both. Construction seen through a factory is exact;
spend is exact only when `observed` is True.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field

# The three factories through which an LLM client can come into existence. The NAMES are hand-written
# and that is the residual — the modules below are derived, these are not. It is closed one instrument
# over by `test_llm_client_construction_has_a_single_choke_point`, which pins by AST that SDK clients
# are constructed only inside the leaf adapters, making `build_client` provably the choke point that
# the other two must reach. A shrunken tuple here goes red there rather than quietly covering less.
FACTORIES = ("build_router", "get_provider", "build_client")


def provider_bindings(ensure_imported: bool = True) -> "list[tuple[object, str]]":
    """`[(module, attr), …]` — every module-level NAME through which an LLM client can be built.

    Derived from `sys.modules`, so a consumer module written tomorrow is covered the day it is
    written. Only `ultracua.*` modules are scanned: a test or a bench holding its own reference is not
    a production boundary, and wrapping one would make this instrument measure itself.

    `ensure_imported` is load-bearing and easy to leave out — the first draft of this module did. A
    scan of `sys.modules` sees only what is ALREADY loaded, so a consumer imported lazily after the
    ledger was entered keeps its unwrapped binding and the run reports a confident ZERO over a
    boundary nobody watched. Importing the known consumers first makes the scan's answer independent
    of what the caller happened to touch beforehand.
    """
    if ensure_imported:
        # Every module that could hold a binding. A LIST, and therefore the same shape this module
        # warns about elsewhere — but a bounded one: a module absent here is still scanned once
        # anything imports it, so the failure mode is "covered late", not "never covered".
        import ultracua.cli  # noqa: F401
        import ultracua.daemon.server  # noqa: F401
        import ultracua.flow  # noqa: F401
        import ultracua.flows  # noqa: F401
        import ultracua.llm  # noqa: F401
        import ultracua.providers  # noqa: F401

    out = []
    for name, mod in list(sys.modules.items()):
        if not name.startswith("ultracua") or mod is None:
            continue
        for attr in FACTORIES:
            if callable(getattr(mod, attr, None)):
                out.append((mod, attr))
    return out


@dataclass
class Crossing:
    """One factory call: where it happened and what it was asked for."""

    site: str
    factory: str
    argument: str


@dataclass
class BoundaryLedger:
    """Context manager. Wraps every derived binding, counts crossings, reads spend from the routers.

        with BoundaryLedger() as ledger:
            await flows.replay(...)
        ledger.crossings      # [Crossing(site='ultracua.flows', ...), …]
        ledger.by_site()      # {'ultracua.flows.build_router': 1}
        ledger.usage()        # UsageTotals summed over every router this run constructed

    Re-entrant use is refused rather than silently nesting: two ledgers over one call would each see
    the other's wrapper as the binding and unwinding them in the wrong order would leave a wrapper
    installed permanently.
    """

    crossings: list = field(default_factory=list)
    routers: list = field(default_factory=list)
    unobserved: list = field(default_factory=list)
    _saved: list = field(default_factory=list)
    _active: bool = False

    def __enter__(self) -> "BoundaryLedger":
        if self._active:
            raise RuntimeError("BoundaryLedger is already active; nesting would leak a wrapper")
        bindings = provider_bindings()
        if not bindings:
            raise RuntimeError(
                "no provider factory bindings found — `ultracua` is not imported yet, so this ledger "
                "would report a confident ZERO over an unwrapped boundary. Import what you are about "
                "to run first."
            )
        for mod, attr in bindings:
            original = getattr(mod, attr)
            self._saved.append((mod, attr, original))
            setattr(mod, attr, self._wrap(mod.__name__, attr, original))
        self._active = True
        return self

    def __exit__(self, *exc) -> None:
        for mod, attr, original in reversed(self._saved):
            setattr(mod, attr, original)
        self._saved.clear()
        self._active = False

    def _wrap(self, module_name: str, attr: str, original):
        ledger = self

        def wrapper(*args, **kwargs):
            ledger.crossings.append(Crossing(
                site=f"{module_name}.{attr}", factory=attr,
                argument=str(args[0]) if args else "",
            ))
            made = original(*args, **kwargs)
            # isinstance, NOT a duck-typed `.totals` probe. `obs.py:99-104` records why: a test hands
            # replay a provider that RAISES on any attribute access it does not permit, and poking a
            # foreign object for an attribute is the shape inviolable #1 forbids. Asking the real
            # class is safe; asking an unknown object what attributes it has is not.
            from ultracua.llm.base import Router
            if isinstance(made, Router):
                ledger.routers.append(made)
            return made

        wrapper.__name__ = getattr(original, "__name__", attr)
        wrapper.__doc__ = getattr(original, "__doc__", None)
        wrapper.__wrapped__ = original
        return wrapper

    # -- views -------------------------------------------------------------------------------------

    def by_site(self) -> dict:
        """`{'ultracua.flows.build_router': 2, …}` — construction, per call site.

        The per-SITE split is what makes "0-LLM" checkable without refusing: a replay that legitimately
        extracts once shows one crossing at a known site, where a blanket refusal would have shown a
        violation.
        """
        out: dict = {}
        for c in self.crossings:
            out[c.site] = out.get(c.site, 0) + 1
        return out

    def usage(self):
        """Spend, summed over every router this run constructed, read where `Router.complete` meters it.

        Returns a fresh `UsageTotals`; never mutates a router's own. `accounting_failed` is carried
        forward if ANY router set it, because a partial accounting must not report as a clean zero.
        """
        from ultracua.obs import UsageTotals

        total = UsageTotals()
        for r in self.routers:
            t = r.totals
            total.input_tokens += t.input_tokens
            total.output_tokens += t.output_tokens
            total.cache_read_tokens += t.cache_read_tokens
            total.cache_write_tokens += t.cache_write_tokens
            total.calls += t.calls
            total.accounting_failed = total.accounting_failed or t.accounting_failed
            for model, row in t.per_model.items():
                prev = total.per_model.get(model, (0, 0, 0, 0, 0))
                total.per_model[model] = tuple(a + b for a, b in zip(prev, row))
        return total

    def mark_unobserved(self, reason: str) -> None:
        """Declare that some LLM-capable path in this run is not being watched."""
        if reason not in self.unobserved:
            self.unobserved.append(reason)

    @property
    def observed(self) -> bool:
        """Could this ledger see every router the run could have used? Evaluated ON READ.

        Never latched at construction, and `RouterWatch` records why in its own words: the first two
        attempts at that pattern "got the timing wrong in the same way" — a spender sets
        `accounting_failed` while the run is happening, and a check in `__init__` therefore could not
        fire, so the guard was inert while its commit message claimed it had a caller. Anything that
        can become true mid-run must be asked for at report time.
        """
        return not (self.unobserved or any(r.totals.accounting_failed for r in self.routers))

    def reached_an_llm(self):
        """True / False / **None**, and the None is the point.

        Keyed on SPEND rather than construction: building a router and never calling it is not
        reaching an LLM, and `mode="replay"` nulls the provider at `flow.py:171` precisely so that can
        happen — counting construction as a violation is the over-refusal this module exists to avoid.

        But a False that the ledger could not actually see is worse than no answer, so an unobserved
        run returns None and the caller reports UNKNOWN. Only a run that could see every router it
        could have used is allowed to claim zero.
        """
        if self.usage().calls > 0:
            return True
        return False if self.observed else None
