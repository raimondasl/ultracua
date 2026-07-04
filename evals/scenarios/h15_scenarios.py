"""H15 evals: air-gapped zero-key mode — local LLM runtime + manifest pinning (key-less, aspirational).

ROADMAP H15: `ultracua --local` runs every learn/record-time LLM touchpoint (authoring,
structured extraction, recorder captions, vision grounding, heal/replan) against a local
llama.cpp/Ollama backend as a first-class native `LLMClient` — `force_tool` implemented as
JSON-schema-constrained decoding. A model manifest (model + quant + weights sha256 + engine +
version + backend) is pinned into the flow as ADDITIVE fields; replay of an LLM-extraction read
fails LOUD on manifest mismatch; pinned scalar reads stay 0-LLM and unaffected. Staged:
(1) local backend + extraction + captions; (2) `vision.LocalGrounding` behind a per-quant CI
gate; (3) EXPERIMENTAL reads-only local authoring (coordinate proposals back-resolved via
elementFromPoint -> _SPECOF_JS into ordinary locator-based CachedSteps, block_mutations=True
hard-defaulted — write flows stay recorder-authored).

Partial credit measured today (the shipped substrate each rung is specified to ride on):
- build_client("mock") is the key-less scripted-client seam the local test path follows, and it
  honors force_tool (the constrained-decoding contract's canonical half)
- ToolDef.strict + LLMRequest.force_tool exist — what Ollama's `format` parameter maps FROM
- the key-less gate works: no key -> _llm_configured is False and caption_for returns None
  (skip, never a retry storm — the gate 'local' must extend with a reachability probe)
- CachedFlow tolerates an additive unknown field and FlowSpec loads via _only_known — the
  no-schema-bump precedent the manifest fields depend on; FlowReplayError (the loud channel a
  manifest mismatch raises through) is shipped
- GroundingProvider protocol + MockGrounding + the _GROUND_TOOL schema (the constrained-decode
  target) exist; ACTION_TOOL is strict/additionalProperties:false; _author_steps already takes
  block_mutations; _SPECOF_JS (the coordinate back-resolution target) is shipped
- the OpenAI adapter's to_native/from_native round-trip the exact wire shape a local /v1
  endpoint speaks (dict-in/dict-out, forced tool, strict) — the day-0 spike's request/response

Everything here is key-less and browserless: structural probes + pure-function wire checks, $0.
"""

from __future__ import annotations

import dataclasses

from evals.core import MISSING_EXC, Ctx, expect, import_probe, missing, ok, probe, scenario

# The future manifest field wherever it could plausibly be named (additive Optional field on
# CachedFlow / FlowSpec per the plan — never FlowMeta, whose unknown-key path resets history).
_MANIFEST_NAMES = ("model_manifest", "manifest", "llm_manifest", "extractor_manifest")


@scenario(
    id="h15.backend.local_llm_client",
    title="'local' as a first-class native LLM backend (llm/local.py, build_client, _LLM_BACKENDS)",
    group="h15", aspirational=True, tags=("local", "backend", "llm"),
    notes="H15 stage 1: LocalClient over Ollama/llama-server; force_tool = schema-constrained decoding",
)
async def local_llm_client(ctx: Ctx):
    from ultracua import providers
    from ultracua.llm import build_client
    from ultracua.llm.types import LLMRequest, Message, TextBlock, ToolDef

    checks = []
    # H15 stage 1 — the native adapter module itself (NOT an OpenAI-compat shim, per the llm/
    # docstring stance): llm/local.py:LocalClient speaking Ollama /api/chat with force_tool
    # mapped to schema-constrained decoding.
    found_local, exc = import_probe("ultracua.llm.local")
    checks.append(expect(found_local, "ultracua.llm.local module exists (native LocalClient)",
                         f"{type(exc).__name__}: {exc}", aspirational=True))
    # The factory must know the backend. build_client raises ValueError("unknown LLM backend")
    # for an unregistered name — ValueError is NOT in MISSING_EXC (it's the registry's loud
    # not-registered signal, not a broken API), so translate it to `missing` here.
    try:
        client = build_client("local")
    except ValueError as e:
        checks.append(missing("build_client('local') returns a native local client", str(e)))
    except MISSING_EXC as e:
        checks.append(missing("build_client('local') returns a native local client",
                              f"{type(e).__name__}: {e}"))
    else:
        checks.append(expect(hasattr(client, "complete"),
                             "build_client('local') returns a native local client",
                             f"no .complete on {type(client).__name__}"))
    # ...and the provider layer must route it (providers/__init__.py registration + CLI choices
    # hang off this tuple) so `--provider local` can drive authoring/captions/extraction.
    checks.append(expect("local" in providers._LLM_BACKENDS,
                         "'local' is a registered provider backend",
                         f"_LLM_BACKENDS={providers._LLM_BACKENDS}", aspirational=True))
    # PARTIAL CREDIT (shipped substrate): the key-less scripted-client seam the local test path
    # is specified to follow (plan: "a fake local server following the llm/mock.py MockClient
    # pattern") — and it honors force_tool, the canonical half of the constrained-decode contract.
    mock = build_client("mock")
    req = LLMRequest(model="mock", tools=[ToolDef("submit", "d", {"type": "object"})],
                     force_tool="submit", messages=[Message("user", [TextBlock("hi")])])
    resp = await mock.complete(req)
    tu = resp.tool_use("submit")
    checks.append(expect(tu is not None,
                         "build_client('mock') honors force_tool (the key-less local-test seam)",
                         f"blocks={resp.blocks!r}"))
    # PARTIAL CREDIT (shipped substrate): the canonical types already carry what Ollama's
    # `format` parameter maps FROM — ToolDef.strict + LLMRequest.force_tool. LocalClient is a
    # translation of existing fields, not a new request shape.
    tooldef_fields = {f.name for f in dataclasses.fields(ToolDef)}
    req_fields = {f.name for f in dataclasses.fields(LLMRequest)}
    checks.append(expect("strict" in tooldef_fields and "force_tool" in req_fields,
                         "ToolDef.strict + LLMRequest.force_tool exist (constrained-decode inputs)",
                         f"ToolDef={sorted(tooldef_fields)} LLMRequest={sorted(req_fields)}"))
    return checks


@scenario(
    id="h15.config.local_keygate",
    title="'local' counts as configured via explicit env + reachability (today it silently keys off ANTHROPIC_API_KEY)",
    group="h15", aspirational=True, tags=("local", "config", "keygate"),
    notes="H15 stage 1: _KEY_ENV['local'] + local_base_url; a down endpoint must skip fast, never retry-storm",
)
async def local_keygate(ctx: Ctx):
    import os

    from ultracua import flows
    from ultracua.config import Settings

    checks = []
    # H15 — _llm_configured must learn a 'local' backend (explicit env like ULTRACUA_LOCAL_URL +
    # a fast reachability probe). Today _KEY_ENV.get('local', ("ANTHROPIC_API_KEY",)) FALLS BACK
    # to the Anthropic key: an air-gapped local backend would be gated by a cloud key it neither
    # has nor wants — exactly the wrong env.
    checks.append(expect("local" in flows._KEY_ENV,
                         "_KEY_ENV knows a 'local' backend (its own configured-signal, not a cloud key)",
                         f"_KEY_ENV keys={sorted(flows._KEY_ENV)} — 'local' currently falls back "
                         "to ANTHROPIC_API_KEY", aspirational=True))
    # The endpoint knob itself (plan: config.py local_base_url) so the CLI/daemon can point at
    # Ollama/llama-server without abusing another provider's env.
    settings_fields = {f.name for f in dataclasses.fields(Settings)}
    checks.append(expect(any(n in settings_fields for n in
                             ("local_base_url", "local_url", "ollama_base_url")),
                         "Settings carries a local endpoint knob (local_base_url)",
                         f"fields={sorted(settings_fields)}", aspirational=True))
    # CPU-laptop inference blows the cloud-tuned timeout — the plan requires a PER-BACKEND
    # llm_timeout_s override so 'local' doesn't inherit 60s cloud retry semantics.
    checks.append(expect(any(n in settings_fields for n in
                             ("local_llm_timeout_s", "llm_timeouts", "llm_timeout_overrides")),
                         "Settings carries a per-backend llm_timeout override for 'local'",
                         f"only the global llm_timeout_s exists "
                         f"({'llm_timeout_s' in settings_fields})", aspirational=True))
    # PARTIAL CREDIT (shipped keygate, measured with keys STRIPPED): no key -> not configured,
    # and caption_for returns None — recording stays key-less with a silent SKIP, never an LLM
    # attempt (the router retries with backoff, so an attempt would be a retry storm). This is
    # the exact gate 'local' must extend: a down local endpoint has to land on this same path.
    key_envs = ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GEMINI_API_KEY", "GOOGLE_API_KEY")
    saved = {k: os.environ.pop(k, None) for k in key_envs}
    try:
        gated = not any(flows._llm_configured(b) for b in ("anthropic", "openai", "gemini"))
        checks.append(expect(gated, "no key -> _llm_configured is False for every cloud backend",
                             "a backend claims to be configured with all key envs stripped"))
        checks.append(expect(flows.caption_for("anthropic") is None,
                             "no key -> caption_for returns None (skip, never an LLM attempt)",
                             "caption_for built a captioner without a key"))
    finally:
        for k, v in saved.items():
            if v is not None:
                os.environ[k] = v
    return checks


@scenario(
    id="h15.manifest.model_pinning",
    title="model manifest pinned into the flow; mismatch fails loud (additive-field substrate works today)",
    group="h15", aspirational=True, tags=("local", "manifest", "fail-loud"),
    notes="H15 stage 1: {model, quant, weights_sha256, engine, engine_version, backend} on CachedFlow/FlowSpec",
)
async def model_pinning(ctx: Ctx):
    import json
    import time

    from ultracua import flows
    from ultracua.cache import CachedFlow, flow_key
    from ultracua.flows import FlowSpec, _only_known

    checks = []
    # H15 — the manifest travels WITH the learned flow (additive Optional fields on CachedFlow,
    # no SCHEMA_VERSION bump per the StepConfirm precedent) so replay can compare the model that
    # authored an LLM-extraction read against the model available now.
    flow_fields = set(CachedFlow.model_fields)
    checks.append(expect(any(n in flow_fields for n in _MANIFEST_NAMES),
                         "CachedFlow carries a model-manifest field",
                         f"fields={sorted(flow_fields)}", aspirational=True))
    # ...and on the named recurring task too (FlowSpec — never FlowMeta, whose unknown-key
    # TypeError-reset would wipe approval + run history).
    spec_fields = {f.name for f in dataclasses.fields(FlowSpec)}
    checks.append(expect(any(n in spec_fields for n in _MANIFEST_NAMES),
                         "FlowSpec carries a model-manifest field",
                         f"fields={sorted(spec_fields)}", aspirational=True))
    # PARTIAL CREDIT (shipped substrate): a cached flow whose JSON carries an UNKNOWN additive
    # field still loads — the exact forward-compat property that lets the manifest ship without
    # a schema bump (an older reader must not turn a manifest-carrying flow into a miss).
    cache = ctx.cache()
    key = flow_key("h15 manifest probe", "http://127.0.0.1/manifest")
    cache.put(CachedFlow(key=key, goal="h15 manifest probe",
                         start_url="http://127.0.0.1/manifest", steps=[], created_ts=time.time()))
    p = cache.root / f"{key}.json"
    raw = json.loads(p.read_text(encoding="utf-8"))
    raw["model_manifest"] = {"model": "qwen3-vl-8b", "quant": "Q4_K_M", "weights_sha256": "ab" * 32,
                             "engine": "llama.cpp", "engine_version": "b1234", "backend": "local"}
    p.write_text(json.dumps(raw), encoding="utf-8")
    checks.append(expect(cache.get(key) is not None,
                         "CachedFlow with an additive unknown field still loads (no-schema-bump precedent)",
                         "an extra JSON key turned the flow into a cache miss"))
    # PARTIAL CREDIT (shipped substrate): FlowSpec's _only_known load path drops keys a version
    # doesn't know — a manifest-carrying spec written by a NEWER version loads on an older one.
    data = {"name": "n", "start_url": "http://127.0.0.1/", "goal": "g",
            "model_manifest": {"model": "m"}}
    status, spec = await probe(lambda: FlowSpec(**_only_known(data, FlowSpec)))
    checks.append(expect(status == "ok" and spec.name == "n",
                         "FlowSpec loads via _only_known past an unknown manifest key",
                         f"status={status} value={spec!r}"))
    # PARTIAL CREDIT (shipped fail-loud channel): the manifest-mismatch refusal is specified to
    # raise FlowReplayError ("relearn or re-pull model") — the error type already exists, so the
    # new check plugs into an established loud path instead of inventing a new failure mode.
    checks.append(expect(hasattr(flows, "FlowReplayError")
                         and issubclass(flows.FlowReplayError, RuntimeError),
                         "FlowReplayError exists (the loud channel a manifest mismatch raises through)",
                         "no FlowReplayError in ultracua.flows"))
    return checks


@scenario(
    id="h15.grounding.local_vision",
    title="vision.LocalGrounding: local screenshot grounding behind the shipped GroundingProvider protocol",
    group="h15", aspirational=True, tags=("local", "vision", "grounding"),
    notes="H15 stage 2: constrained-decode _GROUND_TOOL on a local VLM; per-(model,quant,engine) CI gate",
)
async def local_vision(ctx: Ctx):
    import inspect

    from ultracua import vision

    checks = []
    # H15 stage 2 — the local grounding provider itself (screenshot + _GROUND_TOOL schema via
    # constrained decode, explicit coordinate-space normalization for Qwen-VL-family resized/
    # normalized coords -> viewport px). Today only Mock + Anthropic grounding exist.
    checks.append(expect(hasattr(vision, "LocalGrounding"),
                         "vision.LocalGrounding exists",
                         f"vision exposes {[n for n in dir(vision) if 'Grounding' in n]}",
                         aspirational=True))
    # PARTIAL CREDIT (shipped substrate): the GroundingProvider protocol — the seam LocalGrounding
    # slots into WITHOUT touching the vision tier's callers — already has the exact decide shape.
    sig = inspect.signature(vision.GroundingProvider.decide)
    checks.append(expect(list(sig.parameters)[1:] == ["goal", "screenshot", "viewport"],
                         "GroundingProvider protocol has the decide(goal, screenshot, viewport) seam",
                         f"params={list(sig.parameters)}"))
    # PARTIAL CREDIT (shipped substrate): MockGrounding satisfies the protocol scripted — the
    # key-less test pattern a LocalGrounding CI gate reuses (golden screenshots, no live model).
    mg = vision.MockGrounding([{"action": "click_xy", "intent": "tap the save glyph",
                                "coords": [10, 20]}])
    action, _ttft = await mg.decide("save", b"", {"width": 100, "height": 100})
    checks.append(expect(action.action == "click_xy" and action.coords == [10, 20],
                         "MockGrounding returns a scripted click_xy (the key-less CI-gate pattern)",
                         f"action={action!r}"))
    # PARTIAL CREDIT (shipped substrate): _GROUND_TOOL is already a closed JSON schema with
    # integer x/y — precisely the shape Ollama's `format` constrained decoding takes, so the
    # local VLM call is a schema handoff, not a new protocol.
    schema = vision._GROUND_TOOL["input_schema"]
    props = schema["properties"]
    checks.append(expect(schema.get("additionalProperties") is False
                         and props["x"]["type"] == "integer" and props["y"]["type"] == "integer",
                         "_GROUND_TOOL is a closed schema with integer coords (constrained-decode target)",
                         f"schema={schema}"))
    return checks


@scenario(
    id="h15.authoring.local_provider",
    title="experimental reads-only local authoring (providers/local_agent.py) — the gated frontier rung",
    group="h15", aspirational=True, tags=("local", "authoring", "write-safety"),
    notes="H15 stage 3: coordinate proposals back-resolved via _SPECOF_JS; block_mutations hard-defaulted",
)
async def local_provider(ctx: Ctx):
    import inspect

    from ultracua import locators
    from ultracua.flow import _author_steps
    from ultracua.providers import ACTION_TOOL, get_provider

    checks = []
    # H15 stage 3 — the screenshot-native local author (UI-TARS/Fara class) whose coordinate
    # proposals compile to ordinary locator-based CachedSteps. Shipped dark, measured on the
    # MiniWoB/drift-sandbox harnesses before any claim — but the module must exist to measure.
    found, exc = import_probe("ultracua.providers.local_agent")
    checks.append(expect(found, "ultracua.providers.local_agent module exists",
                         f"{type(exc).__name__}: {exc}", aspirational=True))
    # ...and the provider registry must route it. get_provider raises ValueError("unknown
    # provider") for an unregistered name — the registry's loud signal, not in MISSING_EXC,
    # so translate it to `missing` here.
    try:
        prov = get_provider("local")
    except ValueError as e:
        checks.append(missing("get_provider('local') returns the local authoring provider", str(e)))
    except MISSING_EXC as e:
        checks.append(missing("get_provider('local') returns the local authoring provider",
                              f"{type(e).__name__}: {e}"))
    else:
        checks.append(expect(hasattr(prov, "decide"),
                             "get_provider('local') returns the local authoring provider",
                             f"no .decide on {type(prov).__name__}"))
    # PARTIAL CREDIT (shipped substrate): ACTION_TOOL is already strict + additionalProperties:
    # false — schema-guaranteed-valid arguments are what make a 4-8B author's tool calls usable
    # without a retry loop (the same constrained-decode contract as extraction).
    checks.append(expect(ACTION_TOOL.get("strict") is True
                         and ACTION_TOOL["input_schema"].get("additionalProperties") is False,
                         "ACTION_TOOL is strict/closed (the local author's constrained-decode target)",
                         f"strict={ACTION_TOOL.get('strict')}"))
    # PARTIAL CREDIT (shipped write-safety): the reads-only guard the local author must HARD-
    # default already exists — _author_steps(block_mutations=True) refuses to EXECUTE a mutating
    # action. A weaker local author exploring a live site clicks real buttons; without this gate
    # a discovery misstep is a live write, not a discarded candidate.
    params = inspect.signature(_author_steps).parameters
    checks.append(expect("block_mutations" in params,
                         "_author_steps takes block_mutations (the local author's reads-only gate)",
                         f"params={list(params)}"))
    # PARTIAL CREDIT (shipped substrate): _SPECOF_JS — the in-page describe used to back-resolve
    # a coordinate proposal (elementFromPoint -> LocatorSpec) so authored steps replay 0-LLM,
    # locator-based, and mutation-gated. Raw click_xy authoring would skip every one of those.
    checks.append(expect(isinstance(getattr(locators, "_SPECOF_JS", None), str),
                         "_SPECOF_JS exists (the coordinate back-resolution target)",
                         "no _SPECOF_JS in ultracua.locators"))
    return checks


@scenario(
    id="h15.day0.openai_base_url",
    title="day-0 local path: the OpenAI adapter pointed at a local /v1 (Ollama) — wire shape works, plumbing probed",
    group="h15", aspirational=True, tags=("local", "day0", "openai"),
    notes="H15 plan step 1 spike: OPENAI_BASE_URL -> Ollama /v1 validates the loop before the native adapter",
)
async def openai_base_url(ctx: Ctx):
    import os

    from ultracua.llm import openai as oai
    from ultracua.llm.types import LLMRequest, Message, TextBlock, ToolDef

    checks = []
    local_url = "http://127.0.0.1:11434/v1"  # Ollama's OpenAI-compat endpoint (never contacted)
    # H15 day-0 — first-class plumbing: the adapter should take an explicit base URL (config-
    # driven, not env-only) so `--local` can point it at Ollama without mutating the process env.
    status, val = await probe(oai.OpenAIClient, base_url=local_url)
    checks.append(expect(status == "ok",
                         "OpenAIClient accepts an explicit base_url",
                         f"{type(val).__name__}: {val}", aspirational=True))
    # The env path that makes the spike possible TODAY (SDK-level: AsyncOpenAI reads
    # OPENAI_BASE_URL at construction — no network happens until a request is sent). Missing
    # when the openai extra isn't installed: the day-0 path is genuinely unavailable then.
    saved = {k: os.environ.get(k) for k in ("OPENAI_API_KEY", "OPENAI_BASE_URL")}
    os.environ["OPENAI_API_KEY"] = "sk-eval-dummy"  # constructor wants a key; never used
    os.environ["OPENAI_BASE_URL"] = local_url
    try:
        status, sdk = await probe(lambda: oai.OpenAIClient()._sdk())
        if status == "ok":
            base = str(getattr(sdk, "base_url", "")).rstrip("/")
            checks.append(expect(base.endswith("127.0.0.1:11434/v1"),
                                 "the adapter's SDK honors OPENAI_BASE_URL (day-0 Ollama /v1 path)",
                                 f"base_url={base!r}"))
        else:  # the day-0 spike is aspirational — an uninstalled/odd SDK is a gap, not a bug
            checks.append(missing("the adapter's SDK honors OPENAI_BASE_URL (day-0 Ollama /v1 path)",
                                  f"{type(sdk).__name__}: {sdk}"))
    finally:
        for k, v in saved.items():
            os.environ.pop(k, None)
            if v is not None:
                os.environ[k] = v
    # PARTIAL CREDIT (shipped wire shape, pure function): to_native emits the forced-tool +
    # strict request a local /v1 server receives — the constrained-decoding half of the loop
    # already leaves the adapter in the right shape.
    tool = ToolDef(name="submit", description="d",
                   input_schema={"type": "object", "properties": {}, "additionalProperties": False},
                   strict=True)
    body = oai.to_native(LLMRequest(model="qwen3:8b", tools=[tool], force_tool="submit",
                                    messages=[Message("user", [TextBlock("extract")])]))
    checks.append(expect(body.get("tool_choice", {}).get("function", {}).get("name") == "submit"
                         and body["tools"][0]["function"].get("strict") is True,
                         "to_native emits forced-tool + strict (what a local /v1 endpoint receives)",
                         f"tool_choice={body.get('tool_choice')} tools={body.get('tools')}"))
    # PARTIAL CREDIT (shipped wire shape, pure function): from_native parses a PLAIN DICT
    # response — exactly what a local server's JSON gives back (no SDK typed objects) — including
    # the tool_call arguments-as-JSON-string quirk. The extraction loop's return half works.
    raw = {"model": "qwen3:8b",
           "choices": [{"finish_reason": "tool_calls",
                        "message": {"content": None,
                                    "tool_calls": [{"id": "c1", "type": "function",
                                                    "function": {"name": "submit",
                                                                 "arguments": '{"found": true, "data": 42}'}}]}}],
           "usage": {"prompt_tokens": 10, "completion_tokens": 5}}
    resp = oai.from_native(raw)
    tu = resp.tool_use("submit")
    checks.append(expect(tu is not None and tu.input == {"found": True, "data": 42},
                         "from_native parses a dict-shaped local-server response",
                         f"blocks={resp.blocks!r}"))
    return checks
