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
  3. *Docker*: **this host has a daemon and CI does not.** Added at 0.121.0 with R4.85's fourth
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
