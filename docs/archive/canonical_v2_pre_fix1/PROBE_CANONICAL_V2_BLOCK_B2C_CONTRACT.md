# ProBE canonical V2 — Block B2c primary facts projection

This stage consumes **only** the B1 `candidate_provenance.tsv` and its verified
summary, for one specific release. It does not read any historical Stage 05A2,
05C2, 05D, 06C1 or frozen scientific output. It creates the separate immutable
`primary_candidate_facts.tsv` with a SHA256-linked summary.

The output is a **preparation of primary candidate facts**, not a complete
Stage 05A2 replacement. `ready_for_06a=false` is intentional: FULL metadata,
sequences, 02B target taxonomy revalidation and the independent 05C2 audit
still need their canonical cold-start producers and validation.

- One `(release,target_id,RID)` per line; no filtering by EXACT or GO donor.
- GO-positive, FULL exact, documentary/lineage and FULL rescue provenance
  retained independently in their original JSON payloads.
- A target with a GO-positive EXACT candidate may also have additional
  documentary candidate pairs; none is suppressed by coverage.
- Lineage fragments are retained without qualification as GO donors.
- FASTA ordinals are **not** confused: GO scanner record number and FULL
  record number retain separate columns and semantics. FULL `record_number`
  must not be assumed to equal UniProt RID. If independent FULL ordinals
  disagree, source-specific ordinals remain in separate fields, the collapsed
  ordinal is blank, and downstream materialization must resolve the conflict
  before any alignment job is authorized.
- GO k-mer metrics are retained only when produced by the GO scanner; rescue
  k-mer metrics only when rescue is the available source. FULL-only exact
  evidence asserts only exactness, never invented similarity metrics.
- Lineage-only candidate sequence identity is not inferred. Literal identity
  and DP eligibility remain downstream responsibilities, following EXACT
  donor contract v1 without modification.
- Intrinsic UniProt metadata are compared across source channels; target
  search bucket is source-specific and not treated as a protein identity.
- Stage 04D2 -> 04E preservation is separately attested by B2b. The future
  canonical DAG must require B2b attestation before considering projection
  verified; this stage is not a substitute for the documentary audit.

Mode is **new-source data preparation**, never frozen-run reconstruction. No
scientific publications or existing tracked files are modified by the install.
