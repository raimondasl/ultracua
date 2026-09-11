# The test suite never found a bug

*What ultracua's four audit rounds actually taught, written at close-out 2026-09-10 at 0.184.0, and
corrected at 0.185.0 by the adversarial pass over its own plain-language rewrite. That pass found
**ten errors in this document** — a ceiling of 4/7 described as two rows failing rather than three,
a stale count attributed to the register when it was the README's, a distance given as twenty lines
that is 1,282, "three changes" over a list of four, "a majority" over 18 of 45, two findings named
as blocked where there are three, "years apart" over 33 days, "over ten releases" over roughly
forty, "months afterwards" over two weeks, and a severity breakdown summing to 73 under a headline
of 75 — plus two figures that had simply gone stale.*

> **This is the version for someone who already knows this codebase.** It names finding ids, decision
> letters, function names and instruments directly. [method.md](method.md) is the same argument and
> the same numbers written for a reader who does not — start there unless you want the identifiers.

> **Every number here is derived from this repository by a command that was run, not recalled.** That
> is not a stylistic choice. In the week this was written I shipped and then corrected a speedup
> figure the artifact prices differently, a metadata field that does not exist, an occurrence count
> off by one, and an open-finding count off by one. A piece *about* rigour containing an underived
> number is worse than no piece. Where a number is not derivable I say so.

ultracua is a Computer Use Agent: learn a browser flow once with an LLM, replay it deterministically
with no planning model in the loop, fail loud on drift. Active development stopped on 2026-09-09. The
product was a usable prototype of a real pattern and is archived as one; the [close-out
banner](open-defects.md) says what that means.

This document is about the other thing it produced, which is more portable than the code: **a way of
working that assumes your own output is wrong, and builds instruments to find out where.**

---

## 1. The number that motivates everything

Four adversarial audit rounds, in a project that ran 87 days:

| round | findings | state today |
|---|---|---|
| 1 (2026-07-31) | 20 | all fixed |
| 2 (2026-08-02) | 10 | all fixed |
| 3 (2026-08-03) | 13 numbered | **2 open** (R3.2, R3.7) |
| 4 (standing, per-slice) | **160** | **73 open / 83 fixed / 4 parked** |

**≈203 findings. Not one was discovered by the test suite.**

That suite is not weak. At close-out it is **2,495 collected tests** — 1,899 browser-free, 596 driving
real headless Chromium — against **17,282 lines** of `src/`. The test-to-source line ratio is **3.29**.
There are **14 mutation registries holding 113 registered mutants**, six AST ratchets, a 187-row drift
benchmark, two committed goldens, and a CI gate running the whole suite on two operating systems.

It caught none of the 203.

### The precise version of that claim, with its exception named

The suite did not *discover* defects. It did, constantly, **catch the slice being written** — which is
a different job, and the one it is actually good at. In the final week alone the counting guard caught
me twice inside the commit that was correcting counts; the settle guard has refused a fourth call site
three separate times; the plan-state guard forced an artifact to be renamed to what it was; the
direct-import guard found three more violations on its first run, two of them pre-existing.

So:

> **A regression suite is a ratchet, not a detector.** It proves that what you already understand
> stays fixed, and it stops you shipping the mistake you are making right now. It cannot fail for a
> defect nobody has conceived of, because every test in it encodes a conception.

Mutation testing does not rescue this, and it is worth being clear why, because mutation testing is
what you reach for when you distrust a suite. **It probes guards that exist.** Every defect in this
register was a guard that was *missing*. A mutant is killed by the cell written for its neighbour; a
missing guard has no neighbour. Measured here: the nine known mutants — one per defect class the
project has shipped — are **9 killed, 0 survived**. That result is real and it says nothing whatever
about the 203.

---

## 2. What did find them

An **adversarial pass aimed squarely at the new code, before the PR opens.** Not review of the diff:
a separate effort whose only job is to break the thing just built, with the tree committed and the
auditor unable to edit it.

The record, and the reason it became doctrine: **four changes passed the full suite, passed the drift
benchmark with every invariant holding, passed their own purpose-built property matrices, and were
critically wrong.** The reverted 0.73.0 redesign (754 tests green). The parked 0.76.0 branch (785
tests green, benchmark byte-identical). Both attempts at R3.7 — the second of which additionally
showed 0-LLM survival *up* at every mutation intensity with zero rows regressed, and produced a silent
wrong-record bind.

Hence the rule this repository states first and loudest:

> **Green is not evidence in this codebase.**

Three close-out audits were run in the final week. Each found something real, and most of what each
found was mine:

- **Day 1** (making the front door true): the headline speed claim traced to learn-vs-replay ratios on
  two containers, three months old, with **no artifact committed anywhere in the tree**. Also: a
  "refused by design" list that inverted the truth on three entries, turning an open silent hole into
  a safety feature on the front page.
- **Day 2** (freezing the programme): two fabrications — a `meta` provenance field that does not exist
  in either baseline, and a fixture-speedup range whose lower bound appears in no artifact. Plus an
  open-finding count wrong in all four banners, while the same banner named the two findings it
  omitted.
- **Day 3** (the release): `anthropic>=0.109.2` with no upper bound resolved to a version whose
  `messages.create` no longer accepts `temperature`, which the authoring path passes on every call.
  **Every learn of a freshly-installed release would have died on its first model call** — permanently,
  on an index that cannot be corrected.

**The audits are not clean instruments either.** The Day 2 pass returned **45 findings — 27 confirmed,
16 partial, 2 refuted** on hostile re-derivation, where *partial* means the symptom was real and the
stated mechanism or severity was not. That ratio is the honest one: **18 of 45 — two in every five —**
of what an adversarial pass returns needs correcting before it is acted on — which is why every finding goes through a hostile
second pass first, and why this project's standing rule is *reproduce a finding yourself before fixing
it*. A fix built on a wrong diagnosis is worse than none.

---

## 3. Truth-as-data: make the tree adjudicate the document

The README's register row said "nine remain open" for **five weeks** after that stopped being true —
prose in a hand-written table, which is why the register's own machine-checked index never caught it.
A completed
1,750-line step was still marked "never started" three days and six merged slices later, and came
within one instruction of being built a second time.

The response was not to be more careful. It was to **move every count into data and render the prose
from it**:

| artifact | owns | checker |
|---|---|---|
| `docs/register/state.json` | every round-4 finding's id/status/summary | `scripts/render_register.py --check` |
| `docs/plan/state.json` | programme status, with each step's declared artifact | `tests/test_plan_state.py` |
| `tests/.browser_tests.json` | which tests launch a browser | derived by probe; CI `fast` job |
| `tests/.ratchets.json` | six code shapes that may only shrink | `test_every_ratchet_holds` |
| `tests/mutations/*` | 113 mutants that must stay dead | `prove_red`, `mutation_sweep` |
| `baselines/*.json` | measured benchmark records | `test_the_baseline_is_current` |
| `tests/goldens/*` | hook-fire counts, auth-retry truth table | diffed cell by cell |

Three principles fell out, each learned by shipping its opposite:

**Nobody types a count.** The register's index table is *rendered*; a finding is filed by adding a row
and re-rendering, so the totals cannot drift from the rows. `test_register_count.py` then asserts the
prose against the rows — and it caught a line going stale *inside the slice that wrote it*.

**Assert both directions.** `test_plan_state.py` requires a `done` step to have its named artifact
**and** a `pending` step to not have it. The second direction is the one that failed. Same shape in the
allowlist guards: an entry naming a row that no longer exists is a silencer with nothing to silence,
and it stays green while the hole it documents may have closed.

**Quiet is an allowlist.** Enumerate the outcomes that are allowed to be silent, never the ones that
should be loud — so a status added tomorrow is loud by default. This exists because a fleet supervisor
had two alerting channels that each tested `status == "failed"`, and a third bucket satisfying neither
was invisible in both.

### What truth-as-data cannot do, measured

**Every guard here reads numbers. None reads a sentence.** On the last working day I pointed the
method at the tree it was about to describe and found **ten live falsehoods**, of which the machinery
could catch zero:

- A fabricated `meta` field surviving **inside the register finding about artifacts outliving their
  corrections** — **1,282 lines** from the same file's banner that refutes it, after a commit claiming it was
  "corrected everywhere."
- A finding whose **headline states the opposite of its own retraction**, 3,500 characters further
  down the same entry. A correction appended to a long entry does not reach its headline, and the
  headline is what gets read.
- One file giving **two different answers to the same question** 753 lines apart (a benchmark budget
  as 220s in one place, 260s in another; the constant says 260).
- Two stale counts **inside the checkers' own docstrings** — "only 5 of 53 rows state a severity" in
  the renderer built so nobody types a count. It is 94 of 160.

The machinery is genuinely good at what it covers. The residual is exactly the part a machine cannot
read, and the only instrument that has ever caught it is another reading.

---

## 4. The most transferable idea: the overloaded sensor

One value meaning two things, where the code cannot tell which. **Four independent surfaces in this
codebase, found 2026-08-03, 2026-08-28, 2026-08-29 and 2026-09-05 — two of them a day apart — all the
same fault:**

| sensor | means | and also means |
|---|---|---|
| `anchor_id = None` | this row has no discriminating token *(accepted residual)* | I looked in the wrong container *(silent disarm)* |
| `resolve() → None` | nothing on the page matched | I found it and **refused** *(four distinct safety refusals)* |
| `state_changed` | the page is different | the page has not finished rendering |
| `precond_scope` | the target's enclosing form | the entire document, because `closest()` matched nothing |

Each produced a real defect, and each defeated the obvious fix, because **the obvious fix is a better
inference over the same overloaded value.** Retry while `resolve()` returns `None` — measured, that
waits out a *competing* candidate and binds the wrong record under the recorded row's idempotency key.
Reject positional row tokens — measured, that sets `anchor_id` to `None`, which the resolver reads as
*no guard*, moving the harm from "the guard agreed wrongly" to "the guard never ran."

### The gate that came out of it

**D5, the two-strikes rule:** when two fix shapes for one finding have been *built and measured wrong*,
the third attempt must change **what the decision is made from** — inference → a human's verdict, or
inference → a loud refusal — rather than refine the inference. Moving within a class (a better
constant, a better window, a better in-page probe) is not a third attempt; it is the second one again.

A strike is a fix that was built and measured wrong. A hypothesis discarded at design time is free.
The count is per finding, and it does not reset when the person changes.

**Three findings are blocked by it**: R3.7 (two attempts), R4.5 (two, and the only entry whose
`attempts` are recorded as data), and R3.2 (three).

**And when the sensor class really does change, it works.** `resolve() → None` was split by
`saw_candidates`, set by the resolver's own candidate walk: *false* means nothing answered (wait and
retry), *true* means the page answered and the resolver declined (fail loud, immediately, no wait).
All four safety refusals kept their instant failure; the unpainted-page case got its retry. That is
the whole difference between a fix and a fifth inference.

---

## 5. Measure whether the fix can fire, before you build it

Three plan decisions were settled in the final phase, each by a free measurement taken *before* any
implementation. All three changed or killed the prescription:

- **D3** (reject positional row tokens): the prescription is a **measured no-op** — same wrong bind
  with the token and without it. Three alternative sensors were built and each died to a different
  instrument, including one killed by a pre-existing test the 187-row benchmark had rated as costing
  a single row. **Decision: no change**, residual published.
- **D4** (watch websockets for writes): there is **no scope to achieve parity with** — the event
  exists on `Page` and not on `BrowserContext`. Measured across 14 pages on two live substrates: zero
  websockets observable, while the one substrate that *does* open a socket opens it from a shared
  worker the only available event cannot see. So the prescribed watcher is inert where there is
  nothing and blind where there is something, and its zero would read as *no websocket writes
  happened*. **Decision: no change** — the artefact would have been actively misleading rather than
  merely useless.
- **D2** (refuse sole-candidate fuzzy binds for mutating steps): the scope clause makes it a **measured
  no-op** — 22 rows bind that way and zero are on a write. And the remedy the code itself named, twice,
  in two files, had never been re-measured: reproduced, it costs exactly as much as deleting the
  candidate outright. A narrower rule nobody had proposed — refuse only when the recorded CSS path
  resolves uniquely to a *different* element — removed the wrong bind at **byte-identical** survival.
  **Decision: change**, and neither half of the entry's own prescription survived.

Each cost **$0.00** and each would otherwise have been a slice. The pattern is the point:

> **A plan entry is a claim about the tree, and it gets verified like one.** Four entries in this
> project outlived their subject — a prerequisite listing six fixtures that all already existed, a
> note five slices stale, three sentences outliving their fix, a step held by omission for 64
> versions.

---

## 6. Mechanism evidence does not entail the outcome

The longest worked example is one application — Odoo, a client-rendered SPA — across roughly forty
releases, six measured series:

**0.181 → 0.524 → 0.381 → 0.524 → 0.619 → 0.714**, ending at `availability_rate` **0.714 over n=21**
with standard deviation **0.000**.

The instructive part is the third number. Six observation fixes landed; the agent demonstrably got
*better* — the control scenario went from recipes of 12, 10 and 6 steps to 6, 6, 6, identical every
rep — and **the score went down**. Better authoring meant more real interactions, which meant more
requests, which on an app serving reads over POST meant more exposure to a write gate that a separate
open defect was mis-triggering.

Four predictions were written down *before* buying the measurement. The pattern across them:

- Predicted "reads improve" → reads dropped. **The mechanism was right and the direction was wrong.**
- Predicted 0.714 → the ceiling was arithmetically **0.571** — 4/7, so **three** rows could not pass
  at all (`odoo-search` plus both writes).
  Writing the number down is what made the miscount visible.
- Predicted the mean correctly and the **mechanism wrong** — the row I predicted at 3/3 came in at
  1/3 while the row I expected to flake held. Checking only the headline would have scored it a clean
  success.
- Finally predicted the **row** as the primary claim, with the number following from it. Both held.

> **A prediction is a pair — the number and the reason — and only the second is worth anything when
> the first is right.** A prediction whose number is right and whose row is wrong is a failure that
> reads as a success.

And three consecutive slices improved a mechanism and moved no behaviour at all. Mechanism evidence —
mutations killed, invariants holding, a guard measurably firing — does not entail the outcome. The
acceptance for a fix is the thing the user sees.

---

## 7. The instruments were wrong too

This is the part that earns the rest, because an instrument you trust unconditionally is just a slower
assumption.

**A stub was inert twice, in one slice.** A fixture claimed an LLM was "unreachable in both
directions." First failure: the consumer does `from .providers import build_router`, which binds the
name at import, so patching the source module reached nothing. Fixing that by deriving modules from
the live import graph was still not enough, because the factory *names* stayed hand-listed and the one
that matters was missing. Measured consequence: a replay built **105 real Anthropic clients** while all
25 cells passed and the corpus cell printed "0 reached an LLM." Closed not by extending the list but by
an AST scan pinning that SDK clients are constructed only inside leaf adapters — which is what makes
the choke point provable rather than asserted.

**The arming harness reported its own blind spot as a hole in the suite.** It caught `Exception`, and a
`pytest.raises` miss is `_pytest.outcomes.Failed`, a `BaseException` — five false verdicts. The same
trap recurred twice more in cells written an hour later, and I hit it again two weeks afterwards, one hour
after reading the note about it.

**A scan matched its own prose at least ten times.** A cell counting occurrences of a pattern goes red
on the comment explaining the pattern. Adding a cleverer needle only moves the collision. The rule that
finally stuck: **never assert an ordering or an absence over source text** — read the AST, or assert
the property behaviourally.

**A survivor is as often a broken mutation as a weak cell.** Recorded repeatedly: a mutation mangled by
shell escaping and never applied; one aimed at the wrong assertion; one whose find-text had gone stale
and was correctly reported as an *error* rather than a survivor. Read the message, not the verdict.

**And a new mechanism can silently disarm an existing guard.** A stall guard made an older cell's loop
exit early, so the mutation that cell used to kill began surviving — nothing went red, and only
re-running the registry showed it. *When you add a mechanism, ask which existing guards it makes
unfalsifiable.*

---

## 8. What it cost, and what it could not outrun

- **87 days**, 2026-06-16 to 2026-09-11. 686 commits, 266 merges, 179 distinct versions in
  `pyproject.toml`'s history (`git log --format=%H -- pyproject.toml` | per-commit `version =` | `sort -u`),
  counted the day this was written and excluding this piece's own change.
- **17,282 lines** of source; **56,893** of tests; **15,764** of benchmarks and probes; **18,983** of
  markdown (18,157 before this piece and its plain-language twin existed). More prose than product.
- **~$21.77** of measurement spend, summed from the figures recorded in the working notes. That is a
  sum of self-reported numbers rather than an invoice, and it excludes authoring cost.
- Full suite **~38 minutes** locally; the browser-free tier **~91 seconds**.

**What it reached:** two live applications, seven scenarios each, three passes each. Gitea
`availability_rate` **0.762**, Odoo **0.714**, each over n=21 scenario-observations. No silently-wrong
outcome in any committed customer-benchmark series.

**What it did not:**

- **75 findings open** at close-out. The **73** from round 4 are 9 high-severity, 18 medium, 17 low,
  and **29 carrying no severity at all** (R3.2 and R3.7 are not in `state.json`), because the field is filled in when a slice touches a finding and most were never
  touched again.
- Two findings blocked indefinitely behind D5, and two decisions blocked indefinitely for
  over-refusal.
- The headline availability numbers **mix two things**: a read counted as "available" still calls the
  model once per replay to read the answer off the page. Writes are the rows that genuinely replay
  with no model call. Nothing in the benchmark record says which is which.
- Neither baseline records **when or against what it was cut**. The dates live only in prose.

The honest summary of the product: the strongest single piece of evidence for it is one scenario —
a real write, learned in two steps, replayed at zero model calls, the gate not refusing, the server
holding exactly one record, three passes out of three. The strongest evidence against it is that
reaching a comparable number on the *second* application took ten `src/` fixes across roughly forty
releases, and **a third class of application should be assumed to need its own.**

That is what a solo project could not outrun: not any individual defect, but the per-application
cost of the first one.

---

## 9. What I would keep

If you take one thing, take the first. If you take two, take the second.

1. **Assume your own output is wrong and build the instrument that says where.** Not more care — care
   does not scale and does not survive a tired afternoon. An instrument does.
2. **Run an adversarial pass over your own new code before the PR, not one release later.** Green is
   not evidence. Point it at a committed tree it cannot edit, and reproduce every finding yourself
   before acting on one.
3. **Move every count into data and render the prose from it.** Nobody types a number. Assert both
   directions, because the direction that fails is the one you did not think to check.
4. **Two strikes, then change the sensor class.** When two built fixes have been measured wrong, the
   third must change what the decision is made *from*.
5. **Measure whether the fix can fire before you build it.** Three decisions here were settled for
   $0.00 each, and all three would otherwise have been slices.
6. **Write the prediction down as a pair** — the number *and* the reason — before buying the
   measurement. A right number over a wrong mechanism is the failure that reads as a success.
7. **Ship the instrument with the diagnosis.** A conclusion is worth what its reproducibility is
   worth, and the next person to doubt it should re-derive it in a minute rather than re-buy the run.

And the one this document is itself an instance of: **when you finish, point the method at the thing
it was about to describe.** On the last working day it found ten live falsehoods — one of them inside
the finding about that exact failure, 1,282 lines from the banner refuting it, after a commit of mine
claiming it was fixed everywhere.

That is not an embarrassing footnote. It is the result.

---

*The evidence for everything above is in [`docs/open-defects.md`](open-defects.md) — 160 round-4
findings with their measurements — and in [`CLAUDE.md`](../CLAUDE.md), the working notes. Both are
long, both accrete rather than getting rewritten, and both are wrong in places nobody has read
recently. The [close-out banner](open-defects.md) says what an open finding means now.*
