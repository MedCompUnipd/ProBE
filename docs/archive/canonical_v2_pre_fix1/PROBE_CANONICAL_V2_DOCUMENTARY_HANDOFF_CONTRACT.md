# ProBE canonical V2 — documentary candidate handoff v1

This is an integration component, **not** the final executable DAG.

## Invariants

- Read the two `documentary_nonexact_matches.tsv` lookup bundles and verify
  their schemas, payload hashes, distinct START/END release identities, common
  target SHA, and reciprocal cross-release EXACT registry provenance.
- `EXACT START ∪ EXACT END` is an **anchor-key** union only. Protein candidates
  remain keyed by `(evaluation_release, target_id, candidate_rid)`.
- Preserve every source lookup observation (all original columns and the new
  `potential_unique_locus` flag), including alias origins, collisions, source
  scope and source release; never infer a unique genomic locus for the target.
- A literal and non-colliding typed `ORDERED_LOCUS` or `ORF` key can be tagged
  as *potential* documentary support when both target and candidate key
  multiplicities equal 1. This tag is not a frozen scientific decision; it
  cannot authorize DP, select a GO donor or replace the Stage 07 decision.
- Name/synonym and normalized-dash/underscore matches remain discoverable
  hints, not sufficient support by themselves. No fabricated 04D1/04D2 rows.
- NONEXACT candidates must be in the same resolved TaxID as their target for
  the evaluated release. Input cross-TaxID violations cause failure.
- Include independently documented candidates even if the target has no EXACT
  candidates. Retain candidates already seen by other sources.
- No GOA is read; no DP is performed; no `annotation_donor` decision is made.
- Fragment records may be present only as documentary observations. Their
  eligibility is subject to downstream frozen fragment and DP policies.

## Output

Atomic, immutable directory containing:

- `documentary_candidate_handoff.tsv`: unique per-release pairs and counts;
  GO donor authorized is always false, DP authorized is always false.
- `documentary_evidence_observations.tsv`: complete original lookup rows,
  adding `potential_unique_locus`, without collapsing source provenance.
- `documentary_handoff.summary.json`: input/output hashes and release identity.

## Not yet production-complete

This unit does **not** make `04D1` or `04D2` claims and does not purport to be
an alternative to the validated Stage 07 resolver. The canonical DAG must still
use this evidence to construct a genuine, versioned documentary V2 resolver
entry and must pass real Snakemake cold-start and Stage 08 tests before
production. Scientific frozen artifacts and all legacy source bytes remain
unchanged. No claim of a full canonical V2 release should use this artifact.
