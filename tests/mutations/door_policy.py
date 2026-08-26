"""Mutations for reshape-plan step 1.7 — the door policy and the daemon's closed sets.

Applied by `scripts/prove_red.py` to a scratch copy of `src/`.

WHAT THEY ATTACK. 1.7's `src/` change is one function that refuses three out-of-set values before
the engine, and its risk is the same in both directions: refusing too little (the silent grounding
fall-through it replaced) and refusing too much (a validator that says no to everything passes every
"is it refused?" cell ever written — D0's shape). Both directions are here.

The rest attack the TABLE's load-bearing claims, which are claims about `src/`: which modes each door
can reach, and therefore which doors can re-perform a write by re-authoring a flow.

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
    "tests/test_door_policy.py",
]

MUTANTS = [
    (
        "the_grounding_check_goes_back_to_one_equality",
        "daemon/server.py",
        "    grounding = params.get(\"grounding\")\n"
        "    if grounding is not None and grounding not in GROUNDINGS:",
        "    grounding = params.get(\"grounding\")\n"
        "    if False:",
        "the exact shape that was silently wrong: any value but `anthropic` fell through to "
        "`grounding = None` and the run proceeded WITHOUT grounding, telling the caller nothing.",
    ),
    (
        "the_validator_refuses_everything",
        "daemon/server.py",
        "    if mode not in MODES:",
        "    if True:",
        "the D0 direction. A validator that refuses every request satisfies every 'is it refused?' "
        "cell, and takes the daemon offline. The permits-half of the matrix is what sees it.",
    ),
    (
        "the_validation_runs_after_the_clients_are_built",
        "daemon/server.py",
        "async def _run(params: dict) -> dict:\n    _validate_run(params)\n",
        "async def _run(params: dict) -> dict:\n",
        "ORDER is what this step buys: `run_cached` has refused an unknown mode since R4.31, but "
        "only after `get_provider` built a Router and `AnthropicGrounding()` an SDK client. Without "
        "the call here a refused request pays for both first.",
    ),
    (
        "the_daemon_keeps_its_own_copy_of_the_mode_set",
        "daemon/server.py",
        "from ..flow import MODES, run_cached",
        "from ..flow import run_cached\nMODES = frozenset({\"auto\", \"learn\", \"replay\"})",
        "two lists of the permitted values is how one of them silently stops permitting something "
        "the other does — here, `repair` would become unreachable through this door with nothing "
        "saying so.",
    ),
    (
        "the_provider_set_loses_the_key_less_one",
        "providers/__init__.py",
        "PROVIDERS = frozenset(_LLM_BACKENDS) | {\"mock\"}",
        "PROVIDERS = frozenset(_LLM_BACKENDS)",
        "the closed set must agree with the branches `get_provider` actually has. Dropping `mock` "
        "refuses the one provider that needs no key — a refusal of the key-less path, invisible to "
        "any cell that only checks that bad values are rejected.",
    ),
    (
        "the_cli_root_reaches_every_engine_mode",
        "cli.py",
        "        choices=[\"auto\", \"learn\", \"replay\"],",
        "        choices=[\"auto\", \"learn\", \"replay\", \"repair\"],",
        "the table states an asymmetry — the CLI root cannot reach `repair` — and an asymmetry "
        "nobody re-checks is prose. Widening a door is a policy change and must be a visible diff.",
    ),
    (
        "the_auto_fall_through_widens_to_replay",
        "flow.py",
        "        if report.success or mode in (\"replay\", \"repair\") or report.mode == \"escalate\":",
        "        if report.success or report.mode == \"escalate\":",
        "THE BRANCH THE WHOLE TABLE RESTS ON. Widened this way, a failed `mode='replay'` falls "
        "through to a full re-author — so every door in the table acquires the ability to "
        "re-perform a write, including the gated ones, and every `no` in that column is wrong.",
    ),
    (
        "run_many_quietly_pins_a_mode",
        "parallel.py",
        "                        results[i] = await run_cached(**kwargs)",
        "                        results[i] = await run_cached(mode=\"replay\", **kwargs)",
        "the table's strongest claim is that this door constrains NOTHING. A door that quietly "
        "constrains something is a door whose policy row is fiction — in either direction.",
    ),
]

# Empty, and it must stay that way: an entry here is a hole in the matrix with a reason attached.
KNOWN_SURVIVORS: dict = {}
