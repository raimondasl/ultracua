"""Mutations for R4.102 — telling the agent that more page exists below the fold.

    uv run --no-sync python scripts/prove_red.py tests/mutations/below_fold.py

The feature is one integer, and every way it goes wrong is a way it goes SILENT: a count that never
reaches the prompt, a count that includes controls nobody can scroll to, or a count folded into the
fingerprint so that scrolling reads as drift. None of those break a test that only looks at the
Observation, which is exactly why the cells this registry names look at the prompt and the gate too.

Each entry is `(id, path-under-src/ultracua, find, replace, why)`. A `find` that no longer matches is
an ERROR rather than a survivor: a stale mutation reports the suite as stronger than it is.
"""

KILLED_BY = [
    "tests/test_below_fold_signal.py",
]

MUTANTS = [
    # ---- the one-liner R4.102 refuted, restored -------------------------------------------------
    ('the_height_comparison_comes_back', "snapshot.py",
     "    if (r.top > innerHeight) belowFold++;",
     "    if (document.body.scrollHeight > innerHeight && r.top > innerHeight) belowFold++;"
     "  // MUTANT: gate the count on the body scrolling",
     "R4.102 measured this exact predicate INERT on Odoo, which keeps docH == vpH == 720 while "
     "hiding 12 controls in an inner-scrolling container -- half the corpus, and the reason the "
     "obvious one-liner was never the fix. Killed by "
     "test_the_derivation_counts_elements_rather_than_comparing_heights."),

    # ---- the count must be about LAYOUT, not about everything isVisible rejects ------------------
    ('css_hidden_controls_are_counted', "snapshot.py",
     "    const st = window.getComputedStyle(el);\n"
     "    if (st.display === 'none' || st.visibility === 'hidden' || parseFloat(st.opacity) === 0) continue;",
     "    // MUTANT: count anything laid out below the fold, hidden or not",
     "A `display:none` control cannot be reached at any scroll offset, so counting it tells the "
     "agent to scroll toward something that will never appear -- a signal that cannot be acted on, "
     "which is worse than none. Killed by "
     "test_css_hidden_controls_are_not_counted_as_below_the_fold."),

    # ---- it has to reach the prompt, or the whole feature is inert -------------------------------
    ('the_signal_never_reaches_the_prompt', "providers/llm_agent.py",
     '    if getattr(obs, "below_fold", 0):',
     '    if False:  # MUTANT: computed, carried, and never rendered',
     "The feature, inert. The Observation still carries the count, the fingerprint is still clean, "
     "every structural cell still passes -- and the agent sees exactly what it saw before, because "
     "the prompt is the only channel it has. This is R4.131's lesson restated: a cell that stops at "
     "the Observation scores this green. Killed by "
     "test_the_signal_reaches_the_prompt_and_names_the_action."),

    ('a_page_that_fits_still_pays_for_the_clause', "providers/llm_agent.py",
     '    if getattr(obs, "below_fold", 0):',
     '    if True:  # MUTANT: always render it, even at zero',
     "Two of fourteen surveyed targets have nothing below the fold, and every turn on those pages "
     "would carry `0 more are further down the page` -- noise the agent has to reason about, on the "
     "prompt's hottest line. Killed by test_a_page_that_fits_reports_zero_and_says_nothing."),

    # ---- the fingerprint must not move ----------------------------------------------------------
    ('the_count_joins_the_fingerprint_basis', "snapshot.py",
     '    basis = json.dumps(sorted([e.role, e.name, e.tag] for e in elements), ensure_ascii=False)',
     '    basis = json.dumps(sorted([e.role, e.name, e.tag] for e in elements), ensure_ascii=False)'
     ' + str(raw.get("below_fold"))  # MUTANT: fold the count into the basis',
     "Every cached recipe's stored `precond_fingerprint` would be invalidated by this release, and "
     "the mutation gate would read drift from a control nobody can see -- including on any page "
     "whose footer loads lazily. Killed by test_the_count_is_not_in_the_fingerprint."),
]
