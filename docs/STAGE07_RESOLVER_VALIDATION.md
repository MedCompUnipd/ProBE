# Stage 07 Resolver - Scientific Validation Record

Validation date: 2026-10-09

Status: SCIENTIFIC DECISION CORE VALIDATED.
Production publisher and workflow integration NOT YET COMPLETE.

## 1. Scope

This record documents validation of the Stage 07 resolver
decision kernel and its evidence assembly against the frozen
ProBE pre-resolver v2 canonical run.

START release: 2023_01
END release: 2026_03

Canonical run:
`/data/PROGENY/ProBE-runs/pre-resolver-v2-2023_01-2026_03`

All upstream stages 01-06C1 and their frozen reference
artifacts remain unchanged.

Stage 07 preserves every candidate pair and does not select
a single best UniProt record.

UNRESOLVED means insufficient positive evidence for
association; it does not establish biological nonidentity.

## 2. Strict scientific regression

Frozen reference:
`/data/PROGENY/tmp/resolver_final_decision_dry_run.tsv.gz`

Regression implementation:
`src/probe/resolver_regression.py`

Successful diagnostic:
`/data/PROGENY/tmp/stage07_strict_regression_v3/`

Results:

| Metric | Value |
|---|---:|
| Candidate pairs | 984079 |
| ACCEPTED | 298498 |
| UNRESOLVED | 685556 |
| AUDIT_ONLY | 25 |
| Reference fields compared | 17 |
| Pairs with differences | 0 |
| Differing fields | 0 |
| Status | PASS |

Comparison is keyed by (release, target_id, rid).
Warning tokens are compared as sets because the resolver
uses canonical lexical ordering.

This proves equivalence to the frozen strict dry-run oracle
for its 17 fields, not independent biological truth.

## 3. Automated tests

Stage 07 focused suite: 208 passed.

Complete repository suite:
531 passed, 1 skipped.

The skipped test requires an optional local TESTING
Python oracle for UniProt release FASTA validation.

Ruff check and format checks passed.

## 4. Parametric policy validation

Policy defaults:

- subtaxon_policy: strict
- partial_sequence: allow
- documentary_fragment: allow

All eight combinations were evaluated against the same
984079 real candidate pairs.

Diagnostic reports:
`/data/PROGENY/tmp/stage07_policy_audit_summary.tsv`
`/data/PROGENY/tmp/stage07_optional_audit_details.tsv`

Under strict subtaxon policy:
298498 ACCEPTED, 685556 UNRESOLVED, 25 AUDIT_ONLY.

With include_audited_same_species:
298522 ACCEPTED, 685556 UNRESOLVED, 1 AUDIT_ONLY.

Exactly 24 independently verified same-species,
wrong-subtaxon audit candidates become ACCEPTED:
21 direct PRIMARY and 3 direct SECONDARY accessions.

The single CROSS_SPECIES candidate remains AUDIT_ONLY.

Original source scope remains TAXONOMY_AUDIT for
promoted audit-origin candidates.
Evaluation scope records the effective policy gate.

The 24 newly accepted candidates affect the accepted
multiplicity count on 28 rows.

## 5. Annotation donor policies

Under strict subtaxon policy:

| Partial | Fragment | Donors |
|---|---|---:|
| allow | allow | 298498 |
| allow | report_only | 294361 |
| report_only | allow | 290586 |
| report_only | report_only | 288374 |

Fragment report_only removes donor eligibility from 4137
otherwise eligible rows.

Partial report_only removes donor eligibility from 7912
otherwise eligible rows.

Both report_only switches together affect 10124 rows,
with an overlap of 1925 rows.

These switches do not change ACCEPTED, UNRESOLVED,
AUDIT_ONLY, decision basis, or candidate coverage.

Association and annotation donation are distinct.

## 6. Locus identifier specificity

ORDERED_LOCUS and ORF fields are not intrinsically
standardized or globally unique identifiers.

Stage 04D1 bucket-aware collision information is necessary
but not sufficient to establish biological specificity.

The canonical dataset contains the UniProt record
A0A0R4IBL7 (HTF4_DANRE), whose original Swiss-Prot GN
field includes ORFNames=SO:0001217 in both releases.

The enriched FASTA preserves this value as
GENE=N:tcf12;O:SO%3A0001217.

The SO term is ontology-level information, not a
specific locus identifier.

Stage 07 recognizes scalar SO:[0-9]{7} values after
decoding their percent-encoded representation for
specificity assessment only. Literal stored keys are
retained for documentary joins.

This recognizer is intentionally scoped and is not
an exhaustive catalog of all possible generic UniProt
ORF or locus placeholders.

Nonidentifying or colliding keys do not invalidate
other independent documentary evidence.

## 7. Output and provenance

The decision assembly exposes a 19-column row schema,
including evaluation_scope and audit_outcome.

For AUDIT_ONLY rows,
accepted_candidates_for_target_release is serialized
as "." rather than numeric zero.

The strict historical oracle covers 17 columns.
The two new columns were checked through tests and
policy invariants, not an independent 19-column oracle.

No empirical identity, mismatch, gap, length-ratio,
or locus-fanout cutoff is introduced.

Similarity-only fragment discovery remains excluded
upstream; documentary fragments remain eligible for
evaluation under the applicable rules.

## 8. Validation limits and remaining work

Optional policy testing validates differential behavior
and invariants; no independent oracle exists for all
eight configurations.

The SO recognizer does not solve every possible ambiguity
in the biological specificity of locus identifiers.

Production acceptance still requires:

- validated configuration integration and defaults;
- Stage 07 publisher with atomic output publication;
- output manifest and deterministic summary JSON;
- DAG and stage-registry integration;
- production dry-run and full-run verification;
- repeatability and artifact integrity checks.

The pre-resolver workflow registry remains authoritative
for the currently integrated stages 01-06C1.

No upstream frozen stage is modified by this validation.
No Stage 07 production integration is claimed here.
