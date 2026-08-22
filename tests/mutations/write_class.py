"""Mutations for reshape-plan step 1.6 — the named write questions and `FlowSpec.key`.

Applied by `scripts/prove_red.py` to a scratch copy of `src/`, so these reach `flows.py` for real.

WHAT THEY ATTACK. 1.6's whole risk is a SILENT SEMANTIC CHANGE smuggled into a rename: 27 raw
predicates became five named questions, and a named question that is one shape different from the
expression it stood in for is a behaviour change nobody asked for. Three of the eight below are
exactly that — a question quietly widened or narrowed by one clause. Three more collapse the
DECLARATION / RECIPE split, which is R3.5 itself and the reason the two families are shaped
differently at all.

(id, module-relative path, find, replace, why it must not survive)
"""

MUTANTS = [
    (
        "the_confirm_question_drops_its_confirm_clause",
        "flows.py",
        "declares_confirm=mutate is not None and mutate.has_confirm(),",
        "declares_confirm=mutate is not None,",
        "a named question widened to mean 'declares a write' — the MCP surface would then advertise a "
        "declared write with NO confirm barrier, which is a write replay cannot verify landed.",
    ),
    (
        "the_precheck_question_counts_precheck_url_too",
        "flows.py",
        "declares_precheck=mutate is not None and mutate.has_precheck(),",
        "declares_precheck=mutate is not None and (mutate.has_precheck() "
        "or mutate.precheck_url is not None),",
        "the one shape a reader reliably guesses wrong: `precheck_url` alone declares NO precheck. "
        "Widening it grants the auth-refresh retry to a write that has no idempotency check at all.",
    ),
    (
        "declared_barriers_stands_in_for_MULTIPLE_barriers",
        "flows.py",
        "declares_multiple_barriers=mutate is not None and mutate.is_multiwrite(),",
        "declares_multiple_barriers=mutate is not None and bool(mutate.step_confirms),",
        "a SINGLE-barrier flow would be refused its retry as a multi-write. Over-refusal, which is "
        "the direction a green suite is happiest to accept — the D0 shape.",
    ),
    (
        "the_recipe_count_counts_every_step",
        "flows.py",
        "    return sum(1 for s in (cached_flow.steps if cached_flow is not None else [])\n"
        "               if getattr(s, \"mutating\", False))",
        "    return len(cached_flow.steps if cached_flow is not None else [])",
        "the recipe question stops being about WRITES. A three-step read would then be refused its "
        "auth-refresh retry as a multi-commit flow.",
    ),
    (
        "the_recipe_boolean_stops_agreeing_with_the_count",
        "flows.py",
        "    return recipe_write_count(cached_flow) > 1",
        "    return recipe_write_count(cached_flow) > 0",
        "the two spellings of one fact are only safe while exactly one of them does the counting.",
    ),
    (
        "is_write_flow_forgets_the_recipe_half",
        "flows.py",
        "    return cached_flow is not None and any(getattr(s, \"mutating\", False) "
        "for s in cached_flow.steps)",
        "    return False",
        "R3.5 ITSELF. An UNDECLARED write — nothing declared, a cached step that in fact commits — "
        "would score retry-safe and re-fire its commit under a byte-identical Idempotency-Key.",
    ),
    (
        "the_retry_gate_asks_the_declaration_about_the_recipe",
        "flows.py",
        "    cached_writes = recipe_write_count(cached_flow)",
        "    cached_writes = len(spec.mutate.step_confirms or []) if spec.mutate else 0",
        "the collapse the plan's critic names: `record()` permits two mutating steps with NO "
        "`step_confirms`, which reads as a single write by declaration and commits twice in fact.",
    ),
    (
        "the_key_swaps_two_of_its_three_strings",
        "flows.py",
        "        return flow_key(self.goal, self.start_url, self.scope)",
        "        return flow_key(self.start_url, self.goal, self.scope)",
        "the failure the 24 transcriptions were 24 chances at. Type-silent: all three are `str`, and "
        "the flow is then keyed to a cache entry that is not its own.",
    ),
]

# Empty, and it must stay that way: an entry here is a hole in the matrix with a reason attached.
KNOWN_SURVIVORS: dict = {}
