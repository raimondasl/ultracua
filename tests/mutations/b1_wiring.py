"""The eleven record-plumbing mutations R4.48 measured as surviving the ENTIRE suite, plus six
added since — two at R4.59 and four at 1.5's first step.

Each entry is a source transformation applied to a SCRATCH COPY of `src/ultracua/` — never the tree
under test — plus the reason it is a real defect if it survives. `scripts/prove_red.py` applies them one
at a time and reports which are killed.

This is the number that decides whether `tests/test_replay_exit_matrix.py` is worth having: before it,
all eleven survived 1100+ tests, because only two cells in the whole suite passed `record=` to `replay()`
and eight of the ten write sites were never reached.

A mutation that no cell kills is not a bug in the mutation — it is a hole in the matrix, and it must be
listed in KNOWN_SURVIVORS with a reason and a register id, so the gap is named rather than absent.
"""

from __future__ import annotations

# (id, file, find, replace, why-it-matters)
MUTANTS = [
    ("mark_ok_precheck", "flows.py",
     '        _mark_ok(record)          # M3: a success return that never enters _attempt_replay',
     '        pass  # MUTANT: _mark_ok removed from the first precheck exit',
     "the idempotency-precheck success would report ok=False with a stale failure_code (B1's M3)"),

    ("mark_ok_post_refresh", "flows.py",
     '                    _mark_ok(record)      # M3: as above, on the post-auth-refresh precheck',
     '                    pass  # MUTANT: _mark_ok removed from the post-refresh precheck',
     "a write already done, discovered after re-login, would report as a failure"),

    ("mark_ok_relearn", "flows.py",
     '                _mark_ok(record)      # M3: clears the failed attempts\' ok=False + failure_code',
     '                pass  # MUTANT: _mark_ok removed from the relearn success',
     "a relearn that produced the answer would still report the earlier attempts' failure"),

    ("absorb_relearn_success", "flows.py",
     '                _absorb_usage(record, _relearn_watch.as_dict(settings.model))\n'
     '                record.mode = "relearn"',
     '                record.mode = "relearn"  # MUTANT: the relearn spend is not absorbed',
     "the largest spend in the run (a full re-author) would be missing from the bill (B1's M2)"),

    ("absorb_relearn_raise", "flows.py",
     '                    _absorb_usage(record, _relearn_watch.as_dict(settings.model))\n'
     '                    record.mode = "raised"',
     '                    record.mode = "raised"  # MUTANT: spend lost when learn() raises',
     "a provider 500 mid-authoring would report the earlier attempts' cents against real dollars (F2)"),

    ("auth_refreshed_flag", "flows.py",
     '                    record.auth_refreshed = True     # G10: previously a log line and nothing else',
     '                    pass  # MUTANT: the refresh is not recorded',
     "a caller cannot tell a clean run from one that had to re-authenticate"),

    ("pre_stamp_mode_raised", "flows.py",
     '        record.attempts += 1\n        record.mode = "raised"',
     '        record.attempts += 1  # MUTANT: the pre-stamp no longer marks the run unknown',
     "a raise mid-attempt would leave a CONFIDENT DENIAL about a write that may have committed (M4)"),

    ("pre_stamp_attempts", "flows.py",
     '        record.attempts += 1\n        record.mode = "raised"\n'
     '        _forget_negative_write_evidence(record)',
     '        record.mode = "raised"\n        _forget_negative_write_evidence(record)'
     '  # MUTANT: attempts not counted',
     "a multi-attempt run would look like a single attempt, understating what it did"),

    ("forget_negative_evidence", "flows.py",
     '        record.ok, record.failure_code = True, ""\n        _forget_negative_write_evidence(record)',
     '        record.ok, record.failure_code = True, ""'
     '  # MUTANT: _mark_ok no longer clears negative write evidence',
     "a `False` meaning 'that attempt did not confirm' would be read as 'no write happened' (F3/F4)"),

    ("mode_last_attempt", "flows.py",
     '        record.mode = report.mode                       # last attempt wins: it is the outcome',
     '        pass  # MUTANT: the record never learns which path produced the outcome',
     "the record would keep the pre-stamp's 'raised' for a run that completed normally"),

    ("llm_calls_accumulate", "flows.py",
     '        record.llm_calls += report.llm_calls',
     '        pass  # MUTANT: llm_calls never accumulates',
     "a healed or replanned run would report 0 LLM calls beside a usage showing spend"),

    # R4.59, added at 1.5's first step. These two are not record-PLUMBING like the eleven above; they
    # are the confident-zero coercion itself, registered because the cell that was supposed to forbid
    # it (R4.51's) had been asserting the OPPOSITE, strictly, as a standing demand that someone
    # implement it. Both were armed by hand and both now die.
    ("cost_sticky_none_merge", "flows.py",
     '    dst["cost_usd"] = None if (a is None or b is None) else round(a + b, 6)',
     '    dst["cost_usd"] = round((a or 0) + (b or 0), 6)  # MUTANT: an unknown attempt sums as free',
     "a priced attempt merged with an unobserved one would report a confident partial sum as the total"),

    ("cost_passthrough_coercion", "flows.py",
     '        record.usage = dict(usage)\n        return',
     '        record.usage = dict(usage, cost_usd=usage.get("cost_usd") or 0.0)  # MUTANT\n        return',
     "a single-attempt run whose engine reported UNKNOWN would claim a priced zero"),

    # R4.47's class, added at 1.5's first step. The population block writes six fields on EVERY
    # attempt, failed or not, and only the usage absorb was covered: these four survived the whole
    # matrix until `test_the_population_block_reaches_the_record_on_a_FAILED_run` was written. The
    # helper could not even express a duration — `FlowReport.total_ms` derives from its traces and
    # `StepTrace.total_ms` from its spans, so with no cell scripting a span every report measured 0.0.
    ("population_no_total_ms", "flows.py",
     "        record.total_ms += report.total_ms",
     "        pass  # MUTANT: the run's duration never reaches the record",
     "a failed run would report 0 ms, and a caller timing the fleet would see free failures"),

    ("population_no_traces", "flows.py",
     "        record.traces.extend(report.traces)",
     "        pass  # MUTANT: the traces never reach the record",
     "a caller diagnosing a FAILURE is exactly who needs the traces, and would get none"),

    ("population_no_healed_steps", "flows.py",
     "        record.healed_steps += report.healed_steps",
     "        pass  # MUTANT: heals are not counted",
     "a run that healed would look like a clean 0-LLM replay in the record"),

    ("population_no_idempotency_keys", "flows.py",
     "        record.idempotency_keys.extend(",
     "        _mutant_ignored = (",
     "the keys a resume must re-use would be lost on the failing run that most needs them"),
]

# Mutants no cell kills yet. Each needs a reason and, where it is a real gap, a register id — an empty
# list is the goal, and a silent absence is what this file exists to prevent.
KNOWN_SURVIVORS: dict = {}
