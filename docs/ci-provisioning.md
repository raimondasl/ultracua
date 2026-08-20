# CI provisioning — the measurement behind dropping `--with-deps`

**Taken 2026-08-19/20, at 0.110.0, over the 29 CI runs then in the Actions retention window
(58 ubuntu `test` jobs, 2026-08-18T04:56Z to 2026-08-20T03:30Z).**

This file exists because **GitHub Actions logs expire.** `.github/workflows/ci.yml`'s comments and
`docs/reshape-plan.md` §13 both now assert numbers whose only primary source was
`gh api repos/raimondasl/ultracua/actions/jobs/<id>/logs` for a handful of jobs in one retention
window. In ninety days those are gone, and an assertion nobody can re-check is the thing `baselines/README.md` and
`tests/.manifest_cost.jsonl` exist to prevent elsewhere. Everything below is what was actually
observed, with the job ids, so a later reader can at least see what was claimed and by what route.

---

## The question that was being asked, and why it was the wrong one

`docs/reshape-plan.md` §13 (written 2026-08-19) opened Phase 1 with a **"step 0 — CI capacity"**,
carrying this Unknown:

> whether ubuntu is now genuinely slower or hung once — `-q` prints no progress, so the killed job's
> log cannot say, and one observation cannot distinguish them.

Every clause is false, and all of it was refutable for the price of one `gh run view`:

* the dead jobs died in **step 4**, before `pytest` was ever invoked, so `-q` is irrelevant;
* that step's log is apt's own verbose, timestamped output, which says precisely what happened;
* there were **eight** such observations available, not one.

The step-0 that was written proposed two levers. **Both are measured no-ops** — see *What this
refutes* below. What it got right was its own last bullet ("if the two cheap levers do not bring
ubuntu back inside the budget, the cause is a hang rather than balance"), whose antecedent was
already satisfied when it was typed.

---

## The shape of it, across the whole window

58 ubuntu install-step observations. The healthy mode is **0.4-0.6 min**; anything above ~2 min is
the fault showing.

```
08-18T04:56  0.6 0.4     08-18T22:05  0.4 0.4     08-19T04:16  0.7 0.4
08-18T12:39  0.5 0.4     08-18T22:57  0.4 0.4 *   08-19T04:39  1.8 25.1 X
08-18T13:14  0.5 0.6     08-18T22:59  0.4 0.4     08-19T06:45  1.5 25.1 X
08-18T16:29 25.2 4.9 X   08-18T23:52  0.5 0.5     08-19T17:25  0.5 0.5
08-18T18:02  1.1 4.2     08-19T00:12  0.5 0.4     08-19T20:50 13.7 25.2 XX
08-18T18:26  1.4 25.2 X  08-19T01:10  0.4 0.4     08-19T21:31 20.6 25.2 XX
08-18T19:04  0.4 1.3     08-19T02:04  5.6 1.7     08-20T03:09  0.4 0.6
08-18T19:36  0.5 0.6     08-19T02:27  1.2 1.8     08-20T03:30  0.4 0.4
08-18T19:58  0.4 0.5     08-19T02:37  0.4 1.3
08-18T20:43  2.5 0.4     08-19T03:16  2.3 5.9
08-18T21:21  1.0 0.8                                X = job killed at the wall
                                                    * = concurrency cancellation, NOT a timeout
```

**Eight of 58 ubuntu jobs (14%) were killed by the install step.** Six of the eight were still
inside apt at the 25-minute wall; the other two got past it (13.7 m, 20.6 m) and were killed with
the suite mid-flight. Four more survived after losing 4.2-5.9 minutes to it.

**It is episodic, not standing.** Clean before 08-18T16:29, clean again for eight straight hours
overnight, entirely clean at 08-19T17:25 between two failure episodes, and clean on both runs since.
That pattern is why the acceptance question at the bottom of this file matters so much.

**And note `08-18T22:57`.** Both shards read `cancelled` with a 0.4 min install — those are
concurrency cancellations from a superseding push. Same conclusion string as a 25-minute apt hang,
completely different cause, and nothing in the job name or status distinguishes them. That is the
mis-attribution this slice is about, visible in a single row.

## The primary measurement

The nine most recent runs, in full. Step 4 is `uv run --group bench playwright install --with-deps chromium`;
step 7 is the pytest shard. Job budget `timeout-minutes: 25`.

| run | created (UTC) | shard | conclusion | install | suite | total |
|---|---|---|---|---|---|---|
| 32327218825 | 08-20T03:09 | 1/2 | success | 0.4m | 13.7m | 14.2m |
| 32327218825 | 08-20T03:09 | 2/2 | success | 0.6m | 13.1m | 13.8m |
| 32304351703 | 08-19T21:31 | 1/2 | **cancelled** | **20.6m** | 4.6m | 25.3m |
| 32304351703 | 08-19T21:31 | 2/2 | **cancelled** | **25.2m** | — | 25.3m |
| 32300702632 | 08-19T20:50 | 1/2 | **cancelled** | **13.7m** | 11.5m | 25.3m |
| 32300702632 | 08-19T20:50 | 2/2 | **cancelled** | **25.2m** | — | 25.3m |
| 32281490663 | 08-19T17:25 | 1/2 | success | 0.5m | 13.8m | 14.4m |
| 32281490663 | 08-19T17:25 | 2/2 | success | 0.5m | 12.4m | 13.1m |
| 32224739831 | 08-19T06:45 | 1/2 | success | 1.5m | 13.8m | 15.3m |
| 32224739831 | 08-19T06:45 | 2/2 | **cancelled** | **25.1m** | — | 25.2m |
| 32216556697 | 08-19T04:39 | 1/2 | success | 1.8m | 13.7m | 15.6m |
| 32216556697 | 08-19T04:39 | 2/2 | **cancelled** | **25.1m** | — | 25.2m |
| 32215161053 | 08-19T04:16 | 1/2 | success | 0.7m | 13.2m | 13.9m |
| 32215161053 | 08-19T04:16 | 2/2 | success | 0.4m | 13.5m | 13.9m |
| 32211571183 | 08-19T03:16 | 1/2 | success | 2.3m | 13.4m | 15.9m |
| 32211571183 | 08-19T03:16 | 2/2 | success | 5.9m | 13.7m | 19.7m |
| 32209202425 | 08-19T02:37 | 1/2 | success | 0.4m | 13.7m | 14.2m |
| 32209202425 | 08-19T02:37 | 2/2 | success | 1.3m | 13.7m | 15.1m |

**The suite is flat.** Every job that reached it: **12.4–13.8 min**, no trend. Ubuntu is the
*fastest* arm — windows runs 15.2–17.7. Nothing in this table supports a capacity finding about the
test suite on either OS.

**The install is the entire variance**, and it is bimodal: twelve jobs at 0.4–2.3 min, then 5.9,
then six at 13.7–25.2. **Six deaths in this window, all six with install ≥ 13.7 min. Four ran ZERO
tests.** (Eight deaths across the wider 58-job window above.)

**A caveat that must travel with the table.** The four 25.1–25.2 m figures are **right-censored**:
a cancelled step's `completed_at` is the cancellation instant, so those are
`min(install_time, remaining_budget)`, not install time. Nothing in any log bounds how long they
would have taken. Any statistic mixing the twelve uncensored with the six censored observations
(a median over all eighteen, say) describes neither population — don't compute one.

### The control group was already in the repo

Windows runs the same browser download with **no** `--with-deps`, therefore no apt:

```
windows `playwright install chromium`, 18 jobs, same nine runs
  min 18s   median 20s   max 29s
  18s 18s 19s 19s 20s 20s 20s 20s 20s 20s 20s 20s 21s 21s 22s 22s 25s 29s
```

**18/18 completions, zero hangs.** The one windows `failure` (32224739831 shard 2/2) ran a full
16.2 m suite — a real test failure, not a timeout.

### The A/B that settles it: same run, same second, two runners

Run **32300702632**, both ubuntu shards, started `20:50:20Z`, identical command.

| | shard 1/2 (recovered) | shard 2/2 (died) |
|---|---|---|
| runner-local mirror | `Hit: http://azure.archive.ubuntu.com` | `Ign: http://azure.archive.ubuntu.com` |
| `apt-get update` | `Fetched 11.4 MB in 2s (6988 kB/s)` | fell back to `https://archive.ubuntu.com` |
| then | package fetch at ~76 kB/s | **stalled 22m55s** on one 126 kB `InRelease` |
| package fetch | `Fetched 21.1 MB in 13min 5s (26.9 kB/s)` | never reached |
| outcome | 13.7 m install, suite ran, killed at the wall | killed at the wall, **0 tests** |

Verbatim from shard 2/2's log — the last two lines before the kill, twenty-three minutes apart:

```
2026-08-19T20:52:42.5832602Z Get:5 https://archive.ubuntu.com/ubuntu noble-security InRelease [126 kB]
2026-08-19T21:15:37.6723382Z ##[error]The operation was canceled.
```

The failure is a runner drawing an unreachable `azure.archive.ubuntu.com`, apt failing over to the
public archive, and apt having **no effective timeout** on the stall. It is per-runner, not
per-shard: both shards run the identical command before any test executes, and there is no
mechanism by which the shard number could reach it. (Five of six deaths landing on shard 2/2 is a
scheduling coincidence, not a signal.)

---

## What `--with-deps` actually installs on this image

From the same log (shard 1/2, which got far enough to print the plan):

```
The following NEW packages will be installed:
  fonts-freefont-ttf fonts-ipafont-gothic fonts-tlwg-loma-otf fonts-unifont
  fonts-wqy-zenhei xfonts-cyrillic xfonts-encodings xfonts-scalable xfonts-utils
0 upgraded, 9 newly installed, 0 to remove and 16 not upgraded.
Need to get 21.1 MB of archives.
After this operation, 79.5 MB of additional disk space will be used.
```

**Nine packages. Every one a font. Zero shared libraries.** Chromium's actual runtime dependencies
are already on the `ubuntu-latest` image, which is *why* apt reports nothing else as new — it
reported each of them `already the newest version`. That is structural rather than lucky: the image
ships Chrome, Chromium, Edge and Firefox, which hard-depend on the same set.

So the flag buys CJK / Cyrillic / Thai / Unifont glyph coverage, at 21.1 MB over the mirror that
killed a third of the ubuntu arm.

### Nothing in this repo renders a non-Latin glyph

Scan of every `.py` and `.html` under `tests/` — **twelve distinct non-ASCII codepoints**:

```
U+2014 (em dash) x1387   U+2026 x20   U+200B x3   U+202E x3   U+2192 x3
U+2705 x2   U+2286 x2   U+200E x1   U+FEFF x1   U+00B1 x1   U+26A0 x1   U+FE0F x1

CJK / Kana      : NONE
Cyrillic        : NONE
Thai            : NONE
```

And every one of them is in a **Python comment or string constant**, not an HTML fixture. The
zero-width and bidi-override characters (`U+200B`, `U+202E`, `U+200E`, `U+FEFF`) are inputs to
`src/ultracua/audit.py:191`'s stripper regex, tested in Python with no browser involved.

**The strongest evidence is still the windows control**, not this scan: the windows arm has never
had any of those nine packages and is 18/18 green on the same suite against the same fixtures.

### The residual risk, stated rather than waved away

Fonts change text *metrics*, and metrics reach one mechanism that matters:
`src/ultracua/snapshot.py:85-90` decides which elements enter an `Observation` using
`r.width < 1 || r.height < 1` and `r.top > innerHeight`, and `elements` is the fingerprint basis. A
font substitution that changed line-wrapping could in principle reorder or drop a candidate.

Why that is accepted:

* the nine packages **add** glyph coverage for scripts this repo never renders; they do not replace
  the default Latin face;
* no test asserts on a bounding box (`getBoundingClientRect` appears twice, both inside
  `snapshot.py`), there are no screenshot comparisons, and `drift_bench`'s "vision" is a simulated
  `OracleProvider`, not pixels;
* **windows has an entirely different font stack** (no DejaVu, none of these nine) and produces
  identical results, including the structural fingerprints. If reading order were font-fragile the
  two arms would already disagree.

The direction of a mistake here is loud: a genuinely missing *library* means Chromium cannot launch,
so every browser test dies at once, not subtly.

---

## What this refutes

**§13's step 0 proposed two levers. Neither addresses the fault.**

| lever | verdict |
|---|---|
| regenerate `.test_durations` | **no-op.** Four of six dead jobs ran zero tests; balance cannot help a job that never collects. And the shards are *already* balanced: ubuntu 13.7/13.1, windows 16.3/15.4 — ~4% apart. The file is genuinely stale (836 ids against 1191 collected, and `ci.yml` said "836 tests"), but that costs wall-clock balance only, and the measured balance is fine. |
| raise `timeout-minutes` 25 → 40 | **rescues 2 of 6, and buys the other 4 a fifteen-minute-longer death.** They were still inside apt at the 25-minute wall with no sign of finishing. |

Neither was taken. `ci.yml`'s job budgets are **unchanged**.

---

## What was done instead

1. **Deleted `--with-deps`**, and collapsed the two OS-conditional install steps into one shared
   step running the byte-identical command windows already ran. That deletes 100% of the observed
   failure surface, and keeps windows usable as a control for linux.
2. **Budgeted every authored step except one.** The exception is `Run the test suite`, whose
   duration *is* the capacity signal. A provisioning over-run now fails at a **named** step in
   minutes instead of at an anonymous job wall in 25.
3. **Pinned the class** in `tests/test_ci_provisioning.py` — one browser install, shared by both
   OSes, no `--with-deps`/`apt-get`/`sudo` in any step; plus a standing arming cell that mutates the
   workflow four ways and requires each to be caught.

### What was deliberately NOT done

* **No `timeout-minutes` on the suite step.** A second ceiling underneath the job wall would be a
  scheduled future red on the merge gate, on the arm that is actually growing (windows, 15.2–17.7 m
  against a 25 m wall), with nothing to acknowledge it. That is the D0 over-refusal shape.
* **No job-budget raise.** The removal restores ~11 min of headroom under the existing 25.
* **No claim that a job-level `cancelled` now means the suite over-ran.** It cannot be made to mean
  that: the `always()`/`failure()` tail runs past the wall on a failing job, and GitHub injects
  unbudgetable steps of its own (`Set up job`, `Post Install uv`, `Complete job`). An earlier draft
  asserted that converse with a worked sum; the sum was wrong on the failure path (41 > 40).
* **No smoke-launch step.** `playwright install` already ldd-validates and then swallows its own
  verdict, so a missing library is a genuine quiet outcome — but a hand-rolled launcher would be a
  second transcription of `src/ultracua/browser.py`'s launch path, and headless resolves to
  `chromium_headless_shell`, a *different* binary from the `chromium` the suite may exec. A guard
  that certifies the wrong binary is worse than none. Filed, not shipped.
* **No `SPLITS = 2` / two-OS-matrix pins.** Same class, same parser, deferred to keep this one slice.

---

## How to check this claim later, honestly

**A clean post-merge streak is NOT evidence.** Across the 58-job window, **44 jobs completed with the
install under two minutes while the flag was still present** — and run 32281490663 was entirely clean
at 17:25, sitting between two failure episodes. Eight hours of 08-18/19 overnight runs were clean too.
"Ten consecutive green ubuntu runs" is therefore satisfied by the **unchanged** workflow, so it cannot
distinguish the fix from the base rate. This is `CLAUDE.md`'s green-is-not-evidence rule pointed at
this file's own adjudicator, and it is the reason the checks below are static and distributional
rather than a streak count.

What *is* checkable:

* **Static** — `tests/test_ci_provisioning.py` fails if the flag returns, on every fast-tier run.
* **Distributional** — the ubuntu install step should now sit in the windows distribution
  (~18–30 s), not merely "under a minute". A single sample above ~2 min is worth investigating even
  if the job passes.
* **The A/B the merge itself performs** — after this change the ubuntu arm runs the full browser
  suite with those nine font packages absent. Both shards must be read, since the ~500 browser tests
  are split two ways and one shard is half the experiment.

**Unresolved, and it should stay written down:** whether the apt failures were a standing property
of the runner fleet or an external mirror incident that has already ended. The data is episodic —
hard onset 08-18T16:29, entirely clean at 08-19T17:25, dead again at 08-19T20:50. The removal is
correct either way, because the nine packages are unused; but do not let a quiet fortnight be read
as proof that removing them is what caused the quiet.
