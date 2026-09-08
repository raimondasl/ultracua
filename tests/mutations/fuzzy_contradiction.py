"""Mutations for D2's contradiction check on the one fuzzy Tier-1 candidate — 0.178.0 (R4.156).

Applied by `scripts/prove_red.py` to a scratch copy of `src/`, so these reach `locators.py` for real.

WHY THESE EXIST. `role+name~` is a case-insensitive SUBSTRING match sitting in the CONFIDENT tier —
"a fuzzy matcher wearing an identity anchor's clothes", in that file's own words. Tier 1 returns the
first unique match OUTRIGHT, so until 0.178.0 it could bind a decoy whose name merely CONTAINED the
recorded one while the recorded css path still pointed at the real target, and nothing cross-checked
it. That is a silent wrong-TARGET click reaching a plausible page — the harm class this register has
rated critical every time it has appeared.

THE SHAPE OF THE FIX IS THE THING TO KEEP DEAD, and both halves are load-bearing in OPPOSITE
directions, which is why a single mutation cannot cover it:

  * remove the check and the decoy binds again (`silent_wrong` 6 -> 4 reverts);
  * turn it from CONTRADICTION into AGREEMENT and the check becomes the variant measured to cost
    five 0-LLM rows -- survivals 84 -> 79, k50 6 -> 2 -- because a positive-agreement gate can only
    ever accept a bind Tier 2 would have made anyway, so the candidate collapses into `css` and its
    whole remaining value (an augmented label on a page whose structure ALSO moved) is destroyed.

A registry that only attacked the first would be satisfied by shipping the expensive variant.

(id, module-relative path, find, replace, why it must not survive)
"""

# THE KILLER SUITE. `test_locators.py` holds both directions as a PAIR -- the refusal and its cost
# control -- and neither cell alone distinguishes the shipped rule from the refuted alternatives:
# `refused_when_css_contradicts` passes under an agreement gate too (it refuses, just via Tier 2's
# `conflict`), which is why that cell also asserts `sink["fuzzy_contradicted"]`.
KILLED_BY = [
    "tests/test_locators.py",
]

_CHECK = """            if label == "role+name~" and css_loc is not None:"""

MUTANTS = [
    (
        "the_contradiction_check_never_runs",
        "locators.py",
        _CHECK,
        """            if False and label == "role+name~" and css_loc is not None:""",
        "the D2 hole restored verbatim: the sole surviving fuzzy candidate binds the substring decoy "
        "outright while the recorded css path still resolves to the target. Measured on the drift "
        "corpus as `fuzzy-decoy/fuzzy-decoy-wins`, act trail ['wrong-decoy', 'WRONG-PAGE'] -- a "
        "wrong-target click that reaches a plausible page and reports success.",
    ),
    (
        "contradiction_becomes_agreement",
        "locators.py",
        """                css_kind, css_first = await classify(css_loc)
                if css_kind == "unique" and not await _same_element(first, css_first):""",
        """                css_kind, css_first = await classify(css_loc)
                if css_kind != "unique" or not await _same_element(first, css_first):""",
        "the REFUTED variant, and the one a careless fix would ship. Requiring css to AGREE rather "
        "than merely not contradict measured 0-LLM survivals 84 -> 79 and k50 6 -> 2, because an "
        "absent css (the corpus's five `rename_augment+wrap` rows, where a wrap breaks the recorded "
        "path) can never agree -- so the candidate is refused exactly where it is the only thing "
        "left. HEALING.md had recorded this as 'the same cost as full deletion' and it is why the "
        "shipped rule is contradiction-only.",
    ),
    (
        "a_contradicting_css_is_trusted_to_be_the_same_element",
        "locators.py",
        """                if css_kind == "unique" and not await _same_element(first, css_first):""",
        """                if css_kind == "unique" and not (first is not None and css_first is not None):""",
        "the check kept but its comparison gutted, so no contradiction is ever detected. Distinct "
        "from the first mutant: the branch still exists and still reads `css_loc`, so a structural "
        "scan asserting the check is present passes, and only a cell that drives the decoy page "
        "goes red.",
    ),
    (
        "the_refusal_falls_through_to_binding_anyway",
        "locators.py",
        """                    if sink is not None:
                        sink["fuzzy_contradicted"] = True
                    continue""",
        """                    if sink is not None:
                        sink["fuzzy_contradicted"] = True""",
        "the contradiction is DETECTED and reported on the sink, and then the bind happens anyway. "
        "This is the shape that would pass a cell asserting only the observability field -- which is "
        "why `test_a_sole_surviving_fuzzy_candidate_is_refused_when_css_contradicts_it` asserts the "
        "bind is None FIRST and the sink flag second, never the flag alone.",
    ),
]
