"""Count every TCP connection the suite's fixture servers accept. A pytest plugin, for R4.22.

    PYTHONPATH=scripts uv run --no-sync pytest tests/ -q -p count_fixture_connections

WHY THIS EXISTS. R4.22 (`net::ERR_NO_BUFFER_SPACE`, Windows only, 7 occurrences) spent six occurrences
undiagnosed because the available evidence was OS-level sampling — `scripts/sample_resources.ps1` reads
TIME_WAIT and pool sizes every ~7 s, which cannot bracket a transient and cannot attribute anything to
this codebase. This counts the thing the leading hypothesis was actually about: how many ephemeral ports
the suite burns.

It answered the question in one run. **1884 connections over 1806 s = 1.04/s**, which at Windows'
120 s TIME_WAIT delay is ~125 sockets held — **0.8% of the 16384-port ephemeral range**. The prediction
validated against occurrence 7's independent samples (observed TIME_WAIT 130 and 186), so the model is
right and the pressure simply is not there. Socket churn is not R4.22. See the register for the full
disposition.

KEEP IT because the next occurrence deserves a number rather than another hypothesis, and because a
future change that multiplies fixture traffic should be visible: if this number moves by an order of
magnitude, the refutation above stops holding and R4.22's socket hypothesis is back on the table.

Patches both server classes the suite instantiates, so fixtures that build their own are counted too,
not just the shared `_serve` helper.
"""

from __future__ import annotations

import http.server
import time

# Windows' defaults, and the only two constants the verdict depends on. Both are stated rather than
# assumed silently: the range is `netsh int ipv4 show dynamicport tcp`, the delay is TcpTimedWaitDelay.
_EPHEMERAL_PORTS = 16384          # 49152-65535
_TIME_WAIT_S = 120.0

_COUNT = {"connections": 0}
_START = time.monotonic()


def _wrap(cls) -> None:
    original = cls.process_request

    def process_request(self, request, client_address):
        _COUNT["connections"] += 1
        return original(self, request, client_address)

    cls.process_request = process_request


for _cls in (http.server.ThreadingHTTPServer, http.server.HTTPServer):
    _wrap(_cls)


def pytest_terminal_summary(terminalreporter, exitstatus, config) -> None:
    n = _COUNT["connections"]
    elapsed = max(1.0, time.monotonic() - _START)
    # A raw count says nothing on its own — sockets DRAIN. What decides exhaustion is the arrival RATE
    # against the TIME_WAIT window, and reporting the count alone is how "the suite burns a lot of
    # sockets" survived as a story long enough to be proposed as R4.22's cause.
    held = n / elapsed * _TIME_WAIT_S
    terminalreporter.write_line("")
    terminalreporter.write_line(
        "fixture TCP connections accepted = %d over %.0f s (%.2f/s)" % (n, elapsed, n / elapsed))
    terminalreporter.write_line(
        "  steady state at a %.0f s TIME_WAIT delay: ~%.0f sockets held = %.1f%% of %d ephemeral ports"
        % (_TIME_WAIT_S, held, held / _EPHEMERAL_PORTS * 100.0, _EPHEMERAL_PORTS))
