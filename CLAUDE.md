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

**And read `docs/reshape-plan.md` before proposing a refactor** — it answers "why does every change
break something". It measures the shapes that manufacture the recurring defect classes (a 27-param
positional funnel, string-keyed dict channels, a record written at 10 sites, 33 raw `spec.mutate`
predicates, 24 `flow_key` transcriptions, 24 bare `raise FlowReplayError`), argues for re-shaping rather
than a rewrite or a `flows/` split, and records what NOT to re-propose. It changes the shape fixes land
in; it does not re-sequence `correctness-plan.md` and closes none of its findings.

**Phase 0's instruments are BUILT** — the fast tier, register-as-data and the exit-set matrix — so the
plan is no longer a proposal. **§13 RE-PRICES it** — §12's own trigger fired (1.5's audits returned twelve, against a ~3 threshold), so the order was re-derived from measurement: 0.8 (CI-derived marks) first because the manifest tax crossed its 4 h rule at 5.00 h, then **B2 in parallel** (unblocked since day one, never started, zero audit burden), then 1.4. §12 fixed the earlier order and holds 0.5/0.6/0.7 with named triggers,
derived from which instrument each Phase-1 step's own pins name (only 0.4 is gating; nothing names
0.5–0.7). Read §12 before picking a step, for the same reason `correctness-plan.md` exists: picking ad
hoc re-creates the orderings a plan exists to prevent.

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
- The full suite is **key-less** — real headless Chromium against local fixtures, no API key, and
  **31m35s measured on this host at 0.108.0** (15 → 18 → 21 → 31 over the last several releases; the
  "~21 min" this line used to claim was stale). **CI shards it across two runners per OS** because it had
  reached 21m53s against a 25-minute job timeout, which was a deterministic failure approaching.
- **Tiers: `--tier fast` before a commit, the whole suite before a MERGE.** `pytest --tier fast` runs the
  **665 of 1165** tests that provably never launch Chromium — **~71 s at 0.4a**, against the full
  suite's 32 minutes (the other 500 are browser tests; both numbers are derived by a probe, not
  estimated). **The tier is getting slower and the trend is worth watching**: 46.8 s -> 58 s -> 71 s over
  three slices, so it no longer meets the plan's "<60 s" acceptance. Roughly 8 s of the latest rise is
  `test_ratchets.py`, which re-derives six AST ratchets over a scratch copy of `src/`; the obvious next
  saving is a single-pass visitor instead of six `ast.walk`s per module, and it was NOT taken because
  this host's own variance on the same tier measured 49-64 s in one afternoon, which is larger than the
  saving. Re-measure on CI before optimising. It is not a skip-list: every
  Playwright entry point is wrapped on the CLASS in the ROOT `conftest.py`, so under the fast tier a launch
  **raises** — an unclassified test that launches fails loudly rather than quietly running slowly — and a
  collected test in NEITHER tier is a collection error naming it (`check_shard_coverage`'s property, one
  instrument over). Membership is **derived, never declared** — a `pytest.mark.browser` is refused by a
  test, because a hand-written list is only as good as its worst entry. Regeneration is **two phases**,
  and the second is not optional: `pytest -q --store-browser-marks` (full run) then
  `python scripts/derive_test_tiers.py`. Attribution is order-dependent — when a module shares browser
  work through a fixture, the sibling that triggers it first is charged and the rest look "fast" — so
  the manifest is a FIXED POINT, not one run's output. Measured: all 15 non-launching tests in
  `test_drift_bench.py` classified fast and every one launched once the tier deselected the sibling that
  primed the bench. The CI `fast` job is what keeps it honest afterwards. The old rule ("green before a
  commit") moves to **green before a merge, on CI, on both OSes** — a local green is measured to be
  weaker evidence anyway (platform + keys), and its 31-minute cost is what pushed B1's author to targeted
  subsets that missed 7 breakages. The conftest also **scrubs the provider keys** for every run, so the
  `ANTHROPIC_API_KEY=` prefix is now the default rather than something to remember.
- **`flows.replay()` has a browser-free exit-set matrix — use it, and keep it whole.**
  `tests/test_replay_exit_matrix.py` drives every exit of `replay()` in ~1.3 s through
  `tests/_fake_engine.py`, which scripts the engine at the SIX module bindings `flows.py` already holds
  (no `engine=` parameter was added to `src/` — patching the binding reaches every call, and a def-time
  default would make the 27 existing `monkeypatch.setattr(flows_mod, ...)` sites inert). The exit set is
  DERIVED by AST (16 today) and ratcheted, so an exit added tomorrow fails the ratchet instead of
  slipping in uncovered. Its fidelity is not self-asserted: `tests/test_fake_engine_fidelity.py` derives
  the `out` contract from the engine's own source and runs the REAL `_make_finalize` against a REAL
  session. **What it cannot see is anything page-side** — `drift_bench` and the browser write-safety
  matrix stay the adjudicators there.
- **A structural scan that names ONE function asserts a negative about a body that can walk away.**
  Measured at 1.5, where splitting `replay()` and `_attempt_replay` into wrapper+body silently
  disarmed THREE scans at once: the exit-set ratchet read "2 exits", `test_boundary_truth`'s
  health-view guard read "0 `_record_run` sites", and R3.3's arming scan flagged the delegating
  return. All three fired, which is the net working — but when you split a function, the scans that
  NAME it are a fourth thing to check beside the ratchets, the mutants and the goldens. Name every
  half, and fail if a named half is MISSING rather than scanning whatever is found.

- **The RunRecord has ONE writer — `_RecordSink` (1.5, 0.110.0).** `replay()` is a wrapper whose only
  job is calling `finish()` exactly once; `_replay_body` appends frozen `_AttemptFacts` and cannot
  write the record at all. Usage comes from ONE run-scoped watch, so a raised attempt, an exit whose
  report omits `usage`, and the relearn all stopped needing sites of their own — that is what closed
  seven B1 findings at once. **Do not add a `record.<field>` write anywhere else**: an AST pin
  (`test_every_record_write_is_inside_the_sink`) fails naming it, because the count was never the
  invariant — the SPREAD was. `finish()` is TOTAL (it runs in the `except` arm; its own failure goes
  to `record.note`, never over the caller's exception), and the landed/committed rule is: True if any
  attempt evidenced it, else None if any attempt is unknown (raised / precheck-skip / relearn-raise),
  else False.

- **Six shapes may only ever SHRINK — the ratchets.** `python scripts/ratchets.py` counts them by AST
  (`--print` for every site, `--update` to re-seed) and `test_every_ratchet_holds` runs it in the fast
  tier. Today: `spec_mutate_raw` 27, `flow_key_transcriptions` 25, `bare_flow_replay_error` 24,
  `cli_system_exit` 34, `run_record_write_sites` 16, `engine_positional_params` 98 — each tagged with the
  Phase-1 step that removes it. **A shrink FAILS too**, asking for `--update`: a ratchet that tolerates
  progress silently stops ratcheting, and the next regression is measured against the old, looser
  number. A derivation that matches NOTHING fails with its own message, the rule `prove_red` already
  applies to a stale mutation. **Do not quote `reshape-plan.md`'s counts** — they were greps, and two of
  five were wrong (grep counts the shape inside COMMENTS: `spec.mutate is not None` appears in three
  findings' prose, inflating 27 to 33). Derive, then cite.

- **The wiring mutants must stay dead.** `scripts/prove_red.py tests/mutations/b1_wiring.py` applies each
  of R4.48's eleven record-plumbing mutations to a scratch copy of `src/` and reports which are killed:
  **0 of 11 before the matrix, 11 of 11 after**. At 1.5 all seventeen went STALE at once — the sites they named ceased to exist — and were reported as ERRORS, not survivors; re-expressed against the sink they are **16 killed, 0 survived**, and two of the rewrites SURVIVED first, naming two properties no cell drove, and the `red-proof` CI job keeps it that way. A mutation
  whose find-text no longer matches is reported as an ERROR, not a survivor, because a stale mutation
  silently reports the suite as stronger than it is. A survivor is a hole in the matrix, not a bug in the
  mutation: add a cell, or register it with a reason and a finding id.
- **Re-deriving the tier manifest is TAXED, and the tax is now metered, not remembered.** Measured at
  0.109.0: **31m58s** for the marks phase plus **2m03s** for the fixed-point loop (1 round, 15 tests
  promoted back — all of `test_drift_bench.py`, the known order-dependent population). A full
  `--store-browser-marks` run appends what it cost to `tests/.manifest_cost.jsonl`, and
  `python scripts/manifest_cost.py report` joins those timings with the manifest's git history and
  prints a verdict. The obvious cheaper design — merge new ids in rather than rewrite — is a ONE-WAY
  trade: it does NOT weaken deletion detection (`test_the_manifest_covers_everything_this_run_collected`
  checks `known - collected` in seconds, and `tests/test_manifest_cost.py` arms that guard), but it
  DOES lose **de-classification** — a test that stops launching keeps its browser mark forever, still
  collects, and is silently deselected from the fast tier for good. Measured: of the three manifest
  deltas so far, ONE de-classified three tests, and they were the tier's own arming cells. So the
  verdict today is DO NOT switch. Also measured and refused: gating on a collection fingerprint would
  have saved zero runs (all three deltas added ids). Run the report before proposing either.

- **A shard must never be a hole.** `pytest-split` partitions the real collection, so a new test file
  lands in a shard by construction — but the `shard-coverage` CI job asserts it (union == full, no
  duplicates), because a test in NO shard leaves every shard green and nothing in the suite can fail for
  it. Regenerate balance with `pytest -q --store-durations` when the suite's shape changes a lot;
  a stale `.test_durations` costs WALL-CLOCK balance only, never coverage.
- **The register's STATE is data; its NARRATIVE is prose.** `docs/register/state.json` holds every
  round-4 finding's id/status/summary and the R4 STATUS INDEX table is RENDERED from it
  (`python scripts/render_register.py --write`; `--check` is what the suite runs). File a finding by
  adding an entry and re-rendering — the counts cannot drift from the rows because nobody types them.
  The 3900 lines of evidence stay exactly where they are: narrative does not drift, it accretes, and
  moving it would be churn for nothing. Optional fields (severity, attempts, `blocked_by`, pins) are
  OMITTED rather than guessed — only 5 of 53 summaries state even a severity, so back-filling the rest
  would be 50 hand-typed facts, which is the transcription class the move exists to end. Fill one in
  when a slice touches that finding; `D5`'s two-strikes gate becomes a SCHEMA RULE the moment an entry
  records its `attempts`.
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

**A green property is worth exactly what its STUB is worth — so test the stub, and assume the stub is
inert until measured.** S14's `no_llm` fixture said an LLM was "unreachable in BOTH directions" and was
wrong twice over. First: `flows.py` does `from .providers import build_router`, so patching
`ultracua.providers.build_router` never reached `ultracua.flows.build_router`. Fixing that by deriving
the MODULES from the live import graph was not enough either, because the factory NAMES stayed
hand-listed and the one that matters — `llm.build_client`, which the other two call — was missing: a
replay was made to build **105 real Anthropic clients** while all 25 cells passed and the corpus cell
printed "0 reached an LLM". A patch-list is only as good as its worst-known entry, so **close the class
instead**: an AST scan now pins that SDK clients are constructed only inside the leaf adapters, which is
what makes `build_client` provably the choke point.

**And a cell that cannot fail is not a test — arm the violation and watch it go red.** Three cells in
that same slice were green while exercising nothing: auth-refresh refuses early without a
`storage_state` (so `_form_login` never ran), `dry_run` aborted at pre-flight for a step lacking
`mutating=True` (so no browser opened), and a door called with a bad kwarg raised `TypeError`, which the
harness accepted as a legitimate refusal. **If a cell claims to reach a mechanism, count the mechanism's
calls and assert the count; and never let "any exception" stand in for "refused".**

The third trap here is subtler and cost real time: `mode="replay"` nulls the provider at `flow.py:171`,
so handing the engine a raising stub proves nothing on that path. The honest property was the STRUCTURAL
one — assert the nulling holds — not a stub the engine never sees.

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
