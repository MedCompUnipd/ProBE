# Ontology preflight report

## Status

This report was generated on 2026-09-19 from the locally supplied
`data/go.owl`. The project now designates `go.owl` as the master ontology for
navigation, propagation, benchmark construction, and evaluation. The earlier
`NON_GO_PLUS_ONTOLOGY` finding is obsolete and is no longer emitted.

No ontology import was downloaded and no reasoner was run. The artifact is
ignored by Git and remains a local test input.

## Identity

- ontology IRI: `http://purl.obolibrary.org/obo/go.owl`;
- version IRI: `http://purl.obolibrary.org/obo/go/releases/2025-02-06/go.owl`;
- declared release date: `2025-02-06`;
- retrieval date supplied for this local copy: `2026-09-18`;
- SHA-256: `42d7869b3a47cd4a9751d7f2f373acf737b64a1435e4b02b169d134b240dc666`;
- ontology fingerprint: `36a2bc96adf2a506f7162677a67869e93b5684d9b878713873b82332bf69f773`;
- report ID: `471656c182291cc044ae452f2dc7fb0cbf2118f0792dcbe2590c0a106e3c2ed2`;
- import declarations: none;
- axiom mode: asserted plus locally reconstructible expressions, without a
  reasoner.

## Inventory

- RDF triples: 1,423,478;
- named or referenced classes: 51,641;
- GO terms in the projection: 51,641;
- object properties or observed relation IRIs: 10;
- extracted class edges: 100,481;
- active MF terms: 10,154;
- active BP terms: 26,091;
- active CC terms: 4,022;
- terms marked obsolete: 7,728;
- terms marked deprecated: 11,374.

No external classes occur in this artifact. This is consistent with the choice
to use `go.owl` instead of the cross-ontology `go-plus.owl` graph.

## Edge dispositions

| Disposition | Count |
| --- | ---: |
| Propagating | 80,771 |
| Navigable only | 17,349 |
| External context | 0 |
| Cross-aspect | 2,338 |
| Unknown or unsupported | 23 |

The propagating projection contains 72,894 `is_a` edges and 7,877 same-aspect
`part_of` edges. It has no cycle. Every active non-root term retains an `is_a`
path to its own aspect root in this artifact; no term requires admission through
a `part_of`-only root path.

Cross-aspect edges are retained for audit and excluded from scoring. They affect
1,574 source terms but do not invalidate those terms because each retains a
valid own-aspect `is_a` path.

## Non-propagating findings

The semantic navigation projection contains 26 cyclic strongly connected
components. These cycles involve non-propagating relations and do not occur in
the `is_a`/`part_of` scoring hierarchy.

Three temporal relations have no approved use in the initial relation policy
and remain excluded:

- `RO:0002091` (`starts_during`), with no extracted edge in this artifact;
- `RO:0002092` (`happens_during`), with 22 excluded edges;
- `RO:0002093` (`ends_during`), with one excluded edge.

No unsupported anonymous axiom shape, property-hierarchy cycle, or individual
relation cycle was reported by the supported extractor.

## Alternate and deprecated identifiers

The previous loader reported 3,646 `AMBIGUOUS_ALTERNATE_ID` errors because GO
can retain a deprecated class stub while also listing that identifier as an
alternate ID. The new resolver follows all explicit `replaced_by` and alternate
routes, accepts the result only when they converge on exactly one active term,
and still rejects true active-ID conflicts. The provisional artifact now loads
with zero validation errors.

## GOA compatibility check

The corrected projection was reused, without replacing the ontology, to read
the two local 500-accession GAF samples. The diagnostic result is unchanged
from the earlier comparison:

- `experimental_strict`: 58 qualifying direct events on 27 accessions (32
  branch acquisitions, 9 specificity refinements, and 17 evidence upgrades);
- `experimental_all`: 59 qualifying direct events on 28 accessions (33 branch
  acquisitions, 10 specificity refinements, and 16 evidence upgrades).

These are not ground-truth counts. The GAF files contain no protein sequences,
so the check used accession IDs only as temporary diagnostic identities. It also
precedes construction of `Q`, `K0`, `X1`, `M`, and `T`. Thirty-two old
annotations remain excluded as unresolved obsolete terms, 32 are normalized by
an unambiguous replacement, four `NOT` assertions are retained as exclusions,
and one new annotation uses a term absent from this ontology.

## Approval gate

Milestone 2A remains active. Before Milestone 2B may construct `Q`, `K0`, `X1`,
`M`, or `T`, the project still requires:

1. confirmation that the recorded SHA-256 identifies the selected immutable
   `go.owl` release;
2. a fresh preflight run under the updated official `go.owl` policy;
3. review of imports, external endpoints, unsupported expressions, unknown
   relations, cross-aspect edges, root reachability, and every cycle class;
4. explicit approval of the resulting scoring projection and root-eligibility
   policy.
