"""The survey's target set is the instrument, so its properties are pinned like any other.

`benchmarks/web_survey.py` exists to answer one question: do this project's observation findings hold
outside its own two substrates, or were they tuned to Odoo? The ANSWER is only as good as the target
set, and a target set degrades in specific ways -- families collapse toward one framework, a URL
drifts onto a marketing page, someone adds a site that needs a login. These cells hold the shape.

OFFLINE. Nothing here loads a page; the survey itself needs the network and the two containers.
"""

from __future__ import annotations

import json
from pathlib import Path
from urllib.parse import urlparse

from benchmarks import web_survey as WS

BASELINE = Path(__file__).resolve().parent.parent / "baselines" / "web_survey.json"


def test_every_target_says_why_it_is_here() -> None:
    """A target with no stated reason cannot be re-justified, and the set then drifts by accretion."""
    for t in WS.TARGETS:
        assert t.why and len(t.why) > 40, f"{t.name} has no real reason recorded"
        assert t.family, f"{t.name} declares no rendering family"


def test_the_families_are_diverse_enough_to_answer_the_question() -> None:
    """THE WHOLE POINT. The corpus had two families and could distinguish server-rendered from SPA
    and nothing else -- so 'this generalizes' was extrapolation from ONE SPA. A survey that collapsed
    back toward one framework would silently reproduce that limitation while looking broader."""
    families = {t.family for t in WS.TARGETS}
    spa = {f for f in families if f.startswith("SPA")}
    server = {f for f in families if f.startswith("server")}
    print(f"    {len(families)} families: {sorted(families)}")
    assert len(spa) >= 4, f"only {len(spa)} SPA families; 'SPA' must not mean one framework again"
    assert len(server) >= 3, f"only {len(server)} server-rendered families"
    assert len(WS.TARGETS) >= 12, "fewer than a dozen targets"


def test_no_public_target_needs_a_login() -> None:
    """The survey must stay free and side-effect-free: one GET per target, no credentials, no forms.
    Only the two LOCAL substrates authenticate, through the product's own `refresh_auth`."""
    for t in WS.TARGETS:
        if t.auth:
            assert t.local, f"{t.name} is public and declares auth={t.auth!r}"
            assert t.auth in ("gitea", "odoo"), f"unknown substrate {t.auth!r}"


def test_local_targets_point_at_localhost_and_public_ones_do_not() -> None:
    """A public target on localhost would be measured against whatever happens to be listening."""
    for t in WS.TARGETS:
        host = urlparse(t.url).hostname or ""
        is_local = host in ("localhost", "127.0.0.1")
        assert is_local == t.local, f"{t.name}: local={t.local} but host={host!r}"


def test_the_calibration_targets_are_the_corpus_pages() -> None:
    """THE CHECK THAT MAKES THE OTHER TWELVE ROWS BELIEVABLE, and it was WRONG on the first run.

    The Gitea and Odoo rows exist to reproduce numbers already measured elsewhere -- Odoo at 19%
    unnamed. The first draft pointed Gitea at `/bench/bench/issues` (a 404) and ran both without a
    session, so Odoo redirected to `/web/login`: the calibration rows measured an error page and a
    login form, and reported Odoo at 6 interactables where the corpus says 80. Every comparison
    against them would have been meaningless, and the aggregate would have looked fine.
    """
    from benchmarks import corpus

    by = {t.name: t for t in WS.TARGETS}
    gitea, odoo = by["gitea"], by["odoo"]
    assert gitea.auth == "gitea" and odoo.auth == "odoo", "a calibration row lost its session"
    gitea_paths = {e.scenario.url_path for e in corpus.for_substrate("gitea")}
    odoo_paths = {e.scenario.url_path for e in corpus.for_substrate("odoo")}
    assert any(gitea.url.endswith(p) for p in gitea_paths), (
        f"the Gitea target {gitea.url!r} is not a corpus page, so its numbers are not comparable "
        f"to the findings it calibrates. Corpus paths: {sorted(gitea_paths)}")
    assert any(odoo.url.endswith(p) for p in odoo_paths), (
        f"the Odoo target {odoo.url!r} is not a corpus page. Corpus paths: {sorted(odoo_paths)}")


def test_target_names_are_unique() -> None:
    names = [t.name for t in WS.TARGETS]
    assert len(names) == len(set(names))


def test_the_viewport_is_pinned() -> None:
    """Both the fold and the cap questions are functions of viewport. A survey run at another size
    answers a different question while printing the same table."""
    assert WS.VIEWPORT == (1280, 720)


def test_the_committed_baseline_matches_the_declared_targets() -> None:
    """The artifact and the instrument must not drift apart: a row for a target that no longer
    exists, or a target with no row, means the published numbers describe a different set."""
    if not BASELINE.exists():
        return
    rows = json.loads(BASELINE.read_text(encoding="utf-8"))["rows"]
    assert {r["name"] for r in rows} == {t.name for t in WS.TARGETS}, (
        "baselines/web_survey.json describes a different target set than benchmarks/web_survey.py")


def test_the_baseline_records_the_cap_it_was_measured_against() -> None:
    """`max_elements` decides the CAP column and bounds every per-page count, so a baseline that
    does not record it cannot be compared with a later run."""
    if not BASELINE.exists():
        return
    d = json.loads(BASELINE.read_text(encoding="utf-8"))
    assert isinstance(d.get("max_elements"), int) and d["max_elements"] > 0
