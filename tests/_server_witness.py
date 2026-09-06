"""Wait for the SERVER's own record of a request the product has already seen (R4.151, 0.175.0).

TWO OBSERVERS WATCH ONE REQUEST AND CANNOT SEE IT AT THE SAME MOMENT. A loopback fixture appends to
its record on the handler thread, once the request has crossed the socket and been dispatched. The
product watches `page.on("request")`, which fires when the browser SENDS. Every guard built on that
listener -- `_maybe_heal`'s wire check, `_replay_step`'s `expect_request` -- therefore returns
STRICTLY BEFORE the fixture has written anything down, and a test asserting the fixture's record the
instant the product returns is reading across a race it did not declare.

MEASURED, on an idle Windows host, six reps, one clock: send -> handler 1.0 ms, send -> assertion
2.6 ms. **A margin of 1.5-1.8 ms.** That is what `main` lost at c7d31a5, on ubuntu, where the
product logged *"heal: the proposed action fired a WRITE on the wire"* and the very next line
asserted on an empty list.

ONLY A POSITIVE PREMISE RACES, AND THAT ASYMMETRY IS THE WHOLE OF WHEN TO REACH FOR THIS.
  * *"the fixture DID post"* is read too EARLY, so it must wait. This is the failure mode.
  * *"the fixture posted NOTHING"* cannot fail by being read late; reading it late is what makes it
    STRONGER, since a stray write has had more time to arrive. Waiting for it would burn the whole
    deadline on every call and buy nothing.
So a cell asserting an empty record is deliberately left alone, and one asserting a non-empty record
takes this. Applying it uniformly would be the more obvious change and the wrong one.

WHAT THIS DELIBERATELY IS NOT: a check that the PRODUCT saw the write. That is the thing under test
in every cell that needs this, so a premise reading it is vacuous by construction -- it would pass
against a fixture that never posted at all. The server's record is the INDEPENDENT witness. What
this moves is WHEN it is read; an absent request still fails, with the caller's own message, one
bounded wait later.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any, Callable

# A WALL-CLOCK DEADLINE, NOT A TICK COUNT, because a tick count does not mean what it says. The first
# draft here was `range(500)` over `asyncio.sleep(0.01)` commented as "5 s"; measured, a 10 ms sleep
# costs **16.5 ms** on this Windows host (timer granularity), so the real bound was 8.2 s here and
# ~5 s on the ubuntu arm -- one constant meaning two different things on the two platforms CI runs.
_DEADLINE_S = 5.0
_POLL_S = 0.01


async def recorded(probe: Callable[[], Any]) -> Any:
    """Poll `probe()` until it is truthy, or `_DEADLINE_S` passes; return its last value.

    Pass a probe that SNAPSHOTS — `lambda: list(hits)` — and the value handed back cannot be changed
    afterwards by a teardown, a late handler, or a caller reading it two lines further down. That is
    what makes the wait the only thing standing between the premise and the race, rather than one of
    several accidental sources of slack (see `_deferred_heal` in `tests/test_round2_fixes.py`, whose
    first draft was rescued by a ~0.5 s teardown and was therefore inert).

    The deadline is spent ONLY when the premise is already going to fail, so the happy path costs one
    probe and the failing path costs five seconds and then fails exactly as it would have.
    """
    deadline = time.monotonic() + _DEADLINE_S
    while True:
        value = probe()
        if value or time.monotonic() >= deadline:
            return value
        await asyncio.sleep(_POLL_S)
