"""H6 evals: Drift-repair bot — canary-triggered heal PRs (ROADMAP.md H6).

The horizon: OFFLINE, failure-triggered repair that emits reviewable heal PRs instead of
runtime patches — Tier 1 a 0-LLM HybridSimilo relocalizer (stored element-property snapshots
scored against a page-wide property harvest), Tier 2 per-element visual memory re-grounded by
a local pointing model; healed flows re-enter the cache only via verify + human approval, and
the replay path stays untouched and 0-LLM.

What is measured here:
- PARTIAL CREDIT (shipped today, key-less): the runtime self-heal lane — a drifted READ
  replays via `mode="repair"` re-grounding and the healed locator persists back to 0-LLM;
  a drifted WRITE is never healed, never replanned, never re-driven (write safety).
- ASPIRATIONAL (expected `missing`): the Similo property set on LocatorSpec, the visual-memory
  artifact on CachedStep, the offline `ultracua.heal` harvest + scorer modules, and the
  `flows.heal` -> HealProposal -> heal-approve pipeline surface.
"""

from __future__ import annotations

from evals.core import Ctx, expect, import_probe, scenario
from evals.fixtures import Fixture, page


class _ReGround:
    """Scripted key-less re-grounding 'provider': click the first link on the page, then done.

    Serves BOTH roles a heal exercise needs: the learn-time author (click -> done, the tests/
    convention) and the heal-time re-grounder (`_maybe_heal` asks it once for the drifted step
    and it points at the survivor link). `calls` counts decide() invocations so write-safety
    checks can assert the provider was NEVER consulted for a drifted write.
    """

    def __init__(self) -> None:
        self.calls = 0
        self._clicked = False

    async def decide(self, goal, obs, history):
        from ultracua.types import Action

        self.calls += 1
        if not self._clicked:
            for el in obs.elements:
                if el.role == "link":
                    self._clicked = True
                    return Action(action="click", intent="open the report page", ref=el.ref), None
        return Action(action="done", intent="done"), None


@scenario(
    id="h06.heal.read_selfheal_repair",
    title="shipped heal tier: a drifted READ re-grounds under mode='repair', then returns to 0-LLM",
    group="h06", tags=("heal", "read", "repair"),
)
async def read_selfheal_repair(ctx: Ctx):
    """H6's Tier-1 promise is 'most drift breakages become repin proposals'. The shipped
    building block is the RUNTIME heal lane: repair-mode replay re-grounds a broken read step
    via the provider and persists the fixed locator. This scenario measures that public
    behavior end-to-end (partial credit for H6 — the offline/PR half is probed elsewhere)."""
    from ultracua.flow import run_cached

    checks = []
    fx = Fixture({
        "/": page('<a href="/answer">open the daily report</a>'),
        "/answer": page('<h1>Report</h1><p id="total">total: 42</p>'),
    })
    with fx.serve() as base:
        cache = ctx.cache()
        goal = "open the daily report page"
        learned = await run_cached(base + "/", goal, _ReGround(), cache, mode="learn", headless=True)
        # Baseline gate: without a cached flow none of the drift checks below mean anything.
        checks.append(expect(learned.success, "baseline learn succeeds on the fixture",
                             f"note={learned.note!r}"))
        if not learned.success:
            return checks

        # DRIFT the entry page: the link is renamed AND re-parented so EVERY stored locator tier
        # breaks at once — role+name, exact text, tag-scoped substring, and the positional css path.
        fx.pages["/"] = page('<section aria-label="Reports"><a href="/answer">view report v2</a></section>')

        # Shipped fail-loud: with NO heal provider a drifted replay must fail, never guess an element.
        plain = await run_cached(base + "/", goal, None, cache, mode="replay", headless=True)
        checks.append(expect(not plain.success and plain.healed_steps == 0,
                             "drifted replay without a provider fails loud (no silent guess)",
                             f"mode={plain.mode} success={plain.success}"))

        # Shipped self-heal: repair-mode replay re-grounds the broken step via the provider —
        # either an in-place heal (replay+heal) or a suffix-replan of the tail (replay+replan).
        repaired = await run_cached(base + "/", goal, _ReGround(), cache, mode="repair", headless=True)
        checks.append(expect(repaired.success and repaired.mode in ("replay+heal", "replay+replan"),
                             "repair-mode replay re-grounds the drifted read",
                             f"mode={repaired.mode} success={repaired.success} note={repaired.note!r}"))
        # Honest accounting: a repair that consulted the provider must SAY so (llm_calls /
        # healed_steps) — H6's review-queue economics depend on repairs being visible events.
        checks.append(expect(repaired.llm_calls >= 1
                             and (repaired.healed_steps >= 1 or repaired.mode == "replay+replan"),
                             "repair accounting is honest (provider use reported, never hidden)",
                             f"llm_calls={repaired.llm_calls} healed={repaired.healed_steps}"))

        # The repaired locator must PERSIST so the flow re-enters the 0-LLM lane — the same
        # cache-re-entry contract the offline heal-PR bot will rely on.
        again = await run_cached(base + "/", goal, None, cache, mode="replay", headless=True)
        checks.append(expect(again.success and again.llm_calls == 0,
                             "healed locator persisted: the NEXT replay is pure 0-LLM again",
                             f"mode={again.mode} llm_calls={again.llm_calls}"))
        checks.append(expect(not fx.writes, "read heal/repair sent no write to the server",
                             f"writes={[(w.method, w.path) for w in fx.writes]}"))
    return checks


@scenario(
    id="h06.heal.write_drift_never_healed",
    title="a drifted WRITE is never healed, replanned, or re-driven — and no write leaves the browser",
    group="h06", tags=("heal", "write-safety"),
)
async def write_drift_never_healed(ctx: Ctx):
    """H6's hardest rule (and the repo's): healing a write could double-submit, so a drifted
    mutating step must fail loud with ZERO provider involvement even in repair mode. The heal-PR
    bot inherits this shipped gate (write proposals get prefix-only verification, never replay)."""
    import time

    from ultracua.cache import CachedFlow, CachedStep, flow_key
    from ultracua.flow import run_cached
    from ultracua.locators import LocatorSpec

    fx = Fixture({
        "/": page('<form action="/submit" method="post"><button type="submit">Pay now</button></form>'),
    })
    with fx.serve() as base:
        cache = ctx.cache()
        goal = "pay the invoice"
        key = flow_key(goal, base + "/", "default")
        # Handcraft a cached WRITE flow whose recorded precondition can no longer hold (stale
        # fingerprint = the page changed since learn/approval) so the mutation gate MUST trip.
        step = CachedStep(
            intent="click Pay now", action="click",
            locator=LocatorSpec(role="button", name="Pay now", tag="button", text="Pay now"),
            precond_fingerprint="fingerprint-drifted-on-purpose", mutating=True,
        )
        cache.put(CachedFlow(key=key, goal=goal, start_url=base + "/",
                             steps=[step], created_ts=time.time()))

        provider = _ReGround()  # would happily click if consulted — the point is it must NOT be
        report = await run_cached(base + "/", goal, provider, cache, mode="repair", headless=True)

    return [
        # Fail loud, not silent: a write under drift is the caller's to re-learn + re-approve.
        expect(not report.success, "drifted write replay fails (never silently succeeds)",
               f"mode={report.mode} success={report.success}"),
        # The stop must come from the mutation gate itself (gate=drift on the step trace), not
        # from some downstream accident that a future refactor could remove.
        expect(any(t.meta.get("gate") == "drift" for t in report.traces),
               "the mutation gate is what stopped it (gate=drift recorded on the step)",
               f"metas={[t.meta for t in report.traces]}"),
        expect(report.healed_steps == 0 and report.mode == "replay",
               "no heal and no suffix-replan was attempted on the write",
               f"mode={report.mode} healed={report.healed_steps}"),
        # Zero provider consultation: even repair mode never LLM-re-drives a write under drift.
        expect(provider.calls == 0 and report.llm_calls == 0,
               "the provider was never consulted for a drifted write (0 calls, even in repair mode)",
               f"provider.calls={provider.calls} llm_calls={report.llm_calls}"),
        # The oracle for write safety: nothing mutating ever reached the local server.
        expect(not fx.writes, "no write reached the server",
               f"writes={[(w.method, w.path) for w in fx.writes]}"),
    ]


@scenario(
    id="h06.similo.locator_property_set",
    title="Similo property set on LocatorSpec (geometry/classes/abs-XPath) + visual-memory artifact",
    group="h06", aspirational=True, tags=("heal", "similo", "locators"),
)
async def similo_locator_property_set(ctx: Ctx):
    """H6 plan step 1: extend `_SPECOF_JS`/LocatorSpec with the Similo property set (bbox/location,
    area, class list, absolute XPath) as additive Optional fields, and step 4: a per-element
    visual-memory artifact on CachedStep. Inspect the dataclass/model fields directly — the
    capability IS the captured property set, so field presence is the honest probe."""
    from ultracua.cache import CachedStep
    from ultracua.locators import LocatorSpec

    lf = set(LocatorSpec.model_fields)
    sf = set(CachedStep.model_fields)
    ok_v, vision = import_probe("ultracua.vision")
    return [
        # Partial credit: two of Similo's strongest signals are ALREADY captured per element —
        # visible text and neighbor text (the anchor + its source travel with every spec).
        expect({"text", "anchor", "anchor_source"} <= lf,
               "visible-text + neighbor-anchor signals already captured (partial Similo set)",
               f"fields={sorted(lf)}"),
        # The geometric signal Similo weights heavily (bbox/location/area) is not yet captured.
        expect(bool(lf & {"bbox", "rect", "location", "area", "x", "y", "width", "height"}),
               "geometry properties (bbox/location/area) not yet on LocatorSpec", aspirational=True),
        expect(bool(lf & {"classes", "class_list", "css_classes"}),
               "class-list property not yet on LocatorSpec", aspirational=True),
        expect(bool(lf & {"xpath", "abs_xpath", "absolute_xpath"}),
               "absolute-XPath property not yet on LocatorSpec", aspirational=True),
        # Tier-2 visual memory: crop+bbox+caption captured at pin time, stored per step.
        expect(bool(sf & {"visual", "crop", "visual_memory"}),
               "CachedStep visual-memory artifact (crop/bbox/caption) not yet stored", aspirational=True),
        # Partial credit: the tier-2 regrounder's plug-in slot exists — a local pointing model
        # would ship as a GroundingProvider impl, and that protocol is already public.
        expect(bool(ok_v) and hasattr(vision, "GroundingProvider"),
               "GroundingProvider protocol shipped (the tier-2 pointing-model plug-in slot)"),
    ]


@scenario(
    id="h06.harvest.offline_page_wide",
    title="offline page-wide property harvest + 0-LLM Similo scorer (ultracua.heal.*)",
    group="h06", aspirational=True, tags=("heal", "similo", "harvest"),
)
async def offline_page_wide_harvest(ctx: Ctx):
    """H6 plan steps 2-3: `heal/harvest.py` (a single-evaluate page-wide specOf pass WITHOUT the
    viewport filter or the 80-element cap — the runtime snapshot cannot feed Similo) and
    `heal/similo.py` (a pure-Python weighted scorer emitting candidate + confidence + top-2
    margin, refusing on narrow margins). Neither exists yet; probe by import."""
    ok_loc, loc = import_probe("ultracua.locators")
    ok_pkg, _pkg = import_probe("ultracua.heal")
    ok_h, harvest = import_probe("ultracua.heal.harvest")
    ok_s, similo = import_probe("ultracua.heal.similo")
    return [
        # Partial credit: the shared specOf capture the harvest would reuse is ALREADY factored
        # out (_SPECOF_JS is the single capture source for learn AND record — parity by
        # construction; the plan names it as harvest's reuse point).
        expect(bool(ok_loc) and hasattr(loc, "_SPECOF_JS") and callable(getattr(loc, "describe", None)),
               "shared specOf capture already factored for harvest reuse (_SPECOF_JS + describe)"),
        expect(bool(ok_pkg), "ultracua.heal package not yet present", aspirational=True),
        # The harvest must be page-WIDE (no viewport filter, no element cap) and offline-only —
        # a module that never gets called from the replay path.
        expect(bool(ok_h) and any(callable(getattr(harvest, n, None))
                                  for n in ("harvest", "harvest_page", "page_harvest")),
               "page-wide property harvest (no viewport filter / element cap) not yet built",
               aspirational=True),
        # The scorer is the 0-LLM heart of Tier 1: stored spec vs harvest, with confidence and a
        # top-2 margin so a narrow margin REFUSES instead of best-guessing (repo constraint #2).
        expect(bool(ok_s) and any(callable(getattr(similo, n, None))
                                  for n in ("score", "rank", "relocate", "similo_score")),
               "0-LLM Similo scorer (candidate, confidence, top-2 margin) not yet built",
               aspirational=True),
    ]


@scenario(
    id="h06.healpr.flow_verbs",
    title="heal-PR pipeline surface: flows.heal -> HealProposal -> heal-approve (human-gated re-entry)",
    group="h06", aspirational=True, tags=("heal", "heal-pr", "trust"),
)
async def healpr_flow_verbs(ctx: Ctx):
    """H6 plan steps 5-6: a `flows.heal(spec)` verb producing a reviewable HealProposal bundle
    (old/new spec, before/after evidence, confidence), a heal-approve verb applying the repin
    under the meta lock (write flows forced back to unapproved), and a heal-job queue fed by
    canary / run_all failures. Probe the verb surface; give credit for the shipped halves the
    pipeline composes (canary trigger + human approval gate)."""
    import dataclasses

    import ultracua.flows as flows

    meta_fields = {f.name for f in dataclasses.fields(flows.FlowMeta)}
    return [
        expect(callable(getattr(flows, "heal", None)),
               "flows.heal verb (emit a reviewable HealProposal, never a runtime patch) not yet built",
               aspirational=True),
        expect(hasattr(flows, "HealProposal"),
               "HealProposal evidence bundle (old/new spec, evidence, confidence) not yet defined",
               aspirational=True),
        expect(any(callable(getattr(flows, n, None)) for n in ("heal_approve", "approve_heal", "apply_heal")),
               "heal-approve verb (apply repin under the meta lock; writes -> unapproved) not yet built",
               aspirational=True),
        expect(any(hasattr(flows, n) for n in ("heal_queue", "enqueue_heal", "HealQueue")),
               "canary-failure -> heal-job queue not yet built", aspirational=True),
        # Partial credit: the trigger source the bot consumes is shipped — canary probes the
        # entry page + first locator with no actions/writes/health record, fleet-wide too.
        expect(callable(getattr(flows, "canary", None)) and callable(getattr(flows, "canary_all", None)),
               "canary trigger source shipped (the failure feed heal jobs would consume)"),
        # Partial credit: the re-entry gate is shipped — healed flows may only come back through
        # the same approve/unapprove trust surface every flow already uses.
        expect(callable(getattr(flows, "approve", None)) and callable(getattr(flows, "unapprove", None))
               and "approved" in meta_fields,
               "human approval gate shipped (the ONLY re-entry path for healed flows)"),
    ]
