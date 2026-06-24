"""Recorder ceiling validation — turn the Phase-I recorder's "moves the ~40% ceiling" claim from ASSERTED
into MEASURED, on the real MiniWoB++ tasks the LLM-authoring discovery loop can't crack.

    uv run --group bench python -m benchmarks.recorder_ceiling
    uv run --group bench python -m benchmarks.recorder_ceiling --provider anthropic   # contrast arm (paid)

The ceiling (per STATUS): garbled-label SELECTION tasks (`click-checkboxes`, `click-option`, …) where the
LLM mis-GROUNDS — it picks the wrong element no matter how many times it re-rolls, so more sampling doesn't
help. A demonstration removes grounding: a human reads the page and clicks the right boxes. This harness
makes that measurable, key-less:

  for each (task, seed):
    serve + SEED a deterministic instance  ->  a "human" demo-oracle reads the instruction's NAMED targets
    and clicks the matching (garbled-label) boxes + Submit  ->  `record_demo` captures it  ->  REPLAY 0-LLM
    ->  read MiniWoB's own reward oracle (WOB_RAW_REWARD)  ->  assert reward > 0.

The demo-oracle is the *human stand-in*: it reads the full DOM and performs the correct actions; the
recorder captures the NODES it touched (resilient locators), with no LLM grounding. MiniWoB randomizes per
seed, so a recorded flow replays its OWN seed (each instance is a flow) — the honest claim for a randomized
gym; record-once-replay-many is for stable recurring pages. `--provider` adds the contrast arm: LLM
authoring on the SAME seeds, to print "recorder N/N vs LLM authoring M/N".
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
from pathlib import Path
from tempfile import TemporaryDirectory

from ultracua.cache import FlowCache
from ultracua.flow import run_cached
from ultracua.recorder import record_demo

from .miniwob_env import (
    StaticServer,
    instruction,
    make_finalize,
    make_prepare,
    miniwob_html_root,
    read_instruction,
    task_url,
)

# The grounding-hard SELECTION tasks (garbled labels) — the genuine capability ceiling. All share the same
# shape: <label><input id=chN> garbled-label</label> rows + a Submit button, and the instruction names the
# targets VERBATIM, so the human stand-in reads them off and clicks the matching boxes.
#   NOTE: `click-checkboxes-soft` ("select words *similar to* unhappy") is deliberately EXCLUDED — it's a
#   SEMANTIC task (synonyms), not a garbled-label grounding one, so it needs a knowledge-bearing
#   demonstrator (a real human, or an LLM caption). Our automated exact-label oracle can't demo it — which
#   is itself the honest boundary: the recorder routes around GROUNDING, but the demonstration must still be
#   CORRECT, and semantic correctness isn't something a scripted oracle supplies.
CEILING_TASKS = ["click-checkboxes", "click-checkboxes-large", "click-option"]
SEEDS = [1, 2, 3]

# Reads each selectable box (checkbox/radio) + its visible label, plus the submit button — what the page
# actually shows, the way a human sees it.
_BOXES_JS = r"""
() => {
  const boxes = [...document.querySelectorAll('#area input[type=checkbox], #area input[type=radio]')]
    .map((c) => ({ label: (c.parentElement.textContent || '').replace(/\s+/g, ' ').trim(), id: c.id }));
  const btn = document.querySelector('#area button, #area [type=submit]');
  return { boxes, submit_id: btn ? btn.id : null };
}
"""


def _named_targets(instr: str) -> set[str]:
    """The labels the instruction names as targets — exactly what a human reads off 'Select X, Y and Z …'."""
    m = re.search(r"[Ss]elect\s+(.*?)\s+and click Submit", instr)
    if not m:
        return set()
    tl = m.group(1).strip()
    if tl.lower() == "nothing":
        return set()
    return {t.strip() for t in re.split(r",\s*|\s+and\s+", tl) if t.strip()}


def _make_demo():
    """The human stand-in: read the instruction's named targets, click the boxes whose visible label matches,
    then Submit. (Identical for checkboxes + radios — both are <label><input>+text rows with a Submit.)

    Caveat (latent, not hit by seeds 1–3): MiniWoB's random labels can in principle collide, and an exact
    label match would then click both twins — a wrong-bind. A real demonstrator faces the same ambiguity; a
    productionized recorder would resolve it by the clicked node's identity, not the label string."""
    async def demo(page) -> None:
        instr = await instruction(page)
        targets = _named_targets(instr)
        dom = await page.evaluate(_BOXES_JS)
        for box in dom["boxes"]:
            if box["label"] in targets:
                await page.click(f'#{box["id"]}')
        await page.click(f'#{dom["submit_id"]}' if dom["submit_id"] else "#area button")
    return demo


async def _llm_solves(url: str, goal: str, prepare, provider_name: str) -> bool:
    """Contrast arm: does LLM authoring solve the SAME seeded instance? (paid; --provider)."""
    from ultracua.providers import get_provider

    with TemporaryDirectory() as td:
        report = await run_cached(url, goal, get_provider(provider_name), FlowCache(root=Path(td)),
                                  mode="learn", headless=True, prepare=prepare, finalize=make_finalize())
        return bool((report.extra.get("finalize") or {}).get("raw", 0) > 0)


async def validate(task: str, seed: int, provider_name: str | None) -> dict:
    srv = StaticServer(miniwob_html_root())
    base = srv.start()
    try:
        url = task_url(base, task)
        prepare = make_prepare(seed)
        goal = await read_instruction(url, prepare)
        with TemporaryDirectory() as td:
            cache = FlowCache(root=Path(td))
            flow = await record_demo(url, _make_demo(), goal=goal, cache=cache, headless=True, prepare=prepare)
            # Strip id/test-id from the recorded specs so REPLAY must re-ground by role+name+css — the SAME
            # surface the LLM mis-grounds. MiniWoB's `chN` ids are internal scaffolding a real garbled-label
            # page wouldn't hand us; leaning on them would measure "click the Nth box by id", not the
            # grounding the recorder routes around. (The recorder captures ids in general — useful when a
            # real page has stable ones — we drop them HERE to keep the measurement on the hard surface.)
            for s in flow.steps:
                if s.locator is not None:
                    s.locator = s.locator.model_copy(update={"elem_id": None, "testid": None})
            cache.put(flow)
            id_free = all(not s.locator.elem_id and not s.locator.testid for s in flow.steps if s.locator)
            report = await run_cached(url, goal, None, cache, mode="replay", headless=True,
                                      prepare=prepare, finalize=make_finalize())
        raw = (report.extra.get("finalize") or {}).get("raw", 0) or 0
        n_targets = sum(1 for s in flow.steps if s.locator and s.locator.role in ("checkbox", "radio"))
        row = {"task": task, "seed": seed, "instruction": goal, "steps": len(flow.steps),
               "n_targets": n_targets, "id_free": id_free,
               "replay_success": bool(report.success), "reward": raw,
               "recorder_solved": bool(report.success and raw > 0), "llm_calls": report.llm_calls}
        if provider_name:
            row["llm_solved"] = await _llm_solves(url, goal, prepare, provider_name)
        return row
    finally:
        srv.stop()


async def measure(provider_name: str | None = None, tasks=None, seeds=None) -> dict:
    tasks = tasks or CEILING_TASKS
    seeds = seeds or SEEDS
    rows = [await validate(t, s, provider_name) for t in tasks for s in seeds]
    solved = sum(1 for r in rows if r["recorder_solved"])
    return {
        "provider": provider_name or "none",
        "instances": len(rows),
        "recorder_solved": solved,
        "recorder_rate": round(solved / len(rows), 4) if rows else 0.0,
        "all_replays_0llm": all(r["llm_calls"] == 0 for r in rows),
        "all_id_free": all(r["id_free"] for r in rows),  # replay re-grounds by role+name+css (no id crutch)
        "multi_target_instances": sum(1 for r in rows if r["n_targets"] >= 2),
        "llm_solved": (sum(1 for r in rows if r.get("llm_solved")) if provider_name else None),
        "rows": rows,
    }


async def run(provider_name: str | None, json_path: str | None) -> int:
    print(f"recorder ceiling validation: provider-contrast={provider_name or 'off'}  "
          f"tasks={CEILING_TASKS}  seeds={SEEDS}\n")
    rec = await measure(provider_name)
    for r in rec["rows"]:
        mark = "SOLVED" if r["recorder_solved"] else "MISS"
        extra = f"  (LLM {'solved' if r.get('llm_solved') else 'MISSED'})" if provider_name else ""
        print(f"  [{mark:<6}] {r['task']:<22} seed={r['seed']}  reward={r['reward']:+.2f}  "
              f"targets={r['n_targets']:<2} llm={r['llm_calls']}{extra}  | {r['instruction']}")
    print(f"\n== recorder solved {rec['recorder_solved']}/{rec['instances']} ceiling instances 0-LLM "
          f"({rec['multi_target_instances']} multi-target) — replay re-grounds by role+name+css, no id "
          f"(all_id_free={rec['all_id_free']}, all_0llm={rec['all_replays_0llm']}) ==")
    if provider_name:
        print(f"== contrast: LLM authoring solved {rec['llm_solved']}/{rec['instances']} of the SAME seeds ==")
    else:
        print("== contrast (LLM fails these): asserted from STATUS's measured ceiling; run --provider to "
              "measure it on these same seeds ==")
    if json_path:
        Path(json_path).write_text(json.dumps(rec, indent=2), encoding="utf-8")
        print(f"wrote {json_path}")
    # the lever holds iff the recorder cracks the ceiling instances 0-LLM AND did so id-free (the grounding surface)
    ok = rec["recorder_solved"] == rec["instances"] and rec["all_replays_0llm"] and rec["all_id_free"]
    return 0 if ok else 1


if __name__ == "__main__":
    ap = argparse.ArgumentParser(prog="benchmarks.recorder_ceiling")
    ap.add_argument("--provider", default=None, choices=["anthropic", "openai", "gemini"],
                    help="also run LLM authoring on the same seeds (the paid contrast arm).")
    ap.add_argument("--json", dest="json_path", default=None)
    args = ap.parse_args()
    raise SystemExit(asyncio.run(run(args.provider, args.json_path)))
