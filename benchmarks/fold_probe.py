"""Which interactable controls exist on a page but are NOT in the agent's observation? (R4.102)

WHY THIS IS A COMMITTED INSTRUMENT AND NOT A SCRATCH SCRIPT. R4.100's lesson, two slices old: the
sensor that ships must be the one the diagnosis used. `gitea-start-timer` was diagnosed by counting
off-screen interactables against the snapshot's contents, and the eventual fix -- whatever shape it
takes -- has to be validated by the same count, before and after. A number recovered from terminal
scrollback is not a baseline.

WHAT IT MEASURES, AND THE ONE THING THAT MAKES IT NON-OBVIOUS. `snapshot.py` returns
viewport-visible interactables by design (`r.top > innerHeight` is dropped, and its docstring says
so). The question is therefore not "is the page taller than the viewport" -- **that test is inert on
Odoo**, which keeps `document.body.scrollHeight == innerHeight` and scrolls an INNER container
instead. Measured: `odoo-sort-list` reports a 720px body in a 720px viewport with **12** controls
below the fold. So the probe counts ELEMENTS whose rect falls outside the viewport, which is true of
both layouts, rather than comparing heights, which is true of only one.

It reports the CONTROLS, not a verdict: whether a hidden control matters depends on whether the task
needs it, and only the corpus knows that.
"""
from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

#: Runs in the page. Mirrors `snapshot.py`'s own notion of an interactable and of visibility, so the
#: two counts are comparable -- a probe with a wider selector would report "hidden" controls the
#: snapshot would never have offered anyway, which is a false alarm in an instrument.
COUNT_JS = """() => {
  const sel = 'a,button,input,select,textarea,summary,[role=button],[role=link],[role=tab]';
  const above = [], below = [];
  for (const el of document.querySelectorAll(sel)) {
    const r = el.getBoundingClientRect();
    if (r.width === 0 && r.height === 0) continue;
    const cs = getComputedStyle(el);
    if (cs.visibility === 'hidden' || cs.display === 'none' || cs.opacity === '0') continue;
    const name = (el.getAttribute('aria-label') || el.innerText || el.value || '')
                   .trim().slice(0, 60);
    // OUTSIDE THE VIEWPORT IN ANY DIRECTION, matching snapshot.py's own rejection test rather than
    // only checking below -- a horizontally scrolled table hides controls the same way.
    (r.top > innerHeight || r.bottom < 0 || r.left > innerWidth || r.right < 0
      ? below : above).push({tag: el.tagName.toLowerCase(), name, top: Math.round(r.top)});
  }
  return {above, below, docH: document.body.scrollHeight, vpH: innerHeight};
}"""


@dataclass(frozen=True)
class FoldReport:
    """What the page offers versus what the agent was shown."""

    url: str
    in_viewport: int
    off_screen: int
    doc_height: int
    viewport_height: int
    #: `(tag, name, top)` for each off-screen control, so a reader can tell an irrelevant footer link
    #: from the button the task needs.
    hidden: tuple = ()

    @property
    def body_scrolls(self) -> bool:
        """Would a naive `docH > vpH` hint fire here?

        FALSE ON ODOO WHILE CONTROLS ARE STILL HIDDEN, which is the whole reason this is a property
        worth publishing: it is exactly the fix that looks obvious and would be inert on half the
        corpus.
        """
        return self.doc_height > self.viewport_height

    def __str__(self) -> str:
        flag = "" if not self.off_screen else (
            "  <-- HIDDEN" + ("" if self.body_scrolls else ", AND THE BODY DOES NOT SCROLL"))
        return (f"{self.url:58} doc={self.doc_height:>5} vp={self.viewport_height:>4} "
                f"shown={self.in_viewport:>3} hidden={self.off_screen:>3}{flag}")


async def probe(session, url: str, settle_s: float = 2.0) -> FoldReport:
    """Navigate an ALREADY-AUTHENTICATED session and count what the viewport excludes.

    Takes a session rather than making one: the controls that matter are usually the authenticated
    ones (Gitea's time tracker is absent to an anonymous visitor entirely), and R4.98 is the standing
    reminder that a probe run logged-out measures a different page.
    """
    import asyncio

    await session.goto(url)
    await asyncio.sleep(settle_s)
    d = await session.page.evaluate(COUNT_JS)
    return FoldReport(
        url=url, in_viewport=len(d["above"]), off_screen=len(d["below"]),
        doc_height=int(d["docH"]), viewport_height=int(d["vpH"]),
        hidden=tuple((h["tag"], h["name"], h["top"]) for h in d["below"]))
