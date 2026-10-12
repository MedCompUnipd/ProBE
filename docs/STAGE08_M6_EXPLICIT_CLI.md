# Stage 08 M6 - Explicit collection CLI entry point

**Status:** additive runner, not Snakemake-integrated. Stage 01-07 and M1-M4
are unchanged. The real GAF performance study M5 completed on six bounded
64-MiB windows (START/END head/middle/tail) without creating canonical results.

## Public interface

`scripts/pipeline/08_collect_annotations.py` takes four explicit completed
bundle directories and a destination. It has two modes:

- **Default:** `PREFLIGHT_ONLY`, calling the M2 preflight. This loads the Stage
  07 association projection and validates manifest identities, but does not
  open GAF contents, write outputs or publish Stage 08.
- **`--execute`:** calls the already-tested M4 collector on both complete
  normalized GAFs and writes one immutable Stage 08 bundle. The flag is a
  deliberate user opt-in, not a performance check or workflow invocation.

Neither mode enables `allow_invalidated_destination`. The CLI cannot replace
an existing published bundle. Replacement after workflow invalidation requires
an independently reviewed Snakemake wrapper that invokes M4 under the Stage 07
rerun contract. Do not remove a valid manifest manually to force a run.

This CLI does not change the evidence registry, donor selection, GO propagation,
truth/masking or scientific eligibility. All annotation evidence codes remain
unfiltered; START and END donor authorizations remain separate.

## M5 observations and limits

M5 scanned six noncanonical samples (three per release) totaling approximately
384 MiB. The measured M3 sample speed ranged from 103.5 to 123.5 MiB/s and
peak process RSS was 426.77 MiB. Available scratch space exceeded 5 TiB.
The diagnostic is not a full-source performance prediction. It did not verify
canonical-source SHA256 or original full-file line numbers, and it cannot
certify missing GO donor annotations.

The approximately 9-MB donor coverage table was written in **every** sample
regardless of the number of matched annotations. Do not extrapolate six such
fixed-size tables as six independent full-run outputs: M4 produces exactly one
coverage table for each release. Any output-size estimate must distinguish
constant per-release coverage overhead from variable source/link records.

## Validation

New tests use the existing M4 synthetic fixture and cover preflight-by-default,
explicit execution, completed-bundle immutability, preflight read-only behavior,
invalid options and help. They never use the canonical GAFs. The server must
validate Ruff, the focused test, all Stage 08 tests and project regression.

## Required integration before production

1. Inspect and patch the **actual current** `workflow/Snakefile`, the Stage 07
   invalidation approach and the pipeline config schema without assuming line
   anchors from older snapshots.
2. Add Snakemake's Stage 08 runner and dry-run tests for unchanged inputs,
   Stage 07-only parameter changes and Stage 08-only parameter changes. Ensure
   that no Stage 01-07 tasks are scheduled for Stage 08-only changes.
3. Validate publication/recovery with small fixtures and a bounded disk check.
4. Only then perform a deliberate full START+END GAF collection and audit
   its manifest/provenance; do not treat M5 samples as certified collection.
