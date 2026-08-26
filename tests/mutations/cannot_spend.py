"""Mutations for step 1.3 — the cannot-spend third state and run-scoped accounting failures.

    uv run --no-sync python scripts/prove_red.py tests/mutations/cannot_spend.py

(No `--tests`. The killer suite is DECLARED below as `KILLED_BY` since 0.6 — it used to ride on this
command line and in `ci.yml`, where the reviewer of a new mutation could not see it.)

Each entry is `(id, path-under-src/ultracua, find, replace, why)`, with an optional sixth element
naming a killer suite for that mutant alone. A `find` that no longer matches
is an ERROR rather than a survivor: a stale mutation silently reports the suite as stronger than it
is, and this file exists to make the opposite claim checkable.

WHAT IS NOT HERE, and it is a limit of the instrument rather than a hole in the matrix.
`scripts/prove_red.py` installs a mutant by putting a copy of `src/` first on `PYTHONPATH`, so it
cannot reach `benchmarks/variance.py` or `benchmarks/drift_bench.py` — the two READERS 1.3 fixes.
That is R4.77. Their guards are armed in-process at the foot of `tests/test_cannot_spend.py`.
"""

# THE KILLER SUITE, DECLARED HERE rather than typed into `ci.yml`.
#
# It used to be a `--tests` flag on the CI command line, which is two problems in one line. It was
# invisible to whoever wrote or reviewed this registry -- the reviewer of a new mutation could not
# see what would be run against it -- and it made the registry list in `ci.yml` a SECOND source of
# truth about which registries exist. Nothing asserted the two agreed, so a registry added here and
# not there would simply never run, with every job green. `scripts/mutation_sweep.py` now derives
# the set from this directory and each registry supplies its own killers.
KILLED_BY = [
    "tests/test_cannot_spend.py",
    "tests/test_run_record.py",
    "tests/test_obs.py",
]

MUTANTS = [
    # ---- the third state: the declaration channel itself ------------------------------------
    ('the_owners_own_totals_are_ignored', "obs.py",
     '            r = getattr(o, "router", o)',
     '            r = getattr(o, "router", None)  # MUTANT: only a ROUTER may carry totals',
     "The pre-1.3 world for every key-less owner. A teacher with no `router` attribute falls to "
     "None, exposes no totals, and is classified an unwatched SPENDER — so `ScriptedProvider`, "
     "`MockProvider`, `MockGrounding` and the oracle teacher all report cost UNKNOWN, which is "
     "R4.53 exactly. Killed by test_a_key_less_owner_reports_a_real_zero_not_an_unknown."),

    ('an_unwatched_spender_is_waved_through', "obs.py",
     '                self._unobserved = True\n                continue',
     '                continue  # MUTANT: an owner with no totals is assumed harmless',
     "The other direction, and the one that makes the fix worthless: 'declare zero for everything' "
     "passes every cell about key-less teachers and destroys the only claim the tri-state exists "
     "to make. Killed by test_an_owner_that_could_spend_and_exposes_nothing_is_STILL_unobserved."),

    ('the_declaration_stops_declaring', "obs.py",
     '        return UsageTotals()\n\n    def add(self, usage, model: str = "") -> None:',
     '        return None  # MUTANT: the channel goes quiet\n\n'
     '    def add(self, usage, model: str = "") -> None:',
     "The declaration channel emptied out. Every key-less owner still SAYS `cannot_spend()`, and "
     "what it hands back is no longer a `UsageTotals` — so the isinstance check files all of them "
     "under unwatched spender again and R4.53 is exactly as it was, with the fix's own vocabulary "
     "still in the source. Killed by test_a_key_less_owner_reports_a_real_zero_not_an_unknown."),

    # ---- inviolable #1: the declaration is offered, never extracted --------------------------
    ('a_third_attribute_is_probed', "obs.py",
     '            t = getattr(r, "totals", None)',
     '            if getattr(o, "accounting_failed", False):  # MUTANT: commit 00888b4, restored\n'
     '                self._unobserved = True\n'
     '            t = getattr(r, "totals", None)',
     "Commit `00888b4` verbatim: an accounting probe reaching for a third attribute on the OWNER. "
     "`_Exploding.__getattr__` raises on anything but `router`, so this trips inviolable #1's "
     "tripwire on the repair path — which is what happened when it shipped. Killed twice, by the "
     "AST scan (test_classifying_an_owner_touches_only_router_and_totals) and by the behaviour "
     "(test_the_tripwire_survives_the_classification_end_to_end)."),

    # ---- run-scoped accounting failures ------------------------------------------------------
    ('the_failure_flag_is_sticky_again', "obs.py",
     '        return not any(t.accounting_failures > (snap[6] if len(snap) > 6 else 0)\n'
     '                       for t, snap in self._pairs)',
     '        return not any(t.accounting_failures > 0 for t, _ in self._pairs)  # MUTANT',
     "R4.53's second half restored: reading the absolute count answers 'has accounting EVER failed "
     "on this object', so a caller reusing a grounding or a provider across runs reports "
     "unobserved for the rest of the process on the strength of one earlier failure. Killed by "
     "test_an_accounting_failure_belongs_to_the_run_it_happened_in's third assertion."),

    ('the_delta_carries_the_running_total', "obs.py",
     '            accounting_failures=self.accounting_failures - (snap[6] if len(snap) > 6 else 0),',
     '            accounting_failures=self.accounting_failures,  # MUTANT: not a delta',
     "A delta describing ONE run must not carry a failure from an earlier one. Killed by "
     "test_the_delta_of_a_run_carries_only_that_runs_failures."),

    ('the_snapshot_forgets_the_count', "obs.py",
     "                self.cache_write_tokens, self.calls, dict(self.per_model),\n"
     "                self.accounting_failures)",
     "                self.calls, dict(self.per_model))  # MUTANT: a 6-tuple again",
     "The snapshot is what makes the count run-scoped. Drop it and `since()`'s length guard reads "
     "zero, so every delta carries the running total and the stickiness is back by another route — "
     "which is why the guard is a guard and not a silent default. Killed by "
     "test_the_delta_of_a_run_carries_only_that_runs_failures."),

    ('accounting_failed_can_be_written_again', "obs.py",
     '    @property\n    def accounting_failed(self) -> bool:',
     '    accounting_failed = False\n\n    def _unused_accounting_failed(self) -> bool:  # MUTANT',
     "A settable flag alongside the counter is two representations of one fact, and the writers "
     "would drift apart silently — `= True` would stop moving the number `observed` reads. Killed "
     "by test_accounting_failed_is_readable_and_not_writable and by the run-scoped cell."),

    # ---- the early return that carried no accounting at all ---------------------------------
    #
    # ANCHORED ON THE `_log.warning` SITE, because the two escalate returns are now textually
    # identical and a find matching twice is reported as ambiguous — an ERROR, not a survivor. One
    # mutation is enough to prove the guard fires; the guard itself counts BOTH sites and asserts
    # each carries `usage`, so the other is not left uncovered.
    ('an_escalate_report_carries_no_usage', "flow.py",
     'escalating instead of retrying")\n'
     '            return FlowReport(mode="escalate", success=False, traces=traces,\n'
     '                              note="interstitial/CAPTCHA detected",\n'
     '                              extra={"escalate": True,\n'
     '                                     "usage": _usage_watch.as_dict(settings.model)})',
     'escalating instead of retrying")\n'
     '            return FlowReport(mode="escalate", success=False, traces=traces,\n'
     '                              note="interstitial/CAPTCHA detected",\n'
     '                              extra={"escalate": True})  # MUTANT',
     "`flows.py`'s contract is that `usage` is always populated. Both escalate paths were early "
     "returns that quietly were not, so every reader rendered an escalated run's cost as UNKNOWN — "
     "and `drift_bench` published `cost_usd: null` for a wholly key-less run on the strength of two "
     "rows out of 370. Killed by test_every_escalate_report_carries_its_usage."),
]

# Mutants no cell kills yet. Each needs a reason and, where it is a real gap, a register id — an
# empty dict is the goal, and a silent absence is what this file exists to prevent.
KNOWN_SURVIVORS: dict = {}
