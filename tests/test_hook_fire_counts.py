"""reshape-plan step 1.8 — how many times each HOOK fires, per mode, as a committed table.

WHY THIS EXISTS AND WHY IT WAS WRITTEN FIRST. 1.8 replaces ~21 scalar parameters threaded through the
engine with two objects. Every call site moves. The failure that migration invites is not a crash — a
dropped hook is silent: the run still succeeds, the caller's callback simply never fires. A `finalize`
that stops being called returns `None` data on a read; a `pre_write` that stops being called turns the
whole-flow confirm from an absent->present TRANSITION back into a bare presence check, which is the
exact defect R4.12 records on the learn path.

So the numbers below were captured on the tree BEFORE the refactor and are asserted after it. They are
a fact about behaviour, not about shape: if a bundle drops a hook on one path, the count for that
(mode, hook) moves and this file says which.

`verifier` deliberately has a row of zeros on the replay modes — it is consulted only while AUTHORING.
A table with no zeros in it is a table that cannot show a hook being wired somewhere new by accident.
"""

from __future__ import annotations

import json
import pathlib

import pytest

from ultracua.cache import FlowCache, flow_key
from ultracua.flow import run_cached
from ultracua.providers.scripted import ScriptedProvider

from benchmarks.shop_flow import GOAL, STEPS, index_url

GOLDEN = pathlib.Path(__file__).resolve().parent / "goldens" / "hook_fire_counts.json"

HOOKS = ("on_step", "prepare", "finalize", "pre_write", "verifier")


class _Counter:
    """One set of counting hooks for one run. Each records a fire and does nothing else."""

    def __init__(self) -> None:
        self.n = {h: 0 for h in HOOKS}

    def kwargs(self) -> dict:
        async def prepare(session):
            self.n["prepare"] += 1

        async def finalize(session):
            self.n["finalize"] += 1
            return {"ok": True}

        async def pre_write(session):
            self.n["pre_write"] += 1

        async def verifier(goal, obs):
            self.n["verifier"] += 1
            return True

        def on_step(tr):
            self.n["on_step"] += 1

        return dict(on_step=on_step, prepare=prepare, finalize=finalize,
                    pre_write=pre_write, verifier=verifier)


# The five rows. `learn_capped` is not a mode — it is a learn stopped by `max_steps`, and it is here
# because WITHOUT IT THE `verifier` ROW IS ALL ZEROS. The verifier is consulted at exactly one place,
# `if not success and steps and verifier is not None`, so any run whose agent emits `done` never
# reaches it. A row that is zero everywhere pins nothing: the migration could drop the parameter
# entirely and this file would stay green. The anti-vacuity cell below is what caught that.
#
# `max_steps` rather than a truncated script, and the difference is measured: `ScriptedProvider`
# returns `done` once its list is EXHAUSTED, so a shorter script still ends cleanly and still never
# asks the verifier. Capping the loop is the only way in through the existing machinery.
ROWS = ("learn", "learn_capped", "auto", "replay", "repair")

# Two of the flow's four steps — enough that `steps` is non-empty and `success` is not yet set.
CAPPED_AT = 2


async def _fire_counts(row: str, tmp_path) -> dict:
    """Run one flow through `run_cached`, counting every hook.

    `learn` gets a scripted teacher and a fresh cache; the replay modes get a cache already seeded by
    a learn, so each row measures the mode it names rather than whatever the cache happened to hold.
    """
    mode = "learn" if row == "learn_capped" else row
    cache = FlowCache(root=tmp_path / row)
    url = index_url()
    if mode != "learn":
        seed = await run_cached(url, GOAL, ScriptedProvider(list(STEPS)), cache,
                                mode="learn", headless=True)
        assert seed.success, seed.note
        assert cache.get(flow_key(GOAL, url)) is not None

    c = _Counter()
    provider = ScriptedProvider(list(STEPS)) if mode in ("learn", "auto", "repair") else None
    extra = {"max_steps": CAPPED_AT} if row == "learn_capped" else {}
    report = await run_cached(url, GOAL, provider, cache, mode=mode, headless=True,
                              **extra, **c.kwargs())
    assert report.success, f"{row}: {report.note}"
    return c.n


@pytest.mark.parametrize("mode", ROWS)
async def test_the_hooks_fire_the_number_of_times_the_golden_says(mode: str, tmp_path) -> None:
    stored = json.loads(GOLDEN.read_text(encoding="utf-8"))["counts"]
    got = await _fire_counts(mode, tmp_path)
    assert got == stored[mode], (
        f"mode={mode}: hook fire counts moved.\n  golden: {stored[mode]}\n  actual: {got}\n"
        f"A hook that stops firing is SILENT — the run still succeeds and the caller's callback "
        f"simply never runs. If the change is deliberate, regenerate with --update-hook-counts and "
        f"read the diff before committing it.")
    print(f"{mode:<8} {got}")


def test_the_table_is_not_degenerate() -> None:
    """ANTI-VACUITY, both ways. A table of all zeros is satisfied by an engine that calls no hook at
    all; a table with no zeros cannot show a hook wired somewhere it does not belong."""
    stored = json.loads(GOLDEN.read_text(encoding="utf-8"))["counts"]
    assert set(stored) == set(ROWS)
    for mode, row in stored.items():
        assert set(row) == set(HOOKS), f"{mode}: the golden's hooks are {sorted(row)}"
    flat = [n for row in stored.values() for n in row.values()]
    assert any(n > 0 for n in flat), "every count is zero — no hook fires anywhere"
    assert any(n == 0 for n in flat), (
        "no count is zero. `verifier` is consulted only while AUTHORING and must read 0 on the "
        "replay modes; without a zero anywhere the table cannot show a hook being wired somewhere "
        "new by accident.")
    per_hook = {h: sum(row[h] for row in stored.values()) for h in HOOKS}
    assert all(n > 0 for n in per_hook.values()), (
        f"a hook never fires in ANY mode: {[h for h, n in per_hook.items() if not n]}. That row is "
        f"pinning nothing, so the migration could drop it and this file would stay green.")
    print(f"per-hook totals across the four modes: {per_hook}")
