"""CI provisioning: quiet is an allowlist, and the browser install carries no apt.

WHY THIS FILE EXISTS. Over 2026-08-18/19, EIGHT of fifty-eight ubuntu `test` jobs died at the
25-minute job wall. Every one died inside `playwright install --with-deps chromium` -- six while
`apt-get update` hung after the runner-local `azure.archive.ubuntu.com` mirror was `Ign:`-ed and apt
failed over to the public archive with no effective timeout (one stalled 22m55s on a single 126 kB
file), the rest starved by a 21.1 MB font fetch crawling at 26.9 kB/s. Six of the eight ran ZERO
tests. `docs/ci-provisioning.md` holds the measurement, because Actions logs expire.

The observable outcome in every case was `tests (ubuntu-latest 2/2) | cancelled`. Nothing in that
string says which mechanism failed, so it was read -- by a human, and then by `docs/reshape-plan.md`
section 13, which wrote a whole prerequisite step on the reading -- as "the test suite got slower".
The suite was 12.4-13.8 min throughout, flat, and is the FASTEST arm. That mis-attribution is the
defect these cells exist to prevent from recurring: it is the third inviolable ("never silently
return or act WRONG -- fail LOUD") wearing a CI hat.

TWO PROPERTIES, and they are deliberately narrow.

  1. Every authored step is BUDGETED except an allowlist whose duration IS the signal. So a
     provisioning over-run fails at a NAMED step in minutes, not at an anonymous job wall in 25.
     This is `docs/open-defects.md`'s reporting rule -- enumerate the QUIET outcomes, never the loud
     ones -- applied to a workflow file: a step added tomorrow is budgeted by default, and buying
     silence costs an allowlist entry with a written reason.

  2. The browser install is ONE step, shared by both OSes, and runs no apt. This pins the CLASS
     ("unbounded network provisioning on the critical path"), not the instance. Deleting
     `--with-deps` without this cell would leave a fix that any future slice could silently revert
     while every other test in the suite stayed green.

WHAT THESE CELLS DO NOT CLAIM. They do not claim that a job-level `cancelled` can only mean the
suite over-ran. It cannot be made to mean that: the `always()`/`failure()` tail runs past the wall
on a failing job, and GitHub injects steps of its own (`Set up job`, `Post Install uv`,
`Complete job`) that no `timeout-minutes` can reach. An earlier draft of this slice did assert that
converse, with a worked sum, and the sum was wrong on the failure path. The honest property is the
forward one only.

Nothing here launches a browser -- it is text over one file, and belongs in the fast tier.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
CI = ROOT / ".github" / "workflows" / "ci.yml"

# A step begins at this indent inside `steps:`. Nothing else may.
_STEP_START = re.compile(r"^      - (?P<key>[a-z-]+):\s*(?P<rest>.*)$")
_STEP_KEYS = {"name", "run", "uses"}
_JOB_START = re.compile(r"^  (?P<job>[a-z][a-z0-9-]*):\s*$")

# THE ALLOWLIST. One entry, and the value is the reason it is quiet -- not a comment, a required
# field, so that adding an entry costs the same as writing down why. See the module docstring for
# why bounding this particular step was refused rather than merely not-done.
DURATION_IS_THE_SIGNAL: dict[str, str] = {
    "Run the test suite (shard ${{ matrix.group }} of 2)": (
        "this step's duration is the capacity signal itself; a second ceiling underneath the job "
        "wall would be a scheduled future red with nothing to acknowledge it (the D0 shape)"
    ),
}

# System provisioning that must not appear in a `run:` by default. `--with-deps` is the measured
# offender; the others are the same class arriving by a different door.
REFUSED_PROVISIONING = {
    "--with-deps": (
        "installs nine font packages (21.1 MB of CJK/Cyrillic/Thai/Unifont coverage) and ZERO "
        "libraries on this runner image, over an apt mirror that killed 8 of 58 ubuntu jobs"
    ),
    "apt-get": "unbounded network provisioning; apt has no effective timeout on a stalled mirror",
    "apt install": "as `apt-get`",
    "sudo ": "a provisioning escalation on the critical path",
}

# ...and the escape hatch, because a rule with no way to say yes gets deleted by whoever first needs
# one. This is NOT "never apt" -- that would be a refusal standing on one incident, which is the D0
# shape. It is "apt is refused BY DEFAULT, and taking it back on costs a written reason". An entry is
# `(step name, token) -> why`, so it grants the exception to one step rather than to the file.
DELIBERATE_PROVISIONING: dict[tuple[str, str], str] = {}


class Step:
    """One authored step, with the raw block that follows it."""

    def __init__(self, job: str, line_no: int, block: list[str]) -> None:
        self.job = job
        self.line_no = line_no
        self.block = block

    @property
    def name(self) -> str | None:
        for line in self.block:
            m = re.match(r"^\s+(?:- )?name:\s*(.+?)\s*$", line)
            if m:
                return m.group(1)
        return None

    @property
    def label(self) -> str:
        return self.name or self.block[0].strip()

    @property
    def budget(self) -> int | None:
        for line in self.block:
            m = re.match(r"^\s+timeout-minutes:\s*(\d+)\s*$", line)
            if m:
                return int(m.group(1))
        return None

    @property
    def run(self) -> str:
        """Every `run:` line's payload, including a block scalar's body — and NOTHING else.

        The indentation is load-bearing. A step's block runs to the next step, which sweeps up any
        comment paragraph sitting between the two, and an earlier draft appended those to the run
        text: 22 captured lines for a 6-line pwsh script. Since these cells scan `run` for `apt-get`
        and `sudo `, that turns a COMMENT mentioning apt into a failing test — the false-positive
        door `docs/open-defects.md` flags for string containment over a 35%-comment file. A YAML
        block scalar's body is exactly the lines indented deeper than its key, so that is what is
        taken, and the first line at or below the key's indent ends it.
        """
        out: list[str] = []
        body_indent: int | None = None
        for line in self.block:
            if body_indent is not None:
                if not line.strip():
                    continue
                if len(line) - len(line.lstrip()) <= body_indent:
                    body_indent = None  # dedented out of the scalar; a comment cannot re-enter it
                else:
                    out.append(line.strip())
                    continue
            m = re.match(r"^(\s*)(?:- )?run:\s*(.*?)\s*$", line)
            if m:
                payload = m.group(2)
                if payload in ("|", ">", "|-", ">-", "|+", ">+"):
                    body_indent = len(m.group(1))
                elif payload:
                    out.append(payload)
        return "\n".join(out)

    def __repr__(self) -> str:  # pragma: no cover - diagnostics only
        return f"<{self.job}:{self.line_no} {self.label!r}>"


def parse_steps(text: str) -> list[Step]:
    """Every authored step in `ci.yml`, TOTALLY.

    Totality is asserted, not hoped for: a `      - ` line whose key is not one of name/run/uses
    RAISES rather than being skipped. A parser that silently omits the steps it was built to
    enumerate, while reporting that it enumerated everything, is the silent-negative shape this
    file exists to close -- one level down, which CLAUDE.md names as the thing to be most
    suspicious of here.
    """
    lines = text.splitlines()
    starts: list[tuple[str, int]] = []
    job = None
    for i, line in enumerate(lines):
        m = _JOB_START.match(line)
        if m:
            job = m.group("job")
        if line.startswith("      - "):
            s = _STEP_START.match(line)
            if s is None or s.group("key") not in _STEP_KEYS:
                raise AssertionError(
                    f"{CI.name}:{i + 1} starts a step with an unrecognised key: {line.strip()!r}. "
                    f"This parser claims to enumerate EVERY step; teach it the new key rather than "
                    f"letting it skip one silently."
                )
            assert job is not None, f"{CI.name}:{i + 1} is a step outside any job"
            starts.append((job, i))

    steps = []
    for n, (job, i) in enumerate(starts):
        end = starts[n + 1][1] if n + 1 < len(starts) else len(lines)
        steps.append(Step(job, i + 1, lines[i:end]))
    return steps


def unbudgeted(steps: list[Step]) -> list[Step]:
    return [s for s in steps if s.budget is None and s.label not in DURATION_IS_THE_SIGNAL]


def refused_hits(steps: list[Step]) -> list[tuple[Step, str]]:
    """Every (step, token) pair this file refuses — the ONE definition of the predicate.

    Shared by the cell that enforces it and by the arming cell that proves the cell can fail. Two
    copies of a predicate is how an arming proof ends up proving something the real check does not
    do; this repo counts `flow_key` transcriptions by AST for the same reason.
    """
    return [
        (s, bad)
        for s in steps
        for bad in REFUSED_PROVISIONING
        if bad in s.run and (s.label, bad) not in DELIBERATE_PROVISIONING
    ]


@pytest.fixture(scope="module")
def steps() -> list[Step]:
    return parse_steps(CI.read_text(encoding="utf-8"))


def test_every_authored_step_is_budgeted_or_allowlisted(steps: list[Step]) -> None:
    """Quiet is an allowlist. A step added tomorrow is LOUD by default."""
    assert len(steps) >= 10, (
        f"only {len(steps)} steps parsed out of {CI}; the parser has probably stopped matching and "
        f"every cell in this file would pass vacuously"
    )
    missing = unbudgeted(steps)
    assert not missing, (
        "these CI steps run unbounded, so an over-run in one of them dies at the anonymous job wall "
        "instead of naming itself:\n"
        + "\n".join(f"  {CI.name}:{s.line_no} [{s.job}] {s.label}" for s in missing)
        + "\n\nAdd `timeout-minutes:` generous enough that only a hang fires it, or -- if the step's "
        "DURATION is the signal being measured -- add it to DURATION_IS_THE_SIGNAL with the reason."
    )


def test_no_allowlist_entry_names_a_step_that_walked_away(steps: list[Step]) -> None:
    """A scan that NAMES something asserts a negative about a body that can be renamed.

    This is the fourth thing to check when a step is renamed, beside the ratchets, the mutants and
    the goldens: an allowlist entry whose step no longer exists silently grants silence to nothing,
    and would keep granting it if a NEW step later took the old name.
    """
    labels = {s.label for s in steps}
    stale = sorted(set(DURATION_IS_THE_SIGNAL) - labels)
    assert not stale, (
        f"DURATION_IS_THE_SIGNAL names {stale}, which no step in {CI.name} matches any more. The "
        f"step was renamed or removed; update the allowlist rather than leaving a dead entry that "
        f"a future step could inherit."
    )
    for label, reason in DURATION_IS_THE_SIGNAL.items():
        assert len(reason) > 40, f"the allowlist entry {label!r} has no stated reason"


def test_the_browser_install_is_one_step_shared_by_both_oses(steps: list[Step]) -> None:
    """The CLASS pin behind deleting `--with-deps`, not a pin on that one flag.

    Measured 2026-08-19 over 18 ubuntu jobs: with the flag, the install step was bimodal -- twelve
    runs at 0.4-2.3 min and six at 13.7-25.2 min, the latter all dying. The windows arm ran the same
    download without apt: 18/18 at 18-29 s. Deleting the flag deletes the entire observed failure
    surface, and this cell is what stops it coming back.
    """
    installs = [s for s in steps if "playwright install" in s.run]
    assert installs, (
        f"no step in {CI.name} installs a browser, but the suite drives real headless Chromium. "
        f"Either this cell's matcher has gone stale or CI has stopped installing the browser."
    )

    # PER JOB, and every command BYTE-IDENTICAL. This used to read `len(installs) == 1`, which said
    # the right thing while only one job needed a browser and the wrong thing the moment a second did
    # (0.4b's `red-in-ci`, which needs Chromium so that a new browser cell is classified `unusable`
    # rather than counted as evidence for failing on both runs). The defect it was written for was
    # never the COUNT: it was two OS-conditional steps in ONE job whose commands had DRIFTED, so the
    # linux arm carried `--with-deps` and windows stopped being a control for it. Both halves of that
    # are asserted below, and together they are strictly stronger than the count was -- a second job
    # whose install drifts from the first now fails too, which the count could not see.
    per_job: dict = {}
    for s in installs:
        per_job.setdefault(s.job, []).append(s)
    split = {j: v for j, v in per_job.items() if len(v) > 1}
    assert not split, (
        "the browser install is split WITHIN a job: "
        + "; ".join(f"{j} -> " + ", ".join(f"{CI.name}:{s.line_no} ({s.label})" for s in v)
                    for j, v in split.items())
        + ". It was two OS-conditional steps until reshape-plan step 0, and the Linux one carried "
        "`--with-deps`; keeping them collapsed is what makes windows a usable CONTROL for the linux arm."
    )
    commands = {s.run.strip() for s in installs}
    assert len(commands) == 1, (
        f"{len(installs)} browser installs across jobs {sorted(per_job)} run {len(commands)} "
        f"DIFFERENT commands: {sorted(commands)}. One of them can then acquire a flag the others do "
        f"not have, which is exactly how `--with-deps` reached only the linux arm."
    )

    for step in installs:
        assert not any("if:" in line and "runner.os" in line for line in step.block), (
            f"{CI.name}:{step.line_no} makes the browser install OS-conditional again. The two arms "
            f"must run the byte-identical command, or a failure on one stops being evidence about "
            f"the other."
        )
        for bad, why in REFUSED_PROVISIONING.items():
            assert bad not in step.run, (
                f"{CI.name}:{step.line_no} reintroduces {bad!r} on the critical path: {why}. "
                f"See docs/ci-provisioning.md for the measurement, and note that a missing shared "
                f"library fails LOUD (Chromium cannot launch, so every browser test dies at once) "
                f"whereas this flag's cost is silent and unbounded."
            )


def test_no_step_anywhere_smuggles_provisioning_onto_the_critical_path(steps: list[Step]) -> None:
    """The same class, one door over: apt/sudo in ANY step, not just the install one.

    Refused by DEFAULT, not absolutely. `DELIBERATE_PROVISIONING` is how a future need says yes with
    a reason attached — because a rule with no way to grant an exception gets deleted wholesale by
    whoever first needs one, and this register already carries D0 as the standing example of a
    refusal that was right in spirit and too broad in fact.
    """
    hits = refused_hits(steps)
    assert not hits, "\n".join(
        f"{CI.name}:{s.line_no} [{s.job}] {s.label}: {bad!r} — {REFUSED_PROVISIONING[bad]}. "
        f"If this one is deliberate, register it in DELIBERATE_PROVISIONING as "
        f"({s.label!r}, {bad!r}) with the reason, and give the step a `timeout-minutes` tight "
        f"enough that a stalled mirror cannot eat the job."
        for s, bad in hits
    )


def test_no_provisioning_exception_names_a_step_that_walked_away(steps: list[Step]) -> None:
    """The exception list gets the same staleness guard as the budget allowlist.

    A granted exception whose step no longer exists is silence waiting for a step to inherit it: a
    NEW step taking the old name would arrive pre-approved for apt without anyone deciding so.
    """
    labels = {s.label for s in steps}
    stale = sorted((lbl, tok) for (lbl, tok) in DELIBERATE_PROVISIONING if lbl not in labels)
    assert not stale, (
        f"DELIBERATE_PROVISIONING grants exceptions to steps that no longer exist: {stale}. Remove "
        f"them — a dead grant is one a future step can inherit without anybody deciding to give it."
    )
    for key, reason in DELIBERATE_PROVISIONING.items():
        assert len(reason) > 40, f"the provisioning exception {key} has no stated reason"


def test_every_scan_in_this_file_goes_red_when_armed() -> None:
    """ARM THE VIOLATION. A cell that cannot fail is not a test.

    `scripts/prove_red.py` proves the wiring mutants stay dead on every CI run; `test_ratchets.py`
    injects an extra site per ratchet on every fast-tier run. Both are STANDING instruments, not
    one-shot proofs taken once before a PR and never again. This is the same move for this file:
    mutate the workflow in memory SEVEN ways and require the checkers to notice each one — six
    violations that must be caught, and one legal shape that must NOT be. Without it, a later
    refactor could make every cell above unfalsifiable and nothing would say so.

    Each mutation also asserts that it CHANGED something, because a find-text that no longer matches
    reports this cell as stronger than it is — the rule `scripts/prove_red.py` already applies to a
    stale mutation, which it reports as an ERROR rather than a survivor.
    """
    text = CI.read_text(encoding="utf-8")

    # (a) drop a budget -> the budget scan must name that step
    stripped = text.replace("      - name: Install Chromium\n        timeout-minutes: 8\n",
                            "      - name: Install Chromium\n", 1)
    assert stripped != text, "the mutation found nothing to change -- it has gone STALE, which " \
                             "silently reports this cell as stronger than it is"
    caught = unbudgeted(parse_steps(stripped))
    assert [s.label for s in caught] == ["Install Chromium"], (
        f"removing a step's `timeout-minutes` was not caught; got {[s.label for s in caught]}"
    )

    # (b) put `--with-deps` back -> the class pin must REFUSE it. Asserted through `refused_hits`,
    # the same predicate the real cell calls, so this cannot drift into proving a different rule.
    regressed = text.replace("playwright install chromium",
                             "playwright install --with-deps chromium", 1)
    assert regressed != text, "the `--with-deps` mutation is STALE; the install command changed shape"
    hits = refused_hits(parse_steps(regressed))
    assert [(s.label, bad) for s, bad in hits] == [("Install Chromium", "--with-deps")], (
        f"re-adding `--with-deps` was not refused; got {[(s.label, b) for s, b in hits]}"
    )

    # (c) an unrecognised step key -> totality must RAISE, not skip
    with pytest.raises(AssertionError, match="unrecognised key"):
        parse_steps(text.replace("      - name: Install Chromium",
                                 "      - shell: bash\n      - name: Install Chromium", 1))

    # (d) rename the allowlisted step -> the stale-entry check must notice.
    # This is the one direction the pre-fix workflow could NOT prove red (the suite step's name is
    # unchanged there), so it is armed here instead of being taken on trust.
    (allowed,) = DURATION_IS_THE_SIGNAL
    renamed = text.replace(f"- name: {allowed}", "- name: Run the suite", 1)
    assert renamed != text, (
        f"the rename mutation found no step called {allowed!r} -- it has gone STALE, and "
        f"test_no_allowlist_entry_names_a_step_that_walked_away is asserting nothing"
    )
    labels = {s.label for s in parse_steps(renamed)}
    assert allowed not in labels, "the rename did not take"
    assert unbudgeted(parse_steps(renamed)), (
        "after renaming the allowlisted step it became unbudgeted AND unmatched, and neither "
        "checker noticed -- silence would then be granted to a step nobody named"
    )

    # (f) LET THE TWO JOBS' INSTALLS DRIFT APART. This is what the `len(installs) == 1` clause used
    # to buy and lost the moment a second job legitimately needed a browser; it is bought back here
    # as byte-identity, which is the property that actually kept windows a control for linux.
    drifted = text.replace("playwright install chromium",
                           "playwright install chromium --dry-run", 1)
    assert drifted != text, "the drift mutation is STALE; the install command changed shape"
    with pytest.raises(AssertionError, match="DIFFERENT commands"):
        test_the_browser_install_is_one_step_shared_by_both_oses(parse_steps(drifted))

    # (g) SPLIT ONE JOB'S INSTALL IN TWO -- the original defect, now stated per job rather than per
    # file. Duplicated inside the same job, so the file-wide count is irrelevant to catching it.
    one_install = "      - name: Install Chromium\n        timeout-minutes: 8\n" \
                  "        run: uv run --group bench playwright install chromium\n"
    assert text.count(one_install) >= 1, "the split mutation is STALE; the install block changed shape"
    doubled = text.replace(one_install, one_install + one_install, 1)
    with pytest.raises(AssertionError, match="split WITHIN a job"):
        test_the_browser_install_is_one_step_shared_by_both_oses(parse_steps(doubled))

    # (e) THE QUIET DIRECTION, pinned as hard as the loud one. `ci.yml` is ~35% comments and several
    # of them discuss `apt-get` by name -- including the paragraph explaining why it is gone. A scan
    # that matched comment text would fail on its own rationale, so prove it does not: a forbidden
    # token in a COMMENT must be invisible, while the same token in the `run:` must not be.
    commented = text.replace(
        "      - name: Install Chromium\n",
        "      # a comment mentioning apt-get install and sudo apt, which must NOT trip the scan\n"
        "      - name: Install Chromium\n", 1)
    assert commented != text, "the comment mutation is STALE"
    leaked = refused_hits(parse_steps(commented))
    assert not leaked, (
        f"a COMMENT leaked into a run payload and was refused: "
        f"{[(s.label, bad) for s, bad in leaked]}. The scan would then refuse a workflow for "
        f"DESCRIBING the thing it forbids -- including the paragraph in ci.yml explaining why "
        f"`--with-deps` was removed, which is the one comment guaranteed to mention apt."
    )


def test_the_shard_emits_tier_marks_under_a_label_that_ignores_the_attempt(steps) -> None:
    """The 0.8 wiring, derived from the workflow rather than remembered.

    Two properties, and the second is the one that was measurably WRONG when it first shipped.

    (a) The shard step must carry `--emit-marks` and set `ULTRACUA_MARKS_LABEL`, and an upload for
        `tier-marks-*` must exist. Without this cell the flag can be deleted from `ci.yml` and every
        test in the repo stays green — and CI stays green too, because the upload deliberately carries
        `continue-on-error: true` and `if-no-files-found: ignore` (a missing part must not redden four
        merge-gate jobs; the loudness belongs in `tier_marks.py`, which can name the missing shard).

    (b) The LABEL must not vary with `github.run_attempt`. `_tiers.reconcile_attempts` groups a re-run's
        artifacts BY LABEL so a truncated first attempt is superseded by the complete second one — and
        the first draft put `attempt${{ github.run_attempt }}` in the label, giving one shard two
        labels and making that reconciliation inert in production. Measured: it kept 3 of 3. The
        attempt still belongs in the artifact NAME, which is all upload-artifact@v4's immutable-name
        rule needs, and this cell pins exactly that split.
    """
    shard = next((s for s in steps if "--splits" in s.run), None)
    assert shard is not None, "no sharded pytest step in ci.yml; this cell has stopped guarding it"
    assert "--emit-marks" in shard.run, (
        "the sharded run no longer emits tier marks, so CI produces no observations and re-deriving "
        "the manifest goes back to a ~36-minute local suite run (reshape-plan step 0.8)")

    label = next((ln for ln in shard.block if "ULTRACUA_MARKS_LABEL" in ln), None)
    assert label is not None, "the shard emits marks but does not label the part"
    assert "run_attempt" not in label, (
        f"ULTRACUA_MARKS_LABEL varies with the run attempt ({label.strip()}), so a re-run produces TWO "
        f"labels for one shard and `_tiers.reconcile_attempts` reconciles nothing. Keep the attempt in "
        f"the artifact NAME instead — that is what v4's immutable-name rule needs.")
    for axis in ("matrix.os", "matrix.group"):
        assert axis in label, f"the label does not distinguish {axis}, so two shards share one label"

    uploads = [s for s in steps if any("tier-marks-" in ln for ln in s.block)]
    assert uploads, "nothing uploads the tier marks, so nothing can ever pull them"
    assert any("run_attempt" in ln for s in uploads for ln in s.block), (
        "the artifact name does not carry the run attempt; a re-run would collide with the first "
        "attempt's artifact under upload-artifact@v4's immutable-name rule")
