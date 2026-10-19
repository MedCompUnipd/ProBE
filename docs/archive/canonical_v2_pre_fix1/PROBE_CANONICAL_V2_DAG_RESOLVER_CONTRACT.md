# ProBE canonical V2 — Stage 07 and Snakemake integration gate

## Scope and input boundary

This adds a NEW independent workflow entrypoint, `workflow/canonical_v2.smk`,
which **does not** replace `workflow/Snakefile` before a real server dry-run.
The new entrypoint consumes already pinned release-local 03/04 scientific
scanner input files and Stage 01/02 preparation; it independently builds
B1/B2c, the EXACT registers, documentary lookup, handoff, B3, full 06A,
selective DP/06C1, Stage 07, GOA M8, M10c and Temporal V2.

Upstream FASTA/GOA normalizations and 03 species/FULL scans are **inputs** of
this first integration; their creation from raw accession/taxonomy input is
not yet recomputed by the new entrypoint. Complete "from-raw cold start"
should not be claimed until upstream scheduling is integrated and validated.
No old 05A2/05C2/05D/06C1 publication is required. The independent legacy
04D1/04D2 documentary tables remain real inputs, not fictitious output
adapters; the rescue scanner's 05R1 output is a source of candidate discovery,
not a required finalized Stage 05 publication.

## EXACT and NONEXACT

- EXACT candidate discovery is identity-based and policy-agnostic; START and
  END registries contribute metadata to a single query space. A key's origin
  release, RID, literal identity, scope and ambiguity are conserved.
- NONEXACT discovery and documentary qualification are release-local. A
  normalized spelling is a search hint, never standalone documentary proof.
  Independent verified accession/lineage facts are kept. If a target has no
  EXACT, independent documentation still functions normally.
- An inherited EXACT metadata key does NOT assert that the target originated
  from the same biological genomic locus; it does not by itself authorize
  NONEXACT GO transfer.
- NONEXACT DP is permitted only at the same *resolved* TaxID and is never
  cross-subtaxon. EXACT cross-subtaxon GO donor selection is configurable,
  `same_species_exact` by default, `strict` optionally.
- START and END donor decisions, GOA imports and taxonomy stay separate.
  Reuse of END-origin documentary keys in START does not transfer END GO to
  START.

## Stage 07 scientific publication

`publish_canonical_stage07` consumes the SHA256-protected complete 06A
workload, full 06C result, target 01, 02B taxonomy and genuine 04B/04D
source evidence, along with the V2 literal documentary observations.
It constructs *native typed facts* and calls the existing, frozen
`resolve_stage2c_variant` decision kernel. It never fabricates old Stage 2C
manifests or artificial 04D2 rows. Output is one atomic Stage 07 bundle with
`manifest.json`, `07_accepted_associations.tsv` (13-column Stage 08 contract),
`07_selection_metadata.json`, full 19-column decision ledger and summary.
Existing M8/GOA consumers validate the Stage 07 manifest and consume only
association rows with `annotation_donor=YES` for GO transfer.

## DAG and execution safety

Provide a strict JSON specification as `--config canonical_v2_spec=...`.
Example: `config/canonical_v2.example.json` contains verified upstream paths
when the historical report supplied them and **explicit non-runnable
`/REPLACE_WITH_...` placeholders** for evidence/GOA inputs still to pin.
It is not ready for production execution without resolving those placeholders
and preflighting their identities and contents.

`mode=prepare`: `rule all` ends at release-complete workload 06A; no DP,
resolver, GAF scan or Temporal V2 is run. `mode=execute`: `rule all` targets
Temporal V2. Snakemake `--dry-run` is always safe, including mode=execute.
No mode implicitly publishes science.

Upstream output paths carry a fingerprint **independent of the donor policy**;
Stage 07/08 paths carry a distinct policy fingerprint. Switching
`strict`/`same_species_exact` therefore reuses EXACT discovery, documentary
lookup, candidate materialization and alignment results. Policy changes
cannot silently overwrite a published bundle. All publishers fail if their
output destination exists. This integration creates no commits, no push and
no modifications to historical fingerprints or publications.

## Mandatory deployment gates

1. Tracked Git clean, exact frozen HEAD and pinned scientific source hashes.
2. Archive member integrity and SHA256, stage all new files in isolation.
3. Real server `pixi run ruff check` with explicit first-party stage behavior.
4. Pixi synthetic tests on staged files, CLI help check.
5. Real server `pixi run snakemake --dry-run`, both modes, using a synthetic
   layout that never opens GOA or runs science; check the actual DAG exists.
6. Install only missing files; Ruff, tests and dry-run again under the real
   repository. No replacement of existing `workflow/Snakefile`.
7. Before full execution, pin real inputs, run a second dry-run on a fresh
   separate root and perform a cold-start synthetic integration with real
   06C/Stage 07/Stage 08 outputs. Server-side acceptance MUST verify this;
   tests plus DAG dry-run alone do not establish full scientific correctness.
