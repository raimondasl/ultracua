"""Assert the CI shards PARTITION the suite: every collected test in exactly one shard.

The reason this exists rather than being taken on trust. Sharding trades one silent failure for another
if you let it: a test that lands in NO shard still leaves every shard green, and no test in the suite can
fail for it — the coverage is simply gone, and the signal that would tell you is the signal you removed.
That is the "quiet is an allowlist" defect (R3.9/CLI-1) wearing a CI hat, and this project has shipped
that shape twice.

`pytest-split` partitions the collection, so the property should hold by construction. "Should hold by
construction" is exactly the class of claim this register keeps finding counterexamples to, so it is
checked: collect the whole suite, collect each shard, and compare the sets — union equals the whole, and
no test appears twice.

Runs collection only (`--collect-only`), so it costs seconds and launches no browser.
"""

from __future__ import annotations

import re
import subprocess
import sys

SPLITS = 2


def _nodeids(stdout: str) -> set[str]:
    """Every collected node id in `--collect-only -q` output.

    Deliberately NOT a strict regex. The first draft used `^tests[/\\][^\\s:]+::[^\\s]+$` and silently
    dropped 11 of 836 tests, because a parametrized id can contain SPACES — `test_window_size_env_parse[
    1440 x 900 -expected3]`, `test_a_held_write_never_arrives[... -form-POST navigation]`. An
    undercounting collector is the exact failure this script exists to catch, one level down: it would
    have reported a clean partition while 11 tests were invisible to it. Match on structure that cannot
    be confused instead — a line naming a file under tests/ with a `::` in it.
    """
    out: set[str] = set()
    for raw in stdout.splitlines():
        line = raw.strip().replace("\\", "/")
        if "::" in line and line.startswith("tests/"):
            out.add(line)
    return out


def collect(*extra: str) -> set[str]:
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "--collect-only", "-q", *extra],
        capture_output=True, text=True, check=False,
    )
    if proc.returncode not in (0, 5):        # 5 = no tests collected, which is itself a finding below
        print(proc.stdout[-4000:], file=sys.stderr)
        print(proc.stderr[-2000:], file=sys.stderr)
        raise SystemExit(f"collection failed ({' '.join(extra) or 'full suite'}): rc={proc.returncode}")
    found = _nodeids(proc.stdout)

    # CROSS-CHECK AGAINST PYTEST'S OWN COUNT, because this parser has already been wrong once in exactly
    # the direction that matters: it under-collected 11 of 836 (parametrized ids can contain SPACES) and
    # would have reported a clean partition over a hole. Two summary shapes, and reading the wrong number
    # from the sharded one is its own false alarm — this script raised exactly that on its first run:
    #     full   "836 tests collected in 0.86s"
    #     shard  "418/836 tests collected (418 deselected) in 0.81s"   <- SELECTED is the first number
    for line in reversed(proc.stdout.splitlines()):
        m = re.search(r"(?:(\d+)/\d+|(\d+))\s+tests? collected", line)
        if m:
            reported = int(m.group(1) or m.group(2))
            if reported != len(found):
                raise SystemExit(
                    f"the node-id parser is unreliable: pytest reported {reported} tests collected, this "
                    f"script recognised {len(found)}. Fix the parser before trusting any result below — "
                    f"an under-counting collector reports a clean partition over a hole.")
            break
    return found


def main() -> int:
    full = collect()
    if not full:
        raise SystemExit("collected ZERO tests for the full suite — the check itself is broken")

    shards = {g: collect(f"--splits={SPLITS}", f"--group={g}") for g in range(1, SPLITS + 1)}
    union: set[str] = set().union(*shards.values())

    missing = full - union
    dupes = [t for t in union if sum(t in s for s in shards.values()) > 1]

    for g, s in shards.items():
        print(f"shard {g}/{SPLITS}: {len(s)} tests")
    print(f"union: {len(union)}   full: {len(full)}")

    if missing:
        print(f"\nFAIL: {len(missing)} test(s) are in NO shard — they run nowhere and every shard is "
              f"still green:", file=sys.stderr)
        for t in sorted(missing)[:20]:
            print(f"  {t}", file=sys.stderr)
        return 1
    if dupes:
        print(f"\nFAIL: {len(dupes)} test(s) are in MORE THAN ONE shard (wasted wall-clock, and a shard "
              f"count that lies):", file=sys.stderr)
        for t in sorted(dupes)[:20]:
            print(f"  {t}", file=sys.stderr)
        return 1

    print("\nOK: the shards partition the suite — every test runs in exactly one.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
