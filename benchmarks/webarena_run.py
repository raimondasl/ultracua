"""Live WebArena-Verified runner: drive ultracua against a real site container, then score it.

This is the bridge from the offline adapter ([webarena_env.py]) to a real run:
  1. start the site container (Docker) and wait until it's serving,
  2. render the task's start URL at the local container (config),
  3. DRIVE the ultracua agent on the task intent — with the site's auto-login header and HAR
     recording on — via the learn/replay flow cache (so we also measure the speedup),
  4. EXTRACT the structured answer from the final page (one focused LLM call),
  5. write `agent_response.json`, then SCORE via the isolated evaluator and read back 0/1.

Requires Docker running + `ANTHROPIC_API_KEY` (gitignored `.env`). Today it targets the
offline-scorable RETRIEVE tasks on `shopping_admin` (header auto-login, read-only — no env
reset needed); NAVIGATE/MUTATE and other sites come later.

Usage:
  uv run python -m benchmarks.webarena_run --site shopping_admin --task-ids 108
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import shutil
import subprocess
import time
import urllib.request
from pathlib import Path

from ultracua.cache import FlowCache
from ultracua.config import settings
from ultracua.flow import run_cached
from ultracua.providers import get_provider

from benchmarks import webarena_env as wa

# Per-site container + auth spec (from webarena_verified's DEFAULT_CONTAINER_CONFIGS + auth header).
SITES = {
    "shopping_admin": {
        "image": "am1n3e/webarena-verified-shopping_admin",
        "port": 7780,
        "env_ctrl_port": 7781,
        "placeholder": "__SHOPPING_ADMIN__",
        # Header-based auto-login (bypasses the UI form): name is X-M2-Admin-Auto-Login,
        # value is "username:password" (per docs/environments/shopping_admin.md). The
        # `...-User: admin` form in the package docstring is stale and does NOT authenticate.
        "auth_header": {"X-M2-Admin-Auto-Login": "admin:admin1234"},
    },
}

_STATUSES = sorted(wa.STATUSES)


# --- docker / container lifecycle -------------------------------------------------------------
def _docker_bin() -> str:
    if shutil.which("docker"):
        return "docker"
    p = r"C:\Program Files\Docker\Docker\resources\bin\docker.exe"
    return p if os.path.exists(p) else "docker"


def _docker(*args: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run([_docker_bin(), *args], capture_output=True, text=True, check=check)


def container_up(site: str, name: str | None = None) -> str:
    spec = SITES[site]
    name = name or f"wa-{site}"
    ps = _docker("ps", "--filter", f"name=^{name}$", "--format", "{{.Names}}", check=False)
    if name in (ps.stdout or ""):
        return name
    _docker("rm", "-f", name, check=False)
    _docker(
        "run", "-d", "--name", name,
        "-p", f"{spec['port']}:80", "-p", f"{spec['env_ctrl_port']}:8877",
        f"{spec['image']}:latest",
    )
    return name


def wait_ready(site: str, timeout: float = 180.0) -> bool:
    url = f"http://localhost:{SITES[site]['port']}/"
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=5) as r:  # noqa: S310 (localhost)
                if r.status < 500:
                    return True
        except Exception:  # noqa: BLE001
            pass
        time.sleep(3)
    return False


def container_down(site: str, name: str | None = None) -> None:
    _docker("rm", "-f", name or f"wa-{site}", check=False)


def write_local_config(site: str, path: Path | None = None) -> Path:
    """A config mapping the site placeholder to the local container, for agent-input/eval."""
    spec = SITES[site]
    path = path or Path(settings.data_dir) / "webarena" / "config.local.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    cfg = {"environments": {spec["placeholder"]: {"urls": [f"http://localhost:{spec['port']}"]}}}
    path.write_text(json.dumps(cfg, indent=2), encoding="utf-8")
    return path


# --- answer extraction (final structured read) ------------------------------------------------
_EXTRACT_TOOL = {
    "name": "submit_answer",
    "description": "Return the WebArena-Verified agent response for this task.",
    "input_schema": {
        "type": "object",
        "properties": {
            "task_type": {"type": "string", "enum": sorted(wa.TASK_TYPES)},
            "status": {"type": "string", "enum": _STATUSES},
            "retrieved_data": {
                "type": ["array", "string", "number", "null"],
                "description": "The answer for a RETRIEVE task (array of values or objects); null for NAVIGATE/MUTATE.",
            },
            "error_details": {"type": ["string", "null"]},
        },
        "required": ["task_type", "status"],
    },
}


def _extract_sync(intent: str, page_text: str) -> dict:
    import anthropic

    client = anthropic.Anthropic()
    msg = client.messages.create(
        model=settings.model,
        max_tokens=1500,
        tools=[_EXTRACT_TOOL],
        tool_choice={"type": "tool", "name": "submit_answer"},
        messages=[
            {
                "role": "user",
                "content": (
                    "You are reading the final page an agent reached while doing a web task. "
                    "Return the answer strictly per the WebArena-Verified schema. For a RETRIEVE "
                    "task, put the requested data in `retrieved_data` in the EXACT shape the task "
                    "asks for (units, fields, ordering). Shape rules: a single value -> a scalar or "
                    "a 1-element list, NEVER a nested array (e.g. \"000000299\", not [[\"000000299\"]]); "
                    "a list of records -> a FLAT list of {key: value} objects. Follow any explicit "
                    "format directive in the task verbatim. If the data isn't on the page, set status "
                    "to NOT_FOUND_ERROR.\n\n"
                    f"TASK: {intent}\n\nFINAL PAGE TEXT:\n{page_text}"
                ),
            }
        ],
    )
    for block in msg.content:
        if block.type == "tool_use":
            d = block.input
            rd = d.get("retrieved_data")
            # Defensively unwrap one spurious nesting level ([["x"]] -> ["x"]); WebArena
            # retrieved_data is a flat list of scalars or {key: value} objects, never list-of-list.
            if isinstance(rd, list) and len(rd) == 1 and isinstance(rd[0], list):
                rd = rd[0]
            return {
                "task_type": d.get("task_type", "RETRIEVE"),
                "status": d.get("status", "SUCCESS"),
                "retrieved_data": rd,
                "error_details": d.get("error_details"),
            }
    return {"task_type": "RETRIEVE", "status": "UNKNOWN_ERROR", "retrieved_data": None,
            "error_details": "extractor returned no tool call"}


def _make_finalize(intent: str, out: dict):
    async def _finalize(session):
        # Let async grid/AJAX content settle before reading — replay fires cached clicks far
        # faster than the LLM-paced learn run, so without this it can extract a still-loading page.
        try:
            await session.page.wait_for_load_state("networkidle", timeout=8000)
        except Exception:  # noqa: BLE001
            pass
        try:
            text = await session.page.inner_text("body")
        except Exception:  # noqa: BLE001
            text = ""
        text = " ".join(text.split())[:12000]
        answer = await asyncio.to_thread(_extract_sync, intent, text)
        out.update(answer)  # the 4-key agent_response the runner writes
        # Signal completion to run_cached so a read task that solved via this full-text extraction
        # caches its flow even though the agent never emitted `done`. (`solved` is read by
        # _learn; it is NOT written into agent_response.)
        solved = answer.get("status") == "SUCCESS" and answer.get("retrieved_data") not in (None, [], "")
        return {"solved": solved, **answer}

    return _finalize


# --- run one task -----------------------------------------------------------------------------
async def run_task(
    site: str,
    task: dict,
    output_root: Path,
    *,
    mode: str,
    cache: FlowCache,
    provider,
    max_steps: int,
    headless: bool,
) -> dict:
    spec = SITES[site]
    task_id = task["task_id"]
    intent = task["intent"]
    start_url = task["start_urls"][0]
    answer: dict = {}
    wa.task_dir(output_root, task_id).mkdir(parents=True, exist_ok=True)  # for the HAR

    t0 = time.perf_counter()
    report = await run_cached(
        url=start_url,
        goal=intent,
        provider=provider,
        cache=cache,
        mode=mode,
        max_steps=max_steps,
        headless=headless,
        scope=f"webarena:{site}:{task_id}",
        prepare=None,
        # finalize extracts the answer AND signals `solved`, so a read task that solved via the
        # final full-text extraction caches its flow even if the agent never emitted `done`.
        finalize=_make_finalize(intent, answer),
        record_har_path=str(wa.har_path(output_root, task_id)),
        extra_headers=spec["auth_header"],
    )
    elapsed = (time.perf_counter() - t0) * 1000.0

    # A cache miss (no learned flow) never creates a session/HAR, so there's nothing to score.
    if report.mode == "miss":
        return {
            "task_id": task_id, "mode": "miss", "score": 0.0, "llm_calls": report.llm_calls,
            "elapsed_ms": round(elapsed, 1), "answer": {},
            "fail": ["replay: no learned flow (cache miss — learn didn't emit done)"],
        }

    if not answer:  # finalize didn't run — write a safe failure response
        answer = {"task_type": "RETRIEVE", "status": "UNKNOWN_ERROR",
                  "retrieved_data": None, "error_details": "no answer produced"}
    wa.write_agent_response(output_root, task_id, **answer)
    # A run that failed early may not have flushed a HAR with entries; the evaluator needs one,
    # so backfill a placeholder rather than letting it skip the task (which would be unscorable).
    if not wa.har_path(output_root, task_id).exists():
        wa.write_placeholder_har(output_root, task_id)
    wa.run_eval(output_root, [task_id])
    try:
        score = wa.get_score(output_root, task_id)
        fails = wa.get_failure_reasons(output_root, task_id) if score < 1.0 else []
    except FileNotFoundError:
        score, fails = 0.0, ["evaluator skipped the run (invalid submission)"]
    return {
        "task_id": task_id, "mode": report.mode, "score": score,
        "llm_calls": report.llm_calls, "elapsed_ms": round(elapsed, 1),
        "answer": answer, "fail": fails,
    }


async def run_tasks(site: str, task_ids: list[int], *, provider_name: str, max_steps: int,
                    headless: bool, mode: str) -> list[dict]:
    config = write_local_config(site)
    tasks = wa.get_agent_input(task_ids, config_path=config)
    provider = get_provider(provider_name)
    out_root = Path(settings.data_dir) / "webarena" / "live" / site
    results = []
    for task in tasks:
        cache = FlowCache(root=out_root / "flows")
        row: dict = {"task_id": task["task_id"]}
        if mode in ("auto", "learn"):
            row["learn"] = await run_task(site, task, out_root / "learn", mode="learn", cache=cache,
                                          provider=provider, max_steps=max_steps, headless=headless)
        do_replay = mode == "replay" or (mode == "auto" and row.get("learn", {}).get("score") == 1.0)
        if do_replay:
            row["replay"] = await run_task(site, task, out_root / "replay", mode="replay", cache=cache,
                                           provider=provider, max_steps=max_steps, headless=headless)
        results.append(row)
    return results


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Drive ultracua on live WebArena-Verified tasks and score.")
    ap.add_argument("--site", default="shopping_admin", choices=sorted(SITES))
    ap.add_argument("--task-ids", required=True, help="comma-separated task ids")
    ap.add_argument("--provider", default="anthropic")
    ap.add_argument("--max-steps", type=int, default=15)
    ap.add_argument("--headed", action="store_true", help="show the browser (default headless)")
    ap.add_argument("--mode", choices=("auto", "learn", "replay"), default="auto",
                    help="auto=learn then replay-on-success; learn=learn only; replay=use cached flow")
    ap.add_argument("--keep-up", action="store_true", help="leave the container running on exit")
    args = ap.parse_args(argv)

    site = args.site
    task_ids = [int(t) for t in args.task_ids.split(",")]

    if not wa.cli_available():
        print("webarena-verified CLI unavailable (need uv). Aborting.")
        return 2
    print(f"[live] starting {site} container ...")
    container_up(site)
    if not wait_ready(site):
        print("[live] container did not become ready in time.")
        return 1
    print("[live] container ready.")

    try:
        results = asyncio.run(run_tasks(
            site, task_ids, provider_name=args.provider, max_steps=args.max_steps,
            headless=not args.headed, mode=args.mode,
        ))
    finally:
        if not args.keep_up:
            print("[live] stopping container ...")
            container_down(site)

    print("\n==== results ====")
    for row in results:
        ln, rp = row.get("learn"), row.get("replay")
        parts = [f"task {row['task_id']}:"]
        if ln:
            parts.append(f"learn score={ln['score']} ({ln['llm_calls']} LLM, {ln['elapsed_ms']}ms, {ln['mode']})")
        if rp:
            speed = (ln["elapsed_ms"] / rp["elapsed_ms"]) if (ln and rp["elapsed_ms"]) else 0
            tag = f" -> {speed:.1f}x" if ln else ""
            parts.append(f"| replay score={rp['score']} ({rp['llm_calls']} LLM, {rp['elapsed_ms']}ms{tag})")
        print(" " + " ".join(parts))
        for label, r in (("learn", ln), ("replay", rp)):
            if r and r["score"] < 1.0 and r["fail"]:
                print(f"     {label} diag: {r['fail'][:2]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
