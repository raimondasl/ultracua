"""ultracua benchmarks — deterministic local fixtures + a learn-vs-replay runner.

This is the Phase-1 micro-benchmark: a small static-HTML flow we control end to end, so
the cache's replay speedup and correctness can be measured reproducibly with no network,
no API key, and no site drift. Public benchmarks (see the survey in the PR) plug in later
for realism; this stays as the fast, deterministic inner-loop check.
"""
