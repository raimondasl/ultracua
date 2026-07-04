"""H12 evals: talk-through & point-and-teach recorder (ROADMAP.md H12).

The horizon: an opt-in NARRATION channel for the headed recorder — mic capture during the demo,
local ASR, timestamp alignment to the action trace, and ONE record-time LLM fusion pass (the
exact `caption_intents` shape: opt-in, best-effort, degrade to placeholders) compiling narration
into (a) human-authored intent captions, (b) per-write confirm predicates VALIDATED against the
recorded demo's own end-state before attach, and (c) inert `slot_hint` annotations. Point-and-
teach is a pointing AID: a local grounding model highlights the candidate element, the human
still physically clicks it, so grounding output provably never enters the flow artifact.

What is measured here:
- PARTIAL CREDIT (shipped today, key-less): the caption machinery the fusion pass extends —
  `record(caption=...)` runs end-to-end with an injected captioner (captions replace placeholder
  intents, typed values are redacted from the captioner's input, failures degrade to
  placeholders), and the upgrade-only classify asymmetry `fuse_narration` must preserve; plus
  the per-write barrier attach machinery (`StepConfirm` frozen at record time) and the
  `resolve(unique=True)` primitive the demo-validation step is specified to use.
- ASPIRATIONAL (expected `missing`): `narrate=` on the record surface, `fuse_narration`, event
  timestamps in the capture payload, demo-validated confirm predicates, the narration-provenance
  marker, `CachedStep.slot_hint`, and `vision.LocalGrounding` + the point-and-teach surface.

Everything here is key-less: local Fixture pages, real headless Chromium, injected fake
captioners (a captioner is just an async (goal, steps) -> intents callable), $0.
"""

from __future__ import annotations

from evals.core import Ctx, expect, import_probe, scenario
from evals.fixtures import Fixture, page


def _echo_captioner(prefix: str, sink: dict):
    """A count-matched fake 'fusion pass': labels step i as '<prefix> i' and records what it was
    shown. Always returns exactly one intent per step, so the caption is never dropped for a
    count mismatch — the shipped contract under test is the WIRING, not a real model's output."""

    async def _c(goal, steps):
        sink["goal"] = goal
        sink["steps"] = [dict(s) for s in steps]
        return [f"{prefix} {i}" for i in range(len(steps))]

    return _c


def _fixed_captioner(intents: list):
    async def _c(goal, steps):
        return list(intents)

    return _c


@scenario(
    id="h12.caption.fused_intents_keyless",
    title="shipped caption lane: record(caption=...) fuses injected intents, redacts typed values, degrades safely",
    group="h12", tags=("recorder", "caption", "narration"),
    notes="H12 plan step 2 rides on the shipped caption_intents contract — measured end-to-end with a fake captioner",
)
async def fused_intents_keyless(ctx: Ctx):
    """The fusion pass H12 specifies ('exact caption_intents shape') already has its record-time
    seam shipped: `record(caption=...)` runs ONE best-effort labeling call whose output replaces
    the placeholder intents in the cached flow. This scenario measures that seam key-less — an
    injected captioner stands in for narration fusion, so what passes here is exactly the wiring
    fuse_narration will reuse."""
    import json

    from ultracua.cache import flow_key
    from ultracua.flows import FlowSpec, record

    checks = []
    fx = Fixture({
        # A type + a navigating click: the two capture shapes the caption summary must cover
        # (a redacted text field and a named commit).
        "/read": page('<input id="q" aria-label="Search query"><a href="/done">Continue</a>'),
        "/done": page("<h1>done</h1>"),
    })
    with fx.serve() as base:
        cache = ctx.cache()

        async def _demo(pg) -> None:
            await pg.fill("#q", "s3cret-tok3n")     # the literal a human typed — may be a password
            await pg.locator("#q").blur()           # commit the edit -> a `change` -> a `type` step
            await pg.get_by_role("link", name="Continue").click()

        sink: dict = {}
        spec = FlowSpec(name="h12cap", start_url=base + "/read", goal="search then continue to done")
        res = await record(spec, demo=_demo, headless=True, cache=cache,
                           caption=_echo_captioner("narrated intent", sink))
        flow = cache.get(flow_key(spec.goal, spec.start_url, spec.scope))
        # The headline shipped behavior: the injected captions REPLACED the placeholder intents
        # in the cached (verify-by-replayed) flow — the exact substitution narration fusion needs.
        checks.append(expect(res.cached and flow is not None
                             and all(s.intent.startswith("narrated intent") for s in flow.steps),
                             "injected captions replace placeholder intents in the cached flow",
                             f"cached={res.cached} intents={[s.intent for s in flow.steps] if flow else None}"))
        # Privacy floor for the future transcript path: the captioner's step summary must NEVER
        # carry a `type` step's literal value (spoken/typed credentials are H12's top privacy
        # risk); the field's accessible name + goal are what it labels from.
        checks.append(expect(bool(sink.get("steps"))
                             and any(s.get("action") == "type" and s.get("text") is None
                                     for s in sink.get("steps", []))
                             and "s3cret-tok3n" not in json.dumps(sink.get("steps", [])),
                             "typed value is redacted from the captioner input (name+goal only)",
                             f"captioner saw steps={sink.get('steps')}"))

        # Best-effort contract: a captioner OUTAGE must never break recording — the flow still
        # caches with placeholder intents (the same degrade path a failed fusion pass must take).
        async def _boom(goal, steps):
            raise RuntimeError("captioner outage")

        async def _demo2(pg) -> None:
            await pg.get_by_role("link", name="Continue").click()

        spec2 = FlowSpec(name="h12cap2", start_url=base + "/read", goal="continue to done")
        res2 = await record(spec2, demo=_demo2, headless=True, cache=cache, caption=_boom)
        flow2 = cache.get(flow_key(spec2.goal, spec2.start_url, spec2.scope))
        checks.append(expect(res2.cached and flow2 is not None
                             and all(s.intent.startswith(s.action) for s in flow2.steps),
                             "a raising captioner degrades to placeholder intents; the flow still caches",
                             f"cached={res2.cached} intents={[s.intent for s in flow2.steps] if flow2 else None}"))
        # Caption + record + verify-by-replay are all READ-path here: nothing may write.
        checks.append(expect(not fx.writes, "the caption lane sent no write to the server",
                             f"writes={[(w.method, w.path) for w in fx.writes]}"))
    return checks


@scenario(
    id="h12.caption.write_upgrade_asymmetry",
    title="the classify asymmetry fuse_narration must preserve: captions upgrade a declared write, never a read",
    group="h12", tags=("recorder", "caption", "write-safety"),
    notes="H12 risk item: mis-fused narration must never guess-attach a write; upgrades are scoped + declared-only",
)
async def write_upgrade_asymmetry(ctx: Ctx):
    """H12's plan says the fusion pass must 'preserve the caption asymmetry': a better intent may
    UPGRADE a bland commit to mutating (gated on its own scope) in a DECLARED write flow, but a
    caption that invents a 'submit' keyword must never reclassify — or false-refuse — a benign
    READ. Both halves are shipped today; measuring them pins the invariant narration fusion
    inherits."""
    from ultracua.cache import flow_key
    from ultracua.flows import FlowSpec, MutateSpec, record

    checks = []
    fx = Fixture({
        "/account": page("<h1>Account</h1><button id=x>Manage</button>"),  # bland commit, no wire write
        "/read": page('<a href="/done">Continue</a>'),
        "/done": page("<h1>done</h1>"),
    })
    with fx.serve() as base:
        cache = ctx.cache()

        # DECLARED write flow: the bland 'Manage' click fires no wire write and no form submit,
        # so only the caption's keyword side can catch it — the upgrade must land WITH the step's
        # own precond_scope (an ungated write would fire blind under drift).
        async def _write_demo(pg) -> None:
            await pg.get_by_role("button", name="Manage").click()

        wspec = FlowSpec(name="h12w", start_url=base + "/account", goal="manage the account",
                         mutate=MutateSpec(confirm_text_contains="done"))
        wres = await record(wspec, demo=_write_demo, headless=True, cache=cache,
                            caption=_fixed_captioner(["delete the account"]))
        wflow = cache.get(flow_key(wspec.goal, wspec.start_url, wspec.scope))
        wstep = wflow.steps[0] if wflow and wflow.steps else None
        checks.append(expect(wres.cached and wres.is_write and wstep is not None
                             and wstep.intent == "delete the account"
                             and wstep.mutating and bool(wstep.precond_scope),
                             "a write-keyword caption upgrades a bland DECLARED commit to mutating + gated",
                             f"cached={wres.cached} step={(wstep.intent, wstep.mutating, bool(wstep.precond_scope)) if wstep else None}"))

        # READ flow, same mutating-keyword caption: the intent text is relabeled but the step
        # must stay non-mutating and the flow must still cache — no false refusal of a read.
        async def _read_demo(pg) -> None:
            await pg.get_by_role("link", name="Continue").click()

        rspec = FlowSpec(name="h12r", start_url=base + "/read", goal="continue to done")
        rres = await record(rspec, demo=_read_demo, headless=True, cache=cache,
                            caption=_fixed_captioner(["submit the order"]))
        rflow = cache.get(flow_key(rspec.goal, rspec.start_url, rspec.scope))
        rstep = rflow.steps[0] if rflow and rflow.steps else None
        checks.append(expect(rres.cached and rstep is not None
                             and rstep.intent == "submit the order" and rstep.mutating is False,
                             "a mutating-keyword caption never reclassifies (or refuses) a benign READ",
                             f"cached={rres.cached} note={rres.note!r} "
                             f"step={(rstep.intent, rstep.mutating) if rstep else None}"))
        # The oracle: neither demo (nor the read's verify-by-replay) put a write on the wire —
        # the upgrade above was purely classification, not an actual submission.
        checks.append(expect(not fx.writes, "no write reached the server in either flow",
                             f"writes={[(w.method, w.path) for w in fx.writes]}"))
    return checks


@scenario(
    id="h12.narrate.talkthrough_channel",
    title="narration channel: narrate= capture, fuse_narration, event timestamps, local ASR machinery",
    group="h12", aspirational=True, tags=("recorder", "narration", "asr"),
    notes="H12 plan steps 1-2: ts in the capture payload, narrate=True mic capture + faster-whisper, fuse_narration",
)
async def talkthrough_channel(ctx: Ctx):
    """H12 plan steps 1-2 name the narration surfaces exactly: `ts` stamped on each capture event
    (for transcript alignment), `narrate=True` on the record surface (mic capture + local ASR,
    audio deleted after transcription), and `recorder.fuse_narration` — the ONE record-time
    fusion call copying the `caption_intents` contract. All probed by signature/attribute so a
    not-built-yet surface reports `missing`, never a crash."""
    import inspect
    import re

    import ultracua.flows as flows
    import ultracua.recorder as recorder

    rd_params = set(inspect.signature(recorder.record_demo).parameters)
    rec_params = set(inspect.signature(flows.record).parameters)
    ok_n, _ = import_probe("ultracua.narration")
    return [
        # Partial credit: the contract fuse_narration is specified to COPY is shipped — the
        # opt-in, best-effort caption seam (record_demo takes a caption callable) measured
        # end-to-end in h12.caption.fused_intents_keyless.
        expect(callable(getattr(recorder, "caption_intents", None)) and "caption" in rd_params,
               "caption_intents contract + record_demo(caption=...) seam shipped (fusion's template)",
               f"record_demo params={sorted(rd_params)}"),
        # The opt-in narration switch on the record surface (mic capture during the demo).
        expect("narrate" in rd_params or "narrate" in rec_params,
               "narrate= opt-in not yet on record_demo / flows.record",
               f"record params={sorted(rec_params)}", aspirational=True),
        # The fusion pass itself: (router, goal, steps, transcript) -> captions + predicate +
        # slot candidates, windowed ts/seq alignment, unalignable segments degrade to placeholders.
        expect(callable(getattr(recorder, "fuse_narration", None)),
               "recorder.fuse_narration (the ONE record-time fusion pass) not yet built",
               aspirational=True),
        # Plan step 1: `ts` on each capture event. Events are transient (never serialized into
        # CachedStep), so the honest probe is the capture payload itself — today the store()
        # push carries {action, spec, value, ctx, scope, seq} and no timestamp.
        expect(bool(re.search(r"\bts\s*:", getattr(recorder, "_CAPTURE_JS", "") or "")),
               "capture events carry no `ts` timestamp yet (transcript alignment basis)",
               aspirational=True),
        # The ASR half: local transcription machinery (sounddevice + faster-whisper per the plan),
        # whether it lands as a module or recorder-level helpers.
        expect(bool(ok_n) or any(callable(getattr(recorder, n, None))
                                 for n in ("transcribe", "capture_narration", "NarrationCapture")),
               "local ASR / narration-capture machinery not yet present", aspirational=True),
    ]


@scenario(
    id="h12.predicates.demo_validated_confirms",
    title="demo-validated per-write confirm predicates: attach machinery shipped, end-state validation missing",
    group="h12", aspirational=True, tags=("recorder", "write-safety", "predicates"),
    notes="H12 plan step 3: fused confirms checked against the demo's own end-state via resolve(unique=True)",
)
async def demo_validated_confirms(ctx: Ctx):
    """H12's 'stricter fail-loud write barriers for free': narration-fused confirm predicates must
    be VALIDATED against the recorded demo's own end-state before attach (dropped loudly if they
    don't hold — a hallucinated predicate that flaps on replay erodes the fail-loud signal). The
    attach machinery predicates ride on is shipped (StepConfirm frozen onto the gated write at
    record time); the validation step is not: today a predicate that provably does NOT hold on
    the demo's end-state still attaches untested."""
    import inspect

    from ultracua.cache import StepConfirm, flow_key
    from ultracua.flows import FlowSpec, MutateSpec, record

    checks = []
    fx = Fixture({
        "/form": page('<form action="/submit" method="post"><button type="submit">Place order</button></form>'),
        "/thanks": page("<h1>Thanks for your order</h1>"),
    }, post_redirect="/thanks")
    with fx.serve() as base:
        cache = ctx.cache()

        async def _demo(pg) -> None:
            await pg.get_by_role("button", name="Place order").click()

        # Record A: a per-write barrier whose predicate DOES hold on the demo end-state ("Thanks"
        # is on /thanks) — the survivor a future validation pass must keep.
        spec_a = FlowSpec(name="h12wa", start_url=base + "/form", goal="place the order",
                          mutate=MutateSpec(confirm_url_contains="thanks",
                                            step_confirms=[StepConfirm(confirm_text_contains="Thanks")]))
        res_a = await record(spec_a, demo=_demo, headless=True, cache=cache)
        flow_a = cache.get(flow_key(spec_a.goal, spec_a.start_url, spec_a.scope))
        step_a = flow_a.steps[0] if flow_a and flow_a.steps else None
        # Baseline gate: without a captured+gated write the attach checks below mean nothing.
        checks.append(expect(res_a.cached and res_a.is_write and step_a is not None
                             and step_a.mutating and bool(step_a.precond_scope),
                             "a demonstrated form-submit write is captured GATED (mutating + precond_scope)",
                             f"cached={res_a.cached} note={res_a.note!r}"))
        if step_a is None:
            return checks
        # Partial credit: the attach machinery H12's predicates flow through — the StepConfirm is
        # FROZEN into the cached write step at record time (ordinal binding, human-reviewable).
        checks.append(expect(step_a.confirm is not None
                             and step_a.confirm.confirm_text_contains == "Thanks",
                             "per-write barrier freezes into the cached write step (attach machinery shipped)",
                             f"confirm={step_a.confirm}"))

        # Record B: a predicate that provably does NOT hold on the demo's own end-state. A
        # demo-validating recorder (plan step 3) drops it loudly or refuses; today it attaches
        # untested -> the capability is `missing`.
        spec_b = FlowSpec(name="h12wb", start_url=base + "/form", goal="place the order again",
                          mutate=MutateSpec(confirm_url_contains="thanks",
                                            step_confirms=[StepConfirm(
                                                confirm_text_contains="token-absent-from-demo-endstate")]))
        res_b = await record(spec_b, demo=_demo, headless=True, cache=cache)
        flow_b = cache.get(flow_key(spec_b.goal, spec_b.start_url, spec_b.scope))
        step_b = flow_b.steps[0] if flow_b and flow_b.steps else None
        checks.append(expect((not res_b.cached) or step_b is None or step_b.confirm is None,
                             "a predicate that does NOT hold on the demo's end-state is not yet "
                             "validated/dropped at record time",
                             f"cached={res_b.cached} confirm={getattr(step_b, 'confirm', None)}",
                             aspirational=True))
        # Write safety around validation: exactly the two demo writes hit the wire — record never
        # re-fires a write to verify anything (which is WHY validation must read the demo's own
        # end-state instead of replaying).
        checks.append(expect(len(fx.writes) == 2
                             and all(w.method == "POST" and w.path == "/submit" for w in fx.writes),
                             "only the demos' own writes hit the wire (a write is never re-fired to verify)",
                             f"writes={[(w.method, w.path) for w in fx.writes]}"))

    # The validation primitive plan step 3 names: resolve(unique=True) — ambiguity fails loud, so
    # a predicate can only validate against ONE unambiguous element. Already shipped.
    from ultracua import locators

    checks.append(expect("unique" in inspect.signature(locators.resolve).parameters,
                         "resolve(unique=True) validation primitive shipped (ambiguity fails loud)"))
    # Prunability: validated-on-demo is necessary, not sufficient (personalization/animation can
    # still flap a predicate) — the plan marks fused confirms as narration-derived so a human can
    # prune them in inspect output. No provenance field exists yet.
    checks.append(expect(bool(set(StepConfirm.model_fields)
                              & {"source", "provenance", "narration_derived", "derived_from"}),
                         "StepConfirm carries no narration-provenance marker yet (needed for pruning)",
                         aspirational=True))
    return checks


@scenario(
    id="h12.pointing.grounding_aid_and_slots",
    title="point-and-teach aid (vision.LocalGrounding) + inert CachedStep.slot_hint annotations",
    group="h12", aspirational=True, tags=("recorder", "grounding", "slots"),
    notes="H12 plan steps 4-5: slot_hint metadata (no runtime substitution) + local grounding as a pointing AID",
)
async def grounding_aid_and_slots(ctx: Ctx):
    """H12 plan step 5: a LOCAL grounding model (`vision.LocalGrounding`) highlights the candidate
    element for a typed/spoken instruction — the human still physically clicks it, so grounding
    output provably never enters the flow artifact. Plan step 4: narration-derived slot/
    variability annotations stored as an INERT, reviewable `slot_hint` on CachedStep (no runtime
    substitution until the slots runtime reworks the idempotency-key basis). All field/attribute
    probes — nothing here needs a model."""
    import inspect

    from ultracua.cache import CachedStep

    import ultracua.recorder as recorder

    ok_v, vision = import_probe("ultracua.vision")
    step_fields = set(CachedStep.model_fields)
    rd_params = set(inspect.signature(recorder.record_demo).parameters)
    return [
        # Partial credit: the plug-in slot a local pointing model implements is public — the
        # GroundingProvider protocol (+ MockGrounding for key-less tests) already ships.
        expect(bool(ok_v) and hasattr(vision, "GroundingProvider") and hasattr(vision, "MockGrounding"),
               "GroundingProvider protocol + MockGrounding shipped (the pointing-model plug-in slot)"),
        # The local grounding implementation itself (Holo2-class GGUF via a local llama.cpp
        # server per the plan) — only AnthropicGrounding (cloud) exists today.
        expect(bool(ok_v) and hasattr(vision, "LocalGrounding"),
               "vision.LocalGrounding (local pointing model) not yet built", aspirational=True),
        # The recorder-side aid: instruction -> ground -> hit-test -> highlight overlay, with
        # low-confidence grounds showing candidates or declining (fail loud, never guess).
        expect(any(callable(getattr(recorder, n, None))
                   for n in ("point_and_teach", "ground_instruction", "highlight_candidate"))
               or bool(rd_params & {"grounding", "pointer", "point_and_teach"}),
               "point-and-teach surface not yet on the recorder", aspirational=True),
        # ARTIFACT PURITY (a shipped invariant, checked so a future LocalGrounding can't erode
        # it): because the human clicks, the captured step is an ordinary locator step — no
        # grounding output may ever serialize into the flow artifact.
        expect(not (step_fields & {"grounding", "grounded_by", "grounding_confidence", "highlight"}),
               "grounding output stays OUT of the flow artifact (no grounding fields on CachedStep)",
               f"fields={sorted(step_fields)}"),
        # Plan step 4: the inert slot annotation (StepConfirm's additive-Optional precedent —
        # no schema bump). Runtime substitution is explicitly NOT probed here: it is blocked on
        # the slots runtime reworking safety.idempotency_key / flow_key first.
        expect(bool(step_fields & {"slot_hint", "slot", "slots", "slot_hints"}),
               "CachedStep.slot_hint (inert, reviewable slot annotation) not yet stored",
               aspirational=True),
        # Partial credit for the precedent slot_hint is specified to copy: CachedStep already
        # carries an additive Optional field (confirm) that deserializes old flows unchanged.
        expect("confirm" in step_fields and CachedStep.model_fields["confirm"].default is None,
               "additive-Optional CachedStep field precedent shipped (slot_hint's extension path)"),
    ]
