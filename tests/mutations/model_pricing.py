"""Mutations for the cost table and the configured models — 0.120.0.

Applied by `scripts/prove_red.py` to a scratch copy of `src/`, so these reach `obs.py` and
`config.py` for real.

WHY THESE EXIST. `_PRICES` stopped being a convenience the moment B3 made the customer bench gate
on cost: an absent entry takes `cost_usd` to None, which `outcomes._cost_of` turns into
`BenchRecordError("unpriced_spend")` — correct, loud, and discovered at the END of a paid run.
The guard is that the table is kept ahead of `config.settings`, DERIVED rather than repeated.

Both directions are attacked. Pricing too little makes a run unpriceable; pricing too much turns
an unknown bill into a confident wrong number, which is the failure 1.3 spent a slice removing
from `variance._cost` and `drift_bench`.

(id, module-relative path, find, replace, why it must not survive)
"""

MUTANTS = [
    (
        "the_strong_tier_model_loses_its_price",
        "obs.py",
        '    "claude-opus-5": (5.0, 25.0),\n',
        "",
        "the configured strong model with no price entry is the 0.120.0 defect verbatim: every "
        "call unpriceable, cost_usd None, and the customer bench refuses the run it just paid for.",
    ),
    (
        "an_unknown_model_gets_priced_anyway",
        "obs.py",
        "        if model and model.startswith(prefix):\n            return p\n    return None",
        "        if model and model.startswith(prefix):\n            return p\n    return (5.0, 25.0)",
        "the other direction, and the worse one. A default price turns an UNKNOWN bill into a "
        "confident wrong number — the `or 0.0` shape 1.3 removed from two readers, one layer down. "
        "`unpriced_calls` would report zero and the bench would publish a fabricated total.",
    ),
    (
        "the_default_model_drifts_off_the_price_table",
        "config.py",
        'os.getenv("ULTRACUA_MODEL", "claude-opus-5")',
        'os.getenv("ULTRACUA_MODEL", "claude-opus-6")',
        "the DRIFT itself: a plausible future default that no entry covers. This is why the guard "
        "derives the models from `settings` instead of listing them beside the table — a hand-typed "
        "list is only as good as its worst entry, and both lists are edited by different slices.",
    ),
    (
        "the_fast_tier_model_drifts_off_the_price_table",
        "config.py",
        'os.getenv("ULTRACUA_FAST_MODEL", "claude-haiku-4-5")',
        'os.getenv("ULTRACUA_FAST_MODEL", "claude-haiku-5")',
        "the same drift on the tier the guard could plausibly have forgotten. `settings.tier` "
        "defaults to strong, so the fast model is only reached under ULTRACUA_TIER=fast — which is "
        "exactly the kind of 'rarely exercised' path a derived check must still cover.",
    ),
]

# Empty, and it must stay that way: an entry here is a hole in the matrix with a reason attached.
KNOWN_SURVIVORS: dict = {}
