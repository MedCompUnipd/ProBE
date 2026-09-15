# ProBE scientific decisions

## Status and scope

The authoritative implementation is branch `codex/modifiche-main`. The historical
ProBE codebase is out of scope; only `reference/owlLibrary3.py` is retained as
optional ontology reference material.

ProBE works with arbitrary FASTA protein targets. CAFA datasets may be inputs or
validation cases, but core code must contain no CAFA4- or CAFA5-specific branches.

## Exact sequence identity

Accessions are unstable and are not the primary identity of a protein sequence.
They may become secondary, be replaced, move between representations, disappear,
refer to isoforms, or remain while their sequence changes.

The primary internal identity is the SHA-256 digest of an explicitly normalized
exact protein sequence, accompanied by sequence length and normalization-policy
version. The same exact sequence may correspond to multiple aliases. A target with
an unresolved accession may still match a historical or current record through an
identical sequence. Exact identity and similarity are separate concepts.

## Ambiguous residues

Hashing does not resolve biological ambiguity. Two sequences match exactly only
when their normalized character strings are identical. `U`, `O`, `B`, `Z`, `J`,
`X`, and `*` require explicit validation and provenance. Do not silently convert
ambiguous residues. A versioned optional policy may remove one terminal stop; an
internal stop remains a significant validation event.

## Single ontology snapshot

One pinned official `go-plus.owl` snapshot is used throughout the benchmark
lifecycle. Its release date is an independent benchmark input and need not match
either GOA release. Both GOA releases are interpreted using the same vocabulary;
the same snapshot is used for construction and evaluation.

This supersedes the branch's earlier proposal to project between two ontology
snapshots. A term absent from the pinned snapshot is excluded symmetrically from
either GOA release. Active alternate IDs and one unambiguous `replaced_by` target
are normalized to the active canonical ID. Obsolete or deprecated terms without
one active replacement are excluded; `consider` suggestions are reported but are
never selected automatically.

Record the ontology URL, retrieval date, version IRI, SHA-256, parser version, and
relation-policy version. Never silently replace a published benchmark's ontology.

## Relations and propagation

Navigability is not annotation inheritance. The graph can be navigated upward
through `is_a`, `part_of`, `regulates`, `positively_regulates`, and
`negatively_regulates`. The conservative annotation propagation policy uses only
`is_a` and `part_of`.

`regulates`, `positively_regulates`, and `negatively_regulates` are causal and may
be navigable, but propagation through them changes the protein-to-term meaning.
`has_part` is not an inverse propagation equivalent to `part_of`. Classify
`occurs_in`, `capable_of`, `capable_of_part_of`, `enables`, `involved_in`,
`located_in`, and other relations by stable IRI, domain, range, and semantics before
admitting them to closure.

`has_part` is retained as graph information but is excluded from upward
navigation and annotation closure. ProBE maintains both a conservative
annotation-closure graph and a richer causal graph. The graph used for scoring
must be pinned in run metadata.

## Evidence codes

Traditional experimental codes are `EXP`, `IDA`, `IPI`, `IMP`, `IGI`, and `IEP`.
High-throughput experimental codes are `HTP`, `HDA`, `HMP`, `HGI`, and `HEP`.
Both are experimental, but their subcategories remain observable.

Curated but not directly experimental categories may include phylogenetic `IBA`,
`IBD`, `IKR`, `IRD`; computational `ISS`, `ISO`, `ISA`, `ISM`, `IGC`, `RCA`;
and statements `TAS`, `IC`. They may define a broader curated benchmark but must
not be silently merged into strict experimental ground truth.

Exclude by default from positive strict ground truth: `IEA` (automatic), `ND`
(absence of biological data), `NAS` (non-traceable statement), and assertions with
the `NOT` qualifier. A NOT assertion also excludes that node and all descendants
reachable through `is_a` and `part_of`, for the same sequence identity and GO
aspect. Preserve excluded records for auditing.

## Information content and SimGIC

Information content is computed independently within each GO namespace from
direct annotation counts accumulated over the conservative annotation closure.
SimGIC is the information-content-weighted Jaccard similarity of the two propagated
term sets. The annotation corpus, smoothing value, ontology snapshot, and relation
policy are part of the reproducibility metadata.

## Direct and propagated annotations

Direct annotations and ontology-derived ancestors are separate products. A new
direct experimental assertion may select a target. A derived ancestor must not be
reported as a new direct annotation. Propagated ground truth retains a link to the
direct assertion that generated it.

## Annotation novelty

Novelty is not simply a new GAF row. Comparison must consider sequence identity,
aliases, GO IDs, alternate IDs, obsolescence, qualifiers, evidence, references,
extensions, product forms, assignment source, and direct versus propagated status.
Every selected target receives a machine-readable explanation.

## Reproducibility

Benchmark outputs record hashes of every input, ontology identity, GOA releases,
sequence-normalization policy, evidence policy, relation policy, software version,
parameters, and validation outcomes. Ambiguous cases are quarantined rather than
silently selected or discarded.
