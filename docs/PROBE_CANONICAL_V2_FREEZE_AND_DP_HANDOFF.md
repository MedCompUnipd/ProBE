# ProBE canonical V2 — frozen scientific baseline / DP handoff

Date: 2026-10-21. Branch: `chatgpt/probe-v2-validated`.
Source HEAD before consolidation: `7250810e1c37880fbb077b6f3184f98b520fc160`.
The final consolidation commit SHA is reported by the on-server freeze gate.

## Validation evidence

- Production cold-start fresh128 directory:
  `/data/PROGENY/ProBE-runs/canonical-v2-fresh128-phase1-6f0d55124262`.
- START `2023_01`, END `2026_03`, 128 original target IDs; Stage 00 is external.
- Fresh run: `PHASE_STATUS=COMPLETE`, `SCIENTIFIC_STATUS=PASS_FRESH128_EXISTING_DP`, all 61 Snakemake steps completed, scientific integral verifier PASS.
- Independent comparison against the October 19 FULL fragment FIX1 pilot:
  `PROBE_V2_FIX1_VS_FRESH128_20261021T082256Z_jk6t2obc_REPORT.tar.gz`;
  `PASS_ROW_LEVEL_SCIENTIFIC_EQUIVALENCE`: 32 TSV tables byte-for-byte identical and 4 scientific JSON summaries equivalent; 36/36 PASS.
- Scientific counts: 2,717 Stage 06A jobs, 243 EXACT, 1,322 DP-eligible NONEXACT, 1,152 explicitly excluded NONEXACT; 1,322 complete DP pair results (2,644 directions) across 13 chunks, 535,683,860 certified DP cells; 260 accepted donors; 3,068 START and 2,574 END GO association links; 17,864 M10c propagated term records; 1,439 benchmark term rows (209 EVAL, 1 NEUTRAL, 1,229 SKIP).
- The consolidation source files are copied verbatim from `reviewed_source/` in the successful fresh128 report, **after** Ruff. They are checked against the run's `source_identity.json`; do not substitute the earlier pre-Ruff ZIP overlay.

## Fixed scientific contracts (do not change as a side effect of DP work)

1. EXACT is literal IUPAC26 including `B/J/X/Z`, not a wildcard search. FULL EXACT scanning includes `FRAG=1` when the whole sequence literally matches, after which downstream eligibility policies still apply.
2. NONEXACT needs independent documentary eligibility and the **same resolved TaxID** for DP; do not make DP choose the biological candidate set.
3. START and END candidate discovery are release-specific. EXACT documentary lookup may use START ∪ END search keys with origin-release provenance, as the **audited retrospective look-ahead mode**. This is not prospective-only discovery and must not be mislabeled as such.
4. GO imports are **strictly release-local**; END GO never becomes START GO. Positive GO closure, negative `NOT` descendant inference under the permitted ontology relations, evidence classes, confirmation and neutral masking are handled by existing tested stage logic; retain original assertions and provenance.
5. Experimental END annotations define eligible temporal benchmark truth under policy; the benchmark is **not** simply END-minus-START. Predictors and external scoring metrics are out of this DAG.
6. Scientific donor policy in the validated run: `same_species_exact`, `partial_sequence=allow`, `documentary_fragment=allow`. The current DP backend is `certified-geometric-bounds-v2`, two glocal affine directions, literal match +2/mismatch -1/gap open -3/extend -1, as implemented (preserve deterministic tie-breaks).

## Snakemake operating boundary

Snakemake manages dependencies, scheduling, resource limits and `--rerun-incomplete` restart. Raw Stage 01–05R1 and canonical producer scripts validate their own data products; final standalone scientific verification checks all required scientific artifacts. Do not add a second scheduler or parse-time SHA scan of enormous releases.

Reproduce via `workflow/canonical_v2_fresh.smk` and the `probe-canonical-v2-fresh-recipe-v1` recipe. This freeze commits the validated workflow and stage implementation; it does **not** create a general-purpose Stage 00 adapter, CLI to generate recipes from all new data sources, or predictor metrics. Those are explicitly separate boundaries. Use a fresh recipe and new run root for different release/target experiments.

For future runs, export `TMPDIR`, `TMP`, `TEMP` pointing under `/data/PROGENY/tmp/probe-v2-scratch`, not `/tmp`; verify that tools using private scratch respect those variables. Use `tmux` for long SSH jobs.

## Next chat scope: exact large-pair DP

Implement a workload planner that inventories **all** independently admitted NONEXACT pairs, with `(release, target_id, RID)` keys, both directional certified cell counts, dimensions, resources and backend assignment. Preserve the current exact bounded backend for ordinary pairs and independently resumable chunks; the 500M-cell total cap is per invocation, not the entire dataset. A **single pair** exceeding `max_pair_cells` (default 50M cells across both directions) requires an exact memory-controlled backend, no heuristics or silent scientific exclusions.

Evaluate a rolling-state affine DP with accumulated path statistics (possible because the 49-column two-direction TSV does not require CIGAR), affine divide-and-conquer, and tiled certified traceback alternatives. Prove identity with the frozen C++ reference including tie-breaks, free terminal gaps, ambiguous IUPAC characters, asymmetry, coordinates, gap openings and all derived metrics. Measure memory/CPU; add interruption/resume checkpoints inside one pair. The common merge must exhaustively reconcile expected vs produced keys, enforce no duplicates and deterministic order, and furnish provenance for each backend while keeping existing Stage 06C1, 07, M10c and temporal logic untouched.

Run unit, differential, metamorphic and randomized tests, one controlled integration on selected difficult pairs, then a **new** isolated fresh128 E2E only after DP implementation passes. Do not run the ~145k target universe. Do not touch the previous completed fresh128 or October 19 FIX1 results, `main`, or `/data/PROGENY/ProBE-chatgpt`.
