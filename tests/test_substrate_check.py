"""The substrate preflight: it must tear down, and it must say WHY it could not start. (2.4 / B5.)

WHAT THE PREFLIGHT IS FOR. The customer benchmark spends real money, and every dollar is wasted if
the substrate was never serving properly — a failure mode that is quiet by nature here (R4.89 is a
database manager served with HTTP 200 at every URL readiness looked at, R4.98 a login that reported
success having authenticated nobody). So `benchmarks/substrate_check.py` runs the container layer
first, for free, on every trigger.

THE SAME DOCKER GUARD AS `tests/test_substrates.py`, and it is worth saying the reason correctly
because this file got it wrong first. CI HAS a daemon (measured: Docker 28.0.4, R4.109). The
asymmetry is one level in: this host runs a LIVE, SEEDED substrate between sessions and a CI job
starts with nothing running, so a cell that reaches Docker passes here against a real Gitea and
fails both CI arms. Made to RAISE rather than remembered, on `subprocess.run` rather than `_compose`.

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
            "a cell in test_substrate_check.py reached `subprocess.run` for real. This host runs a "
            "live, seeded substrate and a CI job starts with nothing running, so such a cell passes "
            "here against a real Gitea and fails both CI arms. Patch what you need in the cell body."
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

    def snapshot(self) -> None:
        self._step("snapshot")

    def down(self, wipe: bool = False) -> None:
        self.calls.append(f"down(wipe={wipe})")


def _install(monkeypatch, sub: _FakeSub) -> None:
    monkeypatch.setitem(SC.SUBSTRATES, "gitea", lambda: sub)
    monkeypatch.setattr(SC.S, "substrate_report", lambda: {"images": {"gitea": "gitea/gitea:1.22"}})


def test_the_happy_path_brings_up_seeds_snapshots_and_tears_down(monkeypatch) -> None:
    """Anti-vacuity for everything below, and it pins the ORDER: seed after readiness, not before.

    THE `snapshot` IN THIS LIST IS THE WHOLE OF R4.110. `score_one` calls `substrate.reset()` before
    every scenario and `reset()` restores from a snapshot that `seed()` does not take — so a
    substrate this module provisioned without one is unusable by the only consumer there is.
    Measured on 2.4a's first real weekly run: all seven Gitea scenarios raised `no seed at
    /data/gitea/seed.db`, every one attributed to the harness, and the record refused rather than
    published as 0.0.
    """
    sub = _FakeSub()
    _install(monkeypatch, sub)
    rep = SC.check("gitea")
    assert rep["ok"] is True
    assert sub.calls == ["up", "assert_writable", "seed", "snapshot", "assert_writable",
                         "down(wipe=True)"]
    assert rep["up_s"] >= 0 and "seed_s" in rep


def test_seeding_always_snapshots(monkeypatch) -> None:
    """Stated on its own, because the cell above would still pass if `snapshot` moved BEFORE `seed`.

    Order matters and is not decoration: a snapshot taken before the fixtures exist freezes an empty
    world, and every scenario would then reset into a substrate with no data — which scores as the
    agent failing to find things that were never there. That is the silent-wrong direction.
    """
    sub = _FakeSub()
    _install(monkeypatch, sub)
    SC.check("gitea")
    assert "snapshot" in sub.calls, "a seeded substrate with no snapshot cannot be reset"
    assert sub.calls.index("seed") < sub.calls.index("snapshot"), (
        f"the snapshot was taken BEFORE the seed, freezing an empty world: {sub.calls}"
    )


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


# ---------------------------------------------------------------------------------------------------
# THE WORKFLOW PROPERTY (R4.110). A job that runs a SCORED corpus must provision its substrate in
# THAT job. `needs:` is ordering, not a shared machine -- every GitHub job gets a fresh runner -- and
# `score_one` calls `reset()`/`await_ready()` but never `up()`, because on a developer host the
# substrate is already running between sessions.
#
# This is the third local-vs-CI axis (R4.109) arriving one layer down from where 0.137.0 had just
# corrected it, in the same slice, in a job I wrote after writing the correction. A reviewer reading
# `needs: substrates` sees a dependency and reads it as provisioning; nothing in the file said
# otherwise and no test could fail for it.

from pathlib import Path                                            # noqa: E402

from test_ci_provisioning import parse_steps, workflows             # noqa: E402

ROOT = Path(__file__).resolve().parents[1]


def _all_steps() -> list:
    out = []
    for wf in workflows():
        out += parse_steps(wf.read_text(encoding="utf-8"), file=wf.name)
    return out


def _jobs_running(token: str, steps=None) -> "set[tuple[str, str]]":
    """`{(workflow file, job)}` whose steps run `token`. Over `run` payloads, never raw text.

    Takes the step list so the arming cell can drive it against a MUTATED workflow -- a scan that
    can only read the real file is a scan nobody has watched go red.
    """
    return {(s.file, s.job) for s in (steps if steps is not None else _all_steps())
            if token in s.run}


def test_every_job_that_pays_for_a_corpus_provisions_its_own_substrate(steps=None) -> None:
    """THE defect R4.110 was, stated as a property over the workflow files.

    Both halves are derived: which jobs run a scored corpus, and which jobs provision. A job that
    does the first without the second gets seven harness rows and a refused record -- loud, and
    entirely avoidable at review time.
    """
    steps = _all_steps() if steps is None else steps
    paying = _jobs_running("benchmarks.corpus_run", steps)
    assert paying, (
        "no workflow job runs `benchmarks.corpus_run` -- either the weekly benchmark was removed "
        "or this scan has gone stale, and in both cases the property below is vacuous"
    )
    provisioning = _jobs_running("benchmarks.substrate_check", steps)
    unprovisioned = sorted(paying - provisioning)
    assert not unprovisioned, (
        f"these jobs run a SCORED corpus without provisioning a substrate in the same job: "
        f"{unprovisioned}. `needs:` is ordering, not a shared machine -- each job gets a fresh "
        f"runner -- and `score_one` never calls `up()`. Measured cost of getting this wrong: seven "
        f"scenarios raising `no seed at /data/gitea/seed.db` on 2.4a's first real weekly run "
        f"(R4.110). Add a `substrate_check --keep` step BEFORE the corpus step."
    )


def test_the_provisioning_step_keeps_the_substrate_running(steps=None) -> None:
    """`--keep` is the whole point of provisioning rather than preflighting.

    Without it `check()` tears the substrate down in its `finally`, and the corpus step that follows
    finds nothing running -- the same failure as having no step at all, arrived at from the other
    side, and the one a reader of the workflow would least expect.
    """
    steps = _all_steps() if steps is None else steps
    for s in steps:
        if "benchmarks.substrate_check" not in s.run:
            continue
        pays = any("benchmarks.corpus_run" in o.run
                   for o in steps if o.job == s.job and o.file == s.file)
        if pays:
            assert "--keep" in s.run, (
                f"{s.where} provisions for a paid corpus run in job {s.job!r} without `--keep`, "
                f"so `check()` tears the substrate down before the corpus step reaches it"
            )


def test_the_workflow_cells_go_red_when_armed() -> None:
    """Both cells assert a NEGATIVE about a workflow, and both are new. Arm them.

    Mutations are applied to the WEEKLY file's text and re-parsed, rather than by patching a `Path`
    -- `Path.read_text` is read-only on a `WindowsPath` and a draft that patched it died on the
    restore in its own `finally`, on the platform this repo is developed on.
    """
    from _arming import assert_red

    weekly = next(w for w in workflows() if w.name == "mutation-sweep.yml")
    text = weekly.read_text(encoding="utf-8")

    def parsed(mutated: str) -> list:
        other = [s for w in workflows() if w.name != weekly.name
                 for s in parse_steps(w.read_text(encoding="utf-8"), file=w.name)]
        return other + parse_steps(mutated, file=weekly.name)

    # (a) REMOVE THE PROVISIONING STEP -- the exact shape of R4.110, which shipped and cost a paid
    # run. The step is deleted by name so the mutation names one site.
    marker = "      - name: Provision Gitea for the scored run\n"
    assert marker in text, "the mutation is STALE; the provisioning step was renamed"
    start = text.index(marker)
    end = text.index("      - name: Run the Gitea corpus and gate it", start)
    stripped = text[:start] + text[end:]
    assert_red(test_every_job_that_pays_for_a_corpus_provisions_its_own_substrate, parsed(stripped))

    # (b) DROP `--keep` -- the same failure reached from the other side, and the one a reader would
    # least expect: the step is there, it runs, and it tears the substrate down before the corpus.
    dropped = text.replace("--substrate gitea --keep", "--substrate gitea", 1)
    assert dropped != text, "the mutation is STALE; the provisioning command changed shape"
    assert_red(test_the_provisioning_step_keeps_the_substrate_running, parsed(dropped))
