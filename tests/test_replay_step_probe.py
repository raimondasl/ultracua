"""The replay-step probe's pure parts. (R4.143.)

The probe spends money and needs live substrates, so what can be checked offline is: it patches the
binding the CALLER holds, it never invents an absence, and it refuses an unknown scenario. Each of
those has cost this project a slice somewhere.
"""

from __future__ import annotations

import inspect

import pytest

from benchmarks import replay_step_probe as P


def test_it_patches_the_binding_the_CALLER_holds_not_the_definition() -> None:
    """S14's lesson, which cost a slice: `from .locators import resolve` binds the function OBJECT.

    Patching `ultracua.locators.resolve` leaves `flow.resolve` pointing at the original, so the spy
    never runs and the probe reports an empty call list -- a confident "nothing resolved" for a
    replay that resolved everything. The same shape let a `no_llm` fixture build 105 real Anthropic
    clients while all 25 cells passed.
    """
    src = inspect.getsource(P)
    assert "flow_mod.resolve = spy" in src, "the probe stopped patching flow.py's binding"
    assert "locators.resolve" not in src, (
        "the probe reaches for the DEFINITION module; that binding is not the one flow.py calls")
    assert "finally:" in src and "flow_mod.resolve = real_resolve" in src, (
        "the patch must be restored in a `finally`, or one probe run silently instruments every "
        "later one in the same process")


def test_an_ABSENT_sink_is_reported_as_None_and_never_as_False() -> None:
    """`saw_candidates` has three states and only two are about the page.

    False means the page did not answer the recorded spec. None means the CALLER passed no sink, so
    nothing was measured. Collapsing them publishes "the element was absent" for a call that never
    carried the sensor -- which is this register's single most repeated defect, and the whole reason
    `saw_candidates` exists (R4.115).
    """
    src = inspect.getsource(P.probe)
    assert '(sink or {}).get("saw_candidates")' in src, (
        "the sink read changed shape; `.get` returning None for a missing sink is what keeps "
        "'not measured' distinct from 'measured absent'")
    assert 'saw_candidates=False' not in src and "saw_candidates', False" not in src, (
        "a default of False would invent an absence for an unmeasured call")


def test_the_page_is_photographed_at_the_FIRST_failure_only() -> None:
    """A later resolve runs against a page the earlier failure already changed.

    The diagnosis that produced R4.143 turned on the page at the moment step 2 found nothing -- the
    leads LIST, no form. Overwriting that with a later snapshot would report the wrong scene, and
    photographing every failure would make a long replay unreadable.
    """
    src = inspect.getsource(P.probe)
    assert "page_at_failure is None" in src, (
        "the probe no longer records only the FIRST failing resolve")


def test_a_dead_page_does_not_replace_the_diagnosis() -> None:
    """`page.evaluate` on a navigating or closed page raises. The probe must still return its call
    list -- the resolve trace IS the finding, and losing it to a failed screenshot would be the
    instrument destroying its own evidence."""
    src = inspect.getsource(P.probe)
    assert "except Exception" in src and '"error"' in src, (
        "a failed page read must be recorded as an error row, not raised over the diagnosis")


def test_it_refuses_a_scenario_the_corpus_does_not_have() -> None:
    """Derived from the corpus, so a scenario added tomorrow is accepted without editing a list."""
    with pytest.raises(SystemExit) as ei:
        P.main(["--scenario", "not-a-scenario"])
    assert "no such scenario" in str(ei.value)
