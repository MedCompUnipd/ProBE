# Stage 07 to Stage 08 interface and rerun contract

Status: implementation specification; not yet production integrated.

## 1. Scientific boundary

Stage 07 identifies supported candidate-record associations and determines
annotation-donor eligibility.

Stage 08 retrieves and processes GO annotations from eligible donors.

Stage 07 does not select benchmark targets, impose GO-based eligibility
criteria, or calculate benchmark ground truth.

ACCEPTED and annotation_donor=YES are distinct properties.

UNRESOLVED means insufficient association evidence; it does not establish
biological nonidentity.

## 2. Stage 07 output bundle

The common Stage 07 bundle contains:

- 07_resolved_candidate_associations.tsv
- 07_accepted_associations.tsv
- 07_selection_metadata.json
- 07_summary.json
- stage_outcome.json
- manifest.json

The existing 19-column resolved-associations schema is unchanged.

The summary and stage outcome retain their existing scientific and technical
meanings.

The manifest records checksums, upstream provenance, implementation identity
and effective policies for all outputs.

## 3. Accepted-associations TSV

This is a deterministic projection of the complete decision table.

Rows are included if and only if decision=ACCEPTED.

Proposed columns, in order:

1. release
2. target_id
3. rid
4. target_accession
5. candidate_accession
6. annotation_donor
7. decision_basis
8. sequence_relation
9. exact_sequence
10. scope
11. evaluation_scope
12. audit_outcome
13. accepted_candidates_for_target_release

The triple (release, target_id, rid) is unique.

All fields are copied from the complete decision table without
reclassification or reinterpretation.

The projection preserves deterministic row ordering.

Stage 08 must select GO donors using annotation_donor=YES, not merely
the presence of a record in the accepted-associations table.

ACCEPTED records with annotation_donor=NO remain present for traceability.

A record with scope=TAXONOMY_AUDIT may be ACCEPTED under an explicitly
permitted optional policy; its original scope must remain visible.

## 4. Selection metadata

07_selection_metadata.json is a structured, versioned scientific explanation
of the effective Stage 07 decision and donor policies.

Its content must include:

- schema version and Stage 07 policy version;
- exact START and END release identities;
- the effective values of all three configurable policies;
- the meaning of ACCEPTED, UNRESOLVED and AUDIT_ONLY;
- direct PRIMARY/SECONDARY accession continuity rules;
- nondirect documentary accession and exact-sequence requirements;
- stable typed locus requirements and lineage collision safeguards;
- exact-sequence, exact-subsequence and equal-length-variant treatment;
- taxonomy and same-species subtaxon audit conditions;
- cross-species audit exclusion;
- fragment discovery and documentary-fragment donor rules;
- partial-sequence donor restrictions;
- interpretation of scope, evaluation_scope and audit_outcome;
- the Stage 08 donor-consumption contract;
- references to the authoritative decision rules, summary and manifest.

Descriptions must reflect implemented and tested rules, not introduce new
biological acceptance criteria.

The manifest remains authoritative for file-level SHA256 provenance.
The summary remains authoritative for quantitative statistics.

The metadata is generated deterministically from the effective policies
and the versioned scientific rule descriptions.

## 5. Rerun and recovery semantics

The operational output location remains results/pipeline-v2/common/07.

No separate output tree is required for each policy combination.

A change in a Stage 07 policy invalidates Stage 07 and its actual downstream
dependencies, not unrelated or already-valid upstream stages.

A change in upstream scientific inputs or parameters may invalidate the
affected upstream stage and its actual downstream dependencies.

A valid completed bundle is not silently overwritten by an independent
Stage 07 invocation.

Replacement of an invalidated bundle is permitted through the workflow
invalidation/recovery contract. A complete manifest must not be bypassed
merely to force a replacement.

An interrupted or incomplete bundle must not be treated as completed.

Publication remains atomic at the bundle level. No partially produced
Stage 07 output is exposed as a completed result.

Previous runs may be archived explicitly when historical comparisons are
required; routine recalculation does not require duplicating all upstream data.

## 6. Required acceptance tests

- The original 19-column output remains byte-identical for the canonical
  strict/allow/allow configuration.
- The new accepted TSV is an exact projection of ACCEPTED rows.
- ACCEPTED records excluded from donation are retained in the projection.
- Optional audited same-species associations retain their audit provenance.
- Metadata reflects each effective policy combination.
- Metadata contains no fabricated or unverified scientific assertions.
- All bundle outputs and implementation dependencies are manifest-tracked.
- Atomic publication and recovery after interruption are tested.
- Snakemake dry-runs distinguish policy-only changes from upstream changes.
- Stage 08 consumes donor eligibility without rerunning Stage 07 rules.
