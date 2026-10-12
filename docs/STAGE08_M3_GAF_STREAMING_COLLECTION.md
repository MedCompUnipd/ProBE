# Stage 08 M3 - One-release streaming normalized-GAF collection

**Status:** Additive candidate module, synthetic validation only. Not yet a
production collector, workflow rule or published Stage 08 bundle. Stage 01-07,
M1, M2 and prior production outputs remain unchanged.

## Purpose and boundaries

`probe.stage08_gaf_collection.collect_one_normalized_gaf()` consumes an M2
`GafCollectionInput`, an M1 `DonorIndex` and one **empty unpublished staging
directory**. It scans one full normalized GAF without loading the annotation
corpus into memory, retaining all assertions of eligible Stage 07 donors and
all authorized links to targets in that same release. The caller must provide
one separate staging directory for START and END, and later orchestrate atomic
publication only after both complete and validate.

This module does not fetch or normalize GO, rerun Stages 01-07, inspect FASTA,
apply evidence policies, infer identity, propagate GO, veto NOT, compute direct
GO truth, use an ECO registry, schedule Snakemake, publish a completed manifest
or implement Stage 08 invalidation/restart semantics.

## Outputs in the supplied unpublished staging directory

For one role `start` or `end`:

- `08_<role>_source_assertions.tsv`: one line per selected **physical GAF row**,
  with a stable assertion ID, release ID, **physical line number of the complete
  normalized GAF** and its original 17 fields in their exact values. Literal
  quotation marks are not CSV-quoted: the TSV reproduces the field content.
- `08_<role>_annotation_links.tsv`: one line per accepted donor/target link,
  identified by `(release, target_id, rid, assertion_id)`; one GAF source row
  linked to two targets is written once in sources and twice in links.
- `08_<role>_donor_coverage.tsv`: one row per Stage 07 accepted association of
  this release, including donor NO; positive and explicit NOT row counts remain
  separate. After verified complete EOF it may report `ROWS_RETRIEVED`,
  `NO_RETAINED_GOA_ROWS`, or `DONOR_DISABLED`.
- `08_<role>_evidence_inventory.tsv`: unclassified observed evidence codes,
  partitioned by aspect and polarity, with counts of distinct physical source
  rows and association links. It is *not* an inventory of the entire GOA and
  does not claim observed-code semantics.
- `08_<role>_scan_summary.json`: schema, complete-source SHA256, physical/data
  line counts, retained source/link counts, eligible donor count, missing donor
  association count and `coverage_certified=true` after successful validation.

No evidence code is filtered. NOT is a property of a row; it is never converted
to positive or used to veto another donor.

## Scanning and integrity strategy

The binary reader makes one pass over the GAF and computes SHA256 from the
**actual consumed bytes in that same pass**. It checks the actual file's size,
filesystem identity timestamps before and after, the version 2.x header and
the source SHA256 declared in the Stage 01B1 manifest. Symlinks are rejected.
A missing final newline is permitted, preserving the final line.

The first two fields are examined as raw bytes to skip unneeded accessions
before constructing Python annotation objects. The remaining 17 fields are
parsed only for donor-matching rows, using the existing `AnnotationRecord`
structure and `SourceAssertion` field-consistency checks. A regression test
compares representative retained rows with `GafParser`. This is deliberately
an adapter for the filtered streaming path, not a second normalization policy.

A source-row SHA256 mismatch or read/parser failure aborts collection. Neither
coverage nor scan-summary files is produced in such a failure. Source/link files
may be present in the private staging directory: **they are invalid partial
artifacts and must never be published or read as complete results**. The existing
`atomic_stage_bundle()` is the required later publication mechanism.

The in-memory state scales with the number of Stage 07 association keys and
observed evidence-code/aspect/polarity combinations, **not** the number of GAF
rows or saved source/association links. The output TSV size grows with donor
annotations and link multiplicity. CPU and disk throughput on 304 GB of GOA
have not been profiled; do not launch canonical scans before a bounded benchmark
and a manifest-aware atomic runner with interruption/restart tests.

## Not-yet-implemented safeguards

- A production wrapper must invoke the M2 preflight, establish stage-relevant
  implementation provenance, supply private staging and call the collector for
  both roles under an atomic *common* bundle publication transaction.
- Final input revalidation, output manifest with SHA256, outcome, consistent
  cross-role metrics and failure/restart behavior remain to be implemented.
- The full donor-free target catalog is not an M3 input. As in M1, absent
  accepted associations do not produce invented target coverage rows.
- The current collector checks the exact match of source SHA256, but has no
  snapshot/lock protection against arbitrary concurrent filesystem changes.
- No independently measured full-release memory, runtime or output size is
  claimed from synthetic tests. A bounded sample and review of throughput are
  required before a 300+ GB scan.

## Validation

Synthetic tests include multi-donor and shared-donor attribution, START/END
independence, positive/NOT separation, donor NO, absent donor with successful
EOF, preservation of physical source lines and quotation marks, unfamiliar
codes, parser parity, same-size SHA mutation, malformed rows, corrupted UTF-8,
missing version header, size mismatch and protection of an existing staging
directory. The server validation script will run Ruff, the focused suite and
then the entire repository suite before any production-scale use.
