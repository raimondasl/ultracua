# A dry run for browser automation that actually holds

*Four ways a "nothing will be saved" guarantee breaks silently, each found by measuring rather than
by reading documentation. Extracted from ultracua at close-out, 11 September 2026.*

> **Every claim here was measured against Playwright 1.60.0 and headless Chromium, with a local
> server counting the bytes that actually arrived.** None of it was read from documentation. They are
> facts about somebody else's library on one date, so if you are relying on them, re-derive them
> against your own version before you do — the code that produced these numbers is in the repository
> and the measurements are in its tests.

---

## The problem

You have automation that drives a browser through a real application, and the application has no
staging copy. You want to know what the automation *would* do before you let it do it.

The obvious answer is to intercept the browser's network traffic and drop anything that looks like it
changes data, recording it instead. Playwright and Puppeteer both make that a few lines:

```python
await context.route("**/*", handler)
```

**The guarantee is the whole feature.** A dry run that writes for real is worse than no dry run at
all, because you will trust it. So the design rule throughout is: *anything that cannot be proven
held aborts the run.*

That rule is easy to state and has four non-obvious ways of being broken. Each of the four below was
found by building the naive version first, then measuring what actually reached the server.

---

## 1. Letting "harmless" requests through is an arbitrary-write channel

**The obvious design.** Hold anything your write-classifier matches; let everything else continue to
the network. That seems strictly better than blocking everything — the page keeps working, analytics
still flow, and you have only stopped the dangerous traffic.

**What breaks.** The interception layer sits between the page and the network, and it is notified
once per *request*. It is **not** notified about redirect hops. So a request you release, which is
then answered with a **method-preserving redirect** — HTTP 307 or 308 — spawns a second request your
handler never sees. And that second request carries the **original body**.

Measured, with a server counting arrivals:

| chain | hops intercepted | hops that reached the server |
|---|---|---|
| `POST → 307 → POST` | 1 of 2 | **both** |
| `POST → 307 → 307 → POST` | 1 of 3 | **all three** |
| form-POST navigation `→ 307` | 1 of 2 | **both** |

So any host you have classified as safe — an analytics endpoint, a CDN, anything on a vendor
allowlist — can convert a "dry" run into a real write to **any endpoint it names**, carrying the
page's own payload. Your report shows nothing, because your handler was never called for the hop that
landed.

**The fix is policy, not enumeration.** Only idempotent methods may reach the network at all:

```python
IDEMPOTENT_METHODS = ("GET", "HEAD", "OPTIONS")
```

Everything else is fulfilled locally, telemetry or not. Classification then decides only
*record-versus-abort*, never *hold-versus-release* — which is what makes the invariant checkable by
reading one branch.

This closes redirect-born writes **structurally** rather than by listing them, and the argument is
short enough to verify: no redirect status turns a GET into a POST (302 and 303 degrade toward GET;
307 and 308 preserve the method), and a request that never reaches a server cannot be redirected by
one.

Worth noting what this costs: the page's analytics break during a dry run. That is the correct trade
and it should be stated in the report rather than quietly worked around.

---

## 2. Intercept at the browser context, never at the page

**The obvious design.** `page.route(...)`. It is the one every tutorial shows, and it is the object
you already have.

**What breaks.** A `fetch(POST)` issued from a **service worker** is not delivered to a page-level
handler. It is delivered to a context-level one. Under `page.route` it is not seen, and it **reaches
the server**.

This is not an exotic case — service workers are ordinary on any application with offline support or
push notifications, and the traffic they carry is frequently the interesting kind.

**The fix.** Install on the context, and additionally set `service_workers="block"` so a worker
cannot forge a confirmation the automation is waiting on.

**Know the limit of this.** Context scope is *wider* than page scope; it is not universal. A write
issued from a **shared worker** — a script that runs outside any single tab and is shared between
them — reaches the server and is visible at **neither** scope, and `context.route` does not catch it
either. That was measured too, and it stayed an open defect. If your target uses shared workers, this
design does not cover them, and no amount of route configuration will change that. You need a proxy
or a raw debugger-protocol client.

---

## 3. Answer a held form submission with `204`, not with a fake page

**The obvious design.** When you hold a navigation-triggering form POST, fulfil it with some
synthetic HTML so the page has something to render.

**What breaks.** Fulfilling a form-POST navigation **navigates the document to the form's action
URL**. If the automation is checking "did we land on the confirmation page?" — and confirmation
barriers very often are URL checks — a flow whose confirmation URL *is* its own form action will see
a genuine URL transition and **pass a barrier for a write that never happened**.

That is the worst possible failure for this tool: not a missed write, but a fabricated success.

**The fix.** Fulfil with HTTP `204 No Content`. The browser stays on the pre-write document, so
nothing downstream can misread a navigation that did not occur.

`route.abort()` is worse than either: it leaves the page at `chrome-error://chromewebdata/`, and the
document is destroyed.

---

## 4. In-page patching may only cover what interception cannot see

**The obvious design.** Some channels are awkward to intercept, so patch them out in the page —
inject a script that replaces `fetch`, `navigator.sendBeacon` and friends with no-ops.

**What breaks.** A patch that *swallows* a call hides it from the interception layer entirely. Two
distinct harms:

- A `sendBeacon` drop-patch **deletes the interception layer's own evidence** for that channel. You
  no longer hold the beacon; you make it invisible, and your report shows a beacon-free run.
- A `fetch` drop-patch additionally breaks anything else that waits on requests — in this codebase it
  zeroed the held-write count while looking perfectly safe.

**The fix.** Measure which channels the route layer actually holds, and patch only what it provably
cannot see. The committed tests cover four — `fetch`, `navigator.sendBeacon`, a form-POST navigation
and `XMLHttpRequest` — and the route layer holds all four. For those, in-page code **observes and
calls through** rather than swallowing.

One honesty note about that list, since this document is being published as evidence. The original
source comment cited the beacon measurement against four channel names, three of which
(`pagehide`, `visibilitychange`, `<a ping>`) left no committed test, and one of which named a fixture
that exists nowhere in the repository at all. Those measurements were real when they were taken and
they are not reproducible from the tree, so do not treat them as covered. The four above are.

Only genuinely uninterceptable channels get poisoned — and poisoned loudly, so reaching one raises
rather than silently doing nothing:

```javascript
poison('RTCPeerConnection');
poison('webkitRTCPeerConnection');
poison('WebTransport');
```

"We could not observe this" is not a safe outcome. It is an abort.

---

## Two ledgers, because one cannot check itself

The route layer records every request it handled. A second, independent listener on the context
records every request the browser *emitted*. At the end of the run, the two are reconciled: any
non-idempotent request the event layer saw and the route layer did not is a request that escaped.

Under the GET-only policy above, that is structurally unreachable — which is exactly why it is worth
checking. **It makes the mechanism re-prove itself on every run rather than once per library
version.** When Playwright changes something, you find out from a failing run rather than from a
silent write.

The same instinct covers handler faults. Every path through the handler is wrapped, because an
unwrapped exception leaves the page's `fetch` **pending forever** — measured: it never resolves and
never rejects, the server gets nothing, and the library re-raises the error attached to an unrelated
later call. That is fail-closed, and indistinguishable from "this step issued no writes". A latch
turns it into a loud, attributed abort instead.

---

## What it refuses to claim

Three things, all of which a less careful version would get wrong by being confident.

**After the first hold, the page is running on fiction.** A held write is answered with a synthesised
response, so every subsequent step executes against page state that never existed — write #2's body
may be computed from a reply that was invented. The report therefore states three numbers, not two
(writes planned, writes reached, requests held), and explicitly labels steps after the first hold as
unrepresentative.

**Confirmation barriers are not evaluated at all.** They are reported as `held — unverifiable`.
Anything else would be scoring a check against a fabricated response.

**Attribution has three states, not two.** Which step caused a given held write is a genuinely hard
question when writes arrive late. The states are *attributed* (only one step had acted, so only one
step could own it), *ambiguous* (several had), and *ungated* (it arrived outside any step's window).
Collapsing ambiguous into "unknown step" would mean one value carrying two meanings a consumer cannot
separate — and two earlier drafts of that function tried to narrow the candidate set with a timing
constant, which made the same causal situation report "ambiguous" at one setting and confidently name
the **wrong** step at another.

There is no `success` field on the report, deliberately. A run that holds one write and stops is both
a complete, useful dry run *and* a flow not shown to work; any boolean would be read as "safe to
approve".

---

## Using it

The implementation is `src/ultracua/dryrun.py` — 364 lines, and its only dependencies are a logger
and a one-line write classifier (non-idempotent method, not a known telemetry host). It is written
for Playwright's Python API and the ideas port directly to the Node API and to Puppeteer.

Its tests are in `tests/test_dryrun.py` and `tests/test_dryrun_attribution.py` — 18 of them, 15 of
which need a real browser. The two worth reading first are
`test_a_released_post_would_leak_through_a_307_redirect`, which proves the fixture really produces
the leak, and `test_the_arbiter_holds_the_redirect_chain_at_the_first_hop`, which proves the policy
closes it. That pairing is the pattern: prove the hazard exists before proving you closed it,
otherwise the second test passes against a fixture that was never dangerous.

**This project is archived and nobody is maintaining this.** Copy it, read it, or ignore it. If you
copy it, re-run the four measurements against your own library version first — every number above is
a claim about someone else's release, and the whole point of the exercise was not taking those on
trust.

---

*Part of [ultracua](https://github.com/raimondasl/ultracua). The write-up of how these were found —
and why the test suite found none of them — is [`docs/method.md`](method.md).*
