"""Mutations for reshape-plan step 1.1 — the keyword-only engine chain.

Applied by `scripts/prove_red.py` to a scratch copy of `src/`, so these reach `flow.py` for real
rather than in-process. The plan's own acceptance for 1.1 is "shown RED against a mis-keyed scratch
copy"; this is that, standing rather than taken once.

WHAT EACH ONE ATTACKS. The arity pin and the forwarding pins are DIFFERENT sensors and the split is
deliberate — `_replay(..., on_step=finalize, finalize=on_step)` satisfies the arity pin completely,
which is the critic's clause on this step. Three of the seven below are exactly that shape: a
type-silent swap between two parameters that both accept `None`, both accept a callable, or are both
`str`. If any of them survives, the pin that was supposed to see it does not.

(id, module-relative path, find, replace, why it must not survive)
"""

MUTANTS = [
    (
        "a_type_silent_swap_at_the_four_none_site",
        "flow.py",
        "        on_step=None,           # the caller's progress callback is not this verification's business\n"
        "        prepare=prepare,",
        "        on_step=prepare,\n"
        "        prepare=None,",
        "the exact defect 1.1 exists to prevent, at the exact line the critic named: `on_step` and "
        "`prepare` are both Optional callables, so nothing in the type system separates the right "
        "order from the wrong one. The runtime identity cell must see it arrive under the wrong name.",
    ),
    (
        "the_star_walks_back_out_of_learn",
        "flow.py",
        "async def _learn(\n    url: str,\n    *,\n    goal: str,",
        "async def _learn(\n    url: str,\n    goal: str,",
        "the whole of step 1.1 for `_learn`: without the `*`, `goal` and `key` may be passed "
        "positionally again and the type-silent str/str/str swap is back.",
    ),
    (
        "a_positional_creeps_into_the_prefix",
        "flow.py",
        "async def _replay_step(\n    session: BrowserSession,\n    step: CachedStep,\n    *,\n    provider: Optional[Provider],",
        "async def _replay_step(\n    session: BrowserSession,\n    step: CachedStep,\n    provider: Optional[Provider],\n    *,",
        "the prefix table is what stops the `*` drifting one parameter at a time. This one is legal "
        "under the same-type rule, so ONLY the committed table can catch it — which is why the table "
        "is committed rather than derived from the rule alone.",
    ),
    (
        "an_undeclared_drop",
        "flow.py",
        "            on_step=on_step, grounding=grounding,\n        )",
        "            on_step=on_step,\n        )",
        "a parameter that stops being forwarded takes its DEFAULT silently. Here the learn path would "
        "quietly lose its grounding model. DELIBERATE_DROPS is asserted both ways for this.",
    ),
    (
        "a_forward_silently_renamed",
        "flow.py",
        "storage_state=storage_state, verify_replay=verify_replay, samples=samples,\n            reflect=reflect,",
        "storage_state=storage_state, verify_replay=verify_replay, samples=samples,\n            reflect=verify_replay,",
        "two bools, so the swap is type-silent and best-of-N would reflect whenever verification was "
        "on. Both the RENAMED table and the runtime identity cell should see it.",
    ),
    (
        "a_str_for_str_swap_the_arity_pin_cannot_see",
        "flow.py",
        "                session, step, provider=provider, tr=tr, goal=goal, governor=governor, scope=scope,",
        "                session, step, provider=provider, tr=tr, goal=scope, governor=governor, scope=goal,",
        "`goal` and `scope` are both `str` and both forwarded by keyword, so the arity pin is fully "
        "satisfied. The RENAMED table is the only sensor that can fail for it.",
    ),
    (
        "the_subject_stops_being_the_subject",
        "flow.py",
        "        report = await _replay(\n            url, key=key, flow=cached,",
        "        report = await _replay(\n            goal, key=key, flow=cached,",
        "the positional subject is the one argument every edge passes and the one the kwarg check "
        "cannot see. `url` and `goal` are both `str`, so replay would navigate to the goal text.",
    ),
]

# Empty, and it must stay that way: an entry here is a hole in the matrix with a reason attached, not
# a bug in the mutation. A mutation whose find-text no longer matches is reported as an ERROR.
KNOWN_SURVIVORS: dict = {}
