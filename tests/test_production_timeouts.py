"""The SHIPPED timeout defaults may not drift, and one of the two escape hatches is CLOSED (R4.24).

A localhost round-trip stalled past the 5 s action budget on the Windows runner. The obvious mitigation
— set `ULTRACUA_ACTION_TIMEOUT_MS=15000` in CI, leaving the shipped default alone — **was built and then
NOT shipped**, and this file used to say the opposite. Its docstring claimed "CI now sets" it and its
failure message called it "the knob to reach for", while `test_raising_the_bound_for_ci_is_an_env_var...`
below said, correctly, that the mitigation was not shipped. The file contradicted itself for four
releases, and the half that a failing assertion actually prints was the wrong half.

WHY IT IS CLOSED, because "do not do this" without a reason gets undone by the next person who meets the
flake. `benchmarks/drift_bench.py:622` records the AMBIENT `settings.action_timeout_ms` into every run —
deliberately, per its own note at line 80, since forcing a value once made "part of what the resilience
number measured the speed of the machine measuring it". `baselines/drift_v2.json` then PINS
`action_timeout_ms: 5000`, and `tests/test_drift_bench.py::test_the_baseline_is_current` exact-compares
it. So an ambient CI override does not merely fail to help: it reddens the bench, ~14 minutes into a
shard, in a test that has nothing to do with timeouts. R4.24 records this and concludes "R4.24 ships
unmitigated".

**The two variables are NOT symmetrical**, which is why the messages below are derived per-field rather
than written once. `action_timeout_ms` is pinned by a baseline; `nav_timeout_ms` is pinned by none, so
`ULTRACUA_NAV_TIMEOUT_MS` remains a knob a slow machine can legitimately turn.

NOTHING HERE TRANSCRIBES THE WORKFLOW. Whether CI sets a variable is read from `ci.yml`, and whether a
field is baseline-pinned is read from `baselines/`. That is the point: the defect being fixed was a
sentence about CI that was never true, sitting in the one place a reader looks when the guard fires.

Asserted against the SOURCE rather than the imported value, because `Settings` reads the environment at
class-definition time — so under an environment that DID set these, an import-time check would assert
the very thing it is meant to catch.
"""

from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest

_CONFIG = Path("src/ultracua/config.py")
_CI = Path(".github/workflows/ci.yml")
_BASELINES = Path("baselines")

# name -> (env var, shipped default). Both timeouts, because raising either one hides the same class of
# stall, and `nav_timeout_ms` is the one a form-POST navigation actually rides.
_BOUNDS = {
    "action_timeout_ms": ("ULTRACUA_ACTION_TIMEOUT_MS", "5000"),
    "nav_timeout_ms": ("ULTRACUA_NAV_TIMEOUT_MS", "15000"),
}


def _defaults_from_source() -> dict:
    """`{field: (env_var, default_literal)}` for every `int(os.getenv("X", "N"))` field in Settings."""
    tree = ast.parse(_CONFIG.read_text(encoding="utf-8"))
    out: dict = {}
    for node in ast.walk(tree):
        if not (isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) and node.value):
            continue
        call = node.value
        if isinstance(call, ast.Call) and isinstance(call.func, ast.Name) and call.func.id == "int":
            inner = call.args[0] if call.args else None
            if (isinstance(inner, ast.Call) and isinstance(inner.func, ast.Attribute)
                    and inner.func.attr == "getenv" and len(inner.args) == 2):
                env, default = inner.args
                if isinstance(env, ast.Constant) and isinstance(default, ast.Constant):
                    out[node.target.id] = (env.value, default.value)
    return out


def _ci_yaml_without_comments() -> str:
    """`ci.yml` with comment lines removed.

    Load-bearing: this file's whole subject is a variable the workflow does NOT set, so the workflow is
    very likely to acquire a comment SAYING so. A bare containment check would then read that comment as
    the setting and report the opposite of the truth — the false-positive door
    `tests/test_ci_provisioning.py` closes for `apt-get`, one file over. Full-line comments are how
    `ci.yml` writes them; an inline trailing `#` would still fool this, which is a known and accepted
    limit rather than an unnoticed one.
    """
    return "\n".join(
        line for line in _CI.read_text(encoding="utf-8").splitlines()
        if not line.lstrip().startswith("#")
    )


def _fields_pinned_by_a_baseline() -> dict[str, str]:
    """`{settings field: baseline filename}` for every field a committed baseline records.

    A pinned field cannot be overridden ambiently without re-recording that baseline, because the bench
    compares the recorded value exactly. DERIVED from `baselines/`, so a baseline that starts or stops
    pinning a field changes this answer without anybody editing a list.
    """
    out: dict[str, str] = {}
    for path in sorted(_BASELINES.glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):  # pragma: no cover - a corrupt baseline is not our news
            continue
        if isinstance(data, dict):
            for field in _BOUNDS:
                if field in data:
                    out.setdefault(field, path.name)
    return out


def _pinned_overrides(ci_text: str) -> list[tuple[str, str]]:
    """`[(field, env), ...]` for every bound whose field a baseline pins AND whose env `ci_text` sets.

    THE one definition of the violation, called by the cell that enforces it and by the cell that proves
    the enforcement can fire. Two copies of a predicate is how an arming proof ends up proving something
    the real check does not do.
    """
    pinned = _fields_pinned_by_a_baseline()
    return [
        (field, env)
        for field, (env, _default) in sorted(_BOUNDS.items())
        if field in pinned and env in ci_text
    ]


def _how_to_buy_time(field: str, env: str) -> str:
    """The advice a failing bound should print — COMPUTED, never remembered.

    This function exists because the sentence it replaces was false from 0.85.0 to 0.110.0: it named a CI
    variable that has never been in `ci.yml` and, worse, one that cannot be put there.
    """
    if env in _ci_yaml_without_comments():
        return f"CI already sets `{env}` in {_CI}; raise that, not the shipped default."
    pinned_by = _fields_pinned_by_a_baseline().get(field)
    if pinned_by:
        return (
            f"`{env}` is NOT a knob you may reach for. `benchmarks/drift_bench.py` records the ambient "
            f"`settings.{field}` into every run, `baselines/{pinned_by}` pins it, and "
            f"`tests/test_drift_bench.py::test_the_baseline_is_current` exact-compares it — so setting "
            f"`{env}` in CI reddens the bench ~14 min into a shard, in a test about corpus provenance "
            f"rather than about timeouts. That mitigation was built and refused; see R4.24 in "
            f"docs/open-defects.md. Re-record the baseline deliberately, or fix the slow thing."
        )
    return (
        f"`{env}` is available and nothing in CI sets it today; no baseline pins `{field}`, so a slow "
        f"machine can buy time there without moving what ships."
    )


@pytest.mark.parametrize("field", sorted(_BOUNDS))
def test_the_shipped_timeout_default_has_not_been_raised(field: str) -> None:
    """If this fails because CI is flaky, the fix is NOT this number — see the message for which knob."""
    found = _defaults_from_source()
    assert field in found, (
        f"{field} is no longer an `int(os.getenv(...))` default in config.py — this guard has stopped "
        f"guarding anything, which is worse than it failing")
    env, default = found[field]
    assert (env, default) == _BOUNDS[field], (
        f"{field} ships as {found[field]}, expected {_BOUNDS[field]}. Raising a production timeout makes "
        f"every real user wait longer before a stuck page fails LOUD.\n"
        f"    {_how_to_buy_time(field, _BOUNDS[field][0])}")


def test_the_guard_can_actually_fail() -> None:
    """Anti-vacuity. A parser that silently finds nothing would let every assertion above pass over an
    empty dict — the shape this project has already shipped twice (a shard checker that under-collected,
    a redaction docstring that cited a floor it did not carry)."""
    found = _defaults_from_source()
    assert len(found) >= 2, f"the source parser found {len(found)} int-env defaults; it is not working"
    assert found["action_timeout_ms"] == ("ULTRACUA_ACTION_TIMEOUT_MS", "5000")


def test_raising_the_bound_for_ci_is_an_env_var_and_the_env_var_still_exists() -> None:
    """The escape hatch must stay available even though R4.24's mitigation was NOT shipped.

    The whole argument for not touching these defaults is that a slow environment has a knob of its own.
    If someone deletes the `os.getenv` indirection — "nothing sets it, so simplify" — the only remaining
    way to give CI more time becomes editing the shipped default, which is the thing above forbids. The
    knob is load-bearing precisely when it is unused.
    """
    found = _defaults_from_source()
    for field, (env, _default) in _BOUNDS.items():
        assert found.get(field, (None, None))[0] == env, (
            f"{field} no longer reads {env} from the environment — a slow machine now has no way to buy "
            f"time except by changing what ships")


def test_ci_does_not_override_a_timeout_that_a_baseline_pins() -> None:
    """R4.24 CARRIED, not cited — which is the phrase this module's docstring used to use while not
    doing it.

    The register says the mitigation "was built, then not shipped" because it reddened
    `test_drift_bench.py::test_the_baseline_is_current`. Nothing enforced that. So the next person to
    meet an action-timeout flake could add the variable to `ci.yml`, watch a corpus-provenance test go
    red ~14 minutes into a shard, and have no way to connect the two — the register's reasoning lived
    in a document nobody reads at that moment.

    This turns that into a one-second failure that explains itself, in the fast tier. It is deliberately
    keyed on "a baseline pins this field", not on the variable's name: `ULTRACUA_NAV_TIMEOUT_MS` is
    unpinned and stays perfectly usable, and if the bench's provenance set ever changes, this follows it.
    """
    pinned = _fields_pinned_by_a_baseline()
    for field, env in _pinned_overrides(_ci_yaml_without_comments()):
        raise AssertionError(
            f"{_CI} sets `{env}`, but `baselines/{pinned[field]}` pins `{field}` and "
            f"`tests/test_drift_bench.py::test_the_baseline_is_current` exact-compares it against the "
            f"AMBIENT value the bench records (`benchmarks/drift_bench.py:622`). This reddens the bench "
            f"in a test about corpus provenance, minutes into a shard, for a reason nothing at the "
            f"failure site would explain.\n"
            f"    This exact mitigation was built and refused once already — R4.24 in "
            f"docs/open-defects.md. If it is genuinely wanted now, re-record `baselines/"
            f"{pinned[field]}` in the same commit so the pin and the environment agree."
        )


def test_the_derived_facts_are_not_vacuous() -> None:
    """Anti-vacuity for the two derivations the messages above are built from.

    A derivation that quietly finds nothing turns every message into the cheerful branch — "`X` is
    available and nothing sets it" — which is precisely the false reassurance this slice removed. Both
    halves are asserted against what is true TODAY, so a change to either has to be looked at.
    """
    ci = _ci_yaml_without_comments()
    assert "playwright install chromium" in ci, (
        "the comment-stripped ci.yml no longer contains a line it certainly has; the stripper is eating "
        "content, so every containment check built on it is unreliable")
    assert len(ci.splitlines()) > 60, "ci.yml parsed to too few non-comment lines to be believable"

    pinned = _fields_pinned_by_a_baseline()
    assert pinned.get("action_timeout_ms"), (
        "no baseline pins `action_timeout_ms` any more. If that is deliberate, R4.24's refusal no "
        "longer applies and `ULTRACUA_ACTION_TIMEOUT_MS` becomes a legitimate CI knob — update the "
        "module docstring, which currently says otherwise.")
    assert "nav_timeout_ms" not in pinned, (
        "a baseline has started pinning `nav_timeout_ms`. That closes the second escape hatch too, and "
        "the docstring's claim that the two variables are NOT symmetrical is now wrong.")


def test_a_comment_about_the_variable_is_not_mistaken_for_setting_it() -> None:
    """ARM THE FALSE-POSITIVE DIRECTION, which is the likely one here.

    `ci.yml` is roughly a third comments, and the single most likely edit after this slice is a comment
    explaining why `ULTRACUA_ACTION_TIMEOUT_MS` is deliberately absent. A containment check that read
    that comment as the setting would fail the guard above for documenting it — refusing a workflow for
    describing the thing it forbids. Pin the quiet direction as hard as the loud one.
    """
    real = _CI.read_text(encoding="utf-8")
    env = _BOUNDS["action_timeout_ms"][0]
    assert env not in _ci_yaml_without_comments(), "precondition: today the workflow does not set it"

    commented = real.replace(
        "jobs:", f"# we deliberately do NOT set {env} here -- see R4.24\njobs:", 1)
    assert commented != real, "the mutation is STALE; `jobs:` is no longer in ci.yml"
    stripped = "\n".join(
        ln for ln in commented.splitlines() if not ln.lstrip().startswith("#"))
    assert not _pinned_overrides(stripped), (
        f"a COMMENT mentioning {env} survived comment-stripping, so documenting the refusal would trip "
        f"the guard that enforces it")


def test_the_pinned_override_guard_goes_red_when_armed() -> None:
    """...and the LOUD direction, which nothing else in this file drives.

    `test_ci_does_not_override_a_timeout_that_a_baseline_pins` asserts an absence. An absence passes just
    as happily when the predicate is broken as when the workflow is clean, and this file has already
    shipped one guard that was green while asserting something false. So make the violation and require
    it to be seen — standing, on every fast-tier run, not once before a PR.
    """
    real = _CI.read_text(encoding="utf-8")
    env, expected_field = _BOUNDS["action_timeout_ms"][0], "action_timeout_ms"

    armed = real.replace('      UV_PYTHON: "3.12"', f'      UV_PYTHON: "3.12"\n      {env}: "15000"', 1)
    assert armed != real, (
        "the arming mutation is STALE -- no `UV_PYTHON` env block was found in ci.yml, so this cell is "
        "no longer proving anything. Re-point it at however the workflow now sets environment variables.")

    caught = _pinned_overrides("\n".join(
        ln for ln in armed.splitlines() if not ln.lstrip().startswith("#")))
    assert caught == [(expected_field, env)], (
        f"adding `{env}` to ci.yml was not caught; got {caught}. The guard reads as green whether or "
        f"not the workflow is clean, which is worse than not having it.")
