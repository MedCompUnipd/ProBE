# ProBE canonical V2: Block B1 candidate provenance contract

Origin: 2026-10-16 frozen source archive, HEAD expected `6d6e03b`.

This is the first isolated component of Block B, NOT a complete replacement
for Stage 05A2. No Snakemake rule, existing scientific source or publication is
modified. No discovery, GO collection, DP or benchmark is run.

## Inputs per release

- `discovery_union`: validated `03_unified_discovery.tsv` produced by the
  existing Stage 2C GO-positive/FULL exact union, **not** historical 05A2.
- `go_positive_pairs`: species-wide GO-positive scanner TSV for the release,
  including real scanner metrics and GO-positive FASTA record ordinal.
- `full_exact_pairs`: FULL exact scanner TSV for that release. The FULL record
  ordinal is distinct from the GO-positive ordinal; do not invent k-mer metrics.
- `lineage_pairs`: independently produced Stage 04E collapsed documentary
  evidence. Full long-form 04D2 remains an upstream provenance authority.
- `rescue_pairs`: independently produced Stage 05R1 FULL rescue scanner TSV.
  The rescue trigger and its completeness proof will be addressed in Block B2.
- `release`: explicit START or END release; invoke once for each release.

Empty channels MUST have header-only TSVs: a missing source is a failure.

## Output

The new directory contains `candidate_provenance.tsv` and
`candidate_provenance.summary.json` with source SHA256 and output SHA256.
One row per `(release, target_id, RID)` retains independently named raw
source JSON for GO-positive, FULL exact, lineage and rescue. Cross-channel
identity is checked, overlaps are conserved, duplicate keys fail closed.

No old Stage 05A2/05C2/05D/06C1 publication is ever opened by this module.
The loader imports the *existing* Stage 2C discovery gate validator to
avoid duplicating its scientific definition. These recorded gates are
**discovery diagnostics**, NOT donor permissions. Same-TaxID EXACT admission
remains the immutable resolver V1 responsibility.

A lineage-only candidate with incomplete taxonomy remains represented.
FRAG=1 is allowed only through independent documentary provenance, never
through sequence-only discovery. This module NEVER promotes taxonomy audit
candidates into the primary universe. Audit reconstruction is a separate
Block B step.

## Not yet implemented; must be completed before canonical activation

1. Produce FULL rescue autonomously with a conservative trigger and prove
   that historically recoverable target/GO paths are retained.
2. Anchor Stage 04E collapsed records to the authoritative Stage 04D2
   long-form manifest; validate documentary/direct-anchor completeness.
3. Produce the final Stage 05A2-compatible primary projection and a
   separately validated 05C2 audit; reconcile metadata and sequence stores.
4. Connect only the resulting validated inputs to 06A/06C1, the frozen EXACT
   donor v1 resolver, Stage 08 and the final single canonical Snakemake DAG.
5. Execute a full cold-start synthetic test and separate reuse-only adoption
   test, then full regression before any canonical cut-over.

## Safety

Module output directory must not exist. Inputs are read-only. It publishes
an output only after building and checking a temporary SQLite-backed ledger.
Do not run this module against scientific publications without verifying the
upstream manifest chain and the rescue completeness conditions.
