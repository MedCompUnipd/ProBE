# Stage 08 M10c1: streaming adapter for published M8 TSVs

This **library-only** addition reconstructs `ProjectedAssertion` from the M8
`08_<role>_source_assertions.tsv` and `08_<role>_annotation_links.tsv` tables.
It never opens a GAF and never changes Stage 01-08, their outputs, or the
already validated M10a/M10b science.

The M8 collector writes source rows in original physical GAF order and link
rows contiguously for each source assertion. The adapter performs a streaming
merge in that order and keeps only one source's donor links in memory. It
checks the literal 17 fields, source release, source SHA256 identity, original
physical line, reconstructed assertion ID, donor authorization and duplicates.
At successful iterator exhaustion it checks the *complete derived TSV bytes*
against the expected SHA256 values, input stability, and the source/link row
counts supplied from the validated M8 manifest/collection summary. The caller
must supply these trusted metadata values; the adapter does not validate the
entire published M8 manifest itself. A partially consumed generator is **never
certified**, and these routines provide no published certification marker.

M10b's `propagate_projected_assertions` can consume this iterator without
materializing it. The resulting propagated support stream may be many times
larger than the original rows, especially for NOT descendancy. This increment
intentionally does **not** materialize, sort, summarize or publish such supports.
A later M10c increment must first establish bounded external sorting and a
safe, atomic, policy-fingerprinted output plan. It must explicitly define
whether all support rows or compact indexed provenance should be persisted.

M10c1 does not interpret evidence-code profiles, assign EVAL/SKIP, perform NOT
veto, run any full Stage 08 interpretation, or calculate benchmark metrics.
