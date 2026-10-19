# ProBE canonical V2 — Block B2a: rescue reference coverage

This component adds **no** new scan, dynamic programming, donor decision,
publication rewrite, or Snakemake rule. It does **not** modify B1 or the frozen
EXACT donor v1 resolver. It is **not** the whole Block B2.

## Scientific problem

The old 05B/05R0 selected k-searchable targets uncovered by a `rank_aware`
(or explicitly configured `strict_taxon`) GO-positive sequence search plus
Stage 04E lineage. The new Stage 2C GO-positive `species_wide` search sees
additional cross-subtaxon candidates. Using its raw target coverage directly
would suppress rescue for some previously uncovered targets.

The two C++ candidate scanners in the October 16 source archive differ in
this area only in taxonomic bucket selection and taxonomy-policy plumbing.
Their k-mer admission and geometry implementations are otherwise identical.

`rescue_reference.py` recovers a **reference GO coverage** by filtering the
new species-wide GO-positive pairs through the exact **old 02B target primary
bucket** and candidate OX buckets. It unions that target coverage with Stage
04E documentary lineage coverage. It selects only targets still uncovered and
having `sequence_length >= k` for the existing FULL rescue scanner.

- A species-wide candidate at a different resolved TaxID does not silently
  suppress rescue for a rank-aware below-species target.
- For species-level reference buckets, cross-subtaxon GO-positive hits remain
  reference-eligible as before.
- Documented lineage-only candidates contribute to reference coverage, even
  when FRAG=1 (the eventual donor decision remains independent).
- Eligible 04B direct anchors with **no** 04E lineage for their target fail
  closed, matching the pre-existing Stage 05B invariant.
- FULL exact discovery is deliberately **not** included in the historical
  rescue trigger; it did not exist in the old 05A baseline.
- No old 05A, 05A2, 05B, 05R0, 05D or 06C1 publication is required.

## Inputs

All sources must be present, including header-only channels when empty:

1. `01` target TSV (`target_id`, `sequence_length` and all original fields).
2. `02B` target taxonomy TSV (original `primary_bucket`, resolved/species
   TaxID) and OX taxonomy TSV (`raw_ox`, resolved/species buckets).
3. The **raw GO-positive species-wide candidate pairs** from Stage 2C 03.
4. Stage 04E collapsed documentary lineage TSV for the same release.
5. Stage 04B direct anchor classification for the same release.
6. A content-pinned JSON scan-equivalence contract, with
   `schema = probe-canonical-v2-scan-equivalence-v1` and equal scanning
   parameters `k`, `max_required_shared`, `min_target_kmer_fraction`,
   `fragment_policy=exclude`, plus matching SHA256 identities for the GO
   positive FASTA, targets, target taxonomy and OX taxonomy.

The contract requires `species_wide.taxonomy_policy=species_wide` and
`reference.taxonomy_policy=rank_aware` or `strict_taxon`.

The planner independently hashes the three TSV inputs represented by their
pinned SHA256 values. It does **not** independently rehash the GO FASTA:
matching FASTA identities and scanner implementation equivalence must be
attested from actual upstream manifests by the future DAG. This component
thus reports `CONDITIONAL_MANIFEST_ATTESTATION_REQUIRED`, not an unconditional
scientific equivalence claim. Do not invoke it on unverified real publications.

## Outputs

A new, never-overwritten directory containing:

- `rescue_reference_coverage.tsv`: per-target GO species-wide coverage,
  GO reference coverage, documentary coverage, direct anchor counts and
  selection decision;
- `rescue_target_ids.txt`;
- `rescue_targets.tsv` and `rescue_target_taxonomy.tsv`, subsets with the
  **original** complete headers compatible with the FULL rescue scanner;
- `rescue_reference.summary.json`, with input/output SHA256 values and counts.

The planner is read-only on all inputs and fails before publication on
inconsistent release, taxonomy, duplicate records, direct-anchor invariant,
missing input, policy drift or conflicting checksum.

## Remaining Block B work

- Attest the actual GO FASTA, scanner parameters and C++ implementation
  identity from canonical Stage 01C/02B/03 manifests.
- Prove the coverage projection against pre-existing frozen 05B results
  *without running either primary scanner or the FULL rescue scanner*.
- Connect the selected targets to a future cold-start 05R1 runner.
- Validate Stage 04E summaries against authoritative long-form 04D2.
- Build the final lossless primary projection, taxonomy audit and metadata/
  sequence inputs before invoking the frozen EXACT donor v1 resolver.

**No DAG edits, no historical publication writes, no Git commit/push.**
