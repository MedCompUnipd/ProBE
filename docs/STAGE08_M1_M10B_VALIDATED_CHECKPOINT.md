# ProBE Stage 08 - validated M1-M10b checkpoint

## Scope

This checkpoint records the implementation, tests, and scientific boundaries
of Stage 08 through M10b, on branch `chatgpt/pre-resolver-v2`. It does **not**
represent a completed GO benchmark or a completed GO annotation interpretation.
Stages 01-07 remain frozen; no canonical GAF is rescanned as part of the
checkpoint.

## Canonical raw collection (M8)

- Run: `pre-resolver-v2-2023_01-2026_03` on `thanos`.
- START release `2023_01`, normalized GOA GAF 184,318,414,244 bytes,
  SHA256 `648524ffe1282afcac25a96b0db3fd041c0769aeccf1d3d4ad7d5ea9c5c712d8`.
- END release `2026_03`, normalized GOA GAF 119,974,698,776 bytes,
  SHA256 `e11b67e4b0899dbd95feec59759ce8b72211396da77333ea3df75044eb7b7011`.
- Stage 07 accepted donor associations: 150,185 START; 148,313 END.
- M8 source rows retrieved: 3,308,322 START; 3,138,181 END;
  **6,446,503 total**.
- Donor-target annotation links: 3,324,167 START; 3,153,364 END;
  **6,477,531 total**.
- Authorized donor associations without GAF rows: 7,856 START;
  6,821 END; **14,677 total**. These count associations, not necessarily
  distinct target proteins.
- Publication verified with complete EOF and full GAF SHA256 checks.
- Archived server report:
  `/data/PROGENY/tmp/stage08_m8_execute_oNz6u3.txt`.
- Completed raw collection bundle:
  `results/pipeline-v2/common/08/` inside the canonical run.

## Post-collection components

- M9 evidence registry: 26 legacy codes represented; all 23 observed codes
  from retrieved START/END donor annotations recognized. Evidence profiles
  remain user-configurable; no profile is assumed to define benchmark truth.
- Pinned GO snapshot SHA256:
  `42d7869b3a47cd4a9751d7f2f373acf737b64a1435e4b02b169d134b240dc666`.
- Ontology audits found 40,267 active GO terms and 78,639 eligible unique
  same-aspect `is_a`/existential `part_of` direct edges, with an exact match
  between the independent eligible-edge projection and the ProBE loader.
- M10a: positive ancestor / NOT descendant closure engine with bounded
  per-instance caches, cycle and identifier validation.
- M10b: GO propagation support preserving donor / source provenance,
  direct vs inherited distinction, source evidence code, polarity and
  positive-NOT coexistence without prematurely assigning EVAL or SKIP.

## Validation at M10b

- Ruff checks/format: PASS on new M10b code.
- M10b focused tests: 24 passed.
- Stage 08 tests: 267 passed.
- Full project suite: 864 passed, 1 skipped (optional TESTING oracle missing).
- M10b pinned ontology check: PASS.
- Canonical M8 bundle unchanged: YES.
- Archived server report:
  `/data/PROGENY/tmp/stage08_m10b_donor_go_GFCEBZ.txt`.

## Scientific interpretation and remaining work

- Raw collection is distinct from evidence-policy selection, GO inheritance,
  temporal changes, benchmark EVAL/SKIP labeling, and scoring.
- Positive propagation is upwards via the approved GO structural edges;
  NOT propagation is downwards. Source evidence code and donor provenance
  must be preserved. Regulatory/context relations are not silently treated
  as equivalent to inherited positive function.
- The `go.owl` snapshot and normalizer constrain the recognized GO universe;
  discarded or obsolete GOA terms are not recoverable from the M8 bundle.
- The 14,677 zero-row associations are observational coverage outcomes,
  not proof of biological absence.
- M10b is a tested library, not yet the large-scale interpretation of all
  M8 source assertions. Later stages must construct temporal classification,
  truth/masking, output IDs, per-aspect assessment and scoring contracts.
- No production input files, GAF contents, pipeline run outputs or temporary
  local configurations belong in Git.
