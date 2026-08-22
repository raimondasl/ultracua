"""drift-bench v2, part 3 of 4 — `OracleProvider`, the KEY-LESS recovery arm.

WHY THIS EXISTS. To measure whether a heal or a suffix-replan works, something has to answer the engine's
"which element should I act on now?" question. `ScriptedProvider` cannot: it binds by (role, accessible-name
substring) over `obs.elements`, and a row is only heal-eligible *because* the accessible name was destroyed —
so the teacher is blind exactly when the heal matters. The measurement would be a tautology: 0 recoveries,
for the same reason the drift was hard.

So this provider answers from the fixture's ground-truth marker: it looks up `[data-oracle="<id>"]` and
returns that element's `data-ultracua-ref` (stamped by the snapshot the engine took immediately before, so
the ref is exact).

WHAT THAT DOES AND DOES NOT MEASURE — this framing must travel with every number derived from it:
it is a PERFECT-VISION MODEL AT ACCURACY 1.0. It measures the MECHANISM: does a heal persist, is a
no-effect heal correctly rejected, does the suffix-replan splice, is the working prefix preserved, is the
flow re-cached, does the approval digest move. That is a CEILING on recovery, not a heal rate. The record
names these `mechanism_*` and never `heal_rate`. Only the paid `--provider anthropic` arm speaks to whether
a real model picks the right element.

A PERFECT-VISION ORACLE IS NOT AUTOMATICALLY A CORRECT ONE, which is why the golden trail judges it too.
The first version of this class keyed on "the first unperformed plan entry" and, on a heal of step 2,
re-served step 0 — re-typing into the wrong field. The engine happily reported `success=True,
mode=replay+heal`. Only the act trail caught it, as a DIVERGENCE scored `wrong`. Hence the three behaviours
below, each of which exists because its absence produced a measurably wrong number.
"""

from __future__ import annotations

from typing import Optional

from ultracua.obs import UsageTotals
from ultracua.providers.base import Action

# The engine's heal hint, built in `flow._maybe_heal`:
#     f"{goal} — specifically right now: {step.intent}"
# A heal is a request about ONE named step; a replan is authoring from the bare goal. Keying off this marker
# is what keeps them apart.
_HEAL_MARKER = " — specifically right now: "

_REF_JS = """
(oracle) => {
  const el = document.querySelector('[data-oracle="' + oracle + '"]');
  if (!el) return null;
  return el.getAttribute('data-ultracua-ref');
}
"""


class OracleProvider:
    """A page-reactive teacher with ground-truth element identity.

    `plan` mirrors the scenario's steps but addresses elements by their `data-oracle` id instead of by role
    and name: `[{action, oracle, text?, intent}]`.
    """

    def __init__(self, plan: list) -> None:
        self.plan = [dict(p) for p in plan]
        self.page = None
        # DECLARED, not inferred: this teacher has no LLM path at all, so its cost is a real
        # zero rather than an unknown. `RouterWatch` reads this through the same attribute it
        # already reads on a spender, which is why the declaration is a `UsageTotals` and not
        # a flag it would have to probe for (R4.53; commit `00888b4` for why probing is out).
        self.totals = UsageTotals.cannot_spend()

        self.heal_calls = 0
        self.replan_calls = 0
        self.gave_up = 0

    def bind(self, page) -> "OracleProvider":
        self.page = page
        return self

    # -- helpers ------------------------------------------------------------------------------------
    async def _acts(self) -> list:
        if self.page is None:
            return []
        try:
            return await self.page.evaluate("() => (window.__ucaActs ? window.__ucaActs() : [])")
        except Exception:  # noqa: BLE001 — navigating/detached: treat as nothing performed yet
            return []

    @staticmethod
    def _performed(acts: list, entry: dict) -> bool:
        """Has this step already been actuated? The act trail is the authority — a click logs the bare oracle
        id, a type/select logs `<id>=<value>`.

        Must consider the ALT id too: when a step is served via its alternate route the trail records the
        ALT's id, so checking only the primary made the step look unperformed forever. The replan then
        re-served it, found the alternate gone (it is on the previous page), gave up, and the recovery
        stalled one step in — reported as a wrong outcome rather than the successful replan it was."""
        ids = [entry["oracle"]] + ([entry["alt"]] if entry.get("alt") else [])
        return any(a == oid or a.startswith(oid + "=") for a in acts for oid in ids)

    async def _ref(self, oracle: str) -> Optional[str]:
        if self.page is None:
            return None
        try:
            return await self.page.evaluate(_REF_JS, oracle)
        except Exception:  # noqa: BLE001
            return None

    def _entry_for_intent(self, intent: str) -> Optional[dict]:
        for p in self.plan:
            if p.get("intent") == intent:
                return p
        return None

    async def _serve(self, entry: dict, *, allow_alt: bool = False) -> tuple:
        ref = await self._ref(entry["oracle"])
        if ref is None and allow_alt and entry.get("alt"):
            # The learned target is gone but the fixture offers ANOTHER route to the same place.
            #
            # `allow_alt` is granted to the REPLAN path only, and that asymmetry is a claim about what the
            # two tiers mean. A single-step HEAL re-grounds ONE step: "find the element this step refers to".
            # If that element does not exist, a truthful healer must DECLINE — substituting a different
            # navigation control is not a re-grounding, it is a change of plan, which is precisely the
            # suffix-replan's job. Granting the fallback to the heal path made every removed-route row heal
            # (measured) and left the replan tier at a structural zero, i.e. unmeasurable for the second
            # time — the exact failure v2 exists to fix, one tier down.
            ref = await self._ref(entry["alt"])
            if ref is not None:
                entry = {**entry, "oracle": entry["alt"]}
        if ref is None:
            # BEHAVIOUR 3: `give_up`, never a spurious `done`. `_author_steps` sets
            # `success = authored_ok`, so a provider that says `done` when the goal is unreachable
            # manufactures a FALSE SUCCESS — the engine reports the flow solved having never reached the
            # goal. (Observed before this branch existed.) `no_false_success` is the invariant that guards it.
            self.gave_up += 1
            return Action(action="give_up", intent=f"target {entry['oracle']!r} is gone"), None
        act = {"action": entry["action"], "ref": ref, "intent": entry.get("intent", "")}
        if entry.get("text") is not None:
            act["text"] = entry["text"]
        return Action(**act), None

    # -- the Provider interface --------------------------------------------------------------------
    async def decide(self, goal: str, obs, history: list) -> tuple:
        if _HEAL_MARKER in goal:
            # BEHAVIOUR 1: intent-keyed. A heal asks about ONE step; serve exactly that step's entry.
            # Serving "the next unperformed entry" instead re-typed step 0's value into step 2's field and
            # the engine called it a successful heal.
            self.heal_calls += 1
            intent = goal.split(_HEAL_MARKER, 1)[1].strip()
            entry = self._entry_for_intent(intent)
            if entry is None:
                self.gave_up += 1
                return Action(action="give_up", intent=f"no plan entry for {intent!r}"), None
            return await self._serve(entry)

        # A REPLAN: re-author the tail from the bare goal.
        # BEHAVIOUR 2: skip what already happened. The replan loop drives the flow forward from the CURRENT
        # page, so re-serving an already-actuated step would re-fire it and diverge the trail from golden.
        # The trail itself is the record of what has been performed — no bookkeeping to fall out of sync.
        self.replan_calls += 1
        acts = await self._acts()
        for entry in self.plan:
            if not self._performed(acts, entry):
                return await self._serve(entry, allow_alt=True)
        return Action(action="done", intent="plan complete"), None


def plan_for(scenario: dict) -> list:
    """Derive an oracle plan from a scenario's scripted steps.

    The oracle id per step comes from the fixture: every scenario's steps actuate elements whose
    `data-oracle` ids are, in order, the non-`DONE` entries of its `golden` trail — so the plan cannot drift
    out of sync with the fixture, and a fixture edit that changes what the flow does breaks the golden-trail
    assertion at learn time rather than silently mis-teaching a heal.
    """
    ids = [a.split("=")[0] for a in scenario["golden"] if a != "DONE"]
    acts = [s for s in scenario["steps"] if s["action"] not in ("done", "give_up")]
    assert len(ids) == len(acts), (
        f"{scenario['name']}: {len(acts)} actuating steps but {len(ids)} oracle ids in `golden` — the plan "
        f"and the fixture disagree")
    alts = scenario.get("alt_routes") or {}
    return [{"action": a["action"], "oracle": oid, "text": a.get("text"), "intent": a.get("intent", ""),
             "alt": alts.get(oid)}
            for a, oid in zip(acts, ids)]
