"""Mutations for R4.115 sites (2) and (3) — the mutation gate deciding drift from an unsettled page.

    uv run --no-sync python scripts/prove_red.py tests/mutations/gate_settle.py

This is the WRITE GATE, so the mutations pull in both directions and both matter. Remove the settle
and the gate refuses byte-identical recipes at random (the measured defect). Move it out of the
mutating branch and every read step pays a wait the product's speed claim cannot afford. Put it after
the comparison and it is decoration that every structural cell still passes.

Each entry is `(id, path-under-src/ultracua, find, replace, why)`. A `find` that no longer matches is
an ERROR rather than a survivor: a stale mutation reports the suite as stronger than it is.
"""

KILLED_BY = [
    "tests/test_learn_settle.py",
    "tests/test_write_safety_invariants.py",
]

MUTANTS = [
    ('the_gate_reads_the_page_again_without_settling', "flow.py",
     '            tr.meta["gate_settled"] = await session.await_settled()',
     "            pass  # MUTANT: decide drift from whatever is painted right now",
     "THE DEFECT, restored. R4.115 traced it at a real gate call: t+0ms returns the PREVIOUS step's "
     "recorded scope and t+100ms returns exactly this step's -- the gate is not seeing drift, it is "
     "seeing the past. Measured cost (R4.139): a byte-identical 6-step recipe passed once and was "
     "refused twice across three reps, with `mutation_gate_refused` the only differing field. "
     "Killed by test_the_gate_settles_before_it_compares_and_only_for_a_write."),

    ('the_settle_moves_after_the_comparison', "flow.py",
     '            tr.meta["gate_settled"] = await session.await_settled()\n'
     "            if step.precond_scope and step.locator is not None:",
     "            if step.precond_scope and step.locator is not None:",
     "Decoration. Every other structural cell still passes -- the call exists, the trace field is "
     "populated -- and the comparison it exists to make like-for-like still reads a half-drawn page. "
     "This is the shape a count-based pin cannot see. Killed by "
     "test_the_gate_settles_before_it_compares_and_only_for_a_write."),
]
