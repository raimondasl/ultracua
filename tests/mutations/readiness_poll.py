"""Mutations for R4.144 -- the replay looking more than once for a page that has not painted.

    uv run --no-sync python scripts/prove_red.py tests/mutations/readiness_poll.py

The mutations pull in BOTH directions, and the inert ones are the dangerous half. Break the poll and
Odoo's write row goes back to `refused_wrongly` (the measured defect). Break the REFUSAL guard and a
poll waits a competing candidate out into a wrong-record bind -- which R4.115 measured, and which no
cell about the poll's success can see. Break the `already-quiet` skip and every static row pays for a
wait that can find nothing (42 retries, 10.1 s on drift_bench).

Each entry is `(id, path-under-src/ultracua, find, replace, why)`. A `find` that no longer matches is
an ERROR rather than a survivor: a stale mutation reports the suite as stronger than it is.
"""

KILLED_BY = ["tests/test_readiness_poll.py", "tests/test_learn_settle.py"]

MUTANTS = [
    ('the_replay_looks_only_once', "flow.py",
     "        if time.monotonic() >= deadline:",
     "        if True:  # MUTANT",
     "THE DEFECT, restored: one look and no more. Measured in a real replay -- look 1 lands in the "
     "quiet gap between an onchange POST and the asset bundle that follows it, and the target only "
     "appears at look 5. `odoo-create-lead` was `refused_wrongly` and is `true` 3/3 with the poll. "
     "Killed by test_the_target_that_appears_after_several_quiet_gaps_IS_found."),

    ('the_poll_waits_a_REFUSAL_out', "flow.py",
     "        if s.get(\"saw_candidates\"):\n"
     "            # THE PAGE ANSWERED THIS TIME. A refusal, not an unpainted page -- and waiting on a\n"
     "            # refusal is precisely what bound the wrong record when this was keyed on `None`.\n"
     "            tr.meta[tag + \"readiness_retry\"] = f\"{why}:refused-at:{looks}\"\n"
     "            return None",
     "        pass  # MUTANT: keep polling even once the page has answered",
     "THE SAFETY DIRECTION, and every cell about the poll BINDING still passes under it. R4.115 "
     "measured this exact remedy binding `/cancel/30` where `/cancel/3` was recorded, by waiting for "
     "a competing row to disappear. A poll must never make a refusal more likely to pass. The "
     "top-of-function guard does NOT cover this -- that cell enters before the loop. Killed by "
     "test_a_refusal_that_APPEARS_MID_POLL_stops_it_too."),

    ('a_refusal_reaches_the_poll_at_all', "flow.py",
     '        tr.meta[tag + "readiness_retry"] = "refused"\n        return None',
     '        tr.meta[tag + "readiness_retry"] = "refused"  # MUTANT: fall through',
     "The same hazard through the FIRST door rather than a later look. The guard at the top is what "
     "makes a refusal cost zero milliseconds; without it every ambiguous page pays the full budget "
     "before failing, and the refusal has been waited on. Killed by "
     "test_a_REFUSAL_stops_at_once_and_is_never_waited_out."),

    ('the_already_quiet_skip_is_spent', "flow.py",
     '        tr.meta[tag + "readiness_retry"] = "already-quiet:skipped"\n        return None',
     '        pass  # MUTANT: poll a page that was already quiet when we asked',
     "The COST direction. A page quiet BEFORE the failing resolve has not changed since it, so the "
     "answer is provably the same -- measured at 42 such retries costing 10.1 s on drift_bench's "
     "static corpus, 6.1% of a run whose budget this had already pushed past on CI. Every cell about "
     "the poll finding its target still passes. Killed by "
     "test_a_page_ALREADY_QUIET_when_asked_still_skips_entirely."),

    ('the_poll_is_unbounded', "flow.py",
     "    deadline = time.monotonic() + settings.settle_cap_ms / 1000.0",
     "    deadline = time.monotonic() + 3600.0  # MUTANT",
     "A page that never stops painting -- an animation, a live ticker -- would hold the replay open "
     "indefinitely. `settle_cap_ms` is the same budget `await_settled` already respects, so the "
     "failure direction stays 'no better than before, just later'. Killed by "
     "test_a_target_that_never_appears_is_BOUNDED."),

    ('the_beat_between_looks_is_removed', "flow.py",
     "        await page.wait_for_timeout(settings.settle_poll_ms)",
     "        pass  # MUTANT: spin",
     "Without the beat the loop spins the resolver ladder as fast as the event loop allows for the "
     "whole budget, because the next `await_settled` returns `already-quiet` instantly on a page "
     "between network-gated stages. It still BINDS -- every success cell passes -- so only the "
     "budget cell could see it -- and it could not. With the stall guard the loop exits after six "
     "looks either way, so a LOOK COUNT is blind too; what the beat buys is TIME (six quiet "
     "looks span ~320 ms with it and ~18 ms without), and only a page that goes still and THEN "
     "produces the target can see that. Killed by "
     "test_the_beat_bounds_how_OFTEN_the_poll_asks."),

    ('the_happy_path_pays_for_the_poll', "flow.py",
     "    if loc is not None:\n        return loc",
     "    if False:\n        return loc  # MUTANT",
     "The population that matters most: on a server-rendered substrate every resolve binds first "
     "try, and this path fired 0 times across three Gitea scenarios. Making a bound locator fall "
     "through re-resolves it on every step of every replay, which is pure tax on the 0-LLM speed "
     "claim. Killed by test_a_resolve_that_SUCCEEDED_never_enters_the_helper_at_all."),
    ('the_entry_check_never_fires', "flow.py",
     '        if not busy_at_entry:',
     "        if False:  # MUTANT: poll even a page that was not mid-fetch",
     "THE COST FIX, removed. drift_bench's 36 stalling rows read ZERO outstanding at entry and "
     "never rise, and without this check each pays the full stall window -- 23.4 s between them, "
     "which is what made widening the window unaffordable in the first place. Every cell about "
     "the poll BINDING still passes. Killed by "
     "test_nothing_in_flight_at_entry_gives_up_after_the_FIRST_look."),

    ('the_entry_reading_is_taken_AFTER_the_settle', "flow.py",
     '    busy_at_entry = BrowserSession.inflight(page)',
     "    busy_at_entry = 0  # MUTANT: read nothing",
     "The reading itself. Hard-coding zero makes the check fire on EVERY page, so a render that "
     "was mid-fetch is abandoned after one look -- R4.146's defect, restored through the new "
     "mechanism rather than the old one. Killed by "
     "test_something_in_flight_at_entry_is_what_LETS_it_poll."),
]
