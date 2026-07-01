# Examples — a real, read-only recurring data-pull

A worked end-to-end example on a **real website**, read-only (no login, no writes): pull a fresh data
point each morning, returning it as structured JSON. It's the cleanest way to *see* ultracua's one
trick — **learn a navigation once with an LLM, then replay it deterministically at 0-LLM** — and it
doubles as a demo script you can record.

The runnable version is [`examples/hn_digest.py`](examples/hn_digest.py).

## The use case

> *"Each morning, open the **top story's discussion on Hacker News** and give me its title, points,
> and comment count."*

Hacker News is a good demo target: real and recognizable, ~15-year-stable HTML, no anti-bot, scrape
tolerant, visually clean, and it has a natural one-click navigation (front page → the top story's
comments) whose data is **fresh on every replay**.

## Run it

```bash
uv run python examples/hn_digest.py            # headless
uv run python examples/hn_digest.py --headed   # watch the browser (for a recording)
uv run python examples/hn_digest.py --fresh    # forget the learned flow and re-learn
```

Or as a few lines with the library API:

```python
import asyncio
from ultracua import FlowSpec, learn_flow, approve_flow, replay_flow

spec = FlowSpec(
    name="hn-top-discussion",
    start_url="https://news.ycombinator.com",
    goal="open the comments/discussion page of the current top story on the front page",
    extract="the story title, its score in points, and the number of comments",
    extract_schema={"type": "object", "properties": {
        "title": {"type": "string"}, "points": {"type": "integer"}, "comments": {"type": "integer"}},
        "required": ["title"]},
)

asyncio.run(learn_flow(spec))   # one-time: the agent reasons out the navigation (~15-20s)
approve_flow(spec)              # a human verifies once, then trusts the replay
data = asyncio.run(replay_flow(spec))   # every run after: 0-LLM navigation, returns fresh data
print(data)                     # {'title': 'Emacs 31 Is Around the Corner…', 'points': 101, 'comments': 15}
```

Or entirely from the CLI (the spec is saved under `.ultracua/specs/`; the `extract_schema` is
library-only):

```bash
uv run ultracua flow learn  --name hn --url https://news.ycombinator.com \
  --goal "open the comments page of the current top story" \
  --extract "the story title, its points, and the number of comments" --verbose
uv run ultracua flow approve --name hn
uv run ultracua flow replay  --name hn          # prints the JSON; exits non-zero on drift
uv run ultracua flow status  --name hn          # health: runs / last success / failures
```

## What actually happens (measured against the live site)

| | learn (first run) | replay (every run after) | speedup |
|---|---|---|---|
| **data flow** (returns JSON) | 16.6 s | **3.9 s** | ~4× |
| **navigate-only** (`extract=None`) | 8.8 s | **~1.3 s** (3 runs: 1.26 / 1.36 / 1.41 s) | ~6.5× |

Two honest notes, both worth saying out loud in a demo rather than hiding:

- **"0-LLM replay" means 0-LLM *navigation*.** The data flow's replay still makes **one** extraction
  call to read the answer off the final page — that's most of the 3.9 s. The navigation itself is
  deterministic and LLM-free. A `navigate-only` flow (or a pinned-selector read — `pin_read` /
  `--pin-read`, which pins a deterministic 0-LLM read of a scalar answer) replays with *zero* LLM calls.
- **On a real site, replay is network-bound (~1–4 s), not the ~50× of local-fixture benchmarks.** The
  win here isn't raw milliseconds — it's that replay drops the **multi-step agent reasoning** (and its
  cost and flakiness) and reproduces the *exact* path every time, returning structured data you can
  trust or fail loudly on.

## What it demonstrates (the talking points)

- **Define → learn → approve → replay** in a few lines; the spec persists.
- **Deterministic 0-LLM navigation** — the same path every run, no agent re-reasoning, no flaky clicks.
- **Structured output** — `extract_schema` gives clean, validated JSON.
- **Trust controls** — `replay(require_approved=True)` refuses an unapproved flow; a change in the
  data's *shape* vs the learned run is treated as drift; `on_drift="raise"` fails loud so a cron job
  alerts instead of returning garbage.
- **Observability** — `--verbose` logs each run with a `run_id` and the token usage + $ cost.

## Recording a demo reel

- Run **`--headed`** so the browser is on screen; the contrast between the deliberate ~15 s **learn**
  and the snappy **replay** is the punchline.
- Show the **two runs back-to-back**: first `--fresh` (learn + the agent navigating), then a plain run
  (instant replay returning the same shape with *today's* numbers).
- Add **`--verbose`** for a technical audience — the `run_id`, step trace, and `usage=[… ~$0.00…]`
  cost line land well.
- End on **`ultracua flow status --name hn`** (the fleet-health view) to imply "now schedule it."

## Swap the site

Change `start_url` / `goal` / `extract` to retarget. Good read-only candidates:

- **PyPI** — `https://pypi.org/project/<pkg>/`, *"the latest released version and its date"* (a real
  dependency monitor). Navigate via "Release history" so there's a cacheable step.
- **Wikipedia** — the main page → *"today's featured article title and its first sentence"*.

**Avoid** Google, Amazon, social networks, ticketing, and anything behind a login or with aggressive
bot defenses — both for reliability (a captcha mid-recording) and because this example is deliberately
read-only. Be a good citizen: respect `robots.txt`, keep the default pacing, and don't hammer a site.
