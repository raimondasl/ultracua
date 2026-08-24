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

import datetime
import http.cookiejar
import json
import os
import re
import subprocess
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parent.parent
COMPOSE = ROOT / "benchmarks" / "substrates" / "docker-compose.yml"

# The clock the Odoo container is pinned to. Odoo's demo data is seeded relative to "today", so a
# scenario about "the overdue invoice" changes meaning as the wall clock moves. Owned here rather
# than only in the compose file so a run RECORDS the epoch it measured under — a baseline that does
# not know its own clock cannot be compared with one taken later.
FAKETIME_EPOCH = "@2026-01-15 09:00:00"

# How far the Odoo container's clock may sit from the pinned epoch before readiness refuses.
# GENEROUS ON PURPOSE: `faketime` with an absolute `@epoch` starts there and then advances in real
# time, so a long-lived container drifts forward legitimately. This separates "pinned, and has been
# up a while" from "not pinned at all" — and the failure it exists to catch misses by MONTHS
# (2026-08 against a 2026-01 epoch), not by days, so the tolerance costs nothing.
CLOCK_DRIFT_TOLERANCE_S = 7 * 86400

# Below this, an observation is a skeleton rather than a page. Not a guess: a served Gitea landing
# page is ~13.5 kB of HTML with dozens of elements, and an Odoo web client shell before its registry
# loads is a near-empty body. The number is a FLOOR for "something rendered", not a quality bar, and
# it is deliberately far below any real page so it can only fire on the failure it names.
SKELETON_ELEMENT_FLOOR = 3

# Gitea's seeded world. FIXED CONTENT, because a scenario asserts against it: `gitea-filter-state`
# needs both states present, `gitea-sort-list` needs a creation order that differs from alphabetical
# (or sorting proves nothing), and `gitea-search` needs one distinctive token that appears in exactly
# one title. Titles are deliberately mundane; the read-only ones must NOT trip
# `safety.MUTATING_KEYWORDS`, whose measured false-positive rate on ordinary controls is 28%.
#   (title, body, closed)
ISSUES = (
    ("Zebra crossing renders behind the map layer", "Reported on the staging tile server.", False),
    ("Alpha channel lost on export",                "Only for 16-bit sources.",             False),
    ("Marmalade parser rejects trailing commas",    "The distinctive one, for search.",     False),
    ("Kerning regression in the sidebar",           "Since the font bump.",                 True),
    ("Dark mode contrast below AA on links",        "Measured 3.9:1.",                      False),
    ("Batch importer times out past 10k rows",      "Needs a cursor.",                      True),
    ("Tooltip clips at the viewport edge",          "Right edge only.",                     False),
)

# The instant issue #1 is dated, each later issue one day after it. FIXED, so the corpus's "oldest
# issue" has the same answer in March and in June — the same reason the Odoo clock is pinned. Set to
# 2026-01-15 09:00:00 UTC to match FAKETIME_EPOCH, so both substrates describe one consistent world.
ISSUE_EPOCH = 1768467600

# A LOCAL, THROWAWAY FIXTURE CREDENTIAL for a container bound to localhost with registration
# disabled — the same standing as the `odoo/odoo` Postgres pair already in the compose file. It is
# not a secret and must never become one: if this substrate is ever exposed beyond localhost, this
# constant is the first thing that has to go.
GITEA_PASSWORD = "benchbench"

# Odoo's demo administrator. Created by the demo data, not by this harness -- `admin`/`admin` is
# what `-i base --without-demo` omitted means, and changing it would mean patching the demo module.
# SAME STANDING AS `GITEA_PASSWORD` and the `odoo/odoo` Postgres pair in the compose file: a local,
# throwaway fixture credential for a container bound to localhost. It is not a secret and must never
# become one; if this substrate is ever exposed beyond localhost this constant is the first thing
# that has to go. Needed here because the ASSET WARMUP below is authenticated -- measured, an
# anonymous GET of `/web` serves the login page, which references no `/web/assets/` bundle at all.
ODOO_LOGIN = "admin"
ODOO_PASSWORD = "admin"

# Column separator for `Gitea.query` and `Odoo.query`. `|` is sqlite3's (and psql's) default and
# appears in real issue titles and customer names, so a row silently splits into an extra column and
# the oracle compares a malformed identity.
#
# WHAT THIS IS AND IS NOT A GUARANTEE OF, since the difference is the interesting part. No SEEDED
# value contains `<::>`; an AGENT could still type it into a lead name. That residual is accepted
# because its failure is LOUD in both oracles rather than silent: the extra column makes the row a
# different arity, so a read oracle sees an identity that is not in its baseline and REFUSES, and a
# write oracle raises while unpacking. Both attribute to the harness, neither scores a quiet pass --
# which is the only property this constant has to have.
SQL_SEP = "<::>"


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


def _anonymous_opener():
    """A cookie-carrying opener with NO session. `_authenticate` is what gives it one.

    Its own jar per call, deliberately: a shared session outliving a `reset()` would carry a cookie
    for a database that no longer exists, and the resulting failure would name the wrong cause --
    the same reason `Gitea.token()` is invalidated by its reset.
    """
    return urllib.request.build_opener(
        urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))


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
        """Container health, then a substantive HTTP response, then CAN IT BE WRITTEN TO.

        The first two fail differently: a container can be `healthy` while a proxy in front of it is
        not, and an HTTP probe can pass against a cached error page. Neither alone has been enough
        anywhere this repo has looked.

        THE THIRD LAYER EXISTS BECAUSE THE FIRST TWO ARE BOTH READS (R4.85). A `wget --spider`
        healthcheck and an HTTP GET pass perfectly against a substrate whose database the service
        cannot write, which is precisely the state `Gitea.reset()` used to leave behind. A substrate
        that cannot accept a write is not ready; discovering that from a scored write scenario
        attributes the harness's breakage to the agent.

        A NOT-WRITABLE verdict raises immediately rather than spinning: it never self-heals, and
        burning the full timeout would report it as "not ready in 300s" instead of naming it. Only
        an ABSENT state can resolve on its own, so only that one keeps waiting.
        """
        deadline = time.monotonic() + timeout_s
        last = ""
        while time.monotonic() < deadline:
            unhealthy = [c for c in self.containers if _container_health(c) not in ("healthy", "none")]
            if unhealthy:
                last = f"container(s) not healthy: {', '.join(unhealthy)}"
            elif _http_ok(self.url + self.health_path, self.min_body_bytes):
                try:
                    self.assert_writable()
                except SubstrateNotReady as exc:
                    if "NOT WRITABLE" in str(exc):
                        raise
                    last = str(exc).splitlines()[0]
                else:
                    return
            else:
                last = f"no substantive response from {self.url + self.health_path}"
            time.sleep(poll_s)
        raise SubstrateNotReady(
            f"{self.name} was not ready within {timeout_s}s — {last}.\n"
            f"    Refusing rather than proceeding: a scenario started against a half-built substrate "
            f"scores the harness's impatience as the agent's failure (R4.40)."
        )

    def assert_writable(self) -> None:
        """Nothing to check by default. Overridden where a reset COPIES the substrate's state files.

        DELIBERATELY NOT A SHARED MECHANISM, and that is measured rather than stylistic:
        `docker compose exec` lands as a DIFFERENT user per image — **root** in gitea's Alpine, uid
        101 `odoo` in odoo's Debian — so one probe cannot be correct for both. The gitea probe needs
        an `su` hop precisely because root would make it vacuous; running that same probe against
        odoo reports its perfectly healthy filestore as NOT WRITABLE, because Debian's `su` demands a
        password when a non-root caller invokes it. A false alarm in the readiness path is the
        over-refusal shape D0 records, and it would have taken the whole Odoo arm offline.
        """

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
    user: str = "bench"
    repo: str = "acme"
    # Process-local, never serialized. `repr=False, compare=False` so a token cannot reach a log or
    # a record through a dataclass repr, which is how secrets leak by accident.
    _token: Optional[str] = field(default=None, repr=False, compare=False)
    _token_seq: int = field(default=0, repr=False, compare=False)
    data_dir: str = "/data/gitea"
    db_path: str = "/data/gitea/gitea.db"
    seed_path: str = "/data/gitea/seed.db"

    def token(self) -> str:
        """A cached API token for this process. Minted on first use, never written to disk.

        CACHED because gitea REFUSES a duplicate token name ("access token name has been used
        already"), and an oracle probes at least twice by construction — once for the premise and
        once to adjudicate. The first draft minted per call with a fixed name and failed on the
        SECOND probe, every time.

        INVALIDATED BY `reset()`, and that is not tidiness: the token is created after `snapshot()`,
        so restoring the seed DELETES it. A cached token that survived a reset would 401 on the next
        probe, and an oracle would report a harness fault as a substrate one — the attribution error
        this whole benchmark exists to avoid.
        """
        if self._token is None:
            self._token = self.mint_token(f"oracle-{os.getpid()}-{self._token_seq}")
            self._token_seq += 1
        return self._token

    def mint_token(self, name: str = "bench") -> str:
        """A fresh API token, minted on demand and NEVER stored.

        The oracles need API access; the alternative to minting is persisting a token in a file or a
        report, and this repo's standing rule is that secrets are env-resolved and never serialized.
        Gitea is happy to issue several, so a caller that needs one asks for one.

        Run as the `git` user: the CLI REFUSES to run as root ("Gitea is not supposed to be run as
        root"), while `docker compose exec` lands as root in this image — so the hop is required
        here for the opposite reason it is required in `assert_writable`.
        """
        proc = _compose("--profile", self.profile, "exec", "-T", "-u", "git", "gitea",
                        "gitea", "admin", "user", "generate-access-token",
                        "--username", self.user, "--token-name", name, "--scopes", "all", "--raw")
        token = (proc.stdout or "").strip().splitlines()[-1:] or [""]
        token = token[0].strip()
        if not re.fullmatch(r"[0-9a-f]{40}", token):
            raise SubstrateError(
                f"{self.name}: token mint did not return a token. Gitea prints its ERRORS on stdout "
                f"too, so a missing user yields a plausible-looking 56-character string — which is "
                f"why this is shape-checked rather than merely non-empty.")
        return token

    def query(self, sql: str) -> tuple:
        """A READ-ONLY SQLite query against the live database, rows as tuples of strings.

        The API is the convenient surface; the database is the authoritative one, and some facts are
        only on the second. Measured: a RUNNING stopwatch appears nowhere in the API except
        `/user/stopwatches`, which carries no id — so an oracle built on it could not tell two apart,
        which is R4.87. The `stopwatch` table has an `INTEGER PRIMARY KEY`.

        Reads only, and safe against the running service: SQLite's WAL permits concurrent readers.
        A WRITE would not be, which is why `_space_issue_timestamps` stops the service and this does
        not.
        """
        proc = _compose("--profile", self.profile, "exec", "-T", "gitea",
                        "sqlite3", "-separator", SQL_SEP, self.db_path, sql)
        return tuple(tuple(line.split(SQL_SEP))
                     for line in (proc.stdout or "").splitlines() if line.strip())

    def _api(self, token: str, method: str, path: str, payload: "dict | None" = None) -> dict:
        req = urllib.request.Request(
            f"{self.url}/api/v1{path}", method=method,
            data=None if payload is None else json.dumps(payload).encode(),
            headers={"Authorization": f"token {token}", "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                body = resp.read().decode() or "null"
        except urllib.error.HTTPError as exc:
            raise SubstrateError(
                f"{self.name}: {method} {path} -> HTTP {exc.code}: {exc.read().decode()[:200]}") from exc
        return json.loads(body)

    def seed(self) -> None:
        """Build the world every Gitea scenario starts from: one admin, one repo, `ISSUES`.

        NOT idempotent and deliberately not: it is called once against a virgin instance, and
        `snapshot()` is what makes it repeatable. A `seed()` that tolerated existing state would
        quietly produce a DIFFERENT world on a second call, which is the drift the template exists
        to remove.

        The admin user comes from the CLI rather than the API because there is no API before there
        is a user. `INSTALL_LOCK=true` in the compose file is what lets that skip the web installer.
        """
        _compose("--profile", self.profile, "exec", "-T", "-u", "git", "gitea",
                 "gitea", "admin", "user", "create", "--admin",
                 "--username", self.user, "--password", GITEA_PASSWORD,
                 "--email", f"{self.user}@example.invalid", "--must-change-password=false")
        token = self.mint_token("seed")
        self._api(token, "POST", "/user/repos", {"name": self.repo, "auto_init": True})
        # Time tracking is OFF by default and `gitea-start-timer` is the scenario that needs it —
        # a real write with NO enclosing form, catchable only by the wire (benchmark-plan §7).
        self._api(token, "PATCH", f"/repos/{self.user}/{self.repo}",
                  {"has_issues": True, "external_tracker": None, "internal_tracker": {
                      "enable_time_tracker": True, "allow_only_contributors_to_track_time": False,
                      "enable_issue_dependencies": True}})
        for title, body, closed in ISSUES:
            issue = self._api(token, "POST", f"/repos/{self.user}/{self.repo}/issues",
                              {"title": title, "body": body})
            if closed:
                self._api(token, "PATCH",
                          f"/repos/{self.user}/{self.repo}/issues/{issue['number']}",
                          {"state": "closed"})
        self._space_issue_timestamps()

    def _space_issue_timestamps(self) -> None:
        """Give every issue a DISTINCT, fixed creation time, one day apart, oldest = #1.

        WHY THIS IS NOT COSMETIC. The seed creates all seven issues in about a second, so six of them
        shared a timestamp and `sort by oldest` was a TIE. Gitea's web UI happens to break that tie by
        id, so the order looked right — but a corpus scenario whose expected answer depends on an
        undocumented tie-break is one Gitea release away from flipping, and this corpus has to stay
        comparable across months. Measured before the fix: 6 of 7 issues at `12:33:21`.

        The API cannot do it — `created_at` is accepted in the request and IGNORED (measured: asked
        for 2020-01-02, got the wall clock) — so this goes through SQLite directly, with the service
        stopped. Stopped, not live: `snapshot()`'s docstring already explains what a copy under a
        running server does to the WAL, and a write is worse than a copy.

        NOTE the API's `sort` parameter is inert in 1.22 regardless — `oldest`, `mostcomment` and
        `leastcomment` all return plain descending id, measured against genuinely distinct comment
        counts. The WEB route sorts correctly, and that is the one the agent drives. An oracle must
        therefore compute expected order from the underlying facts, never by asking the API to sort.
        """
        _compose("--profile", self.profile, "stop")
        try:
            # `[index]` and not `"index"`: `index` is a SQLite keyword, and the double quotes are
            # stripped by the shell layers between here and sqlite3 — measured, it arrived as
            # bare `index` and failed with `near "index": syntax error`. Square brackets are
            # SQLite-legal and have no meaning to the shell.
            sql = (f"UPDATE issue SET created_unix = {ISSUE_EPOCH} + ([index] - 1) * 86400, "
                   f"updated_unix = {ISSUE_EPOCH} + ([index] - 1) * 86400 "
                   f"WHERE repo_id = (SELECT id FROM repository WHERE lower_name = '{self.repo}');")
            _compose("--profile", self.profile, "run", "--rm", "-T", "--entrypoint", "sh", "gitea",
                     "-c", f"sqlite3 {self.db_path} \"{sql}\" && "
                           f"chown \"$(stat -c %u:%g {self.data_dir})\" {self.db_path}")
        finally:
            _compose("--profile", self.profile, "start", check=False)
        self._token = None
        self.await_ready()

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

        A FOURTH, found by driving it (0.121.0, R4.85). The restore ran `cp` inside a
        `run --rm --entrypoint sh` container, which in this image lands as **root** — so the restored
        `gitea.db` came out `root:root` while the service runs as `git`, and SQLite reported
        `attempt to write a readonly database` on the first write. `await_ready()` never noticed,
        because all three of its layers are READS: a `wget --spider` healthcheck and an HTTP GET both
        pass happily against a read-only database. Every scenario runs after a reset, so this was
        every scenario, and B3 would have published the harness's broken substrate as the agent's
        failure — R4.40's shape one level over, in the silent direction.

        Reproduced from a virgin instance before it was fixed: `git:git` on boot, still `git:git`
        after `snapshot()` (which only creates `seed.db`), `root:root` after `reset()`.

        The `chown` is written against the DIRECTORY's owner rather than a hardcoded `git`, so it
        stays right if the image changes uid. `--reference` is GNU and busybox rejects it.
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
                           f"cp {self.seed_path} {self.db_path} && "
                           f"chown \"$(stat -c %u:%g {self.data_dir})\" {self.db_path}")
        finally:
            _compose("--profile", self.profile, "start", check=False)
        self._token = None      # the seed predates it; a survivor would 401 on the next probe
        self.await_ready()

    def assert_writable(self) -> None:
        """Can the SERVICE write its database? The question `await_ready`'s three read-layers cannot ask.

        RUN AS THE OWNING USER, resolved at run time, and that is the whole point: `docker compose
        exec` lands as **root** in this image and root can write anything, so the same `test -w` run
        without the `su` hop passes over a database the service cannot touch. Measured both ways
        before this shipped.
        """
        probe = (f'test -f {self.db_path} || {{ echo ABSENT; exit 3; }}; '
                 f'u=$(stat -c %U {self.data_dir}); '
                 f'su -s /bin/sh "$u" -c "test -w {self.db_path}"')
        proc = _compose("--profile", self.profile, "exec", "-T", "gitea", "sh", "-c", probe,
                        check=False)
        if proc.returncode == 0:
            return
        absent = "ABSENT" in (proc.stdout or "")
        raise SubstrateNotReady(
            f"{self.name}: {self.db_path} is " + ("ABSENT" if absent else "NOT WRITABLE by the user "
            f"that owns {self.data_dir}") + ".\n"
            f"    Refusing rather than proceeding. A read-only database serves every readiness probe "
            f"in this module — they are all reads — and then fails the first WRITE scenario, which "
            f"B3 would score against the agent rather than the harness (R4.85)."
        )


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
    # `/web/login`, NOT `/web/database/selector` (R4.89). The selector is what Odoo serves INSTEAD of
    # the app when it cannot pick a database, so pointing readiness at it means the probe passes on
    # exactly the broken state. Measured: bound to one database `/web/login` is a 4,430-byte login
    # form; unbound it is the 42,559-byte DATABASE MANAGER -- and both are HTTP 200, which is why
    # `assert_bound_to_one_database` reads the BODY rather than trusting the status.
    health_path: str = "/web/login"
    containers: tuple = ("ultracua-bench-odoo-db-1", "ultracua-bench-odoo-1")
    db_name: str = "bench"
    seed_db: str = "bench_seed"
    # `crm` for the lead scenarios, `sale` for the order ones. `base` comes with both but is named
    # so the install is explicit.
    #
    # `sale` IS LOAD-BEARING RATHER THAN CONVENIENT. `odoo-open-record` is the pair-mate of
    # `gitea-open-issue`, and what the pair isolates (benchmark-plan section 7) is that Odoo's
    # version ALSO trips the KEYWORD classifier -- on "order". `safety.MUTATING_KEYWORDS` contains
    # `order`, whose measured false-positive rate on ordinary read-only controls is 28%, so a read
    # scenario about a sale order is the one place this corpus can measure over-gating caused by a
    # WORD rather than by a transport. Navigating to a CRM lead trips nothing, and building the pair
    # on `crm` alone would silently drop that arm while looking complete.
    #
    # STILL A SHORT LIST: every extra module is more demo rows, a slower seed and a larger template.
    # The corpus reads CRM and Sales and nothing else.
    modules: str = "base,crm,sale"

    def _psql(self, sql: str) -> str:
        proc = _compose("--profile", self.profile, "exec", "-T", "odoo-db",
                        "psql", "-U", "odoo", "-d", "postgres", "-tAc", sql)
        return proc.stdout.strip()

    def query(self, sql: str) -> tuple:
        """A READ-ONLY query against the SCENARIO database, rows as tuples of strings.

        The Odoo half of `Gitea.query`, and the reason the R4.86 clock scan finally has real SQL to
        police: every Gitea oracle but one asks the HTTP API, where a clock read is not expressible.

        AIMED AT `db_name`, NOT `postgres`, which is the whole difference from `_psql`. `_psql` runs
        the LIFECYCLE statements -- `DROP DATABASE`, `CREATE DATABASE ... TEMPLATE` -- which cannot
        run from inside the database they are dropping. An oracle asks about the scenario's world and
        must not be able to reach those, so it gets its own accessor rather than a flag on that one.

        Safe against the running service: Postgres readers never block, and the reset stops `odoo`
        for its own reason (a template needs no other session), not for this one.

        `-F SQL_SEP` rather than psql's default `|`, for the reason the constant states -- a
        separator that occurs in the data silently splits one column into two and the oracle then
        compares malformed identities. Customer names in Odoo's demo data are free text.
        """
        proc = _compose("--profile", self.profile, "exec", "-T", "odoo-db",
                        "psql", "-U", "odoo", "-d", self.db_name, "-tA", "-F", SQL_SEP, "-c", sql)
        return tuple(tuple(line.split(SQL_SEP))
                     for line in (proc.stdout or "").splitlines() if line.strip())

    # Odoo's filestore is per-database and lives in the ODOO container, not the db one.
    filestore: str = "/var/lib/odoo/filestore"
    data_dir: str = "/var/lib/odoo"

    def await_ready(self, timeout_s: int = 300, poll_s: float = 2.0) -> None:
        """Everything the base class checks, and THEN that the clock is really pinned (R4.86).

        Ordered after readiness because the probe execs into a running container. The clock check is
        not part of `assert_writable` on purpose — they are different questions with different
        remedies, and collapsing two questions into one predicate is the shape R3.3 spent six passes
        on.
        """
        super().await_ready(timeout_s=timeout_s, poll_s=poll_s)
        self.assert_bound_to_one_database()
        self.assert_clock_pinned()

    def assert_bound_to_one_database(self) -> None:
        """Is the agent's entry point the APP, or Odoo's database manager? (R4.89)

        WHAT WAS MEASURED, and it is worse than a wrong landing page. With two databases visible --
        `bench` and its own reset template `bench_seed` -- and no `--db-filter`, Odoo cannot choose,
        so it serves the DATABASE MANAGER at every entry point an agent could use: `/`, `/web` and
        `/web/login` all returned the same 43,551-byte page listing both databases with **Delete,
        Duplicate, Backup and Restore** beside each. So the benchmark's start page offered a
        one-click deletion of the template every later `reset()` depends on, and any scenario would
        have spent its first turns getting past a chooser that is no part of the task.

        AND NOTHING ABOVE COULD SEE IT. The compose healthcheck, the `min_body_bytes` probe and
        `assert_writable` all pass against the manager page -- it is a healthy 200 with a large body
        on a writable volume. That is R4.85's finding restated: a layer that only asks "did something
        answer" cannot distinguish the app from the page Odoo serves when the app is unreachable.
        This layer reads the BODY for the manager's own master-password field, which is present on
        the manager and absent from the login form.

        TWO CAUSES, DIFFERENT REMEDIES, so they are separated by asking Postgres rather than guessed
        from the page (with `--no-database-list` the manager lists nothing in either case):
          * the scenario database does not exist yet -- `up()` legitimately runs before `seed()`;
          * it exists and the app still will not bind to it -- the db-filter is missing or wrong.

        RAISES IMMEDIATELY rather than spinning, for the reason `assert_writable` already gives:
        neither state self-heals, so burning the timeout would report "not ready in 300s" instead of
        naming which of the two it is.
        """
        try:
            with urllib.request.urlopen(self.url + "/web/login", timeout=10) as resp:
                body = resp.read().decode("utf-8", "replace")
        except (urllib.error.URLError, OSError, ValueError) as exc:
            raise SubstrateNotReady(f"{self.name}: could not read {self.url}/web/login: {exc}") from exc
        # DECIDED ON A POSITIVE, NOT ON AN ABSENCE. `"master_pwd" not in body` alone accepts anything
        # that is not the manager -- an error page, a proxy's 200, a future Odoo whose manager drops
        # that field -- and it accepts it as READY, which is the wrong direction for a readiness
        # layer to fail in. Neither half is sufficient alone: the manager page ALSO carries
        # `name="login"`, inside its create-database modal, which is what made a browser probe fill
        # an invisible field and time out before any of this was understood.
        if "master_pwd" not in body and 'name="login"' in body and 'name="password"' in body:
            return
        exists = self.db_name in self._psql("SELECT datname FROM pg_database").split()
        if "master_pwd" not in body:
            raise SubstrateNotReady(
                f"{self.name}: /web/login is neither a login form nor the database manager "
                f"({len(body)} bytes). Something is answering on {self.url} that is not the Odoo web "
                f"client -- refusing rather than treating an unrecognised page as ready.")
        raise SubstrateNotReady(
            f"{self.name}: /web/login is serving the DATABASE MANAGER, not the app.\n"
            + (f"    The database {self.db_name!r} does not exist yet. Run `seed()` (then "
               f"`snapshot()`) before any scenario -- `up()` deliberately runs first, so this is the "
               f"expected state of a virgin instance and not a misconfiguration."
               if not exists else
               f"    {self.db_name!r} EXISTS and the app still will not bind to it, so the "
               f"`--db-filter` in the compose file is missing or does not match. Until it does, an "
               f"agent's first page is a chooser listing {self.seed_db!r} -- the reset template -- "
               f"beside a Delete button (R4.89).")
        )

    def assert_clock_pinned(self) -> None:
        """`LD_PRELOAD` fails OPEN, which is why this cannot be left to configuration.

        `odoo:17` ships no `libfaketime`, so for the whole of B2's life `ld.so` printed one line to
        stderr — `cannot be preloaded: ignored` — and Odoo ran on the real wall clock while
        `substrate_report()` recorded `faketime_epoch` into the run record. A stated guarantee that
        is false is worse than an absent one. `Dockerfile.odoo` supplies the library and asserts its
        path at BUILD time; this asserts the effect at RUN time, because the two can come apart
        (an image rebuilt from the wrong Dockerfile, a compose override, an env var).

        WHY IT MATTERS, measured on a seeded database rather than argued: Odoo's demo data is
        generated relative to install time, and re-seeding under the pinned clock moved the activity
        deadlines from 2026-08-21..27 to 2026-01-13..19. The Postgres template freezes those stored
        dates; it cannot freeze `now()`, and "overdue" is `deadline < now()`.

        The window is deliberately generous. `faketime` with an absolute `@epoch` starts there and
        then ADVANCES in real time, so a long-lived container legitimately drifts forward; this is
        checking that the clock is pinned to roughly the right place, not that it is frozen solid.
        An unpinned container fails by seven MONTHS, not seven days.
        """
        if not FAKETIME_EPOCH:
            return          # deliberately unpinned: the existing knob IS the acknowledgement
        proc = _compose("--profile", self.profile, "exec", "-T", "odoo",
                        "date", "-u", "+%Y-%m-%dT%H:%M:%S", check=False)
        observed = (proc.stdout or "").strip().splitlines()[-1:] or [""]
        try:
            seen = datetime.datetime.strptime(observed[0], "%Y-%m-%dT%H:%M:%S")
            want = datetime.datetime.strptime(FAKETIME_EPOCH.lstrip("@").strip(), "%Y-%m-%d %H:%M:%S")
        except ValueError as exc:
            raise SubstrateNotReady(
                f"{self.name}: could not read the container clock ({observed[0]!r}) to verify the "
                f"pinned epoch {FAKETIME_EPOCH!r}: {exc}") from exc
        drift = abs((seen - want).total_seconds())
        if drift > CLOCK_DRIFT_TOLERANCE_S:
            raise SubstrateNotReady(
                f"{self.name}: the container clock reads {seen.isoformat()} but the pinned epoch is "
                f"{want.isoformat()} — {drift / 86400:.1f} days apart.\n"
                f"    `LD_PRELOAD` fails OPEN: if libfaketime is missing, ld.so warns and the "
                f"container runs on the real clock while the run record claims the epoch. Rebuild "
                f"with `Dockerfile.odoo`, or set ULTRACUA_BENCH_FAKETIME= to run deliberately "
                f"unpinned (R4.86)."
            )

    def assert_writable(self) -> None:
        """The volume the filestore lives on, tested DIRECTLY — no `su` hop, and that is the point.

        `docker compose exec` lands here as uid 101 `odoo`, which IS the service user (the image ends
        `USER odoo`), so a plain `test -w` asks the real question. Its gitea sibling must hop, because
        exec lands there as root. Same invariant, two probes, for a reason measured rather than
        assumed.

        Aimed at the VOLUME rather than at `filestore/<db>`: `up()` calls `await_ready()` before
        anything is seeded, and a probe pointed at a per-database directory would report ABSENT on
        every fresh instance and spin until the timeout.

        Odoo's own reset is not known to break this — it copies with `cp -a`, which preserves
        ownership, and runs as the service user anyway. Measured after a real reset: `odoo:odoo` on
        both `bench` and `bench_seed`. This guards the copy METHOD changing, which is how the gitea
        side broke.
        """
        proc = _compose("--profile", self.profile, "exec", "-T", "odoo", "sh", "-c",
                        f"test -d {self.data_dir} || {{ echo ABSENT; exit 3; }}; "
                        f"test -w {self.data_dir}", check=False)
        if proc.returncode == 0:
            return
        absent = "ABSENT" in (proc.stdout or "")
        raise SubstrateNotReady(
            f"{self.name}: {self.data_dir} is " + ("ABSENT" if absent else "NOT WRITABLE by the "
            "service user") + ".\n"
            f"    Refusing rather than proceeding: every readiness probe above this one is a READ, "
            f"so an unwritable substrate passes them all and fails the first write scenario, which "
            f"B3 would score against the agent rather than the harness (R4.85)."
        )

    def _authenticate(self, opener) -> int:
        """Log `opener` in as the demo administrator and return its uid. Raises rather than returns 0.

        The credential is `ODOO_LOGIN`/`ODOO_PASSWORD`, which the DEMO DATA owns -- so a database
        seeded without demo data has no such user, and that is the likeliest reason this ever fails.
        """
        payload = json.dumps({"jsonrpc": "2.0", "method": "call", "params": {
            "db": self.db_name, "login": ODOO_LOGIN, "password": ODOO_PASSWORD}}).encode()
        req = urllib.request.Request(f"{self.url}/web/session/authenticate", data=payload,
                                     headers={"Content-Type": "application/json"})
        uid = json.loads(opener.open(req, timeout=60).read()).get("result", {}).get("uid")
        if not uid:
            raise SubstrateNotReady(
                f"{self.name}: could not authenticate as {ODOO_LOGIN!r}. The demo data owns that "
                f"account, so a database seeded WITHOUT demo data has no such user -- see `seed()` "
                f"on the `--without-demo` trap.")
        return uid

    def rpc(self, model: str, method: str, args: list, kwargs: "dict | None" = None):
        """One `call_kw` against the live web client -- the substrate's OWN write path.

        THE LIVENESS PASS NEEDS THIS AND NOTHING ELSE DOES. `--arm-oracles` proves an oracle can say
        NO by substituting a falsified probe; what it structurally cannot see is an oracle whose
        QUERY is aimed at the wrong table, because such an oracle rejects every falsified world
        correctly and reports nothing about the real one. Telling those apart needs a REAL change,
        and it has to be made the way the application makes one -- not with an UPDATE, which would
        skip Odoo's ORM and could leave the row in a state the app never produces.

        Deliberately NOT used by `seed()`. Seeding runs the module installer in a `run --rm`
        container, which is a different mechanism on purpose: a seed that went through the same door
        as the liveness probe could not detect a broken door.
        """
        opener = _anonymous_opener()
        self._authenticate(opener)
        payload = json.dumps({"jsonrpc": "2.0", "method": "call", "params": {
            "model": model, "method": method, "args": args, "kwargs": kwargs or {}}}).encode()
        req = urllib.request.Request(f"{self.url}/web/dataset/call_kw", data=payload,
                                     headers={"Content-Type": "application/json"})
        body = json.loads(opener.open(req, timeout=120).read())
        if "error" in body:
            raise SubstrateError(
                f"{self.name}: {model}.{method} failed: "
                f"{body['error'].get('data', {}).get('message') or body['error']}")
        return body.get("result")

    #: `(src|href)="/web/assets/..."` as the served HTML spells it. Both attributes, because the
    #: CSS bundle arrives on a `<link href>` and the JS on a `<script src>`.
    _ASSET_RE = re.compile(r'(?:src|href)="(/web/assets/[^"]+)"')

    def warm_assets(self) -> int:
        """Compile the web client's asset bundles NOW, so no scenario pays for it. Returns how many.

        THE PLAN PREDICTED "tens of seconds"; THE MEASUREMENT SAYS 2.42 s, AND BOTH HALVES MATTER.
        `realistic-benchmark-plan.md` lists "the first request after a reset recompiles asset bundles
        (tens of seconds -> false drift on whichever scenario runs first)" as a MAJOR risk and asks
        for a warmup request. Measured at 0.124.0 over `web.assets_web.min.{css,js}` (4.9 MB across
        two bundles): cold 1.17 s + 1.30 s, warm 0.02 s + 0.03 s -- so +2.42 s per reset, not tens of
        seconds. Small, REAL, and charged entirely to whichever scenario runs first, which is what
        makes a per-scenario wall-clock number depend on the order the corpus happens to run in.

        WHY IT IS A METHOD AND NOT A ONE-OFF. The bundles are `ir_attachment` rows whose BYTES live
        in the filestore -- half in Postgres, half on disk, like every other piece of Odoo state --
        and the template captures whatever existed when `snapshot()` ran. Called at the end of
        `seed()` it freezes the template WARM: measured, `bench_seed` then carries FIVE `ir.ui.view`
        attachments -- the three `web.assets_frontend*` bundles the login page needs and the two
        `web.assets_web` ones the backend needs -- and the per-reset penalty falls to +0.00 s. Called
        again at the end of `reset()` it turns that from an assumption into a guarantee: 0.03 s
        against a warm template, and it pays the 2.4 s itself against a cold one.

        BOTH SIDES OF THE LOGIN WALL ARE WARMED, because both are on the agent's path: it lands on
        `/web/login` (three `web.assets_frontend*` bundles) and ends up in the backend (two
        `web.assets_web` ones). Warming only one leaves the other cold on the first scenario.

        THE SENSOR IS A DIFFERENTIAL, AND THE FIRST VERSION WAS NOT. It used to raise only when NO
        bundle was found, on the measured claim that an anonymous `/web` referenced none -- which was
        true of the DATABASE MANAGER page Odoo was serving before R4.89 was fixed, and stopped being
        true the moment it served a real login page. A broken session would then have warmed the
        three frontend bundles, returned 3, and never raised, while the 4.9 MB backend pair -- the
        entire cost this method exists to absorb -- stayed cold. So the check is now that the
        AUTHENTICATED page reaches bundles the anonymous one cannot, which is the property meant all
        along and needs no bundle name written down to survive an Odoo upgrade.
        """
        opener = _anonymous_opener()

        def bundles_on_web() -> set:
            html = opener.open(f"{self.url}/web", timeout=60).read().decode("utf-8", "replace")
            return set(self._ASSET_RE.findall(html))

        try:
            anonymous = bundles_on_web()
            self._authenticate(opener)
            backend = bundles_on_web()
            for url in sorted(anonymous | backend):
                opener.open(self.url + url, timeout=600).read()
        except (urllib.error.URLError, OSError, ValueError) as exc:
            raise SubstrateNotReady(
                f"{self.name}: the asset warmup could not reach the web client: {exc}") from exc
        if not backend - anonymous:
            raise SubstrateNotReady(
                f"{self.name}: authenticated and anonymous /web referenced the SAME bundles "
                f"{sorted(anonymous)}, so the session never reached the backend and the bundles this "
                f"method exists to warm are still cold.\n"
                f"    Refusing rather than reporting a count. A warmup that silently warms the wrong "
                f"thing is a guarantee that is false, and the cost it was meant to absorb reappears "
                f"on whichever scenario runs first (R4.86's shape)."
            )
        return len(anonymous | backend)

    def seed(self) -> None:
        """Create `bench` from scratch, WITH demo data, under the pinned clock.

        THE FLAG TRAP, and it cost a whole seed to find: `--without-demo=False` DISABLES demo data.
        Odoo reads any non-empty value as a comma-separated list of modules to skip demo for, so the
        string "False" is just a module name nobody has and the switch is simply ON. The first seed
        written that way produced **0 leads and an empty world** — which is R4.40's near-empty
        observation arriving from the harness rather than from impatience, and it would have been
        scored as the agent failing to find anything. OMITTING the flag is the only spelling that
        means "load it", so it is omitted here and asserted by a test.

        Run in a `run --rm` container rather than `exec`, because the service holds sessions on the
        database and `DROP DATABASE` fails with `There are 2 other sessions using the database` —
        the same reason `snapshot()` and `reset()` stop it.

        The clock matters here and not only at read time: demo data is generated RELATIVE TO INSTALL
        TIME. Seeding under an unpinned clock bakes today's dates into the template, so
        `assert_clock_pinned` runs FIRST rather than after — by the time the rows exist it is too
        late, and `reset()` would faithfully restore the wrong world forever (R4.86).
        """
        self.assert_clock_pinned()
        _compose("--profile", self.profile, "stop", "odoo")
        try:
            self._psql(f'DROP DATABASE IF EXISTS "{self.db_name}"')
            _compose("--profile", self.profile, "run", "--rm", "-T", "--entrypoint", "odoo", "odoo",
                     "-d", self.db_name, "-i", self.modules, "--stop-after-init",
                     "--db_host=odoo-db", "--db_user=odoo", "--db_password=odoo", timeout=1800)
        finally:
            _compose("--profile", self.profile, "start", "odoo", check=False)
        self.await_ready()
        # WARM BEFORE THE SNAPSHOT, or every reset for the life of this template pays the compile.
        self.warm_assets()

    def snapshot(self) -> None:
        """Freeze the seeded world: a template database plus its filestore.

        Runs AFTER `seed()` has warmed the asset bundles, which is what makes the template warm. The
        bundles are `ir_attachment` rows (Postgres) whose bytes are in the filestore (disk), so both
        halves of this method are needed to carry them -- the filestore copy is not only about
        attachments a scenario opens."""
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

        DRIVEN FOR THE FIRST TIME AT 0.124.0, AND IT WORKS. It carried a standing note saying it was
        reviewed code nobody had run -- written on a host with 308 MB free, where any timing would
        have meant nothing. Measured now on an unloaded host: **13.5-16.1 s per reset** over eight
        consecutive runs, template and filestore consistent afterwards; the top of that range
        includes the asset warmup this slice added. The plan budgeted 30-60 s. For scale, the rest of
        the lifecycle measured 87.6 s to `seed()` (with `sale`) and 14.0 s to `snapshot()`.

        AND DRIVING IT FOUND THE ONE THING REVIEW COULD NOT: the template is COLD. Restoring it
        discards the compiled asset bundles, so the first backend page load after every reset paid
        +2.42 s. See `warm_assets`, which now absorbs that at both ends.
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
        # 0.03 s against a warm template, 2.4 s against a cold one -- and it is this SECOND call that
        # makes the guarantee hold rather than usually hold. A template restored from an older
        # snapshot, or one taken before `seed()` warmed, is cold; without this the cost lands on the
        # first scenario and reads as that scenario being slow.
        self.warm_assets()


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
