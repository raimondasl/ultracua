"""H8 evals: action-breadth verification pack — files + tabs + deep DOM (key-less, aspirational).

ROADMAP H8: staged breadth with verification contracts. (1) FILE verbs — `download`/`upload`
ActionTypes with Playwright's blocking expect_download/save_as barrier and an `ArtifactContract`
(filename glob + MIME + magic-bytes, strict by default) so an error page renamed `.pdf` fails
LOUD; upload sha256 manifests with a per-flow idempotency basis. (2) VOLATILE-ID locator
blocklist (`ember\\d+`, React useId, GUIDs) so specs never rank on session-random ids — the
Salesforce-class enterprise win. (3) TAB GRAPH — opener lineage + settled-URL identity (never
index), `expect_popup`/`switch_tab` verbs, loud failure on unexpected/missing/ambiguous tabs.
(4) same-origin iframe + open-shadow perception via `frame_path` addressing across the whole
stack (the one deliberate SCHEMA_VERSION bump).

Partial credit measured today (the shipped substrate each stage is specified to ride on):
- Playwright's expect_download + save_as blocking barrier works THROUGH a BrowserSession click
- the recorder already captures a file-input interaction (as a `type` — the documented mis-capture)
- describe() captures the resilient role+name tier, and resolve() survives a ROTATED volatile id
  when role+name stays unique — and fails LOUD (None) when it doesn't, instead of guessing
- popups surface on the session's context (the substrate a context.on('page') registry builds on)
- Playwright's frame tree reaches an in-iframe element (what a descending SNAPSHOT_JS will walk)

Everything here is key-less: local Fixture pages, real headless Chromium, scripted demos, $0.
"""

from __future__ import annotations

from evals.core import Ctx, expect, fail, import_probe, scenario
from evals.fixtures import Fixture, page


def _action_verbs() -> set:
    """The shipped canonical action vocabulary. ActionType is a Literal, so probing a future verb
    is a membership test — constructing Action(action="download") would raise pydantic's
    ValidationError (a ValueError, NOT in MISSING_EXC), so we never construct to probe."""
    from typing import get_args

    from ultracua.types import ActionType

    return set(get_args(ActionType))


@scenario(
    id="h08.files.download_barrier_contract",
    title="download verb + ArtifactContract: blocking save barrier works today, the verb/contract don't exist",
    group="h08", aspirational=True, tags=("files", "download", "artifact"),
    notes="H8 stage 1: expect_download+save_as as the step barrier; ArtifactContract on CachedStep",
)
async def download_barrier_contract(ctx: Ctx):
    import ultracua.cache as cache_mod
    import ultracua.flows as flows_mod
    from ultracua.browser import BrowserSession
    from ultracua.types import Action

    checks = []
    payload = "H8-ARTIFACT-BYTES not-an-error-page\n"
    fx = Fixture({
        "/": page('<a id="dl" href="/report.bin" download="report.bin">download the report</a>'),
        "/report.bin": payload,  # served as-is; the `download` attribute makes Chromium save it
    })
    with fx.serve() as base:
        session = await BrowserSession(headless=True).start()
        try:
            await session.goto(base + "/")
            obs = await session.snapshot()
            ref = next((e.ref for e in obs.elements
                        if e.role == "link" and "download the report" in e.name), None)
            if ref is None:  # a plain <a href> must be perceivable — that's SHIPPED behavior
                checks.append(fail("snapshot perceives the download link",
                                   f"elements={[(e.role, e.name) for e in obs.elements]}"))
                return checks
            # PARTIAL CREDIT (shipped substrate): Playwright's BLOCKING download barrier — the
            # exact primitive H8's `download` verb is specified to ride on (NOT experimental raw
            # CDP lifecycle events) — completes through an ordinary ultracua session.act() click.
            saved = ctx.tmp / "report.bin"
            try:
                async with session.page.expect_download() as dl_info:
                    await session.act(Action(action="click", intent="download the report", ref=ref))
                download = await dl_info.value
                await download.save_as(str(saved))
            except Exception as exc:  # noqa: BLE001 — a broken shipped primitive is a real fail
                checks.append(fail("expect_download + save_as barrier completes via session.act",
                                   f"{type(exc).__name__}: {exc}"))
                return checks
            checks.append(expect(download.suggested_filename == "report.bin",
                                 "expect_download + save_as barrier completes via session.act",
                                 f"suggested_filename={download.suggested_filename!r}"))
            # The download IS the data (artifact contracts are the read-side twin of the Phase-G
            # write barriers): the saved bytes must be exactly what the server sent — the
            # integrity floor an ArtifactContract's magic-bytes probe will formalize.
            got = saved.read_text(encoding="utf-8") if saved.exists() else None
            checks.append(expect(got == payload,
                                 "the saved artifact's bytes are intact (the contract's integrity floor)",
                                 f"exists={saved.exists()} got={got!r}"))
        finally:
            await session.close()

    # The first-class `download` ActionType (H8 stage 1) — today the vocabulary has no file verbs,
    # so a learned/recorded flow cannot EXPRESS a download step at all.
    checks.append(expect("download" in _action_verbs(),
                         "'download' is a first-class ActionType verb",
                         f"verbs={sorted(_action_verbs())}", aspirational=True))
    # The ArtifactContract itself (filename glob + MIME + magic-bytes, strict by default) —
    # probed wherever it could plausibly land (cache.py per the plan; flows; its own module).
    found_art_mod, _ = import_probe("ultracua.artifacts")
    has_contract = (found_art_mod or hasattr(cache_mod, "ArtifactContract")
                    or hasattr(flows_mod, "ArtifactContract"))
    checks.append(expect(has_contract, "ArtifactContract type exists",
                         "no artifact contract anywhere — an error page renamed .pdf would today "
                         "have NO check behind it", aspirational=True))
    # The contract must travel WITH the step (an Optional CachedStep field, StepConfirm precedent)
    # so replay enforces it 0-LLM the moment the download lands.
    step_fields = set(cache_mod.CachedStep.model_fields)
    checks.append(expect(any(n in step_fields for n in ("artifact", "artifact_contract", "contract")),
                         "CachedStep carries an artifact-contract field",
                         f"fields={sorted(step_fields)}", aspirational=True))
    return checks


@scenario(
    id="h08.files.upload_verb_capture",
    title="a demoed file-input set records as a first-class upload step (today: mis-captured as `type`)",
    group="h08", aspirational=True, tags=("files", "upload", "recorder"),
    notes="H8 stage 1 recorder fix: map file-input change events to `upload` (today replay fill() throws)",
)
async def upload_verb_capture(ctx: Ctx):
    import dataclasses

    from ultracua.flow import FlowReport
    from ultracua.flows import MutateSpec
    from ultracua.recorder import record_demo

    checks = []
    attachment = ctx.tmp / "invoice.txt"
    attachment.write_text("invoice #42\n", encoding="utf-8")
    fx = Fixture({"/": page('<form><label for="doc">Invoice file</label>'
                            '<input type="file" id="doc"></form>')})

    async def _demo(pg) -> None:
        # set_input_files fires the `change` event the recorder's capture listener sees.
        await pg.set_input_files("#doc", str(attachment))

    with fx.serve() as base:
        flow, _, _, _ = await record_demo(base + "/", _demo, goal="attach the invoice file",
                                          cache=ctx.cache(), headless=True)
        touched = next((s for s in flow.steps if s.locator and s.locator.elem_id == "doc"), None)
        # PARTIAL CREDIT (shipped substrate): the recorder's change listener DOES see the
        # file-input interaction — the capture plumbing the `upload` verb will reuse exists.
        checks.append(expect(touched is not None,
                             "the recorder captures the file-input interaction at all",
                             f"steps={[(s.action, s.locator.elem_id if s.locator else None) for s in flow.steps]}"))
        # The H8 fix: that event must become a first-class `upload` step. Today it is mis-captured
        # as a `type` whose text is the browser's C:\fakepath\... value — replaying it calls
        # fill() on a file input, which THROWS (the documented silent hole, ROADMAP H8 plan).
        checks.append(expect(touched is not None and touched.action == "upload",
                             "the file-input set records as an `upload` step",
                             f"today action={getattr(touched, 'action', None)!r} "
                             f"text={getattr(touched, 'text', None)!r} (replay fill() on a file "
                             "input throws)", aspirational=True))

    # The verb in the canonical vocabulary (same gap as `download`: inexpressible today).
    checks.append(expect("upload" in _action_verbs(),
                         "'upload' is a first-class ActionType verb",
                         f"verbs={sorted(_action_verbs())}", aspirational=True))
    # Upload idempotency is a REAL fork (content-hash re-fires on regenerated files; path-hash
    # wrongly dedupes changed ones) — H8 requires an EXPLICIT per-flow basis declaration on the
    # write spec, never a default. Field scan on the dataclass: immune to unrelated ctor errors.
    mut_fields = set(MutateSpec.__dataclass_fields__)
    basis_names = ("upload_idempotency_basis", "upload_basis", "idempotency_basis")
    checks.append(expect(any(n in mut_fields for n in basis_names),
                         "MutateSpec declares an explicit upload idempotency basis",
                         f"none of {basis_names} on MutateSpec", aspirational=True))
    # PARTIAL CREDIT (shipped substrate): FlowReport.extra — the surface the plan names for the
    # upload sha256 manifest — already exists on the report.
    checks.append(expect("extra" in {f.name for f in dataclasses.fields(FlowReport)},
                         "FlowReport.extra exists (where the upload sha256 manifest will surface)"))
    return checks


@scenario(
    id="h08.locators.volatile_id_blocklist",
    title="volatile-ID blocklist: specs must not rank on session-random ids (ember1234 -> ember9999)",
    group="h08", aspirational=True, tags=("locators", "drift", "salesforce"),
    notes="H8 stage 2: regex blocklist in _SPECOF_JS cssPath/elem_id — the Salesforce-class enterprise win",
)
async def volatile_id_blocklist(ctx: Ctx):
    from ultracua.browser import BrowserSession
    from ultracua.locators import describe, resolve

    checks = []
    fx = Fixture({
        # An Ember/Lightning-style session-random id: a DIFFERENT id every visit.
        "/": page('<button id="ember1234">Save draft</button>'),
        # The same page next session: the id ROTATED (the canonical Salesforce locator pain).
        "/rotated": page('<button id="ember9999">Save draft</button>'),
        # And the hard case: role+name AMBIGUOUS with the recorded id gone — no landmark anchors.
        "/twins": page('<button id="ember5678">Save draft</button>'
                       '<button id="ember9012">Save draft</button>'),
    })
    with fx.serve() as base:
        session = await BrowserSession(headless=True).start()
        try:
            await session.goto(base + "/")
            obs = await session.snapshot()
            ref = next((e.ref for e in obs.elements if e.role == "button"), None)
            spec = await describe(session.page, ref) if ref else None
            if spec is None:  # describe() on a snapshot-tagged button is SHIPPED behavior
                checks.append(fail("describe() captures a LocatorSpec for the button",
                                   f"ref={ref!r} spec=None"))
                return checks
            # PARTIAL CREDIT (shipped substrate): the resilient Tier-1 hints — role + accessible
            # name — are captured; the blocklist only removes the BRITTLE hints beneath them.
            checks.append(expect(spec.role == "button" and spec.name == "Save draft",
                                 "describe() captures the resilient role+name tier",
                                 f"role={spec.role!r} name={spec.name!r}"))
            # H8 stage 2: a session-random id must never be RECORDED as an identity hint.
            # Today _SPECOF_JS stores el.id verbatim -> the spec ranks on a value that is
            # guaranteed dead next session.
            checks.append(expect(spec.elem_id != "ember1234",
                                 "the spec does not store the volatile id as an identity hint",
                                 f"elem_id={spec.elem_id!r} (no ember\\d+/useId/GUID blocklist yet)",
                                 aspirational=True))
            # ...and the css path must not ANCHOR on it either. Today cssPath short-circuits at
            # the first id ('#ember1234'), so the whole structural fallback dies with the id.
            checks.append(expect("ember1234" not in (spec.css or ""),
                                 "the css path does not anchor on the volatile id",
                                 f"css={spec.css!r}", aspirational=True))
            # PARTIAL CREDIT (shipped resilience): with the id ROTATED but role+name still unique,
            # the Tier-1 confident locator re-finds the button — replay survives id churn when the
            # accessible name carries the identity.
            await session.goto(base + "/rotated")
            loc = await resolve(session.page, spec, unique=True)
            found_id = await loc.get_attribute("id") if loc is not None else None
            checks.append(expect(found_id == "ember9999",
                                 "resolve() survives a rotated volatile id via role+name (Tier 1)",
                                 f"resolved id={found_id!r}"))
            # PARTIAL CREDIT (shipped fail-loud): role+name ambiguous AND the recorded id gone —
            # every strategy is 0-or-2+ matches, so unique resolve must return None (drift, fail
            # loud downstream), never silently pick one of the twins. This is exactly the case the
            # blocklist + neighbor anchor combination is meant to make RESOLVABLE, but until then
            # refusing is the only correct answer.
            await session.goto(base + "/twins")
            twin = await resolve(session.page, spec, unique=True)
            checks.append(expect(twin is None,
                                 "ambiguous role+name with the id gone fails LOUD (no silent guess)",
                                 "resolve picked one of two identical buttons"))
        finally:
            await session.close()
    return checks


@scenario(
    id="h08.frames.deep_dom_perception",
    title="same-origin iframe + open-shadow perception: the snapshot is top-frame/light-DOM only today",
    group="h08", aspirational=True, tags=("frames", "shadow-dom", "perception"),
    notes="H8 stage 4: SNAPSHOT_JS descends same-origin frames + open shadow roots; frame_path on LocatorSpec",
)
async def deep_dom_perception(ctx: Ctx):
    from ultracua.browser import BrowserSession
    from ultracua.locators import LocatorSpec

    checks = []
    fx = Fixture({
        # The ONLY commit control lives inside a same-origin iframe (the enterprise-portal shape);
        # a second control lives inside an OPEN shadow root (the web-component shape).
        "/": page('<button id="top">Top action</button>'
                  '<iframe src="/frame" style="width:420px;height:160px"></iframe>'
                  '<div id="host"></div>'
                  '<script>const r = document.getElementById("host").attachShadow({mode:"open"});'
                  'const b = document.createElement("button"); b.textContent = "Shadow save";'
                  'r.appendChild(b);</script>'),
        "/frame": page('<button id="confirm">Confirm order</button>'),
    })
    with fx.serve() as base:
        session = await BrowserSession(headless=True).start()
        try:
            await session.goto(base + "/")
            # Wait for the child frame's DOM so a FUTURE frame-descending snapshot isn't
            # flake-missed here — and so the substrate check below measures a LOADED frame.
            # frame_locator auto-waits for the frame to attach (goto's domcontentloaded returns
            # before the iframe even exists in page.frames) — and it is the exact per-hop descent
            # primitive H8's resolve() extension is specified to use.
            confirm = session.page.frame_locator("iframe").locator("#confirm")
            try:
                await confirm.wait_for(state="attached")
                in_frame = await confirm.count()
            except Exception:  # noqa: BLE001 — counted as substrate absence below, never a crash
                in_frame = 0
            obs = await session.snapshot()
            names = {e.name for e in obs.elements}
            # PARTIAL CREDIT (shipped): top-frame perception works — the deep-DOM stage EXTENDS
            # this pipeline, it doesn't replace it.
            checks.append(expect("Top action" in names,
                                 "the snapshot perceives the top-frame control",
                                 f"names={sorted(names)}"))
            # PARTIAL CREDIT (shipped substrate): Playwright's frame tree reaches the in-frame
            # element — the descent path SNAPSHOT_JS/resolve will walk (frame_locator per hop)
            # already exists in the driver; only ultracua's use of it is missing.
            checks.append(expect(in_frame == 1,
                                 "Playwright's frame_locator reaches the in-iframe control (descent substrate)",
                                 f"count={in_frame} frames={[f.url for f in session.page.frames]}"))
            # H8 stage 4 — iframe perception: the only Confirm control on the page is INVISIBLE
            # to the observation today (SNAPSHOT_JS walks document.querySelectorAll in the top
            # document only), so a flow through this portal cannot even be EXPRESSED.
            checks.append(expect("Confirm order" in names,
                                 "the snapshot perceives the same-origin in-iframe control",
                                 "iframe blindness: SNAPSHOT_JS never descends frames",
                                 aspirational=True))
            # H8 stage 4 — open-shadow perception: querySelectorAll does not pierce shadow roots,
            # so the web-component button is equally invisible (Playwright itself pierces OPEN
            # roots — the gap is ours, not the driver's).
            checks.append(expect("Shadow save" in names,
                                 "the snapshot perceives the open-shadow-root control",
                                 "shadow blindness: the snapshot walk is light-DOM only",
                                 aspirational=True))
        finally:
            await session.close()
    # frame_path addressing (the ordered chain of iframe identifiers) on the spec — the field the
    # whole-stack descent hangs off, and the reason for the ONE deliberate SCHEMA_VERSION bump.
    checks.append(expect("frame_path" in LocatorSpec.model_fields,
                         "LocatorSpec carries frame_path addressing",
                         f"fields={sorted(LocatorSpec.model_fields)}", aspirational=True))
    return checks


@scenario(
    id="h08.tabs.popup_graph_verbs",
    title="tab graph: expect_popup/switch_tab verbs + tab-aware steps (popups surface but are unreachable)",
    group="h08", aspirational=True, tags=("tabs", "popup"),
    notes="H8 stage 3: context.on('page') registry, opener-lineage identity, loud failure on tab surprises",
)
async def popup_graph_verbs(ctx: Ctx):
    from ultracua.browser import BrowserSession
    from ultracua.cache import CachedStep
    from ultracua.types import Action

    checks = []
    fx = Fixture({
        "/": page('<a id="pop" href="/export" target="_blank">open the export window</a>'),
        "/export": page("<h1>Export ready</h1><p>your export is ready</p>", title="export"),
    })
    with fx.serve() as base:
        session = await BrowserSession(headless=True).start()
        try:
            await session.goto(base + "/")
            obs = await session.snapshot()
            ref = next((e.ref for e in obs.elements
                        if e.role == "link" and "export" in e.name), None)
            if ref is None:
                checks.append(fail("snapshot perceives the popup link",
                                   f"elements={[(e.role, e.name) for e in obs.elements]}"))
                return checks
            # PARTIAL CREDIT (shipped substrate): a same-origin popup opened by an ordinary
            # session.act() click SURFACES on the session's context — the event source
            # (context.on('page')) the H8 tab registry is specified to subscribe to.
            try:
                async with session.page.context.expect_page() as pg_info:
                    await session.act(Action(action="click", intent="open the export window", ref=ref))
                popup = await pg_info.value
                await popup.wait_for_load_state("domcontentloaded")
            except Exception as exc:  # noqa: BLE001 — the driver substrate failing is a real fail
                checks.append(fail("a popup opened via session.act surfaces on the context",
                                   f"{type(exc).__name__}: {exc}"))
                return checks
            checks.append(expect(popup.url.rstrip("/").endswith("/export")
                                 and len(session.page.context.pages) == 2,
                                 "a popup opened via session.act surfaces on the context (registry substrate)",
                                 f"popup.url={popup.url} pages={len(session.page.context.pages)}"))
            # H8 stage 3 — but the OBSERVATION cannot reach it: the session is hard-wired to the
            # opener page, so replay is structurally blind to anything in the new tab (agents
            # losing state across tab switches is a top documented failure class).
            obs2 = await session.snapshot()
            checks.append(expect("Export ready" in obs2.text or obs2.url.rstrip("/").endswith("/export"),
                                 "the session's observation can reach the popup's content",
                                 f"snapshot still sees the OPENER only: url={obs2.url}",
                                 aspirational=True))
        finally:
            await session.close()

    # The tab verbs in the canonical vocabulary: expect_popup (a loud barrier — popup within
    # timeout + unique URL match, else FlowReplayError) and switch_tab (identity by opener
    # lineage + settled-URL pattern, NEVER index).
    verbs = _action_verbs()
    checks.append(expect("expect_popup" in verbs and "switch_tab" in verbs,
                         "expect_popup + switch_tab are first-class ActionType verbs",
                         f"verbs={sorted(verbs)}", aspirational=True))
    # Steps must SAY which tab they run on (Optional CachedStep.tab; None = main page, so old
    # flows deserialize unchanged — the StepConfirm no-schema-bump precedent).
    checks.append(expect("tab" in CachedStep.model_fields,
                         "CachedStep carries a tab identity field",
                         f"fields={sorted(CachedStep.model_fields)}", aspirational=True))
    # And the session needs the registry/switch API itself (class-level scan: methods/properties,
    # not instance attrs).
    api_names = ("switch_tab", "expect_popup", "tabs", "tab_registry")
    checks.append(expect(any(hasattr(BrowserSession, n) for n in api_names),
                         "BrowserSession exposes a tab registry / switch API",
                         f"none of {api_names} on BrowserSession", aspirational=True))
    return checks
