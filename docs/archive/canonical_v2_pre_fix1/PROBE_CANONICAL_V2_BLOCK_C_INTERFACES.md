# Canonical V2 Stage 06/07/08 interface proofs

Status: staging only. This package does **not** activate Snakemake.

## Scope and scientific noninterference

This package adds two format adapters and one CLI. It does not change any
existing scientific algorithm, alignment policy, GOA, M10c or temporal
benchmark. No historical Stage 2C plan is reconstructed. No existing
publication is replaced.

* The B3 output is checked against its own complete digest table, release
  mapping and closed list of filenames before six Stage 05/06A payloads are
  copied byte-for-byte into the validated Stage 2C preview interface.
* `probe.stage2c_alignment_completion._preview` verifies the resulting
  staged directory. The completion policy gate remains the existing,
  validated code. The new adapter does not execute DP.
* The Stage 2C resolver result is checked against the release-specific,
  policy-specific summary and SHA256 before publishing the 13-column Stage 07
  input. The `DonorAssociation` contract, preserved `annotation_donor` flags,
  multiplicities and Stage 08 `_selection` validator are exercised.
* A new Stage 07 manifest records the source resolver outputs. Historical
  Stage 07 adapter publications remain immutable.

## Remaining gates before canonical Snakemake activation

1. Stage 06C1 execution or authorized reuse must be connected to the B3
   preview using `reuse_canonical=None` for a brand-new run, never a legacy
   dependency. DP remains forbidden for NONEXACT different-TaxID pairs.
2. A new-run resolver must use this preview/completion plus Stage 01/02/04
   source bundles. It must execute only the selected donor policy.
3. Stage 08 M8, M10c and Temporal V2 must consume the new Stage 07 bundle
   and precisely pinned GOA, GO policy, ontology and registry inputs.
4. The frozen 2026-10-16 `reuse-only` route must verify its original Stage
   2C, adapter, M8, M10c and Temporal V2 fingerprints without recalculation
   or modification.
5. Snakemake must prove one reachable canonical DAG, and a fresh synthetic
   cold-start must have no compulsory legacy Stage 05/06/07 publication.

No full canonical DAG or production readiness is asserted by this package.
