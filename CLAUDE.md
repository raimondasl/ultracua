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
predicates, 24 `flow_key` transcriptions, 24 bare `raise FlowReplayError` — the last of those
closed at 1.4), argues for re-shaping rather
than a rewrite or a `flows/` split, and records what NOT to re-propose. It changes the shape fixes land
in; it does not re-sequence `correctness-plan.md` and closes none of its findings.

**Phase 0's instruments are BUILT** — the fast tier, register-as-data and the exit-set matrix — so the
plan is no longer a proposal. **§13 RE-PRICES it** — §12's own trigger fired (1.5's audits returned twelve, against a ~3 threshold), so the order was re-derived from measurement: a **step 0** (CI provisioning, done 2026-08-20), then 0.8 (CI-derived marks) because the manifest tax crossed its 4 h rule at 5.00 h, then **B2** (unblocked since day one, never started, zero audit burden), then 1.4. **Step 0 is worth reading even though it is closed** — it was filed as "CI capacity" on the reading that ubuntu's suite had got slower, and the suite was never involved: 8 of 58 ubuntu jobs were killed inside `playwright install --with-deps chromium`, six of them still in `apt-get` at the wall having run ZERO tests, while the suite stayed flat at 12.4-13.8 min and remained the FASTEST arm. `--with-deps` installs nine font packages and no libraries. A `cancelled` job says nothing about which mechanism failed, and a wrong prerequisite got written into the plan on that ambiguity one day after §13 was written to prevent exactly that. `docs/ci-provisioning.md` holds the measurement, because Actions logs expire. The order is STRICTLY LINEAR — nothing runs in parallel, so §5's 55-80 day figure, which assumed Phase 2 alongside from day one, is the parallel-world number. §12 fixed the earlier order and holds 0.5/0.6/0.7 with named triggers,
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

- **A refusal names a REMEDY, and the base class is abstract (1.4a, 0.111.0).** Twenty-four refusals
  shared `code="replay_error"`, so "flow not approved", "no login configured" and "batch has more rows
  than max_rows" all reached the MCP wire, `DryRunReport.aborted` and a `--json` batch report as one
  word. Now 27 distinct codes, and `FlowReplayError("x")` **raises `TypeError`** — which is the only
  sensor that also reaches the INDIRECT raise (`raise _classify_replay_failure(kind)(...)` resolves its
  class at run time, so no AST scan keyed on a class name can see it, and that route produced the
  base-coded refusal for the commonest failure a fresh flow has). The base is still the `except` target
  of the whole family: the taxonomy narrowed, the catch did not.
  * **Four axes, and `__init_subclass__` enforces the relations between them ONCE.** `code` (a remedy),
    `retryable` (may an agent re-run), `landed` (the ledger's two-state ARMING token — NOT
    `RunRecord.landed`'s tri-state report), and `can_follow_actuation` (can this be raised after the
    write may have fired). Each must be DECLARED, never inherited — that is R4.18 in one line, where
    `MetaUnwritableError` inherited `retryable=True` from its pre-write twin and an MCP agent re-fired a
    commit. **Retryable + post-actuation is now a TypeError**, as is a duplicate code, a code another
    vocabulary owns (`RESERVED_CODES` — `ToolOutcome`/`SkippedFlow` share the field), and a `landed=True`
    claim from a position where nothing could have fired. Two hand-written lists were deleted for this:
    `test_boundary_truth`'s four post-actuation classes and `test_inviolable_properties`'s `>= 10` floor.
  * **`tests/test_refusal_codes.py` holds the BINDING** — which class each function raises — as one
    committed table keyed on the FUNCTION, not the line. Reviewing its diff IS reviewing a taxonomy
    change. Deliberately not "a cell per subclass at its raise site" (the plan's wording): 24 bespoke
    cells is the per-branch shape this register keeps re-filing.

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
  tier. Today: `spec_mutate_raw` 27, `flow_key_transcriptions` 25, `cli_system_exit` 34,
  `engine_positional_params` 98, and **two at ZERO** — `run_record_write_sites` (1.5) and
  `bare_flow_replay_error` (1.4) — each tagged with the Phase-1 step that removes it. **A shrink FAILS
  too**, asking for `--update`: a ratchet that tolerates progress silently stops ratcheting, and the
  next regression is measured against the old, looser number. A derivation that matches NOTHING fails
  with its own message, the rule `prove_red` already applies to a stale mutation. **A ratchet whose end
  state is ZERO is an EARNED exemption** (`MAY_BE_ZERO`): it must prove its own pattern is still live
  from the other half of the same walk — the writes INSIDE the sink, the raises of the SUBCLASSES — and
  `LIVENESS_GUTS` in `tests/test_ratchets.py` is what arms that, one row per exemption, with a missing
  row a red test. **Do not quote `reshape-plan.md`'s counts** — they were greps, and two of
  five were wrong (grep counts the shape inside COMMENTS: `spec.mutate is not None` appears in three
  findings' prose, inflating 27 to 33). Derive, then cite.

- **The wiring mutants must stay dead.** `scripts/prove_red.py tests/mutations/b1_wiring.py` applies each
  of R4.48's eleven record-plumbing mutations to a scratch copy of `src/` and reports which are killed:
  **0 of 11 before the matrix, 11 of 11 after**. At 1.5 all seventeen went STALE at once — the sites they named ceased to exist — and were reported as ERRORS, not survivors; re-expressed against the sink they are **16 killed, 0 survived**, and two of the rewrites SURVIVED first, naming two properties no cell drove, and the `red-proof` CI job keeps it that way. A mutation
  whose find-text no longer matches is reported as an ERROR, not a survivor, because a stale mutation
  silently reports the suite as stronger than it is. A survivor is a hole in the matrix, not a bug in the
  mutation: add a cell, or register it with a reason and a finding id.
- **Re-deriving the tier manifest no longer needs a local suite run (0.8).** `pytest --emit-marks PATH`
  writes an OBSERVATION -- `collected`/`selected`/`reported`/`launches` -- and NEVER the manifest, which
  is what lets a CI shard emit evidence although it may never write a classification. CI's four `test`
  jobs each emit one and upload it; `python scripts/tier_marks.py pull` fetches them, and `merge` checks
  the contract, writes a CANDIDATE, validates THAT with the fixed-point loop (via
  `ULTRACUA_TIER_MANIFEST`), and swaps only on convergence. **Both halves of that earned their keep on
  their first real use**: the candidate-swap reported "the candidate did NOT converge. The committed
  manifest is UNTOUCHED" where write-then-validate would have left a broken artifact on disk, and the
  identity check caught a test RENAMED after its observation was emitted -- in 2 s, naming both sides.
  **Identity is the collected ID SET, never a sha**: on a `pull_request` GitHub checks out
  `refs/pull/N/merge`, so a run correctly identified by your commit collected a different TREE (verified,
  run 32380417760 -> `HEAD is now at 2c908fc Merge 5d47964 into 4f7ac67`), and the inflated `total` would
  then DISARM the deletion detector, which only runs when `len(collected) >= data["total"]`. It is
  LOCAL-FIRST on purpose: an earlier draft made CI the sole supplier, which needs an open PR, `gh` auth,
  no concurrent push and a 16-25 min wait, with the 36-minute local run as the fallback -- that moves the
  tax behind a network precondition rather than removing it. **A SINGLE-PLATFORM part set cannot
  DE-CLASSIFY** (it holds existing browser marks and says how many): a local emit is single-platform by
  construction, and measured, two windows parts moved a ubuntu-only launcher into the fast tier where
  ubuntu's `fast` job then raises. Promotion still works from one arm, because that direction is safe.
  And the CI label is `os-group` with the attempt in the artifact NAME only -- put the attempt in the
  LABEL and a re-run gives one shard two labels, which makes attempt reconciliation inert (measured: it
  was). Reconciliation is last-wins for `selected`/`reported` and a UNION for `launches`, because a
  completeness record expires and evidence does not.
- **The older tax, kept for the history it explains.** Measured at
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
  verdict today is DO NOT switch — and that verdict is now ENFORCED rather than merely stated: clause
  (b) read a rolling ten-delta window until 0.8's prerequisites, so when the one de-classifying
  revision scrolled out the report began recommending merge-mode, the design this very paragraph
  refuses. It reads all of history now. **And the write path is guarded on FOUR axes, not one** —
  tiered, under-collected, NARROWED by deselection, and never-ran. The last two were open: a sharded
  `--store-browser-marks` (which is what CI runs) passed the collection guard and would have written
  only the shard, and `--collect-only --store-browser-marks` wrote `0 browser, 0 fast` over 1201
  classifications and called it a success. Both measured, both now refuse. Also measured and refused: gating on a collection fingerprint would
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

* **CI IS A REPORTING SURFACE, and its outcomes are the least-enumerated ones here.** A job's
  `conclusion` is a single overloaded word: over 58 ubuntu jobs, `cancelled` meant an apt hang that ran
  ZERO tests, a superseding push, AND a run killed with the suite mid-flight — same string, nothing to
  tell them apart. That ambiguity wrote a wrong prerequisite into `reshape-plan.md` §13, one day after
  §13 was written to stop exactly that. The fix (step 0, 2026-08-20) was the allowlist shape again: **every
  authored step in `ci.yml` carries `timeout-minutes` except a written allowlist** (today one entry —
  the suite, whose duration IS the signal), so a provisioning over-run fails at a NAMED step in
  minutes. `tests/test_ci_provisioning.py` pins it, with five armed mutations. What it deliberately does
  NOT claim is the converse — a job-level `cancelled` still has several causes, because the
  `always()`/`failure()` tail and GitHub's own injected steps are unbudgetable, and a draft that
  asserted otherwise had a worked sum that was wrong on the failure path.

Corollary for both: pin the quiet direction as hard as the loud one. "Alert on everything" passes every
test written for the finding.

## "It spent nothing" and "nobody was watching" are different answers (1.3, 0.114.0)

`RouterWatch` classifies each owner of a run by whether a `UsageTotals` is reachable at
`owner.router.totals` or `owner.totals` — and those two attributes are the ENTIRE contract. There are
**three states in the world and two in the report**:

* **watched** — a real router, and its delta is this run's spend;
* **cannot spend** — the owner declares `UsageTotals.cannot_spend()`, an accounting object that will
  never grow, and contributes a real zero. That is every key-less teacher: `ScriptedProvider`,
  `MockProvider`, `MockGrounding`, the bench's `OracleProvider`;
* **an unwatched spender** — no totals anywhere, so the run reports cost UNKNOWN.

Before 1.3 the last two were one bucket, so the whole population the key-less suite and `drift_bench`
are built from reported `cost_usd: None, unobserved_llm_path: True` while `flow.py`'s own comment said
the opposite. A declared zero and a measured zero report IDENTICALLY on purpose — a third word in the
record would be a distinction with no consumer.

* **The declaration is offered, never extracted.** Commit `00888b4` added a `getattr(o,
  "accounting_failed", False)` probe on the owner and tripped inviolable #1's tripwire, because
  `_Exploding` raises on any attribute but `router` and a replay does not POKE at a provider. So the
  third state is declared through an attribute the watch already reads, and
  `test_classifying_an_owner_touches_only_router_and_totals` derives the permitted set — `{router,
  totals}`, named, because a scan asserting "few attributes" would pass a probe for the wrong one.
* **`_Spender` is the residual and must stay unobserved.** Without that control the fix is "always say
  zero", which is the confident wrong number the tri-state exists to prevent, reached from the other
  side.
* **`accounting_failures` is a COUNTER, not a flag, and that is the whole of the second half.** Every
  other field on `UsageTotals` is monotonic and made run-scoped by `since()`; this one was a sticky
  bool, so a watch built for a LATER run reported `observed=False` having seen nothing go wrong. **A
  bool cannot be deltaed out of that** — two consecutive failures leave it True both times, so there
  is no transition to see. `accounting_failed` survives as a read-only property (every reader
  unchanged); the four writers say `+= 1` deliberately, because a settable property translating
  `= True` into `+= 1` is one token meaning two things.
* **`or 0.0` in a reader undoes all of it.** `variance._cost` and `drift_bench`'s total both collapsed
  unknown into free. `drift_bench`'s was wrong in BOTH directions at once — `sum(x or 0.0) or None`
  also turned a genuine total of exactly zero, which is what a key-less run produces, back into
  "unknown". Unknown is now ABSORBING in both, and a real zero is a real zero.
* **Two early returns carried no accounting at all.** `flow.py`'s escalate paths built their
  `FlowReport` before the line every other exit passes through, so an escalated run reported no usage
  and every reader rendered it UNKNOWN. Measured end to end: `drift_bench` went from `cost_usd: null`
  over 370 key-less rows to `0.0` over 370, via an intermediate state — 368 zeros and 2 unknowns —
  that is what made the remaining hole obvious. A guarantee with the words "always populated" in it is
  worth checking at every early return.

**And one instrument lesson, caught in the act.** `test_every_escalate_report_carries_its_usage` first
read `flow.py` from `src/` BY PATH, and its registered mutation was reported as a SURVIVOR while the
guard was perfectly fine — `prove_red` installs its mutant as a copy on `PYTHONPATH`, so a
path-reading cell parses pristine source. That is R4.75 happening live. `inspect.getsource(module)` is
the fix, and it is already written in `prove_red`'s own docstring.

## The customer benchmark mints exactly one verdict, in one place (B3, 0.113.0)

`benchmarks/outcomes.py` owns the whole outcome vocabulary — `{ok, wrong_data, refused, over_gated}`
for reads, `{true, incorrect_target, double, suppressed, refused_correctly, refused_wrongly}` for
writes — and `benchmarks/customer_bench.py`'s `ScenarioRun` still carries **no `outcome` field**,
which is a decision rather than a leftover. A verdict is an ADJUDICATION (harness facts + a
server-side oracle + the corpus author's ground truth); a scenario record is an OBSERVATION.
`outcomes.Scored` holds the pair. That is B2's `harness_error`-vs-`agent_error` line one level up.

* **`unscored` is a seventh state and it is NOT a verdict.** It is removed from every denominator in
  both directions, and it is deliberately absent from `QUIET_OUTCOMES` — a gate that read it as a
  pass would let a run where nothing could be adjudicated report green.
* **The three inviolables are not a rate.** `wrong_data`/`incorrect_target`/`double`/`suppressed`
  fail the run absolutely; nineteen passes beside one `double` is not a 95% success. The escape is a
  published `(scenario, outcome)` allowlist — the shape `drift_bench` already uses — because a loud
  channel nobody can discharge gets switched off wholesale.
* **`CODE_FAMILY` is a TOTAL partition of `flows.REGISTRY` with no default**, which is the whole
  reason 1.4 had to land before B3. `.get(code, PAGE)` here is the defect class the benchmark
  measures: a bucket that absorbs what nobody classified, with a confident rate reported over it.
  The twelve MCP-minted codes in `flows.RESERVED_CODES` are deliberately **unclassified**, and what
  makes that safe is derived rather than asserted: `run_scenario` takes the code from
  `flows.outcome_of` and nothing else, so the day a bench arm drives the MCP surface,
  `test_the_reserved_vocabulary_is_unreachable_from_the_bench` fails and somebody classifies them
  knowing what they mean.
* **The ordering clause is the design, and `agent_ran` is its scope.** A write-safety violation the
  ORACLE can see is decided BEFORE any unscored reason, because three HARNESS-family codes declare
  `can_follow_actuation` — so "the bench misconfigured something" and "the write fired twice" are
  simultaneously true, and with the excuse first a `double` the server holds lands in no number
  anywhere. But `harness_error` has a SECOND door where the agent never ran (reset/readiness failing
  returns before `agent_call`), and B2's rule 3 guarantees the substrate is then still carrying the
  PREVIOUS scenario's records — so without the guard, a failed container restart published the
  previous row's write as this row's `incorrect_target`. A recorded fact, not `wall_s == 0.0`,
  because a timer is not a boundary (R4.26).
* **THREE VERDICTS WERE INFERRED FROM THE ABSENCE OF A REFUSAL CODE, and all three were wrong.**
  This is the slice's own recurring defect and worth reading as one:
  * `suppressed` from `not code` — an LLM arm that finishes its turn without doing the task arrives
    with an empty code (B2's own comment calls that the normal case), so "the product silently
    suppressed N writes" was really "the agent failed N tasks";
  * `wrong_data` from `data_correct is False` — an oracle comparing a run's answer to server truth
    returns False just as readily when there was NO answer, so five kinds of LOUD refusal were
    published as SILENT wrong data;
  * `refused_correctly` from a bare `raised` — `_classify_read` branched on the crash code from the
    first draft and `_classify_write` did not, so an untyped exception (including a BENCH bug, since
    `except Exception` wraps `agent_call` itself) credited the write gate.

  The fix is one fact, not three branches: **`ScenarioRun.claimed_complete: Optional[bool] = None`**,
  which `run_scenario` deliberately does NOT set — an `agent_call` returning an observation has
  claimed nothing. Each ARM sets it; until one does, the answer is `unscored`. Every one of these
  minted an INVIOLABLE outcome, the channel that fails a run absolutely and whose only discharge
  permanently blinds that scenario to the real violation.
* **A row that exists to prove a gate holds could not fail in that direction.** `expect_refusal` was
  read only in the branch reached when nothing landed, so a write that LANDED against the corpus's
  declaration returned `true` — quiet, counted as availability. Measured: a broken write gate
  published `availability 1.000` against the working run's `0.667`, `inviolable: []`, nightly green,
  and the nightly comparison read the regression as an IMPROVEMENT. `expect_refusal` now means
  *the intended matched set is EMPTY*, adjudicated in clause 1 with the other violations.
* **`over_gated` needs the recipe to MARK a write, not merely to be readable.** `present` is a
  readability fact and was the wrong predicate: `GateEvidence(present=True, mutating_steps=0)`
  satisfied it and the verdict printed `mutating_steps: 0` as its own evidence. Four of the eight
  `WRITE_GATE` codes are approval-LIFECYCLE gates — `NotApprovedError` fires on
  `(require_approved or declares_write) and not meta.approved` (`flows.py:3414`), so a caller passing
  `require_approved=True` for a plain read gets it — and a bench arm that forgot to `approve()`
  would have published its own omission as the benchmark's headline. The plan always said three
  inputs; the first draft used one.
* **A rate is gated on the baseline's WILSON LOWER BOUND, never on `variance.compare_records`.**
  That function's tolerance is `max(rate_floor, baseline_std)`, which measures noise only when each
  `per_rep` value is another REP of one benchmark. B3 hands it one value per DIFFERENT scenario, so
  the sample stdev of a 0/1 vector is a closed form of the mean — `sqrt(p(1-p)·n/(n-1))`, largest
  exactly where the rate is most interesting. **MEASURED: a 0.700 baseline over ten scenarios yields
  std 0.483, so a run at 0.300 did not regress** — the gate tolerated a forty-point drop in the
  headline number. A single pass has no repetition and therefore no noise estimate; what it has is
  an honest error bar on a proportion, which the record already publishes. `variance.py` is left
  untouched as a result: an earlier draft added a `gated_rates=` keyword to it, and once B3 stopped
  delegating that would have been a shared-module change with no consumer.
* **The corpus is FIXED, so a flipped scenario is a fact — reported by name, and NOT gated.** B3 runs
  each scenario once, so nothing in it can separate "this flow stopped working" from "this flow is
  flaky"; gating one flip makes a flaky substrate keep the nightly permanently red, which is how a
  loud channel gets switched off and takes the inviolable one dark with it. `FLIP_IS_GATED = False`
  names that decision once. Note the gate's sensitivity is a function of how many scenarios share a
  rate, not of this channel: the same single flip on a corpus with ONE write scenario takes
  `write_availability_rate` 1.0 → 0.0 and Wilson's bound on 1/1 is 0.207, so the aggregate catches it.
  B5's repeated nightly is where a flip itself becomes gateable.
* **`availability_rate` counts a scenario's DECLARED PURPOSE, which is not `Verdict.quiet`.** They
  were the same predicate, so `refused_correctly` — the product doing exactly the right thing on a
  row written to prove a write gate holds — scored **0.0 on the headline**. An `expect_refusal` row
  is a safety probe, not a task a customer wants done: it leaves the availability rates entirely and
  gets `gate_holds_rate`, where being refused is the 1. Two questions, one numerator — the shape this
  slice kept finding.
* **`CODE_FAMILY`'s ASSIGNMENT is a committed table, not just its totality.** `UNSCORED_FAMILIES`
  deletes a scenario from every denominator, so a code moved into HARNESS does not produce a wrong
  number — it produces a MISSING one and the mean goes UP. Measured by sweeping all 28 assignments:
  13 were caught by other cells and **two were not** — `escalate` (the commonest way a 0-LLM replay
  arm fails) and `auth_expired`, which between them lifted availability 78.6% → 100% with every cell
  green. Two cross-axis properties are DERIVED beside the table: `WRITE_GATE ⇒ can_follow_actuation
  is False`, and `landed is True ⇒ POST_ACTUATION`.
* **The gate has FIVE channels and channel 0 is coverage.** Measured: 13 of 14 scenarios dying on
  `login_failed` published `availability_rate {mean 1.0, n 1}` with `unscored: 13` and gated GREEN —
  the unscored list was reported and read by nothing. Every unscored row now fails unless its
  `(scenario, reason)` pair is acknowledged. **Not** a `scored_fraction` floor: a floor is a tuning
  constant, and R3.12's first fix draft was refused for being built on one.
* **`variance.build_record`'s `pass_k`/`pass_rate_wilson95` are renamed on B3's record** to
  `subset_all_pass`/`availability_wilson95`. Same arithmetic; the borrowed names mean "k consecutive
  attempts at ONE task", and B3 hands it one value per SCENARIO — so a 14-scenario corpus with one
  permanent failure would print `pass_k: {"14": 0.0}` beside an availability of 0.93.

**And the instrument note: `scripts/prove_red.py` cannot reach `benchmarks/`** — it installs a
mutant by putting a copy of `src/` first on `PYTHONPATH`, and pytest puts the repo ROOT at
`sys.path[0]`, so every mutation of a bench module reports as a SURVIVOR while the guard is fine.
That is R4.77. The discipline is kept without the file: `tests/test_bench_cells_are_armed.py`
mutates each function's own source in-process and requires the named guard to go red, with a stale
find-text an ERROR rather than a pass. **Its first run produced five FALSE verdicts** because
`assert_red` caught `Exception` and a `pytest.raises` failure is `_pytest.outcomes.Failed`, a
`BaseException` — the arming harness was reporting its own blind spot as a hole in the suite. Three
more kills were credited and not earned: an `OSError` from `inspect.getsource` on an exec'd mutant
(the scan it faced never ran), an `AttributeError` crash, and one kill credited to the wrong guard.
The OSError one recurred TWICE more in cells added an hour later, which is what moved the fix from
the cell to the harness: `mutate_function` now compiles under a `.py` path registered in `linecache`
so a mutant has retrievable source, and asserts that before handing it over.
**When you build an arming matrix here, tally the EXCEPTION TYPES** — a matrix with no
`AssertionError` behind a cell is a cell that died on the way rather than noticed. Today: 67
AssertionError, 5 `pytest.raises` DID-NOT-RAISE, 2 KeyError, 1 BenchRecordError, zero crashes.

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
