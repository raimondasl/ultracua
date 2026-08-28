"""Would demoting the wire marks make a replay green? Flip them and find out. (Measurement #1.)

    uv run --no-sync python -m benchmarks.mark_flip_probe --recipe D:/some/cache/abc.json \\
        --scenario odoo-sort-list

WHAT IT ANSWERS. Every approach in `docs/reads-over-post.md` assumes that fixing R4.27's marking
improves Odoo availability, and nothing measured it. This runs the A/B that does: the SAME cached
recipe replayed twice against the SAME substrate, where the only difference is whether the
wire-marked steps carry `mutating`. Arm A is the recipe as learned; arm B is a copy with every
`wire`-sourced mark flipped off, which is what a body-evidence classifier would have produced.

THE ANSWER IT GAVE (0.141.0, `odoo-sort-list`, twice -- once on a day-old recipe and once on one
learned minutes earlier, so staleness is excluded):

    control   step 1 click  mutating=True   gate=drift        gate_bound_by=none
                            -> "mutation gate: target missing/ambiguous"
    demoted   step 1 click  (no mark)       (no gate)         bound_by=none
                            -> "locator unresolved or ambiguous (drift)"

`bound_by: none` in BOTH arms. The locator never resolved either way; the mutation gate was
reporting a locator failure it happened to reach first. **Demotion changes the message, not the
outcome.**

THREE THINGS THIS HARNESS HAS TO GET RIGHT, each of which it got wrong first and would have reported
as a result:

  * **RE-KEY the recipe.** Cached bench flows were learned through the idempotency proxy and the flow
    key is `flow_key(goal, start_url, scope)` -- their `start_url` carries an ephemeral port that no
    longer exists, so a naive replay MISSES the cache and fails for a reason unrelated to the mark.
    The key is asserted to hit before an arm is believed.
  * **APPROVE THROUGH THE PRODUCT'S VERB.** A hand-written `{"approved": true}` sidecar has no
    `steps_hash`, and both arms then died on `StaleApprovalError` -- agreeing for a reason that has
    nothing to do with the mark. That guard is also load-bearing here: `mutating` is one of
    `_HASHED_STEP_FIELDS`, so flipping it legitimately invalidates an approval and each arm must be
    approved against its own steps.
  * **READ THE STEP RECORD, not the exception.** "replay failed (page drift?): Error" names no step
    and does not say whether the gate spoke. `StepTrace.meta["gate"]` is set in exactly one place in
    `flow.py`, inside `if step.mutating:`, so its presence is a structured fact and its ABSENCE in
    the demoted arm is the measurement.

BOTH ARMS ARE 0-LLM AND FREE. `flows.replay` takes no provider by default, so nothing here spends --
the learn that produces the recipe does, and that is the caller's to buy.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import shutil
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from benchmarks import corpus                                       # noqa: E402
from benchmarks import substrates as S                              # noqa: E402
from benchmarks.scored_run import LOGIN, PASS_ENV, USER_ENV, spec_for   # noqa: E402
from ultracua.flows import FlowCache, RunRecord, approve, refresh_auth, replay  # noqa: E402

SUBSTRATES = {"gitea": S.Gitea, "odoo": S.Odoo}


def demote(recipe: dict, *, sources=("wire",)) -> "tuple[dict, list]":
    """A copy with every step marked ONLY by `sources` demoted. Returns (recipe, flipped indices).

    `mutating_sources` is cleared rather than edited, which is what a body classifier that never
    promoted the step would have left behind. NOTE that a real fix should do the opposite -- ADD a
    provenance mark rather than strip `MARK_WIRE`, or R4.27 becomes invisible (safety.py says so).
    This is a probe reproducing a counterfactual, not a model of the fix.
    """
    out = json.loads(json.dumps(recipe))
    flipped = []
    for i, st in enumerate(out.get("steps", [])):
        if st.get("mutating") and set(st.get("mutating_sources") or ()) <= set(sources):
            st["mutating"], st["mutating_sources"] = False, None
            flipped.append(i)
    return out, flipped


def install(recipe: dict, spec, root: Path) -> str:
    """Write the recipe into a FRESH cache under this spec's key, approved. Returns the key."""
    if root.exists():
        shutil.rmtree(root)
    root.mkdir(parents=True)
    r = dict(recipe, start_url=spec.start_url)
    key = spec.key
    (root / f"{key}.json").write_text(json.dumps(r), encoding="utf-8")
    cache = FlowCache(root=root)
    assert cache.get(key) is not None, (
        f"cache MISS on {key} — the arm would replay nothing and fail for a reason unrelated to the "
        f"mark, which is how both arms come to agree for the wrong reason")
    approve(spec, cache=cache)
    return key


def summarise(rec: RunRecord) -> list:
    return [{"index": getattr(t, "index", i), "meta": dict(getattr(t, "meta", {}) or {})}
            for i, t in enumerate(getattr(rec, "traces", []) or [])]


async def arm(entry, recipe: dict, *, storage: str, root: Path, reset: bool) -> dict:
    sub = SUBSTRATES[entry.scenario.substrate]()
    if reset:
        sub.reset()
        sub.await_ready()
    spec = spec_for(entry, sub.url, storage)      # direct at the substrate: no proxy, no port churn
    key = install(recipe, spec, root)
    rec, t0 = RunRecord(), time.monotonic()
    try:
        await replay(spec, cache=FlowCache(root=root), on_drift="raise", record=rec)
        out = {"ok": True}
    except BaseException as exc:                  # noqa: BLE001 - the refusal IS the datum
        out = {"ok": False, "error_class": type(exc).__name__, "error": str(exc)[:300]}
    return {**out, "key": key, "traces": summarise(rec),
            "llm_calls": getattr(rec, "llm_calls", None), "wall_s": round(time.monotonic() - t0, 1)}


async def run(scenario: str, recipe_path: Path, workdir: Path) -> dict:
    entry = next((e for s in corpus.CORPORA for e in corpus.for_substrate(s)
                  if e.scenario.name == scenario), None)
    if entry is None:
        raise SystemExit(f"no scenario named {scenario!r}")
    recipe = json.loads(recipe_path.read_text(encoding="utf-8"))
    marked = [(i, s.get("action"), tuple(s.get("mutating_sources") or ()))
              for i, s in enumerate(recipe.get("steps", [])) if s.get("mutating")]
    if not marked:
        raise SystemExit(
            f"{recipe_path.name} has NO mutating steps, so the flip is a no-op and both arms would "
            f"be identical — a probe that cannot distinguish its two arms measures nothing.")

    workdir.mkdir(parents=True, exist_ok=True)
    sub = SUBSTRATES[entry.scenario.substrate]()
    sub.reset()
    sub.await_ready()
    storage = str(workdir / f"auth-{scenario}.json")
    cfg = LOGIN[entry.scenario.substrate]
    os.environ[USER_ENV], os.environ[PASS_ENV] = cfg["user"], cfg["password"]
    await refresh_auth(spec_for(entry, sub.url, storage), headless=True)

    flipped_recipe, flipped = demote(recipe)
    control = await arm(entry, recipe, storage=storage, root=workdir / "control", reset=True)
    treated = await arm(entry, flipped_recipe, storage=storage, root=workdir / "demoted", reset=True)
    return {"scenario": scenario, "recipe": str(recipe_path), "marked_steps": marked,
            "flipped_steps": flipped, "control": control, "demoted": treated}


def _print(res: dict) -> None:
    print("=" * 78)
    print(f"  {res['scenario']}   marked {res['marked_steps']}   flipped {res['flipped_steps']}")
    for label in ("control", "demoted"):
        a = res[label]
        print(f"\n  --- {label.upper()}  ok={a['ok']}  llm_calls={a['llm_calls']}")
        for t in a["traces"]:
            m = t["meta"]
            gate = f"  GATE={m['gate']}" if m.get("gate") else ""
            bound = m.get("gate_bound_by", m.get("bound_by"))
            print(f"      step {t['index']} {str(m.get('action')):9} ok={m.get('ok')}"
                  f"{gate}  bound_by={bound}")
            if m.get("note"):
                print(f"          {m['note'][:88]}")
        if not a["ok"]:
            print(f"      -> {a['error'][:150]}")
    print("\n" + "=" * 78)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--scenario", required=True)
    ap.add_argument("--recipe", required=True, type=Path, help="a cached flow JSON to A/B")
    ap.add_argument("--workdir", type=Path, default=Path("D:/ultracua-data/mark-flip"))
    ap.add_argument("--out", default=None)
    args = ap.parse_args(argv)

    res = asyncio.run(run(args.scenario, args.recipe, args.workdir))
    _print(res)
    if args.out:
        Path(args.out).write_text(json.dumps(res, indent=1), encoding="utf-8")
    # The VERDICT, in one line, because a reader of a wall of traces needs the answer stated.
    same = res["control"]["ok"] == res["demoted"]["ok"]
    print(f"  demotion changed the OUTCOME: {not same}"
          f"   (control ok={res['control']['ok']}, demoted ok={res['demoted']['ok']})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
