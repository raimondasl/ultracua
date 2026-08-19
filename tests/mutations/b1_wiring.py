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
     '        if not self._facts or any(f.outcome in _UNKNOWN_OUTCOMES for f in self._facts):\n            return None',
     '        if False:  # MUTANT: an unknown outcome answers the write question "no"\n            return None',
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
     '        record.failure_code = "" if exc is None else (getattr(exc, "code", "") or "raised")',
     '        record.failure_code = "" if exc is None else "replay_error"  # MUTANT: one flat code',
     'R4.49: the record and the exception would describe one run in two vocabularies again'),

    ('ok_not_derived_from_the_exit', "flows.py",
     '        record.ok = exc is None',
     '        record.ok = bool(self._facts) and self._facts[-1].outcome == "ok"  # MUTANT',
     "R4.57's shape: ok would follow the last ATTEMPT rather than the run, so a precheck skip or a relearn success would report the failed attempt's verdict"),

    ('finish_is_not_total', "flows.py",
     '        except BaseException as inner:  # noqa: BLE001 - a record must never replace the real outcome\n            record.note = f"the run record could not be completed: {type(inner).__name__}: {inner}"',
     '        except BaseException:\n            raise  # MUTANT: the diagnostic replaces the outcome',
     "finish() runs in replay()'s except arm, so its own failure would REPLACE the exception the caller is being told about"),

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

    ('attempts_counts_everything', "flows.py",
     '        record.attempts = sum(1 for f in self._facts if f.outcome in _ENGINE_OUTCOMES)',
     '        record.attempts = len(self._facts)  # MUTANT',
     'attempts has always meant ENGINE attempts; a precheck skip counting as one would make a run that never touched the engine report that it did'),

]

# Mutants no cell kills yet. Each needs a reason and, where it is a real gap, a register id — an empty
# dict is the goal, and a silent absence is what this file exists to prevent.
KNOWN_SURVIVORS: dict = {}
