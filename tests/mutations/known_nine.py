"""The NINE known mutants — one per defect class this project has actually shipped.

WHY THIS FILE EXISTS AT ALL. These nine were applied by hand at 0.75.0, each in its own git worktree,
each run against the whole suite, and **all nine were caught**. That measurement is quoted in
`CLAUDE.md`, in `docs/correctness-survey.md` (PROCESS-4) and in `docs/reshape-plan.md`'s acceptance
column — and until this file it existed only as PROSE. Nothing could re-run it. That is the exact
shape `scripts/prove_red.py`'s own docstring refuses in the small ("a stale mutation silently reports
the suite as stronger than it is"), one instrument out: a 9/9 score nobody can reproduce is a claim,
and this repo's rule is that a claim nobody re-measures is how a green instrument stays green while
covering less.

WHAT THE NINE ARE. Verbatim from the 0.75.0 record, in its order: the R3.2 refusal removed, the
wire-vs-classifier check dropped, `cache.get` failing open, the row-identity discrimination test
removed, the Idempotency-Key dropped on replay, the precondition gate disabled, `is_write_request`
blinded, MCP exposing unapproved flows, the promotion loop's silent drop.

THEY ARE RE-EXPRESSED, NOT COPIED. Six of the nine sites have moved since 0.75.0 — the R3.2 refusal
migrated from `flows.py` into `flow._learn` (so it covers `ultracua run` and the daemon, which never
reach the `flows.py` surface), the row-identity check moved behind `resolve`'s single funnel, and the
promotion loop grew its attributed-but-failed arm. Each mutation below states the property, not the
2026-06 line number. A find-text that no longer matches is an ERROR here, which is what forces the
re-expression rather than letting the nine quietly rot into nine no-ops.

WHY THEIR KILLERS ARE MOSTLY BROWSER TESTS, and why that is the whole reason this registry runs in the
weekly sweep rather than in the per-PR `red-proof` job. Seven of the nine are page-side properties: a
gate that reads a live fingerprint, a header that must ride a real request, a row bound against a real
DOM. `red-proof` installs no Playwright deliberately (a killer-suite leg with a browser cell fails
EVERY mutant's baseline — measured on CI, 8 failed / 135 passed on both arms, while green locally), so
these could not live there. The sweep installs Chromium, which is the one thing it can afford that a
merge-gate job cannot. `scripts/mutation_sweep.py` DERIVES which side a registry lands on from the tier
manifest rather than taking a declaration, so this file cannot be put in the wrong job by hand.

(id, module-relative path, find, replace, why it must not survive, killer suite)
"""

# The registry's default killer suite. Every mutant below overrides it, because the whole point of
# nine unrelated defect classes is that no single file guards them — but a default has to exist or a
# tenth mutant added without one would silently inherit `prove_red`'s exit-matrix default and be
# scored against a suite that cannot see it.
KILLED_BY = ["tests/test_unattributed_write.py"]

MUTANTS = [
    # ------------------------------------------------------------------ 1. the R3.2 refusal removed
    (
        "the_r3_2_refusal_never_fires",
        "flow.py",
        '        if write_unattributed:\n            success = False\n',
        '        if False:\n            success = False\n',
        "a write fired on the wire that no step could be attributed to, and the flow CACHES anyway. "
        "It then replays ungated, un-keyed and with no precondition -- or with all three attached to "
        "a step that never writes. This is inviolable #3 and R3.2's entire harm class. Note the site: "
        "the refusal lives in `flow._learn`, NOT on the `flows.py` surface, because `ultracua run` and "
        "the daemon call `run_cached` directly and a guard living only there protected one of three "
        "callers.",
        ["tests/test_unattributed_write.py"],
    ),
    # ------------------------------------------- 2. the wire-vs-classifier disagreement check dropped
    (
        "the_wire_and_the_classifier_are_never_compared",
        "flow.py",
        "        if not gated or not (acting_at_write & gated):\n",
        "        if False:\n",
        "the keyword classifier says the write is on step A, the wire says it fired while step B was "
        "in flight, and nothing notices. The recipe caches the gate, the precondition and the "
        "Idempotency-Key on a step that never writes while the real commit replays with none of them. "
        "The classifier is a GUESS (D0) and this comparison is the only place the guess is checked "
        "against evidence.",
        ["tests/test_unattributed_write.py"],
    ),
    # ------------------------------------------------------------------ 3. `cache.get` failing open
    (
        "an_unreadable_cached_flow_reads_as_not_learned",
        "cache.py",
        "        if io_error is not None:\n            raise CacheUnreadableError(",
        "        if io_error is not None:\n            return None\n"
        "        if io_error is not None:\n            raise CacheUnreadableError(",
        "'the file could not be read right now' collapses into 'this was never learned', which is a "
        "fail-OPEN: `is_write_flow(spec, cache.get(key))` returns False for None, so an undeclared "
        "write runs on the scheduled fleet with no gate. The fault is real on Windows (AV/indexer "
        "sharing violations on this same directory), which is why the retry loop above it exists.",
        ["tests/test_cache_unreadable.py"],
    ),
    # ----------------------------------------------- 4. the row-identity discrimination test removed
    (
        "the_bound_row_is_never_checked_against_the_recorded_one",
        "locators.py",
        "    if got == spec.anchor_id:\n        return loc\n",
        "    if True:\n        return loc\n",
        "a row anchor binds to WHATEVER row the resolver reached and the recorded identity is never "
        "compared. Acting on the wrong record is the silent-wrong this whole anchor exists to "
        "prevent, and it is inviolable #2. R3.7 is the residual that survives WITH this check; "
        "without it there is no check at all.",
        ["tests/test_row_identity_binding.py"],
    ),
    # ------------------------------------------------------- 5. the Idempotency-Key dropped on replay
    (
        "the_write_replays_with_no_idempotency_key",
        "flow.py",
        '        await session.set_transient_headers({"Idempotency-Key": key})',
        "        await session.set_transient_headers({})",
        "the key is still MINTED and still recorded in the trace -- so the ledger, the dry-run report "
        "and `preflight_keys` all keep showing a key that no request ever carried. A retry of a row "
        "then double-submits against a backend whose dedupe was the thing making the retry safe. "
        "Inviolable #3, and the mutation is deliberately the SILENT half: nothing observable changes "
        "except the wire.",
        ["tests/test_press_gate.py"],
    ),
    # ------------------------------------------------------------- 6. the precondition gate disabled
    (
        "the_mutation_gate_never_runs",
        "flow.py",
        '    if step.mutating:\n        drifted, reason = False, ""\n',
        '    if False:\n        drifted, reason = False, ""\n',
        "a mutating step blind-replays under arbitrary page drift: no scope check, no fingerprint "
        "check, no fail-loud. This is the single largest write-safety guard in the engine, and it "
        "takes the Idempotency-Key with it (minted inside the same block), so the mutation is a "
        "superset of #5 -- which is why they carry DIFFERENT killers and are not folded into one.",
        ["tests/test_press_gate.py"],
    ),
    # ------------------------------------------------------------------- 7. `is_write_request` blinded
    (
        "is_write_request_sees_no_writes",
        "safety.py",
        '    return method.upper() in ("POST", "PUT", "PATCH", "DELETE") and not is_telemetry_host(url)',
        "    return False",
        "the wire signal goes dark everywhere at once -- the learn-time attribution, the "
        "unattributed-write refusal, the dry-run arbiter and the recorder all stop seeing writes. "
        "It is the one input that is EVIDENCE rather than a guess, and the keyword classifier it "
        "backstops is measured at 45% recall on genuine writes.",
        ["tests/test_safety.py"],
    ),
    # ------------------------------------------------------------ 8. MCP exposing unapproved flows
    (
        "mcp_advertises_an_unapproved_flow",
        "mcpserver/server.py",
        '    if not health.approved:\n        return SkippedFlow(spec_name, "not_approved",',
        '    if False:\n        return SkippedFlow(spec_name, "not_approved",',
        "an outside agent -- Claude, Cursor, an IDE -- is handed a tool for a flow no human ever "
        "reviewed. The server's stated contract is that it only RUNS already-approved flows and can "
        "never approve or author one; this removes the half that makes that true at `tools/list`.",
        ["tests/test_mcp_listing_visibility.py"],
    ),
    # --------------------------------------------------------- 9. the promotion loop's silent drop
    (
        "an_attributed_write_whose_step_failed_is_dropped",
        "flow.py",
        '            unattributed["hit"] = True\n'
        '            _log.warning("learn: a wire write was attributed to step %d',
        '            _log.warning("learn: a wire write was attributed to step %d',
        "a write that WAS positively attributed -- to a step whose act then failed, so no row exists "
        "to carry its gate -- is silently continued past. The flow caches without it and replay fires "
        "it ungated, un-keyed and with no precondition. It is exactly as unattributable as one nobody "
        "claimed, and the `continue` on the next line is what makes the drop silent rather than loud.",
        ["tests/test_unattributed_write.py"],
    ),
]
