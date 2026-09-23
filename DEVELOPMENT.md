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
4. **Release pairs are generic.** Acquisition uses the roles `start` and `end`;
   release identifiers and dates remain explicit provenance rather than API
   constants.
5. **One ontology is pinned.** Both annotation releases are interpreted through
   the same immutable GO snapshot. Its date is an independent benchmark input.
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
- Symmetric normalization of both GOA releases through one ontology vocabulary.
- Auditable exclusion of unknown, obsolete, deprecated, and NOT-constrained terms.
- Detection of acquired terms, changed evidence, upgrades, and removals.
- Unit tests and small synthetic end-to-end fixtures.

### Explicitly deferred

- A general analysis CLI; the dedicated acquisition utility is intentionally
  limited to freezing configured upstream inputs.
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
| M4 | GO OWL loader and ontology semantics | active | current branch | `go.owl` is the master ontology; Milestone 2A context, exact-IRI edge policy, deterministic preflight, and synthetic tests are implemented; final report review remains required |
| M4P | Release preprocessing and synchronization | complete | current branch | Flag-based CLI canonicalizes GO IDs, retains NOT provenance, removes every root row, restricts GOA to FASTA ACCIDs, writes an ACCID-only missing log, and filters FASTA to proteins with valid non-root annotations; a disposable SQLite index bounds memory for the documented 109 GB FASTA and 178 GB GOA inputs |
| M4T | Target ID mapping and exact sequence matching | complete | current branch | Deterministic T+9 IDs, heterogeneous-header mapping, policy-versioned symmetric normalization, hash-indexed exact matching, and UniProt ACCID extraction implemented |
| M4A | Reproducible upstream acquisition | complete | current branch | Explicit TOML-selected `start`/`end` UniProt, GOA, taxonomy, and optional ontology inputs; streaming atomic downloads, safe extraction, disk preflight, checksums, manifests, validation, and repair behavior implemented without biological parsing |
| M5 | Snapshot validation and comparison events | complete | current branch | Canonical direct assertions, cumulative evidence profiles, prior-knowledge states, direct-event precedence, and exact t0/t1 release orchestration implemented |
| M5T | Truth and evaluation masks | complete | current branch | Explicit-policy construction of aspect-specific Q, D_score, provenance-bearing G_candidate, all-usable K0, D_neutral1, X1, M, and final T; direct candidates remain intermediate only |
| M6 | End-to-end historical CAFA/GOA fixture | planned | unassigned | Pin small redistributable fixture and checksums |
| M7 | Full-release profiling and storage decision | planned | unassigned | Choose backend only from measured constraints |
| M8 | Ground-truth export schema | planned | unassigned | Must preserve direct versus propagated terms |
| M9 | Prediction evaluation and CAFA metrics | planned | unassigned | Separate package layer over comparison results |

## Open decisions

### Relations included in propagation

Ontology navigation retains `is_a`, `part_of`, `regulates`,
`positively_regulates`, and `negatively_regulates`. Annotation inheritance and
NOT constraints conservatively use only `is_a` and `part_of`, because traversing
causal relations changes the gene-product-to-term relation. Confirm the scoring
closure against the intended evaluation protocol before publishing a benchmark.

The Milestone 2A implementation now inventories every observed property and
assigns a disposition per extracted edge. `is_a` and same-aspect `part_of` are
the only propagating cases. Subproperties do not inherit propagation permission.
The current `go.owl` report is recorded in
`docs/ONTOLOGY_PREFLIGHT_PROVISIONAL.md`. `go-plus.owl` is not used for node
navigation or scoring. Final acceptance still requires review of the pinned
`go.owl` checksum and report.

Truth/mask construction exposes two root-eligibility policies. The conservative
`is_a_rooted` profile keeps part-of-only paths quarantined; `safe_root_paths`
admits them only when selected explicitly. No headline default is inferred from
predictor performance.

### Evidence policy presets

The compatibility `experimental()` preset accepts traditional and
high-throughput experimental codes. Explicit cumulative profiles now separate
`experimental_strict`, `experimental_all`, phylogenetic, traceable, and broader
non-electronic evidence. Milestone tests use `experimental_strict`; the profile
used by the definitive benchmark still requires biological approval.

K0 has the separate named `all_usable_positive_t0` policy, including IEA and
excluding NAS/ND/NOT-derived states. Neutral t1 truth retains distinct primary
non-electronic and all-usable sensitivity profiles. Event selection likewise
keeps new-knowledge, refinement, combined, and evidence-confirmation separate;
the last can unmask only the upgraded direct term while leaving known ancestors
masked.

## Testing strategy

Every scientific rule needs a small fixture demonstrating both the accepted and
rejected case.

- FASTA: normalization, uncommon residues, malformed records, duplicate IDs.
- GAF: version headers, all 17 fields, negation, malformed rows, compression.
- Identity: duplicate sequences, multiple accessions, unmatched targets.
- Ontology: alternate IDs, transitive replacement of deprecated stubs, checksum
  and fingerprint validation, complete-graph retention, exact-IRI edge
  dispositions, root reachability, projection-specific cycles, information
  content, and SimGIC.
- Snapshot: unknown terms and aspect/namespace disagreement.
- Comparison: canonical aggregation independent of assertion context, evidence
  threshold upgrades, prior-knowledge states, redundant ancestors, specificity
  refinements, branch acquisitions, shared-ontology enforcement, and NOT
  constraints.
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
- Split `ontology.py` only when graph size or substantially more complex ontology
  semantics requires it.
