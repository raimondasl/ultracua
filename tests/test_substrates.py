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
            f"    Unit tests here must not touch Docker. NOT because CI lacks a daemon -- it "
            f"has one (measured: Docker 28.0.4, R4.109) -- but because this host runs a LIVE, "
            f"SEEDED substrate between sessions and a CI job starts with nothing running. So "
            f"a cell that reaches Docker passes here against a real Gitea and fails both CI "
            f"arms; R4.85's readiness layer did exactly that.\n"
            f"    Use the `compose` fixture, patch `S._compose` or `subprocess.run` in the test "
            f"body, or neutralise the probe that calls it (e.g. `assert_writable`) when this cell "
            f"is about a different layer."
        )

    monkeypatch.setattr(S.subprocess, "run", _refuse)

    # AND THE SAME AXIS OVER HTTP, which is how it arrived the SECOND time (0.124.0). `Odoo.seed()`
    # and `Odoo.reset()` now end in `warm_assets()`, which fetches `/web` and its asset bundles --
    # so four cells that mock `_compose` began reaching THIS HOST'S live Odoo on :8069 and passing
    # for that reason. Measured by stopping the container: 4 failed, 22 passed. CI has no Odoo, so
    # every one of them would have been red there and green here, which is R4.85's finding exactly,
    # one transport over.
    #
    # Both entry points, because they are different objects: `_http_ok` calls `urlopen`, while
    # `warm_assets` and `rpc` go through `build_opener(...).open`. Patching one leaves the other
    # live, and a half-closed guard is worse than none -- it reads as closed.
    def _refuse_http(self_or_url, *args, **kwargs):
        # `urlopen(url)` puts the target first; `OpenerDirector.open(self, url)` puts it in `args`.
        target = args[0] if isinstance(self_or_url, S.urllib.request.OpenerDirector) else self_or_url
        target = getattr(target, "full_url", target)
        raise AssertionError(
            f"{request.node.name} tried to reach {str(target)[:80]!r} over HTTP.\n"
            f"    Same axis as the Docker guard above: this host runs the substrates and CI does "
            f"not, so a cell that reaches one is green here and red on both CI arms.\n"
            f"    Use the `compose` fixture (it neutralises `warm_assets`), or patch the probe this "
            f"cell is not about."
        )

    monkeypatch.setattr(S.urllib.request, "urlopen", _refuse_http)
    monkeypatch.setattr(S.urllib.request.OpenerDirector, "open", _refuse_http)



class _Body:
    """A minimal `urlopen` result: enough for the readiness and warmup probes, and no more."""

    def __init__(self, text: str, status: int = 200):
        self._text, self.status = text, status

    def read(self, n: int = -1) -> bytes:
        return self._text.encode()[:None if n is None or n < 0 else n]

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class _Proc:
    def __init__(self, stdout: str = "", returncode: int = 0):
        self.stdout, self.stderr, self.returncode = stdout, "", returncode


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
    """The boundary, exactly, because a floor that drifts silently changes what readiness means.

    **THIS CELL WORKED.** It pinned the floor at 3 and said "if someone raises it to improve
    detection, this goes red and they have to argue for it". At 0.126.0 someone did, and the argument
    is a measurement rather than an intuition: an unrendered Odoo shell snapshots 5 elements, so the
    old floor sat BELOW the skeleton and R4.40's guard could not fire on the substrate its own comment
    cited. The literal stays here — a floor that moves without a red test is a floor nobody re-argued
    — and `test_the_skeleton_floor_sits_in_the_measured_gap` is what makes the number defensible
    rather than merely current.
    """
    assert S.SKELETON_ELEMENT_FLOOR == 12, "the floor moved; re-argue it against MEASURED pages"
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
    `warm_assets` joins them for the same reason at 0.124.0: `seed()` and `reset()` both end in it,
    and it speaks HTTP rather than `docker compose`, so faking `_compose` alone left four cells
    reaching this host's live Odoo. Every list is DERIVED, because the first version of this fixture
    named `Substrate.await_ready` and stopped reaching Odoo the moment Odoo grew an override.
    """
    c = _Compose(stdout="yes")
    monkeypatch.setattr(S, "_compose", c)
    for name, stub in (("await_ready", lambda self, **kw: None),
                       ("assert_clock_pinned", lambda self: None),
                       ("warm_assets", lambda self: 0)):
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


def test_every_probe_the_lifecycle_calls_outside_compose_is_neutralised() -> None:
    """DERIVED FROM THE SOURCE, because the list is what went stale. `seed()` and `reset()` reach
    past `_compose` — for the clock, for writability, for the asset warmup — and each addition has
    silently un-mocked the cells below until someone noticed. `warm_assets` is the one that did it
    over HTTP, where the Docker guard could not see it."""
    import inspect

    called = set()
    for cls in (S.Odoo, S.Gitea):
        for meth in ("seed", "reset", "snapshot"):
            if meth not in vars(cls):
                continue
            src = inspect.getsource(vars(cls)[meth])
            called |= {n for n in ("warm_assets", "assert_clock_pinned", "await_ready",
                                   "assert_writable", "assert_bound_to_one_database")
                       if f"self.{n}(" in src}
    neutralised = {"await_ready", "assert_clock_pinned", "warm_assets"}
    # `assert_writable` and `assert_bound_to_one_database` are reached only THROUGH `await_ready`,
    # which is stubbed, so they need no entry of their own — asserted rather than assumed.
    assert called <= neutralised, (
        f"the lifecycle calls {sorted(called - neutralised)} directly and the `compose` fixture does "
        f"not neutralise it, so those cells will reach a real substrate")


def test_the_http_guard_refuses_a_unit_test_that_reaches_a_substrate() -> None:
    """The guard itself, armed. It is the only thing standing between a green local suite and a red
    CI one for anything that speaks HTTP, and a guard nobody has watched refuse is a guard nobody
    has watched."""
    with pytest.raises(AssertionError, match="over HTTP"):
        S._http_ok("http://localhost:8069/web/login", 1)
    with pytest.raises(AssertionError, match="over HTTP"):
        S.Odoo().warm_assets()


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
    # This cell patches by hand rather than taking the `compose` fixture, so it owns the same list --
    # and the autouse HTTP guard is what told it so, loudly, the moment `seed()` grew a warmup.
    monkeypatch.setattr(S.Odoo, "warm_assets", lambda self: order.append("WARM") or 0)
    S.Odoo().seed()
    assert "CLOCK-CHECK" in order, "the seed never verified the clock"
    init = next(i for i, c in enumerate(order) if "--stop-after-init" in c)
    assert order.index("CLOCK-CHECK") < init, (
        f"the clock is checked AFTER the rows are created: {order}")
    assert "WARM" in order and order.index("WARM") > init, (
        f"the seed must warm the asset bundles AFTER the install, so `snapshot()` freezes a warm "
        f"template and no scenario pays the 2.42 s compile: {order}")


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
# --- R4.89: the agent's entry point must be the APP, not Odoo's database manager -----------------

def test_the_compose_file_binds_odoo_to_exactly_one_database() -> None:
    """WHAT WAS MEASURED, and it is worse than a wrong landing page.

    With two databases visible -- `bench` and its own reset template `bench_seed` -- and no
    `--db-filter`, Odoo cannot choose, so it serves the DATABASE MANAGER at every entry point an
    agent could use: `/`, `/web` and `/web/login` all returned the same 43,551-byte page listing both
    databases with Delete / Duplicate / Backup / Restore beside each. The benchmark's start page
    offered one-click deletion of the template every later `reset()` depends on.

    `--no-database-list` is the second half and not a belt-and-braces extra: with a db-filter alone
    `/web/database/manager` stays reachable. Verified in the image's own source -- `exp_drop`,
    `exp_create_database`, `exp_duplicate_database`, `exp_restore` and `exp_rename` all carry
    `@check_db_management_enabled`, which raises `AccessDenied` when `list_db` is false.
    """
    text = S.COMPOSE.read_text(encoding="utf-8")
    assert "--db-filter=^bench$$" in text, (
        "the odoo service does not bind to one database. Unanchored or absent, Odoo serves its "
        "database MANAGER instead of the app, listing the reset template beside a Delete button "
        "(R4.89). `$$` is compose's escape for a literal `$`.")
    assert "--no-database-list" in text, (
        "`/web/database/manager` is still reachable, and its drop/restore forms act on the "
        "benchmark's own databases")


def test_the_odoo_healthcheck_is_not_pointed_at_the_page_that_means_it_is_broken() -> None:
    """`/web/database/selector` is what Odoo serves INSTEAD of the app, so a probe aimed there passes
    on exactly the state it should catch -- R4.85's finding (every readiness layer was a read) one
    door over."""
    # THE `test:` LINES, NOT THE BLOCK. The compose file now carries a COMMENT explaining why the
    # selector is the wrong probe, so a scan over the raw text matches the prose and fails on the
    # very edit that fixed it -- CLAUDE.md's "do not sed a file that is 40% prose about the shape you
    # are removing", wearing an assertion. This cell got it wrong on its first run.
    probes = [line.split("test:", 1)[1] for line in S.COMPOSE.read_text(encoding="utf-8").splitlines()
              if line.strip().startswith("test:")]
    assert probes, "no healthcheck commands found; the derivation is broken"
    bad = [x for x in probes if "/web/database/selector" in x]
    assert not bad, (
        f"a healthcheck probes the database selector, which answers 200 whether or not the app is "
        f"bound to a database: {bad}")
    assert S.Odoo().health_path == "/web/login"


def test_readiness_refuses_when_odoo_is_serving_its_database_manager(monkeypatch) -> None:
    """The runtime half. Both pages are HTTP 200 with a large body, so status and `min_body_bytes`
    cannot tell them apart -- measured, 4,430 bytes bound versus 42,559 unbound. The body is the
    only discriminator, and `master_pwd` is the field the manager has and the login form does not."""
    o = S.Odoo()
    monkeypatch.setattr(o, "_psql", lambda sql: "bench bench_seed postgres")
    monkeypatch.setattr(S.urllib.request, "urlopen",
                        lambda *a, **k: _Body('<input name="master_pwd" type="password"/>'))
    with pytest.raises(S.SubstrateNotReady) as exc:
        o.assert_bound_to_one_database()
    assert "DATABASE MANAGER" in str(exc.value)
    assert "db-filter" in str(exc.value), "the refusal must name the remedy, not just the symptom"


def test_a_virgin_instance_is_told_to_seed_rather_than_to_fix_its_config(monkeypatch) -> None:
    """TWO CAUSES, DIFFERENT REMEDIES. `up()` legitimately runs before `seed()`, and with no `bench`
    database Odoo serves the manager -- measured against a filter matching nothing. Telling that
    operator the db-filter is wrong sends them to fix something that is not broken, which is the
    attribution error this whole benchmark exists to avoid, aimed at a human."""
    o = S.Odoo()
    monkeypatch.setattr(o, "_psql", lambda sql: "postgres template0 template1")
    monkeypatch.setattr(S.urllib.request, "urlopen", lambda *a, **k: _Body('name="master_pwd"'))
    with pytest.raises(S.SubstrateNotReady) as exc:
        o.assert_bound_to_one_database()
    assert "does not exist yet" in str(exc.value) and "seed()" in str(exc.value)
    assert "db-filter" not in str(exc.value)


def test_a_bound_app_passes_readiness(monkeypatch) -> None:
    """The other direction, or "refuse everything" satisfies both cells above."""
    o = S.Odoo()
    monkeypatch.setattr(S.urllib.request, "urlopen",
                        lambda *a, **k: _Body('<input name="login"/><input name="password"/>'))
    monkeypatch.setattr(o, "_psql", lambda sql: pytest.fail("a bound app must not need Postgres"))
    o.assert_bound_to_one_database()


# --- the asset warmup, which the plan asked for and nobody had measured --------------------------

def test_the_reset_warms_the_asset_bundles_after_restoring_both_halves(compose, monkeypatch) -> None:
    """Measured: a template restored cold costs +2.42 s on the first backend page load, charged to
    whichever scenario happens to run first. `seed()` warms so the template is frozen warm; `reset()`
    warms so that is a guarantee rather than an assumption about how the template was made."""
    warmed = []
    monkeypatch.setattr(S.Odoo, "warm_assets", lambda self: warmed.append(len(compose.calls)) or 0)
    o = S.Odoo()
    monkeypatch.setattr(o, "_psql", lambda sql: "bench_seed")
    o.reset()
    js = compose.joined()
    filestore = next(i for i, c in enumerate(js) if "cp -a" in c)
    assert warmed, "the reset never warmed the asset bundles"
    assert warmed[0] > filestore, (
        f"the warmup ran before the filestore was restored, so it warmed the OLD world: {js}")


def test_the_warmup_refuses_a_session_that_never_reached_the_backend(monkeypatch) -> None:
    """THE SENSOR IS A DIFFERENTIAL, and the first version was not.

    It raised only when NO bundle was found, on the measured claim that an anonymous `/web`
    referenced none -- true of the DATABASE MANAGER page Odoo served before R4.89 was fixed, and
    false the moment it served a real login page (which carries three `web.assets_frontend*`
    bundles). A broken session would then have warmed those three, returned 3, and never raised,
    while the 4.9 MB backend pair this method exists for stayed cold.
    """
    o = S.Odoo()
    same = '<script src="/web/assets/1/web.assets_frontend.min.js"></script>'
    monkeypatch.setattr(S.urllib.request.OpenerDirector, "open", lambda self, *a, **k: _Body(same))
    monkeypatch.setattr(S.Odoo, "_authenticate", lambda self, opener: 2)
    with pytest.raises(S.SubstrateNotReady, match="never reached the backend"):
        o.warm_assets()


def test_the_warmup_accepts_a_session_that_reaches_further_than_an_anonymous_one(monkeypatch) -> None:
    """Anti-vacuity: without this, "always refuse" passes the cell above."""
    pages = iter(['<script src="/web/assets/1/frontend.js"></script>',
                  '<script src="/web/assets/2/backend.js"></script>'])
    fetched = []

    def _open(self, target, *a, **k):
        url = getattr(target, "full_url", target)
        if url.endswith("/web"):
            return _Body(next(pages))
        fetched.append(url)
        return _Body("bytes")

    monkeypatch.setattr(S.urllib.request.OpenerDirector, "open", _open)
    monkeypatch.setattr(S.Odoo, "_authenticate", lambda self, opener: 2)
    assert S.Odoo().warm_assets() == 2
    assert sorted(u.rsplit("/", 1)[-1] for u in fetched) == ["backend.js", "frontend.js"], (
        f"both sides of the login wall must be fetched; got {fetched}")


# --- the Odoo query surface, which is where the clock scan finally has SQL to police -------------

def test_the_oracle_query_targets_the_scenario_database_and_not_postgres(monkeypatch) -> None:
    """`_psql` runs the LIFECYCLE statements -- `DROP DATABASE`, `CREATE DATABASE ... TEMPLATE` --
    which cannot run from inside the database they drop. An oracle must not be able to reach those,
    so it gets its own accessor rather than a flag on that one."""
    seen = []
    monkeypatch.setattr(S, "_compose", lambda *a, **k: seen.append(a) or _Proc("1<::>x"))
    rows = S.Odoo().query("SELECT id, name FROM crm_lead")
    argv = " ".join(seen[0])
    assert "-d bench " in argv + " " and "-d postgres" not in argv, argv
    assert rows == (("1", "x"),)


def test_the_odoo_query_separator_cannot_occur_in_the_data(monkeypatch) -> None:
    """psql's default `|` appears in free text -- Odoo's customer and lead names are free text -- and
    a separator that occurs in the data silently splits one column into two, so the oracle compares
    malformed identities. Same requirement as `Gitea.query`, and the same constant."""
    seen = []
    monkeypatch.setattr(S, "_compose", lambda *a, **k: seen.append(a) or _Proc(""))
    S.Odoo().query("SELECT 1")
    assert S.SQL_SEP in seen[0], f"no explicit separator in {seen[0]}"
    assert "|" not in S.SQL_SEP


def test_an_rpc_error_is_a_substrate_error_and_not_a_silent_none(monkeypatch) -> None:
    """`call_kw` answers HTTP 200 with an `error` member. Returning `result` unchecked would hand
    the liveness pass a `None` and it would report the probe as seeing nothing -- a broken door
    reported as a blind oracle."""
    monkeypatch.setattr(S.Odoo, "_authenticate", lambda self, opener: 2)
    monkeypatch.setattr(S.urllib.request.OpenerDirector, "open", lambda self, *a, **k: _Body(
        '{"error": {"data": {"message": "Access Denied"}}}'))
    with pytest.raises(S.SubstrateError, match="Access Denied"):
        S.Odoo().rpc("crm.lead", "create", [{"name": "x"}])


def test_the_odoo_module_list_installs_sale_beside_crm() -> None:
    """`sale` is load-bearing rather than convenient: `odoo-open-record` is the one read the corpus
    DECLARES will trip the keyword classifier, and it does so on the word "order". Navigating to a
    CRM lead trips nothing, so building that pair on `crm` alone would silently drop the arm."""
    mods = S.Odoo().modules.split(",")
    assert "sale" in mods and "crm" in mods, mods


def test_the_skeleton_floor_sits_in_the_measured_gap() -> None:
    """THE OLD FLOOR COULD NOT FIRE ON THE CASE ITS OWN COMMENT CITED.

    It was 3, on the strength of "an Odoo web client shell ... is a near-empty body". Measured at
    0.126.0 by driving all five Odoo reads with a real session, the unrendered OWL shell snapshots
    **5** elements — so R4.40's guard would have passed an agent authoring against an unrendered
    page, on the very substrate the comment named.

    Asserted as a RELATIONSHIP rather than as a literal: the floor must be strictly above what a
    skeleton measures and strictly below the smallest page the corpus actually renders. A substrate
    whose real pages are small then forces a rethink here, instead of silently reintroducing a floor
    nothing can trip. Both bounds are measurements and are named as such.
    """
    assert S.MEASURED_SKELETON_ELEMENTS < S.SKELETON_ELEMENT_FLOOR, (
        f"the floor ({S.SKELETON_ELEMENT_FLOOR}) is at or below a measured skeleton "
        f"({S.MEASURED_SKELETON_ELEMENTS}), so R4.40's guard cannot fire on the failure it names")
    assert S.SKELETON_ELEMENT_FLOOR < S.MEASURED_SMALLEST_RENDERED_ELEMENTS, (
        f"the floor ({S.SKELETON_ELEMENT_FLOOR}) is at or above the smallest RENDERED corpus page "
        f"({S.MEASURED_SMALLEST_RENDERED_ELEMENTS}), so readiness would refuse a page that is fine "
        f"— the D0 over-refusal shape, aimed at the harness")


def test_a_real_odoo_skeleton_is_refused_and_a_real_page_is_not() -> None:
    """The guard driven at the two measured populations, so the numbers above are not just ordered
    but ACTED on. Without this, the relationship could hold while `assert_not_a_skeleton` read some
    other constant entirely."""
    with pytest.raises(S.SubstrateNotReady, match="had not rendered"):
        S.assert_not_a_skeleton(_Obs(S.MEASURED_SKELETON_ELEMENTS), substrate="odoo", scenario="x")
    S.assert_not_a_skeleton(_Obs(S.MEASURED_SMALLEST_RENDERED_ELEMENTS), substrate="odoo",
                            scenario="x")
