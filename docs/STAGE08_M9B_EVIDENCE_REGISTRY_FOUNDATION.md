# ProBE Stage 08 / M9b — Versioned evidence registry foundation

Status: **additive, library-only, not in the production Snakemake DAG**.

The canonical M8 raw collection is immutable. M9b adds a structured registry
and independent policy compiler without GO graph traversal, GAF reads, K0,
neutrality processing, NOT veto, truth, or benchmark publication.

## Ontology decision from two read-only audits

Pinned OWL SHA256:
`42d7869b3a47cd4a9751d7f2f373acf737b64a1435e4b02b169d134b240dc666`

The independent audit identifies 72,894 named `is_a` records and 7,877
same-aspect existential `part_of` records, with 78,639 unique direct eligible
edges after deduplication. The loader `GeneOntology.from_owl(strict=False)`
returns exactly these 78,639 eligible edges (zero missing and zero extras).
This includes candidates derived from `owl:equivalentClass` intersections,
not merely explicit `rdfs:subClassOf` triples.

**Scientific scope:** no policy extension is approved from this audit. The
existing conservative positive closure (`is_a`/`part_of`, same aspect) remains
the reference candidate. Regulatory relationships remain distinct navigational
context and must not be silently treated as annotation inheritance. A separate
M10 verification must test closure consistency, DAG assumptions, the negative
closure direction, and original GAF column 4 gene-product relation semantics.
The audit did not perform OWL reasoning or inspect GAF relation qualifiers.

## Registry and policy semantics

`src/probe/stage08_evidence_registry.py` implements:

- A JSON registry (`probe-evidence-registry-v1`), with unique registered
  evidence code strings, documented provenance and user-definable overlapping
  groups. The example registry is **only a compatibility snapshot** of ProBE's
  former groups; it does not claim to be a GO/ECO ontology release.
- `compile_selections` for four explicit independent purposes: `score`,
  `prior`, `neutral`, and `not`. Each references a named profile. Profiles
  select `include_groups` union `include_codes` minus `exclude_codes`.
- Deterministic content fingerprints independent of key/list order, but
  sensitive to changes in registry metadata, effective code selections, or
  the registry identity. This is preparation for downstream provenance.
- Fail-closed behavior for unregistered groups, explicit codes, absent role
  assignments, malformed schema, and duplicate JSON keys.
- Explicit reporting or fail-closed review for codes observed in the donor
  inventory but absent from the configured registry.
- Empty profiles mean *no codes*, not *all codes*.

The compiler does not decide GO annotation quality or impose a tier ordering.
It does not filter stored source assertions; later readers will test selected
codes against the complete donor provenance without dropping other supports.
The `not` selection **only identifies eligible negative assertions**; it does
not implement a NOT veto, contradictions, or masking. Such semantics require
an independent contract and tests.

## Files and publication boundary

- `src/probe/stage08_evidence_registry.py`
- `tests/test_stage08_evidence_registry.py`
- `config/stage08_evidence_registry.example.json`
- `docs/STAGE08_M9B_EVIDENCE_REGISTRY_FOUNDATION.md`

No modifications to Stage 01–08 code, to the completed M8 bundle, or to the
Snakemake workflow. No production interpretation runs or Git commits.
