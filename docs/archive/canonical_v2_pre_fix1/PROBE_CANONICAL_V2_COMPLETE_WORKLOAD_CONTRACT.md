# ProBE canonical V2 — complete release-local alignment workload

Schema: `probe-canonical-v2-complete-workload-v1`.

This is a **new, noncanonical-publication** integration layer. It consumes
previously verified B3 candidate facts and the versioned two-lane documentary
handoff; it does not change the scientific Stage 07 resolver or any historical
results. The layer replaces the former assumption that all candidate records
must be identified before cross-release documentary discovery.

## Invariants

- EXACT START and EXACT END contribute to a shared **search-only** documentary
  register upstream. The candidate identity, FULL record, taxonomy, alignment
  and eventual GO annotation stay restricted to the evaluation release.
- An existing B3 candidate is preserved byte-for-byte as a primary TSV row.
  The B3 bundle, target file, original 06A script and both 01A_FULL manifests
  are SHA256-attested before any output is created.
- Each new documentary candidate is materialized from the FULL V2 record
  belonging to its evaluation release, with RID/accession/OX/fragment and
  SHA256 of the literal amino-acid sequence verified. If its RID already has
  a sequence in B3, that sequence is reused and its primary-reference count
  is updated. Otherwise the source FULL FASTA is scanned once with its pinned
  whole-file digest verified.
- Only documentary candidates with at least one literal typed-key observation
  enter the current 06A workload. Normalization-only matches are written to
  `documentary_normalized_only_deferred.tsv`, with all original observations
  preserved in `documentary_evidence_preserved.tsv`. A literal match is an
  **alignment search input**, not a sufficient proof of donor eligibility.
- Document-only candidates are marked with discovery channel
  `CANONICAL_DOCUMENTARY_V2`, with no fabricated historical lineage or sequence
  discovery flags. The old Stage 07 parser **must not** be used to authorize
  them: the future canonical resolver must consume the new evidence sidecar.
- Stage 06A verifies literal sequence exactness from the real target and FULL
  sequence. This stage runs **no DP, makes no donor decision**, and does not
  select GOA annotations.
- The two evaluation releases have distinct candidate identities and stores.
  Stage 2C alignment completion's input preflight accepts the emitted preview
  format. Its verified same-resolved-TaxID DP gate remains necessary.
- Independent documentary routes for targets without EXACT candidates remain
  available; they are neither suppressed nor invented by this stage.
- Existing Stage 05C2 audit rows are retained with corrected primary-overlap
  flags for newly present candidates. Audit provenance cannot become GO donor
  evidence without the resolver's subsequent checks.

## Output inventory

A completed directory contains:

- `2C_05A2_variant.tsv`: all verified B3 candidates and literal documentary
  additions, sorted and deduplicated within each release.
- `2C_05C2_variant.tsv`: preserved audit provenance.
- `2C_05D_START_variant.tsv`, `2C_05D_END_variant.tsv`: complete FULL sequences.
- `2C_06A_alignment_jobs.tsv`, `2C_06A_summary.json`: verified 06A jobs.
- `documentary_evidence_preserved.tsv` and
  `documentary_normalized_only_deferred.tsv`: complete audit sidecars.
- `2C_alignment_preview.summary.json`: manifest with SHA256 identity and
  `legacy_resolver_compatible: false`.

No historic 05A2/05C2/05D publication is required as an input. The B3 files
may continue to carry those historical-looking output names internally for
compatibility with already validated algorithms, but they are **not** paths to
old frozen publications.

## Remaining integration boundary

This layer closes the critical **all-candidates-before-06A** gap. It is **not**
the completed canonical Snakemake DAG or Stage 07 scientific resolver. The
legacy Stage 07 `load_candidate_universe` contract intentionally rejects
`CANONICAL_DOCUMENTARY_V2` candidates because those observations did not pass
through old `04D1/04D2`. Connecting them requires a new documentary
qualification input, without inventing old evidence. Until that exists, no
canonical GO donation, M8, M10c or Temporal V2 execution is authorized.

## Validation

The preinstallation gate runs Ruff with the actual Pixi/project configuration
and all V2 tests, including independent science regressions selected for
read-only comparison. No `--fix`, commit, push or mass scan is part of this
package. Fresh SHA256 identities must be used for any future run: the frozen
Stage 2C fingerprint `eeac66878bdfb8e1b96fdff6b297a675214912db699d0a715101c5a079ab7990`
is never reused for a new scientific graph.
