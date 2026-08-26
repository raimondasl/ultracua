"""The substrate preflight: it must tear down, and it must say WHY it could not start. (2.4 / B5.)

WHAT THE PREFLIGHT IS FOR. The customer benchmark spends real money, and every dollar is wasted if
the substrate was never serving properly — a failure mode that is quiet by nature here (R4.89 is a
database manager served with HTTP 200 at every URL readiness looked at, R4.98 a login that reported
success having authenticated nobody). So `benchmarks/substrate_check.py` runs the container layer
first, for free, on every trigger.

THE SAME DOCKER GUARD AS `tests/test_substrates.py`, and for the same measured reason: this host has
a daemon and CI does not, so a cell that reaches Docker passes here and fails both CI arms. It is
made to RAISE rather than remembered, on `subprocess.run` rather than on `_compose`.

WHAT THESE CELLS ARE ACTUALLY ABOUT is the two properties a preflight cannot get wrong without
costing more than it saves:

  * IT TEARS DOWN EVEN WHEN THE SUBSTRATE FAILS. A leaked container on a runner is a job that
    reports one failure and then poisons whatever runs next on the same machine — and the failing
    path is exactly the one nobody exercises by hand;
  * A MISSING DAEMON IS REPORTED AS A MISSING DAEMON. "No Docker here" and "Docker is here and the
    substrate is broken" want completely different next actions, and this module exists partly to
    settle which of those CI actually is — a question two other modules in this package currently
    answer by assertion rather than by measurement.
"""

from __future__ import annotations

import subprocess

import pytest

from benchmarks import substrate_check as SC


@pytest.fixture(autouse=True)
def _docker_is_not_available_to_unit_tests(monkeypatch):
    """No cell here may shell out. See `tests/test_substrates.py` for the measurement behind this."""
    def refuse(*a, **kw):
        raise AssertionError(
            "a cell in test_substrate_check.py reached `subprocess.run` for real. This host has a "
            "Docker daemon and CI does not, so such a cell passes here and fails both CI arms. "
            "Patch what you need in the cell body."
        )
    monkeypatch.setattr(subprocess, "run", refuse)


class _FakeSub:
    """A substrate that records the lifecycle calls made against it."""

    def __init__(self, fail_on: str = "") -> None:
        self.calls: list = []
        self.fail_on = fail_on

    def _step(self, name: str):
        self.calls.append(name)
        if self.fail_on == name:
            raise RuntimeError(f"scripted failure in {name}")

    def up(self) -> float:
        self._step("up")
        return 1.0

    def assert_writable(self) -> None:
        self._step("assert_writable")

    def seed(self) -> None:
        self._step("seed")

    def down(self, wipe: bool = False) -> None:
        self.calls.append(f"down(wipe={wipe})")


def _install(monkeypatch, sub: _FakeSub) -> None:
    monkeypatch.setitem(SC.SUBSTRATES, "gitea", lambda: sub)
    monkeypatch.setattr(SC.S, "substrate_report", lambda: {"images": {"gitea": "gitea/gitea:1.22"}})


def test_the_happy_path_brings_up_seeds_and_tears_down(monkeypatch) -> None:
    """Anti-vacuity for everything below, and it pins the ORDER: seed after readiness, not before."""
    sub = _FakeSub()
    _install(monkeypatch, sub)
    rep = SC.check("gitea")
    assert rep["ok"] is True
    assert sub.calls == ["up", "assert_writable", "seed", "assert_writable", "down(wipe=True)"]
    assert rep["up_s"] >= 0 and "seed_s" in rep


@pytest.mark.parametrize("fail_on", ["up", "assert_writable", "seed"])
def test_it_tears_down_however_it_fails(monkeypatch, fail_on: str) -> None:
    """THE PROPERTY THIS FILE EXISTS FOR, over every stage that can raise.

    A leaked container on a runner is worse than the failure that leaked it: the job reports one
    thing and then poisons whatever runs next on the same machine. Parameterised rather than written
    once for `up`, because the stages fail for different reasons and a `finally` that covered only
    the first would pass a single-case cell.
    """
    sub = _FakeSub(fail_on=fail_on)
    _install(monkeypatch, sub)
    with pytest.raises(RuntimeError, match=fail_on):
        SC.check("gitea")
    assert sub.calls[-1] == "down(wipe=True)", (
        f"failing in {fail_on!r} left the substrate UP: {sub.calls}"
    )


def test_keep_leaves_it_running(monkeypatch) -> None:
    """The other direction, asserted because `--keep` exists to hand a live substrate to a follow-on
    run — and a teardown that ignored the flag would silently make the paid job start from cold."""
    sub = _FakeSub()
    _install(monkeypatch, sub)
    SC.check("gitea", keep=True)
    assert not [c for c in sub.calls if c.startswith("down")], sub.calls


def test_no_seed_skips_the_fixture_load(monkeypatch) -> None:
    sub = _FakeSub()
    _install(monkeypatch, sub)
    rep = SC.check("gitea", seed=False)
    assert "seed" not in sub.calls and "seed_s" not in rep


def test_a_missing_daemon_is_reported_as_a_missing_daemon(monkeypatch, capsys) -> None:
    """It must EXIT rather than fall through to a substrate error that blames the benchmark.

    The message is the deliverable here, not the exit code: this module is what settles whether CI
    has a daemon at all, and a run that dies with `NOT WRITABLE` is precisely the ambiguous evidence
    (a container that is not RUNNING) that two other modules in this package currently read as a
    statement about the daemon.
    """
    monkeypatch.setattr(SC.shutil, "which", lambda _n: None)
    rc = SC.main(["--substrate", "gitea"])
    assert rc == 2
    err = capsys.readouterr().err
    assert "NO DOCKER DAEMON" in err and "self-hosted runner" in err


def test_the_probe_never_lets_a_broken_docker_raise(monkeypatch) -> None:
    """`docker_report` is a PROBE and must survive the thing it is probing being broken.

    A report that raises turns "I could not find out" into a crash with a traceback, which is the
    least useful of the three possible outcomes and the one that hides the other two.
    """
    monkeypatch.setattr(SC.shutil, "which", lambda _n: "/usr/bin/docker")
    monkeypatch.setattr(SC.subprocess, "run",
                        lambda *a, **kw: (_ for _ in ()).throw(OSError("no such file")))
    rep = SC.docker_report()
    assert rep["docker_on_path"] is True and rep["version"] is None
    assert "OSError" in rep["version_error"]


def test_every_substrate_the_corpus_knows_is_checkable() -> None:
    """DERIVED, so a third substrate cannot be added to the corpus and silently skip its preflight."""
    from benchmarks import corpus
    assert set(SC.SUBSTRATES) == set(corpus.CORPORA), (
        f"the preflight knows {sorted(SC.SUBSTRATES)} and the corpus has {sorted(corpus.CORPORA)}. "
        f"A substrate with no preflight is one whose containers are first exercised by the job that "
        f"is already spending money."
    )
