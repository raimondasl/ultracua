"""The ratchets hold, and every one of them has been seen to move.

Step 0.4a. A ratchet is only worth its file if three things are true, and each gets a cell that DRIVES
the mechanism rather than describing it:

* it counts the shape (inject one site -> the number rises by exactly one);
* it counts ONLY that shape (the same injection leaves every other ratchet unchanged);
* it counts CODE, not prose (the same text inside a comment and a docstring moves nothing) — which is
  the specific error the plan's own grep-derived numbers made, twice.

Plus the verdict function itself, driven to every corner: growth, shrink, a derivation gone silent, a
ratchet with no baseline, and a baseline with no derivation. A rule that can only fire one way is the
conclusion pre-written.
"""

from __future__ import annotations

import json
import re
import shutil
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import ratchets  # noqa: E402


# One extra site per ratchet, appended to a SCRATCH COPY of src/. Each must parse, and each must move
# exactly one ratchet — that second property is what proves the derivations are specific.
INJECTIONS = {
    "spec_mutate_raw": ("flows.py", "\n\ndef _ratchet_probe(spec):\n    return spec.mutate is None\n"),
    "flow_key_transcriptions": (
        "flows.py", "\n\ndef _ratchet_probe(spec):\n"
                    "    return flow_key(spec.goal, spec.start_url, spec.scope)\n"),
    "bare_flow_replay_error": ("flows.py", '\n\ndef _ratchet_probe():\n    raise FlowReplayError("x")\n'),
    "cli_system_exit": ("cli.py", "\n\ndef _ratchet_probe():\n    raise SystemExit(2)\n"),
    "run_record_write_sites": ("flows.py", '\n\ndef _ratchet_probe(record):\n    record.mode = "x"\n'),
    "engine_positional_params": ("flow.py", "\n\ndef _learn(only_one_param):\n    return None\n"),
}


@pytest.fixture(scope="session")
def pristine_counts():
    """Derived ONCE against the real tree. A scratch copy is byte-identical, so this is its baseline
    too — and paying for it seven times was most of this file's runtime in the fast tier. The
    equivalence is not assumed: `test_a_pristine_copy_derives_the_same_counts` checks it."""
    return {k: len(v) for k, v in ratchets.derive_all().items()}


class _Scratch:
    """A mutable copy of `src/`, restored after each test so the SESSION keeps one directory.

    The obvious shape — copytree into a fresh `tmp_path` per test — cost 16 s of the FAST tier, because
    `ratchets._PARSED` keys on the path and a new tmp directory misses the cache for all 47 modules,
    seven times over. One directory reused means 47 parses once and ONE per test (the file the test
    appends to). Restoring makes the stat match the pristine entry again, so the cache is correct rather
    than merely fast.
    """

    def __init__(self, root: Path):
        self.root = root
        self._saved: dict = {}

    def append(self, filename: str, snippet: str) -> None:
        path = self.root / filename
        self._saved.setdefault(path, path.read_text(encoding="utf-8"))
        path.write_text(self._saved[path] + snippet, encoding="utf-8")

    def restore(self) -> None:
        for path, text in self._saved.items():
            path.write_text(text, encoding="utf-8")
        self._saved.clear()


@pytest.fixture(scope="session")
def _scratch_root(tmp_path_factory):
    dst = tmp_path_factory.mktemp("ratchet_src") / "src" / "ultracua"
    # `__pycache__` is 780 KB of the 1.9 MB and nothing here reads it.
    shutil.copytree(ratchets.SRC, dst, ignore=shutil.ignore_patterns("__pycache__"))
    return dst


@pytest.fixture
def scratch_src(_scratch_root, monkeypatch):
    """The derivations, pointed at the copy. The repo itself is never modified."""
    monkeypatch.setattr(ratchets, "ROOT", _scratch_root.parent.parent)
    monkeypatch.setattr(ratchets, "SRC", _scratch_root)
    scratch = _Scratch(_scratch_root)
    yield scratch
    scratch.restore()


# ---------------------------------------------------------------------------------------------------
# 1. The ratchets themselves.

def test_every_ratchet_holds() -> None:
    """THE cell. Growth fails here; so does an un-locked-in shrink."""
    problems = ratchets.verdict(ratchets.derive_all(), ratchets.read_baseline())
    assert not problems, "\n".join(f"{name}: {msg}" for name, msg in problems)


def test_every_derivation_matches_something() -> None:
    """A derivation that matches nothing is inert, and would report the shape as already removed.

    `MAY_BE_ZERO` is the earned exemption: a ratchet whose END STATE is zero — today only
    `run_record_write_sites`, which counts `record.<field> =` writes OUTSIDE `_RecordSink` and whose
    correct value after 1.5 is none at all. It is not exempt from being live: its derivation asserts
    that the same pattern still finds the writes INSIDE the sink before returning the ones outside, so
    a broken pattern still fails loudly rather than reading as a clean codebase. The cell below arms
    that.
    """
    for name, hits in sorted(ratchets.derive_all().items()):
        if name in ratchets.MAY_BE_ZERO:
            continue
        assert hits, f"{name} matched NOTHING — the pattern is broken, not the codebase clean"


# How to GUT each zero-ratchet's liveness evidence, so the cell below can watch the derivation refuse.
# A `MAY_BE_ZERO` entry with no row here fails, which is what makes the exemption earned rather than
# claimed: joining that set costs an entry, and a ratchet that cannot be gutted cannot prove it is live.
#
# Each value is (pattern, replacement, minimum-hits, expected message fragment).
LIVENESS_GUTS = {
    # `\brecord\.` on a word boundary so `self._record.` — a READ of the sink's own attribute — is
    # untouched. EVERY write, not a couple: leaving one leaves `inside` non-empty and the cell asserts
    # nothing.
    "run_record_write_sites": (r"\brecord\.", "_gone.", 10, "matches NOTHING inside"),
    # Every `raise <Subclass>(` at once — the SUBCLASS raises are what prove the walk still reaches the
    # family. Leaving one behind leaves `sub` non-empty and the assert never fires.
    "bare_flow_replay_error": (r"raise (?=[A-Z])(\w*Error)\(", r"raise _Gone_\1(", 12,
                               "no `raise <FlowReplayError subclass>"),
}


def test_every_zero_ratchet_has_a_way_to_prove_it_is_live() -> None:
    """`MAY_BE_ZERO` is an EXEMPTION, and an exemption nobody arms is a way to hide a dead pattern.

    The cell below was written for one ratchet and named its derivation by hand. A second joined the
    set at 1.4, and a hand-named cell would have left it un-armed while reading as coverage — the
    "structural scan that names ONE function" shape, in the arming rather than in the scan.
    """
    missing = sorted(ratchets.MAY_BE_ZERO - set(LIVENESS_GUTS))
    assert not missing, (
        f"{missing} may report zero and this file has no way to prove their patterns are live. Add a "
        f"LIVENESS_GUTS row, or do not exempt them.")
    stale = sorted(set(LIVENESS_GUTS) - ratchets.MAY_BE_ZERO)
    assert not stale, f"{stale} have a gut recipe and are no longer exempt — drop the row"


@pytest.mark.parametrize("target", sorted(LIVENESS_GUTS))
def test_a_zero_ratchet_still_proves_its_pattern_is_live(target, scratch_src) -> None:
    """The exemption must not become a way to hide a dead pattern.

    A `MAY_BE_ZERO` derivation returns zero by design, so the usual "it matched something" check cannot
    speak for it. Its liveness comes from the OTHER half of the same walk — the writes inside the sink,
    the raises of the subclasses — and deleting that half must make the derivation FAIL rather than
    quietly report a clean tree.
    """
    pattern, replacement, floor, message = LIVENESS_GUTS[target]
    path = scratch_src.root / "flows.py"
    gutted, n = re.subn(pattern, replacement, path.read_text(encoding="utf-8"))
    assert n >= floor, f"{target}: only {n} liveness sites found to remove — this cell would be vacuous"
    scratch_src.append("flows.py", "")          # register the file for restore
    path.write_text(gutted, encoding="utf-8")

    with pytest.raises(AssertionError, match=re.escape(message)):
        ratchets.RATCHETS[target][0]()


def test_the_baseline_is_internally_consistent_and_covers_exactly_the_derivations() -> None:
    data = json.loads(ratchets.BASELINE.read_text(encoding="utf-8"))["ratchets"]
    assert set(data) == set(ratchets.RATCHETS), "the baseline and the derivations have drifted apart"
    for name, entry in data.items():
        assert sum(entry["by_file"].values()) == entry["total"], f"{name}: by_file does not sum to total"
        assert entry["disposition"], f"{name}: no disposition — a ratchet with no plan step is a museum"


# ---------------------------------------------------------------------------------------------------
# 2. Each derivation is shown MOVING, and shown not moving its neighbours.

def test_a_pristine_copy_derives_the_same_counts(scratch_src, pristine_counts) -> None:
    """The premise the two cells below rest on, paid once instead of seven times."""
    assert {k: len(v) for k, v in ratchets.derive_all().items()} == pristine_counts


@pytest.mark.parametrize("target", sorted(INJECTIONS))
def test_each_ratchet_catches_one_injected_site(target, scratch_src, pristine_counts) -> None:
    before = pristine_counts

    filename, snippet = INJECTIONS[target]
    scratch_src.append(filename, snippet)

    after = {k: len(v) for k, v in ratchets.derive_all().items()}
    assert after[target] == before[target] + 1, (
        f"{target}: injecting one site into {filename} moved the count "
        f"{before[target]} -> {after[target]}, expected +1"
    )
    unchanged = {k: (before[k], after[k]) for k in before if k != target and before[k] != after[k]}
    assert not unchanged, (
        f"injecting a {target} site also moved {unchanged} — the derivations overlap, so a fix credited "
        f"to one plan step would move another step's number"
    )


def test_the_shape_inside_a_COMMENT_or_a_DOCSTRING_is_not_counted(scratch_src, pristine_counts) -> None:
    """The specific error the plan's grep made: `spec.mutate is not None` appears in its own findings.

    Six occurrences of the shape as PROSE inflated that count from 27 to 33. This arms the fix.
    """
    before = pristine_counts

    scratch_src.append("flows.py", (
        "\n\ndef _ratchet_prose_probe():\n"
        '    """A docstring saying spec.mutate is not None, and flow_key(spec.goal, spec.start_url,\n'
        '    spec.scope), and raise FlowReplayError(x), and record.mode = y."""\n'
        "    # a comment saying spec.mutate is None and raise FlowReplayError('x')\n"
        '    return "spec.mutate is not None"  # and a string literal saying it too\n'
    ))

    after = {k: len(v) for k, v in ratchets.derive_all().items()}
    assert after == before, (
        f"prose moved a ratchet: {[(k, before[k], after[k]) for k in before if before[k] != after[k]]}. "
        f"That is the grep failure mode this file exists to end."
    )


# ---------------------------------------------------------------------------------------------------
# 3. The verdict function, driven to every corner.

def _sites(n, name="x"):
    return [ratchets.Site(f"src/{name}.py", i) for i in range(n)]


def test_the_verdict_fires_in_every_direction() -> None:
    base = {"spec_mutate_raw": {"total": 10, "by_file": {}, "disposition": "d"}}

    assert not ratchets.verdict({"spec_mutate_raw": _sites(10)}, base), "equal must be silent"

    grew = ratchets.verdict({"spec_mutate_raw": _sites(11)}, base)
    assert len(grew) == 1 and "GREW 10 -> 11" in grew[0][1]

    shrank = ratchets.verdict({"spec_mutate_raw": _sites(9)}, base)
    assert len(shrank) == 1 and "--update" in shrank[0][1], shrank

    # A shrink to ZERO is reported as a broken derivation, not as a triumph — the prove_red rule.
    silent = ratchets.verdict({"spec_mutate_raw": _sites(0)}, base)
    assert len(silent) == 1 and "matches NOTHING" in silent[0][1], silent

    unseeded = ratchets.verdict({"spec_mutate_raw": _sites(10), "brand_new": _sites(3)}, base)
    assert any("no baseline entry" in m for _, m in unseeded), unseeded

    orphaned = ratchets.verdict({}, base)
    assert any("no derivation defines it" in m for _, m in orphaned), orphaned


def test_a_zero_baseline_that_stays_zero_is_not_reported_as_broken() -> None:
    """The end state every ratchet is driving toward must be QUIET, or Phase 1 ends with a red suite."""
    done = {"spec_mutate_raw": {"total": 0, "by_file": {}, "disposition": "d"}}
    assert not ratchets.verdict({"spec_mutate_raw": []}, done)


# ---------------------------------------------------------------------------------------------------
# 4. The surfaces a human types.

def test_the_check_and_update_surfaces_run() -> None:
    import subprocess

    proc = subprocess.run([sys.executable, "scripts/ratchets.py"], cwd=ROOT,
                          capture_output=True, text=True)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "every ratchet holds" in proc.stdout

    shown = subprocess.run([sys.executable, "scripts/ratchets.py", "--print"], cwd=ROOT,
                           capture_output=True, text=True)
    assert shown.returncode == 0
    assert "src/ultracua/flows.py:" in shown.stdout, "--print must name sites, not just counts"
