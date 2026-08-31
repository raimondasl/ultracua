"""Mutations for R4.131 — naming the unnamed controls, without touching the locator rail.

    uv run --no-sync python scripts/prove_red.py tests/mutations/element_hints.py

The feature is small; the DANGER is not. `hint` exists as a separate field only because `_ACCNAME_JS`
is shared with `DESCRIBE_JS` (the cached locator, replayed through `get_by_role(name=...)`) and
`SCOPE_JS` (every recipe's scope fingerprint). The mutations below are the ways that separation gets
undone by a later "simplification" — each one is plausible, and each would either invent names
Playwright cannot match or invalidate every cached flow in every deployment.

Each entry is `(id, path-under-src/ultracua, find, replace, why)`. A `find` that no longer matches is
an ERROR rather than a survivor: a stale mutation reports the suite as stronger than it is.
"""

KILLED_BY = [
    "tests/test_element_hints.py",
    "tests/test_snapshot_roles.py",
]

MUTANTS = [
    # ---- the separation from the locator rail -------------------------------------------------
    ('the_hint_becomes_the_name', "snapshot.py",
     "                 hint: name ? null : hintOf(el),",
     "                 hint: null, name: name || (hintOf(el) || '').split(': ').pop() || '',"
     "  // MUTANT: fold the hint into the accessible name",
     "THE SIMPLIFICATION THIS FIELD EXISTS TO PREVENT. Putting the hint in `name` looks tidier and "
     "reaches the agent identically -- and `name` is in the fingerprint basis, so every cached "
     "recipe's stored fingerprint shifts and the mutation gate reads drift on every hinted page. "
     "Killed by test_the_hint_is_not_in_the_observation_fingerprint."),

    # ---- precedence: a named control must never be re-labelled --------------------------------
    ('a_named_control_is_overwritten_by_its_tooltip', "snapshot.py",
     "                 hint: name ? null : hintOf(el),",
     "                 hint: hintOf(el),  // MUTANT: hint every control, named or not",
     "Costs prompt tokens on every named control on every turn, and worse: it renders a `Save` "
     "button as `Save (tooltip: Ignored)`, inviting the agent to weigh a tooltip against the real "
     "accessible name. Killed by test_a_named_control_gets_no_hint."),

    # ---- the source must travel with the value ------------------------------------------------
    ('the_icon_modifier_is_taken_as_a_glyph', "snapshot.py",
     "        if (!MODIFIER.has(m[1])) return 'icon: ' + m[1];",
     "        return 'icon: ' + m[1];  // MUTANT: first match wins",
     "MEASURED, not hypothetical: `oi oi-fw oi-settings-adjust` then renders `(icon: fw)` -- font "
     "awesome's fixed-width MODIFIER dressed as a glyph name. A hint that is noise is worse than no "
     "hint, because nothing in the rendering distinguishes them. Killed by "
     "test_an_icon_modifier_class_is_not_a_glyph_name."),

    # ---- the redaction channel -----------------------------------------------------------------
    ('a_secret_in_a_tooltip_is_not_scrubbed', "snapshot.py",
     "                if e.hint:\n                    e.hint = e.hint.replace(term, REDACTED)",
     "                pass  # MUTANT: hint is not a redaction channel",
     "`hint` is page-derived and rendered into every prompt, so a `data-tooltip` reading "
     "'Copy sk-live-...' ships the token to the provider and the transcript. This is R9's partial "
     "coverage (2 of 5 Observation fields) arriving on a sixth field. Killed by "
     "test_a_secret_in_a_tooltip_is_redacted."),

    # ---- it has to actually reach the agent ----------------------------------------------------
    ('the_hint_never_reaches_the_prompt', "providers/llm_agent.py",
     '        h = f" ({e.hint})" if (e.hint and not e.name) else ""',
     '        h = ""  # MUTANT: computed and then dropped',
     "The whole feature, inert. Everything upstream still works and every structural pin still "
     "passes -- the observation carries the hint, the fingerprint is untouched, the locator is "
     "clean -- and the agent sees `button: ` exactly as before. A cell that only checks the "
     "Observation would score this green. Killed by "
     "test_the_renderer_shows_the_hint_with_its_source."),

    # ---- R4.132: the sibling role that was never admitted --------------------------------------
    ('the_menu_checkbox_role_is_dropped_again', "snapshot.py",
     "    '[role=menuitemcheckbox]', '[role=menuitemradio]', '[role=option]', '[role=treeitem]',",
     "    // MUTANT: those roles go back to being unadmitted",
     "Odoo's Filters menu renders every option as `<span role=menuitemcheckbox>` -- 15 of 25 items, "
     "including `Archived`. Dropping the role means a menu the agent can OPEN arrives with its "
     "contents invisible, which is R4.132 exactly: measured, 76 visible candidates after the click "
     "and ZERO elements whose text said Archived. Killed by "
     "test_a_menu_checkbox_item_reaches_the_observation."),

    # ---- the divergence from the gate's scope list is a DECISION -------------------------------
    ('the_gates_scope_list_silently_follows', "snapshot.py",
     "  const scope = el.closest('form, dialog, [role=dialog], fieldset, [role=form], section, "
     "main, [role=main], article') || document.body;",
     "  const sel2 = sel + ',[role=menuitemcheckbox]';  // MUTANT: widen the GATE's scope too "
     "\n  const scope = el.closest('form, dialog, [role=dialog], fieldset, [role=form], section, "
     "main, [role=main], article') || document.body;",
     "SNAPSHOT_JS and SCOPE_JS diverge ON PURPOSE. The gate's scope hash is recorded on every cached "
     "step, so widening its selector is a write-safety-adjacent MIGRATION that needs its own slice "
     "and its own measurement of the affected recipes. Absorbing it into a snapshot change would "
     "invalidate those hashes silently. Killed by "
     "test_the_scope_fingerprint_list_is_deliberately_narrower."),
]
