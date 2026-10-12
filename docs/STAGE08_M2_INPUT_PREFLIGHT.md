# Stage 08 M2 - Read-only collection-input preflight

## Status

Additive library increment prepared on 10 October 2026. This is the input
preflight for the future collection runner, not a GAF extractor, a published
Stage 08 bundle, or a Snakemake integration. Only three new files are added.
Stage 01-07, M1, existing modules, configuration and dependencies stay unchanged.

M1 has now passed its real server integration checks on
`chatgpt/pre-resolver-v2`, commit `5e982a7`:

- canonical reader: 298,498 associations and all authorizations verified;
- START: 150,185 YES associations, 148,758 distinct accession donors;
- END: 148,313 YES associations, 146,877 distinct accession donors;
- the accepted TSV and selection-metadata SHA256 values matched their manifest;
- full repository pytest: 687 passed, 1 skipped;
- the skip is the unavailable optional local TESTING Python oracle;
- no Git staging/commit or canonical pipeline run was performed.

Source: `stage08_m1_integrazione_Thd3rd.txt`. These are validation observations,
not hard-coded production thresholds. They do not establish GAF coverage.

## Purpose and API

`probe.stage08_collection_inputs.prepare_collection_inputs()` takes four
explicit bundle directories:

- the common Stage 07 bundle;
- the START Stage 01B1 bundle;
- the END Stage 01B1 bundle;
- the common Stage 01B0 GO-policy bundle.

It returns immutable declarations (`CollectionInputs`, `GafCollectionInput`)
and the complete M1 `DonorIndex`. The report is a detached dictionary, not a
scientific completion manifest. This API neither guesses release IDs from
folder names nor accepts a global union of START/END authorizations.

The donor database is explicitly `UniProtKB` for the current Stage 07 contract.
This is a namespace constraint on the join, not an evidence-code classifier.
No evidence registry or evidence-selection setting is an input to this layer.

## Checks

Reuse `probe.workflow.artifacts.validate_bundle(deep=False)` for all four
bundles. It checks schema, stage completion, payload names and sizes and rejects
symlinks and undeclared files. M2 adds no competing bundle or restart format.

Hash the four manifests and the two consumed Stage 07 payloads:
`07_accepted_associations.tsv` and `07_selection_metadata.json`. Require both
payload hashes/sizes to match the completed Stage 07 manifest.

Validate the versioned Stage 07 metadata, exact START/END identities, effective
policies and policy version against its manifest. Check its donor-consumption
contract (YES criterion, preservation of nondonors and audit origin). Read the
accepted TSV through M1, preserving all 13 fields and all accepted associations.

Require the supplied GO-policy bundle to be a common Stage 01B0 bundle with a
declared `01B0_go_policy.tsv`. Require both GOA bundles to be Stage 01B1 for
the correct roles/releases and to declare `goa.normalized.gaf` with a SHA256.

Require the same ontology SHA256 in the common policy and both GOA manifests.
Check any supplied expected ontology SHA256 against the observed SHA256 in
that manifest. Stage 07 may legitimately have null ontology provenance; when
it is non-null, it must be consistent as well. No OWL or GO-policy payload is
read or reparsed here.

Each GOA manifest must refer, among its inputs, to the supplied 01B0 manifest
by SHA256 and byte size. A matching filename alone is insufficient. This catches
a stale GO-normalization policy even when two policies use the same ontology.
The recorded historical path is kept as provenance, but is not reopened. Byte-
identical bundles can be moved without changing their content identity.

Watch file identity/size/mtime/ctime while reading required inputs to detect
concurrent changes during preflight. This is not a lock or a cryptographic
snapshot guarantee after return. The future runner must revalidate its consumed
inputs and must never rely indefinitely on a prepared in-memory object.

## Explicit limits of verification

Normalized GAF files are only inspected by filesystem metadata. No GAF, FASTA,
OWL, raw UniProt release or ancestor payload is read. Existing validators check
all declared payload names/sizes but do not hash all payloads in shallow mode.

The report says:

- `normalized_gaf_contents_read = false`;
- `normalized_gaf_content_check = NOT_PERFORMED` for each role;
- `annotation_coverage_certified = false`;
- `recursive_upstream_validation = false`.

A same-size GAF mutation before preflight is NOT detected by this shallow check.
The tests explicitly protect this honest boundary. During future collection,
verify the consumed GAF bytes in the scan, or use an explicitly requested deep
check. Do not introduce a redundant full GAF scan into routine restarts.

No donor is declared to have zero GO rows on the basis of this preflight. Only
an actually completed source scan can establish that status. An empty accepted
TSV is valid and creates an empty donor index, not invented targets.

## Identity and invalidation boundary

The versioned input fingerprint covers the four manifest identities, consumed
Stage 07 payloads, roles, release IDs, database namespace and normalized GAF
content declarations. It is not the complete implementation/run fingerprint.
The future runner must additionally identify its relevant implementation and
collection parameters.

A changed Stage 07 manifest changes this input fingerprint. Evidence-registry
and interpretation-only changes do not enter this layer. The rerun matrix in
`STAGE08_EVIDENCE_POLICY_GENERALIZZATA.md`, revision 1.1, remains mandatory for
future workflow integration. This module does not itself execute, schedule,
invalidate, clear or replace any bundle.

## Validation during preparation

Forty-eight new synthetic tests passed with Python 3.13.5. Together with the
90 M1 tests and 12 existing artifact tests extracted from the provided logs,
150 tests passed in the local preparation package.

The tests used the actual supplied implementations of records, M1 and the
existing artifact module. No scientific or bundle-validation dependency was
stubbed. The local package entrypoints were empty: the real repository's
`probe.__init__`, conftest and full test suite were not exercised locally.

Ruff is unavailable in the preparation environment; no local Ruff PASS is
claimed. The installation script runs Ruff only on the two new Python files,
then the focused and full pytest suites in the existing Pixi environment, and
finally this read-only preflight on the canonical four bundle directories.
The real server results remain the acceptance gate for this new increment.

## Required next collection work

Reuse these validated input declarations in the source/writer path. Complete
that path's streaming, persistence, original-row provenance, successful-EOF
coverage certification and atomic publication before running a canonical GAF
scan. Keep evidence interpretation downstream of complete raw collection.
No extra general survey of Stage 01-07 is required to continue that work.
