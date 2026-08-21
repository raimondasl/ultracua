"""The substrate harness's key-less half: readiness refuses, the skeleton guard fires, pins are pins.

Docker is not available in this suite (B5 adds a nightly ubuntu job for that), so what is asserted
here is everything that decides whether a scored number means anything, minus the containers:

  * R4.40's guard — a near-empty first observation is a HARNESS error, never a scored discovery
    failure. This is the cell that stops the bench grading its own impatience as a product defect.
  * readiness REFUSES on timeout rather than returning, because "not ready yet" that reads as "ready"
    is the same defect one layer up.
  * the substrate report carries what a baseline needs to know about the world it measured.

The Docker paths were driven by hand against a live Gitea while authoring: `snapshot()` 15.3 s,
`reset()` 14.2 s, and the instance still served 13,591 bytes afterwards. The Odoo path was NOT — the
authoring host had 308 MB free and ~50k pages/sec, where a bring-up measures nothing (`CLAUDE.md`: a
loaded host cannot adjudicate). That is stated in `Odoo.reset`'s own docstring rather than left for a
reader to discover.
"""

from __future__ import annotations

import pytest

from benchmarks import substrates as S


class _Obs:
    """Duck-typed stand-in: the bench holds no import of the agent's Observation type, deliberately."""

    def __init__(self, n):
        self.elements = list(range(n))


# ---------------------------------------------------------------------------------------------------
# 1. R4.40's guard — the one thing a scored run must call.

def test_a_skeleton_first_observation_is_a_harness_error_not_a_score() -> None:
    """The silent-wrong direction this whole module exists to block.

    R4.40: `_learn` snapshots straight after navigate with no settle floor, so a client-rendered page
    gets authored against its unrendered skeleton. If that reaches the bench, a scored zero reads as
    "the agent could not find the button" when the truth is "there was nothing on the page yet". The
    bench would be grading its own impatience as a product defect — and in the direction that makes
    the subject look worse, which is the direction nobody double-checks.
    """
    with pytest.raises(S.SubstrateNotReady) as exc:
        S.assert_not_a_skeleton(_Obs(0), substrate="odoo", scenario="overdue-invoice")
    msg = str(exc.value)
    assert "ABANDONING rather than scoring" in msg
    assert "odoo" in msg and "overdue-invoice" in msg, "the refusal must say WHICH run it abandoned"
    assert "R4.40" in msg, "and point at the finding, or the next reader re-derives it"

    # ...and the other direction, or "always refuse" satisfies the assertion above. A real page must
    # pass, or the guard would abandon every scenario and the bench would score nothing at all.
    S.assert_not_a_skeleton(_Obs(40), substrate="gitea", scenario="open-issue")


def test_the_skeleton_floor_is_a_floor_and_not_a_quality_bar() -> None:
    """The boundary, exactly, because a floor that drifts upward silently starts rejecting real pages.

    Set far below any served page (a Gitea landing page is ~13.5 kB with dozens of elements) so it can
    only fire on the failure it names. If someone raises it to "improve" detection, this goes red and
    they have to argue for it.
    """
    assert S.SKELETON_ELEMENT_FLOOR == 3, "the floor moved; re-argue it against a real page's size"
    with pytest.raises(S.SubstrateNotReady):
        S.assert_not_a_skeleton(_Obs(S.SKELETON_ELEMENT_FLOOR - 1), substrate="x", scenario="y")
    S.assert_not_a_skeleton(_Obs(S.SKELETON_ELEMENT_FLOOR), substrate="x", scenario="y")


def test_the_guard_survives_an_observation_with_no_elements_attribute() -> None:
    """Duck-typed on purpose, so it must not explode on something that is not an Observation —
    it must REFUSE, which is the safe direction for an object it cannot read."""
    class _Foreign:
        pass

    with pytest.raises(S.SubstrateNotReady):
        S.assert_not_a_skeleton(_Foreign(), substrate="x", scenario="y")
    with pytest.raises(S.SubstrateNotReady):
        S.assert_not_a_skeleton(_Obs(0), substrate="x", scenario="y")


# ---------------------------------------------------------------------------------------------------
# 2. Readiness refuses rather than returning.

def test_readiness_refuses_on_timeout_and_says_what_it_was_waiting_for(monkeypatch) -> None:
    """"Not ready" that returns as "ready" is R4.40 one layer up — so the timeout must RAISE.

    The message has to name the last thing it observed, because the two causes need different fixes:
    an unhealthy container is a substrate problem, and a healthy container serving nothing is an app
    problem.
    """
    monkeypatch.setattr(S, "_container_health", lambda name: "starting")
    monkeypatch.setattr(S, "_http_ok", lambda url, min_bytes: False)
    g = S.Gitea()
    with pytest.raises(S.SubstrateNotReady) as exc:
        g.await_ready(timeout_s=0.1, poll_s=0.01)
    assert "not healthy" in str(exc.value)
    assert "R4.40" in str(exc.value)

    # A healthy container that serves nothing must report the OTHER cause, not the same one.
    monkeypatch.setattr(S, "_container_health", lambda name: "healthy")
    with pytest.raises(S.SubstrateNotReady) as exc2:
        g.await_ready(timeout_s=0.1, poll_s=0.01)
    assert "no substantive response" in str(exc2.value)

    # ...and it returns when both hold, or the cell above is satisfied by a function that always raises.
    monkeypatch.setattr(S, "_http_ok", lambda url, min_bytes: True)
    g.await_ready(timeout_s=1, poll_s=0.01)


def test_a_container_with_no_healthcheck_does_not_block_readiness(monkeypatch) -> None:
    """`none` means the container declares no healthcheck, which is not the same as unhealthy.

    Treating it as a failure would make readiness depend on whether someone wrote a HEALTHCHECK, and
    the HTTP probe is the real evidence anyway.
    """
    monkeypatch.setattr(S, "_container_health", lambda name: "none")
    monkeypatch.setattr(S, "_http_ok", lambda url, min_bytes: True)
    S.Gitea().await_ready(timeout_s=1, poll_s=0.01)


# ---------------------------------------------------------------------------------------------------
# 3. The world a baseline was measured in, recorded rather than assumed.

def test_the_report_records_the_pinned_images_and_the_clock() -> None:
    """A baseline that does not know its own conditions cannot be compared with a later one.

    The images are read FROM the compose file rather than listed here — two copies of a pin is how a
    pin drifts — and the clock is recorded because Odoo's demo data is seeded relative to "today", so
    a scenario about "the overdue invoice" means something different in June.
    """
    r = S.substrate_report()
    assert r["faketime_epoch"] == S.FAKETIME_EPOCH
    assert "/" in r["compose"] and "\\" not in r["compose"], (
        "the compose path is not POSIX, so a baseline recorded on Windows differs from the nightly "
        "ubuntu one in a field that describes nothing about the measurement")
    for image in r["images"].values():
        assert ":" in image and not image.endswith(":latest"), (
            f"{image} is unpinned; a floating tag re-bases the substrate under the baseline and "
            f"`baselines/README.md`'s rule is that a rate measured under one configuration is never "
            f"compared against another")
    assert {"gitea", "odoo", "postgres"} <= set(r["images"]), r["images"]


def test_every_service_in_the_compose_file_declares_a_healthcheck() -> None:
    """Readiness is layered, and the container's own check is the bottom layer.

    A service without one degrades `await_ready` to the HTTP probe alone — which is exactly the
    single-signal readiness R4.40 is about. Derived from the file so a service added tomorrow is
    covered.
    """
    # SECTION-AWARE, and the first draft was not — which this file's own anti-vacuity assert caught.
    # `odoo-db` is BOTH a service and a volume, so a scan that keyed on "2-space line ending in a
    # colon" overwrote the service's body with the volume's empty one and reported 2 services of 3.
    # A hand parser that silently loses an entry is the shape `tests/test_ci_provisioning.py` is
    # written around; here the fix is to know which top-level section we are in.
    text = (S.COMPOSE).read_text(encoding="utf-8")
    services, current, in_services = {}, None, False
    for line in text.splitlines():
        if line and not line[0].isspace() and line.rstrip().endswith(":"):
            in_services = line.rstrip().rstrip(":") == "services"
            current = None
            continue
        if not in_services:
            continue
        if line.startswith("  ") and not line.startswith("    ") and line.strip().endswith(":"):
            current = line.strip().rstrip(":")
            services[current] = []
        elif current and line.startswith("    "):
            services[current].append(line)

    real = {n: b for n, b in services.items() if any("image:" in ln for ln in b)}
    declared = text.count("\n    image:")
    assert len(real) == declared >= 3, (
        f"parsed {len(real)} service(s) with an image but the file declares {declared} `image:` "
        f"lines — the parser is losing one, which is how this cell would pass while covering less")
    for name, body in real.items():
        assert any("healthcheck:" in ln for ln in body), (
            f"service {name!r} declares no healthcheck, so readiness for it rests on one signal")


# ---------------------------------------------------------------------------------------------------
# 4. RESET, which the module docstring names FIRST as deciding whether a scored number means anything
#    — and which had zero coverage until the audit said so. Docker is faked; what is pinned is the
#    ARGV and the ORDER, which is where all three of its defects lived.

class _Compose:
    """Records every `docker compose` argv, and can be told to fail on one of them."""

    def __init__(self, fail_on=None, stdout=""):
        self.calls, self.fail_on, self.stdout = [], fail_on, stdout

    def __call__(self, *args, check=True, timeout=300):
        self.calls.append(args)
        if self.fail_on and self.fail_on in " ".join(args):
            raise S.SubstrateError(f"boom on {self.fail_on}")
        import types
        return types.SimpleNamespace(stdout=self.stdout, stderr="", returncode=0)

    def joined(self):
        return [" ".join(a) for a in self.calls]


@pytest.fixture()
def compose(monkeypatch):
    c = _Compose(stdout="yes")
    monkeypatch.setattr(S, "_compose", c)
    monkeypatch.setattr(S.Substrate, "await_ready", lambda self, **kw: None)
    return c


def test_gitea_reset_removes_the_wal_before_restoring_the_seed(compose) -> None:
    """THE SILENT-WRONG ONE. `docker compose stop` SIGKILLs after the grace period, so a dirty run
    leaves `gitea.db-wal` — and SQLite REPLAYS it over the restored seed on next open. The reset
    would appear to work while the world carried the previous scenario's writes.

    The class docstring reasoned about the WAL for the COPY direction and never for the RESTORE
    direction: the "a guard that exists on a sibling path was never applied to the mechanism" shape
    that CLAUDE.md says predicts the next bug.
    """
    S.Gitea().reset()
    restore = [c for c in compose.joined() if "cp " in c and "seed.db" in c]
    assert restore, f"nothing restored the seed; calls were {compose.joined()}"
    assert "-wal" in restore[0] and "-shm" in restore[0], (
        f"the restore does not remove the WAL: {restore[0]!r} — SQLite will replay the dirty run's "
        f"writes over the seed and the reset will look like it worked")
    assert restore[0].index("rm -f") < restore[0].index("cp "), "the WAL must go BEFORE the copy"


def test_gitea_reset_names_its_precondition_instead_of_failing_blankly(monkeypatch) -> None:
    """It used to encode the precondition as `sh -c 'test -f seed.db && cp ...'` under `check=True`,
    so a never-snapshotted instance raised `SubstrateError: ... failed (1)` with EMPTY stderr. Its
    Odoo sibling had a named precondition saying exactly what to do."""
    c = _Compose(stdout="no")                 # the probe reports: no seed
    monkeypatch.setattr(S, "_compose", c)
    monkeypatch.setattr(S.Substrate, "await_ready", lambda self, **kw: None)
    with pytest.raises(S.SubstrateError, match="no seed"):
        S.Gitea().reset()


def test_a_reset_that_fails_midway_still_restarts_the_service(monkeypatch) -> None:
    """Both resets STOP first, so a raise in between left the service DOWN — and every later scenario
    then reported a harness error from a second, unrelated cause. The `finally` restart is what stops
    one failure from poisoning the rest of the batch."""
    # GITEA: fail on the copy, which happens after the stop.
    c = _Compose(fail_on="cp ", stdout="yes")
    monkeypatch.setattr(S, "_compose", c)
    monkeypatch.setattr(S.Substrate, "await_ready", lambda self, **kw: None)
    with pytest.raises(S.SubstrateError):
        S.Gitea().reset()
    assert any("start" in a for a in c.calls), (
        f"gitea was left stopped after a failed reset; calls were {c.joined()}")

    # ODOO: the failure must land after the stop here too. Its PRECONDITION is checked BEFORE
    # stopping, deliberately — fail early rather than disturb a service that is serving — so a
    # failure there needs no restart and would prove nothing. Let the precondition pass, break the
    # DROP. (The first draft of this cell failed on the precondition and asserted a restart that
    # correctly never happened.)
    c2 = _Compose(stdout="")
    seen = {"n": 0}

    def _psql(self, sql):
        seen["n"] += 1
        if seen["n"] == 1:
            return "bench_seed"                    # the precondition passes
        raise S.SubstrateError("boom on DROP")     # ...and the work fails, after the stop

    monkeypatch.setattr(S, "_compose", c2)
    monkeypatch.setattr(S.Odoo, "_psql", _psql)
    with pytest.raises(S.SubstrateError):
        S.Odoo().reset()
    assert any("stop" in a for a in c2.calls), "the odoo case never reached the stop; it proves nothing"
    assert any("start" in a for a in c2.calls), (
        f"odoo was left stopped after a failed reset; calls were {c2.joined()}")


def test_the_two_substrates_reset_by_DIFFERENT_mechanisms(compose, monkeypatch) -> None:
    """Deliberate, and stated in the module docstring: a shared reset mechanism would hide a reset bug
    inside the very contrast the benchmark exists to show."""
    S.Gitea().reset()
    gitea_calls = compose.joined()

    c2 = _Compose(stdout="bench_seed")
    psql_seen = []
    monkeypatch.setattr(S, "_compose", c2)
    monkeypatch.setattr(S.Odoo, "_psql", lambda self, sql: (psql_seen.append(sql), "bench_seed")[1])
    S.Odoo().reset()

    # About the DATABASE mechanism specifically. Odoo copies files too — its filestore — so a bare
    # "does it use cp" check asserts something FALSE, which is exactly what the first draft of this
    # cell did and why it failed. The distinction that matters is how each restores its DATA.
    assert any(".db" in c and "cp " in c for c in gitea_calls), (
        "gitea no longer resets its database by file copy")
    assert not any(".db" in c and "cp " in c for c in c2.joined()), (
        "odoo now resets its DATABASE by file copy too — the two mechanisms have converged, and a "
        "bug in the shared one becomes invisible in the very contrast the benchmark exists to show")
    assert any("CREATE DATABASE" in s for s in psql_seen), (
        "odoo no longer resets by SQL template, so the two substrates no longer differ in mechanism")


def test_a_missing_docker_binary_is_a_substrate_error_not_a_raw_oserror(monkeypatch) -> None:
    """`FileNotFoundError` and `TimeoutExpired` are the two failures a Docker bench actually hits, and
    neither was a `SubstrateError` — so both escaped the path that exists to record a harness fault as
    a harness fault rather than aborting the run."""
    import subprocess

    def boom(*a, **kw):
        raise FileNotFoundError(2, "No such file or directory", "docker")
    monkeypatch.setattr(subprocess, "run", boom)
    with pytest.raises(S.SubstrateError, match="not on PATH"):
        S._compose("ps")

    def slow(*a, **kw):
        raise subprocess.TimeoutExpired(cmd="docker", timeout=300)
    monkeypatch.setattr(subprocess, "run", slow)
    with pytest.raises(S.SubstrateError, match="did not return within"):
        S._compose("up", "-d")
