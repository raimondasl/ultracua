"""Mutations of the run-record wiring, re-expressed for THE SINK (reshape-plan 1.5).

R4.48 measured eleven mutations of the OLD plumbing surviving the entire suite, because only two cells
passed `record=` and eight of the ten write sites were never reached. Step 0.3's matrix killed all
eleven; six more were added at R4.59 and at 1.5's first step, reaching seventeen.

**All seventeen went STALE the moment the sink landed, and `prove_red` reported them as ERRORS rather
than as survivors** — which is the rule that file exists for. Their find-texts named sites that no
longer exist: three helpers whose whole job was to UNDO an earlier write, a pre-stamp that had to be
cleared, a population block that ran on one path. The class they measured has not gone away, though. It
has MOVED: the questions are now "does every path APPEND its fact?" and "does `finish()` fold them
correctly?", and a mutation of either is exactly as invisible to a per-scenario test as the old ones
were.

So these are rewritten against the new shape rather than deleted. Each entry is a source transformation
applied to a SCRATCH COPY of `src/ultracua/` — never the tree under test — plus the reason it is a real
defect if it survives. A mutation no cell kills is not a bug in the mutation: it is a hole in the
matrix, and it must be listed in KNOWN_SURVIVORS with a reason and a register id.
"""

from __future__ import annotations

# (id, file, find, replace, why-it-matters)
MUTANTS = [
    ('evidence_unknown_becomes_false', "flows.py",
     '        if not self._facts or any(getattr(f, field_name) is None for f in self._facts):\n            return None',
     '        if False:  # MUTANT: a fact that does not know answers the write question "no"\n            return None',
     'a run in which an attempt RAISED would report `landed=False` — the confident denial over a write that may have committed, which is the one error direction nothing downstream catches'),

    ('evidence_true_does_not_win', "flows.py",
     '        if any(getattr(f, field_name) is True for f in self._facts):\n            return True',
     '        if False:  # MUTANT: evidenced-landed no longer wins\n            return True',
     'a write evidenced as landed by one attempt would be un-landed by a later attempt failing earlier, and the ledger would skip a row that really was paid'),

    ('no_raised_fact', "flows.py",
     '    except BaseException:\n        _append(outcome="raised", mode="raised")\n        raise',
     '    except BaseException:\n        raise  # MUTANT: a raised attempt leaves no trace',
     'M4: `run_cached` can raise AFTER the commit POSTed; without the fact the run reports a confident False rather than unknown, and the attempt vanishes from `attempts`'),

    ('no_precheck_fact', "flows.py",
     '        sink.attempt(_AttemptFacts(outcome="precheck", mode="precheck"))\n'
     '        _record_run(cache, key, ok=True)',
     '        pass  # MUTANT: the idempotency skip is not recorded\n'
     '        _record_run(cache, key, ok=True)',
     'an idempotency skip is evidence about an EARLIER run; without the fact the record answers the write question False for a flow whose write demonstrably already happened'),

    ('no_relearn_fact', "flows.py",
     '            rep = res.report\n            sink.attempt(_AttemptFacts(',
     '            rep = res.report\n            _mutant_dropped = (_AttemptFacts(',
     'R4.50: the re-author is the largest spend in the run and its calls, traces, heals and duration would be dropped while its dollars were kept'),

    ('no_relearn_raised_fact', "flows.py",
     '                sink.attempt(_AttemptFacts(outcome="relearn_raised", mode="raised"))\n                raise',
     '                raise  # MUTANT: a provider 500 mid-authoring leaves no trace',
     'F2: a relearn that raised would report landed=False on a run whose write state is unknown'),

    ('no_auth_refreshed_flag', "flows.py",
     '                sink.auth_refreshed()',
     '                pass  # MUTANT: the refresh is not recorded',
     'G10: a caller cannot tell a clean run from one that had to re-authenticate'),

    ('failure_code_from_the_internal_kind', "flows.py",
     '        failure_code = "" if exc is None else (getattr(exc, "code", "") or "raised")',
     '        failure_code = "" if exc is None else "replay_error"  # MUTANT: one flat code',
     'R4.49: the record and the exception would describe one run in two vocabularies again'),

    ('ok_not_derived_from_the_exit', "flows.py",
     '        record.ok = exc is None',
     '        record.ok = bool(self._facts) and self._facts[-1].outcome == "ok"  # MUTANT',
     "R4.57's shape: ok would follow the last ATTEMPT rather than the run, so a precheck skip or a relearn success would report the failed attempt's verdict"),

    ('finish_is_not_total', "flows.py",
     '        except BaseException as inner:  # noqa: BLE001 - a record must never replace the real outcome',
     '        except BaseException:\n            raise  # MUTANT: the diagnostic replaces the outcome\n        except BaseException as inner:',
     "finish() runs in replay()'s except arm, so its own failure would REPLACE the exception the caller is being told about"),

    # ---- the four guarantees the two adversarial audits of 1.5 added. Each has a cell; each mutant
    # ---- is what proves the cell is the thing keeping it.
    ('relearn_success_is_answerable', "flows.py",
     '                outcome="relearn", mode="relearn",',
     '                outcome="relearn", mode="relearn", landed=False, committed=False,  # MUTANT',
     "a relearn is a live authoring run that CAN actuate a write (LearnResult.performed_write exists "
     "for it), so folding a completed relearn as answerable-and-no is a confident denial — the HIGH "
     "regression the first draft of the sink shipped"),

    ('cross_check_is_max_not_sum', "flows.py",
     '        blind = any(sum((r.get(k) or 0) for r in self._reported_usage) > (usage.get(k) or 0)\n'
     '                    for k in ("calls", "input_tokens", "output_tokens"))',
     '        blind = any((r.get(k) or 0) > (usage.get(k) or 0)  # MUTANT: per-attempt, not summed\n'
     '                    for r in self._reported_usage\n'
     '                    for k in ("calls", "input_tokens", "output_tokens"))',
     "with two attempts spending comparably neither exceeds the run total alone, so a half-blind run "
     "reports a CONFIDENT, UNDERSTATED bill — the failure this accounting exists to prevent, missed by "
     "the check written to prevent it"),

    ('note_is_not_cleared', "flows.py",
     '        record.note = ""',
     '        pass  # MUTANT: a stale note survives onto a healthy record',
     "`note` is the one field nothing else writes, so a record reused after a broken fold would carry "
     "the old failure onto a healthy run — a site that has to remember to clear"),

    ('broken_fold_leaves_usage_empty', "flows.py",
     '                if not record.usage:\n'
     '                    record.usage = {"cost_usd": None, "unobserved_llm_path": True}',
     '                pass  # MUTANT: a half-written record keeps the empty-dict default',
     "R4.45's own shape: a record whose cost was never computed would carry `{}`, against RunRecord's "
     "promise that usage is always populated and always carries cost_usd"),

    ('no_watch_cross_check', "flows.py",
     '        if blind or any(r.get("unobserved_llm_path") for r in self._reported_usage):',
     '        if False:  # MUTANT: a blind watch reports a confident zero',
     'if the engine reached a router behind neither owner, the run would claim a priced zero over real spend'),

    ('carried_no_total_ms', "flows.py",
     '        total_ms=report.total_ms,',
     '        total_ms=0.0,  # MUTANT',
     'a failed run would report 0 ms and a caller timing the fleet would see free failures'),

    ('carried_no_traces', "flows.py",
     '        traces=tuple(report.traces),',
     '        traces=(),  # MUTANT',
     'a caller diagnosing a FAILURE is exactly who needs the traces, and would get none'),

    ('carried_no_llm_calls', "flows.py",
     '        llm_calls=report.llm_calls,',
     '        llm_calls=0,  # MUTANT',
     'a healed or replanned run would report 0 LLM calls beside a usage showing spend'),

    ('carried_no_healed_steps', "flows.py",
     '        healed_steps=report.healed_steps,',
     '        healed_steps=0,  # MUTANT',
     'a run that healed would look like a clean 0-LLM replay in the record'),

    # RESTORED. `main` registered a mutant for this field; the 1.5 rewrite dropped it — and it is the
    # one field of the five with write-safety significance, dropped in the same commit whose raise-path
    # regression also lost it. Found by an audit reading the registry against its predecessor.
    ('carried_no_idempotency_keys', "flows.py",
     '        idempotency_keys=tuple(\n            tr.meta["idempotency_key"] for tr in report.traces',
     '        idempotency_keys=(),  # MUTANT\n        _mutant_dropped=tuple(\n            tr.meta["idempotency_key"] for tr in report.traces',
     "the keys a resume must re-use would be lost on the failing run that most needs them"),

    # REUSE. No mutation touched it before the audit round — a change to the reuse behaviour survived
    # the whole registry, which is how the accumulate -> overwrite change went unnoticed in the first
    # place. Two mutants: the counter that makes reuse DETECTABLE, and the fold order that makes a
    # partial write leave an earlier call's data behind.
    ('replay_calls_never_increments', "flows.py",
     '        replay_calls = (record.replay_calls or 0) + 1',
     '        replay_calls = 1  # MUTANT: reuse becomes undetectable',
     "a caller that reused one record by accident reads only the last call's facts with nothing saying "
     "so, and B3's record_disagrees bucket loses the field it keys on"),

    ('write_is_not_atomic', "flows.py",
     '        usage = self._usage()\n',
     '        usage = self._usage()\n'
     '        record.usage = usage  # MUTANT: assigned before the fold completes\n',
     "a fold that fails partway would leave a record half this run and half the last — on a REUSED "
     "record that is an earlier call's landed=True beside this call's verdict, i.e. a false arm"),

    ('attempts_counts_everything', "flows.py",
     '        attempts = sum(1 for f in self._facts if f.outcome in _ENGINE_OUTCOMES)',
     '        attempts = len(self._facts)  # MUTANT',
     'attempts has always meant ENGINE attempts; a precheck skip counting as one would make a run that never touched the engine report that it did'),


    # ---- 1.4b: R4.52's row projection and R4.61's unanswerable write. Each has a cell; each mutant
    # ---- is what proves the cell is the thing keeping it.
    ('write_unverified_denies_its_own_commit', "flows.py",
     '            committed_report = None\n            return _fail(',
     '            committed_report = committed  # MUTANT: R4.61 reintroduced\n            return _fail(',
     "R4.61 exactly: the one kind whose meaning is 'the commit FIRED and cannot be confirmed' records a "
     "confident committed=False. Nothing downstream catches a denial, and an operator's tooling reads "
     "the record rather than the message"),

    ('row_projects_the_arming_token', "flows.py",
     '    if record.committed is True:\n'
     '        return True\n'
     '    if record.committed is None and record.attempts == 0:\n'
     '        return False\n'
     '    return record.committed',
     '    if record.landed is True:  # MUTANT: the row projects the SKIP question\n'
     '        return True\n'
     '    if record.landed is None and record.attempts == 0:\n'
     '        return False\n'
     '    return record.landed',
     "landed asks 'may a resume SKIP this whole row' and needs EVERY recipe write ok; the row's "
     "question is 'may this row's write have committed'. Projecting the wrong one prints false beside "
     "an error string saying the write DID commit"),

    ('row_denial_rests_on_a_declaration', "flows.py",
     '    if record.committed is None and record.attempts == 0:',
     '    if record.committed is None and outcome is not None and not outcome.armed:  # MUTANT',
     "lowering an unknown to a confident DENIAL must rest on a measurement (did any engine attempt "
     "run) and not on a class attribute: R4.74 records that can_follow_actuation has no sensor and "
     "that two of its declarations were measurably wrong when checked by hand"),

    ('row_never_ran_allowlist_denies_everything', "flows.py",
     '        return False if status in _ROW_NEVER_RAN else None',
     '        return False  # MUTANT: every recordless row denies the commit',
     "QUIET IS AN ALLOWLIST. A row status nobody has argued into _ROW_NEVER_RAN must read UNKNOWN, so "
     "a status added tomorrow cannot silently deny a write it knows nothing about"),

    # ---- 1.4b's audit: the ARM, collapsed through a local variable. The AST pin that first guarded
    # ---- this was GREEN over exactly this rewrite, because the needle stays in the guard and the
    # ---- banned symbols sit one statement above it. A text scan is the wrong sensor for a behaviour.
    ('ledger_arm_reads_the_tri_state_report', "flows.py",
     '                if (getattr(exc, "landed", False) and ledger is not None and preview_keys[i]):',
     '                o_arm = outcome_of(exc)  # MUTANT: the report reaches the two-state arm\n'
     '                if (_row_write_evidence(rec, "failed", o_arm) is not False\n'
     '                        and ledger is not None and preview_keys[i]):',
     "`_row_write_evidence` returns None for `write_unverified`, and `None is not False`. The arm "
     "fires, a durable commit line is written for a row nothing confirmed was paid, and every later "
     "resume of that job SKIPS it. ledger.py's invariant verbatim: never a false skip of an un-landed "
     "write. Killed by test_landed_arms_the_ledger's driven per-class property, NOT by any text scan"),
]

# Mutants no cell kills yet. Each needs a reason and, where it is a real gap, a register id — an empty
# dict is the goal, and a silent absence is what this file exists to prevent.
KNOWN_SURVIVORS: dict = {}
