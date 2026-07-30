"""drift-bench v2, part 2 of 4 — the fixtures, the injecting server, and the GOLDEN-TRAIL oracle.

Two mechanisms live here, and both are corrections of a v1 limitation that silently weakened its headline
invariant.

1. SERVER-SIDE MUTATION INJECTION, not v1's `prepare` hook.
   `flow.py` awaits `prepare` exactly ONCE, inside the navigate trace after the first `goto`. So v1 can
   only ever mutate the entry page: a multi-page flow's later pages are unreachable, and therefore a
   mid-flow drift (the most realistic kind) cannot be tested at all. Here the server holds the active
   mutation and injects it into EVERY html response, so page 2 drifts exactly like page 1. The start URL is
   unchanged, so `flow_key(goal, url, scope)` still matches the learned flow — no cache-key surgery.

2. THE GOLDEN TRAIL, not v1's landed-URL sentinel.
   v1 decides an outcome by reading the final URL. That makes its `wrong_binds == 0` invariant sound only
   for a navigating click: a mis-bind onto a NON-navigating element leaves `success=True` on the start page,
   which v1's classifier labels `drifted` — the SAFE bucket. Typing into a decoy field, choosing a wrong
   option, or clicking a wrong button that does not navigate were all laundered into "failed safely".
   So every interactable here carries `data-oracle="<id>"`, and a capture-phase listener appends the exact
   ordered sequence of (element-identity, value) pairs actually actuated to `sessionStorage`. An outcome is
   then judged by comparing that sequence to the one the pristine learn produced. A divergence anywhere is
   `wrong`, whether or not the page navigated.

THE ORACLE MUST STAY INVISIBLE TO THE RESOLVER, or every number here is inflated. `describe()` reads only
`data-testid`, id, placeholder, role, accessible name, text and a css path (locators.py `_SPECOF_JS`), and
`snapshot.py` exposes only `{ref, role, name, tag, type, value, bbox}` — so a `data-oracle` attribute is
not reachable as a locator or as an observation. `tests/test_drift_corpus.py` asserts the string appears
nowhere in `locators.py`/`snapshot.py`, because a future change to the captured attribute set would quietly
turn the oracle into a hint.

WHY sessionStorage AND NOT THE DOM, AND NOT A POST — both alternatives are actively wrong, not merely less
tidy:
  * NOT the DOM. A witness that appends to the page perturbs `snapshot.fingerprint`. `_maybe_heal` rejects
    a no-effect heal by checking `state_changed` — so a DOM-writing witness would make EVERY no-effect heal
    look effective and inflate the heal number.
  * NOT a POST. `safety.is_write_request` counts any same-origin non-telemetry POST, and `_author_steps`'
    watcher attributes it to the step in flight — so `fetch('/_witness')` would classify the witness itself
    as a write and poison the write scenario's double-submit accounting.
"""

from __future__ import annotations

import http.server
import json
import re
import threading
from typing import Optional

FIXTURES_VERSION = 1

# ---------------------------------------------------------------------------------------------------
# The act log. Capture-phase, so it records what was actuated even if a handler stops propagation.
#
# Event wiring is deliberately asymmetric and the asymmetry is load-bearing:
#   * `input`  -> log, but SKIP <select>. Using `change` for text inputs would log on BLUR, which reorders
#     the trail relative to actuation order (measured: ['size=m','qty=3'] instead of ['qty=3','size=m']).
#   * `change` -> log ONLY for <select>. A <select> fires both `input` and `change`, so listening to both
#     double-logs it.
# ---------------------------------------------------------------------------------------------------
_ACTLOG_JS = r"""
(function () {
  var KEY = 'uca_acts';
  function push(v) {
    try {
      var a = JSON.parse(sessionStorage.getItem(KEY) || '[]');
      a.push(v); sessionStorage.setItem(KEY, JSON.stringify(a));
    } catch (e) {}
  }
  function idOf(el) {
    while (el && el.nodeType === 1) {
      var o = el.getAttribute && el.getAttribute('data-oracle');
      if (o) return o;
      el = el.parentElement;
    }
    return 'UNMARKED';
  }
  document.addEventListener('click', function (e) { push(idOf(e.target)); }, true);
  document.addEventListener('input', function (e) {
    if (e.target && e.target.tagName === 'SELECT') return;
    push(idOf(e.target) + '=' + (e.target.value === undefined ? '' : e.target.value));
  }, true);
  document.addEventListener('change', function (e) {
    if (!e.target || e.target.tagName !== 'SELECT') return;
    push(idOf(e.target) + '=' + (e.target.value === undefined ? '' : e.target.value));
  }, true);
  window.__ucaActs = function () {
    try { return JSON.parse(sessionStorage.getItem(KEY) || '[]'); } catch (e) { return []; }
  };
  window.__ucaMark = push;
  // A page-load MARK, declared as `<body data-uca-mark="...">`. It must be pushed from HERE, not from an
  // inline <script> in the page: this snippet is injected at `</body>`, so any page script referencing
  // `window.__ucaMark` runs BEFORE it exists and marks nothing (measured — the goal marker was missing from
  // every trail until this moved). Reading an attribute makes the ordering impossible to get wrong.
  try {
    var m = document.body && document.body.getAttribute('data-uca-mark');
    if (m) push(m);
  } catch (e) {}
})();
"""

# Reads the trail. One `page.evaluate`, no waiting — which is also why v2 does not need v1's fixed 3000 ms
# `wait_for_url` in `finalize` (that wait was most of v1's wall time).
READ_ACTS_JS = "() => (window.__ucaActs ? window.__ucaActs() : [])"

# The target's own identity + handler census, for the oracle-blindness invariant: if a mutation strips a
# handler the trail goes quiet and the row degrades into the SAFE `drifted` bucket, so a positive check is
# required rather than a convention.
CENSUS_JS = r"""
(sel) => {
  const t = document.querySelector(sel);
  const m = window.__ucaMut || {applied: null, error: null, found: null};
  return {
    present: !!t,
    oracle: t ? t.getAttribute('data-oracle') : null,
    on: t ? Array.from(t.attributes).map(a => a.name).filter(n => n.indexOf('on') === 0).sort() : [],
    mut_applied: m.applied,
    mut_error: m.error,
    mut_found: m.found,
  };
}
"""


def mutation_js(primitives: tuple) -> str:
    """Compose primitive JS into one statement body sharing the bound `t`.

    Joined with `;` after stripping trailing semicolons — a plain string concatenation produced a SYNTAX
    ERROR whenever a primitive lacked one, which silently left the page pristine (see `_inject`)."""
    parts = [p.strip().rstrip(";").strip() for p in primitives if p and p.strip()]
    return ";\n".join(parts) + (";" if parts else "")


# ---------------------------------------------------------------------------------------------------
# Scenarios
# ---------------------------------------------------------------------------------------------------
# A note on the ladder target's shape, which is a measured constraint rather than a taste:
# `cssPath` returns `#<id>` and stops for any element bearing an id, so an id-bearing target's css anchor
# IS its elem_id anchor (not independent, and not killable by a wrapper or a reorder). The ladder target
# therefore carries NO id, giving it a genuinely positional css and 7 independent anchors. `shop-4step`'s
# inputs DO carry ids (as real form fields do), so the id-bearing case is covered there.
_ANCHORS_FULL = ("testid", "role+name", "role+name~", "placeholder", "exact-text", "css", "anchor")

# --- v1 ports (verbatim pages; their drift JS is ported in drift_bench) -----------------------------
V1_ANCHOR_PAGE = """<!doctype html><html><head><meta charset=utf-8><title>Shop</title></head><body>
  <header><a href="/help" data-oracle="help">Help</a> <a href="/account" data-oracle="acct">Account</a></header>
  <section id="cart"><h2>Your Cart</h2><p>1 item — a widget</p></section>
  <section id="checkout"><h2>Checkout</h2>
    <p>Review your order, then continue.</p>
    <a href="/done" data-oracle="go">Continue</a>
  </section>
  <footer><p>(c) 2026 Shop</p></footer>
</body></html>"""

V1_SPAN_PAGE = """<!doctype html><html><head><meta charset=utf-8><title>Step</title></head><body>
  <header><a href="/help" data-oracle="help">Help</a></header>
  <main><h2>Review</h2>
    <p>Everything look right?</p>
    <div class="actions">
      <span class="lnk" data-oracle="dismiss" onclick="location.href='/wrong'">Dismiss</span>
      <span class="lnk" data-oracle="go" onclick="location.href='/done'">Proceed</span>
    </div>
  </main>
  <footer><p>(c) 2026 Shop</p></footer>
</body></html>"""

# --- the ladder runway: one target carrying every independent anchor -------------------------------
# The same-tag positional decoy sibling matters: when a mutation moves the target, the cached positional
# css now points at the DECOY, so a css-first resolution would silently bind it. That is the wrong-bind
# this fixture exists to try to induce.
LADDER_PAGE = """<!doctype html><html><head><meta charset=utf-8><title>Pay</title></head><body>
  <header><a href="/help" data-oracle="help">Help</a></header>
  <section id="summary"><h2>Summary</h2><p>One widget, ready to go.</p></section>
  <section><h2>Checkout</h2>
    <p>Almost there.</p>
    <button data-oracle="decoy" data-testid="cancel" placeholder="never" type="button"
            onclick="location.href='/wrong'">Cancel</button>
    <button data-oracle="go" data-testid="continue-btn" placeholder="continue-hint" type="button"
            onclick="location.href='/done'">Continue</button>
  </section>
  <footer><p>(c) 2026 Shop</p></footer>
</body></html>"""

# --- the 4-step, 2-page flow: type -> select -> click -> click ---------------------------------------
# The ONLY scenario that exercises `type`/`select` locator resolution, the only one where a suffix-replan
# is distinguishable from a full re-author (a 1-step flow has no prefix to preserve), and the only one with
# a mutatable SECOND page. Its `select` step is what surfaced the `flow.py` locator defect.
SHOP_PAGE1 = """<!doctype html><html><head><meta charset=utf-8><title>Order</title></head><body>
  <header><a href="/help" data-oracle="help">Help</a></header>
  <section id="order"><h2>Your order</h2>
    <label for="qty">Quantity</label>
    <input id="qty" data-oracle="qty" data-testid="qty-field" placeholder="0" name="qty">
    <label for="size">Size</label>
    <select id="size" data-oracle="size" data-testid="size-field" name="size">
      <option value="s">Small</option><option value="m">Medium</option><option value="l">Large</option>
    </select>
    <a href="/shop2" data-oracle="go" data-testid="next-link">Next</a>
  </section>
  <!-- An ALTERNATE route to the same page, which the pristine learn never uses. It exists so the
       SUFFIX-REPLAN tier can be measured at all: a replan only fires after a heal fails, and a heal only
       fails (with a truthful provider) when the target is GONE — at which point, on a single-route page no
       recovery is possible and replan scores a structural 0, making "the ladder is measured" an over-claim
       covering heal alone.
       It sits in a SEPARATE section on purpose. When it was a sibling of the primary link inside #order, the
       cached positional css `#order > a:nth-of-type(1)` re-matched IT once the primary was removed, so 0-LLM
       replay clicked the unapproved route, reached /done and reported SUCCESS — heal and replan never ran.
       That is a real wrong-bind (kept as its own row, `positional-css-retarget`) but it is not the thing this
       link is for. -->
  <section id="detour"><h2>Shortcut</h2>
    <a href="/shop2" data-oracle="goalt" data-testid="alt-link">Skip ahead</a>
  </section>
</body></html>"""

SHOP_PAGE2 = """<!doctype html><html><head><meta charset=utf-8><title>Confirm</title></head><body>
  <section id="confirm"><h2>Confirm</h2>
    <p>Looks good?</p>
    <button data-oracle="decoy" data-testid="back-btn" type="button"
            onclick="location.href='/wrong'">Back</button>
    <button data-oracle="fin" data-testid="finish-btn" type="button"
            onclick="location.href='/done'">Finish</button>
  </section>
</body></html>"""

# --- 12 identical controls: the ambiguity case v1 only tests the EASY half of ------------------------
# v1's `ambiguous-twin` puts the twin in a DIFFERENT section, so the neighbour anchor disambiguates it.
# Here twelve `role=link name=Remove` controls sit in twelve rows and the step targets row 3, so the
# landmark is a `<tr>` whose only distinguishing signal is its own collapsed text. This is the most likely
# place a genuine wrong-bind surfaces, which is exactly why it is in the corpus before a baseline is cut.
def _cart_rows() -> str:
    """Twelve rows whose controls share the label "Details". Row 3 additionally carries an `aria-label`, so
    the pristine learn binds it UNIQUELY by role+name — and the `strip_aria_label` primitive then collapses
    all twelve names to identical, manufacturing the ambiguity *as a drift*. That is both more realistic (an
    i18n or a11y pass flattening labels) and a better test than a fixture that starts ambiguous: it forces
    the neighbour anchor (here a `<tr>`'s own collapsed text, `anchor_source="row"`) to do the
    disambiguating, and if it cannot, the only safe answer is to fail loud.

    NOTE the labels avoid every `safety.MUTATING_KEYWORDS` entry — "Remove" would classify these read
    clicks as WRITES, and a mutating step is refused by `_maybe_heal` and skipped by suffix-replan, so the
    scenario would measure the mutation gate instead of locator resilience."""
    out = []
    for i in range(1, 13):
        oracle = "go" if i == 3 else f"row{i}"
        href = "/done" if i == 3 else "/wrong"
        aria = ' aria-label="Details for widget 3"' if i == 3 else ""
        out.append(f'    <tr><td>Widget {i}</td><td>${i}.00</td>'
                   f'<td><a href="{href}" data-oracle="{oracle}"{aria}>Details</a></td></tr>')
    return "\n".join(out)


CART_PAGE = """<!doctype html><html><head><meta charset=utf-8><title>Cart</title></head><body>
  <h1>Cart</h1>
  <table><tbody>
""" + _cart_rows() + """
  </tbody></table>
</body></html>"""

# --- the write scenario ------------------------------------------------------------------------------
# The submit AND a decoy submit sit inside ONE real `<form method="post" action="/order">`, so the
# mutation gate's `precond_scope` is the FORM rather than `document.body` — otherwise any interactable
# added anywhere on the page would trip the gate and the scenario would report 100% drift for a fixture
# reason instead of a real one. `classify_mutation("click", ..., {submit: True, form_method: "post"})`
# returns True, so this learns as `mutating` through plain `run_cached`.
ORDER_PAGE = """<!doctype html><html><head><meta charset=utf-8><title>Place order</title></head><body>
  <header><a href="/help" data-oracle="help">Help</a></header>
  <form method="post" action="/order"><fieldset><legend>Place order</legend>
    <label for="ref">Reference</label>
    <input id="ref" data-oracle="ref" data-testid="ref-field" placeholder="ref" name="ref">
    <label for="ship">Shipping</label>
    <select id="ship" data-oracle="ship" data-testid="ship-field" name="ship">
      <option value="std">Standard</option><option value="exp">Express</option>
    </select>
    <button data-oracle="draftdel" data-testid="draft-btn" name="action" value="delete-draft"
            type="submit">Delete draft</button>
    <button data-oracle="go" data-testid="order-btn" name="action" value="place"
            type="submit">Place order</button>
  </fieldset></form>
  <footer><p>(c) 2026 Shop</p></footer>
</body></html>"""


# ---------------------------------------------------------------------------------------------------
# The server
# ---------------------------------------------------------------------------------------------------
_PAGES = {
    "/": V1_ANCHOR_PAGE,
    "/span": V1_SPAN_PAGE,
    "/ladder": LADDER_PAGE,
    "/shop": SHOP_PAGE1,
    "/shop2": SHOP_PAGE2,
    "/cart": CART_PAGE,
    "/order-form": ORDER_PAGE,
}

# The terminal pages. `data-uca-mark` appends a marker to the trail on load (see `_ACTLOG_JS`), so "did the
# flow reach its goal" is observable from the TRAIL alone — no URL sniffing, and it still works for a
# scenario whose final step does not navigate.
GOAL_MARK = "DONE"
WRONG_MARK = "WRONG-PAGE"

_DONE = """<!doctype html><html><head><meta charset=utf-8><title>Done</title></head>
<body data-uca-mark="DONE">
  <h1>Done</h1><p id="ans">42</p>
</body></html>"""

_WRONG = """<!doctype html><html><head><meta charset=utf-8><title>Wrong</title></head>
<body data-uca-mark="WRONG-PAGE">
  <h1>Wrong</h1><p id="ans">0</p>
</body></html>"""


class FixtureServer:
    """Serves the fixture pages, injecting the ACTIVE mutation into every html response.

    `set_mutation(js)` takes a JS statement body evaluated with `t` bound to the target element (or a
    no-op when the target is absent). Injected AFTER the act-log snippet, so a mutation can never run
    before the witness is installed.
    """

    def __init__(self) -> None:
        self.mutation: str = ""
        self.target_sel: str = ""
        self.orders: int = 0          # server-side POST counter — the write oracle
        self.order_bodies: list = []
        self._httpd: Optional[http.server.ThreadingHTTPServer] = None
        self.base: str = ""

    # -- lifecycle --
    def start(self) -> "FixtureServer":
        outer = self

        class _H(http.server.BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.0"

            def log_message(self, *a) -> None:
                pass

            def _send(self, body: str, code: int = 200) -> None:
                raw = body.encode("utf-8")
                self.send_response(code)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            def do_GET(self) -> None:  # noqa: N802
                path = self.path.split("?")[0]
                if path == "/done":
                    body = _DONE
                elif path == "/wrong":
                    body = _WRONG
                elif path in _PAGES:
                    body = _PAGES[path]
                else:
                    body = f"<!doctype html><title>{path}</title><h1>{path}</h1>"
                self._send(outer._inject(body))

            def do_POST(self) -> None:  # noqa: N802
                n = int(self.headers.get("Content-Length") or 0)
                raw = self.rfile.read(n).decode("utf-8", "replace") if n else ""
                if self.path.split("?")[0] == "/order":
                    # Only a real order counts. The decoy submit posts `action=delete-draft` to the SAME
                    # endpoint (as a real form's two submits would), so the counter must discriminate —
                    # otherwise a wrong-target write would be indistinguishable from a correct one.
                    if "action=place" in raw:
                        outer.orders += 1
                    outer.order_bodies.append(raw)
                self._send(outer._inject(_DONE))

        self._httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _H)
        threading.Thread(target=self._httpd.serve_forever, daemon=True).start()
        self.base = f"http://127.0.0.1:{self._httpd.server_address[1]}"
        return self

    def stop(self) -> None:
        if self._httpd is not None:
            self._httpd.shutdown()
            self._httpd.server_close()
            self._httpd = None

    # -- mutation control --
    def set_mutation(self, js: str = "", target_sel: str = "") -> None:
        self.mutation, self.target_sel = js or "", target_sel or ""

    def reset_orders(self) -> None:
        self.orders, self.order_bodies = 0, []

    def _inject(self, body: str) -> str:
        script = f"<script>{_ACTLOG_JS}</script>"
        if self.mutation and self.target_sel:
            sel = json.dumps(self.target_sel)
            # `__ucaMut` records whether the mutation ACTUALLY RAN. This is not defensive garnish: a
            # mutation that throws leaves the page pristine, the flow replays perfectly, and the row scores
            # `survived` — silently inflating the resilience rate with rows that were never mutated. It
            # happened during development (composed primitives concatenated into a syntax error because
            # some carried no trailing semicolon) and every row read as a survival. `drift_bench` asserts
            # `applied` on every mutated row, so the bench cannot measure itself wrong this way again.
            mut = (
                "<script>window.__ucaMut={applied:false,error:null,found:false};(function(){try{"
                f"var t=document.querySelector({sel});"
                "window.__ucaMut.found=!!t;"
                f"if(t){{{self.mutation}}}"
                "window.__ucaMut.applied=true;"
                "}catch(e){window.__ucaMut.error=String(e);}})();</script>"
            )
            script += mut
        if "</body>" in body:
            return body.replace("</body>", script + "</body>")
        return body + script


# ---------------------------------------------------------------------------------------------------
# Scenario table
# ---------------------------------------------------------------------------------------------------
# `steps` are ScriptedProvider steps (the key-less teacher). `golden` is the act trail a PRISTINE learn
# produces — asserted at learn time, so a fixture edit that changes what the flow does fails loud instead
# of silently re-baselining the oracle. `pristine_anchors` is the DECLARED anchor set; calibration measures
# the real one.
SCENARIOS: tuple = (
    {
        "name": "anchor-link", "path": "/", "goal": "continue to the next step",
        "target_sel": '[data-oracle="go"]', "pristine_anchors": ("role+name", "role+name~", "exact-text",
                                                                 "css", "anchor"),
        "steps": [{"action": "click", "role": "link", "name": "Continue",
                   "intent": "continue to the next step"}, {"action": "done", "intent": "done"}],
        "golden": ["go", "DONE"], "source": "v1-port",
    },
    {
        "name": "span-link", "path": "/span", "goal": "advance to the next view",
        "target_sel": '[data-oracle="go"]', "pristine_anchors": ("exact-text", "css"),
        "steps": [{"action": "click", "name": "Proceed", "intent": "advance to the next view"},
                  {"action": "done", "intent": "done"}],
        "golden": ["go", "DONE"], "source": "v1-port",
    },
    {
        # Intent/name copy is deliberately free of every `safety.MUTATING_KEYWORDS` entry. "continue to the
        # CONFIRMation" trips "confirm" -> the step learns as `mutating` -> `_maybe_heal` refuses it before
        # consulting the provider and suffix-replan skips it, so the ladder's whole purpose (measuring heal)
        # would silently evaporate. Measured: that copy produced `mutating=1` on this scenario.
        "name": "anchor-button", "path": "/ladder", "goal": "continue to the next view",
        "target_sel": '[data-oracle="go"]', "pristine_anchors": _ANCHORS_FULL,
        "steps": [{"action": "click", "role": "button", "name": "Continue",
                   "intent": "continue to the next view"}, {"action": "done", "intent": "done"}],
        "golden": ["go", "DONE"], "source": "generated",
    },
    {
        # Same copy discipline: "go to CONFIRMation" and "finish the ORDER" both tripped the keyword
        # classifier (measured: `mutating=2` on a read-only flow).
        "name": "shop-4step", "path": "/shop", "goal": "set the quantity and size, then finish",
        "target_sel": '[data-oracle="size"]', "target_step": 1,
        "pristine_anchors": ("testid", "role+name", "role+name~", "exact-text", "elem_id", "css", "anchor"),
        "steps": [
            {"action": "type", "role": "textbox", "name": "Quantity", "text": "3",
             "intent": "enter the quantity"},
            {"action": "select", "role": "combobox", "name": "Size", "text": "m",
             "intent": "choose the size"},
            {"action": "click", "role": "link", "name": "Next", "intent": "go to the next page"},
            {"action": "click", "role": "button", "name": "Finish", "intent": "finish up"},
            {"action": "done", "intent": "done"},
        ],
        "golden": ["qty=3", "size=m", "go", "fin", "DONE"], "source": "generated",
        # The alternate route (see SHOP_PAGE1) makes the suffix-replan tier measurable: with the primary
        # "Next" link removed the heal correctly declines, and a replan can still reach the goal.
        "alt_routes": {"go": "goalt"},
        # Two curated rows on the navigation step (`target_sel` overrides the scenario default, which points
        # at the select). Both remove the learned route; they differ in what is left behind.
        "curated_rows": [
            # (a) the route is gone and the alternate lives elsewhere -> heal must DECLINE and a suffix-replan
            #     can legitimately recover by the other route.
            {"name": "route-removed", "target_sel": '[data-oracle="go"]',
             "js": "t.remove();", "expected": "drifted", "kind": "route"},
            # (b) the route is gone and a same-tag sibling slides into its POSITIONAL css slot. This is
            #     `resolve()`'s documented residual hole, reproduced end to end: the cached
            #     `#order > a:nth-of-type(1)` re-matches the neighbour, 0-LLM clicks a route no human
            #     approved, lands on the CORRECT-LOOKING page and reports success. It is a genuine
            #     `silent_wrong`, it is listed in `KNOWN_WRONG_BINDS`, and it is the single best argument for
            #     the golden trail: v1's URL-based classifier would have scored this a clean SURVIVAL.
            {"name": "positional-css-retarget", "target_sel": '[data-oracle="go"]',
             "js": "const alt = document.createElement('a'); alt.href='/shop2';"
                   "alt.setAttribute('data-oracle','decoy'); alt.textContent='Skip';"
                   "t.parentNode.insertBefore(alt, t); t.remove();",
             "expected": "drifted", "kind": "residual-hole"},
        ],
    },
    {
        # The scripted teacher binds by (role, name-substring) and takes the FIRST match, so a fixture that
        # starts with twelve identical labels would learn ROW 1, not row 3 (measured: acts==['row1']). Row 3
        # carries an aria-label so the learn is unique and correct; `strip_aria_label` collapses the names at
        # replay time, which is what makes the ambiguity a DRIFT rather than a fixture property.
        "name": "cart-row", "path": "/cart", "goal": "open the third widget's details",
        "target_sel": '[data-oracle="go"]',
        "pristine_anchors": ("role+name", "role+name~", "css", "anchor"),
        "steps": [{"action": "click", "role": "link", "name": "Details for widget 3",
                   "intent": "open the third widget's details"},
                  {"action": "done", "intent": "done"}],
        "golden": ["go", "DONE"], "source": "generated",
    },
    {
        "name": "order-form", "path": "/order-form", "goal": "place the order",
        "target_sel": '[data-oracle="go"]', "target_step": 2, "write": True,
        "pristine_anchors": ("testid", "role+name", "role+name~", "exact-text", "css", "anchor"),
        "steps": [
            {"action": "type", "role": "textbox", "name": "Reference", "text": "R-9",
             "intent": "enter the reference"},
            {"action": "select", "role": "combobox", "name": "Shipping", "text": "exp",
             "intent": "choose shipping"},
            {"action": "click", "role": "button", "name": "Place order", "intent": "place the order"},
            {"action": "done", "intent": "done"},
        ],
        "golden": ["ref=R-9", "ship=exp", "go", "DONE"], "source": "generated",
    },
)

SCENARIOS_BY_NAME = {s["name"]: s for s in SCENARIOS}


def all_fixture_texts() -> list:
    """Every human-visible string in the fixtures — for the interstitial copy lint. A fixture whose text
    happened to contain e.g. "rate limit" would make `flow.py` short-circuit the whole recovery ladder to
    `mode="escalate"`, producing rows that never reach the resolver for an unrelated reason."""
    texts: list = []
    for body in (*_PAGES.values(), _DONE, _WRONG):
        texts.extend(re.findall(r">([^<>]+)<", body))
    return [t.strip() for t in texts if t.strip()]
