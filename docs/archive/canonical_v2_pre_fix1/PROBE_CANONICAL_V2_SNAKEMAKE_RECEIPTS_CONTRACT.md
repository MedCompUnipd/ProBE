# Canonical V2 DAG: out-of-bundle Snakemake receipts

## Why

Snakemake 9 writes `.snakemake_timestamp` inside a `directory()` output.
ProBE Stage 07 and M8 bundles use `manifest.json` to declare their
**exact** file payload; Stage 08 rejects undeclared files. Therefore the DAG must not declare
any published scientific bundle directory as a Snakemake `directory()` output.
Do **not** loosen `validate_bundle()` or add the timestamp to a scientific manifest.

## Rule output contract

Every one of the 12 generating Snakemake rules publishes the same canonical
science bundle at `destination(spec, stage, role)` as before. Only the DAG
bookkeeping output changes: a regular sibling file at
`<canonical-bundle-path>.snakemake.done`.

- Each producer calls the existing immutable `dispatch()` without changing
  the scientific target path.
- Following successful publication, it creates the sibling receipt with the
  complete SHA256 digest of that stage's native provenance anchor (manifest or summary JSON), plus a newline.
- Consumers depend on the **receipt** (not the scientific directory), while
  scientific kernels continue to open the original immutable bundles.
- The workflow checks existing receipts against their corresponding provenance anchors
  when constructing known dependencies. Invalid receipts, missing provenance anchors,
  and symlinks fail closed. A receipt cannot be overwritten.
- On any failure before receipt creation, the original bundle may already have
  been published. Reusing or deleting it must be deliberate and audited; an
  automatic rerun will not silently overwrite that scientific publication.
- The stage output directory and all its declared payload remain unmodified
  by Snakemake. No `.snakemake_timestamp` inside any bundle.

## Validation

The paired synthetic acceptance test seeds only the already-tested workload,
with its receipt **outside** the immutable bundle. It executes exactly five
scientific Snakemake rules: release-local DP, resolver Stage 07, M8, M10c,
Temporal V2. It verifies full DP for a same-TaxID NONEXACT documentary
candidate, START/END donor separation, GO closure, EVAL confirmation, and
exact provenance-receipt SHA256 matches. No real data or frozen scientific publication is
used. Ruff and Snakemake are checked on the server's Pixi environment.

This patch changes orchestration only, not scientific admission policy.

### Canonical stage provenance anchors

| Node | Receipt SHA256 attests |
|---|---|
| exact | `exact_documentary_keys.summary.json` |
| ledger | `candidate_provenance.summary.json` |
| projection | `primary_candidate_facts.summary.json` |
| lookup | `documentary_nonexact_matches.summary.json` |
| handoff | `documentary_handoff.summary.json` |
| b3 | `canonical_stage05_06a.summary.json` |
| workload | `2C_alignment_preview.summary.json` |
| alignment | `canonical_alignment_execution.summary.json` |
| stage07, m8, m10c, temporal | `manifest.json` |

Legacy Stage 05 publications remain unnecessary. Native JSON summaries embed
checksums of their declared scientific outputs; Stage 07 and M8 continue to
pass the stricter `validate_bundle()` contract without any timestamp exception.
