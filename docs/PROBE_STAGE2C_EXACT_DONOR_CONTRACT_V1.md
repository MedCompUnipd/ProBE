# ProBE Stage 2C — EXACT donor contract v1 (normative)

**Status:** scientific contract frozen on 2026-10-16 for the isolated
Stage 2C evidence/resolver variant in ProBE pipeline V2. A change to any
eligibility rule requires an explicit scientific decision, a new contract
version, regression tests and new content-addressed outputs. Optimizations
must preserve the rules. This does **not** silently replace historical
canonical Stage 07 or its immutable results.

## Eligibility matrix

| Verified target–candidate relationship | Dynamic programming | Locus / independent documentary relation | Stage 2C GO donor |
|---|---|---|---|
| Same resolved TaxID, literal EXACT | Never | Not required for automatic EXACT admission | Eligible under `strict` and `same_species_exact` |
| Different resolved TaxIDs, same verified species, literal EXACT | Never | Not required for automatic EXACT admission | Eligible only under `same_species_exact` |
| Same resolved TaxID, NONEXACT | Required if no verified reusable alignment is available | Required, including a valid independently supported relation | Only if the documentary and other resolver criteria admit the pair |
| Different resolved TaxIDs, NONEXACT | Never | Not applicable to this DP route | Not eligible by sequence similarity under either Stage 2C policy |

Here **EXACT** means verified identity of complete, literal amino-acid
sequences, consistent with Stage 06A/06C1 and the original sequence records;
an accession alias, hash, or alignment flag alone is not proof. Both resolved
TaxIDs must come from the relevant verified taxonomy contexts. For different
TaxIDs, shared *verified species* is essential; shared names or an inferred
species anchor are insufficient. The strict policy does not automatically
admit cross-TaxID candidates. All discovered candidates and provenance are
retained even when donor eligibility is denied.

### Guardrails and interpretation

- Automatic EXACT admission requires an unambiguous literal protein sequence;
  `X`, `B`, `Z`, `J` and `*` do not qualify. `U` and `O` are explicit residues.
  An independently accepted legacy documentary association is not rewritten
  merely because the automatic override is unavailable.
- Candidate-fragment records are **not** admitted on exactness or similarity
  alone: independent documentary-fragment evidence is required. The separate
  `documentary_fragment=allow|report_only` control still determines GO donor
  permission; do not confuse this with the target's strict full-length rule.
- The NONEXACT same-TaxID DP route is separate from donor permission. Existing
  verified canonical DP may be reused; a missing required alignment must be
  reported, not silently treated as an audit-only policy skip. Cross-TaxID
  NONEXACT is `policy_skipped` by design and must never trigger a DP job.
- For same-TaxID EXACT, the primary candidate path is auto-admissible without
  documentary locus. The canonical documentary baseline continues to cover
  eligible other cases. Taxonomy-audit records retain their scope and are not
  silently reclassified as primary candidates.
- GO propagation and donation are separate decisions. Source GO annotations
  must be available in the corresponding START/END release; evidence-code
  selection, negative `NOT` assertions, ontology propagation and temporal
  EVAL/SKIP/NEUTRAL masking remain downstream checks. An eligible donor is not
  itself evidence of a newly acquired experimental GO term.

## Implementation and regression boundaries

- Shared Stage 2C discovery, candidate union, provenance and alignment
  preview: `src/probe/stage2c_*` and Stage 2C runner scripts.
- Policy-level admission: `src/probe/stage2c_resolver_variant.py`, checked
  against literal sequences supplied by
  `scripts/pipeline/07_resolve_stage2c_variant.py` and the cross-strain gate
  in `src/probe/cross_strain.py`.
- Strict baseline remains in `src/probe/resolver.py`; frozen canonical
  outputs must not be edited to promote the new contract. The lower-level
  `cross_strain.py` gate's same-TaxID `DEFER_TO_DOCUMENTARY` result is **not**
  the final donor decision: `stage2c_resolver_variant.py` implements the
  authoritative same-TaxID EXACT override after baseline resolution. An
  old helper docstring must not be used as a substitute for this contract.
- Regression protections: `tests/test_stage2c_resolver_variant.py`,
  `tests/test_stage2c_taxid_dp_gate.py`,
  `tests/test_stage2c_policy_skipped_alignment.py`,
  `tests/test_stage2c_dp_chunking.py`, and Stage 2C bridge tests.
- Opt-in DAG: `workflow/stage2c_variants.smk` included from
  `workflow/Snakefile` only with a pinned Stage 2C variant plan;
  `stage2c_execute_variant` defaults to `false`. A separate Stage 08 bridge
  and GO-processing commands consume the published policy-specific donors;
  they are **not** replaced by canonical Stage 08 Snakemake targets.

The Stage 2C evidence integration identity hashes source bytes, including
`workflow/Snakefile`, `workflow/stage2c_variants.smk`, and Stage 2C resolver
sources. A scientific-source edit changes that identity and requires a new
plan/fingerprint. This documentary freeze does not change pinned source bytes.

## Validated frozen scientific reference

**Date:** 2026-10-16. **Branch:** `chatgpt/pre-resolver-v2`.
**Content-addressed Stage 2C run key:**
`eeac66878bdfb8e1b96fdff6b297a675214912db699d0a715101c5a079ab7990`.
START `2023_01`; END `2026_03`.

| Measure | `strict` | `same_species_exact` |
|---|---:|---:|
| Accepted donor associations | 328,630 | 848,631 |
| Newly admitted same-TaxID EXACT donors versus prior contract | +30,138 | +30,138 |
| Additional cross-subtaxon donors against same-run `strict` | — | +520,001 |
| Final benchmark EVAL | 358,527 | 358,675 |
| Final benchmark NEUTRAL | 550 | 550 |

Compared with the **previous** strict contract, the final extended policy
acquired **47,185 propagated target–GO–release keys** and **5,521
associations with newly acquired SCORE support**: 1,763 attributable to
same-TaxID EXACT corrections and 3,758 to the cross-subtaxon EXACT policy.
Of these SCORE gains, 3,053 are new positive associations and 2,468 had
pre-existing positive support without SCORE. None of these newly SCORE-backed
keys had accompanying propagated `NOT` support in the verified comparison.
These counts are **not** equivalent to newly acquired temporal EVAL terms.

Validated scientific chain: Stage 2C discovery and resolver, Stage 08 GOA
collection, M10c ontology/evidence interpretation, Temporal V2 benchmark.
Final resolver patch: Ruff PASS; full regression **1,310 passed, 1 skipped**.
No new DP was performed in the final exact-contract run. Read-only M10c
comparison: `MONOTONIC_M10C_EVIDENCE=PASS`; all older Stage 2C/Stage 08/M10c/
Temporal V2, V5, and canonical publications preserved unchanged. Published
results remain immutable and versioned by their own provenance.

## Scope of Git consolidation

Committing the validated Stage 2C implementation and its documentation does
**not** automatically enable it in normal canonical production runs.
Canonical promotion and integration of downstream Stage 08 jobs into the
Snakemake DAG are distinct engineering decisions requiring separately pinned
outputs and compatibility checks. Do not silently overwrite canonical
Stage 07/08 manifests or use their old decisions as a substitute for this
contract. Historical Stage 2C reports dated 2026-10-14 describe interim
policies and are retained as audit history, not as current normative rules.
