# Parked work — not merged, preserved deliberately

## `causal-attribution` (branch `feat/shared-causal-attribution`, would-be 0.76.0)

An attempt to close **R3.2**'s attribution rule by extracting the recorder's `__ucturn` causal signal
into a shared `src/ultracua/attribution.py` and using it on the learn path to ATTRIBUTE writes.

**It is parked, not abandoned, and it must not be resumed by re-reading the diff alone.** The branch is
green (785 tests, `drift_bench` byte-identical) and was still found defective by a pre-merge adversarial
audit — the third time in this project that a fully-green change in this area was wrong. Round 4's
findings are recorded in `docs/open-defects.md`; the three that remained unresolved when it was parked:

1. **Cross-origin seq collision.** `seq` lives in `sessionStorage`, which is per-ORIGIN, but
   `seq_to_step` is one flat dict resolved at the end of the run. After a cross-origin hop the counter
   restarts at 1 and a later commit silently overwrites an earlier step's entry — a wrong-gate bug.
2. **A swallowed drain rebinds commits to the next step.** `attribution.drain` returns `[]` on any
   exception, so markers left in the buffer are read during a LATER step and bound to it.
3. **Page-synthetic clicks register as commits**, which can launder a deferred write into a
   confidently-attributed one.

Two findings WERE fixed before parking and the fixes are in the branch: the missing
`is_write_request` filter (a telemetry beacon became a "causal write" and displaced the real gate —
measured), and REPLACE-vs-UNION of the temporal attribution (`_merge_attribution`).

**What survives into the plan.** `docs/correctness-plan.md` slice **S6** takes the conservative half
only: use the causal signal purely as a REFUSAL oracle ("a write occurred whose cause the page cannot
prove" → refuse loudly), with no attribution, no seq→step map, and therefore none of hazards 1–2 above.
Hazard 3 remains in scope for S6 and needs a RED test.

## `round4-probes/`

Diagnostic scripts written by audit refuter agents while confirming round-4 findings. **They have no
assertions** — they print state — so they are kept OUT of `tests/`, where they would pass vacuously and
inflate the count. Run them directly with `uv run --no-sync python docs/parked/round4-probes/<f>.py`.
Two probes that DO assert (`test_zz_refuter_wrongate.py`, `test_zzz_refute_replace_gate.py`) remain on
the parked branch under `tests/`.
