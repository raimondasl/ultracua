"""Substrate lifecycle for the customer benchmark: bring up, prove ready, reset between scenarios.

Two substrates, chosen for CONTRAST (see the compose file). What this module owns is the part that
decides whether a scored number means anything:

  * RESET — every scenario must start from the same world, or the second scenario is measuring the
    first one's leftovers. Odoo resets by Postgres template + filestore; Gitea by deleting one SQLite
    file. Deliberately different mechanisms, because a shared one would hide a reset bug in exactly
    the contrast the benchmark exists to show.
  * READINESS — and this is the one with a register entry behind it.

WHY READINESS IS NOT A PING. R4.40: `_learn` snapshots straight after navigate with no settle floor,
so a client-rendered page is authored against its unrendered skeleton — quietly, and one variant
PERSISTS the resulting refusal. A substrate that accepts a TCP connection is not a substrate that can
serve; an Odoo container answers its port long before its registry is loaded. So readiness is a
three-layer thing here: the container's own healthcheck (in compose), then an HTTP probe for a
SUBSTANTIVE response, then a per-scenario hook that says what that scenario needs to exist.

AND THE GUARD THAT FOLLOWS FROM IT. If the agent's first observation is near-empty, that is a HARNESS
ERROR, not a scored discovery failure. Scoring it would quietly convert "we did not wait long enough"
into "the agent could not find the button" — a bench that grades its own flakiness as a product
defect, and does so in the direction that flatters nobody and misleads everybody. `SubstrateNotReady`
is raised instead, and the runner must abandon the scenario rather than record a result.

NOT WIRED INTO CI. Docker, ~3.2 GB of images and a real ERP; `benchmark-plan` B5 adds a nightly
ubuntu job. Everything key-less and Docker-free in here is unit-tested in
`tests/test_substrates.py`.
"""

from __future__ import annotations

import json
import subprocess
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
COMPOSE = ROOT / "benchmarks" / "substrates" / "docker-compose.yml"

# The clock the Odoo container is pinned to. Odoo's demo data is seeded relative to "today", so a
# scenario about "the overdue invoice" changes meaning as the wall clock moves. Owned here rather
# than only in the compose file so a run RECORDS the epoch it measured under — a baseline that does
# not know its own clock cannot be compared with one taken later.
FAKETIME_EPOCH = "@2026-01-15 09:00:00"

# Below this, an observation is a skeleton rather than a page. Not a guess: a served Gitea landing
# page is ~13.5 kB of HTML with dozens of elements, and an Odoo web client shell before its registry
# loads is a near-empty body. The number is a FLOOR for "something rendered", not a quality bar, and
# it is deliberately far below any real page so it can only fire on the failure it names.
SKELETON_ELEMENT_FLOOR = 3


class SubstrateError(RuntimeError):
    """The harness could not put the world into the state a scenario needs."""


class SubstrateNotReady(SubstrateError):
    """Raised instead of scoring. See the module docstring: R4.40's guard."""


def _compose(*args: str, check: bool = True, timeout: int = 300) -> subprocess.CompletedProcess:
    try:
        proc = subprocess.run(
            ["docker", "compose", "-f", str(COMPOSE), *args],
            capture_output=True, text=True, timeout=timeout,
        )
    except FileNotFoundError as exc:                 # no docker binary at all
        raise SubstrateError(f"`docker` is not on PATH: {exc}") from exc
    except subprocess.TimeoutExpired as exc:         # a hung daemon or a wedged pull
        raise SubstrateError(
            f"`docker compose {' '.join(args)}` did not return within {timeout}s") from exc
    if check and proc.returncode != 0:
        raise SubstrateError(
            f"`docker compose {' '.join(args)}` failed ({proc.returncode}):\n{proc.stderr.strip()}")
    return proc


def _http_ok(url: str, min_bytes: int) -> bool:
    """A SUBSTANTIVE response, not merely a response.

    `min_bytes` is what separates "the port answered" from "the app served". A 200 with an empty body
    is what an app returns while it is still starting, and treating it as ready is how a scenario ends
    up authored against a skeleton.
    """
    try:
        with urllib.request.urlopen(url, timeout=5) as resp:
            return resp.status == 200 and len(resp.read(min_bytes + 1)) > min_bytes
    except (urllib.error.URLError, OSError, ValueError):
        return False


@dataclass
class Substrate:
    """One benchmark substrate: how to start it, how to know it is ready, how to reset it."""

    name: str
    profile: str
    url: str
    health_path: str
    containers: tuple = ()
    min_body_bytes: int = 500

    def up(self, timeout_s: int = 300) -> float:
        """Start the profile and block until the substrate can actually serve. Returns seconds taken."""
        started = time.monotonic()
        _compose("--profile", self.profile, "up", "-d", timeout=timeout_s)
        self.await_ready(timeout_s=timeout_s)
        return time.monotonic() - started

    def down(self, wipe: bool = False) -> None:
        """Stop it. `wipe` removes the volumes too, which is the only full reset that always works."""
        args = ["--profile", self.profile, "down"] + (["-v"] if wipe else [])
        _compose(*args, check=False)

    def await_ready(self, timeout_s: int = 300, poll_s: float = 2.0) -> None:
        """Container health FIRST, then a substantive HTTP response.

        Both, because they fail differently: a container can be `healthy` while a proxy in front of it
        is not, and an HTTP probe can pass against a cached error page. Neither alone has been enough
        anywhere this repo has looked.
        """
        deadline = time.monotonic() + timeout_s
        last = ""
        while time.monotonic() < deadline:
            unhealthy = [c for c in self.containers if _container_health(c) not in ("healthy", "none")]
            if unhealthy:
                last = f"container(s) not healthy: {', '.join(unhealthy)}"
            elif _http_ok(self.url + self.health_path, self.min_body_bytes):
                return
            else:
                last = f"no substantive response from {self.url + self.health_path}"
            time.sleep(poll_s)
        raise SubstrateNotReady(
            f"{self.name} was not ready within {timeout_s}s — {last}.\n"
            f"    Refusing rather than proceeding: a scenario started against a half-built substrate "
            f"scores the harness's impatience as the agent's failure (R4.40)."
        )

    def reset(self) -> None:  # pragma: no cover - overridden
        raise NotImplementedError


def _container_health(name: str) -> str:
    """`healthy` / `starting` / `unhealthy`, or `none` when the container declares no healthcheck."""
    proc = subprocess.run(
        ["docker", "inspect", "--format", "{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}",
         name], capture_output=True, text=True)
    return proc.stdout.strip() if proc.returncode == 0 else "missing"


@dataclass
class Gitea(Substrate):
    """SQLite, so the reset is one file — the cheapest honest reset there is.

    `SEED` is captured once from a freshly seeded instance and copied back over the live file with the
    service stopped. Stopped, not live: SQLite's WAL means copying a file under a running server can
    capture a torn state that reads fine and is subtly wrong, which is the silent direction.
    """

    name: str = "gitea"
    profile: str = "gitea"
    url: str = "http://localhost:3000"
    health_path: str = "/"
    containers: tuple = ("ultracua-bench-gitea-1",)
    db_path: str = "/data/gitea/gitea.db"
    seed_path: str = "/data/gitea/seed.db"

    def snapshot(self) -> None:
        """Freeze the CURRENT state as the world every scenario starts from."""
        _compose("--profile", self.profile, "stop")
        try:
            # The WAL moves WITH the database or the seed is a torn state that reads fine.
            _compose("--profile", self.profile, "run", "--rm", "-T", "--entrypoint", "sh", "gitea",
                     "-c", f"cp {self.db_path} {self.seed_path} && "
                           f"rm -f {self.seed_path}-wal {self.seed_path}-shm")
        finally:
            _compose("--profile", self.profile, "start", check=False)
        self.await_ready()

    def reset(self) -> None:
        """Restore the seed, and take the WAL with it.

        THREE THINGS THE FIRST DRAFT GOT WRONG, all in the uncovered half of the file:

        1. The precondition was `sh -c 'test -f seed.db && cp ...'` under `check=True`, so a
           never-snapshotted instance raised `SubstrateError: ... failed (1)` with an EMPTY stderr.
           Its Odoo sibling had a named precondition saying exactly what to do — the "a guard that
           exists on a sibling path was never applied to the mechanism" shape CLAUDE.md says predicts
           the next bug.
        2. It restored `gitea.db` and left `gitea.db-wal` / `-shm` behind. `docker compose stop`
           SIGKILLs after the grace period, so a dirty run leaves a WAL — and SQLite REPLAYS it over
           the restored seed on next open. The reset would appear to work and the world would carry
           the previous scenario's writes. The class docstring reasoned about WAL for the COPY
           direction and never for the RESTORE direction.
        3. No `try/finally`, while `stop` comes first — so any raise in between left the service
           DOWN, and every later scenario reported a harness error from a second, unrelated cause.
        """
        _compose("--profile", self.profile, "stop")
        try:
            probe = _compose("--profile", self.profile, "run", "--rm", "-T", "--entrypoint", "sh",
                             "gitea", "-c", f"test -f {self.seed_path} && echo yes || echo no")
            if "yes" not in probe.stdout:
                raise SubstrateError(
                    f"no seed at {self.seed_path} — call `snapshot()` once after seeding, or the "
                    f"first scenario silently defines the world for every later one")
            _compose("--profile", self.profile, "run", "--rm", "-T", "--entrypoint", "sh", "gitea",
                     "-c", f"rm -f {self.db_path}-wal {self.db_path}-shm && "
                           f"cp {self.seed_path} {self.db_path}")
        finally:
            _compose("--profile", self.profile, "start", check=False)
        self.await_ready()


@dataclass
class Odoo(Substrate):
    """Postgres template + filestore. Odoo keeps state in BOTH, and resetting one is a half-reset.

    `CREATE DATABASE x TEMPLATE seed` is atomic and fast, which is why it beats a dump/restore; it
    requires no other session on the template, which is why the web service is stopped around it.
    The filestore (attachments, generated assets) lives on disk and must move with the database, or
    a scenario that opens an attachment gets the PREVIOUS scenario's file with the current row's id.
    """

    name: str = "odoo"
    profile: str = "odoo"
    url: str = "http://localhost:8069"
    health_path: str = "/web/database/selector"
    containers: tuple = ("ultracua-bench-odoo-db-1", "ultracua-bench-odoo-1")
    db_name: str = "bench"
    seed_db: str = "bench_seed"

    def _psql(self, sql: str) -> str:
        proc = _compose("--profile", self.profile, "exec", "-T", "odoo-db",
                        "psql", "-U", "odoo", "-d", "postgres", "-tAc", sql)
        return proc.stdout.strip()

    # Odoo's filestore is per-database and lives in the ODOO container, not the db one.
    filestore: str = "/var/lib/odoo/filestore"

    def snapshot(self) -> None:
        """Freeze the seeded world: a template database plus its filestore."""
        _compose("--profile", self.profile, "stop", "odoo")
        self._psql(f'DROP DATABASE IF EXISTS "{self.seed_db}"')
        self._psql(f'CREATE DATABASE "{self.seed_db}" TEMPLATE "{self.db_name}"')
        _compose("--profile", self.profile, "start", "odoo")
        self.await_ready()
        _compose("--profile", self.profile, "exec", "-T", "odoo", "sh", "-c",
                 f"rm -rf {self.filestore}/{self.seed_db} && "
                 f"cp -a {self.filestore}/{self.db_name} {self.filestore}/{self.seed_db}")

    def reset(self) -> None:
        """BOTH halves, or it is a half-reset.

        Odoo keeps state in Postgres AND on disk. Restoring only the database leaves the previous
        scenario's attachments behind under ids the new database has reissued, so a scenario that
        opens an attachment gets the wrong file while every row looks right — the silent direction.

        `CREATE DATABASE … TEMPLATE` beats a dump/restore: it is atomic and fast. It also requires no
        other session on the template, which is why `odoo` is stopped around it rather than merely
        asked nicely.

        UNVERIFIED ON THE AUTHORING HOST, and said plainly rather than implied: this was written on a
        machine with 308 MB of available memory and ~50k pages/sec, where bringing up Odoo would
        produce timings and readiness behaviour that mean nothing (`CLAUDE.md`: a loaded host cannot
        adjudicate). The Gitea path below IS verified end to end. Treat this method as reviewed code
        that has not yet been run, and drive it once before the first Odoo scenario is scored.
        """
        if self.seed_db not in self._psql("SELECT datname FROM pg_database"):
            raise SubstrateError(
                f"no template database {self.seed_db!r} — call `snapshot()` once after seeding, or "
                f"the first scenario would silently define the world for every later one")
        _compose("--profile", self.profile, "stop", "odoo")          # release sessions on the template
        try:
            self._psql(f'DROP DATABASE IF EXISTS "{self.db_name}"')
            self._psql(f'CREATE DATABASE "{self.db_name}" TEMPLATE "{self.seed_db}"')
        finally:
            _compose("--profile", self.profile, "start", "odoo", check=False)
        self.await_ready()
        _compose("--profile", self.profile, "exec", "-T", "odoo", "sh", "-c",
                 f"rm -rf {self.filestore}/{self.db_name} && "
                 f"cp -a {self.filestore}/{self.seed_db} {self.filestore}/{self.db_name}")


# ---------------------------------------------------------------------------------------------------
# R4.40's GUARD. The one thing in this module that a scored run must call.

def assert_not_a_skeleton(observation, *, substrate: str, scenario: str) -> None:
    """A near-empty FIRST observation is a harness error, never a scored discovery failure.

    This is the whole reason readiness is a three-layer thing above. If it still slips through — the
    substrate served, but the page had not rendered — the run must ABANDON rather than score, because
    a scored zero here reads as "the agent could not find it" when the truth is "there was nothing on
    the page yet". Converting harness impatience into a product defect is the silent-wrong direction,
    and it flatters no one.

    `observation` is duck-typed on `elements` because the bench holds no import of the agent's
    Observation type and should not acquire one for a length check.
    """
    n = len(getattr(observation, "elements", ()) or ())
    if n < SKELETON_ELEMENT_FLOOR:
        raise SubstrateNotReady(
            f"{substrate}/{scenario}: the first observation had {n} element(s), below the "
            f"{SKELETON_ELEMENT_FLOOR}-element floor — the page had not rendered.\n"
            f"    ABANDONING rather than scoring. A scored zero here would read as a discovery "
            f"failure by the agent when the truth is that the harness did not wait (R4.40)."
        )


def substrate_report() -> dict:
    """What a run must record about the world it measured, so a baseline knows its own conditions."""
    return {
        "faketime_epoch": FAKETIME_EPOCH,
        "images": _pinned_images(),
        # POSIX, always. `str(Path)` yields backslashes on Windows, so a baseline recorded here and
        # one recorded on the nightly ubuntu job would differ in a field that describes nothing about
        # the measurement — a spurious diff in the artifact whose whole job is comparability.
        "compose": COMPOSE.relative_to(ROOT).as_posix(),
    }


def _pinned_images() -> dict:
    """The image tags the compose file pins, read from the file rather than remembered."""
    out = {}
    for line in COMPOSE.read_text(encoding="utf-8").splitlines():
        s = line.strip()
        if s.startswith("image:"):
            ref = s.split("image:", 1)[1].strip()
            out[ref.split(":")[0].split("/")[-1]] = ref
    return out


if __name__ == "__main__":  # pragma: no cover - a convenience surface, not a tested one
    import sys
    print(json.dumps(substrate_report(), indent=1))
    sys.exit(0)
