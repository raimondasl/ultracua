# ultracua — working notes

A Computer Use Agent: **learn a browser flow once, replay it deterministically at 0-LLM, 5–10× faster,
failing LOUD on drift.**

## Read this first

**[docs/open-defects.md](docs/open-defects.md) — the standing defect register.** FOUR rounds. Rounds 1–2
(30 findings) are fixed; **round 3 found 11 more in the 387 lines those fixes added, 1 critical, and
refuted NONE of them** (**2 open** at 0.106.0 — R3.2, R3.7; the count is asserted by
`tests/test_register_count.py`, which caught this very line going stale inside the slice that wrote it —
R3.6 closed after the number was typed); round 4 was a pre-merge audit that PARKED a change rather than
ship it. Two are regressions the fixes introduced. Defect density in fix code measured ~3x
the code it replaced, so **a patch on a patch is the thing to be most suspicious of here** — three round-3
findings are the same shape as the finding they were fixing, one level down. When you close something,
change the shape so the invariant is enforced ONCE rather than adding another per-branch test.

**Two of round 3 were answered that way in 0.73.0** (R3.1, R3.4). **R3.7 has now defeated TWO attempts,
both of which turned a LOUD false refusal into a SILENT wrong-record bind by different routes** — 0.100.0
made the two DOM walks agree (losing containment: an ancestor borrows a nested row's identity), 0.103.0
decoupled the identity from the anchor text (an identity-less wrapper captures `anchor_id=None`, which
`resolve` reads as "no guard"). Both passed the full suite, `drift_bench` with every invariant holding,
and their own purpose-built property matrices. **D5's two-strikes gate now applies: attempt 3 must change
the SENSOR CLASS.** The overloaded thing is `anchor_id=None`, which means both "this row has no
discriminating token" (accepted residual) and "I looked in the wrong container" (a silent disarm), and
`resolve` cannot tell them apart. R4.34 and R4.37 are the other two doors into the same fault; a fix that
does not dispose of all three has now failed twice. **A fourth
(R3.2, write attribution) has now defeated three attempts** — 0.73.0's drain (reverted), 0.74.0's first
refusal draft (over-refused), and a 0.76.0 causal-attribution branch that was green and still wrong
(**parked**, see `docs/parked/README.md`). Read that history before touching attribution: it rules out
every purely temporal design, and it shows that green is not evidence here. **A fifth attempt is now
BLOCKED by decision `D5`** in the register — refuse-or-over-gate plus human adjudication IS the design,
and unblocking it needs a new SENSOR CLASS measured against the existing artifacts first, not a better
inference. The general gate D5 applies (**two strikes, then change the sensor class**) is at the top of
the same file.

**WORK FROM THE PLAN.** `docs/correctness-plan.md` sequences every open finding, test hole and unpinned
residual into slices, worst user harm first, with the dependencies between them made explicit (the net
gets strengthened before it is relied on; a hole-widener never lands before its hole-fix). Picking items
ad hoc re-creates orderings the plan exists to prevent. `docs/correctness-survey.md` is the 58-item
inventory it must dispose of.

**Audit the fix, not just the code it fixes.** The reverted 0.73.0 redesign (754 tests green,
`drift_bench` clean, every regression test verified RED against pre-fix source), the parked 0.76.0
branch (785 tests, same clean bench), and BOTH R3.7 attempts (0.100.0 and 0.103.0 — each with its own
purpose-built property matrix green, bench invariants all holding, and 0.103.0 additionally showing
survival UP at every k with zero rows regressed) were critically wrong. **Green is not evidence in this
codebase.** Four rounds running, fix code has been the defect source, and the only instrument that has
ever caught it is an adversarial pass aimed squarely at the new code — five for five. Run one before you
open the PR, not one release later.

**And note what the two R3.7 attempts have in common: each was blind to a population no instrument
contained.** Attempt 1 died on nested rows sharing an identity string; attempt 2 died on a nested wrapper
that owns no identity at all. Both times every existing shape — 30 test cells and 185 corpus rows — was
built from the same mental image of a row, so the gap was invisible to all of them at once. When a fix
here looks clean, the question to ask is not "did the tests pass" but "what shape is not in them".

Two things about HOW to run it, both learned by getting them wrong. Point it at a **committed** branch in
an **isolated worktree**: an auditor that can edit the tree it is auditing will revert the file under
test to prove a point and silently invalidate whatever suite run you have in flight. And **reproduce its
findings yourself before acting on one** — the first S11 audit's critical was real, but its end-to-end
arm depended on a page detail the finding did not state, and only a hand-written repro showed which part
was load-bearing.

## The three inviolables

Violating any of these is a blocking defect, not a trade-off:

1. **Replay never calls an LLM.**
2. **Never silently return or act WRONG — fail LOUD.**
3. **Write safety** — never double-submit a write, never silently suppress one.

## How work is done here

- **`uv` for everything.** `uv run --no-sync pytest tests/... -q`, `uv run --no-sync python -m benchmarks.X`.
  Never bare `uv sync` (it strips groups) — use `uv sync --all-groups`.
- **A LOCAL GREEN IS WEAKER EVIDENCE THAN CI, IN TWO MEASURED WAYS. Both have shipped a red PR.**
  1. *Platform*: CI runs ubuntu AND windows; local runs here are windows-only (see the fixture-race note
     below).
  2. *Keys*: **importing `ultracua.config` loads `.env` into `os.environ`**, so anything that reaches for
     a provider silently works here and raises `TypeError: Could not resolve authentication method` on
     CI. That is not hypothetical — a test passing `provider=None` to `learn()` made `fixed` False,
     built a live Anthropic client, and was driving the agent with REAL API CALLS locally while failing
     both CI arms (S8/0.84.0). **Run the suite the way CI does before believing it is green:**

         ANTHROPIC_API_KEY= uv run --no-sync pytest tests/ -q

     `load_dotenv` does not override a variable that is already set, so an empty value is enough. When a
     test needs an agent, pass a `ScriptedProvider` AND a mock `Router` — `learn()` only skips building a
     real provider when BOTH are supplied.
- **CI runs the suite on ubuntu AND windows; local runs here are windows-only.** A green local suite is
  therefore half the evidence. The browser fixtures are where this bites: anything whose page state is
  revealed inside a `fetch(...).then(...)` races the agent's next observation, and Linux loses that race
  where Windows wins it. It has already shipped once — an S3 fixture captured a 1-step recipe on ubuntu
  and the test failed with a meaningless "DID NOT RAISE". **Reveal fixture state synchronously, and pin
  the premise** (assert the recipe has the steps the test depends on) so a lost race fails LOUD instead
  of silently testing nothing.
- The full suite is **key-less** — real headless Chromium against local fixtures, no API key, **~21 min**
  and growing (15 → 18 → 21 over the last several releases). It must be green before a commit. Locally,
  run it whole; **CI shards it across two runners per OS** because it had reached 21m53s against a
  25-minute job timeout, which was a deterministic failure approaching.
- **A shard must never be a hole.** `pytest-split` partitions the real collection, so a new test file
  lands in a shard by construction — but the `shard-coverage` CI job asserts it (union == full, no
  duplicates), because a test in NO shard leaves every shard green and nothing in the suite can fail for
  it. Regenerate balance with `pytest -q --store-durations` when the suite's shape changes a lot;
  a stale `.test_durations` costs WALL-CLOCK balance only, never coverage.
- **The browser pool is deferred, now with a number.** 356.7 ms per session with its own Chromium vs
  84.7 ms sharing one — a 2.9 min ceiling over ~650 sessions — and it cannot reach the suite without
  moving all tests onto a session-scoped event loop, because a Playwright `Browser` is loop-bound. See
  `STATUS.md`; do not reach for it as a reaction to a CI flake (R4.22 records the one that did not
  justify it).
- **One slice per PR**, branched off `main`, single-sourced version bump in `pyproject.toml` first
  (then `uv sync --all-groups`). The user reviews and merges; don't merge.
- **Re-run `uv sync --all-groups` after any branch switch that changes the version.** The venv keeps the
  metadata of whatever was installed last, so `test_version_is_single_sourced_from_pyproject` fails with
  a real-looking assertion (`'0.76.0' == '0.75.0'`) that is purely environmental. Diagnose it before
  believing it — and before "fixing" the version to match a stale venv.
- Secrets are env-resolved and **never** serialized, logged or written to disk. Never paste a key value.
- Keep large/working data off `C:` (`D:\ultracua-data`). `.env`, `.ultracua`, `.scratch` are gitignored;
  `baselines/` is committed.

## The suite is regression-shaped — measured, and it matters

Nine mutation tests (one per defect class this project has shipped) are ALL caught, entry-point coverage
is broad, and the tests flagged as unfalsifiable turned out to be fine. The suite is not weak. But
**mutation testing only probes guards that exist, and every defect here has been a guard that was
MISSING** — ~60 findings across four audit rounds, *not one discovered by the suite*. It proves known
bugs stay fixed; it cannot fail for a bug nobody has thought of.

So when you fix something in write safety, add a DIMENSION to
`tests/test_write_safety_invariants.py` — which asserts the inviolable as a property over a
cross-product — rather than only a bespoke test beside the fix. And note both halves are load-bearing:
without the "must remain learnable" clause the property is satisfied by refusing everything, which is a
regression that actually shipped. When you build such a matrix, PRINT what each cell exercised before
believing it; two drafts of that file looked thorough while testing nothing.

## Two habits that have repeatedly paid off

**Reproduce before fixing.** Several reported defects have been misdiagnosed — the symptom real, the stated
cause wrong. The most recent: an audit blamed a row anchor's substring matching when the anchor never ran at
all. A fix built on a wrong diagnosis is worse than none.

**And reproduce the MITIGATION, not just the defect** — a finding's own hedge is the least-examined
sentence in this register. R3.12 was rated medium because a mislabelled row would show "commit B's intent
beside commit A's key"; reproducing it showed the key is a context header live for whichever step is
mid-act, so both fields come from ONE source, the row is internally consistent, and nothing betrays it.
Two independently-sourced facts is the standard R3.3 already set for `landed`; check that they really are
two before pricing a defect on it. The same measurement exposed R4.39, a worse sibling one path over.

**A green property is worth exactly what its STUB is worth — so test the stub.** S14's `no_llm` fixture
said an LLM was "unreachable in BOTH directions", and on the path that mattered it was inert: `flows.py`
does `from .providers import build_router`, so patching `ultracua.providers.build_router` never reached
`ultracua.flows.build_router`. Nothing went red for several releases because every cell drove a module
that imports no factory. Two rules fall out, both cheap: patch a factory on EVERY binding (derive the
list from the live import graph, never hand-list it), and give the stub its own anti-vacuity test. The
same slice found a cell that was green while exercising nothing at all — auth-refresh refuses early
without a `storage_state`, so the path under test never ran. **If a cell claims to reach a mechanism,
count the mechanism's calls and assert the count.**

**A test that ASSERTS the counterexample is worse than no test.** R3.12's first fix draft used a tuning
constant as its candidate horizon, and a cell in its own matrix — `an expired tail is not a candidate` —
was the proof that the constant could name a WRONG step, sitting there green and asserted as correct
behaviour. The matrix was built to answer "what shape is missing"; it contained the refuting shape and
called it a pass. When you write a cell whose expected value differs from a neighbouring cell's, and the
only difference between them is a CONSTANT, that is not two cells — that is a counterexample.

**Verify a regression test fails against the old code.** A test that passes both before and after proves
nothing. This has caught several no-op "fixes".

**The instrument can suppress the defect — and a rare bug is a harness problem, not a patience problem.**
R4.36 is the third time this paid: a CI-only write-refusal MISS (NARROWED, not closed — see R4.38) reproduced 4/4 by making the turn-reset
timer overdue ON PURPOSE — a busy-waiting click handler plus one keystroke queued during the block —
instead of waiting for a starved renderer. Its harness carries a premise worth copying, `saves == 1`:
with three keystrokes the flow refuses for a LATER write, so the test passes while the misattribution
it exists to catch survives untouched.
R4.26 reproduced 1 run in 40 under load and **zero in 150** once an in-page probe was added to watch it.
Waiting longer with a heavier instrument was never going to produce the trace. The deterministic harness
built instead REFUTED the inferred cause on its first run — the guess was that an overdue timer preempts
the turn-reset, and it does so only when the later commit arrives as an INPUT task, never as a timer task.
Build the mechanism on demand; do not fish for it. The same move turned a 1-in-40 field race into an 8/8
RED test that needs no artificial load at all.

**A green suite is not a green change — run `drift_bench` too, and know the host's load.** S6/AB-1's
third draft passed **867 tests** and failed the bench's `ambiguous_disambiguated` invariant. Nothing in
the suite is shaped to notice a resolver regression or a cost; the bench is. So any change to what runs
IN the page — an init script, a listener, a patched entry point — gets a bench run before the PR.

**And the other half of that lesson, learned the same hour: a loaded host cannot adjudicate either.**
That bench run happened while this machine was at ~100% memory and swapping, where a heal that misses
its budget records `drifted` — which is what the failing invariant reads. A controlled A/B minutes later
showed the change costing nothing. Timing numbers and timing-sensitive invariants taken under unknown
load are not evidence; say so in the register rather than quoting them, and let CI decide.

**A timer is not a boundary.** `setTimeout(..., 0)` as a "the turn ended" marker is a bet on the
scheduler, and R4.26 is what losing it costs. When something needs to know that control left a scope, take
the fact from the platform (`window.event` for "a dispatch is in progress") rather than from the clock.

## The keyword classifier is broken, stays, and must not carry a refusal

`safety.MUTATING_KEYWORDS` is a bare substring match. Measured: **28% false positives** on ordinary
read-only controls ("Show borders" → `order`, "Sender" → `send`, "Payment history" → `pay`) and it
catches only **45%** of genuine writes without form context ("Save", "Apply", "Add to cart", "Approve"
all read as reads). **No matching rule fixes it** — word boundaries un-gate 16 of 21 real commits
(`Reorder`, `Resend`, `Ordering`), and the affix-aware variant that keeps every write removes only 3 of
20 false positives, because the survivors are deverbal nouns (`payment`, `sender`, `transfers`)
morphologically identical to the verb. It cannot be **removed** either: it is the only signal available
BEFORE acting, which `flow.py`'s `block_mutations` needs — wire evidence is post-hoc by construction.

So the operative rule, and the thing to stop anyone re-deriving: **a `mutating` mark is a GUESS, not
evidence.** Be conservative because of one (gate it, key it, refuse to re-author it); never refuse a
FLOW because of one. That is decision D0, rejected after a flow-level refusal was built, passed 105
tests plus a 24-cell matrix, and would have broken a large read population. D0 is blocked
**indefinitely**, not pending a small fix. `tests/test_write_classification.py` pins both error
directions so a tightening fails loudly and forces re-measurement.

## "Did the write commit?" has no oracle either — `landed` is evidence-bounded, not truth

The second surface with the same character as the keyword classifier. At the moment the decision is
made, nothing in the system KNOWS whether a write committed. Every available answer is an inference from
a page condition, and the one signal that would settle it — whether a non-idempotent request actually
left the browser — is observed in `_replay_step` but not threaded to `flows.py` (S6/AB-1, blocked on
S17).

**R3.3 cost SIX versions of one predicate, five of them wrong** (0.78.0; the failure modes are in
`docs/open-defects.md`, each reproduced against a live fixture). Every wrong version passed the full
suite and `drift_bench`. They failed in alternating directions, which is the tell that the question is
under-determined rather than that the answers were careless:

| answered "committed" by… | broke on |
|---|---|
| the exception's CLASS | the original finding |
| the return's POSITION | a trailing "Print receipt" step drifting — paid twice |
| `found and not confirm_pre_true` | a leftover banner + a run that never reached the write |
| + proof the baseline ran | the Pay button renamed; baseline is taken BEFORE the click |
| + *some* write step ok | a sibling step misclassified as a write ("Payment history") |

What ships requires two independently-sourced facts: the confirm made an absent→present transition
(measured either side), AND every step the cached recipe marks mutating ran and succeeded.

**The residual that remains, and will remain:** a click that SUCCEEDS while its JS is broken, so no
request is sent, with a banner that appears anyway, still arms. That is not fixable from page evidence.
It is accepted because the SUCCESS path carries the identical hole — it records its ledger row off the
same confirm — so this is parity, not a new risk.

**The operative rule.** `landed` means "the evidence available says it committed", never "it committed".
Treat it as a bound, not a fact:
* it is sound to be conservative because of it (don't retry, tell the operator, record the row);
* never build something that is only correct if it is TRUE — and in particular never widen what a
  `landed=True` row is allowed to SKIP;
* the two questions "may a resume skip this whole row" (needs every write ok) and "must a human be told
  something fired" (needs one) are DIFFERENT — collapsing them was the sixth pass's finding.

Direction of error matters more than accuracy here: a missed arm re-fires under the same
Idempotency-Key; a false arm skips a row that was never paid, and nothing catches that.

## Reporting surfaces: quiet is an ALLOWLIST, and loud needs an acknowledgement

Two rules, both learned by shipping their opposite (R3.9/CLI-1, 0.80.0), and both due again the moment
another skip/refusal class is added — S5 creates one by design:

* **Enumerate the QUIET outcomes, never the loud ones.** `flow run-all` had two cron channels (exit code,
  webhook) and each tested `status == "failed"`, so a third bucket satisfying neither was invisible in
  both, and a flow could leave the fleet permanently with cron reporting green. One verdict function,
  both channels, and a closed set of quiet statuses — so a status added tomorrow is loud by default.
  This is S3's "a test cannot fail for an exit added tomorrow" one abstraction up.
* **A loud channel with no way to ACKNOWLEDGE gets `|| true`d, and takes everything else dark with it.**
  Before making anything alert, ask what the operator does when they cannot fix it. If the answer is
  "nothing", the alert is a regression however correct it is — this is the D0 over-refusal shape wearing
  a reporting hat. Prefer an acknowledgement that already exists (`flow unapprove` served here) over a
  new flag, and keep a fleet-level guard underneath so acknowledging EVERYTHING is still loud.

Corollary for both: pin the quiet direction as hard as the loud one. "Alert on everything" passes every
test written for the finding.

## The pattern that predicts the next bug

Most defects found here are **a guard that already exists on a sibling path and was never applied to the
mechanism** — the replan path guards something the heal path doesn't; the recorder guards something `learn()`
doesn't; `heading`/`label` anchors were hardened and `row` wasn't. When you fix one, check its siblings.

## Measurement, not assertion

`benchmarks/drift_bench.py` (key-less, ~60s, CI-gated) is the instrument for any change to `locators.resolve`
or the recovery ladder. It reports a 0-LLM survival curve by mutation intensity, per-tier recovery rates, and
`silent_wrong` — which must stay within its published allowlist. Use it to *adjudicate* a resolver trade
rather than argue it; that is what it was built for. `baselines/README.md` states plainly what each number
does and does not prove — keep that honesty when adding to it.
