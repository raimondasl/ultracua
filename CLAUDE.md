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
(**parked** on branch `feat/shared-causal-attribution`, whose `docs/parked/README.md` is the write-up
-- that path is NOT on `main`, which is where this pointer used to lead). Read that history before
touching attribution: it rules out
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

**THE PLAN'S STATUS IS DATA — read the STATUS INDEX at the top of it, never a sentence in its
narrative (0.119.0).** `docs/plan/state.json` is the status of record and the index is rendered from
it. **This exists because 2.1 (B2) merged as PR #189 on 2026-08-20 and §13's row naming it as NEXT
still read "never started" three days and six merged slices later** — every row below it had been
marked — so the next instruction to act on that table was an instruction to build B2 a second time.
1750 lines, four modules, three test files, all already on `main`. Two things hid it: the B2 commit
**bumped no version** (0.110.0 either side), and the status lived in four tables with three different
column shapes. **`tests/test_plan_state.py` makes the TREE adjudicate every row, both ways** — a `done`
step must have its named artifact and a `pending`/`held` step must NOT, which is the direction that
failed. Phase 1 is complete; **2.3 (B4) is the next step**, and 0.7 lands with it. **Two held steps'
triggers fired unremarked**: 0.5's (1.1, at 0.115.0) and 0.6's (1.5, at 0.110.0). And the plan holds
TWO order tables — §12's is marked `order-table:historical`, §13's `order-table:operative`; reading a
status out of the superseded one is the same failure one table over.

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
- **DO NOT EDIT THE TREE WHILE A SUITE RUN IS IN FLIGHT — it costs the whole run, twice over.** The
  warning below about auditors applies to YOU. Measured at 0.115.0: editing two files during a
  32-minute run produced **twelve confident-looking failures that were pure artifacts**, because
  `inspect.getsource`/`linecache` read from DISK at call time while the code objects are the ones
  imported at collection — so a structural cell parses source that does not match the function it
  holds, and reports `the derivation found no ... construction -- it has gone stale`. Every one
  re-ran green. Worse, the run's `--emit-marks` observation was then REFUSED by `tier_marks.py`'s
  identity check ("11 this tree has and they do not"), which is that guard working exactly as
  designed and a second 32 minutes gone. Freeze the tree, including `docs/` and `CLAUDE.md` — the
  register-count cells read those.
- **A LOCAL GREEN IS WEAKER EVIDENCE THAN CI, IN THREE MEASURED WAYS. All three have shipped a red PR.**
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
  3. *Docker*: **this host runs a LIVE, SEEDED substrate between sessions and a CI job starts
     with nothing running.** (Until 0.137.0 this line said "CI has no daemon", which is FALSE
     and was never measured -- the ubuntu runner has Docker 28.0.4 and Compose 2.38.2 and
     brings Gitea up in 12.5 s, seeded in 8.7 s. R4.109; the guard was right and its stated
     reason was not.) Added at 0.121.0 with R4.85's fourth
     readiness layer, which execs into a container: two pre-existing cells drove `await_ready()` with
     only layers 1-2 mocked, so they began reaching Docker for real — and passed here against a live,
     healthy Gitea while failing **both** CI arms with a baffling `NOT WRITABLE`. Same shape as the
     keys axis: the local machine supplies something CI does not, so the suite is green for the wrong
     reason. `tests/test_substrates.py` closes it the way the fast tier closes Chromium — an autouse
     fixture makes `subprocess.run` RAISE and name its remedy, so a leak fails HERE instead of on CI.
     Guard `subprocess.run`, **not** `_compose`: the `_compose` version also blocked the cell that
     patches `subprocess.run` itself to test `_compose`'s error wrapping, which is a false positive
     in a guard — D0 wearing a test-harness hat, and it was caught by the guard's own first run.
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
  **1450 of 1957** tests that provably never launch Chromium — **89.4 s at 0.125.0**, against the full
  suite's 32 minutes (the other 507 are browser tests; both numbers are derived by a probe, not
  estimated). **The tier is getting slower and the trend is worth watching**: 46.8 s -> 58 s -> 71 s
  -> 89 s, so it is now half again over the plan's "<60 s" acceptance. **Read the RATE, not the
  total**: the fast population went 665 -> 1450 over the same span, so per-test cost actually FELL
  (107 ms -> 62 ms) and the wall-clock rise is the suite growing, not the tier rotting. That matters
  for what to do about it — trimming a slow cell is the wrong lever when the count is the driver, and
  the honest options are sharding the fast job or accepting a new acceptance number. Roughly 8 s is
  `test_ratchets.py`, which re-derives six AST ratchets over a scratch copy of `src/`; a single-pass
  visitor instead of six `ast.walk`s per module is the obvious saving and was NOT taken, because this
  host's own variance on the same tier measured 49-64 s in one afternoon — larger than the saving.
  Re-measure on CI before optimising. It is not a skip-list: every
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

- **A new test that passes against the base is not a regression test — and CI now says so (0.4b,
  0.115.0).** `scripts/red_in_ci.py` differences this branch's pytest collection against the base's,
  runs the NEW ids **twice** — once here, once with the base's `src/` first on `PYTHONPATH`, the same
  install `prove_red` uses — and fails a src-touching PR when none of them is red against the base.
  * **Twice, not once, is what makes the verdict believable.** A new browser cell on a runner with no
    Chromium, a cell red for an unrelated reason, a broken fixture — each fails against the base too,
    and a single-run design scores every one of them as a guard. Same rule as
    `prove_red._require_a_live_killer_suite`: check the unmutated tree FIRST.
  * **An ImportError against the base is `inconclusive`, never a kill.** A PR adding a `src/` module
    gives its new tests nothing to import there; pytest exits non-zero and reading the exit code
    reports a green gate over a test that never executed a line of what it claims to guard.
  * **Only `src/` is swappable, and that is structural.** `benchmarks/` and `scripts/` sit at the repo
    ROOT, which is `sys.path[0]`, and nothing on `PYTHONPATH` gets in front of it — R4.77 wearing a
    second hat. **Measured on 1.3 against pre-1.3 main: 34 new ids, 10 `guards`**, and the `no_guard`
    rows are exactly the two limits — one structural scan reading `src/` BY PATH (R4.75) and the rest
    aimed at `benchmarks/variance.py`. Those need `tests/_arming.py`, and the loud channel says so.
  * The loud channel's acknowledgement is **derived, not typed**: a PR shipping a registered mutation
    under `tests/mutations/` has proven a guard red through the stronger instrument, and that is its
    own verdict rather than folded into a pass. Quiet is an allowlist; every loud verdict names a
    remedy, asserted both ways.

- **The strong tier is `claude-opus-5`, and the price table may not drift from it (0.120.0).**
  `settings.tier` defaults to **strong**, so a learn runs ENTIRELY on `settings.model` — `fast_model`
  (`claude-haiku-4-5`) is reached only under `ULTRACUA_TIER=fast`, and `vision.py` inherits the strong
  model for grounding, which §9.1 says is not a rare path on Odoo. Every learn-time dollar is the
  strong tier's.
  * **A model absent from `obs._PRICES` is not free, it is UNKNOWN** — `cost_usd` goes None for the
    whole run and `outcomes._cost_of` raises `unpriced_spend`. Correct and loud, but discovered at the
    END of a paid run, so the guard DERIVES the models from `config.settings` rather than repeating
    them (`test_every_model_the_settings_can_name_is_priceable`). The table and the default are edited
    by different slices; that is the drift. `tests/mutations/model_pricing.py` attacks BOTH directions
    — 4 killed — because a DEFAULT price is worse than a missing one: it turns an unknown bill into a
    confident wrong number, which is 1.3's `or 0.0` one layer down. Sonnet 5's introductory rate is
    deliberately NOT in the table: a price that changes on a calendar date makes two runs incomparable
    across it.
  * **TWO PLAUSIBLE DEFECTS WERE REFUTED BY MEASUREMENT, and one of them nearly became a `src/`
    change.** A published parameter table says sampling params are removed on Opus 4.8/5 and return a
    400 — so `decide()`'s unconditional `temperature=1.0` looked like a broken escalation path and a
    silently-inert `reflect=True`. **Probed: accepted on both, alone and with `thinking: adaptive`.**
    Five one-token calls, and the fix would have been a no-op edit to a mechanism that was never
    broken. And "Opus 5 thinks by default so it costs more" has a TRUE premise and a false conclusion:
    4× the output tokens on `17*23`, **+2% and inside the noise** on a real agent turn. Measure the
    thing at the size you actually run it.
  * **The A/B that decided it: 3 real learns each on the demo-shop fixture.** Opus 5 $0.0539 mean
    (0.5% spread) vs Opus 4.8 $0.0552 (**12%** spread), 20.5 s vs 26.3 s, 3/3 goal-reached both. The
    VARIANCE is the reason, not the mean: B3 gates cost through `compare_records`, whose tolerance is
    `max(rate_floor, baseline_std)`, so a noisy strong tier widens the gate against itself.

- **The engine chain is KEYWORD-ONLY after its subject (1.1, 0.115.0).** Seven positional parameters
  across six functions, down from 98 — `url` for the three URL-driven entry points, `session` (+`step`)
  for the two in-session helpers. `flow.py`'s verification call used to pass sixteen positional
  arguments of which FOUR were a bare `None`, at positions 5, 7, 9 and 14; each is now named beside the
  reason it is None.
  * **TWO SENSORS, because neither is enough.** The arity pin (`POSITIONAL_PREFIX`, a committed table
    per function) is fully satisfied by `_replay(..., on_step=finalize, finalize=on_step)`. The
    forwarding pins are what catch that: **every forward is `name=name` unless it is registered in
    `RENAMED`** — thirteen rows against 132 forwards, so a mistranslated site is a one-line review —
    and `DELIBERATE_DROPS` is asserted BOTH ways, because an undeclared drop is a parameter silently
    taking its default. A runtime cell drives the five browser-free edges with a distinct object per
    argument and asserts `is` identity on arrival, including the positional subject.
  * **`tests/mutations/keyword_only_chain.py` keeps all three honest — 7 killed, 0 survived.** Three of
    the seven are type-silent swaps (`str`/`str`, `bool`/`bool`, two `Optional` callables) that the
    arity pin cannot see at all.
  * **The overlap with `scripts/ratchets.py` is two SENSOR CLASSES, not a duplicate.** The ratchet
    reads `src/` by path, so under `prove_red` it parses the pristine tree and cannot contribute a
    kill; the test reads `inspect.getsource(flow_mod)` and does. Collapsing them would disarm one.

- **The write question has a NAME, and the key has one place (1.6, 0.116.0).** `spec.mutate is not
  None` was written out at 27 sites across three modules and `flow_key(spec.goal, spec.start_url,
  spec.scope)` at 24. Nothing was wrong with any of them — that is the point: 27 chances to ask the
  DECLARATION question where the RECIPE question was meant, and 24 chances to reorder two of three
  `str` arguments and key a flow to somebody else's cache entry.
  * **The split is structural, not a naming convention.** A declaration question is an ATTRIBUTE of
    the spec (`spec.write.declares_write/_confirm/_precheck/_barriers/_multiple_barriers`); a recipe
    question is a FUNCTION you must hand the recipe to (`is_write_flow`, `recipe_write_count`,
    `recipe_has_multiple_writes`). **You cannot ask a recipe question of `WriteClass` because it does
    not hold one** — which is what makes R3.5 unaskable rather than merely discouraged. An UNDECLARED
    write (nothing declared, a cached step carrying `mutating=True`) is a write in FACT and not in
    DECLARATION, and `_auth_retry_allowed` keeps both, in separate arms with different remedies.
  * **`spec.write` is computed per access and must stay that way.** `cli.py`'s `flow set-mutate`
    assigns `spec.mutate` after construction, so a `cached_property` would answer with the OLD
    declaration for the rest of the process.
  * **The evidence is a DIFFERENTIAL, because a rename is exactly where a semantic change hides.**
    4410 cells against `main` — `is_write_flow` (90), `_auth_retry_allowed` (1440) and
    `_preflight_row` (2880, reaching 8 distinct exception classes) over every input shape that can
    change an answer — **0 differing**. That evidence dies at merge, so the `_auth_retry_allowed`
    half is now committed as `tests/goldens/auth_retry_truth.json`: **a diff there is a change to
    what may be RE-DRIVEN after an auth refresh**, which is inviolable #3, and gets reviewed cell by
    cell. `_preflight_row` got no golden on purpose — its refusals are already bound by
    `test_refusal_codes.py`.
  * **A RATCHET COUNTS ONE SHAPE, and the other spellings walk free.** The ratchet reads
    `<x>.mutate is (not) None`; a site asking `spec.mutate.has_confirm()` behind a `declares_write`
    local is the same question in different clothes, and **the tree was ratchet-clean at 0 with FOUR
    of them still in it**. Closed as a class by a scan over the three `MutateSpec` methods that answer
    a classification — and note both directions of that scan are load-bearing: `StepConfirm` has its
    own `has_confirm()` (a barrier answering about itself, which must NOT be flagged), and
    `m = spec.mutate; m.has_confirm()` evades a naive receiver test.

- **The engine takes TWO BUNDLES, and the risk inverted (1.8, 0.118.0).** `run_cached`'s public
  keyword signature is unchanged; it builds a `RunOptions` and a `RunHooks` in one place and threads
  those, so the six inner functions went from 8–23 keyword parameters each to a subject plus two
  objects.
  * **The split is by FAILURE MODE.** A wrong option is usually loud — a missing header, a run that
    does not start. **A dropped hook is SILENT**: the run still succeeds and the caller's callback
    never fires. So the hook-fire counts were captured BEFORE anything moved
    (`tests/goldens/hook_fire_counts.json`) and asserted after — unchanged at 19 `on_step`,
    5 `prepare`, 5 `finalize`, 3 `pre_write`, 1 `verifier` across five rows. One of those rows exists
    only to make `verifier` non-zero, and reaching it took two attempts: `ScriptedProvider` returns
    `done` once its list is EXHAUSTED, so a shorter script still ends cleanly and never asks the
    verifier. `max_steps` is the way in.
  * **A PARAMETER LIST ENFORCED WITHHOLDING; A BUNDLE DOES NOT.** `_learn` could not read `params`
    because nobody handed it one; `_replay` could not read `grounding`. Two objects hand every
    function all twenty-one values, so that enforcement now lives in `RECEIVED_BEFORE_1_8` — each
    function's pre-1.8 parameter set, and **no function may read outside its row**. Measured:
    `_learn` reads 14 of 23, `_replay` 14 of 22, `_verify_by_replay` 0 of 11.
  * **A withdrawal is a NAMED VERB**: `opts.without("grounding")`, `hooks.without("on_step",
    "finalize", "pre_write")`. Those were a `None` nine arguments deep, or an absent argument nobody
    could see. `CLEARS` holds them, asserted both ways. **R4.12 is PRESERVED by
    `hooks.without("pre_write")`** — bundling would otherwise have closed an open finding as a
    migration side effect, and a silent fix is as unreviewable as a silent break.
  * **Freeze the bundles.** Three parameters used to be REBOUND in place (`max_steps`, `samples`, and
    `pre_write` nulled after its first fire); the frozen dataclass refused all three, which is the
    guard working — `hooks` outlives the call, so nulling it would silence the probe for every later
    run. Each is a local now.
  * `max_steps` could NOT be bundled: its two callers pass different values (one resolved, one
    `settings.max_steps`), so it is a renamed forward and stays explicit.

- **A line-granular prose restore OVER-corrects, and 1.8 measured how.** 1.6's rule (restore a
  clobbered comment by checking the reverted line is byte-identical to one in `HEAD`) is right until
  a single line holds BOTH a string and code using the same word:
  `extra = {"finalize": fin} if finalize else {}`. The substitution hit the string too, the restore
  reverted the whole line, and the code silently went back to the old name — a `NameError` two
  test-runs later. Substitute per TOKEN, or fix those lines by hand after checking which ones carry
  both.

- **Eight DOORS reach the engine, and they do not permit the same things (1.7, 0.117.0).**
  `tests/test_door_policy.py` prints and commits what each caller of `flow.run_cached` may do. The
  door SET is derived from the source, so a ninth cannot be quietly absent.
  * **The column it exists for is `may_reauthor_a_write`, and it is DERIVED, never typed.** In
    `mode="auto"` a replay that fails irrecoverably falls through to a full re-author, and
    re-authoring a write flow PERFORMS THE WRITE AGAIN. Measured: **`cli_root`, `daemon_run` and
    `run_many` can reach it, and they are exactly the three doors with no flow-level gates** — no
    approval, no write gate, no idempotency key. The one gated door that can is `flows_learn`, where
    a write fires once by design and R3.13's terminal refusal bounds runs 2..N. Recorded as R4.84
    rather than changed: closing it is a refusal, and the plan forbids adding one as a migration
    side effect.
  * The whole table rests on ONE branch (`if report.success or mode in ("replay", "repair") or
    report.mode == "escalate"`), which is pinned — widen it and every `no` in that column is wrong.
  * **A liveness corpus is the other half** (`tests/test_door_liveness.py`, separate file because it
    launches a browser and `red-proof` installs none). Without it the table is satisfied by a
    codebase where every door refuses everything.
  * **The daemon refuses an out-of-set `mode`/`provider`/`grounding` BEFORE building anything.**
    The checking already existed — `run_cached` has refused an unknown mode since R4.31 — but at the
    far END, after `get_provider` built a real Router and `AnthropicGrounding()` an SDK client. And
    `grounding` was not checked at all: `if params.get("grounding") == "anthropic"` turned every
    other value into `None`, so the caller asked for grounding, ran without it, and was told nothing
    (inviolable #2, in one operator). `MODES` and `PROVIDERS` are imported from the modules that
    define them — two lists of the permitted values is how one silently stops permitting what the
    other does.

- **Do not sed a file that is 40% prose ABOUT the shape you are removing.** Measured at 1.6: a
  blanket substitution across `flows.py` rewrote **ten comment and docstring lines**, including
  `_auth_retry_allowed`'s explanation of R3.5 — which then read as if the NEW named question were
  the wrong one, the exact opposite of what it means. **The AST ratchet reported a clean 0 and was
  right about the code and blind to all of it.** Restore prose by checking the reverted line is
  byte-identical to one that really existed in `HEAD`; anything left over is prose this slice wrote
  and must be fixed by hand.

- **Six shapes may only ever SHRINK — the ratchets.** `python scripts/ratchets.py` counts them by AST
  (`--print` for every site, `--update` to re-seed) and `test_every_ratchet_holds` runs it in the fast
  tier. Today: `cli_system_exit` 34; `flow_key_transcriptions` **2** (25 until 1.6, and 2 is the
  FLOOR — `FlowSpec.key`'s own body plus the raw `ultracua <url> <goal>` CLI, whose argparse Namespace
  has `url` rather than `start_url` and is not a FlowSpec at all); `engine_positional_params` **7**
  (98 until 1.1; 7 is its END STATE, one subject per call, not a number still coming down); and
  **three at ZERO** — `run_record_write_sites` (1.5), `bare_flow_replay_error` (1.4) and
  `spec_mutate_raw` (1.6) — each tagged with the Phase-1 step that removed it. **A shrink FAILS
  too**, asking for `--update`: a ratchet that tolerates progress silently stops ratcheting, and the
  next regression is measured against the old, looser number. A derivation that matches NOTHING fails
  with its own message, the rule `prove_red` already applies to a stale mutation. **A ratchet whose end
  state is ZERO is an EARNED exemption** (`MAY_BE_ZERO`): it must prove its own pattern is still live
  from the other half of the same walk — the writes INSIDE the sink, the raises of the SUBCLASSES — and
  `LIVENESS_GUTS` in `tests/test_ratchets.py` is what arms that, one row per exemption, with a missing
  row a red test. **Do not quote `reshape-plan.md`'s counts** — they were greps, and two of
  five were wrong (grep counts the shape inside COMMENTS: `spec.mutate is not None` appears in three
  findings' prose, inflating 27 to 33). Derive, then cite.

- **The mutants must stay dead, and NOBODY TYPES THE LIST (0.6).** `python scripts/mutation_sweep.py`
  runs every registry under `tests/mutations/` — the set is derived from the DIRECTORY, each registry
  declares its own `KILLED_BY` (a mutant may override it), and the TIER SPLIT comes from the tier
  manifest. `--tier fast` is the merge gate's half (**66 mutants, 2m59s**, no browser);
  the weekly `mutation-sweep.yml` runs everything including the nine known mutants, whose killers are
  page-side and need Chromium. Both are asserted to partition the set, so neither can silently drop a
  registry — which the six hand-typed `ci.yml` steps this replaced could not (R4.107).
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

* **A LOUD CHANNEL HAS TO SURVIVE BEING PRINTED, and this slice's audit found that mine did not.**
  1.3 made `cost_usd: None` reach the record and added two `compare_records` verdicts carrying
  `baseline`/`current` as None — then `variance._gate`, the function that decides the exit code,
  formatted both with `f"{x:.4g}"`. `TypeError: unsupported format string passed to
  NoneType.__format__`. So a correctly-computed `regressed: True` died BEFORE printing the `[FAIL]`
  row, its detail, or `== REGRESSION ==`; and the inverse case — a baseline whose cost is unknown,
  which is DESIGNED to pass — exited 1 instead. **`_money()` was written for exactly this and applied
  to two of the THREE print sites.** The cell that existed to prove the channel was loud asserted on
  `compare_records` and never called `_gate`. Two rules fall out: a formatter that cannot render an
  unknown will meet one, and a cell about a LOUD channel has to drive the thing that speaks.

**And one instrument lesson, caught in the act.** `test_every_escalate_report_carries_its_usage` first
read `flow.py` from `src/` BY PATH, and its registered mutation was reported as a SURVIVOR while the
guard was perfectly fine — `prove_red` installs its mutant as a copy on `PYTHONPATH`, so a
path-reading cell parses pristine source. That is R4.75 happening live. `inspect.getsource(module)` is
the fix, and it is already written in `prove_red`'s own docstring.

**`tests/_arming.py` decides what a `TypeError` MEANS by its traceback depth.** A guard invoked with
the wrong arity raises before entering the function (`tb_next is None`) — that is S14's trap and it
is refused as a kill. A `TypeError` raised INSIDE the guard can be the defect itself: 1.3's `_gate`
mutation reproduces by making `f"{None:.4g}"` raise, which is precisely the crash it proves.
Refusing every `TypeError` would have forced a mutation that states something other than the defect.

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

## B4's substrates: two lists, one door, and a probe aimed at the broken page (0.124.0)

The 14-scenario corpus is real — seven per substrate, bound to their oracles in `benchmarks/corpus.py`,
armed offline and verified against live containers. What is worth carrying forward is what building
it broke.

* **THE ORACLE SET HAS EXACTLY ONE DERIVATION, and for one release it had two (R4.88).** PR #202 put
  the Gitea seven in `corpus.py` and left `oracles.REGISTRY` standing, so `--arm-oracles` — the gate
  a scored run must pass — armed **2 of 7** under names no scenario uses and printed *"The oracle set
  is armed."* Six cells in `tests/test_oracles.py` each claimed to be "derived over the registry, so
  an oracle added tomorrow is covered", and all six walked the stale one; the only oracle with real
  SQL was therefore never seen by R4.86's clock scan. **The suite was green because a different
  helper armed the corpus set.** `REGISTRY` and `oracles.for_substrate` are deleted — the set cannot
  live in `oracles.py` anyway, since it is a fact about the corpus and `corpus.py` imports
  `oracles.py`. The sensor drives the OPERATOR surface and compares its printed NAMES: a count would
  not have caught it, because 8 falsifications is a plausible-looking number.

* **THE AGENT'S FIRST PAGE WAS ODOO'S DATABASE MANAGER, WITH DELETE NEXT TO THE RESET TEMPLATE
  (R4.89).** Two databases (`bench`, `bench_seed`) and no `--db-filter` means Odoo cannot pick one,
  so it serves the database manager — measured, at `/`, `/web` AND `/web/login`, all the same
  43,551-byte page listing both with Delete / Duplicate / Backup / Restore. And `health_path` was
  `/web/database/selector`, so **readiness was pointed at the very page that means the app is
  unreachable**: R4.85 restated, since a compose healthcheck, a `min_body_bytes` floor and
  `assert_writable` all pass happily against it. Bound it is 4,430 bytes; unbound 42,559; **both are
  HTTP 200**, so the body is the only discriminator. When you add a substrate, ask what its start
  page actually serves before trusting any probe that says it is up.

* **`Odoo.reset()` HAD NEVER BEEN RUN, AND RUNNING IT FOUND WHAT REVIEW COULD NOT.** It carried a
  standing note saying so. Driven now: **seed 87.6 s, snapshot 14.0 s, reset 14.8–15.5 s** across
  several runs — the plan budgeted 30–60 s for the reset. The thing review missed is that the
  template is **cold**: restoring it discards the compiled asset bundles, so the first backend page
  load after every reset paid **+2.42 s**, charged entirely to whichever scenario ran first. The plan
  predicted "tens of seconds" and asked for a warmup; the number is small, real, and order-dependent,
  which is the part that matters. `warm_assets()` at the end of `seed()` freezes a warm template
  (per-reset penalty measured back to **+0.00 s**) and at the end of `reset()` makes that a guarantee
  rather than an assumption about how the template was made.

* **A WARMUP THAT WARMS THE WRONG THING IS R4.86 IN ONE METHOD, and the first draft was one.** Its
  guard raised only when NO bundle was found, on the measured claim that an anonymous `/web`
  referenced none — true of the database-manager page, and **false the moment R4.89 was fixed and a
  real login page appeared** carrying three `web.assets_frontend*` bundles. A broken session would
  have warmed those, returned 3, never raised, and left the 4.9 MB backend pair cold. The sensor is a
  **differential** now: the authenticated page must reach bundles the anonymous one cannot. Caught by
  re-measuring after a change, which is the only reason it was caught.

* **THE THIRD AXIS HAS A SECOND TRANSPORT (R4.90).** "This host runs the substrates and CI does not"
  arrived over Docker at 0.121.0 and over **HTTP** here: the moment `seed()`/`reset()` grew a warmup,
  four cells that correctly mock `_compose` began fetching from this host's live Odoo on `:8069`.
  Measured by stopping the container — **4 failed, 22 passed**. The autouse guard now makes
  `urllib.request.urlopen` **and** `urllib.request.OpenerDirector.open` raise; both, because they are
  different objects (`_http_ok` uses one, `warm_assets`/`rpc` the other) and a half-closed guard reads
  as closed. `test_every_probe_the_lifecycle_calls_outside_compose_is_neutralised` reads the
  lifecycle methods' own source, so the next probe added to `seed()`/`reset()` fails there instead of
  silently un-mocking a cell.

* **A CORPUS EXPECTED ANSWER IS CHECKED AGAINST THE BROWSER, NOT AGAINST SQL.** They disagree easily:
  an Odoo action carries a domain AND a context, and most CRM entry points default to
  `search_default_assigned_to_me`, so the same table answers 17 or 10 depending which menu reached
  it. An expected answer that differs from the rendered list mints `wrong_data` against an agent that
  answered correctly — inviolable #2 fired from the harness's own mistake. All five Odoo reads were
  driven in a real browser first. Two consequences worth keeping: `/web#model=…&view_type=list` does
  **not** work in Odoo 17 (the hash is rewritten to `/web#cids=1`) and a numeric `action=` is a
  database-assigned id, so start URLs use XML-ID actions; and **no read scenario may expect a number
  ≥ 1000**, because Odoo renders money as `$ 40,000.00` and `check_answer`'s digit-boundary match
  will not find `40000` in a correct answer. Both are pinned by tests.

* **A PREMISE THAT MAKES A SCENARIO DISCRIMINATING IS ASSERTED, NOT ASSUMED.** `odoo-filter-status`
  asks about the **Proposition** stage and not Won, because the seed holds New 3, Qualified 5,
  Proposition 6, Won 3 — so on Won an agent that filtered the WRONG stage still reports the accepted
  answer and the row scores green while measuring nothing. `_stage_count` refuses a clash rather than
  trusting the comment, and `_top_opportunity` refuses a tie for `gitea-sort-list`'s reason one
  substrate over.

* **ONE READ GOAL IS ALLOWED TO TRIP THE KEYWORD CLASSIFIER, AND IT IS DECLARED.** A read goal
  carrying a `MUTATING_KEYWORDS` token MANUFACTURES the `over_gated` the corpus exists to measure.
  `odoo-open-record` trips it on "order" because that is what the pair isolates and a sale order has
  no other name — so `CorpusEntry.keyword_read` declares it, asserted **both ways**: an undeclared
  goal that trips fails, and a declared goal that no longer trips fails too. The second is the
  direction that rots quietly, since rephrasing a goal leaves the flag behind.

* **AN ORACLE MAY DECLARE WHAT IT CANNOT SEE, IF THE SET OF SUCH ORACLES IS PINNED.**
  `odoo-idempotent-replay` cannot distinguish "the mechanism ran and suppressed the second write"
  from "`_precheck_done` returned already-done before the browser did anything" — both leave one
  lead. `INCOMPLETE_WITHOUT` says so, and the set of oracles carrying it is asserted to be exactly
  that one, so the proxy landing forces a red test and a second self-excusing oracle cannot appear
  quietly.

* **AND THE LIMIT THE ARMING GATE HAS, stated because it is easy to forget:** `arm_oracles` replaces
  `probe` with the falsified rows, so **a probe aimed at the wrong table arms perfectly**. Only two
  instruments see that — a surface cell that runs the real probe against a fake serving its own
  query surface (offline, in CI), and `benchmarks/oracle_liveness.py` against a real container. They
  are not redundant. The Odoo liveness arm writes through `call_kw` rather than SQL for the same
  reason: an `UPDATE` proves the probe can read a row the test wrote, not the row the app writes.

## B4's last piece: the proxy that answers "did the mechanism RUN?" (0.125.0)

`odoo-idempotent-replay` could not be scored from the database, and said so. From the server side
"the replay ran and the idempotency mechanism suppressed the duplicate" and "`_precheck_done`
returned already-done before the browser did anything" are THE SAME WORLD: one lead, no second row.
`benchmarks/idempotency_proxy.py` supplies the missing fact — whether a request left the browser
carrying an `Idempotency-Key`.

* **THE CHEAP ROUTE WAS WRONG FOR A CONCRETE REASON, AND CHECKING WHICH IS THE WHOLE LESSON.**
  `run_cached` already takes `record_har_path`, threaded to the session, already used by the
  WebArena arm — so a HAR looked like the obvious answer and needed no new component. It fails on
  one fact: `_already_committed` opens its **own** `BrowserSession` with no `record_har_path`, so on
  the precheck path the HAR is never written at all. The evidence would be an ABSENCE with two
  causes ("the mechanism did not run" / "capture broke"), which is the shape half this register is
  made of. A proxy is always listening, so "no keyed request against a busy log" is a POSITIVE
  observation. The plan said "proxy" and the plan was right.

* **A PAGE-LEVEL PROBE CANNOT SEE A SHARED WORKER.** The first draft REFUSED protocol upgrades, on
  a measured claim that Odoo opened **zero** websockets — `page.on("websocket")` across a full login
  and list view saw none. False, and the probe was watching the wrong object: Odoo 17 connects its
  bus from a shared worker. Driving a browser THROUGH the proxy showed it at once —
  `/websocket?version=17.0-3`, refused, **retried five times in eight seconds and climbing**. So the
  "bounded, stated gap" was really a permanent reconnect loop on a host this repo already says
  cannot adjudicate under load. Upgrades are tunnelled now; the retry count went 5 → 1.

* **AND AN HTML PROBE ASKED THE WRONG LAYER TOO.** "Does the substrate emit self-absolute URLs?" was
  measured across five served pages — gitea root/issues/issue, odoo login/backend — and came back
  **0 of 5**, which read as "a proxy on its own port is transparent". Then a real browser failed to
  log in: Odoo's form login answers `303 Location: http://localhost:8069/web`, absolute, in a
  HEADER. Unrewritten, the browser leaves the proxy at the moment it authenticates and every later
  request, including every write, is invisible. Two probes, two layers, and only the browser found
  it. **`urlopen` also follows redirects by default**, which swallowed that 303 behind the browser's
  back — a proxy that resolves redirects for its client is not a proxy.

* **THE GATE READ "I CANNOT TELL" AS "NO" (R4.91).** `arm_oracles` judged each falsification with
  `if verdict.satisfied:`, and `None` is falsy — so an oracle that can never ADJUDICATE armed
  exactly as if it had said no. That is the gate's own subject one state over: it exists to refuse
  an oracle that cannot fail, and one that cannot decide never fails either. Latent while every
  oracle answered from one source; live the moment one grew an input it could be missing. Note the
  suite did not have this hole — `test_the_whole_corpus_arms` asserts `satisfied is False` per row.
  The OPERATOR GATE did, which is the same surface R4.88 was about.

* **ORDER THE TWO SOURCES SO THE GATE STAYS MEANINGFUL.** `OdooIdempotentReplayOracle` consults the
  server FIRST — a missing, wrong or doubled record is a NO whatever the wire says — and the request
  evidence only ever DOWNGRADES a would-be pass. So every falsification is rejected without any
  proxy at all, an oracle wired without one still proves it can say no, and the mechanism check
  cannot be satisfied by refusing everything.

* **`INCOMPLETE_WITHOUT` WAS RIGHT UNTIL IT WASN'T, AND IT FORCED ITS OWN REMOVAL.** PR 4 declared
  the gap and pinned the set of self-excusing oracles to exactly one, "so the day the proxy lands,
  deleting the marker is forced by a red test". It fired, in the slice that landed it. What replaces
  it is stricter, not looser: the oracle now REFUSES (`satisfied is None`) rather than scoring the
  server half alone.

* **ONE JOIN BETWEEN THE ORACLE'S VERDICT AND B3's VOCABULARY.** B3 fixed `outcomes.Oracle`, B4 built
  `oracles.Verdict`, and **nothing joined them** — every construction of the B3 type lived in a test,
  and the first scored run would have hand-written the translation at its call site. That is R4.88's
  shape (two representations, no derivation) waiting to happen. `corpus.bench_oracle` is the one
  derivation, and its load-bearing clause closes this slice's loop: `satisfied is None` →
  `available=False` → B3 scores `unscored` → the coverage channel fails the run unless a human
  acknowledges that exact pair. **Forgetting to wire the proxy is LOUD, not a quiet green.**

* **A SCAN THAT MATCHES ITS OWN PROSE, FOR THE THIRD TIME.** The cell forbidding a real substrate
  port in the proxy tests failed on its own docstrings, then on its own needle tuple. Both were
  CLAUDE.md's existing rule ("do not sed a file that is 40% prose about the shape you are removing")
  wearing an assertion, and a third needle would only have moved the collision. The fix was to stop
  scanning TEXT and assert the property instead — every `IdempotencyProxy(...)` in the file is
  constructed with the loopback stub — which caught a real one on its first working run.

## Gitea runs, and the login was lying in the safe direction (0.126.0)

Seven of the fourteen corpus scenarios had never been driven. Driving them cost **$1.12** across eight runs and found
five defects, three of them in the bench's own login. The six non-timer learns mean **$0.044**,
which confirms the previous slice's guess that Gitea would be cheaper than Odoo's $0.101 — the
DOM is smaller. The two `gitea-start-timer` runs cost **$0.86 between them**, more than the other
six combined, because a failing learn spends its whole budget before returning nothing.

* **A SELECTOR LIST MATCHES IF ANY BRANCH DOES, WHICH IS HOW A CHECK GETS DISARMED BY A HELPFUL
  FALLBACK (R4.98).** Gitea's `success_selector` was `".dashboard, #navbar"`, and `#navbar` is on
  the logged-OUT page — measured 1. So `_login_succeeded` returned True having authenticated nobody,
  `storage_state` carried no session, and every scenario would have run ANONYMOUSLY. **The blast
  radius is the asymmetry this register keeps re-filing:** the issue list serves HTTP 200 with all
  14 issue-row links to an anonymous visitor, so **all five Gitea reads would have scored green**;
  only the writes bite (the time-tracker control: 0 matches anonymously). Reads unaffected, writes
  fatal — the same sentence as 1.8's premise ordering and R4.90's HTTP leak. Its sibling (R4.97)
  was the opposite error in the same dict: `button[type=submit]` matched **nothing**, because Gitea's
  control is a `<button>` with no `type` and a CSS attribute selector matches the ATTRIBUTE, not the
  HTML default.
* **SO THE SENSOR IS A DIFFERENTIAL AND TAKES NO NEW DECLARATION.**
  `assert_login_discriminates` interrogates the SAME `success_selector` the product uses, on the
  same page, with and without the session: absent anonymously, present authenticated, and the submit
  control reachable. One function rather than two callable halves, precisely so both always run —
  R4.86's `warm_assets` lesson on a third surface. A `,` in a `success_selector` is ALSO pinned
  offline, because the live half needs a container and the mechanism can be refused at edit time.
* **ATTRIBUTE BY WHERE IT HAPPENED, NOT BY EXCEPTION TYPE (R4.99).** `_Record.harness_error` was
  hard-coded `""` and nothing ever set it, so B3's whole `harness` family was unreachable from this
  runner: a broken login published **`not_authored`**, a loud SCORED verdict blaming the product for
  the bench's own misdeclaration. `classify` had the right clause all along — "a harness fault still
  wins: if the reset or the login broke" — and it could not fire. The fix is a PHASE, not another
  `except`: authenticate-and-observe is its own `try` that records `harness_error` and skips the
  learn, so a fault added tomorrow is attributed by position. **Check both directions when you touch
  this** — a fix that routed everything to `harness` would pass its own cell while silently
  reverting R4.96.
* **SHIP THE INSTRUMENT THE DIAGNOSIS USED (R4.100).** R4.94 was diagnosed by counting **LLM calls**
  against the budget, and the sensor that shipped read `len(res.steps)` — the CAPTURED steps. They
  agree only while the agent records an action per turn, and disagree exactly when it records none,
  which is the case the sensor exists for. Measured: `llm_calls` 20, `step_budget` 20, `steps` 0,
  `hit_step_ceiling` **false**. The evidence was already in the previous slice's own artifact and
  was read past. It paid for itself the same hour: the corrected sensor is what let budget
  starvation be RULED OUT as the cause of `gitea-start-timer` failing, rather than assumed.
* **THE SUBSTRATE CONTRAST IS REAL AND IT IS LARGE.** Gitea reads: `mutating_steps` **0**, no
  gating, four of five `ok`. Odoo reads: 3-4 mutating steps from JSON-RPC POSTs, the mutation gate
  refusing every 0-LLM replay, `over_gated`. And `gitea-comment` is the first scenario anywhere in
  this benchmark to demonstrate the whole product claim end to end — a real write, learned in 2
  steps, **replayed at 0 LLM calls**, gate not refusing, server holding exactly one comment.
* **A ZERO-ACTION TASK PRODUCES NO RECIPE, AND THE VOCABULARY CALLS THAT A PRODUCT FAILURE
  (R4.101, OPEN).** `gitea-search` answers correctly in **0 steps** — the target issue is already on
  the start page — so nothing is cached and the run scores `not_authored`. Two problems that must
  not be fixed together: the scenario cannot measure search (and with 7 issues on one page, no
  `url_path` makes it), and `not_authored` reads as the product failing when the truth is that the
  task has no automation value to measure. **Settle it before `baselines/customer_v1.json` is
  written** — it is a permanent 1-in-7 on Gitea's availability that is not a product failure.
* **A SCAN MATCHED ITS OWN PROSE FOR THE FOURTH TIME**, and the fix is the one already written down:
  stop scanning text, assert the property. A cell comparing two source offsets to pin handler ORDER
  went red on the comment explaining the fix. Handler order is a fact about the AST; read it there.

## A search scenario that required no search — and an extraction task must end where the evidence is (0.127.0)

R4.101 half one. `gitea-search` looked for "marmalade", which is in an issue TITLE, so the answer was
on the start page: the agent answered correctly in **0 steps**, `flow.py` caches only
`if success and steps:`, and a correct answer scored **`not_authored`**. The scenario measured no
search at all while being named for one.

* **THE PREMISE IS ASSERTED OFFLINE, AGAINST THE COMMITTED SEED.** `SEARCH_TERM` is single-sourced
  (the goal is an f-string over it, so the goal and the expected answer cannot drift), and
  `tests/test_search_premise.py` derives five facts from `substrates.ISSUES` with no container: the
  term is in **no title**, in **exactly one body**, its issue is **OPEN**, its title is **no other
  scenario's answer**, and the goal still names it. That is the Odoo rule — *a premise that makes a
  scenario discriminating is ASSERTED, not assumed* — finally written for Gitea.
* **AN EXTRACTION TASK MUST END ON A PAGE HOLDING BOTH THE EVIDENCE AND THE ANSWER.** Two wordings
  failed before the third, both by ending on the filtered LIST, and **no list state can satisfy
  them**: the search term lives in the input's `value` rather than the page text, and bodies are not
  rendered. So `?q=16-bit` shows exactly one row with nothing tying it to the term, and the product
  refused both times — *"the only listed issue is 'Alpha channel lost on export'"*, naming the right
  row and declining to claim it matched. **That is inviolable #2 working, not an obstacle to route
  around.** Ending on the ISSUE page settles it (term 3x, title 4x). Measured: **0 steps /
  `not_authored` -> 3 steps / `ok`**.
* **THE ORACLE AND THE AGENT WERE SEARCHING DIFFERENT INDEXES, and only a title term hid it.**
  `_search_title` asked the API to search; `?q=` on the issues API matches **titles only**, while the
  web UI reaches BODIES — `?q=16-bit` returns issue 2 in a browser and nothing at all through the
  API. Agreement was a coincidence of the old term. The fix keeps the server as the source of truth
  (fetch every issue, real bodies) and moves the MATCHING into the harness, where the corpus's own
  definition of the task belongs.
* **A FAKE THAT OMITS A FIELD IS A FAKE THAT AGREES BY ACCIDENT.** `tests/test_oracles.py`'s Gitea
  fake served issues with no `body` at all. Invisible while matching was title-only; the moment the
  term moved, every cell using it computed a blank expected answer. The fake now carries bodies and
  takes the token from `corpus.SEARCH_TERM` rather than retyping it.
* **`keyword_read` SPEAKS FOR THE GOAL, NOT FOR A STEP.** `gitea-search` is `keyword_read=False` and
  its learned recipe still carries `mutating_steps: 1` from source `keyword` — the `press Enter` that
  submits the search. The gate did not refuse, so this is D0 territory (a `mutating` mark is a GUESS;
  be conservative because of one, never refuse a flow for one) and it is recorded rather than fixed.
  Worth knowing twice over: a reviewer reading the flag alone would conclude the row cannot
  manufacture over-gating, and this is the first Gitea row that can reach that path at all.

## `no_actions_needed`, and why EXCLUDING a row is not the neutral choice (0.128.0)

R4.101 half two. A task whose answer is on its landing page is completed with no actions, so nothing
is cached and there is no speed-up to measure. That published `not_authored` — "the product was asked
to author this flow and did not" — for a run returning the exactly correct answer, costing the Gitea
headline **14 points**.

* **THE FIRST DRAFT MADE THE ROW LEAVE THE AVAILABILITY DENOMINATOR, AND AN EXISTING GUARD KILLED
  IT.** By analogy with `expect_refusal`, which leaves the availability rates and gets
  `gate_holds_rate`. `test_a_loud_outcome_is_never_counted_as_available` showed availability going
  **1.0 where counting the row gives 0.5**. **Excluding a row RAISES the mean** — it is not the
  neutral option it reads as — and an acknowledged exclusion makes the inflation permanent, which is
  R4.96 and is the same objection this codebase already records against routing such a row to
  `unscored`: *the acknowledgement IS the deletion*. `expect_refusal` is not analogous because the
  corpus DECLARES it before the run and it keeps a rate of its own; nothing is discovered at run time
  and nothing is deleted.
* **AND THE ZERO IS THE HONEST NUMBER, not merely the safe one.** `availability_rate` asks whether
  the product can do the task deterministically at 0-LLM and 5-10x faster. With nothing to re-plan,
  every run pays the LLM again — no speed-up, so not available. **What was wrong was never the 0; it
  was the LABEL.** `rec["no_recipe"]` enumerates the rows so availability-among-automatable-tasks
  stays computable, and channel 0 reports them — acknowledgeable precisely BECAUSE nothing is
  excluded.
* **MINTED FROM TWO AFFIRMATIVE OBSERVATIONS, never from absence.** `recipe_steps == 0` (the learn
  recorded no action) and `learn_found is True` (it completed the task anyway). The step count alone
  is not enough and the corpus holds the control: `gitea-start-timer` is also zero steps and nothing
  cached, after 40 turns and $0.58, and is a genuine discovery failure.
* **TWO OF THE SLICE'S OWN CELLS PASSED FOR THE WRONG REASON, AND THE ARMING HARNESS SAID SO.** The
  zero-steps control used a WRITE, so the read-only guard blocked the mutation rather than the clause
  under test. The missing-observation cell set both fields to None, so the sibling conjunct covered
  it — the mutation's real victim is a **pure-LLM arm that answers correctly while recording no step
  count**, where reading `None` as falsy republishes every success as "this task needed no work".
  A survivor here is a hole in the CELL as often as in the code.
* **A SECOND IDENTICAL `findings.append` MADE AN OLD MUTATION AMBIGUOUS.** Channel 0 now has two, so
  a needle matching the append alone matched twice and was reported STALE rather than passing. That
  is the rule working; anchor a mutation on enough context to name one site.

## The timer failure was never about reasoning: below the fold is invisible (R4.102, 0.129.0)

`gitea-start-timer` spent **40 turns and $0.58** recording **zero steps** on a two-click task. The
diagnosis cost **nothing** and took three probes, because the first question was the free one.

* **ASK WHAT THE AGENT COULD SEE BEFORE ASKING WHY IT FAILED.** The page holds the control -- a real
  `<button>`, "Start Timer", `aria-label="Start Time Tracking"`, `is_visible` True -- and the agent's
  observation of that page holds **73 elements and no timer at all**. Scroll and re-snapshot and it
  appears at once. Forty turns of not finding it was a grounding limit, and no budget would ever have
  helped; R4.100's corrected ceiling sensor is what had already ruled budget out.
* **THE VIEWPORT BOUND IS NOT THE DEFECT.** `snapshot.py` drops `r.top > innerHeight` deliberately
  and says so. What is missing is any SIGNAL: `Observation` carries `url`, `title`, `elements`,
  `text`, `webmcp_tools`, `fingerprint` and nothing about off-screen content, so the agent cannot
  tell "this is the page" from "this is the top third of it". `scroll` is an available action; there
  is simply no reason to use it.
* **THE OBVIOUS FIX IS INERT ON HALF THE CORPUS, and that is the part worth carrying.** A hint from
  `document.body.scrollHeight > innerHeight` fires on Gitea and NOT on Odoo, which keeps
  `docH == vpH == 720` and scrolls an INNER container -- measured, `odoo-sort-list` shows a 720px
  body in a 720px viewport with **12** controls outside it. A sensor here must count off-screen
  ELEMENTS, not compare heights. Two layouts, one of which makes the intuitive test silently useless.
* **BLAST RADIUS: 5 of 7 distinct start pages hide interactable controls** (`gitea-comment` 15,
  `odoo-sort-list` 12, `odoo-create-lead` 8, `gitea-sort-list` 4, `odoo-open-record` 2). Latent
  wherever the target happens to be above the fold, fatal when it is not -- which is why exactly one
  scenario has failed for it and the rest scored `ok` while carrying the same hazard.
* **THE INSTRUMENT SHIPPED WITH THE DIAGNOSIS** (`benchmarks/fold_probe.py`), which is R4.100's rule
  applied on purpose rather than learned again: a fix has to be validated against the same count that
  found the defect, and a number recovered from terminal scrollback is not a baseline.

## The corpus runs end to end, and one pass is not a baseline (B5, 0.130.0)

`benchmarks/corpus_run.py` loops the corpus, mints one bench record and gates it. Both substrates
completed for the first time. **$2.32 across three passes**, one of which was thrown away.

* **THE SUBSTRATE CONTRAST IS THE HEADLINE, and it is large.** Gitea `availability_rate` **0.857**
  (reads **1.000** over 5, writes 0.500), Odoo **0.143** (reads 0.200, writes 0.000), with
  `over_gated` on three Odoo reads carrying code `drift` -- R4.27's JSON-RPC misfiling, priced.
* **BUT THE ODOO NUMBERS ARE SUSPECT BY THE CORPUS'S OWN RULE.** `odoo-menu-nav` is the declared
  in-substrate CONTROL GROUP -- "if this one fails, the failure is not about drift or saturation,
  and every other number on this substrate is suspect" -- and it failed, 0 steps at the ceiling.
  Three of seven Odoo scenarios hit `MAX_STEPS`. Read the control before reading the mean.
* **ONE PASS CANNOT BE A BASELINE, and this is the measurement rather than the principle.**
  `odoo-create-lead` learned in **6 steps** in an isolated run at 0.125.0 and captured **0 steps**
  here at the same budget. Same scenario, same corpus, same `MAX_STEPS`. That is the flake-versus-
  regression question B3 refuses to gate a single flip over, arriving with a number attached.
* **A DEAD ROW MUST NOT VANISH.** A scenario that raises is recorded as a HARNESS row, so the
  denominator always equals the corpus. Dropping it would raise the mean -- R4.96 one level up, with
  a dead container as the cause instead of a failed learn. `build_bench_record` also REFUSES a
  corpus in which nothing was scored, which is what turned a total crash into a loud error rather
  than a published 0.0.
* **A STUB CANNOT SEE A CHANGED RETURN TYPE.** `score_one` moved from `classify` (returns a
  `Verdict`) to `adjudicate` (returns a `Scored` wrapping one) and kept reading `.outcome` off the
  wrapper. Every scenario raised -- AFTER paying for its learn, because that read is the last
  statement -- and **~$0.60 was discarded**. The batch's own cells script `score_one`, so they
  exercised the shape intended rather than the shape produced. The guard that replaces them derives
  from the AST and the real class, with no stub between. Note its first draft cried wolf: two
  `adjudicate` methods are in scope and `oracle.adjudicate()` legitimately has `.satisfied`, so the
  match is on the RECEIVER, not the method name.
* **A GUARD BUILT ON A REDIRECT ASSUMPTION BLOCKED A WHOLE SUBSTRATE.** `assert_login_discriminates`
  required the success marker to be PRESENT on a logged-in fetch of the login path. Gitea redirects
  `/user/login` to `/`; **Odoo re-serves `/web/login`**, where `.o_web_client` is never present --
  measured at 0.0/0.5/1/2/3/5 s, zero throughout, so not a settle. All 7 Odoo rows refused at
  preflight for **$0.00**, which is the phase boundary working. The half was REMOVED: `refresh_auth`
  already raises `LoginFailedError` on the page the submit LANDED on, so the product owns the
  affirmative check and the removed code was re-asking it on the wrong page. **And every cheap
  portable replacement passes for the wrong reason** -- the only authenticated-ONLY cookies are
  `lang` (Gitea) and `cids` (Odoo), a locale preference and a UI setting, while the real session
  cookies are set for anonymous visitors on both.

## `odoo-menu-nav`, and the recipe length is not the action count (0.131.0)

Diagnosing the Odoo control group cost **$0.55** and most of the work was free. It found a defect in
code shipped three slices earlier and corrected a claim in a finding filed one slice earlier.

* **`steps: 0` MEANS "NOTHING WAS CACHED", NOT "THE AGENT DID NOT ACT" (R4.103).**
  `LearnResult.steps` is `list(cached.steps) if cached else []`. 0.128.0's `no_actions_needed`
  clause used it as the discriminator for "this task needed no work" — and a learn that ACTED and
  then failed verify-by-replay lands on the same 0 (`flow.py` sets `success = False` and does not
  cache, while `out["found"]` is set on the extraction path and stays True). That republishes a real
  authoring failure as "nothing to do here", in the flattering direction. **Measured with the count
  finally recorded: both rows I had read as inert took 21 ACTIONS** — `odoo-menu-nav` 21,
  `gitea-start-timer` 21. This is R4.100's shape one level down: diagnose with one quantity, ship a
  sensor on another. The clause now requires `actions_taken == 0` from `FlowReport.traces`, with
  `recipe_steps == 0` kept beside it.
* **SO R4.102'S WORDING WAS WRONG AND IS CORRECTED IN PLACE.** "Recorded zero steps" painted an idle
  agent; the truth is 21 actions against a page that never showed it the button. The finding itself
  is untouched and stronger — its evidence was always the OBSERVATION, which no step count enters.
* **THE CONTROL GROUP'S HOP COUNT IS MEASURED FROM A PAGE THAT DOES NOT EXIST (R4.104, OPEN).**
  `odoo-menu-nav` declares "two menu hops … deliberately EASY", and `url_path` `/web` redirects to
  `#action=123&menu_id=81` — **Discuss, the messaging app**. Five candidate URLs probed
  (`/web`, `/web#home`, `/web#menu_id=`, `/odoo`, `/web/webclient/home`) and **none serves the app
  menu**. The switcher opens only by clicking `Home Menu`, so the real task is three hops from a
  chat app plus a count. **When a control group fails, check its premise before its subject.**
* **A REFUTED HYPOTHESIS, WRITTEN DOWN SO IT IS NOT RE-DERIVED.** `Home Menu` is a `<button>` whose
  only name source is `title`, and neither `has-text` nor `get_by_role(name=…)` reaches it — which
  looked exactly like the cause. It is not: `BrowserSession.act` resolves every target by
  `[data-ultracua-ref="eN"]`, so the agent never goes through role+name. **My probe was broken, not
  the product**, and one `grep` at `browser.py:_sel` was the difference between a filed finding and
  a filed fiction.

## The Odoo control group was one word away from working (R4.104, 0.132.0)

`odoo-menu-nav` failed at 21 actions and, by its own comment's rule, made the whole Odoo column
unreadable. The navigation was never the problem.

* **DRIVE A CONTROL THE WAY THE PRODUCT DRIVES IT, OR YOU ARE TESTING NOTHING.** Three probes of the
  app switcher failed on `has-text` and `get_by_role(name=…)` and told me only that my probe was
  wrong. `BrowserSession.act` resolves by `[data-ultracua-ref]`; driving `Home Menu` that way works
  first time and offers CRM / Calendar / Contacts / Dashboards / Invoicing / Settings.
* **THE WORD "CONFIGURED" WAS THE DEFECT.** It points at CRM -> Configuration -> Stages, and
  `Configuration` is **absent from the observation** on a deep-linked CRM action (80 elements, zero
  matches, `.o_menu_sections` 0). The answer was on the PIPELINE BOARD the whole time, two clicks
  away. Note the asymmetry that hid it: arriving via the app switcher DOES restore the menu bar, so
  the same app shows different affordances depending on how you got there.
* **AND THE FIRST REWORDING WAS STILL WRONG, WHICH ONLY THE LIVE RUN CAUGHT.** "How many stages does
  the board show" scored `data: 5` against `expected: 4` — and the agent was right to: the board has
  four TITLED groups plus one `o_column_quick_create` placeholder, which is column-shaped. **A
  control group must have ONE reading**, so it asks for the last stage's NAME, which the placeholder
  cannot be confused with. Measured: `data: "Won"` == `expected: "Won"`.
* **TWO RESIDUALS, RECORDED.** It passed at **21 actions against a 20-step ceiling** — no headroom,
  and an earlier wording took 13, so this row will sit near the ceiling and may flake. And its
  OUTCOME is `over_gated` like every Odoo read, so the control cannot be read from the verdict: the
  signal is `data` vs `expected`, and a reader checking only the outcome will think it still fails.
* **A CELL I WROTE IN THIS SLICE WAS DELETED IN IT.** A pairing assertion ("both control groups ask
  `how many`") was refuted by the live run, relaxed — and then PASSED against the mutation written
  to kill it. What remained was already enforced by `CorpusEntry.__post_init__`, so it could not
  fail. Removed rather than kept as decoration: this project's own rule is that a cell which cannot
  fail is worse than none.

## Three reps, and Gitea is baseline-ready while Odoo is not (B5, 0.133.0)

`benchmarks/corpus_aggregate.py` folds N passes into one record and names the rows that moved.
Three full reps of both substrates: **$4.97** ($1.81 Gitea, $3.16 Odoo), ~1.5 h.

* **A SINGLE-PASS ODOO BASELINE COULD HAVE BEEN ANYWHERE FROM 0.000 TO 0.400.** Per-rep
  availability came back `[0.0, 0.4, 0.143]` — mean **0.181, std 0.203**. The single pass taken
  earlier said 0.143, which is inside the range and tells you nothing about where the level is. That
  is the whole argument for reps, with a number on it.
* **GITEA IS TIGHT AND ALMOST READY**: mean **0.762, std 0.082**, `[0.714, 0.714, 0.857]`. Five of
  seven rows are rock solid — `gitea-comment` **3/3 `true`** (a real write, learned and replayed at
  0-LLM, every time) and four reads 3/3 `ok`. `gitea-start-timer` is **0/3 `not_authored`**, the
  same verdict every time: a STABLE, reproducible product limitation (R4.102), which is exactly what
  a baseline wants a known failure to look like. One row is genuinely unstable —
  `gitea-sort-list`, **1/3, and it gave THREE different outcomes** (`ok` / `refused` /
  `not_authored`) in three runs.
* **ODOO IS NOT READY, AND NOT MAINLY BECAUSE OF ITS RATE.** Two rows flip pass/fail
  (`odoo-open-record` 2/3, `odoo-search` 1/3) and **five more fail for DIFFERENT REASONS across
  reps**. A baseline over that would be reproducible in its number and unreadable in its diagnosis.
* **`unstable` CATCHES PASS/FAIL FLIPS AND MISSES REASON FLIPS — FOUND BY THE FIRST REAL SERIES.**
  `odoo-menu-nav` came back 0/3 having given `over_gated` in some passes and `refused` in others,
  and showed as *stable* because neither outcome is a pass. The information was already in the
  record and nothing pointed at it. `varies` is a SEPARATE list on purpose: a rate over such a row
  is reproducible, so a baseline is not endangered — the DIAGNOSIS is.
* **ALL FOUR ARMING MUTATIONS SURVIVED AT FIRST, AND THE REASON IS ALREADY WRITTEN DOWN HERE.** The
  cells did `from benchmarks.corpus_aggregate import fold`, which binds the original function
  object, so patching the module never reached them — S14's `from .providers import build_router`
  lesson, arrived at from the test side. Call through the module binding, or the mutation is
  attacking a function nobody runs.
* **AND ONE THING REPS FIX FOR FREE.** `variance.build_record`'s `per_rep` means one value per REP,
  and B3 had to hand it one per SCENARIO — which is why `pass_k` became `subset_all_pass` and why
  the gate uses a Wilson bound instead of `compare_records`. With three passes the values ARE reps,
  so `std` means what its name says for the first time.

## Gitea is baselined; Odoo waits on R4.27 (0.134.0)

`baselines/customer_v1_gitea.json` is the first customer baseline: three passes, `availability_rate`
**0.762 over n=21** scenario-observations. Diagnosing why Odoo is NOT in it cost **$0.30** and most
of it was free — the 21 records the series had already bought.

* **THE ODOO VARIANCE IS NOT THE AGENT WANDERING (R4.105).** `odoo-search` returned an IDENTICAL
  recipe in all three reps — 2 steps, 4 calls, `mutating_steps: 1`, source `wire` — and scored
  `over_gated` / `ok` / `over_gated`. The only field that differed was `mutation_gate_refused`.
  `odoo-open-record` says it from the other side: `mutating_steps: 5` -> `refused`,
  `mutating_steps: 0` -> `ok` twice. **The mechanism is `flow.py`'s own comment** — "every gated step
  loses self-heal and suffix-replan" — so a step R4.27 misfiled as a write cannot heal, and drift
  becomes a hard refusal. **7 of Odoo's 12 refusals (58%) are the mutation gate**; 4 are ordinary
  locator drift. A baseline written now is dominated by a filed defect and dies when it is fixed.
* **THE HARNESS EXPLANATION WAS TESTED AND REFUTED, which is why the extra runs were worth it.**
  The inter-phase reset was the obvious suspect (this file already records that an Odoo reset leaves
  a cold template). Measured: **3/6 with the reset, 5/6 without** — Fisher p ~ 0.55, and run 4 failed
  with no reset at all. Stopping at the first 3/3 would have shipped a confident wrong cause.
* **A BASELINE MUST PASS THE PASSES IT WAS BUILT FROM, AND THE FIRST DRAFT DID NOT (R4.106).**
  `_cost_findings` regresses at `baseline * 1.25`; three IDENTICAL Gitea passes cost $0.3502 /
  $0.8421 / $0.6167, an **82% spread**, because a failing learn spends its whole budget and which
  rows fail varies. A mean baseline failed rep 2. Only running the finished artifact against its own
  evidence could have caught that. `cost_usd` is the MAX observed now, with `cost_per_rep` carried so
  the mean stays recoverable — the expected cost and the alarm threshold are different questions.
* **THE PLAN-STATE GUARD FORCED AN HONEST NAME.** The artifact was first written as
  `baselines/customer_v1.json`, which is step 2.4's declared artifact — and 2.4 is `pending` because
  the Odoo half, the nightly and the honesty page do not exist. `test_no_unshipped_step_has_its
  _artifact_in_the_tree` fired. Renamed to `_gitea`, which is what it is.
* **A COIN-FLIP ROW IS RECORDED AS NOT PASSING.** `gitea-sort-list` gave three different outcomes in
  three passes; recording its tie as the quiet one would make `_flip_findings` report a flip on most
  future runs, and a channel that cries wolf gets ignored.

## The nine known mutants run again, and a CI job's list was never a list (0.6, 0.136.0)

reshape-plan step 0.6, held behind 1.5 since the order was written and fired at 0.110.0. Taken now
because 2.4's weekly run is specified as sharing this workflow, so the workflow has to exist first.

* **9 KILLED, 0 SURVIVED -- AND IT IS A RE-MEASUREMENT, NOT A RE-RUN.** The nine were applied by hand
  at 0.75.0, each in its own git worktree, all nine caught; the result is quoted in `CLAUDE.md`,
  `docs/correctness-survey.md` and `docs/reshape-plan.md`, and until now **nothing could reproduce
  it**. That is `prove_red`'s own rule one instrument out: a claim nobody re-measures is how a green
  instrument stays green while covering less. **SIX of the nine sites had MOVED** -- the R3.2 refusal
  migrated out of `flows.py` into `flow._learn`, which is what makes it cover `ultracua run` and the
  daemon rather than one caller of three; the row-identity check moved behind `resolve`'s single
  funnel; the promotion loop grew its attributed-but-failed arm. Each mutation states the PROPERTY,
  and a find-text that no longer matches is an ERROR -- which is what forces re-expression instead of
  nine quiet no-ops.
* **WHAT IT DOES NOT RE-MEASURE, stated because the 0.75.0 record is quoted three times:** *"four of
  the nine were caught by exactly ONE test"*. Each mutant here is scored by ONE killer FILE chosen for
  it, so this proves each is still killed and says nothing about by how many cells.
* **A CI JOB'S LIST WAS THE HOLE, TWICE IN ONE FILE (R4.107).** Six registries were six hand-typed
  `prove_red.py <registry> --tests ...` steps in `ci.yml`, so a seventh added under `tests/mutations/`
  and not there would never have run -- every job green, and nothing in the suite able to fail for it.
  And `tests/test_ci_provisioning.py` read `ci.yml` ALONE, which was an unstated assumption that there
  would only ever be one workflow file: the first second file is the one that installs a browser and
  runs on a schedule, i.e. precisely that file's subject, and it would have been entirely unscanned by
  the budget pin, the apt pin and the byte-identical-install pin. Both sets are DERIVED now, and both
  directions are asserted -- a registry named in a workflow `run:` fails, and a workflow file
  contributing no step fails.
* **THE TIER SPLIT CANNOT BE A DECLARATION.** `red-proof` installs no Playwright deliberately, and a
  killer-suite leg with a browser cell fails EVERY mutant's baseline (measured on CI: 8 failed / 135
  passed on both arms, green locally), which reads as a hole in the matrix rather than as a missing
  browser. So which side a registry lands on is read out of the TIER MANIFEST: browser-side if ANY id
  in ANY of its killer files is a browser test -- the conservative direction, because a killer
  DESELECTED by the fast tier would report its mutant as a survivor. An unknown killer file RAISES
  rather than defaulting: guess `browser` and it leaves the merge gate silently, guess `fast` and it
  launches in a job whose conftest raises.
* **THE ACKNOWLEDGEMENT ALREADY EXISTED, WHICH IS THE ONLY REASON THIS ALERT CAN STAY ON.** Before
  making anything alert, ask what the operator does when they cannot fix it -- a loud channel with no
  discharge gets `|| true`d and takes everything else dark (R3.9/CLI-1). Here a survivor goes into
  that registry's `KNOWN_SURVIVORS` with a reason and a finding id: a reviewed diff, not a silenced
  alarm, and `prove_red` fails in the OTHER direction the day it starts being killed again, so the
  list can only shrink.
* **THE GENERIC-OPERATOR HALF IS REFUSED ON MEASUREMENT, AND THE CHEAP DESIGN IS WRONG RATHER THAN
  MERELY EXPENSIVE (R4.108).** Sized by AST over the six hot files: **2848 mutants**, which is
  **70.4 h serial** against the fast tier -- the only killer broad enough -- against a 6 h job cap.
  The runtime is the lesser problem. Against a NARROW killer it is cheap (3.6 h) and actively
  misleading: the `if` family on `safety.py`, the best case in this repository, gives **6 survivors of
  16 (38%)** and **all six are killed by the fast tier**. Extrapolated that is on the order of a
  THOUSAND false holes, each wanting a hand-typed `KNOWN_SURVIVORS` reason. **The missing component is
  a COVERAGE-DERIVED killer selection** -- run only the tests that execute the mutated line -- not
  more mutants.
* **A SURVIVOR AGAINST A KILLER YOU CHOSE IS A STATEMENT ABOUT YOUR CHOICE.** `if slot_values:` ->
  False in `safety.idempotency_key` reads as a live inviolable-#3 defect: two rows of a parameterized
  write mint the SAME key, so a backend dedupe silently drops rows 2..N. It is fully guarded -- seven
  fast cells in `tests/test_slots.py` reach it -- and it was one edit from being filed as a finding.
* **A SCAN MATCHED ITS OWN PROSE FOR THE FIFTH TIME, and the fix is the one already written here.** A
  cell grepping the workflow TEXT for `--tier fast` went red on the comment explaining why the merge
  gate passes it. Stop scanning text, assert the property: `test_ci_provisioning.Step.run` already
  takes a YAML block scalar's body BY INDENT so a comment between two steps cannot leak in, and
  reusing it beat a sixth needle that would only have moved the collision.
* **AND THE ARMING HARNESS CAUGHT MY OWN MUTATION BEING WRONG.** The arming for that cell did
  `text.replace("--tier fast", ..., 1)` and hit the COMMENT four lines above the step -- so it added a
  registry name to prose, the cell correctly ignored it, and `assert_red` reported the cell as
  unguarded. The harness was right and the mutation was wrong. Anchor a mutation on enough context to
  name ONE site.

## The weekly bench runs, and "CI has no Docker" was never measured (2.4a, 0.137.0)

reshape-plan 2.4, SPLIT: 2.4a is the weekly run, the baseline gating and the honesty page, all
shipped; 2.4b is the Odoo half, blocked on R4.27 rather than deferred. Splitting beat holding the
whole step because everything except Odoo was ready.

* **"DOCKER IS PRESENT ON A DEVELOPER HOST AND ABSENT ON CI" WAS WRITTEN IN THREE PLACES AND IS
  FALSE (R4.109).** It was never measured. Traced to its source it rests on 0.121.0, where two cells
  reached `await_ready()` for real and failed both CI arms with `NOT WRITABLE` -- **a container that
  is not RUNNING**, which is trivially true of a job that never started one. **Measured on a
  GitHub-hosted ubuntu runner: Docker 28.0.4, Compose 2.38.2, Gitea up in 12.5 s, writable at
  12.6 s, seeded in 8.7 s.** So the weekly benchmark is an ordinary job and needs no self-hosted
  runner. Same ambiguity class as the CI `cancelled` that put a wrong prerequisite into
  `reshape-plan.md` §13 one day after §13 was written to prevent it -- and settled the same way,
  by measuring.
* **THE GUARD WAS RIGHT AND ITS REASON WAS WRONG, which is the part to carry.** The real asymmetry
  is one level in: this host runs a LIVE, SEEDED substrate between sessions and a CI job starts with
  nothing running, so a cell that reaches Docker still passes here against a real Gitea and still
  fails both CI arms. `tests/test_substrates.py`'s autouse fixture is UNCHANGED; only its message
  is. When you find a premise wrong, check whether the thing built on it is wrong too -- here it
  was not, and deleting the guard would have been the expensive mistake.
* **`--baseline` IS WHAT TURNS ON THREE OF THE FIVE GATE CHANNELS.** `gate_bench_record` runs cost,
  rate and flip only `if baseline is not None`, so a scheduled run that failed to find its baseline
  would print **GATE: PASS** having compared against nothing -- the absolute channels alone. The
  file is therefore loaded and VALIDATED before a single scenario is paid for, with four refusals:
  wrong substrate (the scenario sets are disjoint, so the flip channel finds nothing and the verdict
  is a confident pass over a comparison nobody made), wrong `kind` (a single pass carries n=7 and
  its Wilson bound reads as three times the evidence it is), and the corpus having grown or shrunk
  since the cut.
* **THE GATE'S TOLERANCE IS A MEASURED NUMBER, not a hope.** The baseline is mean 0.762 over n=21,
  whose Wilson 95% lower bound is **0.549** -- so a weekly pass must land **>= 4/7**, against 5/7,
  5/7, 6/7 observed. One failure beyond the known-bad row still passes; two do not. Check this the
  next time the corpus changes size, because the bound moves with `n` and nothing recomputes the
  sentence.
* **A BENCHMARK THAT QUIETLY DOES NOT RUN IS WORSE THAN ONE THAT IS RED.** With no
  `ANTHROPIC_API_KEY` secret the job REFUSES, naming the remedy, rather than skipping -- the first
  reads as "we are measuring" and the second as "fix me". That is only legitimate because the remedy
  is one action by one person; an alarm nobody can discharge gets `|| true`d (R3.9/CLI-1).
* **MONEY IS NEVER SPENT BY ACCIDENT.** `run_bench` defaults to FALSE, so dispatching the workflow
  to re-run the mutation sweep does not also buy a benchmark pass. The free `substrates` preflight
  runs on every PR touching the bench, and `customer-bench` `needs:` it -- a dead container layer
  costs nothing instead of a wasted pass, which is R4.99's phase ordering one level out.
* **THE HONESTY PAGE IS CHECKED IN BOTH DIRECTIONS.** Every id in its open-findings block must still
  be OPEN (a fixed one means the caveat is stale and its number may have moved), and every OPEN
  finding cited anywhere in the honesty region must be declared (a caveat living only in prose is
  one nothing can fail for). What it deliberately does NOT assert is that all 49 open findings
  appear -- that would be noise, and noise is how a channel stops being read. Its arming DERIVES the
  finding it smuggles in rather than naming one: the first draft hard-coded R4.103, which was open
  when I picked it and `fixed` by the time the cell ran.
* **AND THE FIRST REAL WEEKLY RUN FAILED, ON R4.109's OWN ASYMMETRY ONE LAYER DOWN (R4.110, 0.138.0).** `needs:` is ORDERING, NOT A SHARED MACHINE -- every job gets a fresh runner -- and `score_one` calls `reset()`/`await_ready()` but never `up()`, because on a developer host the substrate is already running. All seven scenarios raised `no seed at /data/gitea/seed.db`. A second gap sat behind it: `seed()` does not take the snapshot `reset()` restores from, and nothing in `benchmarks/` ever called `snapshot()`. **It cost $0.00 because three guards worked** -- the seed precondition named its remedy, every row was attributed to the HARNESS family rather than scored against the product, and `build_bench_record` refused to publish over an all-unscored corpus instead of rendering 0.0. **The lesson is not the fix, it is that I wrote the correction and then built a job that violated it in the same slice**: when you correct a premise, grep for what else assumes it.
* **A `      - ` LINE IS NOT A STEP UNLESS IT IS UNDER `steps:`.** Adding a `pull_request: paths:`
  filter fired `parse_steps`' totality assert on `- 'benchmarks/**'` -- correctly, by its own rule.
  Widening `_STEP_KEYS` would have been wrong twice: it would have admitted a non-step AND blunted
  the assert that caught it. Note `_JOB_START` matches the top-level `on:` key too, so tracking
  `steps:` is what keeps a trigger block out of a job.

## D6 was refuted by the measurement it demanded of itself (R4.111, 0.139.0)

Correctness-plan D6 wanted to route a wire-promoted step to the PRECISE form-scope gate instead of
the whole-page one, and made itself conditional on measuring the action types FIRST. It cost
**$0.4634 across four Odoo learns** and the answer was: do not build it.

* **TEN WIRE-PROMOTED STEPS, AND THE FIX ADDRESSES NEITHER HALF.** Six `navigate` -- no locator, no
  scope, so the precise gate is STRUCTURALLY unreachable, there is no element to scope. Four `click`
  -- scope AND locator, so they take the precise gate **already**, and refused anyway.
* **BOTH BRANCHES REFUSE, FOR DIFFERENT REASONS, and that is the whole refutation.** `odoo-menu-nav`
  said `mutation gate: page drift` (the fallback); `odoo-sort-list` said `mutation gate: target
  missing/ambiguous` -- the PRECISE branch's own first failure mode. That one is
  `resolve(..., unique=True)` failing to bind uniquely on a generated DOM: a LOCATOR problem, not a
  scope problem. And `unique=True` is a deliberate write-safety choice, correct for anything the
  system believes is a write.
* **SO THE GATE IS RIGHT AT EVERY BRANCH AND THE DEFECT IS UPSTREAM.** R4.27 marks an Odoo read as a
  write; the write-safety machinery then correctly refuses; an ordinary read becomes a hard refusal.
  Narrowing the gate would weaken write safety for steps believed to be writes -- what D0 was
  blocked for.
* **ONE RUN WOULD HAVE SHIPPED THE WRONG FIX.** From the first scenario alone the conclusion was
  "the precise gate is structurally unreachable for the failing steps" -- true of navigations and
  FALSE of 40% of the population. The second scenario produced no gated step at all. The plan
  budgeted ~$0.10 for ONE run. **When a measurement's first sample gives a clean story, that is the
  moment to buy the second one**, not the moment to write it up.
* **WHAT IT IS NOT: a claim the product gets Odoo WRONG.** No Odoo scenario in any run produced
  `wrong_data`, and `mode="auto"` falls through to a re-author, so the answer still arrives. What is
  lost is the 0-LLM deterministic replay -- the central claim -- so on this class of app the product
  degrades to an ordinary LLM agent. And it is a CLASS: `is_write_request` keys on the METHOD, so it
  is every app serving reads over POST. R4.27's original 12/12 was GraphQL controls, not Odoo.
* **A SIGNAL, NOT YET A FINDING**: `odoo-sort-list`'s gate refusal is ITSELF a locator failure, so
  R4.27 may be partly masking a second independent Odoo problem and fixing it alone might not
  unblock 2.4b. One refusal message is not a measurement.
* **THE INSTRUMENT SHIPS WITH THE REFUTATION** (`benchmarks/gate_probe.py`), because a conclusion of
  the form "do not change `src/`" is worth exactly what its reproducibility is worth, and the next
  person to doubt it should re-derive it in a minute rather than re-buy four learns.
  `tests/test_gate_probe.py` holds the probe's branch against the engine's own source, so a probe
  describing a gate the code no longer has fails there rather than answering confidently and wrongly.

## Reads over POST: eleven approaches surveyed, six dead (R4.27, 0.140.0)

`docs/reads-over-post.md` is the write-up; D7 in the correctness plan is the entry. Read it before
proposing anything here — it exists so the next person does not re-derive nine approaches or
re-propose one of the six that are dead.

* **THE LEAD IS BODY EVIDENCE, and the reason it survives is precise.** A JSON-RPC read-method
  allowlist plus a route-EXACT read-route list, failing closed. The recorded denylist refusal reads
  "a GraphQL MUTATION travels the same URL" -- an Odoo `create` cannot travel under the name
  `search_read`, because the method name IS the operation. Allowlist of reads, unknowns loud. The
  request body is the one sensor class D5 has not spent, and `dryrun.py` already reads `post_data`,
  so it costs no plumbing.
* **THREE APPROACHES DIED IN THE ADVERSARIAL PASS, not in the history**, and each was plausible
  until its failure direction was walked. Two-tier marking reaches **0 of 10** measured steps
  because the refusals are GATE refusals, which return pre-act, and the heal paths are themselves
  `is_write_request` consumers -- restoring recovery leaves it poisoned. The navigate gate is
  **vacuous on Odoo**, where every backend page is origin+path `/web` with all state in the hash it
  would have to tolerate. Response evidence is measured barren (200 for reads, writes AND errors)
  and actively wrong: `web_save` returns the saved record read back.
* **THE ASSUMPTION EVERYTHING RESTS ON IS MEASURED BY NOTHING** -- that fixing the marking improves
  Odoo at all. R4.111's tail is the counter-signal, and the READ path also resolves `unique=True`,
  so demotion may only convert "gate refused" into "healed and still failed". **Six free
  measurements settle it and #1 decides the ranking**: flip the cached marks to `mutating:false` and
  replay 0-LLM. Buy that before any `src/` change.
* **THE HAZARD A DEMOTION FIX CARRIES, which no dossier had priced**: demotion does not merely lose
  a mark, it RE-ARMS verify-by-replay -- a full second browser pass at learn. A wrongly-demoted
  write is double-fired before anything replays. Any such fix needs that as a named write-safety
  cell, not an assumption.
* **AND A FAMILY NOBODY EVALUATED**: a replay-time wire arbiter. Every approach classifies at learn
  and acts on a static mark; the dry-run machinery already proves requests can be held pre-send with
  the body in hand. An escorted believed-write sidesteps D6's "weaken the gate" objection entirely.
  Recorded as the successor design to evaluate, not as a proposal.
* **PROSE OUTLIVES ITS FIX, and three pieces had (R4.113).** `_auth_retry_allowed` sent the operator
  to `flow inspect` to tell them the provenance "is not recorded" -- while `cli.py` prints it three
  lines away, and has since 0.92.0. `run_all` said visibility was the remedy "until a mark can say
  WHY it was set"; it can. And CLAUDE.md's own pointer to the parked attribution branch named a path
  that is not on `main`. When a slice makes a sentence false, grep for the sentence.

## The Odoo blocker is the LOCATOR, not the marking (R4.114, 0.141.0)

`docs/reads-over-post.md` surveyed eleven approaches to R4.27 and made itself conditional on one
measurement: does demoting the wire marks actually make an Odoo replay green? **It does not.** The
survey's own ranking is superseded by its own gate, which is what the gate was for.

* **THE A/B, AND THE ONE FIELD THAT SETTLES IT.** Same recipe, same substrate, differing only in the
  mark. Control: step 1 `click`, `mutating: True`, `gate: drift`, `gate_bound_by: none`, "mutation
  gate: target missing/ambiguous". Demoted: same step, no mark, no gate, `bound_by: none`, "locator
  unresolved or ambiguous (drift)". **`bound_by: none` in BOTH arms** -- the locator never resolved
  either way, and the gate was reporting a locator failure it reached first. Demotion changes the
  MESSAGE, not the outcome.
* **RUN TWICE, BECAUSE ONE SAMPLE HAD A CONFOUND.** The first used day-old recipes, which conflates
  "the marking is not the blocker" with "these recipes went stale". The second used a recipe learned
  minutes earlier against the same seed and said the same thing. That is R4.111's rule -- when the
  first sample gives a clean story, buy the second -- applied to a story I had already written down.
* **THE CENSUS IS THE OTHER HALF, and it was free.** Of 18 non-passing Odoo replay rows across the
  three-rep series: 6 never produced a recipe at all (a DISCOVERY failure), 6 gate page/form drift,
  **4 locator failures with no gate involved**, 1 gate target-missing (itself a locator failure), 1
  data-not-found. The locator problem is visible without any mark in the picture.
* **SO R4.27 IS A REAL DEFECT AND NOT THE ODOO BLOCKER.** Fixing it stops reads being filed as
  writes, which is worth doing on its own terms; the measured availability gain is about zero,
  because the steps it un-gates then fail on the locator. **Do not cost 2.4b as a marking fix.**
* **WHAT IT COST TO LEARN: $0.0998** -- one fresh learn, with both replays and the whole census free
  -- against a slice that would have touched the write rail. `benchmarks/mark_flip_probe.py` ships
  with the finding.
* **THE HARNESS AGREED WITH ITSELF THREE TIMES BEFORE IT WAS RIGHT, and each time for a reason that
  had nothing to do with the mark**: a `TypeError` from calling `replay()` with a parameter it does
  not have, a `StaleApprovalError` from a hand-written approval sidecar with no `steps_hash`, and a
  cache MISS because the bench flow's key carries the idempotency proxy's ephemeral port. **Two
  identical arms are not a null result** -- they are the harness failing in front of you. The probe
  now asserts the key hits, approves through the product's own verb, and refuses a recipe with
  nothing to flip.
* **AND THE ANSWER TO "IS REPLAY 0-LLM" IS NOT UNIFORM**: `flows.replay` builds an extraction router
  whenever `spec.extract` is set and the read is not pinned, so a read flow that extracts a datum
  pays one LLM call per replay. That is why `odoo-search` reported `llm_calls: 1`. Writes and
  navigate-only reads are genuinely 0-LLM; extracting reads are not, unless pinned.

## The Odoo blocker is a READINESS RACE, and the obvious fix is refuted (R4.115, 0.142.0)

R4.114 said the blocker was the LOCATOR. It is not, and that finding's own sensor could not have
seen it -- an A/B of two arms at ONE instant never asks what was on the page. The whole diagnosis
cost **$0.0099**.

* **THE LOCATOR IS FINE.** On a RENDERED page the failing spec binds uniquely on the FIRST
  candidate -- census over the live page, `exact-text` n=1, substring n=1, css n=1, and the real
  `resolve(unique=True)` returns BOUND with `bound_by: exact-text`. Tier 3 is doubly unavailable
  (`role='span'` is not a `KNOWN_ROLE`; `len(anchor)==60 == _ANCHOR_MAX`) and it does not matter.
* **WHAT THE REPLAY IS LOOKING AT**, captured by wrapping `resolve` during a real failing replay:
  `thead th` **0**, `body_chars` **3**. `goto` waits for `domcontentloaded` and `flow.py` resolves
  immediately. Blank at 0.31 s, BOUND at 0.70 s.
* **THREE SITES read page state with no settle**: the locator resolve, the gate's
  `scope_fingerprint`, and the gate's whole-page `session.snapshot()`. The middle one is the
  prettiest evidence -- at a real gate call, `t+0ms` returns the PREVIOUS step's recorded scope and
  `t+100ms` returns exactly this step's. **The gate is not seeing drift, it is seeing the past.**
* **THE SUBSTRATE CONTRAST IS TOTAL**: Gitea 7/7 complete at `domcontentloaded`, ONE whole-page
  fingerprint each; Odoo **0/7**, every scenario **5 elements and 1 character**, 3-4 fingerprints,
  settling 0.62-1.08 s. The 5 is R4.93's separately-measured skeleton.
* **IT IS NOT LLM LATENCY, and getting that wrong is easy.** The author loop is
  `snapshot() -> decide() -> act()`, so the learn's FIRST observation is taken at the same instant
  as the replay's first resolve and sees the same skeleton. What saves the learn is that it
  **RE-OBSERVES every turn**; the replay resolves once and `mode="replay"` nulls the provider, so
  `_maybe_heal` returns immediately and one miss is terminal. R4.40 is the LEARN twin and is OPEN --
  and its guard (`assert_not_a_skeleton`) lives in `benchmarks/`, guarding the learn's start page,
  with nothing guarding the replay anywhere. A guard on a sibling path, never applied.
* **THE OBVIOUS REMEDY IS REFUTED, BY THIS REPOSITORY'S SIGNATURE FAULT.** "Retry `resolve` while it
  returns None" is keyed on an OVERLOADED SENSOR: `None` means five things and FOUR are deliberate
  safety refusals. A poll re-drives them -- for a refusal keyed on a COMPETING candidate, it waits
  for the competitor to go away. **Measured**: two per-row `Cancel` links, recorded row hidden at
  t=400ms, today refuses LOUD and the polled arm BINDS `/cancel/30` at 0.41 s via `role+name`, a
  Tier-1 confident candidate nothing cross-checks, where `/cancel/3` was recorded. That is D5's
  overloaded-`None` one level up from `anchor_id`. **A fix needs a SENSOR that separates "not
  rendered" from "found it and refused"**; the return value cannot express it.
* **BLAST RADIUS IS SUGGESTIVE, NOT PROVEN.** R4.105's census counts 4 locator + 7 whole-page
  refusals of 12, which are two of the three sites -- but exactly ONE scenario was driven end to
  end. `odoo-menu-nav` is neither evidence nor counter-evidence: its recipe carries the proxy's dead
  ephemeral port in every `navigate` step's `text`, and repairing the port invalidates
  `precond_fingerprint`, which is `xxh64(url + basis)`.

## Two ways a benchmark run reports GREEN while measuring nothing (R4.116, 0.142.0)

Both were caught in the same hour, both had already been published in a written-up result, and
neither is exotic. **When a measurement agrees with your hypothesis, check what its numbers mean
before checking anything else.**

* **`RunRecord.llm_calls` IS THE DECIDE COUNTER AND CANNOT SEE AN EXTRACTION CALL.** Its own comment
  says so ("API calls are `usage["calls"]` -- they differ"). An unpinned read runs `extract()` inside
  the finalize closure. A replay reporting `llm_calls: 0` had really spent `{'calls': 1, 'cost_usd':
  0.00989, 'claude-opus-5': 1}`. **Read `usage["calls"]`**, which is what `scored_run` already does,
  and never quote `llm_calls` as evidence of the 0-LLM claim.
* **`odoo-sort-list` CANNOT DETECT WHETHER ITS OWN TASK WAS DONE.** Its recipe clicks the same header
  THREE times and the header is a two-state toggle, so a faithful replay ends ASCENDING. Measured at
  the extraction boundary: 17 rows, top `Balmer Inc: Potential Distributor`, `Need 20 Desks` **last**
  -- and the run reported `RESULT == EXPECTED`. `flows.py` hands the extractor the whole page body
  with the goal as its prompt, so it ranks the rows itself and is indifferent to sort order; the
  oracle is a SQL `max()` over the ANSWER STRING and never looks at the page. Blind at learn time
  (which is how a three-click recipe got cached) and at replay time. **Do not use this row as
  proof-of-green for any intervention** -- it measures the extractor.

## Neither Odoo fix works alone; the COMPOSITION does (R4.117, 0.143.0)

Every experiment before this moved ONE lever, and each failed for its own finding's reason -- which
is why "Odoo is blocked" kept surviving. The 2x2 nobody had run, on a FRESHLY LEARNED
`odoo-sort-list` recipe (`readiness_probe --compose`, ~$0.05):

| | readiness OFF | readiness ON |
|---|---|---|
| **wire marks kept** | step 0 `navigate`, gate drift | step 0 `navigate`, gate drift |
| **marks demoted** | step 2 `click`, `bound_by: none` | **COMPLETED, every step bound** |

* **THE TWO BLOCKERS ARE INDEPENDENT AND SIT ON THE SAME STEP SET.** R4.27's over-marking gates the
  `navigate` on a whole-page fingerprint the render race makes unreproducible; R4.115's race breaks
  the `click`s. Remove either alone and the other remains -- the off-diagonal cells reproduce
  R4.114 and the blast-radius result exactly.
* **IT IS EVIDENCE ABOUT THE GENERAL CASE, not about Odoo.** Both blockers are classes:
  reads-over-POST is every GraphQL/JSON-RPC app (R4.27's original 12/12 was GraphQL), and the
  readiness race is every client-rendered SPA (Gitea 7/7 complete at `domcontentloaded`, Odoo 0/7).
  So the product is not structurally confined to server-rendered apps.
* **WHAT IT IS NOT.** n=1. The demotion is a PROBE of the counterfactual -- a real fix ADDS a
  provenance mark rather than stripping `MARK_WIRE`, or R4.27 goes invisible (`safety.py` says so).
  The readiness half is a CEILING whose naive form is REFUTED and must clear D5. And per R4.116 the
  completed cell's `RESULT == EXPECTED` does not evidence the sort happened -- the claim is only
  that every recorded step bound and executed.
* **READINESS ALONE: 0 of 3 fresh recipes** (R4.115's blast radius, $1.07). Its ceiling reproduces
  on the two recipes it was measured against and on no freshly learned one, because those recipes
  are themselves degraded -- which is the next finding.

## The learn thrashes on navigation and caches the thrash as a success (R4.118, 0.143.0)

Of five bought Odoo learns: **one** produced a recipe that acts; **two** produced recipes that are
**100% `navigate` steps and nothing else** (20 of 20, and 8 of 8); **two** cached nothing after 21
actions at the ceiling. Four of five degraded.

* **THE SHAPE IS DIAGNOSTIC.** `odoo-filter-status` re-navigates to the SAME url **12 times**, with
  intents repeating almost verbatim ("Open CRM pipeline list view", five times). That is an agent
  that cannot tell whether the page arrived -- R4.40 and R4.115's race, on the LEARN side.
* **AND THE URLS IT INVENTS ARE MALFORMED**: `#action=&model=crm.lead&view_type=list`, an EMPTY
  `action=`, four times in one recipe. Odoo serves the default app, so both recipes replay **every
  step `ok`** and end on the Discuss inbox; the run then fails at the extraction. A loud failure
  attributed to the wrong phase -- no step reported a problem.
* **NEITHER REPLAY-SIDE FIX TOUCHES IT.** Both degenerate recipes fail identically in ALL FOUR
  cells of R4.117's 2x2, because the defect is upstream of everything replay does.
* **DETECTABLE FROM THE ARTIFACT ALONE** -- `readiness_probe --recipes DIR`, no substrate, no
  browser, no key. That is the cheapest place to catch it and where the detection now lives.
* **NOT FIXED HERE**, deliberately: a refusal that fires on a legitimate navigate-only flow is D0's
  over-refusal shape wearing a learn hat, and the threshold needs measuring on a population that
  includes real navigation flows first.

## The learn's stuck detector is disarmed by the race (R4.119, 0.144.0)

R4.118's thrash is not a missing guard. `flow.py`'s author loop already bails on no progress --
`changed = state_changed(obs, after)` right after the act, `no_progress = 0 if (ok and changed) else
no_progress + 1`, break at `stuck_limit` (4). And `state_changed` is
`before.url != after.url or before.fingerprint != after.fingerprint`: it asks only whether the page
DIFFERS.

* **MEASURED, WITH A CONTROL.** The SAME Odoo url navigated five times, snapshotting exactly where
  the loop does: fingerprints `3b1d7446 / 76d1fae2 / 76d1fae2 / d3de4936 / d3de4936` at 5-6
  elements, and `no_progress` never exceeded **1** against a limit of 4. Repeat it with a 2.5 s
  settle and all three reads are `7660234d` at 80 elements, `changed` correctly False, `no_progress`
  climbing 0 -> 1 -> 2. **The sensor is fine; what it is fed is not.**
* **SO THE REMEDY IS NOT A NEW GUARD.** Feed the existing one a settled observation. Nothing new
  refuses anything, so it does not go near D0 -- and it is the same readiness predicate R4.115 needs
  on the replay side, so one sensor serves both halves.
* **THIRD INSTANCE OF ONE FAULT CLASS.** `state_changed` (differs / not-yet-rendered),
  `resolve() -> None` (absent / found-and-refused), `anchor_id=None` (no token / wrong container).
  All three conflate "nothing there" with something else; D5's "change the SENSOR CLASS" is their
  general form.
* **WHAT A LEARN SAW IS NOW ON THE RECIPE.** `CachedStep.precond_elements` records the element count
  of the observation each step's `precond_fingerprint` came from, so "was this authored against an
  unrendered page" is an ARTIFACT question (`readiness_probe --recipes`, no substrate/browser/key)
  against R4.93's measured Odoo skeleton of 5. It is UNHASHED and defaulted -- the approval digest is
  byte-identical, pinned by a cell, so no fleet is re-approval-gated -- and an older recipe reports
  **unrecorded**, never zero. Do not collapse those: "the learn saw nothing" and "we did not record
  what the learn saw" are different facts, and collapsing them is this register's most-repeated
  defect.

## The readiness predicate is measured, not argued (R4.120, 0.145.0)

R4.115 refuted the naive remedy and left one question: if a retry on the RETURN VALUE cannot work,
what CAN tell "not rendered yet" from "found it and refused"? `readiness_probe --settle` scores
candidates against a ground truth -- the first moment after which the interactable count never
changes again. **60 page-reps** (15 pages x 4: 7 Gitea, 7 Odoo, one static control), **$0.00**:

| candidate | premature | pages hit | median late |
|---|---|---|---|
| `dcl (today)` | **28** | 7 | -- |
| `ready-state` | 28 | 7 | 235 ms |
| `els-stable` | **17** | 6 | 296 ms |
| **`mut-quiet-200`** | **0** | 0 | **274 ms** |
| `mut-quiet-500` | 0 | 0 | 563 ms |

* **PREMATURITY IS A DEFECT; LATENESS IS A COST.** They are never summed, and a premature firing
  gets no lateness recorded at all -- averaging one in makes the worst candidate look like the best.
* **`els-stable`'s 17 are the PLATEAU flaw made numeric.** Two equal counts 100 ms apart can both
  land inside a pause mid-render. That is exactly why a rewritten blast harness disagreed with a
  validated one and cost an afternoon; the reproduction is now a browser-free cell.
* **`networkidle` is absent because it is separately refuted** -- it never fires on Odoo at all.
* **THE COST IS LOPSIDED, and that decides the design.** Gitea 7/7 and the static control measure
  `true_ready = 0` in EVERY rep, so a wait there is pure tax; Odoo's 468-859 ms is real work. So
  **replay waits CONDITIONALLY** -- and that is D5's sensor-class change: `None` + not-quiet means
  not rendered (wait, retry), `None` + quiet means absent or REFUSED (fail loud, all four safety
  refusals intact), and nothing is paid on the happy path. **Learn waits unconditionally**, where
  274 ms is noise beside an LLM call and is what arms R4.119's stuck detector.
* **REPS ARE NOT OPTIONAL AND THIS NEARLY SHIPPED WITHOUT THEM.** A single pass put
  `odoo-filter-status` at 968 ms where four reps put it at 656-719 -- enough to flip
  `mut-quiet-200` from clean to premature and hand the verdict to `mut-quiet-500`.
* **TWO INSTRUMENT BUGS, FOUND BY DISBELIEVING THE OUTPUT, and the second is the one to carry.**
  The ground truth first took the LAST sample that still DIFFERED (one interval early, and 0 on a
  sparse series). Fixing that, it then reported `samples[0]["t"]` when NOTHING ever changed -- the
  probe's own startup latency -- which said Gitea settled at 219-266 ms and a STATIC FIXTURE at
  16-94 ms. It was one commit from shipping as a "correction" to the TRUE claim that Gitea is
  complete at `domcontentloaded`. **A measurement that indicts a control you built to be trivial is
  measuring itself.**
* **RESIDUAL**: a page with a persistent animation or ticker would never go mutation-quiet. None
  exists in this corpus, so it is unmeasured and the predicate needs a CAP -- ~2 s covers everything
  here (max observed firing 1485 ms), and with a cap it degrades to today's behaviour, just later.

## The learn settles before it looks (R4.40 + R4.119 FIXED, 0.146.0)

The first of the three legs 2.4b waits on, and the smallest, because the guard already existed.
`BrowserSession.await_settled()` resolves once the DOM has been mutation-free for
`settings.settle_quiet_ms` (200), capped at `settle_cap_ms` (2000). Every mutation RESTARTS the quiet
timer, so it means "the page stopped changing" and not "some time passed".

* **BOTH OBSERVATION SITES, and the second is the one that matters.** `_author_steps` settles before
  the loop-head snapshot, so the agent decides from the page rather than its skeleton; and before
  the VERIFY snapshot, which is what makes `changed` mean "my action did something" instead of "the
  render advanced".
* **MEASURED BEFORE AND AFTER on real Odoo**, six navigations to the same url: without the settle the
  observation is 5-6 elements and `no_progress` ends at **0** -- it reaches 3 and then RESETS when a
  stray fingerprint change reads as progress, which is exactly how a learn burns its budget. With it
  the observation is **80 elements every time** and `no_progress` climbs to **4**, the `stuck_limit`.
* **200 ms IS MEASURED, NOT CHOSEN** (R4.120): the cheapest predicate never premature over 60
  page-reps, where acting at `domcontentloaded` was premature 28 times and "two equal element counts
  100 ms apart" 17.
* **FAIL-OPEN BY CONSTRUCTION.** `evaluate` throwing mid-navigation returns `unavailable`; the cap
  gives up and proceeds. Both are the behaviour before this existed, so the mechanism's worst case is
  "no better than before" -- a settle that turned a transient into a failed step would be a
  regression bought with a diagnostic.
* **REPLAY IS DELIBERATELY UNTOUCHED, and a test pins the call sites at exactly two.** Its cost
  profile is the opposite: a server-rendered page measures `true_ready = 0` in EVERY rep, so an
  unconditional wait there is pure tax on the 0-LLM speed claim. Replay gets the CONDITIONAL
  two-state sensor (R4.115) and lands separately.
* **`drift_bench` RE-RUN because this adds a MutationObserver that runs IN THE PAGE** -- the rule
  this file already states. Invariants ALL HOLD, `silent_wrong` 2 (the published allowlist row),
  writes double=0 suppressed=0 wrong_target=0, recovery refused on 14/14 drifted write rows.
* **R4.118 STAYS OPEN ON PURPOSE.** Both of its causes are closed at the source, but the OUTCOME it
  is about -- that a fresh Odoo learn stops producing 100%-`navigate` recipes -- costs paid learns
  and is unmeasured. Mechanism evidence does not entail it, and marking it fixed on mechanism alone
  is the "green is not evidence" failure this register keeps re-filing.

## The settle is validated on the OUTCOME, and the residual is the bail's wreckage (R4.118 FIXED, R4.121, 0.147.0)

R4.40 and R4.119 were fixed at the MECHANISM. R4.118 was about the outcome -- that a fresh Odoo learn
stops caching 100%-`navigate` recipes -- and mechanism evidence does not entail it. Three fresh
learns, **$0.2225**:

| scenario | | steps | nav% | re-nav | malformed | els seen |
|---|---|---|---|---|---|---|
| `odoo-filter-status` | before | 20 | 100% | 12 | 4 | 5-6 |
| | **after** | **2** | **0%** | 0 | 0 | **80** |
| `odoo-open-record` | before | 8 | 100% | 3 | 2 | 5-6 |
| | **after** | **4** | **0%** | 0 | 0 | **80** |
| `odoo-sort-list` (control) | before | 5 | 20% | 0 | 0 | 5-6 |
| | after | 2 | 0% | 0 | 0 | **80** |

* **2 degenerate -> healthy, 0 regressions**, and the CONTROL stayed healthy -- which is the half
  that matters, because a "fix" that made every learn bail early would clean the degenerate pair by
  breaking the working one, and the shape census alone would call that a win.
* **`precond_elements` IS THE DIRECT EVIDENCE** (shipped for exactly this): every step of all three
  recipes was authored at **80 elements** where before they were authored at 5-6.
* **THE LEARNS GOT CHEAPER**: `odoo-filter-status` $0.2472 -> $0.0645, because it stopped spending
  its budget re-navigating.
* **ONE UNEXPECTED IMPROVEMENT**: `odoo-sort-list` now caches TWO clicks (ascending then descending)
  where it cached THREE and ended ASCENDING -- R4.116's "the recipe does not accomplish its own
  goal", fixed as a side effect of the agent being able to see the page it was clicking.
* **THE RESIDUAL IS R4.121, AND IT IS THE BAIL WORKING.** `odoo-open-record` cached 4 `scroll` steps
  and nothing else -- exactly `stuck_limit`. It stopped where it used to burn its whole budget, and
  the artifact is still a recipe that replays four pointless scrolls. `_author_steps` appends on
  `if ok:` and breaks separately on `no_progress`, so **a bailed learn keeps every step that
  succeeded on its own terms**, and `found=True` masks it because the extractor read the answer out
  of the page text anyway.
* **A HYPOTHESIS WAS REFUTED ON THE WAY and it is worth keeping.** The suspicion was that scroll
  defeats the bail the way navigation did, via R4.102's viewport-bounded snapshot changing the
  element set. It does not: `mouse.wheel` leaves `window.scrollY` at **0** on this page, because
  Odoo keeps `docH == vpH` and scrolls an INNER container, so the fingerprint is stable, `changed`
  is correctly False and `no_progress` climbs to 5 in a control. The bail is not fooled by scroll --
  it fires, and the recipe keeps the wreckage. The reason the agent scrolls fruitlessly at all is
  R4.102, still open and untouched.

## The replay's readiness sensor is the RESOLVER's, not the page's (R4.115 FIXED, 0.148.0)

R4.115 refuted the obvious remedy and left a requirement: separate "the page has not rendered" from
"resolve found a candidate and refused", given that the RETURN VALUE structurally cannot. The answer
was not a better page probe -- it was already inside the resolver.

* **`sink["saw_candidates"]`**, set by `_resolve` from its own candidate walk. FALSE means nothing in
  the page answered to the recorded spec; TRUE means the page answered and the resolver DECLINED --
  ambiguity under `unique=True`, the Tier-2 conflict, an identity contradiction, or the row guard.
  `flow._retry_if_unpainted` retries only the first, once, after `await_settled()`.
* **THE FOUR SAFETY REFUSALS FAIL LOUD AND IMMEDIATELY, with no wait at all**, and that is the
  point: waiting is exactly what let the competing row disappear in R4.115's measured wrong-record
  bind. That counterexample is rebuilt as a test and still refuses.
* **COUNTED ON `count() > 0`, BEFORE THE VISIBILITY TEST.** The conservative side: a match that
  exists but is HIDDEN reads as "the page answered", so no retry -- and hiding the recorded row is
  how the bind was manufactured in the first place.
* **THE LEAK THAT NEARLY SHIPPED.** `resolve`'s row-containment guard refuses ABOVE `_resolve`,
  after a real element bound uniquely in the WRONG record, so it never reached the branch that sets
  the flag. Unset reads as falsy, which would have made it the ONE refusal a retry could walk
  through. It sets `saw_candidates = True` explicitly now, with its own cell.
* **BOTH replay resolve sites**, the step's and the mutation gate's, because R4.117's composition
  needs the gate too. `test_write_safety_invariants.py` gains a DIMENSION over every refusal shape
  the gate can produce, on a page that keeps mutating for a second afterwards -- so a retry keyed on
  the return value would have time to find something, and only the sensor stops it.
* **`drift_bench`: invariants ALL HOLD and every number is IDENTICAL to the pre-change run** -- same
  survival curve, same `bound_by` histogram, writes double=0 suppressed=0 wrong_target=0, recovery
  refused 14/14. That is what a change which only fires on unpainted pages should look like against
  static fixtures.
* **A GUARD I WROTE LAST SLICE FIRED ON ME, correctly.** `test_replay_is_deliberately_untouched`
  pinned the settle call sites at exactly two and said a third would have to be argued. It was, and
  the cell now pins the SHAPE instead: two unconditional on the learn, one inside the guarded helper.
  A fourth still fails.
* **THE LIMIT, STATED.** Mutation-quiet cannot tell "finished rendering" from "has not started", so a
  page idle when asked and painting later gets one retry and no more -- the same loud failure as
  before, never a wrong bind. An earlier draft of that cell used an inert-then-inject fixture, which
  is not the measured shape (Odoo mutates continuously while booting) and made the mechanism look
  broken when it was the fixture.

## D7's offline gate: the number is PER-STEP, not per-POST (R4.122, 0.149.0)

`docs/reads-over-post.md`'s lead candidate attaches seven conditions and the first is "measure the
demoted population offline first". `benchmarks/read_post_census.py` is that measurement -- **$0.00**,
no LLM, no learn -- and it changes the shape of the answer.

* **PER-POST 13 of 22 (59%). PER-STEP 2 of 2 (100%).** Per-step is the question: `_author_steps`
  marks a step when a write-classified request fires inside its ACT WINDOW, so a step is demoted
  only when EVERY post in that window is, and one un-cleared background poll re-marks it. Measured
  by loading, settling, then recording ONLY what an interaction caused -- each sort click produces
  exactly one `web_search_read`.
* **THE RESIDUAL IS PAGE-LOAD CHROME.** Odoo's `/mail/*` bus routes carry no ORM method, so a body
  classifier structurally cannot clear them -- and they were measured NOT to land in act windows,
  which is precisely why the per-POST figure understates the fix.
* **THE NEGATIVE CONTROL HOLDS**: real `create` and `web_save`, issued over the page's own session so
  route and body are what the watcher sees, both stay writes. Its first version drove the UI,
  captured NO write, and reported that it proved nothing rather than passing.
* **GITEA MAKES ZERO POSTs** on every corpus read page. A server-rendered substrate is untouched by
  construction.
* **THE ORM METHOD IS IN BOTH THE PATH AND THE BODY, AND THEY AGREE**, so the cross-check is free --
  and a disagreement is a pinned refusal, because an ambiguous operation stays a write.
* **`onchange` WAS OBSERVED LIVE** and is excluded, with a cell that must be deleted on purpose
  before it can be admitted. `systray_get_activities` and `has_group` are genuine reads that are NOT
  on the allowlist and stay writes -- fail-closed working.
* **THE MEASUREMENT CAUGHT A BUG IN THE DRAFTED RULE**: `path.endswith("/call_kw")` matches NOTHING,
  because the route is `/web/dataset/call_kw/<model>/<method>`. It would have shipped demoting
  almost nothing. Found before a line of `src/` was touched.
* **NOTHING IN `src/` CHANGED.** The rule lives in `benchmarks/` as a proposal under measurement, so
  a wrong answer costs a file nobody imports rather than a revert on the write rail. Conditions 3-7
  -- the verify-by-replay re-arm, all 14 `is_write_request` consumers, a provenance mark rather than
  a `MARK_WIRE` strip, no operator door, adjudication -- are what a build slice owes.

## D7 is built, and condition 3's hazard was real (R4.123, 0.150.0)

`safety.body_says_read(url, body)` clears a POST whose own body names a read, so an app serving reads
over POST stops having every read step filed as a write. An ALLOWLIST FAILING CLOSED: `call_kw` plus
three route-exact page-load reads, with the ORM method read from the URL suffix AND the body and
required to AGREE -- unknown methods, batch arrays, non-`call` envelopes, unreadable bodies and any
disagreement all stay writes.

* **SCOPE FIRST: THIS IS NOT R4.27 FIXED.** It is the JSON-RPC half. GraphQL -- where reads and
  mutations share one endpoint, and which is R4.27's ORIGINAL 12/12 population -- is untouched, and
  `test_annotation_disposition.py` still pins those controls caching as write flows.
* **CONDITION 3 WAS NOT THEORETICAL, AND THE NAIVE BUILD FAILED IT ON THE FIRST RUN.** Clearing a
  request also clears `wrote["hit"]`, and `performed_write` is what SKIPS verify-by-replay -- so a
  server whose read-named call really writes gets replayed a second time at learn. Measured:
  **`saves == 2`**. That is a NEW harm rather than the parity the survey claimed, because today such
  a server fires once.
* **THE FIX WAS TO SPLIT AN OVERLOADED FLAG**, which is this repository's most repeated move.
  `performed_write` was deciding BOTH "refuse a flow that wrote but gated nothing" AND
  "verify-by-replay may re-drive this". Body evidence must clear the first -- keeping it conservative
  makes `_learn_once` DELETE every Odoo read flow, so the two cannot simply be kept together -- and
  must never clear the second. A separate `posted` flag now gates verify-by-replay. `saves == 1`.
* **CONDITION 4: 10 call sites, counted from source so a new one cannot appear unnoticed** (flow 6,
  recorder 3, dryrun 1). TWO are reached: the learn watcher and the HEAL watcher. The second is the
  one the survey named -- without it a demoted step is un-gated and then UNHEALABLE, which is worse
  than either alone. The other eight carry recorded reasons (no body available, wait-predicate not
  verdict, a different authoring path, deliberately conservative).
* **CONDITION 5**: the demotion ADDS `MARK_BODY_READ` and never strips `MARK_WIRE` -- the loop reads
  `read_post_by_step - wrote_by_step`, so a step that also had a genuine write keeps both.
* **CONDITION 6**: no operator door. The allowlist is a frozenset and a cell asserts no env var,
  setting or spec field reaches it.
* **CONDITION 7 IS PARTIAL.** `drift_bench` invariants ALL HOLD (writes double=0 suppressed=0
  wrong_target=0, recovery refused 14/14), `gate_probe` and the readiness refutation unchanged. **The
  3-rep corpus run is NOT done** -- it costs real money, and nothing may be claimed about 2.4b until
  it is.
* **AND THE RED-PROOF GATE CAUGHT A REAL HOLE HERE, WHICH IS WORTH THE GENERAL RULE.** CI refused
  the slice: 38 cells `inconclusive` (they import `body_says_read`, absent on the base, and an
  ImportError is correctly not a kill) and the write-safety cell `no_guard` -- it PASSES on the base,
  because without the demotion there is no hazard for it to guard. So **every test in the slice was
  either unrunnable on the base or indifferent to the change, and nothing proved the FEATURE WORKS**.
  That is the shape to watch for: a slice whose cells all import the new symbol has pinned the RULE
  and not the BEHAVIOUR. `tests/test_read_post_not_gated.py` imports only pre-D7 symbols so it runs
  on the base and FAILS there -- a `call_kw` read learns ungated carrying `body_read`, and its
  write-shaped twin (`create`, same route, same envelope, one word different) still gates, without
  which the read assertion is satisfied by a demotion that fired on everything.

## D7 adjudicated: the reads moved, the writes did not, and that is the design (0.151.0)

Condition 7's three-rep run, both substrates, **$3.11**. It discharges the condition and it does
NOT unblock 2.4b.

* **THE SPLIT IS THE RESULT, not the rate.** Odoo reads **0.200 -> 0.733** because D7 demotes them
  and they never reach the mutation gate; writes **0.000 -> 0.000** because `odoo-create-lead`
  issues a real `create`, `body_says_read` refuses to clear it, and the gate then refuses on
  R4.111's `unique=True` bind failure -- *mutation gate: target missing/ambiguous*, its own branch,
  not page drift. Gitea makes zero POSTs on corpus read pages, so D7 cannot touch it, and the
  baseline gate confirms that three times. A fix that had moved the write column would have been
  the alarming outcome.
* **`varies` 5 -> 0 MATTERS MORE THAN 0.181 -> 0.524.** At 0.133.0 five Odoo rows failed for a
  DIFFERENT reason each pass, so the column was unreadable whatever its mean. Six of seven now give
  an identical verdict every pass. And the declared control group `odoo-menu-nav` passes 3/3 where
  it failed before -- by the corpus's own rule, that is what makes any other Odoo number readable.
* **GITEA TIGHTENED AND I AM NOT CLAIMING CREDIT.** 0.762+/-0.082 -> 0.857+/-0.000, and the
  previously-unstable `gitea-sort-list` went 1/3 -> 3/3. Fisher on 1/3 vs 3/3 is p ~ 0.4, and the
  substrate is 7/7 complete at `domcontentloaded` so the settle should be near-inert. The claim is
  **no regression**, which is what the baseline gate actually tested.
* **D7 DEMOTES NO NAVIGATE STEP -- 0 of 7 (R4.127), AND R4.122's 2/2 IS WHY THAT WAS INVISIBLE.**
  That census measured CLICK windows after a deliberate 6 s wait for the page-load chrome; a
  `navigate` step's act window IS the page load, so the wait excluded exactly the traffic that
  decides it -- and R4.122's own docstring names the risk. Two mail-bus routes carry no ORM method
  and cannot be cleared by a body classifier at all. **Latent only by luck**: R4.118's settle
  stopped learns caching navigate steps, so the three post-settle recipes carry none. Shipped with
  its instrument (`--navstep`); the pure half is pinned offline.
* **A LOUD CHANNEL PRINTED ITS NAME AND NOTHING ELSE (R4.126).** `[note] cost cost_usd:` on every
  pass -- and the FAIL direction is the same line, so a real cost regression names no number. That
  is 1.3 one function over, where the formatter CRASHED instead; a blank is worse in one way,
  because a crash gets investigated. Fixed at the PRINTER rather than by adding a `detail` string
  to each append site, which is correct for today's channels and silent for the next one.
* **WHAT STILL BLOCKS 2.4b, none of it D7's**: writes at 0.000 would enshrine R4.111 as the
  expected state; `odoo-open-record` flips 2/3; and `odoo-search` is 0/3 `not_authored` while
  returning the CORRECT answer with its oracle satisfied -- 2 actions, 0 steps cached. That reads
  as both actions failing with the extractor answering off the page anyway, but it is a reading of
  the code, not an instrumented run, and it is NOT `no_actions_needed` (which needs zero actions).

## A zero-action task could not be named, and the scenario that needed the name measured nothing (0.152.0)

Two defects, found by asking why `odoo-search` scored `not_authored` while returning the correct
answer. They compound, and neither is visible from the other.

* **`no_actions_needed` HAS BEEN UNMINTABLE SINCE 0.131.0 (R4.129).** The clause needs
  `actions_taken == 0`; `scored_run` set that to `len(report.traces)`; and `_author_steps` records a
  nav trace at `index=-1` BEFORE the loop and appends another for the terminal `done`. **The floor
  is 2.** So R4.103's fix disarmed the mechanism R4.101's fix had just created, four slices apart,
  and every zero-action task published as *the product was asked to author this flow and did not*.
* **ELEVEN CELLS WERE GREEN OVER A VALUE THE RUNNER CANNOT PRODUCE.** They all build
  `_Record(..., actions_taken=0, ...)` by hand -- pinning the clause faithfully and saying nothing
  about whether anything can supply its input. The fix's cells drive the real `_learn_once` and then
  classify the result, which is the join the existing file never makes. **When a clause keys on a
  number, one cell has to get that number from the thing that computes it.**
* **THE COUNT EXCLUDES TWO THINGS AND MUST NOT EXCLUDE A THIRD.** The navigation (`index < 0`) and a
  decision to STOP (`meta["stop"]`). A FAILED action still counts -- the agent acted, and it not
  working is a different fact; folding it in recreates R4.103 one predicate over. Both directions
  are armed. The census over the 0.151.0 run confirms the model on both substrates: every healthy
  row satisfies `actions = steps + 2`.
* **`odoo-search` MEASURES NO SEARCH, AND THE REPAIR IS BLOCKED BY R4.102 -- FOUND BY BUILDING IT
  (R4.130, OPEN).** All 17 ACTIVE opportunities render on the landing page and the extractor gets
  the whole body, so the answer needs no action: measured 3/3 at zero actions. R4.129 fixes the
  LABEL; the scenario is a separate thing and is NOT fixed. **Every candidate answer was measured
  and fails** -- a form field is an `<input>` and INVISIBLE to `inner_text` (the record page is 453
  chars, all labels), the archived row's email is shared with FOUR active rows, its revenue renders
  `$ 7,500.00` against the corpus's own >= 1000 rule, its salesperson and stage are on the landing
  page. That left the archived row's NAME, spelled `Furnish a 60m² office`, where `60m2` scores
  False and mints `wrong_data` against a correct answer.
* **SO THE SEED-RENAME REPAIR WAS BUILT, RE-SEEDED, AND REFUTED BY ITS OWN SCORED RUN.** Every
  premise passed -- three offline, three live (answer absent from the landing page, present under
  the Archived filter, restored by `reset()`). Then the run: **20 actions, ceiling hit,
  `found: False`, $0.3386.** The reason was free to see and is not reasoning: the agent's
  observation of that page is **80 elements, entirely row content**, with ONE search control and
  **no Filters control, no dropdown toggler, no Archived option at all**. The repair asked for a
  control the agent cannot see. Reverted rather than shipped -- it would have cost $0.34 a run to
  re-measure R4.102. **The general statement: while the extractor gets the whole body and an Odoo
  list renders every row, NO action-requiring read scenario is constructible on that view.** Fixing
  R4.102 is the prerequisite, not a better scenario. And the premise checks were all green while
  the scenario was unusable -- premises bound the TASK, not the agent's ability to see it.
* **THE DIRECT-IMPORT TRAP IS NOW A GUARD, after catching a third and fourth slice.**
  `from benchmarks.X import fn` binds the function OBJECT, so `_arming.mutate_function` -- which
  replaces the module ATTRIBUTE -- never reaches the cell: the mutation SURVIVES and the arming cell
  reports the guard as unarmed while the guard is fine. It caught me TWICE in this slice alone.
  `test_no_test_direct_imports_a_function_an_arming_mutation_targets` derives the targets from the
  arming file's own AST and refuses the form, and **found three more on its first run**, two of them
  pre-existing. Import the MODULE and call through it.
* **AND `assert_red` CANNOT ARM AN `async` CELL.** It calls the guard synchronously, so an async
  guard hands back a coroutine, nothing raises, and it reports a FALSE SURVIVOR. The browser cells
  prove the runner emits the shape; separate synchronous cells pin the arithmetic over it, and the
  mutations target those. Neither half is sufficient alone.

## Three defects behind one control, and the acceptance test still fails (0.153.0)

Asked to look at R4.102, and the thing I had blamed on it was something else. Each fix revealed the
next; the task that motivated all three still does not work.

* **R4.130 BLAMED THE WRONG DEFECT, AND ONE MEASUREMENT SETTLED IT.** PR #232 said Odoo's Archived
  filter was unreachable because the control was below the fold. It is not: the search toggler sits
  at **y=54 in a 720px viewport**, matches the selector, passes `isVisible`, and arrives in the
  observation at **rank 11 of 80**. The cap was checked too (67 visible against 80). What it has is
  **no accessible name** -- one of SEVEN anonymous buttons in that toolbar.
* **R4.131 (FIXED): 106 of 1036 observed interactables have no name** -- Gitea 0-4%, Odoo 19-22% --
  and **58 (55%) carry recoverable material**: `data-tooltip` (the whole view switcher), a
  DESCENDANT's `title` (the toggler's inner `<i title="Toggle Search Panel">`), or an icon class.
  The other 45% is **15 of 15 row-selection checkboxes**, which legitimately have no label, so every
  unnamed control that is a real command is reachable. **+75 chars/turn, ~19 tokens, 2.3%.**
* **IT IS A NEW FIELD BECAUSE `_ACCNAME_JS` IS SHARED, and that is derived rather than preferred.**
  It feeds `SNAPSHOT_JS`, `DESCRIBE_JS` (the cached locator, replayed through `get_by_role(name=)`)
  and `SCOPE_JS` (every recipe's scope fingerprint). Widening it would invent names Playwright's
  accname never computes AND invalidate every cached flow in every deployment. `hint` is
  observation-only, and `Action` has no `name` field at all, so it can never become a locator.
  It is also a SIXTH redaction channel -- a `data-tooltip` can read "Copy sk-live-..." exactly as a
  name can.
* **AN ICON MODIFIER IS NOT A GLYPH.** `oi oi-fw oi-settings-adjust` first rendered `(icon: fw)` --
  font-awesome's fixed-width modifier dressed as a label. A hint that is noise is worse than none,
  because nothing in the rendering distinguishes them. Skip the modifier set.
* **R4.132 (FIXED): `[role=menuitem]` was admitted and `[role=menuitemcheckbox]` was not.** Odoo's
  Filters menu is 15 of 25 items in that role, `Archived` among them, so the menu the agent had just
  been taught to open arrived EMPTY. Eight ARIA roles added; **blast radius measured first at 0
  elements across all 14 corpus start pages**, because they live inside closed menus. `SCOPE_JS` is
  deliberately left narrower -- widening the GATE's scope hash is a migration, pinned both ways.
* **R4.133 (OPEN): with the menu open, the 80-element cap truncates the menu.** 108 candidates, 91
  visible, cap 80. **Do not reach for a bigger cap** -- that taxes every turn of every page for a
  case that only arises with a menu open, which is D0's shape wearing a token budget. The asymmetry
  points at PRIORITY: an open menu is what the user is choosing from.
* **THE ACCEPTANCE TEST FAILED AND IS REPORTED AS SUCH.** The same goal failed again at 20 actions,
  the ceiling, `found: False`, ~$0.35. Mechanism evidence -- 58/106 named, 7 mutations killed,
  `drift_bench` invariants holding -- does not entail the outcome, and this register is full of
  fixes that moved the mechanism and not the number. **Buy the acceptance run; it is the only thing
  that tells you which of these you have actually fixed.**

## Were we improving the system or just fixing Odoo? Measured (R4.134, 0.154.0)

Every observation finding from 0.146.0-0.153.0 was discovered by an Odoo scenario failing, and each
was ARGUED to be a class rather than a quirk -- from a corpus of two substrates differing on one
axis, so every claim was an extrapolation from a single SPA. `benchmarks/web_survey.py` settles it:
**14 targets, 12 rendering families, 665 interactables, $0.00, no LLM and no login.**

| finding | generalizes? | evidence |
|---|---|---|
| **R4.102 fold** | **hardest of all -- and still OPEN** | **12/14** targets, up to **789** hidden on one page |
| R4.115/R4.120 settle | yes | **7/14** change their interactable set after `domcontentloaded` (max 709) |
| R4.132 ARIA roles | yes | **3/14**, including GITEA -- our own corpus was silently affected |
| R4.131 unnamed | real everywhere, Odoo an outlier | 6% overall vs Odoo 19%; saucedemo 33%, conduit 22% |
| **R4.133 cap** | **no** | **0/14** saturate at rest -- it is Odoo-with-a-menu-open |

* **THE OVERFITTING IS IN THE SOURCE LIST, NOT THE DEFECTS.** `data-tooltip` fires six times and
  **all six are Odoo** -- zero elsewhere -- so hint recovery is 60% on Odoo and **19% off it**. The
  defects are general, the mechanism is general, the sniffs were tuned to one app.
* **CALIBRATE OR THE SURVEY IS FICTION.** The Odoo row exists to reproduce a number measured
  elsewhere (19% unnamed). The first run reported **6 interactables** where the corpus says 80,
  because both local rows ran WITHOUT a session -- `/web` redirects to `/web/login` and the Gitea
  path was a 404. The aggregate looked perfectly reasonable. A calibration row that silently
  measures an error page makes every comparison against it meaningless.
* **DO NOT REIMPLEMENT THE THING YOU ARE MEASURING.** The first draft copied `nameOf` into the probe
  and took its 80 in DOM order; `SNAPSHOT_JS` sorts into READING order and runs a second pass. It
  reported Odoo at 36% unnamed against the product's own 19%. Names and hints now come from a real
  `sess.snapshot()`; the JS answers only what the Observation cannot (candidates, below-fold, ARIA).
* **`--only` AND `--local` KEEP IT CHEAP**, and an unreachable target is a ROW rather than a gap: a
  survey that quietly shrinks is one whose aggregate drifts for reasons nobody can see.
* **AND IT FOUND A DEFECT OF ITS OWN (R4.135).** 14 of the 26 non-Odoo unnamed controls are anchors
  wrapping an `<img alt>`. W3C accname folds that into the link's name; `nameOf` ends at
  `textContent` and an `<img>` contributes neither. Measured both ways: our Observation says
  `name=''`, `get_by_role('link', name='YouMind')` matches 1. Safe direction (a lost locator tier,
  not a wrong bind) and NOT fixed here, because it is the shared helper -- a fingerprint migration.
  A nearby guess was checked and is FALSE: an SVG `<title>` child is already named correctly.

## The fold is closed, and each fix uncovered the next one (R4.102 FIXED, 0.155.0)

The broadest finding in the register -- 12 of 14 surveyed targets, up to 789 hidden -- and the last
of the five observation findings still open. Three defects in one chain again, and this time the
outcome moved even though the scenario still fails.

* **`Observation.below_fold`, A COUNT ON THE ELEMENTS HEADER.** Not the elements: R4.134 measured up
  to 789 below the fold against a cap of 80, so including them evicts the visible ones. Not a height
  comparison either -- `scrollHeight > innerHeight` is INERT on an inner-scrolling app, and that
  refutation is now a BEHAVIOURAL cell (a fixture whose body does not scroll while two controls stay
  hidden) rather than a grep. The grep was tried first and went red on its own comment, which is the
  **seventh** time a scan here has matched the prose explaining it.
* **NOT IN THE FINGERPRINT, AND ABSENT WHEN THE PAGE FITS.** Scrolling would otherwise read as drift
  on every cached recipe; and 2 of 14 targets have nothing below the fold, so the clause vanishes
  rather than printing `0 more`. Both are mutations, both killed.
* **THE FIX EXPOSED R4.136: `await_settled()` IS BLIND TO SCROLLING.** It resolves on DOM-MUTATION
  quiet and a scroll mutates nothing, so it returns `already-quiet` immediately -- measured, 4 of 4
  trials -- and the snapshot races the browser applying the scroll. **1 in 4 snapshotted 73 elements
  at y=0 when the page was about to sit at y=600 with 15.** The agent's trace shows the cost
  exactly: scroll, see an IDENTICAL page, scroll again, receive two scrolls' worth and overshoot.
  Fixed by waiting two `requestAnimationFrame` callbacks -- R4.26's *a timer is not a boundary*,
  applied where `wait_for_timeout` was the obvious reach. 4/4 clean after.
* **THE OUTCOME MOVED, WHICH IS NEW.** Three previous slices improved a mechanism and changed no
  behaviour. Here the traced agent goes from 21 actions of thrashing to `scroll -> target visible ->
  click`, and the click fires the real `POST /times/stopwatch/toggle` -- verified server-side, one
  click starts the timer.
* **AND THE SCENARIO STILL FAILS 0/3, FOR A THIRD REASON (R4.137, OPEN).** The endpoint is a TOGGLE.
  The POST reloads the page to the top, the agent scrolls back, finds the control now reading
  `Stop`, and clicks it -- an even number of toggles, and the oracle correctly reports nothing
  changed. **The information is already in the observation**: measured after a successful start, the
  `PAGE TEXT` carries *"bench started working"*. So the remedy is NOT a fourth widening of the
  observation -- that would be treating a judgement problem with a sensor.
* **THE HONESTY PAGE'S GUARD EARNED ITS KEEP.** Marking R4.102 fixed made
  `test_every_declared_caveat_is_still_open` fail, because `gitea-start-timer`'s 0/3 was declared as
  BEING that defect. The number did not move and its REASON did, which is exactly what that page
  exists to say. Note the declared block is regex-scanned, so even a HISTORICAL mention of a fixed id
  inside it reads as a declaration -- put the history in prose above the block.

## The scroll landed on the header, not the list (R4.138, 0.156.0)

Found by asking whether the previous slice could HARM a substrate it was not tested on. It could.

* **R4.102's SIGNAL WAS ACTIVE HARM ON ODOO.** Measured across the corpus: **6 of 7** start pages
  leave `window.scrollY` at 0 after a scroll, with `below_fold` and the element set unchanged. So the
  observation was telling the agent *"12 more are further down the page -- use `scroll`"* and the
  scroll did nothing. **A signal that cannot be acted on is worse than none** -- D0's shape arriving
  through a fix rather than a refusal. Check the previous slice against the substrate it was not
  built on; that check took ten minutes and cost nothing.
* **THE MECHANISM IS NOT "INNER CONTAINERS DO NOT SCROLL", AND GETTING THAT WRONG COST A MUTATION.**
  `mouse.wheel` dispatches at the POINTER, Playwright's pointer starts at (0, 0), and Odoo's
  scroller is centred at (640, 413) under a control panel -- so the wheel lands on the header, whose
  nearest scrollable ancestor is the window, which cannot move. A first fixture put its pane at the
  top-left where the wheel hit it by accident, and `prove_red` correctly called the central mutation
  a SURVIVOR. **A fixture that does not reproduce the defect makes the fix untestable and looks
  fine.**
* **WINDOW-FIRST IS THE WHOLE BALANCE.** Reach the container and Odoo works; take precedence over
  the document and an ordinary page stops scrolling, because most pages have some inner pane with a
  little scroll room. The check is one line and it has a mutation of its own.
* **BIGGEST BY VISIBLE AREA, NOT BY SCROLL ROOM.** A mostly off-screen drawer with 9000px of content
  has far more scroll room than the list the agent is looking at.
* **SCROLLED VIA JS, NOT BY MOVING THE POINTER ONTO IT.** Both work -- the pointer version is what
  the diagnosis used -- but moving the pointer onto a list of rows fires hover toolbars into the very
  observation the scroll was taken to produce.
* **THE ACCEPTANCE MOVED, AND IT IS R4.121's OWN CASE.** `odoo-open-record` cached **4 `scroll` steps
  and nothing else**; it now caches **scroll x3, type, click, click** -- a real recipe that finds the
  search box and opens the record, `data == expected`. The chain is that the scrolls now CHANGE the
  page, so `no_progress` never accumulates, so the stuck-bail does not fire and the agent still has
  budget to find a productive strategy. **R4.121 stays OPEN**: one instance disappearing is not the
  mechanism changing, and a learn that DOES bail still keeps its wreckage.

## The agent got better and the number went down (R4.139, R4.115 REOPENED, 0.157.0)

Three Odoo reps at 0.156.0, **$1.77**, bought to see what six observation fixes were worth.
Availability **0.524 -> 0.381**, reads **0.733 -> 0.533**. The prediction written down beforehand
("reads should improve") was WRONG, and the reason is worth more than the number.

* **THE AUTHORING PLAINLY IMPROVED.** `odoo-menu-nav` went from recipes of **12, 10 and 6 steps** to
  **6, 6, 6** -- identical every rep -- and `odoo-open-record` stopped scrolling fruitlessly and now
  finds the search box. The agent is doing better work and scoring worse.
* **THE DROP IS TWO ROWS, BOTH FLIPPING ON ONE FIELD.** In every failing rep the recipe is unchanged
  and `mutation_gate_refused` goes True. `odoo-menu-nav` produced a BYTE-IDENTICAL recipe three
  times and the gate passed it once, refused it twice.
* **MARK COUNT IS RULED OUT, and that is what settles the cause.** At 0.151.0 the row carried FOUR
  mutating steps and was never refused; at 0.156.0 it carries THREE and is refused twice. Fewer
  marks, more refusals -- so this is not R4.27 marking more, it is the gate deciding differently on
  the same input.
* **R4.115 HAS BEEN MARKED `fixed` SINCE 0.148.0 WITH TWO OF ITS THREE NAMED SITES OPEN.** That
  entry names (1) the resolve, (2) the gate's `scope_fingerprint`, (3) the gate's whole-page
  snapshot. `_retry_if_unpainted` closed (1) at both call sites. `flow.py:1542` still calls
  `scope_fingerprint` with no settle -- and is reached ONLY when the retry did not fire -- and
  `flow.py:1546`'s fallback snapshot likewise. **A wrong status is worse than an open finding,
  because nobody re-reads a closed one**, and the slice that closed it quoted the three-site list in
  its own commit message. When you fix one site of a finding that names several, fix the STATUS to
  match what you closed.
* **ONE PART OF THE DROP IS ATTRIBUTABLE AND IS THE HONEST COST OF R4.138.** `odoo-open-record` used
  to score `mutating_steps: 0` on 4-step recipes that scrolled fruitlessly; it now scores 2 on
  recipes that SEARCH. Searching means POSTs, Odoo serves reads over POST, so **better agent
  behaviour converts directly into gate exposure** while R4.27 stands.
* **NOT A STATISTICAL CLAIM.** Three reps against three, Fisher p ~ 0.4 on the key row, and rep 1
  scored an identical 0.571 in both runs. The DIRECTION is suggestive; the MECHANISM -- identical
  recipe, different verdict -- is an observation and needs no statistics.
* **SO 2.4b IS NOT REACHABLE, AND THE REASON IS NO LONGER OBSERVATION.** Five of seven rows are
  perfectly stable; the two that move are both the gate. What a fix must not do: the fingerprint has
  the overloaded shape R4.115's first remedy was refuted for -- "it differs" means both *the page
  changed* and *the page has not finished drawing*. Settle BOTH sides of the comparison; never wait
  until the fingerprint matches.

## The gate was deciding drift from a half-drawn page (R4.115 CLOSED, 0.158.0)

The two sites R4.115 named and 0.148.0 never touched. One `await_settled()` inside
`if step.mutating:`, ahead of both `scope_fingerprint(target)` and the fallback snapshot.

* **ACCEPTANCE ON THE ROW THAT DIAGNOSED IT.** `odoo-menu-nav` was 1/3 with the gate refusing a
  byte-identical recipe twice; it is now **4/4 `ok`, `mutation_gate_refused` False every rep**,
  three of them carrying the same 6-step/3-mutating shape that used to be refused. Fisher on 1/3 vs
  4/4 is **p ~ 0.14**, so the RATE is suggestive; what is conclusive is that the verdict stopped
  varying on identical input, which is an observation rather than a statistic.
* **WHY WAITING INSIDE A WRITE GATE IS SAFE, which is the argument a change here owes.** Settling
  changes WHEN the gate looks, never WHAT the page contains, so a form that genuinely drifted still
  differs afterwards. Asserted as a DIFFERENTIAL: both arms churn for a second after the first read
  and differ only in whether the form really changed -- mid-render reads `drifted=False`, a changed
  form reads `drifted=True`, both settling to `quiet`. Two independent assertions would have passed
  a gate that always said False.
* **IT IS NOT "WAIT UNTIL THE FINGERPRINT MATCHES"**, which is the overloaded-sensor trap R4.115's
  first remedy was refuted for. `await_settled` resolves on the page's own quiet, knows nothing
  about the fingerprint, and caps at `settle_cap_ms` -- a page that never settles is read exactly as
  it is read today, never later.
* **THE 0.148.0 GUARD FIRED AND FORCED THE ARGUMENT, which is the second time it has.**
  `test_replay_never_settles_UNCONDITIONALLY` refused the fourth call site until the cost was
  MEASURED: on an already-quiet page `await_settled()` returns `already-quiet` in a median of
  **3.3 ms (Gitea) / 2.2 ms (Odoo)**, and the call is scoped to mutating steps so a read pays
  nothing. The cell now pins the SHAPE in three named positions rather than a count -- plus that the
  gate's settle is inside the mutating branch AND before the comparison, because either alone is
  wrong: after the comparison it is decoration that every structural cell still passes.
* **WHAT IS NOT CLAIMED.** One row is not a rate. Whether the Odoo CORPUS number recovers needs its
  own three reps, and the other half of R4.139's drop is untouched -- `odoo-open-record`'s extra
  gate exposure is R4.138's honest cost while R4.27 stands, and the writes remain 0.000 on R4.111.
  (**Those three reps were bought at 0.159.0 and the corpus number DID recover** -- see the next
  section. The rest of this bullet still stands.)

## The gate gave back its whole class, and the ceiling was lower than I predicted (0.159.0)

Three fresh Odoo reps at 0.158.0, **$1.71**, bought to answer the question 0.158.0 left open: does
closing R4.115's sites (2) and (3) move the CORPUS number, or only the one row that diagnosed it.

* **`over_gated/drift` WENT 4 -> 0 ACROSS ALL 21 SCENARIO-OBSERVATIONS.** That is the fix's entire
  target class, and it is the result -- not the rate. `odoo-menu-nav` **1/3 -> 3/3** with `steps=6`
  in every rep, so the identical-recipe/differing-verdict signature that R4.139 IS has gone; and
  `odoo-open-record` **1/3 -> 3/3**. Availability **0.381 -> 0.524**, std **0.165 -> 0.082**,
  unstable rows 2 -> 1. The write rows did not move, which is what SHOULD happen -- they are R4.111,
  a different mechanism, and a fix that had moved them would have been the alarming outcome.
* **THE PREDICTION WRITTEN DOWN BEFOREHAND WAS 0.714 AND WAS ARITHMETICALLY IMPOSSIBLE.** With
  `odoo-search` at `no_actions_needed` (R4.130) and both writes on R4.111, only FOUR of seven rows
  can pass: the ceiling is **4/7 = 0.571**, which is the number I had written down as the FALLBACK.
  Reps 2 and 3 scored exactly 0.571. So the honest claim is not "it improved by 0.14" but **"it
  reached the maximum available in two passes of three"**, and the third lost one row to R4.140.
  Writing the prediction down was still worth it -- it is what made the miscount visible instead of
  letting 0.524 read as a disappointing partial win.
* **A NEW INSTABILITY APPEARED BESIDE THE FIX, AND PROVENANCE COULD NOT CLEAR IT (R4.140).**
  `odoo-filter-status` -- 3/3 at 0.156.0 -- refused `shape_drift` once here, then **2/3 on a
  row-only rerun**, and `git diff --stat` between the two series is **one file, +27 lines: my
  settle**. There was no version of "probably unrelated" worth saying.
* **SO THE MECHANISM SETTLED IT, WHICH IS THE MOVE WORTH COPYING.** The settle lives inside
  `if step.mutating:`. Measured on that row: **`mutating_steps=0`, `mutating_sources=()`,
  `mutation_gate_refused=False`** over three reps -- D7 demotes both of its two clicks, the gate
  never runs, and the settle is **dead code on it**. That is a proof, where the statistics are only
  a consistency check: 0/3 clean at a 1/3 rate has probability 0.30 and Fisher on 0/3 vs 3/9 is
  **p = 0.51**, so 0.156.0's clean sweep needs no explanation beyond luck. **When a new symptom
  appears next to a new fix, look for a reason the fix cannot execute -- a p-value cannot exonerate
  a change, and this one did not need to.**
* **WHAT R4.140 ACTUALLY IS**: the extraction returns `{'count': 6, 'opportunities': [...]}` and the
  optional second key makes the SHAPE vary between learn and replay, so `ShapeDriftError` fires.
  Inviolable #2 working -- the alternative is returning structurally-changed data. What is wrong is
  that a read flow's availability depends on whether an LLM chose to include a second key. Pinning
  the read is the obvious remedy AND makes the replay 0-LLM, but it changes what a read flow may
  return on the one surface whose job is refusing silently-wrong data, and must not be bought by
  loosening the shape gate.
* **AND `score_one` RETURNS A TUPLE UNDER A `-> dict` ANNOTATION**, which cost a paid learn: the
  probe read `.get` off it and raised at the LAST statement, after the money was spent. Exactly B5's
  "a stub cannot see a changed return type", met from the other side -- an annotation is not a
  contract, and a throwaway probe that spends money should unpack the real return before the run,
  not after it.

## `shape_drift` does not generalise, and the remedy I named could not fire (R4.140, 0.160.0)

R4.140 was filed at 0.159.0 with a worry and a remedy, and measurement refuted both. Driving the
other four Odoo reads five times each cost **~$1.7**; the correction to the remedy cost nothing but
reading `pin.py`.

* **THE WORRY WAS RIGHT TO HAVE AND THE ANSWER IS NO.** At a 1/3 rate a clean three-rep run happens
  30% of the time, so the other reads' stability records were NOT evidence of immunity -- that is
  the same arithmetic used to excuse 0.156.0's clean sweep, and it cuts both ways. Measured: **19 of
  19 scored runs returned a bare string** (`odoo-search` 'Quote for 150 carpets', `odoo-sort-list`
  'Need 20 Desks', `odoo-menu-nav` 'Won', `odoo-open-record` 'Gemini Furniture'), one distinct shape
  apiece. `odoo-filter-status` is the ONLY corpus read whose goal asks for two things at once, which
  is what invites a composite answer.
* **THEY ARE STRUCTURALLY IMMUNE, WITH TWO INDEPENDENT SOURCES, AND ONLY AN ARMING PASS FOUND THE
  SECOND.** `_shape_of` leaves the value out of a scalar's shape, so two answers give BYTE-IDENTICAL
  shapes and `_shape_matches` accepts them at `recorded == current`; and even with the value put
  back, the `same primitive type` arm answers True. Breaking EITHER alone leaves the new guard green
  -- so my first two mutations both reported SURVIVED while the cell was fine, and my first
  docstring named the wrong mechanism. Breaking both kills it. **A cell that survives a mutation is
  as often a wrong story about the code as a hole in the suite**, and the difference is one more
  mutation.
* **THE REMEDY THE FINDING NAMED CANNOT FIRE ON THE ROW IT DESCRIBES.** "Pin the read" claimed to
  fix the flake AND deliver 0-LLM. `read_pin` is not a schema pin -- `pin.py` records a resilient
  LOCATOR to the element holding the answer and re-reads `inner_text()`, which is where the 0-LLM
  comes from, and it records only for a SCALAR mapping to **exactly one** element. This row returns
  `{'count': 6, 'opportunities': [...]}`: no pin, ever. Two benefits claimed from a mechanism that
  delivers neither here.
* **WHAT WOULD FIX IT IS ALREADY BUILT, IN TWO HALVES NOBODY HAS JOINED.** `extract()` takes a
  `schema` that becomes the tool-use `input_schema` -- a hard constraint at the API boundary, not a
  prompt request -- and `flows.py` passes `spec.extract_schema`, which defaults to None and is set
  nowhere in the bench. Meanwhile the learn ALREADY records `meta.shape` and uses it only to REFUSE
  afterwards. Deriving one from the other uses the same fact to prevent the divergence instead of
  punishing it, and the fit is exact (`_shape_of` records a dict's sorted KEY SET; `_shape_matches`
  compares only that). **It keeps the LLM call** -- no 0-LLM win -- and must not be bought by
  loosening `_shape_matches`, because a DROPPED key is what that gate exists for.
* **AND THE MEASUREMENT POINTED SOMEWHERE BETTER THAN THE FINDING DID.** All four immune rows return
  a scalar string, which IS `read_pin`'s precondition -- so **four of five Odoo reads may be
  candidates for genuinely 0-LLM replay**, the central product claim. Unverified (the pin needs the
  value to map to exactly one element) and untried (`pin_read` is opt-in and the bench sets it
  False). That is the cheap next measurement here, not a fix to the shape gate.
* **THE SCALAR RESIDUAL, so "scalars are safe" is not over-read**: `_shape_matches` compares `t`
  FIRST, so `6` and `'6'` still drift. A goal asking "how many" is exactly where an extractor might
  quote a count -- so rewording this row to return a scalar NARROWS the failure rather than removing
  it, and dropping "show only" risks turning it into another zero-action row (R4.130).

## The 0-LLM claim is undemonstrated for every read, and pinning cannot fix it here (R4.141, 0.161.0)

The plan's own frontier is 2.4b and its note had gone five slices stale, so the first job was
re-deriving what actually blocks it. That turned up a prior question: **do the corpus's reads replay
at 0-LLM at all?** They do not, and the mechanism built to make them do so cannot fire on either
substrate. Total cost **$0.11**, and the expensive half was refused before it was bought.

* **EVERY CORPUS READ PAYS ONE LLM CALL PER REPLAY.** `flows.py` builds an extraction router when
  `spec.extract is not None and not (spec.pin_read and meta.read_pin)`, and `scored_run.spec_for`
  sets `extract` for every read and `pin_read` for none. **Measured on BOTH substrates rather than
  derived**: `gitea-menu-nav`, `gitea-search` and `odoo-sort-list` each replay `llm_calls=1,
  zero_llm=False`. R4.116 had measured one Odoo instance; the substrate with a published baseline had
  never been checked.
* **SO `availability_rate` MIXES TWO THINGS.** It counts a read as available while it calls the model
  every replay -- and the stated reason `no_actions_needed` scores ZERO is *"every run pays the LLM
  again -- no speed-up, so not available"*. Both are true of an extracting read. The difference is
  real but QUANTITATIVE, and nothing measures where a row falls. The writes are the rows that
  genuinely replay at 0-LLM.
* **THE MISSING NUMBER IS ALREADY COMPUTED AND PUBLISHED NOWHERE.** `scored_run` sets
  `out["replay"]["zero_llm"]`; `build_bench_record`'s per-scenario row carries only
  `{outcome, substrate, code}`. Deliberately NOT plumbed here -- `zero_llm` is an OBSERVATION and
  B3's design keeps those on `ScenarioRun` and adjudications on `Scored`, so it crosses a boundary
  that took a slice to get right and should not be crossed in a slice about something else.
* **THE $0.62 OF LEARNS WAS REFUSED BY A $0.00 MEASUREMENT.** The obvious move was to switch
  `pin_read` on and see. `benchmarks/pin_viability.py` asks the precondition instead, driving the
  product's own `_PIN_JS` and `find_pin`: **0 of 10 reads pinnable**. R4.140 had supplied half the
  precondition free (19 of 19 answers scalar); the half that refuses is DOM IDENTITY. **Odoo carries
  no `id` and no `data-testid` on any of 765 leaf text holders**; Gitea has ids on 371 of 985 and
  never on the element holding an answer.
* **AND "JUST WIDEN THE ALLOWLIST" IS NOT THE REMEDY, WHICH IS THE PART WORTH KEEPING.** A pin must
  locate its element WITHOUT reference to content -- `read_pin` passes `text=None`, commented *never
  anchor on the value*, because anchoring on the learned text finds the element by the OLD value and
  reads it back forever. On a table cell the only content-independent anchors are structural
  (refused, correctly, and already pinned by an existing test) or an id (absent). The recipe's own
  locator tiers do not escape it either: `role+name` on a data cell resolves through its text. A real
  remedy needs a value-independent, non-positional anchor -- a stable ANCESTOR plus an offset, or a
  neighbouring LABEL, which is the `anchor` concept `locators.py` already has for rows. Unevaluated,
  and recorded as the design to evaluate rather than as a proposal.
* **MY OWN PROBE READ THE PREVIOUS PAGE, AND THE LEAF COUNTS ARE WHAT GAVE IT AWAY.** Odoo routes on
  the HASH, so `goto` between two `#action=` urls inside one session is a SAME-DOCUMENT navigation:
  it resolves at once and `await_settled` finds the view it just left already quiet. Three rows
  reported 10-22 leaf text holders where the rendered list has 202. An `about:blank` hop fixes it,
  and the corrected run flipped `odoo-search` to `on_start_page=True` -- independently reproducing
  R4.130's central claim, which is what made the fix believable rather than merely different.
* **AND A SCAN MATCHED ITS OWN PROSE FOR THE EIGHTH TIME.** An ordering cell compared `src.index()`
  offsets and went red on the docstring naming `await_settled` while explaining the bug. It reads the
  AST now. Eight occurrences is enough that the rule should be read as absolute: **never asserts an
  ordering or an absence over source TEXT.**

## The cap hid the menu, and the obvious selector would have reordered the corpus (R4.133 FIXED, 0.162.0)

Asked to retry R4.130. The retry could not run: the control it needs was rendered, visible,
correctly named and **not in the observation**. Diagnosis and fix cost **$0.00** -- the paid run was
never reached, and the finding this closes is the third blocker R4.130 has named.

* **ASK WHAT THE AGENT CAN SEE, AND KEEP ASKING AFTER EACH FIX.** R4.130 blamed R4.102 (wrong -- the
  toggler is at y=54), then R4.131 (right, fixed at 0.153.0). Driving the page now: the toggler IS
  named (`hint='labelled: Toggle Search Panel'`), clicking it DOES bring `menuitemcheckbox` items in
  (R4.132 working) -- and **9 of the page's 16** arrive. 118 visible interactables against a cap of
  80, and a dropdown OVERLAYS the list, so its items interleave with the rows behind them in reading
  order and the cut lands mid-menu. `Archived` was one of the seven lost.
* **PRIORITY, NOT A BIGGER CAP** -- which is what R4.133 was filed asking for. Overlay candidates
  survive; the rest fill the remaining slots in reading order; the kept set is re-FILTERED out of the
  sorted list so survivors stay in reading order rather than menu-first. Cap unchanged at 80, and an
  over-large overlay is itself truncated. **Acceptance: 9 of 16 -> 16 of 16, `Archived` included, at
  the same 80 elements.**
* **THE OBVIOUS SELECTOR WAS REFUSED BY ITS OWN BLAST-RADIUS MEASUREMENT, and that number is the
  real content of this slice.** `el.closest('[role=menu],[role=dialog],[role=listbox],[role=tree]')`
  is the reading anyone would write. Measured across all 14 corpus start pages: **10 were BOTH
  truncated AND carrying 4-11 such elements AT REST.** `role=menu` names a PERSISTENT container on
  both substrates -- Gitea puts it on a CLOSED dropdown (`aria-expanded="false"`), Odoo on its
  `o_menu_systray` toolbar. Neither is a popup, and priority for them silently reorders most of the
  corpus: no error, no invalidation, just a different first element on every page in every
  deployment.
* **RE-KEYED ON THE ITEM'S OWN ROLE**, `menuitem`/`menuitemcheckbox`/`menuitemradio`/`option`: the
  same measurement reports **0 overlay elements at rest on 13 of 14 pages**, and the fourteenth is
  not truncated -- inert across the whole corpus at rest. A closed panel's items are absent from the
  DOM or invisible, so they never become candidates; that is what makes the item role a *state*
  signal where the container role is not. A modal is still recognised by CONTAINER, because its
  contents are ordinary controls with no distinguishing role of their own.
* **PIN THE INERT DIRECTION HARDEST -- it is the one that rots quietly.** An untruncated page keeps
  everything, a truncated page with no overlay takes the same `slice(0, MAX)`, and a bare container
  grants NOTHING. 6 mutations, 6 killed, including the refused first design, which **every
  menu-facing cell still passes**. `drift_bench` invariants ALL HOLD.
* **A FIXTURE THAT OVERFLOWS THE FOLD TESTS NOTHING.** The first draft parked its overlays at
  `top:900px`, outside the 720px viewport, so `isVisible` dropped them (R4.102) and five cells went
  RED against a working fix. R4.138's fixture lesson one finding over: when a new cell fails, ask
  whether the fixture reproduces the condition before touching the code.
* **R4.130 IS NOT FIXED, and the remaining work is a scenario rebuild plus a paid run.** The seed
  rename and goal rewording were reverted at 0.152.0. Its general claim is untouched and is still
  what to check first: while the extractor receives the whole page body and an Odoo list renders
  every row, a landing-page answer needs no action.

* **AND THE INSTRUMENT DEADLOCKED ON ITS FIRST REAL USE (R4.142).** `tier_marks.py merge`
  validates a candidate manifest with `ULTRACUA_TIER_MANIFEST`; `tests/_tiers.py` honours it and
  `scripts/mutation_sweep.py` did not, so the tier SELECTION came from the candidate while a
  registry's tier DERIVATION came from the committed file -- which cannot yet know a killer file the
  PR is adding. The merge then says *"the fast tier is RED but no test launched a browser -- that is
  a real failure, not a classification problem"*, which is accurate about what it saw and points at
  the wrong layer. **A deadlock rather than a wrong answer**: no re-run helps. 0.6 built the sweep
  and 0.8 built the loop, and no slice had ever added a registry whose killer was also new -- two
  components correct alone and wrong at the join, which is `Odoo.reset()`'s standing note one
  instrument over.

## The Odoo writes fail on an inert click, not on the locator (R4.143, 0.163.0)

R4.111 was diagnosed at 0.139.0 and five slices have landed underneath it since -- D7, the learn
settle, the replay settle, the GATE settle and five observation fixes. Nobody had re-asked whether
its story was still true. **$0.43**, two reps each, and both rows reproduce identically every time.

* **NEITHER ROW FAILS THE WAY THE REGISTER SAYS, AND THEY DO NOT FAIL THE SAME WAY AS EACH OTHER.**
  `odoo-create-lead`: **`gate_refused` is FALSE** -- the mutation gate never runs at all.
  `odoo-idempotent-replay`: gate refused, on `form/section drift`, the PRECISE branch's SCOPE
  comparison. The recorded story for both was `resolve(..., unique=True)` failing to bind UNIQUELY
  on a generated DOM.
* **IT IS ABSENCE, NOT AMBIGUITY -- the exact pair `saw_candidates` exists to separate.** The
  failing resolve reports **`saw_candidates=False`** on the first attempt AND on R4.115's
  settle-and-retry. Reading the message alone (*"locator unresolved or ambiguous"*) cannot tell
  those apart, which is why it was wrong for five versions; the sensor added at 0.148.0 answers it
  in one field.
* **THE PHOTOGRAPH IS WHAT SETTLES IT.** At that moment the page is the LEADS LIST --
  `view_type=list`, `has_form: false`, 24 visible fields that are the search box and 22 row
  checkboxes, body text *"New Generate Leads Leads 1-22 / 22 ..."*. So step 2's target is genuinely
  not there, **because step 1's click on `button 'New'` bound by `role+name`, executed without
  error, and left the page where it was.** The failing step is not the broken one, and the operator
  is told the locator drifted when the locator is fine.
* **INVIOLABLE #2 HOLDS, WHICH IS THE PART NOT TO LOSE.** The run fails LOUD and the oracle confirms
  *"nothing changed on the server"* -- no write fired, nothing wrong was returned. What is wrong is
  the ATTRIBUTION. And both replays are genuinely **0-LLM** (`zero_llm: True`), exactly as R4.141
  predicts for a write: these two rows are the ones that DO demonstrate the product's central claim,
  and they fail for an unrelated reason.
* **SO 2.4b's WRITE HALF IS NOT A `unique=True` PROBLEM** and must not be costed as one. R4.111's
  own hedge was right -- *a signal, not yet a finding ... one message is not a measurement* -- and
  the measurement has now been taken.
* **THE NEXT MEASUREMENT, NAMED RATHER THAN GUESSED**: why is the click inert? Two candidates worth
  separating before any `src/` change -- the click lands on a node OWL is about to replace (a
  readiness issue on the ACT side, where every fix so far has been on the resolve side), or the
  recorded target is a different "New" from the one that opens the form. One `scope_fingerprint` in
  the idempotent-replay trace also returns EMPTY, which is unexplained.
* **THE INSTRUMENT SHIPS WITH THE DIAGNOSIS** (`benchmarks/replay_step_probe.py`) and it patches
  `flow.resolve`, not `locators.resolve` -- S14's lesson, since `from .locators import resolve`
  binds the OBJECT and patching the definition module reaches nothing.

## The click was never inert: the render is network-gated (R4.144, 0.164.0)

R4.143 said the Odoo write rows fail on an inert click. Asked to find out why, and the answer is
that they do not. **$0.09** -- one paid learn; every other measurement was free.

* **A PAGE PHOTOGRAPH IS NOT A VERDICT ABOUT AN ACTION, and that is the correction.** R4.143 read
  the leads list still showing after the click and concluded the click did nothing. That view is
  ambiguous between *the click did nothing* and *the render has not finished*, and only the request
  log separates them. The wire: `POST .../crm.lead/onchange` **47 ms after the resolve**, then
  `GET /web/bundle/web_editor.backend_assets_wysiwyg` at +125 ms and `POST .../render_public_asset`
  at +547 ms. Odoo's list -> form transition is a MULTI-STAGE render that fetches JS and CSS it has
  not loaded yet.
* **THE REPLAY LOOKS TWICE AND BOTH LOOKS ARE TOO EARLY**: the step's resolve at +31 ms and
  `_retry_if_unpainted`'s single retry at +359 ms, before `render_public_asset` is even requested.
  Both report `saw_candidates=False`, correctly -- the field is not there yet.
* **R4.115's DOCUMENTED LIMIT, MET IN THE WILD, WITH A THIRD STATE IT DID NOT NAME.** That entry
  says mutation-quiet cannot tell *finished rendering* from *has not started*. This is neither: the
  DOM is quiet because the page is WAITING ON THE NETWORK, with nothing to mutate until the bundle
  lands. R4.120 validated `mut-quiet-200` over 60 page-reps with zero prematures -- but that
  population was page LOADS, so the predicate's evidence never contained a post-click transition
  that fetches new assets.
* **THE COLD/WARM SPLIT IS WHY IT HIDES.** Outside the replay the field is resolvable **188-281 ms**
  after the click on a warm context and **484-750 ms** on a cold one, and in all six reps
  `await_settled()` returned AFTER the field existed -- so settle-then-retry succeeds there. A
  replay opens a fresh context, pays the bundle download, and its one retry lands in the gap.
* **FOUR CANDIDATE CAUSES REFUTED BY A BISECT, which is what stopped a wrong fix.** direct/proxy x
  bare-click/`expect_request` wrapper, five reps each: **5/5 opened in every arm**. So neither the
  idempotency proxy nor the mutating-step request wrapper is involved, and the "stale node OWL is
  about to replace" story dies with them.
* **THE REMEDY DIRECTION, STATED AND NOT BUILT.** One retry cannot cover a render with several
  network-gated stages; what is missing is not a longer wait but MORE THAN ONE look, bounded by
  `settle_cap_ms`. Safe in exactly R4.115's way -- poll only while `saw_candidates` is False, so all
  four safety refusals still fail LOUD and immediately and a competing candidate can never be waited
  out. `networkidle` is separately refuted (it never fires on Odoo), so the fix must not reach for
  it. It needs its own cost measurement on the substrates R4.120 measured at `true_ready = 0`, where
  every extra look is pure tax.
* **THE INSTRUMENT SHIPS WITH THE CORRECTION**: `replay_step_probe --wire` interleaves resolves and
  requests on ONE clock. Two clocks cannot be interleaved, and the interleaving IS the evidence --
  both are pinned by tests.

## The poll's tax was measured on the wrong population, twice (R4.144 FIXED, 0.165.0)

R4.144 named its remedy and demanded a cost measurement before any `src/` change. The measurement
is the whole slice: it shaped the fix twice, and both times because the first number was taken where
the cost is not.

* **THE FIX, AND WHY ONE LOOK CANNOT WORK.** `_retry_if_unpainted` looks repeatedly, bounded, keyed
  on `saw_candidates` EVERY look. The measured trace is identical across every rep: look 1 `quiet`
  misses at ~343 ms, looks 2-4 **`already-quiet`** miss, look 5 binds at 719-906 ms. A loop that
  reused the existing `already-quiet` skip would stop at look 2 and change nothing.
  **`odoo-create-lead`: `refused_wrongly` -> `true`, 5 of 5 paid runs.**
* **TAX NUMBER ONE WAS TAKEN ON SCENARIOS THAT SUCCEED.** Across `gitea-sort-list`, `gitea-search`
  and `gitea-comment` the retry path fired **0 times** -- exactly R4.120's `true_ready = 0`
  prediction -- and I read that as "the tax is zero". It is zero on rows that PASS. `drift_bench` is
  370 rows of deliberately DRIFTED pages, which is the retry path's actual population, and it said
  so on the first run: **184 s -> 259.2 s against a 220 s budget**, `within_wall_budget` FAILED.
  **A cost measured only where the mechanism does not fire is not a cost measurement.**
* **AND THE SECOND MEASUREMENT NAMED THE DESIGN, not just the price.** Instrumented: **36 rows
  paying 2251 ms each, 81.1 s**, every one reporting `already-quiet` on every look after the first.
  That is not a page mid-render -- the element is GONE, and waiting can only spend the budget. A
  page WAITING ON THE NETWORK looks different: its quiet gaps are punctuated by bursts, and each
  burst makes the next settle WAIT rather than answer instantly. **Consecutive `already-quiet` is
  the discriminator**, and the counter RESETS whenever a settle genuinely waited. Six looks is twice
  the Odoo render's measured need of three. Bench **202.7 s, invariants ALL HOLD**; Odoo acceptance
  still 2/2.
* **SAFE IN EXACTLY R4.115's WAY**, which is what a change to the replay's resolve owes: the moment
  the page ANSWERS, this is a refusal rather than an unpainted page and it stops. All four safety
  refusals still fail LOUD and immediately, and a competing candidate can never be waited out -- the
  measured wrong-record bind the first remedy was refuted for. 9 mutations, 9 killed, and the two
  that matter most are INERT to every success cell: waiting a mid-poll refusal out, and polling a
  stopped page to the budget.
* **A CELL THAT CANNOT FAIL FAST CANNOT FAIL.** The budget cell asserted elapsed time AFTER the
  call, so the mutation removing the deadline did not make it red -- it made it HANG, past a 600 s
  harness timeout. An unbounded thing does not return slowly, it does not return. It wraps its own
  `asyncio.wait_for` now.
* **THE 0.148.0 SETTLE GUARD FIRED FOR THE THIRD TIME**, correctly, over a second settle inside the
  helper. It also went red on the new PROSE explaining the poll, because its count was taken over
  source TEXT -- the ninth occurrence of that class, and the first inside a cell whose subject IS a
  count. It reads the AST now and pins four named positions.
* **THE SUITE FAILED A BUDGET THE STANDALONE RUN PASSED, and both numbers are real.** 202.7s
  standalone, **239s in-suite against a 220s budget** where `main` passes -- this host measures ~29s
  higher under its own load. Two cuts followed: the poll now **re-resolves only when the page
  actually CHANGED** (the `already-quiet` skip applied once per look rather than once per call --
  **184.2s -> 199.0s**), and the budget is re-derived to 260s openly, because the remaining ~15s is
  the stall window itself on the densest possible population of absent targets, and shrinking it
  would buy the budget with the margin a multi-stage render needs. Same act 0.148.0 took, same
  stated reason. **The budget was also written TWICE** -- an invariant in the bench and an assertion
  in its test -- and is now `WALL_BUDGET_MS`, read by both.
* **A NEW MECHANISM DISARMED AN EXISTING GUARD, AND NOTHING WENT RED FOR IT.** Before the stall
  guard, `the_beat_between_looks_is_removed` was KILLED by a cell counting looks: without the beat
  the loop spun for the whole budget. The stall guard makes that loop exit after six looks either
  way, so the cell went blind and the mutation started SURVIVING. Only re-running the registry
  showed it. 1.5's function-split lesson arriving through a behaviour change instead of a refactor:
  **when you add a mechanism, ask which existing guards it makes unfalsifiable.**
* **AND THE BASELINED SUBSTRATE WAS CHECKED RATHER THAN ASSUMED (R4.145, OPEN).**
  `gitea-sort-list` came back 1/3, so the poll went on trial: **2/3 against main's own `src/`**,
  Fisher p ~ 1.0, step counts 0/2/8 on main. Not this change -- but the row was **3/3 at 0.151.0**,
  which is the series `baselines/customer_v1_gitea.json`'s 0.857 was cut from. The open question is
  the baseline, not the row.

## The corpus refuted a number I had already written down (R4.146, 0.167.0)

Three Odoo reps at 0.165.0, **$1.79**, bought to see whether R4.133 and R4.144 moved the corpus.
Availability **0.524 -> 0.619**, `varies` still 0, and one rep reached the 0.714 ceiling for the
first time. The interesting part is the row that did not.

* **I PREDICTED THE MEAN CORRECTLY AND THE MECHANISM WRONG, WHICH IS THE MORE DANGEROUS HALF.**
  Written down beforehand: mean **0.62-0.71**, because `odoo-filter-status` flakes about one run in
  three (R4.140). Measured: **0.619** -- and `filter-status` held **3/3** while `odoo-create-lead`,
  which I had predicted at 3/3, came in at **1/3**. Checking only the headline would have called
  this a clean success. **A prediction is a pair -- the number and the reason -- and only the
  second one is worth anything when the first is right.**
* **R4.144's `5/5` WAS A LUCKY STREAK.** That entry recorded 5 of 5 from two SOLO batches. Four
  fresh solo reps returned **3 of 4**, so the honest figure is **9 of 13** and the row is FLAKY, not
  fixed. The mechanism in that entry is right; the claim built on it was too strong. Corrected in
  place -- two batches under the lightest possible conditions is exactly where a streak hides.
* **THE FAILURE HAS ONE SHAPE AND THE INSTRUMENT SHOWS IT.** Every pass reports
  **`quiet:bound:2` at 859-938 ms**; the single failure reports **`already-quiet:stalled:1` at
  734 ms** -- the stall guard giving up because the page had not produced its next render stage yet.
  So it is a RACE between the stall window and the render's inter-stage gap, and the gap sometimes
  exceeds ~730 ms. Not a locator, not a gate.
* **CONTEXT MAKES IT WORSE, CONSISTENTLY WITH THAT MECHANISM.** Solo **8/9**, corpus **1/3**, where
  the row runs sixth of seven after five resets and ~30 minutes of load. Fisher p ~ 0.045 --
  suggestive at that n, and a slower machine producing longer quiet gaps is exactly what the
  mechanism predicts.
* **DO NOT WIDEN THE WINDOW.** The stall guard exists because polling to the budget cost **81 s on
  `drift_bench`**. A window wide enough for a ~1200 ms gap puts ~14 s back, and both knobs trade the
  window directly against the cost. **Consecutive quiet is a PROXY**, now the second spent sensor
  here, so D5 applies: change the sensor class rather than its constant.
* **THE CANDIDATE, NAMED AND NOT BUILT**: whether a request is IN FLIGHT. During the bundle download
  there are pending requests; when the element is gone there are none. Strictly weaker than
  `networkidle`, which is separately refuted because Odoo's bus holds a connection open and it never
  fires -- so that refutation does not carry over. It needs its own cost measurement, and this
  slice's own record is why: **both previous readings of this mechanism's cost were wrong, and both
  were taken on populations it does not fire in.**

## The in-flight sensor: refuted as proposed, kept as something narrower (R4.146, 0.168.0)

R4.146 named "whether a request is IN FLIGHT" as the replacement for consecutive-quiet and made
itself conditional on measuring it first. **~$0.45**, both populations, and the answer split.

* **THE ENTRY READING DISCRIMINATES PERFECTLY, AND THE FREE HALF SAYS SO.** Of `drift_bench`'s 126
  retry-path entries, the **36 that STALL have ZERO pending at entry and zero at every later
  sample**, costing **23.4 s**; Odoo's render gap reads **1-2 pending from entry** in 3 of 3 paid
  reps. At the moment the retry begins the two populations are cleanly separated.
* **THE LIVE COUNT IS NOT A SENSOR, AND ONLY SAMPLING SHOWED IT.** Through one Odoo poll at 25 ms
  intervals the pending count returns to ZERO mid-render -- **busy spans 16-688 ms, zeros span
  516-891 ms on the same run**, element at ~907 ms. Odoo's last stage renders the assets it just
  fetched, which is CPU work with nothing outstanding. So **"nothing pending" does not mean "nothing
  is coming"**, the longest mid-render zero run is ~175 ms (3-4 looks at a 50 ms beat), and a live
  sensor needs the same consecutive-count crutch the stall guard already has. That is not the
  sensor-class change D5 asks for.
* **THE SUMMARY READING WOULD HAVE SHIPPED THE WRONG THING.** The first pass recorded only entry and
  peak, both of which look decisive; the mid-render zeros are invisible unless you sample. Two
  earlier readings of this same mechanism's cost were also wrong for want of the right population --
  **this is the third, and the first where the population was right and the SAMPLING RATE was the
  gap.**
* **WHAT SURVIVES IS NARROWER AND BETTER THAN WHAT WAS PROPOSED.** Trust the reading only AT ENTRY:
  nothing outstanding when the retry began -> give up at once. 36 of 36 on the bench, 0 of 3 on
  Odoo, so it saves the whole 23.4 s and changes nothing about the render. **And the second-order
  effect is the point**: with that check in front, the stall window is only reached by a row that
  HAD something in flight, of which `drift_bench` has none -- so widening the window would cost
  nothing on the very population that priced it at 81 s. The saving funds the fix R4.146 needs.
* **STILL UNMEASURED, and named so it is not assumed**: whether any population other than
  `drift_bench` reaches the stall window with requests outstanding (a wider window would cost
  there), and whether the entry check holds on Gitea, where the retry path fires 0 times and the
  check is inert by construction rather than by measurement.
* **A PROBE THAT SPENDS MONEY REFUSES TO BE A NO-OP.** `inflight_probe` with no mode exits naming
  which one is free and which is paid, rather than running nothing and printing a clean summary --
  the same rule as `arm_oracles`, one instrument over.

## I set out to widen a window and deleted it instead (R4.146 FIXED, 0.169.0)

The plan was R4.146's entry check PLUS a wider stall window, with the saving paying for the width.
Half of that was right. **~$1.7** in acceptance reps, and the measurement changed the design twice.

* **THE ENTRY CHECK WORKS AND IS THE CHEAP HALF.** `BrowserSession` counts outstanding requests;
  the poll reads that count BEFORE it settles, and gives up after one look if nothing was in
  flight. `drift_bench`'s 36 stalling rows leave as `no-inflight` for **8.7 s instead of 23.4 s**
  and the bench goes **199.0 s -> ~187 s**. Note the projection was 23.4 s and the saving is
  **14.6 s**: the settle and the first look remain, deliberately, because they are R4.115's
  mechanism and it is about a page that painted during the SETTLE -- nothing to do with the network.
* **THE WIDENING DID NOT WORK.** At twelve looks `odoo-create-lead` was 5/6 with the SAME verdict,
  `already-quiet:stalled:1`, merely later -- 1235 ms against 734 ms at six. A third round of tuning
  the constant was available and would have been the wrong move.
* **SO THE GUARD WAS MEASURED INSTEAD, AND THE ANSWER WAS TO DELETE IT.** With the entry check in
  front, nothing reaches it: disabling it entirely measures **186.1 s against 187.0 s** -- identical
  -- with the retry path unchanged at 36 rows / 8.7 s. It cost nothing, saved nothing, and its only
  observed effect was cutting off renders that were merely slow, every successful bind landing at
  781-1109 ms. **Removing a mechanism makes its failure mode impossible by CONSTRUCTION, which is a
  stronger claim than the rate supports** -- 6/6 against 5/6 is p ~ 1.0 and proves nothing on its
  own. `settle_stall_looks` goes with it, and so do four cells and two mutations: a cell for a
  mechanism that no longer exists is an assertion about nothing.
* **ACCEPTANCE, STATED HONESTLY: the stalls are gone and the row is not green.** Six reps produced
  **no stall at any width** and 5 of 6 `true`; the single failure is `refused@0ms`, the
  top-of-function SAFETY guard, a different mechanism. Filed as R4.147 at n=1 and explicitly NOT
  claimed as fixed here -- and filed OPEN rather than folded into R4.146's closure, because nobody
  re-reads a closed finding. That is 0.157.0's lesson, where R4.115 sat `fixed` with two of three
  sites still open.
* **AND THE RATE IS NOT CLAIMED TO HAVE IMPROVED.** 5/6 now against 8/9 and 3/4 before is p ~ 1.0
  either way. What is established is mechanism, not rate: the failures were `stalled` and now none
  are.
* **A MUTATION SURVIVED THE REWRITE BECAUSE I COUNTED THE WRONG THING.** The beat's replacement cell
  counted RESOLVE calls -- but the spin lives in the INNER loop, which only calls `await_settled`,
  so `resolve` runs exactly once whatever the beat does. Counting settles kills it. Third time this
  slice a mutation survived because my model of the code was wrong rather than the suite weak, and
  the third time the fix was a better SENSOR rather than a better assertion.

## Odoo reached its ceiling, and the prediction that mattered was the ROW (0.170.0)

Three reps at 0.169.0, **$1.81**. Availability **0.619 -> 0.714**, std **0.082 -> 0.000**, per-rep
`[0.714, 0.714, 0.714]`, `varies` 0, `unstable` 0. Bought to see whether R4.146's stall-guard
removal -- closed on SOLO reps -- survives corpus context, which is the only population it was ever
about.

* **THE PREDICTION WAS WRITTEN DOWN AS A PAIR AND BOTH HALVES HELD.** 0.167.0's lesson was that I
  got the mean right and the mechanism wrong, so this time the ROW was the primary claim:
  `odoo-create-lead` passes 2 or 3 of 3, because the corpus/solo gap WAS the stall guard and the
  guard is gone. It went **3/3**, and the number followed from it. **A prediction whose number is
  right and whose row is wrong is a failure that reads as a success** -- checking only the headline
  would have scored 0.167.0 as a clean win.
* **THE RATE CLAIM IS AVAILABLE NOW BECAUSE THE POPULATION CHANGED, NOT BECAUSE THE EVIDENCE
  ACCUMULATED.** R4.147 says *do not claim the row improved* and is still right about SOLO reps --
  5/6 against 8/9 and 3/4 is p ~ 1.0, and solo was never the broken population. In the CORPUS the
  row is **1 of 9 across three prior versions against 3 of 3 now, Fisher p = 0.018**. The
  same-code comparison (0.165.0's 1/3 against 3/3) is **p = 0.4** and settles nothing alone. Quote
  whichever question is being asked and LABEL it: the pooled arm spans three different mechanisms
  underneath, so it answers *how often has this row ever passed a corpus* (once in nine) rather
  than *is this version better than the last*.
* **AND THE MECHANISM STORY IS AN INFERENCE, NOT A READING -- WHICH I ASSERTED AS CONCLUSIVE AND HAD
  TO WITHDRAW.** The draft said *every 0.165.0 corpus failure read `already-quiet:stalled:1`; no
  `stalled` verdict exists anywhere in this series*. Both halves are wrong. **No corpus run has ever
  recorded a poll verdict at all** -- `readiness_retry` is read only by `benchmarks/inflight_probe.py`,
  built at 0.168.0 -- so `stalled` appears zero times in ALL FOUR series, including 0.165.0 where the
  guard was live and firing. Its absence here is a property of the SCHEMA, not a result. The verdict
  exists in exactly one artifact, `odoo_create_lead_0165_solo_vs_corpus.json`, under `solo`, on one
  of four reps. **What is actually conclusive is a CODE fact**: the guard is deleted, so that failure
  mode is impossible by construction. The corpus series is CONSISTENT with the stall story and
  contains no evidence either way -- and `refused@0ms` (R4.147) emits the identical `DriftError`.
* **THE FALSIFIER I WAS WATCHING WAS THE OTHER ROWS, AND IT HELD -- ON OUTCOMES.** The entry check
  (R4.146) had only ever been measured against `drift_bench` and one scenario, never the corpus, so a
  previously-stable row breaking would have been a regression bought with an acceptance. No outcome
  moved. **An outcome is blind to AUTHORING, though**: `odoo-menu-nav`, the declared control group,
  learned recipes of **6 / 11 / 8 steps** where 0.158.0 got **6 / 6 / 6** -- the inverse of R4.139's
  signature (identical recipe, differing verdict) and not claimed here as a regression, because
  nothing separates it from ordinary learn variance the earlier series happened not to show. Recorded
  because a falsifier stated over outcomes cannot see it.
* **THREE CLEAN REPS IS NOT EVIDENCE R4.147 IS GONE, and the arithmetic says so**: at its observed
  1-in-6 SOLO rate, **P(0 sightings in 3) = 0.58**, so silence is the LIKELIER outcome for a fully
  live defect. Same for R4.140, unfired in six consecutive reps at a filed ~1/3 rate -- p = 0.088,
  suggestive, with no mechanism underneath it and the entry left open. **Buying reps to watch
  something not happen is not a measurement**; catch it under the probe instead.
* **THE CEILING IS ARITHMETIC AND IT MOVED FROM 4/7 TO 5/7.** Two rows still cannot pass, both
  STABLE in OUTCOME: `odoo-search` `no_actions_needed` 3/3 (R4.130 -- a corpus artifact, not a
  product failure) and `odoo-idempotent-replay` `refused_wrongly` **0 of 12 across four series**, now
  the ONLY row holding the write column. **Its outcome is 0/12 and its MECHANISM is not** -- all
  three 0.156.0 reps refused on the PRECISE branch's BIND (`target missing/ambiguous`) and the nine
  since read `form/section drift`, its SCOPE comparison. That is the outcome-vs-reason split `varies`
  exists for, and fusing them is what made me write *both fail the same way every time*.
  **2.4b is no longer blocked on stability** -- 0.133.0 refused Odoo for `varies` 5, and that is 0 --
  so what is left is a judgement about cutting a baseline over one corpus-design artifact and one
  undiagnosed refusal, plus R4.106's rule that `cost_usd` is the MAX observed.
* **AND THE PAGE THAT EXISTS TO NOT GO STALE HAD GONE STALE, plus two documents behind it.**
  `baselines/README.md` still read *0.181 +/- 0.203* and *58% of refusals are the mutation gate* --
  **four Odoo corpus series old** (0.151.0, 0.156.0, 0.158.0, 0.165.0 have run since) -- and
  `docs/reads-over-post.md` and `docs/reshape-plan.md` still assert both numbers in the present
  tense, the latter naming a dependency (D6) refuted at 0.139.0. Its machine-checked block caught
  nothing, correctly: the guard asserts every declared id is still OPEN, and every one was. **A
  caveat can rot without its finding closing**, which is the direction that test does not cover and
  prose has to.
* **THE FIX SENTENCE THAT WAS ITSELF WRONG, because it is the shape to watch for.** The rewrite said
  *neither is the gate: `over_gated/drift` went 4 -> 0 and has stayed there* -- true of the READ
  rows, and false as written: the mutation gate refuses `odoo-idempotent-replay` in **9 of 9 reps
  since 0.158.0**, and the same paragraph named that refusal as one of the two holds two lines later.
  **A staleness fix is a new claim and gets audited like one**; this one contradicted itself inside
  one paragraph, on the page whose whole job is not doing that.

## The pattern that predicts the next bug

Most defects found here are **a guard that already exists on a sibling path and was never applied to the
mechanism** — the replan path guards something the heal path doesn't; the recorder guards something `learn()`
doesn't; `heading`/`label` anchors were hardened and `row` wasn't. When you fix one, check its siblings.

## Measurement, not assertion

`benchmarks/drift_bench.py` (key-less, **~160s** on this host and 181s measured on CI windows -- the `~60s` this line claimed until 0.148.0 was stale by nearly 3x, and the 180s budget it implied sat inside the host's own variance band; the budget is 220s, re-derived from measurement, CI-gated) is the instrument for any change to `locators.resolve`
or the recovery ladder. It reports a 0-LLM survival curve by mutation intensity, per-tier recovery rates, and
`silent_wrong` — which must stay within its published allowlist. Use it to *adjudicate* a resolver trade
rather than argue it; that is what it was built for. `baselines/README.md` states plainly what each number
does and does not prove — keep that honesty when adding to it.
