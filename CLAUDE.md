# ultracua — working notes

A Computer Use Agent: **learn a browser flow once, replay it deterministically at 0-LLM, 5–10× faster,
failing LOUD on drift.**

## Read this first

**[docs/open-defects.md](docs/open-defects.md) — the standing defect register.** FOUR rounds. Rounds 1–2
(30 findings) are fixed; **round 3 found 11 more in the 387 lines those fixes added, 1 critical, and
refuted NONE of them** (nine still open); round 4 was a pre-merge audit that PARKED a change rather than
ship it. Two are regressions the fixes introduced. Defect density in fix code measured ~3x
the code it replaced, so **a patch on a patch is the thing to be most suspicious of here** — three round-3
findings are the same shape as the finding they were fixing, one level down. When you close something,
change the shape so the invariant is enforced ONCE rather than adding another per-branch test.

**Two of round 3 were answered that way in 0.73.0** (R3.1, R3.4): two transcriptions of a JS snippet
became one, and a return type that could only say "absent" learned to say "unreadable". **A third
(R3.2, write attribution) has now defeated three attempts** — 0.73.0's drain (reverted), 0.74.0's first
refusal draft (over-refused), and a 0.76.0 causal-attribution branch that was green and still wrong
(**parked**, see `docs/parked/README.md`). Read that history before touching attribution: it rules out
every purely temporal design, and it shows that green is not evidence here.

**WORK FROM THE PLAN.** `docs/correctness-plan.md` sequences every open finding, test hole and unpinned
residual into slices, worst user harm first, with the dependencies between them made explicit (the net
gets strengthened before it is relied on; a hole-widener never lands before its hole-fix). Picking items
ad hoc re-creates orderings the plan exists to prevent. `docs/correctness-survey.md` is the 58-item
inventory it must dispose of.

**Audit the fix, not just the code it fixes.** Both the reverted 0.73.0 redesign (754 tests green,
`drift_bench` clean, every regression test verified RED against pre-fix source) and the parked 0.76.0
branch (785 tests, same clean bench) were critically wrong. **Green is not evidence in this codebase.**
Four rounds running, fix code has been the defect source, and the only instrument that has ever caught
it is an adversarial pass aimed squarely at the new code — three for three. Run one before you open the
PR, not one release later.

## The three inviolables

Violating any of these is a blocking defect, not a trade-off:

1. **Replay never calls an LLM.**
2. **Never silently return or act WRONG — fail LOUD.**
3. **Write safety** — never double-submit a write, never silently suppress one.

## How work is done here

- **`uv` for everything.** `uv run --no-sync pytest tests/... -q`, `uv run --no-sync python -m benchmarks.X`.
  Never bare `uv sync` (it strips groups) — use `uv sync --all-groups`.
- **CI runs the suite on ubuntu AND windows; local runs here are windows-only.** A green local suite is
  therefore half the evidence. The browser fixtures are where this bites: anything whose page state is
  revealed inside a `fetch(...).then(...)` races the agent's next observation, and Linux loses that race
  where Windows wins it. It has already shipped once — an S3 fixture captured a 1-step recipe on ubuntu
  and the test failed with a meaningless "DID NOT RAISE". **Reveal fixture state synchronously, and pin
  the premise** (assert the recipe has the steps the test depends on) so a lost race fails LOUD instead
  of silently testing nothing.
- The full suite is **key-less** — real headless Chromium against local fixtures, no API key, ~18 min
  (it grew from ~15 when 0.73.0 added a per-step attribution drain to the learn path). It must be green
  before a commit.
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

**Verify a regression test fails against the old code.** A test that passes both before and after proves
nothing. This has caught several no-op "fixes".

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
