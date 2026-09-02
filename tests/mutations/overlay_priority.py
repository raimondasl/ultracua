"""Mutations for R4.133 -- an open overlay surviving the element cap.

    uv run --no-sync python scripts/prove_red.py tests/mutations/overlay_priority.py

The mutations pull in BOTH directions on purpose. Break the priority and Odoo's `Archived` is cut
again (the measured defect, and R4.130's blocker). Break the INERTNESS and every ordinary page's
observation quietly changes -- which nothing else in the suite would fail for, and which would ship
a silent reordering to every deployment.

Each entry is `(id, path-under-src/ultracua, find, replace, why)`. A `find` that no longer matches
is an ERROR rather than a survivor: a stale mutation reports the suite as stronger than it is.
"""

KILLED_BY = ["tests/test_overlay_priority.py"]

MUTANTS = [
    ('nothing_is_ever_an_overlay', "snapshot.py",
     """                 overlay: OVERLAY_ROLES.has(role)
                          || !!el.closest('[aria-modal="true"],dialog[open]'),""",
     "                 overlay: false,  // MUTANT",
     "THE DEFECT, restored. Measured on Odoo's opportunities list with the search panel open: 118 "
     "visible interactables, 16 `menuitem*` roles, and only NINE reaching an 80-element observation "
     "-- `Archived` among the seven cut, which is what blocked R4.130's repair. Killed by "
     "test_an_open_menu_survives_the_cut_even_when_it_sorts_LAST."),

    ('the_overlay_is_hoisted_to_the_front', "snapshot.py",
     "    return cands.filter((c) => keep.has(c));",
     "    return [...over, ...cands.filter((c) => keep.has(c) && !c.overlay)];  // MUTANT",
     "The tempting one-liner, and it PASSES the survival cell. Concatenating menu-then-rest keeps "
     "every menu item and silently reorders the agent's view of every page carrying an overlay, so "
     "a mid-page dropdown becomes rank 0 and 'first' stops meaning top-of-page. Killed by "
     "test_the_survivors_stay_in_READING_ORDER_rather_than_menu_first."),

    ('an_overlay_escapes_the_cap_entirely', "snapshot.py",
     "    if (over.length >= MAX) return over.slice(0, MAX);",
     "    if (over.length >= MAX) return over;  // MUTANT",
     "The cap stops being a cap. A long menu (Odoo's Filters plus Group By plus Favorites runs well "
     "past 80) would grow the observation without bound, which is the token cost this finding was "
     "filed REFUSING -- it asked for priority precisely so the budget stays fixed. Killed by "
     "test_an_overlay_bigger_than_the_cap_is_itself_truncated_in_reading_order."),

    ('every_element_counts_as_an_overlay', "snapshot.py",
     """                 overlay: OVERLAY_ROLES.has(role)
                          || !!el.closest('[aria-modal="true"],dialog[open]'),""",
     "                 overlay: true,  // MUTANT",
     "THE INERT DIRECTION, which is the one that would rot quietly. With everything an overlay the "
     "kept set becomes `over.slice(0, MAX)` -- still `cap` elements, still plausible -- and no cell "
     "about the MENU can tell. Only a cell asserting an ordinary page is untouched can. Killed by "
     "test_a_page_with_no_overlay_is_truncated_EXACTLY_as_before."),

    ('a_closed_dialog_becomes_an_overlay', "snapshot.py",
     "|| !!el.closest('[aria-modal=\"true\"],dialog[open]'),",
     "|| !!el.closest('[aria-modal=\"true\"],dialog'),  // MUTANT",
     "Priority must never resurrect what the visibility test already rejected. Dropping `[open]` "
     "makes a CLOSED dialog's contents overlay-priority; they are invisible, so this is latent until "
     "a page ships a hidden dialog and its buttons start evicting real ones. Killed by "
     "test_a_CLOSED_dialog_does_not_get_priority_even_when_a_page_styles_it_VISIBLE."),
    ('a_container_role_grants_priority_again', "snapshot.py",
     """                 overlay: OVERLAY_ROLES.has(role)
                          || !!el.closest('[aria-modal="true"],dialog[open]'),""",
     """                 overlay: !!el.closest('[role=menu],[role=listbox],[role=tree],' +
                                       '[aria-modal="true"],dialog[open]'),  // MUTANT""",
     "THE FIRST DESIGN, which the blast-radius measurement refused. `role=menu` names a "
     "PERSISTENT container on BOTH benchmark substrates -- Gitea's closed dropdown carries "
     "aria-expanded=false and Odoo's systray toolbar has no expanded state at all -- so 10 of "
     "14 corpus start pages were both truncated and carrying 4-11 'overlay' elements AT REST, "
     "silently reordering most of the corpus. Every menu cell still passes under this mutation; "
     "only a cell asserting a bare container grants NOTHING can see it. Killed by "
     "test_a_CONTAINER_role_alone_grants_nothing_which_is_the_blast_radius."),
]
