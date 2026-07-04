"""ultracua capability evals — a MANUAL, aspirational evaluation suite (NOT CI).

Measures what ultracua can do today against where it is going: the suite covers the shipped core
(learn/replay/recorder/writes/drift) AND the ROADMAP.md "Innovation horizons" scenarios (H1-H16).
Horizon scenarios probe for capabilities that mostly DO NOT EXIST YET — scoring low there is
expected and is the point: the suite is the target, and re-running it over time charts progress.

Run it manually (never wired into CI — the key-less pytest suite in tests/ is the regression gate):

    uv run python -m evals.run --list                # what's in the suite
    uv run python -m evals.run --estimate            # $ cost of a full run vs each tier/group
    uv run python -m evals.run                       # key-less scenarios only ($0, default)
    uv run python -m evals.run --include-llm         # + real-LLM scenarios (costs real $)
    uv run python -m evals.run --group h03,h07       # partial run: just those horizons
    uv run python -m evals.run --include-llm --budget 2.50   # hard $ cap, skips past it

See evals/README.md for the cost table and how to add scenarios.
"""
