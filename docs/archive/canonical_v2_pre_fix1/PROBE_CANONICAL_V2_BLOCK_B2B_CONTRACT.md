# ProBE canonical V2 - Block B2b documentary independence

## Scope

This new module attests the lossless **04D2 -> 04E -> B1 provenance ledger**
chain for a single release. It is deliberately downstream of documentary
one-hop discovery (04D2) and upstream of candidate projection / alignment.
It does not select donor proteins, classify NONEXACT sequence alignments,
run DP, or alter EXACT donor v1. The original long-form 04D2 remains the
source of documentary truth. No legacy Stage 05A2, 05C2, 05D, 06C1 or
published Stage 2C product is needed.

## Critical independent-path invariant

Every target may possess **both** EXACT sequence candidates and independently
found documentary candidates. The latter are never removed because an EXACT
candidate already exists. The B1 ledger persists every `(release,target_id,RID)`
from 04E as a distinct row or an independently identifiable provenance field
of an overlapping row; FULL exact and GO-positive channels are independent.

At this layer, a lineage-only RID is **not** asserted to be NONEXACT: that
requires sequence/geometry verification downstream. If it is NONEXACT,
only an admissible same-resolved-TaxID documentary relation may reach DP,
and only when DP is scientifically needed. Cross-TaxID NONEXACT pairs must
never reach DP. EXACT pairs never require DP or locus; this module neither
modifies nor re-evaluates these scientific rules.

FULL rescue uncovered-target selection is **not a documentary candidate
filter**: the Stage 04D2/04E channel is processed for every target regardless
of GO-positive/FULL-exact coverage or B2a's reference rescue selection.
The historical rescue trigger remains a separate, conditional optimization,
subject to B2a's future real-manifest attestation.

## Verification

`attest_lineage_conservation` reads three already generated tabular inputs:

- authoritative `04D2_lineage_evidence.tsv` (long-form keys and facts),
- corresponding `04E_lineage_candidates.tsv` (collapsed keys and facts),
- `candidate_provenance.tsv` from the new B1 assembler.

It reconstructs the published 04E fields from 04D2 in a temporary SQLite
scratch index, checks every pair/field, then compares every 04E row byte-logically
with the `lineage_source_json` retained in B1. Both directions of set inclusion
are checked, including empty input channels and overlapping EXACT+lineage.

Successful validation returns a JSON-compatible summary of counts and SHA256
for the three *inputs*. This is a diagnostic attestation, not an adoption
receipt or a new scientific publication. No source or repository file is
changed, and scratch files are removed on success or failure.

## Tests and operational gate

Synthetic tests produce **only synthetic** 04E data with the pre-existing
04E collapser; no frozen data are reconstructed. The tests cover EXACT with
coexisting documentary-only candidates, provenance aggregation, `FRAG=1`,
FULL exact cross-subtaxon as an independent channel, failures for missing or
corrupted keys and metadata, duplicate 04E rows, and incorrect ledger facts.

No real 04D2 payload is read by the installer. A future real-data invocation
must be release- and manifest-pinned; it may need significant scratch disk
space and should be planned separately. Existing Stage 2C outputs and the
historical fingerprint remain unchanged. All new files must pass Ruff without
`--fix`, under Pixi, before installation.

## After B2b

1. Prove B2a reference coverage against frozen 05B headers/counts and scanner
   manifests **without running scientific scanners**.
2. Produce the autonomous primary projection from B1 while preserving all
   documentary fields, including target accession and typed lineage keys.
3. Build a separate taxonomy-audit channel, without promoting audit candidates.
4. Verify metadata/sequence availability and DP-policy gating on synthetic
   cold-start fixtures before touching the workflow DAG.
