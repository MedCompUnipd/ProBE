# ProBE development guide

This document is the shared plan and coordination ledger for contributors and
coding agents. Update it in the same change that alters scope, ownership, or a
milestone's status.

## Objective

Build a reproducible, notebook-first Python API that identifies CAFA targets by
exact sequence, associates their historical UniProt identifiers, and detects GO
term acquisition or evidence upgrades between two annotation releases.

Correctness and traceability take priority over convenience features. The
implementation should remain small enough that a researcher can follow an
annotation from input line to comparison event.

## Stable design decisions

These decisions should not be changed incidentally. Record a proposal under
"Open decisions" before changing one.

1. **API first.** Notebooks are the first client. A future CLI must be a thin
   adapter over the same public functions.
2. **Exact identity first.** Normalized protein sequences are keyed by SHA-256
   and length. Similarity search is a separate future feature.
3. **Identifiers are aliases.** CAFA and UniProt identifiers point to an
   internal sequence identity and may be many-to-many at the input boundary.
4. **Release pairs are generic.** Core code uses `old` and `new`; it contains no
   CAFA4/CAFA5-specific branches.
5. **Both ontologies are retained.** New annotations are interpreted with the
   new ontology and projected into the old vocabulary for leakage-aware
   comparison.
6. **Evidence is a policy.** Evidence types form named categories and explicit
   accepted transitions, not a universal confidence score.
7. **Parsing preserves information.** Qualifiers, negation, references,
   assignment source, dates, extensions, and product forms are retained.
8. **No implicit I/O.** Reading and analysis return values. They do not create
   run directories or overwrite files.
9. **Structured validation.** Recoverable data problems are inspectable values;
   strict mode may promote errors to exceptions.
10. **Scale without DataFrame coupling.** Parsers stream. A disk-backed storage
    layer will be introduced behind the public API when full-release profiling
    demonstrates the need.

## Scope boundaries

### In the first usable release

- Plain and gzip FASTA ingestion.
- GAF 2.x ingestion with complete record provenance.
- GO OWL loading for terms, `is_a`, `part_of`, alternate IDs, and obsolescence.
- Exact sequence indexing and CAFA/UniProt alias matching.
- Release-bound annotation snapshots and cross-file validation.
- Projection of new GO terms into the old ontology vocabulary.
- Detection of acquired terms, changed evidence, upgrades, and removals.
- Unit tests and small synthetic end-to-end fixtures.

### Explicitly deferred

- CLI and configuration files.
- Downloading or locating remote releases.
- DIAMOND/MMseqs similarity matching.
- GPAD/GPI and native ECO ingestion.
- Prediction ingestion and CAFA metric calculation.
- Persistent caches, Parquet, DuckDB, or SQLite storage.
- Plotting, reports, and notebook widgets.
- Generic workflow engines or plugin registries.

## Work coordination

Before starting work:

1. Pull or inspect the latest branch and run `pixi run test`.
2. Claim one unowned work item in the table below by adding a short owner name.
3. Note any files likely to overlap another active item.
4. Keep each change focused on one scientific or infrastructure concern.

Before handing work off:

1. Run `pixi run check`.
2. Add or update tests for changed behavior.
3. Update the item status and notes below.
4. Record unresolved scientific questions under "Open decisions".
5. Do not mark an item complete when only its interface or stub exists.

Status values are `planned`, `active`, `blocked`, and `complete`.

## Progress ledger

| ID | Work item | Status | Owner | Notes |
|---|---|---|---|---|
| M0 | Package, Pixi environment, test and lint setup | complete | initial scaffold | Lock resolves declared platforms; verified on macOS arm64 |
| M1 | Source handling and structured validation | complete | initial scaffold | Plain paths, gzip, checksums, structured issues |
| M2 | Streaming FASTA and GAF readers | complete | initial scaffold | Synthetic coverage complete; historical fixtures belong to M6 |
| M3 | Exact sequence index and alias matching | complete | initial scaffold | In-memory exact path complete; full-release profiling belongs to M7 |
| M4 | GO OWL loader and ontology projection | complete | initial scaffold | Initial `is_a`/`part_of`, alternate-ID, projection, and cycle semantics |
| M5 | Snapshot validation and comparison events | complete | initial scaffold | Positive assertions complete; negation remains an explicit open decision |
| M6 | End-to-end historical CAFA/GOA fixture | planned | unassigned | Pin small redistributable fixture and checksums |
| M7 | Full-release profiling and storage decision | planned | unassigned | Choose backend only from measured constraints |
| M8 | Ground-truth export schema | planned | unassigned | Must preserve direct versus propagated terms |
| M9 | Prediction evaluation and CAFA metrics | planned | unassigned | Separate package layer over comparison results |

## Open decisions

### Negated annotations

The parser retains `NOT` annotations, but the first comparison implementation
does not treat a new negated assertion as a simple deletion. We need documented
semantics for interactions between positive and negated assertions before such
events select or exclude proteins.

### Relations included in propagation

The first implementation recognizes `is_a` and `part_of`. Before producing a
published benchmark, confirm the exact relation set against the intended CAFA
evaluation protocol and pin it in the run metadata.

### Ontology projection of new terms

The current policy should project a term absent from the old ontology to the
nearest old ancestor or ancestors. We need to decide whether multiple nearest
ancestors are all admissible and how this should be reported in ground truth.

### Evidence policy presets

The experimental preset accepts GO experimental and high-throughput
experimental codes. Additional presets need biological review before becoming
part of the public API.

## Testing strategy

Every scientific rule needs a small fixture demonstrating both the accepted and
rejected case.

- FASTA: normalization, uncommon residues, malformed records, duplicate IDs.
- GAF: version headers, all 17 fields, negation, malformed rows, compression.
- Identity: duplicate sequences, multiple accessions, unmatched targets.
- Ontology: alternate IDs, obsolete terms, `is_a`, `part_of`, cycles, projection.
- Snapshot: unknown terms and aspect/namespace disagreement.
- Comparison: acquisition, evidence upgrade, evidence-only change, removal, and
  projection.
- Integration: a tiny two-release dataset with a hand-computed expected result.

Tests must not depend on network access or mutable external releases.

## Definition of done

A work item is complete when:

- Its public behavior is documented.
- Tests cover normal operation and important failures.
- Validation failures identify their source and, where applicable, line number.
- `pixi run check` passes.
- No new scientific policy is hidden in parser or storage code.
- The progress ledger and open decisions are current.

## Future module triggers

Add modules because of a demonstrated need:

- Add `storage.py` after profiling shows that streaming plus targeted filtering
  is insufficient for full releases.
- Add `parsing/gpad.py` and `parsing/gpi.py` when GPAD semantics are required.
- Add `eco.py` when GO evidence abbreviations cannot express the chosen policy.
- Add `similarity.py` when approximate rather than exact matching is specified.
- Add `predictions.py` and `metrics.py` only after ground-truth events are stable.
- Add `cli.py` only after at least one complete notebook workflow is validated.
- Split `ontology.py` only when a second ontology or a substantially more complex
  release-mapping strategy requires it.
