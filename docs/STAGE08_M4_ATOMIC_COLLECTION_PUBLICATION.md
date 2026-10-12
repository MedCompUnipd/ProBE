# Stage 08 M4 - Common atomic publication for complete annotation collection

Status: additive library increment, not production workflow integrated.
Prepared 10 October 2026. Stage 01-07 and previously validated M1-M3 remain
unchanged. This document records required behavior and local synthetic tests.

## Purpose

`probe.stage08_collection_publish.publish_raw_annotation_collection()` accepts
four explicit upstream bundle directories and an output destination. It calls
M2 `prepare_collection_inputs()`, runs M3 for START and END, and publishes one
complete **common** Stage 08 raw-collection bundle through the existing
`atomic_stage_bundle()`. No ontology propagation, evidence filtering, NOT veto,
benchmark truth, FASTA parsing, or Stage 07 rerun occurs.

This API **would read the complete GAFs if called with canonical inputs**.
The installation/validation script deliberately never calls it on canonical
inputs. The production CLI and Snakemake integration are deferred.

## Atomicity and certification

Both roles use one private same-filesystem staging transaction. M3 checks EOF,
source-file metadata, version header and exact GAF SHA256 for each release. The
publisher rejects an uncertified result, role/release/digest mismatch, missing
files, extra files, changed upstream input identities and changed implementation.
It also rechecks both GAF filesystem stamps at transaction end.

M3 writes into per-role private directories. After a successful role scan,
M4 flattens each role's five files into the common staging root, using the
already disjoint `08_start_...`/`08_end_...` filenames. This ensures the
existing artifact publisher fsyncs files and their actual containing directory;
no nested directory durability is assumed.

The **single** root manifest is written only by `atomic_stage_bundle()` after
both scans, final validation and construction of all payload files. All output
SHA256 values are persisted in the manifest. Any exception before completion
leaves no completed destination; staging is removed by the existing context
manager. An existing completed destination is never overwritten, even with the
workflow-only invalidation option enabled.

`allow_invalidated_destination=True` is reserved for an upstream workflow that
has *already* invalidated the completion marker. It is not permission to bypass
or delete a valid completed bundle. No manual removal of manifest is required
or performed by this new code.

## Canonical output schema

One common Stage 08 bundle, currently intended for
`results/pipeline-v2/common/08`, contains:

- five `08_start_*` payloads from M3;
- five `08_end_*` payloads from M3;
- `08_collection_summary.json` with the shared input fingerprint, per-role scan
  metrics and `annotation_coverage_certified=true`;
- `stage_outcome.json` with `COMPLETED` or `EMPTY_RESULT` and aggregate counts;
- `manifest.json` with upstream input identities, pinned ontology, parameter
  and implementation fingerprints and SHA256 values for all 12 payloads.

The manifest's eight inputs include four upstream manifests, two verified
Stage 07 payloads, and the two normalized GAF declarations. Their actual bytes
are verified by M3 during collection, not by a separate up-front full scan.
The preflight of M2 checks only size/metadata for GAFs; that remains honest.

The collection parameters explicitly state `evidence_selection=none`,
`not_veto=none` and the Stage 07 donor authorization criterion. Changing later
interpretation-only evidence policies does **not** require rescanning source GAFs
when this full raw collection remains valid.

Zero retrieved donor rows after verified EOF for both roles is a valid
`EMPTY_RESULT`. A donor YES with zero rows then receives
`NO_RETAINED_GOA_ROWS` in its per-association coverage. This is not a statement
about absence of biological function. Donor NO rows are preserved but never
linked. Full catalog coverage for targets with no Stage 07 accepted association
requires a later independent target-catalog input.

## Provenance boundaries

The publisher computes SHA256 of M1-M4's relevant modules, `records.py`,
`workflow/artifacts.py`, `workflow/outcome.py`, and `workflow/runtime.py`,
and refuses publication if the implementation changes mid-collection.
It uses the common ontology provenance already validated by M2. It re-runs
M2 before publication and compares input fingerprints; it also compares file
stamps for both GAFs across the whole collection interval.

This does not claim an operating-system snapshot or a lock on upstream files.
Immutable upstream bundles are required; concurrent replacements outside the
workflow remain a risk. A late modification after the final check is not
excluded by these mechanisms, and a deep bundle validation is recommended
when auditing completed outputs.

## Synthetic acceptance tests

The incremental tests exercise: single bundle with START+END; source physical
line and donor link provenance; donor with no retained rows; donor NO; an
unfamiliar evidence code; complete SHA256 manifest verification; inability to
replace a completed bundle; failure of END after completed START; exception
mid-transaction; mutation of Stage 07 metadata or START GAF during the scans;
implementation fingerprint mutation; empty result; empty donor projection;
explicit invalidated-destination behavior; rogue output rejection; refusal of
uncertified scans and writer lock contention.

Sixteen M4 tests and 171 combined M1-M4 tests passed in the local preparation
package with Python 3.13.5. The repository's full test suite and actual Ruff
validation must be run on the server; preparation does not claim they passed.

## Next work

Before any 304 GB canonical scan:

1. Finalize the production runner and workflow invalidation contract, without
   allowing direct invocation to replace a completed bundle.
2. Run bounded real-GAF throughput/memory/disk checks on a noncanonical output
   directory. The bounded sample must not declare donor absence or certified
   full-coverage status.
3. Confirm output disk headroom, restart behavior and SHA256-on-read overhead.
4. Only then authorize the full START+END collection run, validate its bundle,
   and proceed to configurable evidence-registry interpretation.

Do not alter Stage 01-07, their bundles, or M1-M3 to install this increment.
