# ProBE V2 — B3 autonomous Stage 05 → 06A preparation

## What this implements

This package connects the already validated B1–B2c candidate-provenance
projection to a single, self-contained Stage 05A2/05C2/05D/06A preparation.
It uses the existing scientific Stage 05C2 and 06A Python programs unchanged.
The FULL UniProt V2 sequence materialization is release-scoped, keyed by RID,
checked against Stage 01A_FULL output SHA256, and performed at most once per
release **when new production is explicitly authorized**.

Inputs: the two immutable B2c projection bundles; Stage 02B target taxonomy
TSVs; Stage 04B direct anchor TSVs; Stage 01A_FULL START/END manifests and
FASTA; canonical prepared targets; the existing 05C2 and 06A CLI programs.
There is **no input from any historical 05A2, 05C2, 05D, 06C1 or Stage 07
publication**. Existing scientific outputs remain untouched.

Generated in a new output directory: combined `05A2_final_candidate_union.tsv`,
independent `05C2_taxonomy_audit.tsv`, separate 05D START/END sequence stores,
`06A_alignment_jobs.tsv`, existing CLI summaries, and a SHA-linked provenance
summary. No DP jobs are executed by this component. It creates an alignment
workload but makes no GO donor eligibility decision.

The B2c pair domain is preserved without filtering or trimming. Lineage-only
candidate records remain present even when the target has GO-positive/FULL
EXACT hits. Original GO, FULL, rescue and lineage JSON payloads remain in 05A2
and their binary provenance fields are kept. FULL-only candidates use the
existing `STAGE2C_FULL_EXACT` discovery-channel spelling expected by the
validated resolver; the original `full_exact_species_wide` flag is retained.

A FULL ordinal conflict is a **hard failure**, never silently resolved to a
convenient RID. Other candidate metadata and FULL FASTA bytes must agree.
Target bucket values are supplied independently by Stage 02B. Stage 05C2
continues to preserve audit-only direct accession drift, without upgrading
it to PRIMARY. Stage 06A checks literal EXACT against the materialized
sequences; no dynamic programming takes place here.

## Limits and next gate

This is the autonomous **Stage 05–06A producer**, not yet a published
canonical Snakemake DAG or a replacement of the validated 06C1/resolver.
The future Stage 06 completion must enforce the final EXACT donor contract v1
and forbid DP for all NONEXACT cross-TaxID pairs, including audit-only pairs.

Production runs require real B2b lineage attestation, the rescue reference
and source-scanner manifests in the upstream DAG. B3 assumes those B1–B2c
products have already passed their own scientific validation and checksums.
The previous 16 October results must be *adopted* without invoking this
producer; never point it at an existing frozen output directory.

## Acceptance tests

The synthetic tests demonstrate: mixed EXACT and NONEXACT lineage candidates
are both preserved; FULL-only discovery reaches the unchanged resolver;
05C2 audit is generated independently; 06A tests literal sequence identity;
missing/corrupt FULL or altered B2c input fails closed; no DP or GO propagation
is executed. The complete Stage 06C1 → Stage 08 integration remains a later
gate and must be validated independently.
