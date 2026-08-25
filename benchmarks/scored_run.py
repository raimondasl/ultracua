"""Drive ONE corpus scenario end to end, with a real agent, and mint its verdict. (2.4 / B5, first piece.)

    uv run --no-sync python -m benchmarks.scored_run --scenario odoo-sort-list

**THIS SPENDS MONEY.** A learn is an LLM authoring session against a real ERP; `benchmark-plan` §6
calibrates an Odoo learn at $1.50-4. Nothing else in B4 cost a cent, and this is the boundary.

WHAT IT JOINS, because until now the pieces existed and nothing connected them end to end:

    substrate reset -> readiness -> proxy up -> oracle.premise()
      -> refresh_auth (deterministic, no LLM)  -> learn (THE SPEND)
      -> oracle.adjudicate()  -> corpus.bench_oracle()  -> outcomes.classify()  -> a verdict

Every one of those was built and tested in isolation across #200-#204. A first run is not a
formality: this repo's own record says an instrument that has never been driven is an instrument
with an unknown number of holes -- `Odoo.reset()` carried a "never run" note through three slices and
running it found a cold-template defect review could not.

THREE THINGS IT DELIBERATELY DOES NOT DO YET, stated so the gap is not mistaken for coverage:

  * **It does not go through `customer_bench.run_scenario`.** That harness calls
    `assert_not_a_skeleton(first, ...)` on whatever `agent_call` returns, and `learn()` returns a
    `LearnResult` with no `.elements` -- so wiring it up today would mean handing R4.40's guard a
    shim, which is the "arm the violation" trap this repo documents. The observation below is taken
    by the HARNESS opening the start page, which answers the same question ("did the page render")
    but is NOT the agent's own first snapshot. Closing that properly needs a hook `learn()` does not
    expose, and that is the next piece rather than something to paper over.
  * **It runs one scenario, not the corpus.** A batch, its acknowledgement lists and the gate are
    B5's; this is the smoke that has to pass before spending on fourteen.
  * **It does not persist a baseline.** `baselines/customer_v1.json` is 2.4's artifact and must not
    be written from a single un-repeated run.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ultracua import flows                                    # noqa: E402
from ultracua.flows import (                                  # noqa: E402
    FlowCache, FlowSpec, LoginSpec, approve, learn, refresh_auth, replay,
)
from ultracua.browser import BrowserSession                   # noqa: E402

from benchmarks import corpus                                  # noqa: E402
from benchmarks import outcomes                                # noqa: E402
from benchmarks import substrates as S                         # noqa: E402
from benchmarks.boundary_ledger import BoundaryLedger          # noqa: E402
from benchmarks.idempotency_proxy import IdempotencyProxy      # noqa: E402

SUBSTRATES = {"gitea": S.Gitea, "odoo": S.Odoo}

#: Where the login credentials are read from at RUN TIME. `LoginSpec` never stores them in the spec
#: or the cached flow -- only the resulting cookies -- which is why this is two env names and not two
#: strings. The VALUES are the substrates' demo defaults, already public in `substrates.py`.
USER_ENV, PASS_ENV = "ULTRACUA_BENCH_USER", "ULTRACUA_BENCH_PASS"

LOGIN = {
    "odoo": dict(path="/web/login", user=S.ODOO_LOGIN, password=S.ODOO_PASSWORD,
                 username_selector="input[name=login]", password_selector="input[name=password]",
                 submit_selector="button[type=submit]", success_selector=".o_web_client"),
    "gitea": dict(path="/user/login", user="bench", password=S.GITEA_PASSWORD,
                  username_selector="input[name=user_name]",
                  password_selector="input[name=password]",
                  submit_selector="button[type=submit]", success_selector=".dashboard, #navbar"),
}


def spec_for(entry, base_url: str, storage_state: str) -> FlowSpec:
    """A `FlowSpec` for one corpus entry, pointed at `base_url`.

    `base_url` is the PROXY, not the substrate: only the agent goes through it, so the evidence sees
    what the product put on the wire while every oracle keeps asking the substrate directly.

    `extract` is the scenario's own goal text for a read. That is a SMOKE SIMPLIFICATION and is
    called one: the goal says "…and report X", so it describes the datum well enough to drive an
    extraction, but a corpus that scores reads at scale should carry the datum description as its own
    field rather than reusing prose written for the agent.
    """
    cfg = LOGIN[entry.scenario.substrate]
    return FlowSpec(
        name=f"bench-{entry.scenario.name}",
        start_url=base_url + entry.scenario.url_path,
        goal=entry.scenario.goal,
        extract=None if entry.truth.mutating else entry.scenario.goal,
        storage_state=storage_state,
        headless=True,
        login=LoginSpec(url=base_url + cfg["path"], username_env=USER_ENV, password_env=PASS_ENV,
                        username_selector=cfg["username_selector"],
                        password_selector=cfg["password_selector"],
                        submit_selector=cfg["submit_selector"],
                        success_selector=cfg["success_selector"]),
    )


async def _first_observation(url: str, storage_state: str, headless: bool = True):
    """Open the scenario's start page once and snapshot it — R4.40's question, asked honestly.

    NOT the agent's own first observation, and the module docstring says so. It answers the same
    question the guard exists for ("did the page render, or is this a skeleton?") and it goes through
    the proxy, so the navigation appears in the evidence like any other traffic.

    **`storage_state` IS LOAD-BEARING, AND LEAVING IT OUT MADE THE GUARD PASS ON THE WRONG PAGE.**
    The first draft opened a bare session, so Odoo bounced it to `/web/login` and the snapshot was of
    the LOGIN FORM: 7 elements — comfortably over `SKELETON_ELEMENT_FLOOR`, so the guard went green
    while observing a page the scenario never visits. Measured, and it did not resolve with time
    (7 elements at 0 s, 2 s and 5 s), which is what ruled out the settle explanation and pointed at
    the session. A guard that passes for the wrong reason is worse than one that is absent, because
    nothing downstream has any reason to doubt it.
    """
    session = await BrowserSession(headless=headless, storage_state=storage_state).start()
    try:
        await session.goto(url)
        # BOUNDED SETTLE, because an SPA renders after navigation returns. Measured: Odoo snapshots
        # 5 elements immediately and 80 within two seconds. Polling until the page clears the floor
        # is not the same as waiting a fixed time and hoping — a page that NEVER renders still fails,
        # loudly, which is the whole point of the guard this feeds.
        obs = await session.snapshot()
        for _ in range(10):
            if len(getattr(obs, "elements", ()) or ()) >= S.SKELETON_ELEMENT_FLOOR:
                break
            await asyncio.sleep(0.5)
            obs = await session.snapshot()
        return obs
    finally:
        await session.close()


async def score_one(name: str, *, reset: bool = True, headless: bool = True,
                    cache_dir: "str | None" = None) -> dict:
    entry = next((e for s in ("gitea", "odoo") for e in corpus.for_substrate(s)
                  if e.scenario.name == name), None)
    if entry is None:
        raise SystemExit(f"no scenario named {name!r}")

    substrate = SUBSTRATES[entry.scenario.substrate]()
    if reset:
        substrate.reset()
    substrate.await_ready()

    os.environ[USER_ENV] = LOGIN[entry.scenario.substrate]["user"]
    os.environ[PASS_ENV] = LOGIN[entry.scenario.substrate]["password"]

    out: dict = {"scenario": name, "substrate": entry.scenario.substrate,
                 "mutating": entry.truth.mutating}
    with IdempotencyProxy(substrate.url) as proxy, tempfile.TemporaryDirectory() as tmp:
        out["proxy"] = proxy.base_url
        oracle = entry.oracle(substrate)
        if hasattr(oracle, "evidence"):
            # The runner wires it; the corpus deliberately does not assume a proxy exists.
            oracle.evidence = proxy.evidence
        before = oracle.premise()
        out["premise_rows"] = before.count

        spec = spec_for(entry, proxy.base_url, str(Path(tmp) / "auth.json"))
        # PERSISTENT WHEN ASKED, because `benchmark-plan` §4 is "learn once, replay N times" and a
        # throwaway cache makes every re-score pay for another learn. The flow key is derived from
        # goal+start_url+scope, and `start_url` carries the proxy's EPHEMERAL port — so a reused cache
        # only hits when the port matches, which it will not. `--cache` is therefore honest about what
        # it buys today (re-scoring within one process) and the fixed-port work that would make it buy
        # more is B5's, not something to fake here.
        cache = FlowCache(root=Path(cache_dir) if cache_dir else Path(tmp) / "cache")

        started = time.monotonic()
        with BoundaryLedger() as ledger:
            try:
                # Deterministic and BEFORE the spend: a login that fails must not cost a learn.
                await refresh_auth(spec, headless=headless)
                obs = await _first_observation(spec.start_url, spec.storage_state,
                                               headless=headless)
                S.assert_not_a_skeleton(obs, substrate=entry.scenario.substrate, scenario=name)
                out["first_observation_elements"] = len(getattr(obs, "elements", ()) or ())

                res = await learn(spec, cache=cache)
                out["learned"] = bool(res.cached)
                out["found"] = bool(res.found)
                out["data"] = res.data
                # "did a write fire ON THE WIRE during discovery" -- NOT "is a step marked mutating".
                # On Odoo this is expected to be True even for a pure read, because every list read is
                # a JSON-RPC POST. That is R4.27's finding and the reason this substrate is in the
                # corpus at all, so it is recorded rather than treated as a surprise.
                out["performed_write"] = bool(res.performed_write)
                if res.cached:
                    approve(spec, cache=cache)
                agent_ran, agent_error = True, ""
            except Exception as exc:              # noqa: BLE001 - a failing agent IS a result
                agent_ran, agent_error = True, f"{type(exc).__name__}: {exc}"
                out["agent_error"] = agent_error
        usage = ledger.usage()
        # --- THE REPLAY, WHICH IS FREE AND IS WHERE THE HEADLINE NUMBER LIVES -------------------
        #
        # The learn is the spend; the REPLAY is the product's actual claim — 0-LLM, deterministic,
        # gated. Over-gating cannot be seen on a learn at all: it shows up when a recipe cached as a
        # write demands approval, mints an Idempotency-Key and runs the mutation gate on a task that
        # was only ever a read. A runner that paid for the learn and stopped would have measured
        # everything except the thing this benchmark exists to measure.
        if out.get("learned"):
            proxy.reset()                  # the replay is a separate PHASE; B2's rule 3, for evidence
            # WHICH COMPONENT SAID NO (R4.92). `flow.py` sets `meta["gate"]` in exactly one place and
            # only inside `if step.mutating:`, so a trace carrying it means the MUTATION GATE refused
            # — as opposed to an ordinary step drifting, which is healed rather than refused. Both
            # raise `DriftError` with code `drift`, so the code cannot tell them apart and this is the
            # only structured signal that can. Read from a field, never from the message.
            traces: list = []
            with BoundaryLedger() as rl:
                try:
                    result = await replay(spec, cache=cache, on_step=traces.append)
                    out["replay"] = {"ok": True, "result": result}
                except Exception as exc:   # noqa: BLE001 - a refusal IS the measurement here
                    o = flows.outcome_of(exc)
                    out["replay"] = {"ok": False, "error": f"{type(exc).__name__}: {exc}"[:200],
                                     "code": o.code, "retryable": o.retryable, "landed": o.armed}
                    replay_code = o.code
                else:
                    replay_code = ""
            gate_refused = any((getattr(tr, "meta", None) or {}).get("gate") == "drift"
                               for tr in traces)
            out["replay"]["gate_refused"] = gate_refused
            ru = rl.usage()
            out["replay"]["llm_calls"] = ru.calls
            out["replay"]["zero_llm"] = ru.calls == 0
            out["replay"]["requests"] = proxy.evidence().summary()
        else:
            replay_code, gate_refused = "", False
        out["wall_s"] = round(time.monotonic() - started, 1)
        out["llm_calls"] = usage.calls
        out["tokens"] = {"in": usage.input_tokens, "out": usage.output_tokens}
        # CALLED, not referenced. `UsageTotals.cost_usd` is a method; reading it as an attribute
        # serialised a bound-method repr into the record, which is a number-shaped field carrying no
        # number — 1.3's lesson about a loud channel that cannot survive being printed, one field
        # over. It returns None when any spend cannot be priced, and that None is the honest answer.
        out["cost_usd"] = usage.cost_usd()
        out["accounting"] = "observed" if ledger.observed else "unknown"
        out["requests"] = proxy.evidence().summary()

        verdict = oracle.adjudicate(before, agent_ran=agent_ran)
        out["oracle"] = {"satisfied": verdict.satisfied, "reason": verdict.reason}

        # AFTER the replay, because two of its five facts are about the RUN rather than the recipe.
        # `substrate_pinned` is the harness affirming that the world the replay saw is the world the
        # flow was learned against — true here because `score_one` resets to the template and this
        # arm never varies the substrate version. Arm 2 (drift) must NOT set it, and the default is
        # the safe one: forgetting costs a `refused`, never an inflated `over_gated`.
        out["gate"] = _gate_evidence(spec, cache, gate_refused=gate_refused, pinned=reset)

        expected = "" if entry.truth.mutating else str(entry.expected_answer(substrate))
        out["expected"] = expected or None
        # THE VERDICT DESCRIBES ONE PHASE, AND IT IS THE REPLAY.
        #
        # The first draft handed `classify` the LEARN's data alongside the REPLAY's refusal code, and
        # got `ok` back for a run whose replay was refused by the mutation gate — because a read with
        # `data_correct=True` is `ok` whatever the code says. Reproduced directly against B3 rather
        # than guessed at, and the incoherence was mine: one `classify` call describes ONE run, and I
        # had given it half of each.
        #
        # The replay is the right half. `benchmark-plan` §4 measures availability by learning once and
        # REPLAYING N times; the learn is where the money goes and the replay is the product's actual
        # 0-LLM claim. A record that scored the learn would report the LLM succeeding at a task the
        # deterministic replay cannot do.
        answer = out.get("replay", {}).get("result") if out.get("replay", {}).get("ok") else None
        out["scored_phase"] = "replay"
        bench = corpus.bench_oracle(entry, verdict, expected=expected,
                                    answer="" if answer is None else str(answer))
        scored = outcomes.classify(
            entry.truth,
            _Record(agent_ran, agent_error, out.get("replay", {}).get("ok"), replay_code),
            bench,
            gate=outcomes.GateEvidence(**out["gate"]) if out.get("gate") else None)
        out["outcome"] = scored.outcome
        out["outcome_reason"] = scored.reason
    return out


def _gate_evidence(spec, cache, *, gate_refused: bool = False, pinned: bool = False) -> dict:
    """What the product DID to this flow, read off the cached recipe rather than inferred.

    `over_gated` is the benchmark's headline and B3 refuses to mint it from a refusal code alone —
    four of the eight write-gate codes are approval-lifecycle gates that a caller can trigger for a
    plain read, so the code says nothing on its own. The discriminator is whether anything MARKED
    this flow as a write, which is a fact about the recipe.
    """
    cached = cache.get(spec.key)
    if cached is None:
        return {}
    steps = list(getattr(cached, "steps", ()) or ())
    marked = [s for s in steps if getattr(s, "mutating", False)]
    sources = tuple(sorted({m for s in marked for m in (getattr(s, "mutating_sources", None) or ())}))
    meta = getattr(cached, "meta", None)
    return {"present": True, "mutating_steps": len(marked), "mutating_sources": sources,
            "approved": getattr(meta, "approved", None),
            "declares_write": spec.write.declares_write,
            "mutation_gate_refused": gate_refused, "substrate_pinned": pinned}


class _Record:
    """The minimum `outcomes.classify` reads, duck-typed exactly as it documents."""

    def __init__(self, agent_ran: bool, agent_error: str, claimed, code: str = "") -> None:
        self.agent_ran = agent_ran
        self.agent_error = agent_error
        self.harness_error = ""
        # The REPLAY's refusal code, not the learn's: the replay is the product's real claim, and a
        # read refused there by the write machinery is exactly `over_gated`.
        self.agent_error_code = code
        self.claimed_complete = claimed


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--scenario", required=True)
    ap.add_argument("--no-reset", action="store_true")
    ap.add_argument("--headed", action="store_true")
    ap.add_argument("--cache", default=None, help="persist the learned flow here (see score_one)")
    args = ap.parse_args(argv)
    result = asyncio.run(score_one(args.scenario, reset=not args.no_reset,
                                   headless=not args.headed, cache_dir=args.cache))
    print(json.dumps(result, indent=1, default=str))
    return 0


if __name__ == "__main__":  # pragma: no cover - operator surface
    raise SystemExit(main())
