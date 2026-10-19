# ProBE canonical V2 — FIX1 source baseline

Date: 2026-10-19

## Validated scientific contracts

- EXACT: literal IUPAC26, including B/J/X/Z without wildcard interpretation.
- EXACT taxonomy: same resolved TaxID or approved same-species cross-subtaxon policy.
- NONEXACT: same resolved TaxID, documentary admission, certified affine DP.
- GO: release-local START 2023_01 and END 2026_03.
- Documentary query evidence may span START and END, with explicit
  retrospective provenance; GO annotations remain release-local.
- Fragment and partial-sequence policies remain independently configurable.

## Regression evidence

- Canonical V2 suite: 344 passed, 1 skipped.
- Stage 2C shared suite: 337 passed.
- FULL EXACT focused regression: 61 passed.
- The seven operational Python scripts match the validated FIX1 source.

These are previously observed results, not tests rerun by this commit.

## Current baseline scope

This commit captures the validated FIX1 source implementation,
its scientific contracts, tests, workflow, and review configuration.

The global execution configuration remains BLOCKED.
This source baseline is not a certification of a full-dataset run.

## Unresolved global execution dependencies

1. Independently built and provenance-certified bounded DP executable.
2. Global START FULL EXACT pairs under literal IUPAC26.
3. Global END FULL EXACT pairs under literal IUPAC26.
4. Global START unified discovery certified for the target universe.
5. Global END unified discovery certified for the target universe.

The pinned upstream input identities and manifests also require
certification before global execution.

The 128-target pilot inputs and discovery cannot be substituted
for global inputs.

The Python environment currently used for QA is located in the
original repository and is not yet an independently provisioned
environment for this worktree.

No previous scientific outputs may be modified.
