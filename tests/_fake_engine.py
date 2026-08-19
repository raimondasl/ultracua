"""A scripted stand-in for the engine, so `flows.replay()`'s exits can be driven in milliseconds.

WHY THIS EXISTS. `replay()` and `_attempt_replay` are ~490 lines with no page in them, and they are
where seven of PR #165's ten confirmed defects lived (R4.44-R4.53). Reaching them costs a Chromium
session today, so the whole surface has exactly TWO cells that pass `record=` — and eleven mutations of
the record plumbing survive the entire suite. This makes every exit reachable without a browser, which
is what lets an exit-set matrix exist at all.

WHY THERE IS NO `engine=` PARAMETER IN `src/`. Monkeypatching the bindings `flows.py` ALREADY HOLDS
reaches every call (`flows.py:41` does `from .flow import run_cached`, and the rest are module globals
looked up at call time). A `deps`/`engine` default argument would be ~150 fix-shaped lines on the
hottest file in the repo, and a def-time default binds the FUNCTION OBJECT, which would make the 27
existing `monkeypatch.setattr(flows_mod, ...)` sites inert. All three design judges refuted the seam
independently; the pattern used here is the one `tests/test_inviolable_properties.py:565-574` already
documents.

WHAT IT CAN AND CANNOT TELL YOU. It reproduces the engine's CONTRACT (what `run_cached` returns or
raises, and what `finalize`/`pre_write` write into `out`), not the engine. It cannot see a page-side
timing change, so it does not replace `drift_bench` or the browser write-safety matrix — and its
fidelity to the real contract is asserted by cells that run the REAL engine and diff the `out` it
produces, rather than by trusting this docstring.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

from ultracua import flows as flows_mod
from ultracua.flow import FlowReport
from ultracua.timing import StepTrace

# Every key the real `_make_finalize` / `_make_pre_write` can put in `out`, derived from the source
# rather than remembered. `_attempt_replay` reads the first eight; `pin`/`pinned` are consumed
# elsewhere. A fidelity cell asserts this set still matches the engine.
FINALIZE_KEYS = ("confirm_pre_true", "data", "error", "extract_error", "extract_found", "found",
                 "pin", "pinned", "truncated", "write_landed")
PRE_WRITE_KEYS = ("_pre_confirm",)


@dataclass
class Attempt:
    """One scripted engine outcome: what `run_cached` does, and what `finalize` wrote before it did."""

    report: Optional[FlowReport] = None
    raises: Optional[BaseException] = None
    out: dict = field(default_factory=dict)

    def __post_init__(self) -> None:
        if (self.report is None) == (self.raises is None):
            raise ValueError("an Attempt scripts exactly one of `report` or `raises`")
        bad = set(self.out) - set(FINALIZE_KEYS) - set(PRE_WRITE_KEYS)
        if bad:
            raise ValueError(f"`out` keys the real engine never writes: {sorted(bad)}")


def trace(index: int = 0, ms: float = 12.5, **meta) -> StepTrace:
    """One StepTrace with a real duration, so a cell can script `report.total_ms`.

    `FlowReport.total_ms` is a DERIVED property (`sum(t.total_ms for t in self.traces)`) and
    `StepTrace.total_ms` derives from its spans — neither is settable. That is why the hole existed:
    with no cell scripting a span, `report.total_ms` was 0.0 everywhere, and dropping
    `record.total_ms += report.total_ms` was invisible to the whole matrix.
    """
    st = StepTrace(index=index, meta=dict(meta))
    st.add("act", ms)
    return st


def report(mode: str = "replay", success: bool = True, *, usage: Optional[dict] = None,
           llm_calls: int = 0, note: str = "", traces: Optional[list] = None,
           healed_steps: int = 0) -> FlowReport:
    """A FlowReport shaped like the engine's. `usage=None` reproduces the exits that omit it (R4.45).

    `healed_steps` is scriptable, and durations come from `trace()` above, because the population block
    reads both and NOTHING covered them: measured at 1.5's first step, mutations dropping
    `record.total_ms +=` and `record.traces.extend(...)` both SURVIVED the whole exit matrix while the
    usage absorb beside them was killed. A helper that cannot express a field is a hole in every cell
    that would have used it.
    """
    extra: dict = {} if usage is None else {"usage": usage}
    return FlowReport(mode=mode, success=success, traces=list(traces or []), llm_calls=llm_calls,
                      note=note, extra=extra, healed_steps=healed_steps)


class FakeEngine:
    """Scripts a sequence of attempts and installs itself over the bindings `flows.py` holds."""

    def __init__(self, *attempts: Attempt, precheck: Any = False,
                 refresh: Any = None, learn: Any = None) -> None:
        self.attempts = list(attempts)
        self.calls: list[dict] = []          # one entry per run_cached call, for premise counting
        self._i = -1
        self._precheck = precheck            # bool, list[bool], or an exception to raise
        self._refresh = refresh              # None (ok) or an exception to raise
        self._learn = learn                  # a LearnResult, or an exception to raise
        self.precheck_calls = 0
        self.refresh_calls = 0
        self.learn_calls = 0

    # -- the attempt the engine is currently serving ------------------------------------------------
    @property
    def current(self) -> Optional[Attempt]:
        return self.attempts[self._i] if 0 <= self._i < len(self.attempts) else None

    def install(self, monkeypatch) -> "FakeEngine":
        monkeypatch.setattr(flows_mod, "run_cached", self._run_cached)
        monkeypatch.setattr(flows_mod, "_precheck_done", self._precheck_done)
        monkeypatch.setattr(flows_mod, "refresh_auth", self._refresh_auth)
        monkeypatch.setattr(flows_mod, "learn", self._learn_fn)
        monkeypatch.setattr(flows_mod, "_make_finalize", self._make_finalize)
        monkeypatch.setattr(flows_mod, "_make_pre_write", self._make_pre_write)
        return self

    # -- the six stand-ins ---------------------------------------------------------------------------
    async def _run_cached(self, **kw) -> FlowReport:
        self.calls.append(kw)
        self._i += 1
        att = self.current
        if att is None:
            raise AssertionError(
                f"the engine was called {len(self.calls)} time(s) but only {len(self.attempts)} "
                f"attempt(s) were scripted — the cell reached an exit it did not intend")
        fin = kw.get("finalize")
        if fin is not None:
            await fin(None)                  # populate `out` exactly where the real engine does
        if att.raises is not None:
            raise att.raises
        return att.report

    def _make_finalize(self, spec, router, out, pin=None, redact=()):
        async def _fin(_session) -> None:
            att = self.current
            if att is not None:
                out.update(att.out)
        return _fin

    def _make_pre_write(self, spec, out):
        return None                          # the whole-flow baseline is scripted via `out` directly

    async def _precheck_done(self, spec) -> bool:
        self.precheck_calls += 1
        p = self._precheck
        if isinstance(p, BaseException):
            raise p
        if isinstance(p, list):
            return p[min(self.precheck_calls - 1, len(p) - 1)]
        return bool(p)

    async def _refresh_auth(self, spec, *, headless=None) -> None:
        self.refresh_calls += 1
        if isinstance(self._refresh, BaseException):
            raise self._refresh

    async def _learn_fn(self, spec, **kw):
        self.learn_calls += 1
        if isinstance(self._learn, BaseException):
            raise self._learn
        if self._learn is None:
            raise AssertionError("learn() was reached but the cell scripted no LearnResult")
        return self._learn
