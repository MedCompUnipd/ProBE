# Stage 08 M1 - Donor boundary and annotation projection

## Status and scope

First additive library increment, prepared on 10 October 2026. It is not the
complete Stage 08 production runner. The source snapshot reviewed for integration
was branch `chatgpt/pre-resolver-v2`, commit `5e982a7`.

This increment implements the reader of the complete Stage 07 accepted projection,
a release-qualified donor index, projection of already-parsed annotation records,
and a reference summary with a dynamic evidence-code inventory.

It does not change Stage 01-07, existing Python modules, `workflow/Snakefile`,
configuration files, dependencies, manifests, or canonical run bundles. It does
not run GOA normalization or read candidate FASTA files. It does not implement
new evidence categories, evidence selection, propagation, NOT vetoes, comparison,
truth/masks, or final benchmark eligibility.

Governing project documents:

- `docs/STAGE07_STAGE08_INTERFACE_CONTRACT.md`;
- `docs/STAGE08_AUDIT_E_CONTRATTO_PROPOSTO.md`;
- `docs/STAGE08_EVIDENCE_POLICY_GENERALIZZATA.md`, revision 1.1.

The last document's rerun/invalidation matrix remains a required later workflow
acceptance test. This library-only increment does not implement that matrix yet.

## Files

- `src/probe/stage08_donors.py`
- `src/probe/stage08_annotations.py`
- `tests/test_stage08_donors.py`
- `tests/test_stage08_annotations.py`

No existing file needs to be modified for imports: use these submodules directly.
Do not change `src/probe/__init__.py` merely to expose these provisional APIs.

## Donor boundary

`read_accepted_associations()` accepts a path to the complete 13-column Stage 07
accepted TSV plus explicit START/END release IDs and source database. Those values
must eventually come from validated upstream metadata, not guessed path names.
For the present Stage 07 UniProt contract the source database is `UniProtKB`.

`DonorAssociation` preserves all thirteen raw string fields, including numeric
spellings and documentary/audit metadata. The loader checks the exact header,
row width, required identifiers, flags, release identities, uniqueness of
`(release, target_id, rid)`, consistency of the accession for a release/rid, and
observed versus declared ACCEPTED group multiplicity. The multiplicity includes
YES and NO rows. It does not re-evaluate resolver acceptance or sequence geometry.

`DonorIndex` keeps every accepted association but exposes only YES associations
through `donors_for(release=..., database=..., accession=...)`. Lookup is literal:
no alias expansion, case conversion, suffix removal or sp/tr namespace guessing.
Unknown release IDs are errors. A recognized release with no matching donor
returns an empty tuple. One accession can authorize links to multiple targets;
the two releases have independent authorizations.

## Source and assertion provenance

`NormalizedGafSource(release, path, sha256)` identifies an upstream *normalized*
GAF. Its digest is a declared value that must be checked by a future manifest-aware
runner. Constructing this object does not open the source or independently verify
its SHA256. There is no new trusted manifest format or alternate checksum policy.

`SourceAssertion` wraps the existing `probe.records.AnnotationRecord`, the raw
seventeen GAF fields, the source identity, and the original physical line number.
The adapter checks that the structured record matches those raw fields and that
its source/line agree with the explicit provenance. This is not a new file parser.

The source line counts all physical lines in the complete normalized GAF,
including headers and blank lines. A prefilter must retain that number. Parsing a
subset and presenting its new line number as an original line is not permitted.
The adapter cannot establish the truth of supplied provenance on its own; the
collection layer must produce and validate it.

The stable assertion ID hashes a versioned tuple of release, normalized-source
SHA256 and original line number. Local mount paths do not affect that ID. Two
identical rows at distinct source lines are distinct physical supports. Reordering
a source file changes its source identity/line positions; byte-level provenance
IDs are not expected to stay unchanged after such a reordering.

## Projection

`project_annotations(assertions, donors=...)` is a lazy, single-pass iterator.
Each emitted `ProjectedAssertion` contains the source assertion once and all
its authorized target-record associations. `links()` expands those associations
into `(release, target_id, rid, assertion_id)` links.

The input must remain in increasing source-line order within each release;
START/END may be interleaved. Missing line numbers are allowed for prefiltered
streams. Duplicate/decreasing lines and mixed sources for a release are rejected.
Unknown releases fail; rows from unmatched donors/databases do not link.

This iterator does not open, scan or certify a complete GAF file. A future bulk
reader must prefilter on the exact release/database/accession lookup before
constructing costly records for irrelevant corpus rows, preserve raw fields and
line numbers, and establish completion after successful EOF/validation. Existing
parser behavior should be reused deliberately rather than duplicated silently.

## Reference summaries and dynamic evidence inventory

`summarize_projection()` is an optional reference reducer for synthetic tests and
small extracted subsets. It validates the complete donor set for each supplied
projection and rejects duplicate source assertion IDs. It produces:

- direct groups by `(release, target_id, aspect, GO, negated)`, with the complete
  evidence-code set and source-link IDs;
- observed positive/NOT counts for every accepted association, including donor NO;
- a dynamic inventory per release and evidence code, with source-row counts,
  association-link counts, distinct donors, distinct targets and observed aspects.

The inventory scope is `retrieved_donor_assertions`, not the complete GOA corpus.
A source row shared by two targets counts once as a source row and twice as an
association link. Literal nonblank codes such as synthetic `FUTURE_CODE_X` are
retained without a registry; this does not classify their scientific meaning.
NAS, ND, IEA and NOT rows are not removed during collection. Positive and NOT
supports of the same term remain separate and no cross-donor veto is applied.

The reducer retains support IDs and grouping sets in memory. Its memory use grows
with the retrieved links and distinct grouping/inventory keys. It is a correctness
reference, not a profiled full-corpus persistence implementation. The streaming
projection is a separate API so the later writer can persist records without
materializing the complete input stream. No storage backend has been selected.

## Important completeness boundary

Every summary is explicitly marked `supplied_observations_only`.

Zero observed rows is NOT a proof of `NO_RETAINED_GOA_ROWS`: the caller may have
supplied a sample, an empty stream or a partial prefilter. The accepted association
stays available, but a definitive missing-annotation status requires a collection
runner that certifies complete coverage of the relevant source and donor universe.

M1 does not read the prepared target catalog. Targets with no accepted Stage 07
association therefore are not invented in these summaries. Full target/release
coverage will consume the already-prepared target catalog in a later increment,
without introducing a FASTA dependency.

This also protects cache reuse: a donor absent from cached observations is not
necessarily a donor previously searched with zero results.

## Validation performed while preparing the patch

Python 3.13.5: syntax checks passed. All four new Python files have line lengths
at most 88 characters. Ninety pytest cases passed locally after final source edits.
Tests imported the actual `AnnotationRecord` extracted from the supplied source
snapshot, together with these new modules; no scientific dependency was stubbed.
The temporary local package entrypoint was empty: the installed repository's
`probe.__init__`, its conftest/settings and its full test suite were not exercised.

The tests cover multiple donors, one donor across targets, release separation,
donor NO, empty observations, only-NOT, positive/NOT conflicts, dynamic evidence
codes, repeated codes with distinct references, distinct physical duplicate rows,
exact database/identifier lookup, provenance, source-order validation, malformed
inputs, deterministic summaries, lazy iteration and propagated read failures.

Ruff is not available in the preparation environment, so no local Ruff PASS is
claimed. The server validation script runs, only on these four new Python files:
`ruff check --fix`, `ruff format`, final `ruff check` and `ruff format --check`,
then the focused pytest tests using `pixi run`. The actual project environment is
a mandatory integration gate. No complete Stage 08 or server validation is claimed
until those results are reviewed.

## Next increment

After this boundary passes in the actual repository, implement a manifest-aware
collection runner and a bounded-memory source/writer path, retaining all original
normalized-GAF provenance and certifying per-release donor coverage. Define its
persistence/publication contract before a canonical scan. Evidence registry and
selection must remain downstream of complete collection, so later policy-only
changes do not rescan the large GAFs or invalidate Stage 01-07.

Full workflow integration, invalidation/restart tests, production-scale checks,
and final truth/benchmark behavior remain distinct subsequent increments.
