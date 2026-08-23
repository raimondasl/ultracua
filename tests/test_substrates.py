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


@pytest.fixture(autouse=True)
def _docker_is_not_available_to_unit_tests(monkeypatch, request):
    """No test in this file may shell out to Docker, and this is what makes that TRUE rather than
    intended.

    A THIRD AXIS OF "a local green is weaker evidence than CI", measured at 0.121.0 and now the one
    that has actually shipped a red PR here. CLAUDE.md records two axes — platform (CI runs ubuntu
    AND windows) and keys (`.env` makes a provider reachable locally). This is the third: **the
    developer machine has Docker running and CI does not.**

    R4.85 added writability as a fourth readiness layer, and that layer execs into a container. Two
    pre-existing cells drive `await_ready()` with only layers 1-2 mocked, so they began reaching
    Docker for real — and passed locally against a live, healthy Gitea while failing on both CI
    arms with a baffling `NOT WRITABLE`. The suite was green on this host for the wrong reason.

    So the leak is closed the way the fast tier closes Chromium: not by remembering to patch, but by
    making the call RAISE and name its remedy. A test that needs a mocked `docker compose` asks for
    the `compose` fixture (which installs its own recorder) or patches `_compose` in its own body.

    The guard sits on `subprocess.run` rather than on `_compose`, and that placement is the second
    thing this fixture got wrong before it was right. Guarding `_compose` also blocked
    `test_a_missing_docker_binary_is_a_substrate_error_not_a_raw_oserror`, which patches
    `subprocess.run` itself and calls the REAL `_compose` to check its error wrapping — it never
    reaches Docker at all. A guard whose false positives block correct tests is the same
    over-refusal shape as the `su` probe above; the precise invariant is "no test runs the real
    `docker` binary", so that is what is asserted. A test that patches `subprocess.run` or
    `_compose` in its own body wins, because its patch is applied after this one.
    """
    def _refuse(*args, **kwargs):
        argv = args[0] if args else kwargs.get("args", [])
        raise AssertionError(
            f"{request.node.name} tried to run {' '.join(map(str, argv))[:80]!r} for real.\n"
            f"    Unit tests here must not touch Docker: it is present on a developer host and "
            f"ABSENT on CI, so a test that reaches it passes locally and fails both CI arms — "
            f"measured, R4.85's readiness layer did exactly that.\n"
            f"    Use the `compose` fixture, patch `S._compose` or `subprocess.run` in the test "
            f"body, or neutralise the probe that calls it (e.g. `assert_writable`) when this cell "
            f"is about a different layer."
        )

    monkeypatch.setattr(S.subprocess, "run", _refuse)


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
    # Layer 3 is somebody else's cell (below). Neutralised here because it EXECS INTO A CONTAINER,
    # and leaving it live made this test pass on a host with Docker up and fail both CI arms.
    monkeypatch.setattr(S.Gitea, "assert_writable", lambda self: None)
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
    monkeypatch.setattr(S.Gitea, "assert_writable", lambda self: None)   # layer 3 execs; see above
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


def _classes_defining(method: str) -> list:
    """Every substrate class with its OWN `method` in `__dict__` — base included.

    DERIVED, not listed. Patching `Substrate.await_ready` alone was correct until `Odoo` grew an
    override for the clock check, at which point the base patch stopped reaching Odoo and a reset
    test failed inside a readiness probe it never meant to run. That is CLAUDE.md's "a scan that
    names ONE function asserts a negative about a body that can walk away", wearing a monkeypatch.
    """
    out = []
    stack = [S.Substrate]
    while stack:
        cls = stack.pop()
        if method in vars(cls):
            out.append(cls)
        stack.extend(cls.__subclasses__())
    return out


@pytest.fixture()
def compose(monkeypatch):
    """No Docker, and therefore no probe that needs a real container to answer.

    `assert_clock_pinned` is neutralised alongside `await_ready` because `Odoo.seed()` calls it
    DIRECTLY rather than through readiness — deliberately, since seeding under an unpinned clock
    bakes the wrong dates in. Under the mock it would parse the canned `"yes"` as a date and refuse.
    Both lists are derived for the same reason: the first version of this fixture named
    `Substrate.await_ready` and stopped reaching Odoo the moment Odoo grew an override.
    """
    c = _Compose(stdout="yes")
    monkeypatch.setattr(S, "_compose", c)
    for name, stub in (("await_ready", lambda self, **kw: None),
                       ("assert_clock_pinned", lambda self: None)):
        for cls in _classes_defining(name):
            monkeypatch.setattr(cls, name, stub)
    return c


def test_the_fixture_neutralises_every_readiness_override_not_just_the_base() -> None:
    """Arms the fixture itself. A subclass that grows its own `await_ready` must be caught here
    rather than by a puzzling failure inside an unrelated reset test."""
    found = {c.__name__ for c in _classes_defining("await_ready")}
    assert "Substrate" in found, "the base defines await_ready; the derivation is broken"
    assert found >= {"Substrate", "Odoo"}, (
        f"a class overrides await_ready and the fixture would not neutralise it: {found}")


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


# --- R4.85: readiness is three READS, and a substrate that cannot be written is not ready --------
#
# Found by driving `Gitea.reset()` against a live container for the first time (0.121.0). The restore
# runs `cp` inside `run --rm --entrypoint sh`, which lands as ROOT in the gitea image, so the restored
# `gitea.db` came out `root:root` while the service runs as `git`. Every write then failed with
# SQLite's `attempt to write a readonly database` — and `await_ready()` passed throughout, because a
# `wget --spider` healthcheck and an HTTP GET are both READS.
#
# Reproduced from a virgin instance: `git:git` on boot, still `git:git` after `snapshot()` (which
# only creates `seed.db`), `root:root` after `reset()`. Every scenario runs after a reset.

def test_the_gitea_restore_chowns_the_database_to_whoever_owns_the_data_directory(compose) -> None:
    S.Gitea().reset()
    restore = [c for c in compose.joined() if "cp " in c and "seed.db" in c]
    assert restore, f"nothing restored the seed; calls were {compose.joined()}"
    cmd = restore[0]
    assert "chown" in cmd, (
        f"the restore does not chown the database: {cmd!r} — `cp` in this image runs as root, so the "
        f"service (which runs as `git`) gets a read-only database and every write fails")
    assert cmd.index("cp ") < cmd.index("chown"), "the chown must come AFTER the copy"
    assert "stat -c %u:%g" in cmd, (
        "the chown must derive the owner from the data directory rather than hardcoding `git`, so it "
        "stays correct if the image changes uid")
    assert "--reference" not in cmd, "busybox rejects GNU --reference; it was measured failing"


def test_readiness_refuses_a_substrate_whose_database_the_service_cannot_write(monkeypatch) -> None:
    """The layer that did not exist. A read-only database passes health AND HTTP."""
    g = S.Gitea()
    monkeypatch.setattr(S, "_container_health", lambda name: "healthy")
    monkeypatch.setattr(S, "_http_ok", lambda url, n: True)
    monkeypatch.setattr(S.Gitea, "assert_writable",
                        lambda self: (_ for _ in ()).throw(
                            S.SubstrateNotReady("gitea: /data/gitea/gitea.db is NOT WRITABLE by x")))
    with pytest.raises(S.SubstrateNotReady) as e:
        g.await_ready(timeout_s=5, poll_s=0.01)
    assert "NOT WRITABLE" in str(e.value)


def test_a_not_writable_verdict_raises_at_once_instead_of_burning_the_timeout(monkeypatch) -> None:
    """It never self-heals, so spinning would report `not ready in 300s` instead of naming it.
    ABSENT is the opposite case and must keep waiting — asserted by its sibling below."""
    calls = {"n": 0}

    def _boom(self):
        calls["n"] += 1
        raise S.SubstrateNotReady("gitea: /data/gitea/gitea.db is NOT WRITABLE by x")

    monkeypatch.setattr(S, "_container_health", lambda name: "healthy")
    monkeypatch.setattr(S, "_http_ok", lambda url, n: True)
    monkeypatch.setattr(S.Gitea, "assert_writable", _boom)
    with pytest.raises(S.SubstrateNotReady):
        S.Gitea().await_ready(timeout_s=30, poll_s=0.01)
    assert calls["n"] == 1, f"it retried a verdict that cannot change ({calls['n']} probes)"


def test_an_absent_database_keeps_waiting_because_that_one_can_resolve_itself(monkeypatch) -> None:
    calls = {"n": 0}

    def _absent(self):
        calls["n"] += 1
        raise S.SubstrateNotReady("gitea: /data/gitea/gitea.db is ABSENT")

    monkeypatch.setattr(S, "_container_health", lambda name: "healthy")
    monkeypatch.setattr(S, "_http_ok", lambda url, n: True)
    monkeypatch.setattr(S.Gitea, "assert_writable", _absent)
    with pytest.raises(S.SubstrateNotReady) as e:
        S.Gitea().await_ready(timeout_s=0.3, poll_s=0.01)
    assert calls["n"] > 1, "an ABSENT database must be retried, not refused on the first probe"
    assert "was not ready within" in str(e.value)


def test_the_gitea_probe_hops_to_the_owning_user_and_the_odoo_one_must_not(monkeypatch) -> None:
    """TWO PROBES ON PURPOSE, and this is the cell that says why.

    `docker compose exec` lands as ROOT in gitea's Alpine and as uid 101 `odoo` in odoo's Debian.
    Root can write anything, so the gitea probe MUST hop with `su` or it passes over a database the
    service cannot touch. Running that same `su` probe against odoo reports its healthy filestore as
    NOT WRITABLE, because Debian's `su` demands a password from a non-root caller — a false alarm in
    the readiness path, which is D0's over-refusal shape and would take the Odoo arm offline.
    """
    seen = []
    monkeypatch.setattr(S, "_compose",
                        lambda *a, **k: (seen.append(" ".join(a)),
                                         __import__("types").SimpleNamespace(
                                             stdout="", stderr="", returncode=0))[1])
    S.Gitea().assert_writable()
    S.Odoo().assert_writable()
    gitea_probe = next(c for c in seen if "gitea" in c and "test -w" in c)
    odoo_probe = next(c for c in seen if "odoo" in c and "test -w" in c)
    assert "su -s" in gitea_probe, "the gitea probe runs as root and would be vacuous without the hop"
    assert "su -s" not in odoo_probe, (
        "the odoo probe must NOT use `su`: exec is already the service user there, and Debian's `su` "
        "fails for a non-root caller — measured reporting a healthy filestore as NOT WRITABLE")
    assert "/var/lib/odoo" in odoo_probe and "filestore/" not in odoo_probe, (
        "the odoo probe must target the VOLUME, not a per-database filestore directory: `up()` calls "
        "`await_ready()` before anything is seeded, so a per-db path reports ABSENT and spins")


def test_the_base_class_writability_check_is_a_no_op(monkeypatch) -> None:
    """Anti-vacuity in the other direction: a substrate that copies nothing must not be forced to
    invent a probe, and the base must not silently pass for the two that DO override it."""
    monkeypatch.setattr(S, "_compose", lambda *a, **k: pytest.fail("the base probe ran a command"))
    S.Substrate(name="x", profile="x", url="http://x", health_path="/").assert_writable()
    assert S.Gitea.assert_writable is not S.Substrate.assert_writable
    assert S.Odoo.assert_writable is not S.Substrate.assert_writable


# --- seeding: the half of B2 that was named and never shipped ------------------------------------

def test_the_odoo_seed_never_passes_without_demo(compose) -> None:
    """THE FLAG TRAP, and it cost a whole seed. `--without-demo=False` DISABLES demo data: Odoo reads
    any non-empty value as a list of modules to skip demo for, so "False" is a module name nobody has
    and the switch is simply on. Measured: that spelling produced 0 leads and an empty world — R4.40's
    near-empty observation arriving from the harness, which would be scored against the agent.

    Omitting the flag is the ONLY spelling that means "load it", so the assertion is on ABSENCE.
    """
    S.Odoo().seed()
    init = [c for c in compose.joined() if "--stop-after-init" in c]
    assert init, f"nothing initialised the database; calls were {compose.joined()}"
    assert "without-demo" not in init[0], (
        f"the seed passes --without-demo: {init[0]!r}. Every non-empty value DISABLES demo data, "
        f"including the ones that read like they enable it (`False`, `0`, `no`).")
    assert "-i base,crm" in init[0] or "-i" in init[0], "no module list was installed"


def test_the_odoo_seed_verifies_the_clock_before_it_creates_any_rows(monkeypatch) -> None:
    """Ordering, and it is the whole point (R4.86). Demo data is generated RELATIVE TO INSTALL TIME,
    so seeding under an unpinned clock bakes the wrong dates into the template and `reset()` then
    restores that wrong world forever. Checking after the rows exist is too late."""
    order = []
    c = _Compose(stdout="yes")

    def _record(*a, **k):
        order.append(" ".join(a))
        return c(*a, **k)

    monkeypatch.setattr(S, "_compose", _record)
    monkeypatch.setattr(S.Odoo, "await_ready", lambda self, **kw: None)
    monkeypatch.setattr(S.Odoo, "assert_clock_pinned",
                        lambda self: order.append("CLOCK-CHECK"))
    S.Odoo().seed()
    assert "CLOCK-CHECK" in order, "the seed never verified the clock"
    init = next(i for i, c in enumerate(order) if "--stop-after-init" in c)
    assert order.index("CLOCK-CHECK") < init, (
        f"the clock is checked AFTER the rows are created: {order}")


def test_the_odoo_seed_stops_the_service_around_the_drop(compose) -> None:
    """`DROP DATABASE` fails with `There are 2 other sessions using the database` while the service
    is up — measured, doing it by hand."""
    S.Odoo().seed()
    js = compose.joined()
    stop = next(i for i, c in enumerate(js) if c.endswith("stop odoo"))
    start = next(i for i, c in enumerate(js) if c.endswith("start odoo"))
    init = next(i for i, c in enumerate(js) if "--stop-after-init" in c)
    assert stop < init < start, f"the init is not bracketed by stop/start: {js}"


def test_the_gitea_seed_creates_the_user_before_it_mints_a_token(compose, monkeypatch) -> None:
    """There is no API before there is a user; a token minted first comes back as an error string."""
    order = []
    monkeypatch.setattr(S.Gitea, "mint_token", lambda self, name="bench": order.append("MINT") or "t")
    monkeypatch.setattr(S.Gitea, "_api", lambda self, *a, **k: {"number": 1})
    S.Gitea().seed()
    created = [i for i, c in enumerate(compose.joined()) if "admin user create" in c]
    assert created, f"no user was created; calls were {compose.joined()}"
    assert order == ["MINT"], "the token was minted more than once, or not at all"


def test_minting_a_token_rejects_gitea_error_text_on_stdout(monkeypatch) -> None:
    """Gitea prints its ERRORS on stdout too, so a missing user yields a plausible-looking
    56-character string. Measured while building this: `${#TOK}` reported 56 and the token was an
    error message. Shape-checked, not merely non-empty."""
    monkeypatch.setattr(S, "_compose", lambda *a, **k: __import__("types").SimpleNamespace(
        stdout="Command error: user does not exist [uid: 0, name: bench]", stderr="", returncode=0))
    with pytest.raises(S.SubstrateError) as e:
        S.Gitea().mint_token()
    assert "did not return a token" in str(e.value)

    monkeypatch.setattr(S, "_compose", lambda *a, **k: __import__("types").SimpleNamespace(
        stdout="a" * 40, stderr="", returncode=0))
    assert S.Gitea().mint_token() == "a" * 40


def test_the_seeded_issue_set_can_actually_support_its_scenarios() -> None:
    """The corpus asserts against this world, so its shape is a property and not decoration."""
    assert len(S.ISSUES) >= 5
    states = {closed for _, _, closed in S.ISSUES}
    assert states == {True, False}, "gitea-filter-state needs BOTH states present to prove anything"
    titles = [t for t, _, _ in S.ISSUES]
    assert titles != sorted(titles), (
        "creation order equals alphabetical order, so gitea-sort-list would pass without sorting")
    assert len(set(titles)) == len(titles), "duplicate titles make gitea-open-issue ambiguous"
    distinctive = [t for t in titles if "marmalade" in t.lower()]
    assert len(distinctive) == 1, "gitea-search needs exactly one title carrying its search token"


def test_no_seeded_title_trips_the_write_classifier() -> None:
    """The read scenarios must not be misclassified as writes before they start.

    `safety.MUTATING_KEYWORDS` is a bare substring match with a measured 28% false-positive rate on
    ordinary controls ("Show borders" -> `order`, "Sender" -> `send`), and it CANNOT be fixed or
    removed — see CLAUDE.md. So the seeded world is written around it rather than fighting it, and
    that is pinned here: a title added later that trips it would silently turn a read scenario into a
    gated one and the bench would score `over_gated` against the product for the corpus's mistake.
    """
    from ultracua.safety import MUTATING_KEYWORDS
    tripped = {t: [k for k in MUTATING_KEYWORDS if k in t.lower()] for t, _, _ in S.ISSUES}
    tripped = {t: k for t, k in tripped.items() if k}
    assert not tripped, f"seeded titles trip the write classifier: {tripped}"
