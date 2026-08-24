"""The customer benchmark's harness skeleton (reshape-plan 2.1 / benchmark-plan B2).

WHAT THIS IS AND IS NOT. B2 is the lifecycle and the measuring apparatus: bring a substrate up, prove
it is ready, reset it between scenarios, run one scenario, and record what it COST. It deliberately
does NOT score. The outcome vocabulary — `{ok, wrong_data, refused, over_gated}` for reads and the
six write outcomes — is B3, and inventing a provisional one here would guarantee two vocabularies to
reconcile later. So a run produces raw, checkable FACTS and leaves the verdict empty.

    uv run --group bench python -m benchmarks.customer_bench --substrate gitea --list
    uv run --group bench python -m benchmarks.customer_bench --substrate gitea --scenario smoke-read

NOT WIRED INTO CI, and not runnable key-lessly: a scenario's learn phase drives a real agent. B5 adds
a nightly ubuntu Docker job. Everything Docker-free and key-less is unit-tested in
`tests/test_substrates.py` and `tests/test_boundary_ledger.py`.

THREE THINGS THIS HARNESS REFUSES TO DO, each because the alternative produces a number that lies:

  1. It will not read cost from a `RunRecord`. Cost comes from `BoundaryLedger`, which reads it where
     `Router.complete` meters it. A record is assembled by the code under test; a bench that reads
     the subject's own report of its spend is grading the exam on the answer sheet.
  2. It will not score a scenario whose first observation was a skeleton. That is a harness error
     (R4.40), and `substrates.assert_not_a_skeleton` raises rather than returning a zero that reads
     as a discovery failure.
  3. It will not run a scenario against a substrate it did not reset. `reset()` failing aborts the
     scenario, because the alternative is measuring the previous scenario's leftovers and reporting
     it as this one's result.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import dataclass, field
from typing import Optional
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ultracua import flows                         # noqa: E402

from benchmarks import oracles                      # noqa: E402
from benchmarks import substrates as S              # noqa: E402
from benchmarks.boundary_ledger import BoundaryLedger  # noqa: E402


@dataclass
class Scenario:
    """One measurable task on one substrate.

    `ready` is the PER-SCENARIO readiness hook the plan asks for, and it is separate from the
    substrate's own readiness on purpose: "Gitea is serving" and "the repository this scenario needs
    exists" are different facts, and the second is the one a scenario is actually blocked on. It
    returns True/False and is polled; raising from it is a harness error like any other.
    """

    name: str
    substrate: str
    goal: str
    url_path: str = "/"
    ready: object = None          # Optional[Callable[[], bool]]
    mutating: bool = False


# TWO SMOKE SCENARIOS, which is exactly what B2 is scoped to. They exist to prove the LIFECYCLE end to
# end — reset, readiness, ledger, record — not to measure anything interesting. B4 brings the real
# 14-scenario corpus and its server-side oracles; adding scenarios here before then would produce
# numbers that look like results and are not.
SMOKE = [
    Scenario(name="smoke-read", substrate="gitea", goal="open the repository list and read its count",
             url_path="/explore/repos"),
    Scenario(name="smoke-write", substrate="gitea", goal="create an issue titled 'bench smoke'",
             url_path="/", mutating=True),
]

SUBSTRATES = {"gitea": S.Gitea, "odoo": S.Odoo}


@dataclass
class ScenarioRun:
    """What one scenario produced. FACTS ONLY — the verdict is B3's, and its absence is deliberate."""

    scenario: str
    substrate: str
    wall_s: float = 0.0
    llm_calls: int = 0
    llm_sites: dict = field(default_factory=dict)
    input_tokens: int = 0
    output_tokens: int = 0
    per_model: dict = field(default_factory=dict)
    accounting_failed: bool = False
    llm_accounting: str = "observed"   # "observed" | "unknown" — see BoundaryLedger.observed
    llm_unobserved: list = field(default_factory=list)
    harness_error: str = ""
    # SEPARATE from `harness_error`, because they are different facts about different subjects.
    # "we could not build the world" says nothing about the product; "the agent raised" says a great
    # deal. Sharing one field would make a benchmark unable to distinguish its own failure from the
    # thing it exists to measure — and a failing agent is the NORMAL case a benchmark records.
    agent_error: str = ""
    # THE REFUSAL, STRUCTURED (B3). `agent_error` is `f"{type(exc).__name__}: {exc}"` — a string, and
    # bucketing a benchmark on it would be exactly the "sub-labels from message labels" that
    # `reshape-plan.md` 2.2 forbids. These three come from `flows.outcome_of`, the seam 1.4b built,
    # so B3 buckets on the taxonomy's own vocabulary and a code minted tomorrow arrives named.
    agent_error_code: str = ""
    agent_error_retryable: bool = False
    # RECORDED, AND DELIBERATELY NOT AN INPUT TO ANY VERDICT. `exc.landed` is the product's own claim
    # about its own write — evidence-bounded, never truth (CLAUDE.md). B3's outcome comes from the
    # SERVER; this rides along only so `outcomes.cross_check` can notice the two disagreeing.
    agent_error_landed: bool = False
    # DID THE AGENT RUN AT ALL. `run_scenario` returns early when reset/readiness fails, before
    # `agent_call` is reached — and B2's rule 3 guarantees the substrate is then still carrying the
    # PREVIOUS scenario's records. Without this fact an oracle asked about that world reports them
    # as changed, and B3 mints the previous row's write as this row's `incorrect_target`. A recorded
    # fact rather than `wall_s == 0.0`, because a timer is not a boundary (R4.26).
    agent_ran: bool = False
    # DID THE RUN CLAIM IT FINISHED THE TASK — tri-state, and `None` is the honest default.
    #
    # `run_scenario` deliberately does NOT set it: `agent_call` returning an observation is not a
    # claim about anything, and inferring "it succeeded" from "it did not raise" is exactly what let
    # an LLM arm's ordinary task failure be published as `suppressed` — a write-safety violation.
    # Each ARM sets it (a `replay()` that returns without raising HAS claimed success; an agent's
    # own report of completion is the agent's to give), and until one does, B3 answers the write
    # question `unscored` rather than guessing in the direction of an inviolable.
    claimed_complete: Optional[bool] = None
    # No `outcome` field, and B3 arriving did not change that. B2 records FACTS and B3 mints the
    # verdict onto an `outcomes.Scored` beside this record, never into it — so nothing that reads a
    # raw scenario record can mistake an adjudication for an observation.

    def to_dict(self) -> dict:
        d = dict(self.__dict__)
        d["substrate_world"] = S.substrate_report()
        return d


def run_scenario(scenario: Scenario, *, agent_call, reset: bool = True) -> ScenarioRun:
    """Reset, prove ready, run the scenario under the ledger, record the cost.

    `agent_call` is injected rather than imported so this function is testable without an agent or a
    key: the harness's own logic — reset, readiness, ledger accounting, error attribution — is what
    B2 must get right, and it is exactly the part that a live-agent-only design would leave untested.
    It is called as `agent_call(scenario, url)` and may return an object with `.elements` for the
    skeleton guard.
    """
    run = ScenarioRun(scenario=scenario.name, substrate=scenario.substrate)
    substrate = SUBSTRATES[scenario.substrate]()

    try:
        if reset:
            substrate.reset()
        substrate.await_ready()
        _await_scenario_ready(scenario)
    except S.SubstrateError as exc:
        # A harness error is recorded AS a harness error and the scenario is abandoned. It must never
        # become a scored zero: "we could not build the world" and "the agent failed" are different
        # facts, and only one of them says anything about the product.
        run.harness_error = str(exc)
        return run

    started = time.monotonic()
    with BoundaryLedger() as ledger:
        try:
            run.agent_ran = True          # BEFORE the call: it ran whether or not it returned
            first = agent_call(scenario, substrate.url + scenario.url_path)
            # NOT `if first is not None`. A hook that returns None would have skipped R4.40's guard
            # entirely and produced a clean, scored-looking record — so an agent that cannot show its
            # first observation is treated as unable to prove the page rendered, which is the safe
            # direction. A scenario with genuinely nothing to check passes an object with elements.
            S.assert_not_a_skeleton(first, substrate=scenario.substrate, scenario=scenario.name)
        except S.SubstrateNotReady as exc:
            run.harness_error = str(exc)
        except Exception as exc:  # noqa: BLE001 - the agent failing IS a result, not an abort
            # Recorded rather than propagated. Letting it escape would abort a whole batch on one
            # scenario AND discard the spend already incurred, since `usage()` below would never run —
            # so a crashed run would cost real money and leave no artifact saying so.
            run.agent_error = f"{type(exc).__name__}: {exc}"
            # …and STRUCTURED, through the one seam that classifies both a typed refusal and a bare
            # crash. `outcome_of` returns `code="raised"` for a non-FlowReplayError, so the bench
            # never has to ask `isinstance` and never has to invent a code of its own.
            o = flows.outcome_of(exc)
            run.agent_error_code = o.code
            run.agent_error_retryable = o.retryable
            run.agent_error_landed = o.armed
        finally:
            run.wall_s = round(time.monotonic() - started, 2)
            # In the FINALLY, so spend is recorded on every path out. Cost incurred is cost incurred,
            # whether or not the scenario reached an answer.
            _record_cost(run, ledger)

    return run


def _record_cost(run: "ScenarioRun", ledger: BoundaryLedger) -> None:
    """Copy the ledger's view onto the record, INCLUDING whether it could see everything.

    `llm_calls: 0` and "could not see" are different facts and must not share a representation —
    `RouterWatch` calls the alternative "a CONFIDENT WRONG NUMBER" in its own docstring.
    """
    usage = ledger.usage()
    run.llm_calls = usage.calls
    run.llm_sites = ledger.by_site()
    run.input_tokens, run.output_tokens = usage.input_tokens, usage.output_tokens
    run.per_model = {m: list(v) for m, v in usage.per_model.items()}
    run.accounting_failed = usage.accounting_failed
    run.llm_accounting = "observed" if ledger.observed else "unknown"
    run.llm_unobserved = list(ledger.unobserved)


def _await_scenario_ready(scenario: Scenario, timeout_s: int = 60, poll_s: float = 1.0) -> None:
    """The per-scenario hook. Substrate-ready is not scenario-ready.

    "Gitea is serving" does not mean "the repository this scenario opens exists". Polling the
    scenario's own precondition is what stops a run being scored against a world that has not
    finished being built — the same failure as R4.40, one level out from the page.
    """
    if scenario.ready is None:
        return
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if scenario.ready():
            return
        time.sleep(poll_s)
    raise S.SubstrateNotReady(
        f"{scenario.substrate}/{scenario.name}: the scenario's own readiness hook never passed within "
        f"{timeout_s}s. The substrate is serving but the world this scenario needs does not exist, so "
        f"running it now would score the harness's seeding against the agent."
    )


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--substrate", choices=sorted(SUBSTRATES), default="gitea")
    ap.add_argument("--scenario", default=None)
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--up", action="store_true", help="bring the substrate up and report readiness")
    ap.add_argument("--arm-oracles", dest="arm_oracles", action="store_true",
                    help="prove every oracle can say NO; a scored run refuses without this")
    args = ap.parse_args(argv)

    if args.list:
        # THE CORPUS, NOT THE SMOKE PAIR. `SMOKE` is gitea-only, so `--list --substrate odoo` printed
        # NOTHING and exited 0 — a quiet empty, which is the shape R3.9/CLI-1 is about: an operator
        # surface that answers "there is nothing here" and "I looked in the wrong place" with the
        # same silence. B4's corpus exists now, so this lists what a scored run would actually drive.
        from benchmarks import corpus                      # local: see the gate below on the cycle

        entries = corpus.for_substrate(args.substrate)
        for e in entries:
            kind = "write" if e.truth.mutating else "read "
            flag = "  [keyword]" if e.keyword_read else ""
            print(f"{e.scenario.name:24} {kind}  {e.scenario.goal}{flag}")
        print(f"{len(entries)} scenario(s) on {args.substrate}.")
        return 0

    if args.arm_oracles:
        # THE GATE, and it runs BEFORE anything is scored (benchmark-plan §7, gate 1). An oracle
        # that cannot fail approves everything and publishes a perfect availability rate; every
        # number downstream inherits it. Offline by construction — falsified probes rather than a
        # mutated substrate — so it costs nothing and can gate every run rather than a nightly one.
        substrate = SUBSTRATES[args.substrate]()
        # IMPORTED HERE, NOT AT MODULE SCOPE: `corpus` imports `Scenario` from this module, so a
        # top-level import would be a cycle. The direction is the honest one — the runner depends on
        # the corpus, never the reverse — and this is the only place the runner needs it.
        from benchmarks import corpus

        try:
            report = oracles.arm_oracles(corpus.oracles_for(args.substrate, substrate))
        except oracles.OracleError as exc:
            print(str(exc), file=sys.stderr)
            return 1
        for name, label, satisfied in report:
            print(f"  {name:16} {label:36} rejected={satisfied is False}")
        print(f"{len(report)} falsification(s), every one rejected. The oracle set is armed.")
        return 0

    if args.up:
        substrate = SUBSTRATES[args.substrate]()
        took = substrate.up()
        print(json.dumps({"substrate": args.substrate, "ready_after_s": round(took, 1),
                          "world": S.substrate_report()}, indent=1))
        return 0

    print("B2 is the lifecycle and the apparatus; it does not score.\n"
          "A scenario needs a real agent (and a key), which B4 wires up with its oracles.\n"
          "What you can drive today: `--up`, `--list`, and `run_scenario(..., agent_call=...)`.",
          file=sys.stderr)
    return 2


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
