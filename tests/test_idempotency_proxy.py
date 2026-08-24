"""The proxy that answers "did the write mechanism RUN?" — and the ways it could lie.

WHAT IT IS FOR. B4's gate 1 requires `idempotent-replay` to assert the mechanism ran, because
`_precheck_done` returns `already-done` before any browser action and the SERVER cannot tell that
world from a correctly-suppressed duplicate: both leave one record. The distinguishing fact is
whether a request left the browser carrying an `Idempotency-Key`.

DOCKER-FREE AND SUBSTRATE-FREE, and both are asserted rather than assumed. Every cell here runs
against a stub upstream on the loopback interface; a cell that pointed at :8069 or :3000 would pass
on this host and fail on CI, which is R4.90 exactly. `test_no_cell_here_points_at_a_real_substrate`
derives that from the module's own source, so the next cell someone adds is covered without editing
this one.
"""

from __future__ import annotations

import http.server
import inspect
import json
import socket
import threading
import urllib.error
import urllib.request
from dataclasses import fields

import pytest

from benchmarks import idempotency_proxy as P


# ---------------------------------------------------------------------------------------------
# a stub upstream that REPORTS WHAT IT RECEIVED, so forwarding can be asserted rather than hoped
# ---------------------------------------------------------------------------------------------

class _Upstream:
    """Echoes the request back as JSON, and can be told to answer a redirect or an error.

    It reports the headers it SAW, which is what lets a cell prove the proxy forwarded the ones it
    must and dropped the ones it must not. A stub that only returned 200 would let a proxy that
    mangled every header pass every cell here.
    """

    def __init__(self):
        self.seen: list = []
        stub = self

        class _H(http.server.BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *_a):
                pass

            def _go(self):
                n = int(self.headers.get("Content-Length") or 0)
                body = self.rfile.read(n) if n else b""
                stub.seen.append({"method": self.command, "path": self.path,
                                  "headers": {k.lower(): v for k, v in self.headers.items()},
                                  "body": body.decode("utf-8", "replace")})
                if self.path.startswith("/redirect-absolute"):
                    self.send_response(303)
                    self.send_header("Location", f"{stub.base}/landed")
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                if self.path.startswith("/redirect-relative"):
                    self.send_response(302)
                    self.send_header("Location", "/landed")
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                if self.path.startswith("/boom"):
                    payload = b"upstream says no"
                    self.send_response(503)
                    self.send_header("Content-Length", str(len(payload)))
                    self.end_headers()
                    self.wfile.write(payload)
                    return
                payload = json.dumps({"saw": self.command, "path": self.path}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("X-Stub", "yes")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                if self.command != "HEAD":
                    self.wfile.write(payload)

            do_GET = do_POST = do_PUT = do_DELETE = do_HEAD = _go

        self._httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _H)
        self._httpd.daemon_threads = True
        threading.Thread(target=lambda: self._httpd.serve_forever(poll_interval=0.02),
                         daemon=True).start()
        host, port = self._httpd.socket.getsockname()[:2]
        self.base = f"http://{host}:{port}"

    def stop(self):
        self._httpd.shutdown()
        self._httpd.server_close()


@pytest.fixture()
def upstream():
    u = _Upstream()
    yield u
    u.stop()


@pytest.fixture()
def proxy(upstream):
    p = P.IdempotencyProxy(upstream.base)
    p.start()
    yield p
    p.stop()


def _get(url, headers=None):
    req = urllib.request.Request(url, headers=headers or {})
    with urllib.request.urlopen(req, timeout=10) as r:
        return r.status, r.read(), dict(r.headers.items())


# ---------------------------------------------------------------------------------------------
# 1. REDACTION IS THE TYPE, not a filter over it
# ---------------------------------------------------------------------------------------------

def test_a_recorded_request_has_nowhere_to_put_a_secret() -> None:
    """The strongest form of "never write a credential to disk": no field can hold one.

    `browser.py` states the same rule from the other side — it REFUSES `record_har_path` under
    dry-run because "a HAR would persist raw held write bodies". A proxy that recorded headers
    wholesale would persist session cookies for every request of every scenario, and a reviewer
    would have to check that nothing ever printed them. Derived from the dataclass, so a field added
    tomorrow fails HERE rather than in a leaked artifact.
    """
    allowed = {"method", "path", "query_keys", "key", "status"}
    names = {f.name for f in fields(P.Request)}
    assert names == allowed, (
        f"`Request` grew {sorted(names - allowed)}. Redaction here is structural: a field that can "
        f"hold a body, a cookie, an Authorization header or a query VALUE makes every recorded "
        f"request a credential leak.")


def test_a_query_value_never_reaches_the_log_but_its_name_does() -> None:
    """THE OVER-CLAIM THIS SLICE CAUGHT IN ITS OWN CODE. `path` first held path+query, and a query
    string is a well-known credential carrier — Gitea accepts `?token=<pat>` for API auth. So "no
    field can hold a secret" was false with a `?` in it. Names are kept because they are how a log
    stays readable (`state`, `version`, `action`) and a NAME is not a credential; a denylist of
    sensitive parameter names was refused, because `safety.MUTATING_KEYWORDS` is this repo's standing
    proof that a curated word list is only as good as its worst omission."""
    r = P.Request(method="GET", path="/api/v1/repos", query_keys=("state", "token"), status=200)
    assert "state" in r.query_keys and "token" in r.query_keys
    assert "SECRET" not in repr(r)


def test_the_key_is_recorded_and_the_cookie_is_not(proxy, upstream) -> None:
    """Both halves in one cell, because either alone is satisfied by a broken proxy: recording
    nothing passes the redaction rule, and recording everything passes the evidence rule."""
    _get(proxy.base_url + "/x", {"Idempotency-Key": "uc-k-1",
                                 "Cookie": "session_id=SECRETVALUE",
                                 "Authorization": "Bearer SECRETTOKEN"})
    row = proxy.evidence().requests[0]
    assert row.key == "uc-k-1" and row.keyed
    assert row.path == "/x" and row.query_keys == ()
    blob = repr(row)
    assert "SECRETVALUE" not in blob and "SECRETTOKEN" not in blob, blob
    # ...and the upstream still RECEIVED them, or the proxy would have broken authentication.
    assert upstream.seen[0]["headers"]["cookie"] == "session_id=SECRETVALUE"
    assert upstream.seen[0]["headers"]["authorization"] == "Bearer SECRETTOKEN"


# ---------------------------------------------------------------------------------------------
# 2. transparency — the substrate must behave identically through it
# ---------------------------------------------------------------------------------------------

def test_status_body_and_headers_survive_the_relay(proxy) -> None:
    status, body, headers = _get(proxy.base_url + "/hello?a=1")
    assert status == 200
    assert json.loads(body) == {"saw": "GET", "path": "/hello?a=1"}
    assert headers.get("X-Stub") == "yes"


def test_the_log_splits_the_target_and_keeps_only_the_parameter_names(proxy) -> None:
    """End to end through a live relay, not just on the dataclass: a real request with a real query
    lands in the log with its VALUES gone and its NAMES kept."""
    _get(proxy.base_url + "/api/v1/repos?state=all&token=SECRETPAT&limit=100")
    row = proxy.evidence().requests[0]
    assert row.path == "/api/v1/repos"
    assert row.query_keys == ("limit", "state", "token")
    assert "SECRETPAT" not in repr(row)


def test_an_upstream_error_reaches_the_caller_as_itself(proxy) -> None:
    """A 4xx/5xx is a real answer. Turning it into a proxy error would make the app look broken in
    a way the substrate is not — and Odoo answers 404 on routes its own client probes."""
    with pytest.raises(urllib.error.HTTPError) as exc:
        _get(proxy.base_url + "/boom")
    assert exc.value.code == 503 and exc.value.read() == b"upstream says no"


def test_a_redirect_is_passed_through_rather_than_followed(proxy) -> None:
    """THE DEFECT THIS CELL EXISTS FOR. `urlopen` follows redirects by default, so the first draft
    resolved Odoo's login `303` behind the browser's back: the browser stayed on `/web/login`, never
    saw the redirect, and the session never started. Measured against a real browser, which is the
    only thing that showed it."""
    req = urllib.request.Request(proxy.base_url + "/redirect-relative")
    opener = urllib.request.build_opener(P._NoRedirect)
    with pytest.raises(urllib.error.HTTPError) as exc:
        opener.open(req, timeout=10)
    assert exc.value.code == 302
    assert exc.value.headers["Location"] == "/landed"


def test_an_absolute_redirect_is_rewritten_to_the_proxy(proxy, upstream) -> None:
    """THE SECOND HALF, AND THE ONE A PROBE OF THE HTML COULD NOT SEE. Odoo's login answers
    `Location: http://localhost:8069/web` — absolute, naming the substrate. Passed through unchanged
    the browser leaves the proxy at the moment it logs in, and every request after that, including
    every write, is invisible to the evidence. An earlier probe checked five served PAGES for
    self-absolute urls, found zero, and was asking the wrong layer entirely."""
    opener = urllib.request.build_opener(P._NoRedirect)
    with pytest.raises(urllib.error.HTTPError) as exc:
        opener.open(urllib.request.Request(proxy.base_url + "/redirect-absolute"), timeout=10)
    loc = exc.value.headers["Location"]
    assert loc == proxy.base_url + "/landed", loc
    assert upstream.base not in loc, "the browser would have left the proxy here"


def test_a_relative_redirect_is_left_alone(proxy) -> None:
    """The other direction: rewriting a relative Location would corrupt it. Without this,
    "rewrite everything" passes the cell above."""
    opener = urllib.request.build_opener(P._NoRedirect)
    with pytest.raises(urllib.error.HTTPError) as exc:
        opener.open(urllib.request.Request(proxy.base_url + "/redirect-relative"), timeout=10)
    assert exc.value.headers["Location"] == "/landed"


def test_hop_by_hop_headers_are_not_forwarded(proxy, upstream) -> None:
    """They belong to one connection (RFC 9110). Forwarding `Transfer-Encoding` alongside a
    `Content-Length` the proxy computes is how a relay corrupts a body."""
    _get(proxy.base_url + "/x", {"Connection": "keep-alive", "TE": "trailers"})
    seen = upstream.seen[0]["headers"]
    assert "te" not in seen, seen
    assert seen.get("host", "").startswith("127.0.0.1"), "the Host must name the upstream"


def test_the_body_of_a_post_reaches_the_upstream_unchanged(proxy, upstream) -> None:
    payload = json.dumps({"jsonrpc": "2.0", "params": {"model": "crm.lead"}}).encode()
    req = urllib.request.Request(proxy.base_url + "/web/dataset/call_kw", data=payload,
                                 headers={"Content-Type": "application/json",
                                          "Idempotency-Key": "uc-k-2"})
    with urllib.request.urlopen(req, timeout=10) as r:
        assert r.status == 200
    assert json.loads(upstream.seen[0]["body"]) == {"jsonrpc": "2.0", "params": {"model": "crm.lead"}}


# ---------------------------------------------------------------------------------------------
# 3. the evidence, and its THIRD state
# ---------------------------------------------------------------------------------------------

def test_ran_is_tri_state_and_silence_is_not_a_no(proxy) -> None:
    """NO PREMISE, NO SCORE — `Oracle`'s standing rule, one module over.

    An empty log means the agent may never have started, and answering "the mechanism did not run"
    would convert a harness failure into a product finding. That is the absence-with-two-causes
    shape this proxy exists to avoid, so it must not reappear inside the proxy's own verdict.
    """
    assert proxy.evidence().ran is None
    _get(proxy.base_url + "/just-a-read")
    assert proxy.evidence().ran is False
    _get(proxy.base_url + "/write", {"Idempotency-Key": "uc-k-3"})
    assert proxy.evidence().ran is True


def test_distinct_keys_are_reported_in_first_seen_order(proxy) -> None:
    """A replay that correctly re-fires under the DEDUPE key shows the same key twice; a second,
    different key is a second write. Collapsing them would hide exactly that difference."""
    for k in ("k1", "k1", "k2", "k1"):
        _get(proxy.base_url + "/w", {"Idempotency-Key": k})
    ev = proxy.evidence()
    assert len(ev.keyed) == 4
    assert ev.keys == ("k1", "k2")


def test_reset_separates_one_phase_from_the_next(proxy) -> None:
    """B2's rule 3, wearing an evidence hat: a replay judged against a log still holding the LEARN's
    keyed request reports that the mechanism ran when it did not."""
    _get(proxy.base_url + "/learn", {"Idempotency-Key": "learn-key"})
    assert proxy.evidence().ran is True
    proxy.reset()
    assert proxy.evidence().ran is None
    _get(proxy.base_url + "/replay")
    assert proxy.evidence().ran is False


def test_an_unreachable_upstream_is_recorded_and_reported(upstream) -> None:
    """A dead upstream must not look like a quiet read. The row is kept with `status=0` so the log
    still shows traffic — otherwise `ran` would answer None and call a broken substrate 'unscored'
    when it is in fact loud."""
    p = P.IdempotencyProxy(upstream.base)
    p.start()
    upstream.stop()
    try:
        with pytest.raises(urllib.error.HTTPError) as exc:
            _get(p.base_url + "/x")
        assert exc.value.code == 502
        rows = p.evidence().requests
        assert len(rows) == 1 and rows[0].status == 0
    finally:
        p.stop()


# ---------------------------------------------------------------------------------------------
# 4. protocol upgrades — carried, not refused
# ---------------------------------------------------------------------------------------------

def test_a_failed_upgrade_is_counted_and_never_silent(upstream) -> None:
    """A tunnel that cannot be established is a HARNESS fault. Odoo's bus retries a refused upgrade
    — measured, five times in eight seconds — so a silent drop is not a bounded gap but a permanent
    reconnect loop against a host this repo says cannot adjudicate under load."""
    p = P.IdempotencyProxy(upstream.base)
    p.start()
    upstream.stop()
    try:
        with pytest.raises(urllib.error.HTTPError) as exc:
            _get(p.base_url + "/websocket", {"Upgrade": "websocket", "Connection": "Upgrade"})
        assert exc.value.code == 502
        assert p.evidence().upgrades_failed == 1
        assert p.evidence().upgrades_tunnelled == 0
    finally:
        p.stop()


def test_an_upgrade_reaches_the_upstream_as_an_upgrade(proxy, upstream) -> None:
    """The stub is not a websocket server, so the handshake will not complete — what is asserted is
    that the proxy RELAYED it rather than answering 501 on the substrate's behalf. The end-to-end
    proof is the browser: driving Odoo through the proxy took its bus from five refused attempts
    down to one, which no unit stub can show."""
    s = socket.create_connection(("127.0.0.1", int(proxy.base_url.rsplit(":", 1)[1])), timeout=10)
    try:
        s.sendall(b"GET /websocket?version=17.0-3 HTTP/1.1\r\n"
                  b"Host: proxy\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
                  b"Sec-WebSocket-Version: 13\r\nSec-WebSocket-Key: dGhlIHNhbXBsZQ==\r\n\r\n")
        s.settimeout(10)
        try:
            s.recv(4096)
        except (socket.timeout, OSError):
            pass
    finally:
        s.close()
    assert proxy.evidence().upgrades_tunnelled == 1
    assert any(r["path"].startswith("/websocket") and r["headers"].get("upgrade") == "websocket"
               for r in upstream.seen), [r["path"] for r in upstream.seen]


# ---------------------------------------------------------------------------------------------
# 5. the module's own guards
# ---------------------------------------------------------------------------------------------

def test_an_upstream_that_is_not_an_absolute_url_is_refused() -> None:
    """A bare `host:port` is the natural typo, and it would build a proxy that prefixes every path
    onto a relative string and fails at the first request with a URLError nobody can read.

    NOTE the argument is a nonsense host on purpose: the cell below asserts that no proxy in this
    module names a real substrate, and it caught this one on its first working run — the value was
    `localhost:8069`, which never connects (construction raises first) and is still exactly the
    string that would let the next person copy this cell into one that does.
    """
    with pytest.raises(P.ProxyError, match="absolute http"):
        P.IdempotencyProxy("not-an-absolute-url:1234")


def test_the_base_url_is_refused_before_the_proxy_is_running(upstream) -> None:
    """Returning a plausible-looking URL for a proxy that is not listening would point a whole
    scenario at a closed port and report it as the agent failing to reach the app."""
    p = P.IdempotencyProxy(upstream.base)
    with pytest.raises(P.ProxyError, match="not running"):
        _ = p.base_url


def test_no_cell_here_points_at_a_real_substrate() -> None:
    """R4.90, kept closed by derivation rather than by discipline. A cell that reached :8069 or
    :3000 would pass on a developer host and fail both CI arms, which is precisely how the last two
    slices shipped red PRs — once over Docker, once over HTTP."""
    import ast

    # ASSERT WHAT EVERY PROXY IS BUILT WITH, NOT WHAT THE FILE SAYS. Two text-shaped drafts of this
    # check failed on themselves -- the first matched the docstrings explaining which ports are
    # forbidden, the second matched its own needle tuple. Both were the shape CLAUDE.md already names
    # ("do not sed a file that is 40% prose about the shape you are removing"), and a third needle
    # would only have moved the collision. The property is not "this text mentions no port"; it is
    # "no proxy here points anywhere but the loopback stub", so that is what is derived.
    tree = ast.parse(inspect.getsource(__import__(__name__, fromlist=["x"])))
    built = [n for n in ast.walk(tree)
             if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
             and n.func.attr == "IdempotencyProxy"]
    assert built, "no proxy is constructed in this module; the derivation has gone stale"
    for call in built:
        arg = call.args[0] if call.args else None
        src = ast.unparse(arg) if arg is not None else "<none>"
        if src.startswith("'not-an-absolute-url"):
            continue                    # refused at construction; no socket is ever opened
        assert src.endswith(".base"), (
            f"a proxy here is built with {src!r}. Every upstream must be the loopback stub: a cell "
            f"pointed at a real substrate passes on a developer host and fails both CI arms, which "
            f"is how the last two slices shipped red PRs (R4.85 over Docker, R4.90 over HTTP).")
