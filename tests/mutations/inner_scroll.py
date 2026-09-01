"""Mutations for R4.138 — scrolling the element the page really scrolls.

    uv run --no-sync python scripts/prove_red.py tests/mutations/inner_scroll.py

Two directions matter and they pull against each other. Fail to reach the inner container and the
agent is told to scroll on 6 of 7 Odoo pages and nothing happens (the defect). Take precedence over
the WINDOW and an ordinary page stops scrolling at all (the regression). The window-first check is
the whole of that balance, so it has a mutation of its own.

Each entry is `(id, path-under-src/ultracua, find, replace, why)`. A `find` that no longer matches is
an ERROR rather than a survivor: a stale mutation reports the suite as stronger than it is.
"""

KILLED_BY = [
    "tests/test_inner_scroll.py",
    "tests/test_below_fold_signal.py",
]

MUTANTS = [
    ('the_inner_container_is_never_reached', "browser.py",
     "                if not await self._scroll_inner_container(600):\n"
     "                    await page.mouse.wheel(0, 600)",
     "                await page.mouse.wheel(0, 600)  # MUTANT: window only, as before",
     "The defect itself. `mouse.wheel` moves the WINDOW, and on an app that scrolls an inner "
     "container the window has nowhere to go -- measured, 6 of 7 Odoo corpus pages leave "
     "`window.scrollY` at 0 with the element set unchanged. Worse since R4.102: the observation now "
     "TELLS the agent to scroll, so it is a signal that cannot be acted on. Killed by "
     "test_an_inner_scrolling_page_actually_scrolls."),

    ('the_container_search_preempts_the_window', "browser.py",
     "      const doc = document.scrollingElement || document.documentElement;\n"
     "      if (doc.scrollHeight - doc.clientHeight > 1) return null;",
     "      // MUTANT: search for a container even when the window can scroll",
     "THE REGRESSION DIRECTION. An ordinary page often has some inner pane with a little scroll room "
     "-- a sidebar, a code block -- and scrolling THAT instead of the document leaves the page where "
     "it was. The window-first check is what keeps Gitea (docH 1512 against a 720 viewport) on "
     "exactly the wheel it always had. Killed by test_a_window_scrolling_page_is_untouched."),

    ('the_helper_claims_a_scroll_it_did_not_make', "browser.py",
     "        return bool(res and res.get(\"moved\"))",
     "        return True  # MUTANT: assume the container scrolled",
     "The return value is the FALLBACK SWITCH: False means the caller wheels the window. Claiming "
     "success unconditionally skips the wheel, so a page whose window scrolls -- and which returns "
     "None from the JS -- would never move at all. Killed by "
     "test_the_helper_reports_whether_it_moved_anything."),

    ('the_biggest_scroller_is_chosen_by_scroll_room', "browser.py",
     "        const area = (Math.min(r.bottom, innerHeight) - Math.max(r.top, 0)) * r.width;\n"
     "        if (area > bestArea) { bestArea = area; best = el; }",
     "        const area = el.scrollHeight - el.clientHeight;  // MUTANT: most scrollable wins\n"
     "        if (area > bestArea) { bestArea = area; best = el; }",
     "Biggest by VISIBLE AREA, not by scroll room: a tall off-screen drawer or a collapsed menu has "
     "more scrollable content than the list the agent is looking at, and scrolling it moves nothing "
     "the agent can see. Killed by test_the_deeper_controls_actually_arrive."),

    ('an_error_in_the_page_is_taken_as_a_scroll', "browser.py",
     "        except Exception:                                              # noqa: BLE001\n"
     "            return False",
     "        except Exception:                                              # noqa: BLE001\n"
     "            return True  # MUTANT: swallow the error AND claim it worked",
     "Fail-open means falling back to the wheel, not reporting success. A page that navigates out "
     "from under the evaluate would otherwise have its scroll silently dropped -- the worst case "
     "must be the behaviour that shipped before this existed. Killed by "
     "test_a_window_scrolling_page_is_untouched."),
]
