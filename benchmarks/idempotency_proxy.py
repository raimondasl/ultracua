"""Did the write mechanism RUN? The one question no query about the substrate can answer.

WHY THIS EXISTS, in the plan's own words. B4's gate 1 requires `idempotent-replay` to assert the
mechanism ran, "since `_precheck_done` (`flows.py`) returns `already-done` before any browser action
and would otherwise pass inert". Reproduced against the source rather than taken on trust: on the
skip path `_already_committed` opens its OWN `BrowserSession`, navigates to `precheck_url`, sees the
end-state and returns `{"status": "already-done"}` — so `run_cached` returns before the replay ever
starts, no cached step runs, the mutation gate never fires and no Idempotency-Key is ever minted.

From the SERVER side those two worlds are identical: one lead, no second row. `OdooIdempotentReplay
Oracle` says so itself and declined to pretend otherwise. The distinguishing fact is not a record —
it is whether a REQUEST left the browser carrying an `Idempotency-Key`, which `flow.py`'s mutation
gate puts on the context immediately before it acts.

WHY A PROXY AND NOT `record_har_path`, WHICH ALREADY EXISTS. `run_cached` accepts a HAR path and
threads it to the session, and the WebArena arm already uses it — so the cheap route looked obvious
and is WRONG here, for one concrete reason. `_already_committed` builds its own `BrowserSession`
with no `record_har_path`, so on the precheck path the HAR is never written at all. The evidence
would then be an ABSENCE with two causes — "the mechanism did not run" and "capture broke" — which
is the shape half this register is made of. A proxy is always listening, so "no keyed request
against a busy connection log" is a POSITIVE observation. It also sees the precheck's own navigation,
which is the very traffic that proves which world we are in.

TWO THINGS THIS DELIBERATELY IS NOT.

  * **It is not in front of the oracles.** Only the agent is pointed at the proxy; every oracle keeps
    asking the substrate directly. Routing the independent check through the instrument under test
    would couple the observer to the observed, and the oracle is the only thing in this benchmark
    that is supposed to be independent of it.
  * **It is not on the substrate's published port.** Taking that port over would remove the one
    residual below, at the cost of making a Python process a prerequisite for `seed()`, readiness,
    `warm_assets` and every oracle — a blast radius that manufactures defects here. Measured before
    choosing: neither substrate emits a self-absolute URL in its served HTML (gitea root, issue list
    and issue page; odoo login and backend shell — 0 of 5), so a proxy on its own port is transparent
    to navigation. The residual is Odoo's `web.base.url`, which appears ONCE, in the session-info
    JSON, and is used for generated links (mail, reports) rather than for the SPA's own RPC — none of
    the fourteen scenarios follows one. Stated rather than discovered.

IT CARRIES THE WEBSOCKET, AND THE FIRST DRAFT DID NOT — WHICH IS THE MEASUREMENT LESSON OF THIS FILE.
The first version refused protocol upgrades on the measured claim that Odoo opened **zero**
websockets: a Playwright probe watched `page.on("websocket")` across a full login and list view and
saw none. That claim was FALSE, and the probe was asking the wrong object — Odoo 17 opens its bus
socket from a SHARED WORKER, which never surfaces on the page. Driving a browser through the proxy
showed the truth immediately: `/websocket?version=17.0-3`, refused with 501, **retried five times in
eight seconds and climbing**. So refusing was not a bounded, stated gap at all; it was a permanent
reconnect loop, adding load to a host this repo already says cannot adjudicate timing under load.

Upgrades are therefore TUNNELLED: the handshake is replayed to the upstream verbatim and the two
sockets are pumped until either closes. That keeps the substrate exactly as it is, which is the
benchmark's whole premise. A tunnel that cannot be established is counted in
`Evidence.upgrades_failed` and is a HARNESS error, never a product finding. The tunnelled bytes are
NOT recorded — see `Request`: this module records evidence about writes without ever holding them.
"""

from __future__ import annotations

import http.server
import socket
import threading
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Optional
from urllib.parse import urlsplit

#: The header `flow.py`'s mutation gate puts on the browser context immediately before a write acts.
#: Its PRESENCE is the signal: the product mints one only for a step it has classified as mutating,
#: so a request carrying it is the product asserting "I am about to write".
IDEMPOTENCY_HEADER = "idempotency-key"

#: Hop-by-hop headers, which belong to one connection and must not be forwarded (RFC 9110 §7.6.1).
#: `Host` is dropped separately because the proxy rewrites it to the upstream's authority.
HOP_BY_HOP = frozenset({
    "connection", "keep-alive", "proxy-authenticate", "proxy-authorization",
    "te", "trailer", "transfer-encoding", "upgrade",
})

#: Response headers that can carry an ABSOLUTE url back to the upstream, and must be rewritten to
#: point at the proxy instead. THIS IS NOT DEFENSIVE POLISH -- it was measured, and it is the whole
#: difference between an instrument and a hole. Odoo's form login answers `303` with
#: `Location: http://localhost:8069/web`, so a proxy that passes it through unchanged hands the
#: browser an absolute jump back to the substrate AT LOGIN, and every request after that is invisible
#: to the evidence. An earlier probe of this same question looked only at served HTML, found zero
#: self-absolute urls in five pages, and was simply asking the wrong layer.
ORIGIN_HEADERS = ("location", "content-location")


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Never follow a redirect: the BROWSER must see the 3xx.

    `urllib.request.urlopen` follows them by default, which silently swallowed Odoo's login 303 --
    the browser stayed on `/web/login`, never received the redirect, and the session never started.
    A proxy that resolves redirects on the client's behalf is not transparent, and the failure looks
    like a broken application rather than a broken instrument.
    """

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class ProxyError(RuntimeError):
    """The proxy could not be trusted to have seen what a scenario needs — refused, never guessed."""


@dataclass(frozen=True, kw_only=True)
class Request:
    """ONE observed request, and REDACTION IS THE TYPE rather than a filter over it.

    There is no field for a body, a cookie, an Authorization header or a query VALUE, so no code
    path can put one here and no reviewer has to check that none did. That is deliberate and it is the same rule
    `browser.py` states from the other side: it REFUSES `record_har_path` under dry-run because
    "a HAR would persist raw held write bodies". This module records evidence about writes without
    ever holding the writes.

    KEYWORD-ONLY, and that is not style. Inserting `query_keys` moved every later field by one, and
    a positional construction elsewhere silently put a status code into `key` — which made `keyed`
    truthy and an oracle report that the write mechanism had run when it had not. Caught by a test,
    but it is the same failure class `reshape-plan` 1.1 spent a whole slice removing from the engine
    (98 positional parameters down to 7). A record whose fields will grow should not be positional.

    `key` is the Idempotency-Key and is NOT a secret — it is derived from the flow scope, the step
    index, the intent and the run's slot values, so it identifies a write rather than authorising
    one. Everything that WOULD authorise one is structurally absent.
    """

    method: str
    #: The PATH ONLY. The query string is deliberately not here: it is a well-known credential
    #: carrier — Gitea accepts `?token=<pat>` for API auth and Odoo has `/web/reset_password?token=`
    #: — so "no field can hold a secret" would have been an over-claim with `?` in it, and an
    #: over-claimed guarantee is worse than an absent one (R4.86, one file over).
    path: str
    #: The query's PARAMETER NAMES, sorted. Names keep the log readable (`state`, `version`, `action`
    #: are how you tell one request from another) and a NAME cannot be a credential the way a VALUE
    #: can. A denylist of sensitive parameter names was the obvious alternative and is the shape this
    #: repo distrusts most: `safety.MUTATING_KEYWORDS` is the standing proof that a curated list of
    #: words is only as good as its worst omission.
    query_keys: tuple = ()
    key: str = ""                  #: the Idempotency-Key, or "" when the request carried none
    status: int = 0                #: upstream status, 0 if the request never completed

    @property
    def keyed(self) -> bool:
        return bool(self.key)


@dataclass(frozen=True)
class Evidence:
    """What the proxy saw during one phase of one scenario.

    THE THREE STATES ARE NOT TWO. `ran` distinguishes "a keyed request left the browser" from "it did
    not"; `saw_traffic` is what makes the second of those a POSITIVE observation rather than an
    absence. A phase with no traffic at all has told us nothing — the agent may never have started —
    and `Oracle`'s standing rule applies: no premise, no score.
    """

    requests: tuple = ()
    #: Protocol upgrades relayed to the upstream. Expected NON-ZERO against Odoo, whose bus opens a
    #: websocket from a shared worker; zero against Gitea.
    upgrades_tunnelled: int = 0
    #: Upgrades the proxy could not establish. A harness fault, never a product finding — the
    #: substrate is then degraded and whatever the run measured is about the instrument.
    upgrades_failed: int = 0

    @property
    def saw_traffic(self) -> bool:
        return bool(self.requests)

    @property
    def keyed(self) -> tuple:
        return tuple(r for r in self.requests if r.keyed)

    @property
    def keys(self) -> tuple:
        """Distinct Idempotency-Keys, in first-seen order. A REPLAY that correctly re-fires under the
        dedupe key shows the SAME key as the learn; a second, different key is a second write."""
        seen, out = set(), []
        for r in self.keyed:
            if r.key not in seen:
                seen.add(r.key)
                out.append(r.key)
        return tuple(out)

    @property
    def ran(self) -> Optional[bool]:
        """Did the write mechanism run? `None` when nothing was observed at all."""
        if not self.saw_traffic:
            return None
        return bool(self.keyed)

    def summary(self) -> dict:
        return {"requests": len(self.requests), "keyed": len(self.keyed),
                "distinct_keys": len(self.keys), "upgrades_tunnelled": self.upgrades_tunnelled,
                "upgrades_failed": self.upgrades_failed, "ran": self.ran}


class IdempotencyProxy:
    """A logging reverse proxy in front of one substrate. Start it, point the AGENT at `base_url`.

    Binds an ephemeral port on the loopback interface, so several can run at once and none collides
    with a substrate. Threaded, because Odoo's page load issues a handful of requests concurrently
    and a serialising proxy would change the timing the benchmark measures.
    """

    def __init__(self, upstream: str, *, timeout_s: float = 60.0) -> None:
        if not upstream.startswith(("http://", "https://")):
            raise ProxyError(f"upstream must be an absolute http(s) URL, got {upstream!r}")
        self.upstream = upstream.rstrip("/")
        self.timeout_s = timeout_s
        self._records: list = []
        self._tunnelled = 0
        self._failed_upgrades = 0
        self._lock = threading.Lock()
        self._httpd: Optional[http.server.ThreadingHTTPServer] = None
        self._thread: Optional[threading.Thread] = None
        self._opener = urllib.request.build_opener(_NoRedirect)

    # --- lifecycle --------------------------------------------------------------------------

    def start(self) -> str:
        if self._httpd is not None:
            raise ProxyError("proxy already started")
        proxy = self

        class _H(http.server.BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *_a):        # noqa: N802 - stdlib hook; silence the access log
                pass

            def _relay(self) -> None:
                proxy._handle(self)

            do_GET = do_POST = do_PUT = do_PATCH = do_DELETE = do_HEAD = do_OPTIONS = _relay

        self._httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _H)
        self._httpd.daemon_threads = True
        # `poll_interval` well below the stdlib's 0.5 s default: `shutdown()` waits up to one
        # interval, and this proxy is started and stopped once per PHASE. Measured on the unit
        # file: 0.5 s x 2 servers x 12 cells was ~12 s of pure teardown, on a fast tier whose
        # budget CLAUDE.md already records as drifting.
        self._thread = threading.Thread(
            target=lambda: self._httpd.serve_forever(poll_interval=0.02), daemon=True)
        self._thread.start()
        host, port = self._httpd.socket.getsockname()[:2]
        return f"http://{host}:{port}"

    @property
    def base_url(self) -> str:
        if self._httpd is None:
            raise ProxyError("proxy is not running")
        host, port = self._httpd.socket.getsockname()[:2]
        return f"http://{host}:{port}"

    def stop(self) -> None:
        if self._httpd is not None:
            self._httpd.shutdown()
            self._httpd.server_close()
            self._httpd = None
        if self._thread is not None:
            self._thread.join(timeout=10)
            self._thread = None

    def __enter__(self) -> "IdempotencyProxy":
        self.start()
        return self

    def __exit__(self, *exc) -> bool:
        self.stop()
        return False

    # --- evidence ---------------------------------------------------------------------------

    def evidence(self) -> Evidence:
        """A frozen snapshot of what has been seen so far."""
        with self._lock:
            return Evidence(requests=tuple(self._records), upgrades_tunnelled=self._tunnelled,
                            upgrades_failed=self._failed_upgrades)

    def reset(self) -> None:
        """Forget everything, so the next PHASE is measured on its own.

        A scenario's learn and its replay are different questions asked of the same proxy, and a
        replay judged against a log still holding the learn's keyed request would report that the
        mechanism ran when it did not — the evidence equivalent of B2's rule 3, where an un-reset
        substrate makes the previous scenario's records look like this one's.
        """
        with self._lock:
            self._records.clear()
            self._tunnelled = 0
            self._failed_upgrades = 0

    # --- the relay --------------------------------------------------------------------------

    def _handle(self, h) -> None:
        if (h.headers.get("Upgrade") or "").strip():
            self._tunnel(h)
            return

        length = int(h.headers.get("Content-Length") or 0)
        body = h.rfile.read(length) if length else None
        key = (h.headers.get(IDEMPOTENCY_HEADER) or "").strip()
        headers = {k: v for k, v in h.headers.items() if k.lower() not in HOP_BY_HOP
                   and k.lower() != "host"}
        req = urllib.request.Request(self.upstream + h.path, data=body, headers=headers,
                                     method=h.command)
        try:
            with self._opener.open(req, timeout=self.timeout_s) as resp:
                status, payload, out_headers = resp.status, resp.read(), list(resp.headers.items())
        except urllib.error.HTTPError as exc:
            # A 3xx/4xx/5xx is a REAL RESPONSE and must reach the browser: Odoo answers 303 on a form
            # login and 404 on routes the client probes deliberately, and turning either into a proxy
            # error would make the app look broken in a way the substrate is not. With `_NoRedirect`
            # installed, a redirect arrives here rather than being followed behind the browser's back.
            status, payload, out_headers = exc.code, exc.read(), list(exc.headers.items())
        except (urllib.error.URLError, OSError, socket.timeout) as exc:
            self._record(h.command, h.path, key, 0)
            h.send_error(502, f"benchmark proxy could not reach {self.upstream}: {exc}")
            return

        self._record(h.command, h.path, key, status)
        try:
            h.send_response(status)
            for name, value in out_headers:
                if name.lower() in HOP_BY_HOP or name.lower() == "content-length":
                    continue
                h.send_header(name, self._rewrite_origin(name, value))
            h.send_header("Content-Length", str(len(payload)))
            h.end_headers()
            if h.command != "HEAD":
                h.wfile.write(payload)
        except (ConnectionAbortedError, ConnectionResetError, BrokenPipeError):
            # THE CLIENT HUNG UP MID-RESPONSE, which a browser does routinely -- it navigates away,
            # cancels a prefetch, or drops an image it no longer needs. Measured on the first real
            # agent run: a full `ThreadingHTTPServer` traceback per occurrence, several per page.
            #
            # Swallowed rather than logged, and the ROW IS ALREADY RECORDED above, so the evidence is
            # unaffected: what the client did with the response is not what this proxy is measuring.
            # Narrow on purpose -- three connection errors and nothing else. A bare `except` here
            # would hide a genuine relay defect behind the same silence, and the noise it removes is
            # exactly the noise that would bury one.
            h.close_connection = True

    def _tunnel(self, h) -> None:
        """Relay a protocol upgrade byte-for-byte, so the substrate keeps the transport it has.

        MEASURED, AND THE REASON THIS IS NOT A 501. Odoo 17's bus connects a websocket at
        `/websocket?version=17.0-3` from a SHARED WORKER — invisible to `page.on("websocket")`, which
        is why an earlier probe reported zero and this file's first draft refused them. Refused, the
        client RETRIED: five attempts in eight seconds and still climbing. A permanent reconnect loop
        is not a bounded gap; it is a changed substrate plus a load source, on a host this repo
        already says cannot adjudicate timing under load.

        The handshake is replayed verbatim except for `Host`, which must name the upstream. Nothing
        that crosses the tunnel is recorded: the frames are application traffic, and `Request`'s
        docstring states the rule this module keeps — evidence about writes, never the writes.
        """
        up = urlsplit(self.upstream)
        port = up.port or (443 if up.scheme == "https" else 80)
        try:
            peer = socket.create_connection((up.hostname, port), timeout=self.timeout_s)
        except OSError as exc:
            with self._lock:
                self._failed_upgrades += 1
            h.send_error(502, f"benchmark proxy could not open an upgrade tunnel: {exc}")
            return

        head = [f"{h.command} {h.path} {h.request_version}"]
        for name, value in h.headers.items():
            if name.lower() == "host":
                value = up.hostname if up.port is None else f"{up.hostname}:{up.port}"
            head.append(f"{name}: {value}")
        peer.sendall(("\r\n".join(head) + "\r\n\r\n").encode("latin-1"))

        with self._lock:
            self._tunnelled += 1
        # The handler must not write a response of its own after this point: the connection now
        # belongs to the tunnel.
        h.close_connection = True
        _pump(h.connection, peer)

    def _rewrite_origin(self, name: str, value: str) -> str:
        """Point a redirect back at the PROXY, not at the substrate.

        Measured: Odoo's login answers `303 Location: http://localhost:8069/web`. Passed through
        unchanged, the browser leaves the proxy at the moment it logs in and every later request --
        including every write -- is invisible here. The evidence would then read "no keyed request"
        for a run that keyed one perfectly, which is a false negative in the channel this module
        exists to be right about.

        Rewrites the ORIGIN only, so paths, queries and fragments survive untouched. Both spellings
        of the loopback host are handled because they are not interchangeable to a browser: the proxy
        binds `127.0.0.1` and the substrate is configured as `localhost`, so a cookie set for one is
        not sent to the other.
        """
        if name.lower() not in ORIGIN_HEADERS or not value:
            return value
        up = urlsplit(self.upstream)
        for host in {up.hostname, "localhost", "127.0.0.1"} - {None}:
            origin = f"{up.scheme}://{host}:{up.port}" if up.port else f"{up.scheme}://{host}"
            if value.startswith(origin):
                return self.base_url + value[len(origin):]
        return value

    def _record(self, method: str, target: str, key: str, status: int) -> None:
        """Split the request target so a query VALUE can never reach the log."""
        path, _, query = target.partition("?")
        names = tuple(sorted({kv.partition("=")[0] for kv in query.split("&") if kv}))
        with self._lock:
            self._records.append(Request(method=method, path=path, query_keys=names,
                                         key=key, status=status))

def _pump(a: socket.socket, b: socket.socket) -> None:
    """Copy bytes both ways until either side closes, then close both.

    One thread per direction and a JOIN on the reverse one, because this runs inside the request
    handler: returning while the tunnel is live would let `BaseHTTPRequestHandler` close the client
    socket underneath it. Both sockets are closed exactly once, from here.
    """
    def one_way(src: socket.socket, dst: socket.socket) -> None:
        try:
            while True:
                chunk = src.recv(65536)
                if not chunk:
                    break
                dst.sendall(chunk)
        except OSError:
            pass
        finally:
            try:
                dst.shutdown(socket.SHUT_WR)
            except OSError:
                pass

    back = threading.Thread(target=one_way, args=(b, a), daemon=True)
    back.start()
    one_way(a, b)
    back.join(timeout=5)
    for s in (a, b):
        try:
            s.close()
        except OSError:
            pass
