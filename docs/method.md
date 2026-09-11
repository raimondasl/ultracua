# The test suite never found a bug

*What three months of trying to break my own work actually taught. Written at the close of the
project, 11 September 2026.*

> **Every number in this document was produced by running a command, not by remembering.** That is
> not a stylistic flourish. In the week this was written I published — and then had to correct — a
> speed range whose lower number appears in no measurement anywhere, a data field I described in
> detail that is in neither of the two files I said it was in, a count that was off by one, and a
> second count that was off by one. A document *about* being rigorous that contains a number nobody
> checked is worse than no document. Where a number cannot be produced by running a command, I say so.

---

## What the project was, in plain terms

The first time through, an AI model drives a web browser itself: it looks at the page, works out what
to do next, and does it — click this, type that, press Enter — while the software writes down every
step it took. After that the software can repeat the whole task on its own, with no model deciding
anything. Each repeat then does the same thing every time and costs a fraction of what the first run
cost, where an AI agent charges full price for every run and can choose differently on the same page
twice.

The difficulty is two questions that look like one.

Websites change: a button moves, a label is reworded, a table gains a row. So when the software
returns to a page it recorded last week, it has to ask **are these still the same controls I wrote
down?** Underneath that sits a second question that looks identical and is not — a browser can report
a page as finished loading and then carry on drawing it for another half-second, so *the control is
gone* and *the control is not there yet* arrive looking exactly the same.

Getting either of those wrong on a page that saves something — placing an order, filing a record —
does not produce a visible failure. The software clicks the wrong thing, changes the wrong record,
and reports success. Nothing in it catches that. The only thing that does is a person reading the
records afterwards.

So the project had three rules it was not allowed to break:

1. Repeating a recorded task never calls an AI model.
2. It must never quietly do the wrong thing. Where the page does not support the action it is about
   to take, it stops and says so out loud.
3. On a task that saves data it must never save twice — two records where one was intended — and
   never silently skip a save it was asked to make.

The first of those is the one its own measurements later caught it breaking, and that is at the end
of this piece.

Active development stopped on 9 September 2026. On two real applications — a code-hosting site and a
business system — it both did the task and turned it into a repeatable routine in 16 and 15 of 21
task-runs. It is archived as a usable prototype of a real idea rather than as a finished product.

**This document is about the other thing the project produced, which travels better than the code: a
way of working that starts from the assumption that your own output is wrong, and builds tools to
find out where.**

---

## 1. The number this all comes back to

Four rounds of hostile review — a deliberate effort to break the code, run as a separate job from
writing it — in a project that ran 87 days. A "finding" here means one specific defect, written down
with the evidence that it is real.

| round | findings | where they stand today |
|---|---|---|
| 1 (31 July 2026) | 20 | all fixed |
| 2 (2 August 2026) | 10 | all fixed |
| 3 (3 August 2026) | 13 numbered | **2 still open** |
| 4 (continuous, one per piece of work) | **160** | **73 open, 83 fixed, 4 against a change that never shipped** |

Round 3's review itself returned eleven; two more were added to its numbering later. That is the one
soft edge in the table, and the reason for the *roughly* in what follows.

**Roughly 203 defects. Not one of them was discovered by the test suite.**

That suite is not a weak one. A test here is a small program I wrote alongside the product: it runs
one piece of it and checks the answer against what I expected. They run automatically, and a change
that makes one fail does not go in. At the close there are **2,495 of them**. Of those, 1,899 run in
about a minute and a half with no browser; the other 596 drive a real copy of Chrome — no window on
screen — against test pages kept on the same machine. The product is **17,282 lines** of code, and
the tests themselves are more than three times that size.

Alongside them, five more instruments:

- **113 deliberate sabotages** of the product code. Each is a single corruption applied to a
  throwaway copy, and each has to make at least one test fail.
- **Six automatic counters**, each counting the places in the code still using a shape the defects
  here keep coming from. One tracks the 34 separate exit points in the command-line program, where
  one would do. The count may fall and never rise.
- **A 187-case rig** that mangles web pages on purpose — moving controls, renaming them, deleting
  rows — to see whether the software still clicks the right thing or stops.
- **Two recorded reference outputs**, re-derived on every run. Any difference has to be read line by
  line and deliberately accepted.
- **An automatic check** that runs all of it on two operating systems before a change is accepted.

The suite caught none of the 203.

### The precise version of that claim, including where it is not true

The suite did not *discover* defects. But it did, constantly, **catch the mistake I was making right
now** — which is a different job, and the one it is genuinely good at. In the final week alone, four
times:

- The check that compares the defect counts written in the prose against the actual list of defects
  caught me twice — inside the very change that was correcting those counts.
- The check that pins down where the software may pause and wait for a page to stop changing has
  three times refused a new place to use it, until I justified the addition.
- The check that compares the project plan against the files on disk forced a results file to be
  renamed. I had named it as the finished benchmark across both test applications; it covered one.
- A check on how the sabotage tests themselves are written found three more violations of its own
  rule on its first run, two of them older than the check.

So, the sentence worth taking away:

> **A test suite is a ratchet, not a detector.** It holds in place what you already understand, and
> it stops you shipping today's mistake. It cannot fail for a defect nobody has thought of yet,
> because every test in it is a written-down thought.

The obvious objection is mutation testing — deliberately corrupting your own code to see whether any
test notices. It does not rescue this, and it is worth being clear why, because sabotage is exactly
what you reach for when you stop trusting a suite. **It tests the guards you already have** — a
guard being a check built into the product itself, as opposed to a test, which sits outside it. Every
defect in this project was a guard that was *missing*. A sabotage has to find a real line of code to
corrupt; a guard nobody ever wrote leaves no line to find. Measured here: nine of those 113
sabotages — one for each category of defect this project has actually shipped — are **9 caught, 0
missed**. That number is real, and it says nothing whatsoever about the 203.

---

## 2. What did find them

A deliberate attempt to break the new code, aimed at it specifically, carried out *before* the change
is even put forward for anyone else to look at — not while it sits under review, and not a release
later. It is not a read-through of the lines just changed. It is a separate effort whose only goal is
to make the thing just built do something wrong, working from a frozen copy of the code that it is
not allowed to edit. That last restriction is there because a reviewer who can change the code under
review will alter it to demonstrate a point, and any test run already in flight is then measuring
code that no longer exists.

Here is why it became a rule rather than a suggestion. **Four separate changes passed the entire test
suite, passed the page-mangling rig with every one of its checks holding, passed tables of test cases
built specially to check the very thing each one changed — and were seriously wrong.**

- A redesign, **754 tests green**, that had to be thrown away entirely.
- A later attempt at that same defect — working out which recorded step caused a save the page sent
  late — **785 tests green**, with the rig's output identical to the previous run byte for byte. It
  was left finished and unused in a separate copy of the project, never folded back in.
- Two successive attempts at a different defect, about identifying the right row of a table. Each
  passed a grid of tests built for that exact mechanism. Each turned a refusal that had been correct
  into a **silent click on the wrong row**, by two different routes. The second also showed the
  software surviving page changes *better* than before — however many changes were made to the page,
  with not one of the rig's cases doing worse.

That last pair is the one worth sitting with. The rig had nothing to say because no page in it was
shaped the wrong way: all 30 tests for that mechanism and all 185 cases in the rig put a row's
identifying label in the one place the new code had started looking. The single arrangement that
defeats it — the label one level further out, and nothing where the code now looked — existed in no
instrument at all.

Hence the rule this project states first and loudest:

> **A green test run is not evidence here.**

The fourth round in the table above is this review, run once per piece of work. Three of them fell in
the closing week, and each found something real. Most of what each found was a defect in work I had
written days earlier:

- **Day 1** — making the project's front page true. The headline claim was that the software drove a
  browser at five to ten times human speed. Every number behind it turned out to be the software's
  own first run timed against its own repeat run — never against a person at all — on two test
  applications, three months old, **with no evidence file saved anywhere in the project**. Separately,
  a list on the front page of "things this deliberately refuses to do" had three entries backwards,
  advertising three known, unfixed weaknesses as safety features.
- **Day 2** — freezing the project. Two inventions. The first: a record of where each published
  result came from — which version of the software produced it, and on what date — that I described
  in detail and that is in neither of the two result files, one per test application, where I said it
  was. The second: a claimed speed-up of **38–114×** against a local test page, where the file it was
  supposed to come from records **62–114×**, averaging 86.3 over five runs, and no file in the
  project contains a 38 at all. Plus a count of open defects that was wrong in all four places it
  appeared — in a paragraph that went on to name the two defects the count had missed.
- **Day 3** — the public release. The package declared that it needed version 0.109.2 **or later** of
  the AI provider's code library, with no upper limit. "Or later" had by then reached a version that
  no longer accepts the temperature setting, which controls how varied the model's suggestions are
  and which the software sends on every decision it asks the model to make while learning a task.
  Reproduced against the actual installed package: it fails while *assembling* the request, before
  anything leaves the machine. **Every first run of a freshly installed copy would have died the
  moment it tried to call the model.** Permanently, too: a version on the public catalogue software
  is installed from cannot be edited, and this was the release that archived the project — nobody was
  ever going to publish another one.

**And the hostile reviews are not clean instruments either.** The Day 2 review returned **45
findings**. Checking each one against the code gave **27 confirmed, 16 partly right, and 2 simply
wrong**, where "partly right" means the symptom was real and the stated cause or severity was not.
So **18 of 45 — two in every five — had to be corrected or discarded before anyone could act on
them.** That is why every finding here goes through a second hostile pass before a line of code is
touched, and why this project's standing rule is *reproduce a finding yourself before you fix it*.
**A fix built on a wrong diagnosis is worse than no fix**: you have changed working code and left the
defect in place.

---

## 3. Take the numbers out of the prose and let a machine check them

The project's front page said the defect register had "nine remain open" for **five weeks** after the
true number was two. Nothing caught it, because that sentence was prose in a hand-written table and
no checker reads prose. Separately, a finished piece of work — 1,750 lines across four new modules
and three test files — was still listed as "never started" three days and six completed changes
later. The next instruction taken from that list would have been to build the whole thing again from
scratch.

The response was not to be more careful. Care does not survive a tired Thursday. The response was to
**move every count out of the prose and into a data file a machine checks — and where prose still has
to quote a number, generate that prose from the file**:

| file | what it holds | what checks it |
|---|---|---|
| the defect register's data file | every fourth-round defect's number, status and one-line summary — 160 rows. The 43 from the earlier three rounds are prose only | a script regenerates the register's summary table and fails if what it generates differs from what is there |
| the plan's data file | each step's status, and the name of the file that step was supposed to produce | a test, in both directions |
| which tests need a browser | every test's name and whether running it opens one — worked out by running them and watching, never typed | the minute-and-a-half browser-free run, which fails loudly if a test it believed needed no browser opens one |
| six code-pattern counters | how many places still use each pattern the project was removing — 34 where the command-line tool exits on the spot, for instance | a test that fails if a count rises, and equally if it falls without the new number being recorded in the same change |
| 113 deliberate sabotages | each must make some test fail | two tools — one applies a single sabotage and reports whether any test caught it, one runs all 113 |
| the 187-case rig's results | the measured numbers, and the list of cases the software is known to get wrong | two tests: that the numbers were measured against today's set of cases, and that the known-wrong list still matches what the software actually gets wrong |
| two recorded reference outputs | how often each internal callback fired on each kind of run, and which runs may safely be restarted after a login is refreshed | a test re-derives both from the running code and fails on any difference; a person reads the differences line by line when one is deliberately re-recorded |

Three principles came out of it, each learned by shipping its opposite first:

**Nobody types a count.** The register's summary table is generated. You file a defect by adding a row
and regenerating, so the totals cannot disagree with the rows, because no human ever writes a total.
A further test re-counts the rows and compares that total against the counts still typed by hand in
the surrounding prose — it reads the figure, never the sentence around it — and it caught one going
stale *inside the very change that wrote it*.

**Check both directions.** The plan test requires a step marked finished to have produced its file
**and** a step still marked unfinished to *not* have produced it. The second direction is the one that
actually caught something: a file sitting on disk under a step that still reads "never started" means
the status is wrong, which is exactly what had happened to those 1,750 lines. The same shape turns up
everywhere. A list of known-acceptable exceptions needs a check that every entry still refers to
something real — an entry pointing at a case that no longer exists is a silencer with nothing left to
silence, and it sits there green while the problem it excuses may quietly have been fixed.

**Silence is the short list.** Write down the outcomes that are allowed to pass without an alarm,
never the ones that should raise one. Then an outcome invented tomorrow raises an alarm by default.
This rule exists because the scheduled command that re-ran every saved task had two ways of raising
one — the pass/fail code a scheduler reads, and a message sent to the team — and each asked only "did
any of them fail?". A third answer, *skipped*, was neither, so it reached neither alarm. A task could
fall into it without anyone choosing that: one re-recording that mistook an ordinary page fetch for a
save was enough. So a task could stop running for good while both alarms went on reporting healthy.

### What this approach cannot do, measured

**Every one of those checks reads numbers — even the ones that pull a number out of a sentence. None
of them reads what a sentence says.** On the last working day I pointed the method at the project
this document was about to describe, and found **ten statements that were live and false**. The
machinery could catch zero of them:

- A claim that the two files of published benchmark results each record which version of the software
  they were measured against. Neither does. The claim was still sitting **inside the very defect entry
  about documents outliving their own corrections** — in the same file as the notice saying, in as
  many words, that neither file records a version, a date or a timestamp, and after a change of mine
  announcing it had been corrected everywhere. The two were **1,282 lines apart**, which is how a
  document contradicts itself without anyone noticing.
- A defect entry whose **headline says the opposite of its own retraction**. The retraction is about
  3,500 characters — nearly 600 words — further down the same entry. A correction appended to the end
  of a long entry never reaches the headline, and the headline is the part people read.
- One file giving **two different answers to the same question** 753 lines apart: how long a full run
  of the 187-case rig may take before it counts as too slow, quoted as 220 seconds in one place and
  260 in another. The code says 260.
- Two stale counts sitting **inside the documentation of the checking tools themselves** — including
  "only 5 of 53 rows state a severity", written inside the very tool built so that nobody types a
  count. The register has more than tripled since: 94 of its 160 entries record one today.

The machinery is genuinely good at what it covers. What is left over is exactly the part a machine
cannot read, and the only thing that has ever caught it is somebody reading it again.

---

## 4. The idea that transfers best: one signal meaning two things

A single value that two completely different situations both produce, so the code cannot tell which
situation it is in — and has to act anyway. **Four separate places in this software, all the same
fault, found one at a time and never by looking for the pattern:** 3 August, 28 August, 29 August and
5 September 2026, two of them a single day apart.

| the value | means | and also means |
|---|---|---|
| "this table row has no identifying label" — the label being what the software re-checks to confirm it is acting on the row it recorded | the row genuinely has nothing distinctive, so there is nothing to re-check *(known, and deliberately allowed)* | I looked in the wrong part of the page and found no label there *(so the re-check has nothing to compare, and quietly stops guarding)* |
| "find this control" came back empty | nothing on the page matched | I found it and **deliberately refused** — for any of four distinct safety reasons |
| "the page changed after I acted" | the page is genuinely different | the page had not finished drawing the first time I looked *(so the "am I getting anywhere?" check switches off — one page reloaded twelve times, and a twenty-step recorded routine of nothing but page-loads)* |
| "the part of the page this button sits in is unchanged since I recorded it" | the form containing the button is unchanged, so unrelated churn elsewhere is ignored | **the whole page** is unchanged, because the search for a containing form found nothing *(the careful check silently becomes the crude one it was built to replace)* |

Each produced a real defect. On the two where the obvious fix was actually built and measured, it
failed — because **the obvious fix is a cleverer guess based on the same ambiguous value.** On the
third, the obvious fix worked first time. On the fourth, nobody ever built one.

The two that failed:

- *"If finding the control comes back empty, wait a moment and try again."* It sounds harmless: the
  page was probably still loading. But empty is also what comes back when the software finds **two**
  candidates it cannot tell apart and refuses to guess between them — and waiting does not break that
  tie, it waits for the page to break it. Measured, on a page listing two customers each with their
  own *Cancel* link: everything recorded to identify the right one matched both, so the software
  refused. Then the recorded customer's row was hidden — the ordinary way a page drops a row
  something else has just removed. Only the other customer's link was left, the ambiguity was gone,
  and the retry settled on it with complete confidence. What that costs on a step that saves data was
  measured separately, on the same shape of page: the save went out against the wrong customer,
  carrying the **duplicate-protection token recorded for the right one** — the unique marker every
  save carries so a server can recognise a repeat and refuse it. So the wrong record is written, and
  the right one's save, if it is ever retried, is thrown away as a duplicate of something already
  done. That is rule 3 broken by the very thing that exists to uphold it.
- *"Refuse to trust a row label that is just a position number."* Measured: refusing it leaves the
  label empty — and an empty label is exactly what the code reads as *there is no check to do here*.
  That switches off the check that the control it just found really sits inside the row it recorded.
  The harm moves from "the check agreed with the wrong answer" to "the check never ran". Identical
  outcome, better conscience.

### The rule that came out of it

**Two strikes, then change what the decision is made from.** When two different fixes for one defect
have been built and measured wrong, the third attempt is not allowed to be another guess. It has to
change the *source* of the decision — from a guess to a person's judgement, or from a guess to an
outright refusal to proceed. A better threshold, a better waiting period, or a better piece of
measuring code inside the page is not a third attempt; it is the second one wearing a different hat.

A strike is a fix that was built and measured wrong. An idea discarded at the drawing board costs
nothing and does not count. The count belongs to the defect, and it does not reset because somebody
new is working on it.

**Three defects in this project are frozen under that rule** — two have defeated two attempts each,
one has defeated three — and they will stay frozen.

**And when the source of the decision genuinely does change, it works.** "Find this control came back
empty" was split in two by asking the search code a second question it already knew the answer to:
*did anything on the page match at all, before you applied your safety rules?* If nothing matched,
wait and look again. If something matched and the safety rules rejected it, stop immediately — no
waiting, because waiting is precisely what let the wrong row win above.

All four safety refusals kept their instant stop. The retry is spent only where nothing matched at
all — which might be a page still drawing, or an element that has gone for good. It cannot tell those
two apart, and it does not have to: one more look at an empty page costs a few hundred milliseconds,
while one wait on a refusal costs you the wrong row. That asymmetry is the entire difference between
a fix and a cleverer guess.

---

## 5. Measure whether the fix can even fire, before you build it

Three planned changes were settled in the project's last week, each by a measurement that cost
**$0.00** in AI model calls and was taken *before* anything was built. All three changed or killed
what the plan asked for.

- **"Refuse row labels that are just a position number."** Measured: it changes nothing. This is the
  empty-label case from the previous section — refusing the label leaves it empty, and an empty label
  is what the code reads as *there is no check to do here*. The software settles on the same wrong row
  with the rule and without it. Three alternative approaches were then built, and each was killed by
  a different tool. The 187-case rig priced one of them at a single lost case — one mangled page out
  of 187 it would no longer handle — and then an ordinary test already in the suite killed that
  approach outright: it refused a row whose text had merely been edited, and a changed price does not
  make a row a different record. **The big rig understated the cost; one small existing test priced
  it.** Decision: **no change**, and the case the software gets wrong stays on the rig's short list of
  known wrong results, re-measured and named by every run.

- **"Watch for saves sent down a connection the page keeps open."** Some pages hold a line to the
  server open and send messages along it, rather than making a fresh request each time — and a save
  can go out that way. Measured: the browser automation library reports those messages for one tab at
  a time and offers nothing wider, so there is no whole-browser vantage point to move the watcher to.
  Across all 14 test pages of the two applications, the proposed watcher would have seen **zero** such
  connections. And the one application that *does* open one opens it from a *shared worker* — a script
  that runs outside any single tab — which the library never reports. So the watcher would be
  pointless where there is nothing to watch and blind where there is something, and its report of "no
  saves seen" would read as *no saves happened*. Decision: **no change**, because the thing built
  would have been actively misleading rather than merely useless.

- **"When only one weak match is found, refuse it — on steps that save data."** Measured: across the
  whole 187-case rig, 22 cases are decided by that weak route and **not one of them is on a step that
  saves data**, so the rule as written would never once fire. The harm it was aimed at is real, all
  the same: among those 22 is a case the rig builds to go wrong on purpose, where a redesign renames
  the real control and moves its old wording onto a decoy beside it, so the loose match clicks the
  decoy and the software ends up on the wrong page. Meanwhile the remedy the code's own comments
  *named* — in two different files, its cost never re-derived — was reproduced and cost exactly what
  those comments already said: as much as deleting the weak route entirely. What re-deriving it added
  was the **reason**. Demanding that a second, independent way of finding the control *agree* can only
  ever accept a match that second way would have found anyway, so it destroys the one case the weak
  route exists for. A narrower rule nobody had proposed — refuse only when that second way lands on
  **exactly one** element and it is a *different* one, while finding nothing or finding several says
  nothing at all — removed the bad click with the rig's score **identical to the digit** across all
  187 cases. Decision: **change**, and neither half of the original plan survived: not the restriction
  to saving steps, and not the remedy the code recommended.

That last zero needs one more sentence, because stopping at it would be the flattering version. **The
zero is the rig's, not the world's.** On a page built afterwards to hold the awkward shape — where it
is the recorded structure that moved and the weak name match is the correct one — the new rule
refuses a control the old one found correctly. It refuses out loud rather than clicking a stranger,
which is the direction that had to hold; but the rig's zero is the absence of that shape from the rig,
not the absence of a cost.

Each of those measurements cost nothing and each would otherwise have been a piece of work in its own
right. The general point:

> **A line in a plan is a claim about the code, and it should be checked like one.** Four claims in
> this project had outlived their subject: a plan entry listing six kinds of test page to build that
> all already existed, a note five changes out of date, three sentences that survived the fix that
> made them false, and a step left on hold for 64 releases with no recorded reason.

---

## 6. Proving the mechanism works is not proving the problem is fixed

The longest worked example is not the 187-case rig but a real, off-the-shelf business system — one
whose pages are drawn by the browser after they arrive, rather than sent ready-made — run end to end
and tracked across roughly forty releases.

It has seven tasks, and the score is not how many the software got right. It is **how many it could
both do and turn into a routine it could re-run on its own afterwards.** Getting the right answer once
does not count. One of these seven is answered correctly on *every single run* and still scores zero,
because the answer is already sitting on the page the software starts from: there is nothing to write
down and nothing to repeat. Each figure below is the average of three complete passes through all
seven tasks:

**0.181 → 0.524 → 0.381 → 0.524 → 0.619 → 0.714.**

It finished at **five of seven** — and at five of seven in each of the three passes separately, not
merely on average.

The instructive number is the third one. Six fixes to how the software perceives a page had just
landed. One of the seven tasks is deliberately easy, kept as a control: if *it* fails, you know the
fault is somewhere other than the thing you are measuring. On that control the agent demonstrably got
**better** — the routine it wrote down went from 12, 10 and 6 steps across three passes to 6, 6 and 6,
both shorter and identical every time. **And the score went down.**

Here is why, and it is worth unpacking slowly. Better perception meant the software actually
interacted with the page instead of flailing, and every real interaction makes the browser send a
message to the application's server. Browsers send those in a handful of kinds, and one kind — POST —
is the one conventionally reserved for *change something*. This particular application used POST for
merely looking things up. So from the outside a lookup and a save were indistinguishable, and lookups
were filed as saves. That handed them to the safety gate, the check standing in front of anything
that might change data, and it refused them. **The gate behaved exactly as designed.** What was wrong
was the information reaching it, and that was a separate defect already on the register. So better
work meant more lookups misfiled as saves, and more refusals that should never have happened.

Four predictions were written down *before* the measurements were bought. Five of the seven tasks only
read information and change nothing; the other two save something, and the two halves are scored
separately as well as together. The pattern across the four is the lesson:

- Predicted "the read-only tasks will improve" → they dropped. **The chain of cause was right and the
  direction was wrong.**
- Predicted the score would reach 0.714 → the arithmetic ceiling was **0.571**, four tasks out of
  seven, because the other three could not pass at all for reasons already known. One of those three
  was fixed later, which is how the series ends at five of seven. Writing the number down beforehand
  is the only reason I noticed I had miscounted.
- Predicted the average correctly and **the reason wrong**. The task I had predicted would pass three
  times out of three came in at one, while the task I expected to fail about one run in three held
  firm. Checking only the headline number would have scored that as a clean success.
- Finally made the *task* the main claim and let the average follow: the same task that had just come
  in at one would now pass two or three times out of three. It passed three times out of three, and
  the average came out where that put it. **The reason held, and the number with it.**

> **A prediction is a pair — the number and the reason — and only the reason is worth anything when
> the number turns out right.** A prediction whose number is right and whose reason is wrong is a
> failure that reads as a success.

And three pieces of work in a row each made the machinery measurably better and changed nothing the
software actually did. Evidence that a mechanism works — sabotages caught, safety rules holding across
every case, a check you can watch actually refuse something — does not establish that the problem is
fixed. **The proof of a fix is the thing the user sees.**

---

## 7. The measuring instruments were wrong too

This is the section that earns the rest, because an instrument you trust without checking is just a
slower assumption.

**A fake stand-in did nothing, twice, in the same piece of work.** A test setup claimed an AI model
was "unreachable in both directions". First failure: when the program starts, each file that uses a
shared function takes its own private shortcut straight to it. The fake was installed where that
function is *defined* — but every shortcut was already aimed at the real one, so the substitution
reached nothing at all. Working out those files automatically was still not enough, because the *list
of functions* to replace was typed by hand, and the one missing from it was the low-level function
the other two both call. The fake held the front doors while the back door stood open. The measured
consequence: a run that was supposed to use no AI whatsoever built **105 real API clients for the
model provider**, while all 25 tests passed and the summary printed "0 reached the model." It was
closed not by extending the list but by a structural check that such a client can only be created
inside **four** named low-level files — which makes it provable, rather than merely believed, that
every route to the model runs through the one function the fake replaces.

**The tool that checks whether a test is capable of failing was wrong in both directions.** It works
by sabotaging a piece of the product and confirming that the test complains. It watched for failures
of the ordinary kind — but when a test fails because *the thing it demanded did not happen*, the
testing library reports that as a special kind of failure, deliberately placed outside the ordinary
kind so that a program watching for ordinary failures cannot swallow it. Which is exactly what this
tool was doing. It never saw those failures, and announced **five** perfectly good tests as incapable
of failing. The same tool had a second blind spot pointing the other way: three sabotages were scored
as *caught* when they had not been, so the suite was reported stronger than it was. One of those
recurred twice more in tests written an hour later — the sabotaged copy had no readable source, so a
test that works by reading code died before it read anything, and the tool counted the crash as a
catch. That is what moved the fix out of the individual tests and into the tool. Two weeks after the
first five, I walked into the same trap again, one hour after re-reading my own note about it.

**A check searching the code for a pattern matched the comment explaining the pattern — at least ten
times.** A code file carries human notes mixed in with the instructions, and a plain text search
cannot tell the two apart, so a test that counts how often something appears goes red on the sentence
explaining why it is counting. The tidiest instance is the ninth: a test counting the places where
the software pauses to let a page settle failed because a comment had just been added explaining why
one of those pauses was there — and the comment names the pause too. Writing a cleverer search only
moves the collision onto another sentence. The rule that finally stuck: **never establish that one
thing comes before another, or that something is absent, by searching the text.** Read the code's
parsed structure — what the machine turns the code into before running it, where a comment is a
comment and can never be counted as an instruction — or run the code and watch it do the thing you
claim.

**A sabotage that nothing catches is as often a broken sabotage as a weak test.** Recorded repeatedly
here: one mangled by the command line's quoting rules and never actually applied; one aimed at the
wrong assertion; one whose target text had moved, correctly reported as an *error* rather than as a
miss. Read the message, not the verdict.

**And a new mechanism can silently disable an existing check.** When the software cannot find a
control it waits and looks again, with a short pause between attempts. One sabotage deleted that
pause, and an older test caught it by counting attempts: without the pause the loop spun for the
whole time budget. Then a new safeguard capped the attempts at six, whether the pause was there or
not. Six either way — so the older test could no longer tell the sabotaged code from the healthy
code, and the sabotage began slipping through. No test failed. Only re-running that set of sabotages
on purpose revealed it. *When you add a mechanism, ask which existing checks it has just made
incapable of failing.*

---

## 8. What it cost, and what it could not outrun

- **87 days**, 16 June to 11 September 2026. 686 saved changes, 266 of them merged as complete pieces
  of work, and 179 distinct released versions in the project file's history — counted on the day this
  was written, so this document's own change is not among them.
- **17,282 lines** of product code; **56,893** of tests; **15,764** of measurement tools; **18,983**
  of written documents — a figure that counts this document, where the 18,157 quoted before it
  existed did not. More prose than product, either way.
- **About $21.77** spent on AI model calls for measurement, added up from the figures recorded in the
  working notes as they were taken. That is a sum of self-reported numbers rather than a bill, and it
  excludes the cost of the AI that helped write the code.
- The full test run takes about **38 minutes** on this machine; the browser-free part about **91
  seconds**.

**What it reached.** Two real applications — a code-hosting site and a business system — with seven
tasks each, run three times each, so 21 task-runs apiece. On the code-hosting site the software both
did the task and turned it into a repeatable routine in **16 of 21**; on the business system, **15 of
21**, which is exactly five of its seven tasks in every one of the three passes. In no committed
measurement series did the software ever quietly produce a wrong answer or a wrong action.

**What it did not reach:**

- **75 defects still open** at the close. The **73** of them from the fourth round break down as 9
  serious, 18 moderate, 17 minor, and **29 with no severity recorded at all** — that field only gets
  filled in when somebody touches the entry again, and most were never touched again. The remaining
  two are from round three and predate the data file the breakdown comes from.
- Three defects frozen indefinitely under the two-strikes rule, and two planned changes rejected
  indefinitely because they would have refused too much legitimate work.
- **The first of the three rules is broken, and the measurement that says so is one of those 75 open
  defects.** "Repeating a recorded task never calls an AI model" holds for the tasks that save
  something. It does not hold for the ones that only read: to hand back the answer, a repeat run
  still asks the model to read it off the finished page — one call, every time. Measured on both
  applications, on every read task. So a read task counted as a success above is not the thing the
  product claims to be, and **nothing in the recorded results tells you which rows those are.**
- Neither set of benchmark results records **when, or against which version, it was measured**. Those
  dates exist only in prose.

The honest summary of the product. The single strongest piece of evidence for it is one task: a real
save on a real application, learned in two steps, repeated with no AI model at all, the safety gate
correctly not objecting, the server ending up with exactly one record, three times out of three. The
strongest evidence against it is that reaching a comparable result on the *second* application took
**ten separate fixes to the product code across roughly forty releases** — and **a third kind of
application should be assumed to need its own.**

That is what a one-person project could not outrun: not any single defect, but the cost of the first
application of each new kind.

---

## 9. What I would keep

If you take one thing, take the first. If you take two, take the second.

1. **Assume your own output is wrong, and build the tool that shows you where.** Not more care — care
   does not scale and does not survive a tired afternoon. A tool does.
2. **Try to break your own new code before you put it forward, not one release later.** A green test
   run is not evidence. Work from a frozen copy you cannot edit, and reproduce every finding yourself
   before acting on it: two in every five of them need correcting first.
3. **Move every count out of the prose and into a file a machine checks.** Nobody types a number.
   Check both directions, because the direction that fails is the one you did not think to check.
4. **Two strikes, then change what the decision is made from.** When two built fixes have been
   measured wrong, the third has to change the *source* of the decision, not refine the guess.
5. **Measure whether the fix can even fire before you build it.** Three decisions here were settled
   for $0.00 each, and every one of them would otherwise have been a piece of work in its own right.
6. **Write the prediction down as a pair — the number and the reason — before you buy the
   measurement.** A right number over a wrong reason is the failure that reads as a success.
7. **Publish the tool alongside the conclusion.** A conclusion is worth what its reproducibility is
   worth, and the next person to doubt it should be able to re-derive it in a minute rather than
   re-buy the experiment.

And the one this document is itself an example of: **when you finish, point the method at the thing it
is about to describe.** On the last working day that found ten live false statements in the project —
one of them sitting inside the defect entry about exactly this failure, in the same file as the notice
that disproves it, after a change of mine claiming it had been fixed everywhere.

Then it happened again, to this document. The plain-language version you are reading was checked the
same way before it was published, and the check found that I had described the product as passively
watching a person rather than as an AI model doing the work; that I had claimed repeat runs use no AI
model, in a document whose own closing section says otherwise; that the arithmetic ceiling of four
tasks in seven meant three tasks could not pass rather than two; that three defects are frozen under
the two-strikes rule and I had written two; and that **every single duration I had added while
simplifying — "months apart", "quoted for months", "added that week", "months afterwards" — was
wrong.** The last one is the most useful thing I learned writing it: the errors were not randomly
distributed. They were concentrated in exactly the places where I had reached for a phrase instead of
a measurement.

That is not an embarrassing footnote. That is the result.

---

*The evidence for all of the above is in [`docs/open-defects.md`](open-defects.md) — 160 defects from
the fourth round, each with its measurements — and in [`CLAUDE.md`](../CLAUDE.md), the working notes.
Both are long, both grow by accumulation rather than being rewritten, and both are wrong in places
nobody has read recently. [`docs/method-technical.md`](method-technical.md) is this same document
written for someone who already knows the codebase: denser, and it names the specific functions,
defect numbers and instruments that this version describes in words.*
