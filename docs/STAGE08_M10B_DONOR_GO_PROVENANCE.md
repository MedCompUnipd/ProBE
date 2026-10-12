# Stage 08 / M10b — donor-qualified GO inheritance and polarity

Status: **additive library and synthetic-test increment**. Nothing is installed in
Snakemake, and no interpretation bundle or benchmark truth is published.

## Scope

`probe.stage08_go_supports.propagate_projected_assertions()` accepts the
validated M1 `ProjectedAssertion` stream and the independently audited M10a
`GoClosureIndex`. This pure, lazy adapter produces one `GoPropagationSupport`
per `(source assertion, authorized Stage 07 donor association, implied GO)`.
This library path is for correctness evaluation and small observations; the
large-scale adapter reading the **published M8 source/link TSV files** is still
to be developed. No rereading of original GAFs is required or proposed.

Each support retains its release, target ID, RID, candidate accession,
source assertion ID, pinned normalized-GAF SHA256, physical line number,
source database, source/direct GO ID, propagated GO ID, GO aspect, literal
source evidence code, GAF relation and original literal qualifier (including
`NOT`), polarity, and `DIRECT`/`INHERITED` origin. The complete original 17 GAF
fields remain in M8 and can be joined by `(release, assertion_id)`; duplicating
all fields on every expanded support would cause unnecessary amplification.

The original evidence code is never replaced by a synthetic "inherited"
evidence code. Neither the GAF relation nor the annotation extension is
interpreted to create extra GO edges. For the pinned DAG, positive assertions
are inherited towards `is_a`/`part_of` ancestors and `NOT` towards descendants.
GO terms must be present and have an aspect consistent with the retained GAF.
No unknown GO or aspect is silently dropped.

The helper `summarize_sorted_term_supports()` reports whether a given
`(release, target, aspect, GO)` has positive and/or negative support. It does
**not** veto or prioritize one polarity. Positive/NOT concurrence is reported
as a descriptive conflict, not converted into EVAL, SKIP, or FP. For production
usage the full expanded stream must be **externally sorted** by `term_key`
before using this reducer. A mislabeled or unordered stream fails closed.
The original detail stream must be retained separately.

## Explicit non-goals

- No interpretation of code strength, no activation of registry profiles.
- No automatic drop of `NAS`, `IEA`, new evidence codes, or NOT statements.
- No selection of scoring target universe, no `EVAL`, `SKIP`, FP or FN.
- No validation of 17-column M8 publication against manifest in this adapter;
  that is a prerequisite for the future M10c production reader.
- No attempt to load or materialize millions of M8 records in memory.
- No changes to Stage 01–08, Stage 07 donor decisions, canonical M8 bundle,
  the ontology parser, or M10a graph semantics.

## Acceptance

Synthetic tests cover multi-donor and many-target provenance, source digest
retention, evidence-code preservation (including new codes and `NAS`),
positive and negative closure direction, direct/inherited distinction,
cross-release separation, explicit positive/NOT coexistence, strict source
aspect checks, unauthorized donor rejection, duplicate donor rejection,
lazy evaluation, and sorted-group validation. A separate integration test exercises the actual
M1 `SourceAssertion`, `DonorAssociation` and `project_annotations()` data model.
Production read/serialization
and materialized full-dataset interpretation are future work.
