# ProBE Stage 08: benchmark diagnostics (first integrated increment)

Status: implementation awaiting repository-side Ruff and full regression.
Repository baseline: `31cf555`. Rename to PROGENY is deferred.

## Purpose

Read an **already published, immutable** M12 V2 benchmark and create a
separate immutable diagnostic bundle. This is **not** a benchmark generator,
a donor resolver or an evaluator. The original GAF, M8, M10c and M12 bundles
are never modified. The diagnostic cohort can be filtered without changing
ground truth or the universe in the original M12 files.

Mandatory inputs:

- M12 V2: `benchmark_terms.tsv`, `target_aspects.tsv`, `term_audit.tsv`,
  `manifest.json`; optional `negative_terms.tsv`.
- Input `targets.tsv` for original accession, TaxID and target ID.
- Stage01 `01_targets.tsv` for normalized sequence length, taxon, accession,
  normalization policy and identity collision consistency.

The one known duplicate accession is collapsed only after confirming identical
TaxID, normalized length, sequence SHA256 and normalization policy. The
benchmark itself has already validated the underlying target-sequence bytes.

## Outputs

- `summary.json` and `REPORT.md`: principal totals and caveats.
- `protein_features.tsv`: one row per original accession, TaxID,
  `length_aa`, per-aspect EVAL/SKIP/NEUTRAL, NOVEL/CONFIRMATION and IA sum.
- `aspects.tsv`: both protein-GO association counts (redundant over proteins)
  and distinct GO identifiers for each aspect/status.
- `go_frequency.tsv`: distinct proteins annotated with each aspect/GO/status.
- `length_bins.tsv`: length strata and associated GO counts per aspect/status.
- `taxa.tsv`: exact NCBI TaxID, number of distinct proteins and aspect counts;
  optional labels from user-supplied taxonomy TSV.
- `reasons.tsv`: counts by scientific M12 reason, status, aspect.
- `negative_observations.tsv`: diagnostic NOT observations for selected
  eligible target/aspects (not the second ground truth).
- `ia_bins.tsv`, `ia_categories.tsv`: only if **external** IA weights were
  explicitly provided. No IA is inferred from EVAL frequencies.
- `publication_manifest.json`: input hashes, applied filters, output hashes.

Data integrity rules: verify source manifest counts on unfiltered reports,
NOVEL/CONFIRMATION partition EVAL, and audit NEUTRAL totals. Abort instead of
publishing a partial output. New output destination only; atomic rename of
staging directory after a successful complete calculation.

## Options

- `--benchmark`: published M12 V2 output directory.
- `--targets`: original input target TSV (symlink accepted).
- `--stage01`: Stage01 canonical target metadata TSV.
- `--output`: nonexistent, separate diagnostic directory.
- `--length-edges`: lower bin edges, default `0,300,600,1023,1500,3000`.
- `--min-length`, `--max-length`: **inclusive** target AA length filters.
- `--taxids`: comma-separated NCBI taxon IDs.
- `--taxon-names`: optional TSV of `taxid,scientific_name`.
- `--ia`: optional frozen `go_id,ia` TSV; corpus and ontology must be
  independently validated before using these weights for scoring.

GO statistics concern propagated positive truth/masks from M10c/M12.
Each `(query_id, aspect, GO, status)` represents one association, not a
count of different GO IDs. F/P/C protein counts overlap across aspects.

## Next increment: variant-aware Snakemake

Must inspect the actual server `workflow/Snakefile`, execution scripts and
config merge semantics before editing workflow rules. Preserve published
Stage01-07 and Stage08 bundles. Every variant must have a complete resolved
config, dependency fingerprints, a distinct output path and an immutable
manifest. The planner should show retained artifacts and exactly which rules
would execute **before** launching a Snakemake run. Changes to M12 evidence
classes can reuse M10c; changes to Stage07 donor eligibility must invalidate
M8 and later. Deletion must be opt-in and dependency-aware with a dry-run.

IA for weighted metrics is a separate planned branch: complete positive GOA
START source corpus, propagation along frozen intra-aspect `is_a/part_of`,
per-object GO deduplication, conditional frequencies over all direct parents,
provenance-locked GO weights. The previously supplied target-specific IA
exploratory script is **not** a substitute for global GOA START IA.
