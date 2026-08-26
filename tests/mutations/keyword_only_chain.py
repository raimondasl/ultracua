"""Mutations for the engine chain's shape — reshape-plan steps 1.1 and 1.8.

Applied by `scripts/prove_red.py` to a scratch copy of `src/`, so these reach `flow.py` for real.

RE-EXPRESSED AT 1.8, and the way they came due is worth the line: 1.8 moved every call site, so all
seven of 1.1's find-texts stopped matching at once. `prove_red` reported them as ERRORS rather than
as survivors — which is exactly the rule the harness exists for, because a stale mutation silently
reports the suite as stronger than it is.

WHAT THEY ATTACK NOW. 1.1's risk was ORDER: a positional argument list where two swappable values
are the same type. 1.8's risk is REACH: two bundles hand every inner function all twenty-one values,
so the withholding a signature used to enforce is now enforced by nothing but a test. Both families
are below.

(id, module-relative path, find, replace, why it must not survive)
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
    "tests/test_engine_chain_is_keyword_only.py",
]

MUTANTS = [
    # ---- 1.1's family: the SHAPE of the signatures ------------------------------------------
    (
        "the_star_walks_back_out_of_learn",
        "flow.py",
        "async def _learn(\n    url: str, *, opts: RunOptions,",
        "async def _learn(\n    url: str, opts: RunOptions,",
        "without the `*` the bundles may be passed positionally again, and `opts`/`hooks` are two "
        "adjacent objects that no type error separates — the 1.1 swap in its 1.8 clothes.",
    ),
    (
        "a_positional_creeps_into_the_prefix",
        "flow.py",
        "    session: BrowserSession, step: CachedStep, *, opts: RunOptions,",
        "    session: BrowserSession, step: CachedStep, opts: RunOptions, *,",
        "the prefix table is what stops the `*` drifting one parameter at a time. Legal under the "
        "same-type rule, so ONLY the committed table can catch it.",
    ),
    (
        "a_str_for_str_swap_the_arity_pin_cannot_see",
        "flow.py",
        "                session, step, opts=opts, provider=provider, tr=tr, goal=goal, scope=scope, idx=i,",
        "                session, step, opts=opts, provider=provider, tr=tr, goal=scope, scope=goal, idx=i,",
        "`goal` and `scope` are both `str` and both forwarded by keyword, so the arity pin is fully "
        "satisfied. The RENAMED table is the only sensor that can fail for it.",
    ),
    (
        "the_subject_stops_being_the_subject",
        "flow.py",
        "        report = await _replay(\n            url, opts=opts, hooks=hooks,",
        "        report = await _replay(\n            goal, opts=opts, hooks=hooks,",
        "the positional subject is the one argument every edge passes and the one the kwarg check "
        "cannot see. `url` and `goal` are both `str`, so replay would navigate to the goal text.",
    ),
    (
        "an_undeclared_drop",
        "flow.py",
        "                        block_mutations=True,  # a replay-repair must never perform a NEW write",
        "                        # a replay-repair must never perform a NEW write",
        "a parameter that stops being forwarded takes its DEFAULT silently — here `block_mutations` "
        "falls back to False and a suffix-replan may perform a NEW write during a repair.",
    ),

    # ---- 1.8's family: the REACH of the bundles ----------------------------------------------
    (
        "a_function_reads_what_it_never_received",
        "flow.py",
        "    max_steps = opts.max_steps or settings.max_steps",
        "    max_steps = opts.max_steps or settings.max_steps\n    _ = opts.params",
        "THE CENTRAL RISK OF 1.8. `_learn` never received `params` and must not start reading it "
        "merely because a bundle made it reachable. A signature used to enforce that; nothing does "
        "now except the pin.",
    ),
    (
        "r412_gets_quietly_fixed",
        "flow.py",
        "        hooks=hooks.without(\"pre_write\"),",
        "        hooks=hooks,",
        "R4.12 is OPEN and must stay open through a migration. Handing the learn path `pre_write` "
        "closes it as a SIDE EFFECT, and a silent fix inside a refactor is as unreviewable as a "
        "silent break — the plan's own row says `preserved, not fixed`.",
    ),
    (
        "the_replan_acquires_a_grounding_model",
        "flow.py",
        "                        opts=opts.without(\"grounding\"), hooks=hooks,",
        "                        opts=opts, hooks=hooks,",
        "`_replay` never received `grounding`, so its suffix-replan never had one. The bundle makes "
        "it reachable; dropping the clearing hands the replan a vision model it has never had.",
    ),
    (
        "a_verification_run_starts_carrying_the_callers_hooks",
        "flow.py",
        "        hooks=hooks.without(\"on_step\", \"finalize\", \"pre_write\"),",
        "        hooks=hooks.without(\"on_step\", \"pre_write\"),",
        "the verification replay is supposed to make NO paid call. Letting `finalize` through means "
        "every verify-by-replay runs the caller's extraction — a silent cost, and the kind of "
        "withdrawal that used to be a `None` nine arguments deep and is now a named clearing.",
    ),
]

# Empty, and it must stay that way: an entry here is a hole in the matrix with a reason attached.
KNOWN_SURVIVORS: dict = {}
