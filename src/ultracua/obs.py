"""Observability: a library logger + LLM usage/cost accounting.

The library logs through the `ultracua` logger, which carries a NullHandler by default so
importing ultracua never spams output. The CLI/daemon call `configure_logging()` to attach a
real handler. Every log record carries the current `run_id` (a contextvar set per run_cached
call) so a scheduled job's logs can be traced end-to-end.

`UsageTotals` accumulates token usage across LLM calls (the Router feeds it) and estimates the
dollar cost — surfaced in logs and `FlowReport.extra["usage"]` so an unattended run is auditable.
"""

from __future__ import annotations

import contextvars
import logging
from dataclasses import dataclass, field
from typing import Optional

# --- logging ----------------------------------------------------------------------------------
_ROOT = logging.getLogger("ultracua")
_ROOT.addHandler(logging.NullHandler())  # library default: never emit unless a handler is attached

run_id_var: contextvars.ContextVar[str] = contextvars.ContextVar("ultracua_run_id", default="-")


def new_run_id() -> str:
    """Mint + install a short run id for the current context (returns it)."""
    import uuid

    rid = uuid.uuid4().hex[:8]
    run_id_var.set(rid)
    return rid


class _RunIdFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.run_id = run_id_var.get()
        return True


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger("ultracua." + name)


def configure_logging(level: "str | int" = "INFO", *, stream=None) -> None:
    """Attach a stderr handler to the `ultracua` logger (idempotent). Safe to call repeatedly."""
    lvl = logging.getLevelName(level) if isinstance(level, str) else level
    _ROOT.setLevel(lvl)
    if any(not isinstance(h, logging.NullHandler) for h in _ROOT.handlers):
        return  # already configured
    handler = logging.StreamHandler(stream)
    handler.addFilter(_RunIdFilter())
    handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)s %(name)s [%(run_id)s] %(message)s", "%H:%M:%S")
    )
    _ROOT.addHandler(handler)


# --- LLM usage + cost -------------------------------------------------------------------------
# $ per 1M tokens (input, output) for the known models; cache tokens are approximated at the
# input rate (a small over-estimate for cache reads — fine for a cost ceiling, not billing).
_PRICES = {
    "claude-opus-4-8": (5.0, 25.0),
    "claude-opus-4-7": (5.0, 25.0),
    "claude-opus-4-6": (5.0, 25.0),
    "claude-sonnet-4-6": (3.0, 15.0),
    "claude-haiku-4-5": (1.0, 5.0),
    "claude-fable-5": (10.0, 50.0),
}


def _price(model: str) -> Optional[tuple]:
    for prefix, p in _PRICES.items():
        if model and model.startswith(prefix):
            return p
    return None


@dataclass
class UsageTotals:
    """Token/cost accounting for a set of LLM calls.

    Costs are accumulated PER RESPONSE MODEL, not priced once at a call site's configured model.
    A single run routinely spends at two prices — the fast tier answers most steps and escalates
    to the strong tier when unsure (`providers/llm_agent.py`) — and the tiers here are 5x apart,
    so pricing the whole run at either one is wrong by a wide margin in whichever direction the
    caller happened to pass. The flat token counters are kept beside the buckets because every
    existing reader (baselines, `variance.py`) indexes them by name.
    """

    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    calls: int = 0
    # model -> (input, output, cache_read, cache_write, calls). Keyed by what the RESPONSE said
    # served the request, so an escalation is priced at the strong tier and the rest is not.
    per_model: dict = field(default_factory=dict)
    # HOW MANY TIMES a spender tried to record its own usage and could not (see `vision.py`). It
    # rides HERE rather than on the spending object because `RouterWatch` must never probe a foreign
    # attribute: `test_a_navigate_only_replay_never_reaches_a_provider` hands replay a provider that
    # raises on any attribute access it does not explicitly permit, and inviolable #1 is exactly
    # that — a replay does not poke at a provider. Reading a field of our own dataclass is safe.
    #
    # A COUNTER, NOT A FLAG, AND THAT IS THE WHOLE OF R4.53's SECOND HALF. Every other field here is
    # monotonic and made run-scoped by `since()`; this one was a sticky bool, so a single failure
    # answered "did accounting fail during THIS run?" with yes forever after. A watch built for a
    # later run over the same owner reported `observed=False` having seen nothing go wrong — and a
    # bool cannot be deltaed out of that, because two consecutive failures leave it True both times.
    # Counting is what makes the question answerable per run.
    accounting_failures: int = 0

    @property
    def accounting_failed(self) -> bool:
        """READ-ONLY. Every existing reader keeps working; the four writers say `+= 1` instead.

        Deliberately not a settable property that translates `= True` into `+= 1`: one token
        meaning two things is the shape this register keeps re-filing, and a writer that means
        "another failure" should have to say so.
        """
        return self.accounting_failures > 0

    @staticmethod
    def cannot_spend() -> "UsageTotals":
        """THE DECLARATION A KEY-LESS OWNER MAKES: an accounting object that will never grow.

        `RouterWatch` classifies an owner by whether a `UsageTotals` is reachable at
        `owner.router.totals` or `owner.totals`. Before 1.3 that was a two-way split — watched, or
        an unwatched SPENDER — and every key-less teacher (`ScriptedProvider`, `MockProvider`,
        `MockGrounding`, the oracle teacher) landed in the second bucket. Measured:
        `UsageTotals.observe(ScriptedProvider([]))` reported `cost_usd: None,
        unobserved_llm_path: True`, over the entire population the key-less suite and `drift_bench`
        are built from, while `flow.py`'s own comment said the opposite — "a key-less learn
        (scripted teacher, no router) must SAY it spent nothing rather than stay silent about it".

        The third state is declared, not inferred, and it is declared THROUGH THE SAME ATTRIBUTE the
        watch already reads. That is not a stylistic choice: commit `00888b4` added a `spends`-style
        probe on the owner and tripped inviolable #1's tripwire, because a replay does not poke at a
        provider. An owner that exposes an empty `UsageTotals` is saying "I account for myself, and
        the answer is zero" — which is a fact about itself, offered, not extracted.

        So the absence of a `UsageTotals` now means exactly one thing: an owner that could spend and
        is not being watched. `tests/test_run_record.py`'s `_Spender` is that residual, and it must
        stay unobserved.
        """
        return UsageTotals()

    def add(self, usage, model: str = "") -> None:
        """Accumulate one response's Usage (duck-typed; tolerant of None / missing fields)."""
        if usage is None:
            return
        i = getattr(usage, "input_tokens", 0) or 0
        o = getattr(usage, "output_tokens", 0) or 0
        cr = getattr(usage, "cache_read_tokens", 0) or 0
        cw = getattr(usage, "cache_write_tokens", 0) or 0
        self.input_tokens += i
        self.output_tokens += o
        self.cache_read_tokens += cr
        self.cache_write_tokens += cw
        self.calls += 1
        prev = self.per_model.get(model, (0, 0, 0, 0, 0))
        self.per_model[model] = (prev[0] + i, prev[1] + o, prev[2] + cr, prev[3] + cw, prev[4] + 1)

    def snapshot(self) -> tuple:
        # Elements 6 and 7 are additive: every existing reader indexes [0:5] positionally, and both
        # later ones are read behind a length guard. A snapshot is opaque to every caller outside
        # this class — they take one and hand it back to `since()` — so growing it is safe.
        return (self.input_tokens, self.output_tokens, self.cache_read_tokens,
                self.cache_write_tokens, self.calls, dict(self.per_model),
                self.accounting_failures)

    def since(self, snap: tuple) -> "UsageTotals":
        """A delta UsageTotals = self minus an earlier snapshot() (for per-run scoping)."""
        was = snap[5] if len(snap) > 5 else {}
        delta = {}
        for m, cur in self.per_model.items():
            old = was.get(m, (0, 0, 0, 0, 0))
            d = tuple(cur[k] - old[k] for k in range(5))
            if d[4]:
                delta[m] = d
        return UsageTotals(
            input_tokens=self.input_tokens - snap[0], output_tokens=self.output_tokens - snap[1],
            cache_read_tokens=self.cache_read_tokens - snap[2],
            cache_write_tokens=self.cache_write_tokens - snap[3], calls=self.calls - snap[4],
            per_model=delta,
            # DELTA, not the running total: a delta describing one run must not carry a failure
            # that happened in an earlier one.
            accounting_failures=self.accounting_failures - (snap[6] if len(snap) > 6 else 0),
        )

    def unpriced_calls(self, fallback_model: str = "") -> int:
        """Calls whose serving model has no price entry — their cost is UNKNOWN, never zero."""
        n = 0
        for m, v in self.per_model.items():
            if _price(m) is None and not (not m and fallback_model and _price(fallback_model)):
                n += v[4]
        return n

    def cost_usd(self, model: str = "") -> Optional[float]:
        """Total $ across every serving model. `model` is only a FALLBACK price, used for calls
        recorded before per-model accounting existed (an old snapshot delta) or by a client that
        did not report one. Returns None when any spend cannot be priced — an unknown bill must
        never render as 0.0, which would understate it silently."""
        if self.per_model:
            total = 0.0
            for m, (i, o, cr, cw, _c) in self.per_model.items():
                # `not m` is load-bearing. The fallback is for a response that reported NO model;
                # applying it to a model that WAS reported but is simply absent from `_PRICES`
                # invents a number. `_PRICES` is Anthropic-only, so every OpenAI/Gemini id and
                # every future Claude id lands here — and all three production call sites pass
                # `settings.model`, which is always priced. Without this condition the "unknown
                # cost is never 0.0" guarantee is unreachable on every default path, and a GPT run
                # silently bills at Opus rates.
                p = _price(m) or (_price(model) if (not m and model) else None)
                if p is None:
                    return None
                total += ((i + cr + cw) * p[0] + o * p[1]) / 1_000_000
            return total
        if not self.calls and not (self.input_tokens or self.output_tokens):
            return 0.0                      # nothing was spent — say so, rather than "unknown"
        p = _price(model)
        if p is None:
            return None
        billed_in = self.input_tokens + self.cache_read_tokens + self.cache_write_tokens
        return (billed_in * p[0] + self.output_tokens * p[1]) / 1_000_000

    def as_dict(self, model: str = "") -> dict:
        """`cost_usd` is ALWAYS present. Absent and zero are different claims — absent reads as
        "nobody looked", and that ambiguity is what made a 0-LLM replay's cost unfalsifiable."""
        d = {
            "calls": self.calls,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "cache_read_tokens": self.cache_read_tokens,
            "cache_write_tokens": self.cache_write_tokens,
        }
        cost = self.cost_usd(model)
        d["cost_usd"] = round(cost, 6) if cost is not None else None
        unpriced = self.unpriced_calls(model)
        if unpriced:
            d["cost_unpriced_calls"] = unpriced
        if self.per_model:
            d["by_model"] = {m: v[4] for m, v in sorted(self.per_model.items())}
        return d

    @staticmethod
    def observe(*owners) -> "RouterWatch":
        """Watch every distinct Router behind `owners` (a provider, a Router, or None) and report
        their combined delta. Deduplicated BY IDENTITY, because the agent provider and the
        extraction router are frequently the same object (`flows._router` returns
        `provider.router` when it exists) and counting it twice would double every token while
        every existing test stayed green."""
        return RouterWatch(owners)

    def summary(self, model: str = "") -> str:
        cost = self.cost_usd(model) if model else None
        tail = f", ~${cost:.4f}" if cost is not None else ""
        return (f"{self.calls} call(s), in={self.input_tokens} out={self.output_tokens} "
                f"cache_r={self.cache_read_tokens}{tail}")


class RouterWatch:
    """Run-scoped usage accounting across EVERY router a single run can touch.

    A run holds up to three: the agent provider's, the extraction-only one that `flows.py` builds
    for a data replay, and (out of band) the audit judge's. The agent's is None on `mode="replay"`
    because the heal provider is nulled, so a router-of-the-provider snapshot sees nothing while a
    data replay is genuinely spending money on extraction.

    That distinction is why `complete()` exists. Reporting a flat zero when some spending router
    was never observed would be a CONFIDENT WRONG NUMBER, which is worse than the absent key this
    replaced — so an unobserved path yields `observed=False` and the caller reports unknown, not
    zero. Only a run that could see every router it could have used is allowed to claim zero.

    THREE STATES IN THE WORLD, TWO IN THE REPORT (1.3). An owner is either

      * WATCHED — a `UsageTotals` is reachable at `owner.router.totals` or `owner.totals`, and its
        delta is this run's spend;
      * CANNOT SPEND — it declares an empty `UsageTotals` via `UsageTotals.cannot_spend()`, so it
        is watched and contributes a real zero. That is the key-less teacher population;
      * an UNWATCHED SPENDER — no `UsageTotals` anywhere, so nothing here can say what it spent.

    Only the third makes the run unobserved. Before 1.3 the second and third were the same bucket,
    which meant every key-less run in the suite and in `drift_bench` reported cost UNKNOWN (R4.53).
    Two of those states report identically on purpose: a declared zero and a measured zero are both
    zero, and inventing a third word for the report would be a distinction with no consumer.

    THE DECLARATION IS OFFERED, NEVER EXTRACTED. Exactly two attributes are read off an owner,
    `router` and `totals`, and that is the entire contract — see `UsageTotals.cannot_spend` for why
    a probe for anything else is forbidden rather than merely discouraged.
    """

    def __init__(self, owners) -> None:
        self._pairs = []          # [(totals, snapshot)] — identity-deduped
        self._unobserved = False
        seen = set()
        for o in owners:
            if o is None:
                continue
            r = getattr(o, "router", o)
            t = getattr(r, "totals", None)
            if not isinstance(t, UsageTotals):
                # An owner that could spend but exposes no totals. The population is now exactly
                # that — a key-less owner declares `UsageTotals.cannot_spend()` and lands above —
                # so this branch no longer swallows every scripted teacher along with the vision
                # grounding object it was written for. Do not silently drop it.
                self._unobserved = True
                continue
            if id(t) in seen:
                continue
            seen.add(id(t))
            self._pairs.append((t, t.snapshot()))

    def mark_unobserved(self) -> None:
        """Declare that some LLM-capable path in this run is not being watched."""
        self._unobserved = True

    @property
    def observed(self) -> bool:
        """Evaluated on READ, never latched at construction.

        The first two attempts at this got the timing wrong in the same way: a spender records an
        accounting failure while the run is happening (`vision.py`, inside `decide()`), and the
        watch is built before the run starts. A check in `__init__` therefore could not fire, so
        the guard was inert while its commit message claimed it had a caller. Anything that can
        become true mid-run must be asked for at report time.

        AND IT IS THE DELTA, NOT THE RUNNING TOTAL (R4.53). Reading the absolute count answers "has
        accounting EVER failed on this object", which is a different question and a sticky one: a
        caller that reuses a grounding or a provider across runs got `observed=False` for the rest
        of the process on the strength of one earlier failure. `since()` is what makes every other
        field on `UsageTotals` run-scoped; this now reads the same way.
        """
        if self._unobserved:
            return False
        return not any(t.accounting_failures > (snap[6] if len(snap) > 6 else 0)
                       for t, snap in self._pairs)

    def delta(self) -> UsageTotals:
        out = UsageTotals()
        for totals, snap in self._pairs:
            d = totals.since(snap)
            out.input_tokens += d.input_tokens
            out.output_tokens += d.output_tokens
            out.cache_read_tokens += d.cache_read_tokens
            out.cache_write_tokens += d.cache_write_tokens
            out.calls += d.calls
            for m, v in d.per_model.items():
                prev = out.per_model.get(m, (0, 0, 0, 0, 0))
                out.per_model[m] = tuple(prev[k] + v[k] for k in range(5))
        return out

    def summary(self, model: str = "") -> str:
        d = self.as_dict(model)
        cost = d.get("cost_usd")
        tail = f", ~${cost:.4f}" if cost is not None else ", cost UNKNOWN"
        return (f"{d['calls']} call(s), in={d['input_tokens']} out={d['output_tokens']} "
                f"cache_r={d['cache_read_tokens']}{tail}")

    def as_dict(self, model: str = "") -> dict:
        d = self.delta().as_dict(model)
        if not self.observed:
            # The honest shape: we cannot claim zero, and we must not imply we can.
            d["cost_usd"] = None
            d["unobserved_llm_path"] = True
        return d
