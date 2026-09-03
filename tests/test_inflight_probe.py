"""The in-flight probe's own arithmetic. (R4.146.)

`benchmarks/inflight_probe.py` produced the numbers that REFUTED the simple form of R4.146's
candidate sensor and kept a narrower one. A probe that miscounts would make both halves fiction, and
the failure would be silent -- a plausible number with nothing to contradict it.

The live half needs a browser or a substrate and is not here. What is here is the counting, the
once-only hook, and the refusal to spend money by accident.
"""

from __future__ import annotations

import inspect

import pytest

from benchmarks import inflight_probe as P


class _FakePage:
    """Records handlers the way Playwright's `page.on` does, so they can be fired in order."""

    def __init__(self):
        self.handlers = {}

    def on(self, event, fn):
        self.handlers.setdefault(event, []).append(fn)

    def fire(self, event, n=1):
        for _ in range(n):
            for fn in self.handlers.get(event, []):
                fn(object())


def test_the_count_rises_on_a_request_and_falls_on_EITHER_ending() -> None:
    """`requestfailed` must decrement as `requestfinished` does.

    Counting only successes leaks: a failed request would stay 'in flight' forever, so a page that
    had one would look permanently busy and the sensor would never say "nothing is coming". That is
    the direction that turns a cost saving into an unbounded wait.
    """
    page = _FakePage()
    P.hook(page)
    page.fire("request", 3)
    assert page._inflight == 3
    page.fire("requestfinished")
    page.fire("requestfailed")
    assert page._inflight == 1, "a FAILED request did not decrement the count"


def test_the_count_never_goes_negative() -> None:
    """A `requestfinished` for a request that started before the hook was installed is normal --
    the probe attaches mid-flight. It must floor at zero, or the sensor reads 'busy' as negative
    and every later comparison against 0 is wrong."""
    page = _FakePage()
    P.hook(page)
    page.fire("requestfinished", 2)
    assert page._inflight == 0


def test_hooking_twice_does_not_double_count() -> None:
    """The probe hooks on every retry entry, and a page sees many. Registering the handlers twice
    would count each request twice and inflate every reading in the artifact."""
    page = _FakePage()
    P.hook(page)
    P.hook(page)
    page.fire("request")
    assert page._inflight == 1, "the once-only guard is gone; every count is doubled"


@pytest.mark.parametrize("verdict,kind", [
    ("already-quiet:stalled:7", "stalled"),
    ("quiet:bound:2", "bound"),
    ("quiet:refused-at:2", "refused-at"),
    ("already-quiet:skipped", "skipped"),
    ("refused", "refused"),
])
def test_the_verdict_is_bucketed_by_its_OUTCOME_not_its_settle(verdict: str, kind: str) -> None:
    """`{settle}:{outcome}:{looks}` -- the first field is how the page settled and the second is what
    the poll did. Bucketing on the first would merge `already-quiet:stalled` with
    `already-quiet:skipped`, which are the expensive case and the free one."""
    rows = [{"verdict": verdict, "ms": 1, "inflight_at_entry": 0, "bound": False, "samples": []}]
    assert list(P.summarise(rows)) == [kind]


def test_a_busy_entry_is_counted_separately_from_a_busy_moment() -> None:
    """The finding turns on the ENTRY reading, because the live count is refuted mid-render.

    Measured: `drift_bench`'s 36 stalled rows read 0 at entry AND never rise; Odoo's render gap
    reads 1-2 at entry but returns to zero partway through. So the summary must report entry
    separately -- collapsing them into "was it ever busy" loses the only reading that discriminates.
    """
    rows = [
        {"verdict": "q:stalled:6", "ms": 600, "inflight_at_entry": 0, "bound": False, "samples": []},
        {"verdict": "q:stalled:6", "ms": 600, "inflight_at_entry": 2, "bound": False, "samples": []},
    ]
    s = P.summarise(rows)["stalled"]
    assert s == {"n": 2, "total_ms": 1200, "busy_at_entry": 1, "max_at_entry": 2}


def test_it_refuses_to_do_nothing_and_names_which_mode_spends_money() -> None:
    """A probe with a paid mode must never be a no-op that looks like a clean run."""
    with pytest.raises(SystemExit) as ei:
        P.main([])
    msg = str(ei.value)
    assert "--bench" in msg and "SPENDS MONEY" in msg.upper() or "spends money" in msg


def test_it_patches_the_binding_the_REPLAY_calls() -> None:
    """S14's lesson: patching a definition module never reaches a caller that imported the object.

    This probe's entire value is that it observes the code a real replay runs, so a patch that
    misses would report an empty retry path -- a confident "the poll never fired" for a run in which
    it fired every time.
    """
    src = inspect.getsource(P.install)
    assert "flow_mod._retry_if_unpainted = spy" in src
    assert "real = flow_mod._retry_if_unpainted" in src, (
        "the original must be captured from the same binding it replaces, or a second install "
        "wraps the spy and every timing is counted twice")
