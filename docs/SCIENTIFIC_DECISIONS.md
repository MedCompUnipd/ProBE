# ProBE scientific decisions

## Status and scope

The authoritative implementation is branch `codex/modifiche-main`. The historical
ProBE codebase is out of scope; only `reference/owlLibrary3.py` is retained as
optional ontology reference material.

ProBE supports general protein-function benchmarks. For the strict benchmark,
each target input requires a protein sequence and explicit NCBI TaxID; a free-text
species name is not a substitute. A UniProt accession is optional and serves only
as an alias or consistency check, never as the identity key. CAFA datasets may be
inputs or validation cases, but core code must contain no CAFA4- or
CAFA5-specific branches.

## Exact sequence identity

Accessions are unstable and are not the primary identity of a protein sequence.
They may become secondary, be replaced, move between representations, disappear,
refer to isoforms, or remain while their sequence changes.

The primary internal identity is the SHA-256 digest of an explicitly normalized
exact protein sequence, accompanied by sequence length and normalization-policy
version. The same exact sequence may correspond to multiple aliases. A target with
an unresolved accession may still match a historical or current record through an
identical sequence. Exact identity and similarity are separate concepts.

## Strict target eligibility and taxonomic context

Strict targets must represent full-length proteins. A target explicitly identified
as a fragment is ineligible. Database-fragment matching and reconciliation remain
future work and are not part of the strict eligibility decision.

Resolve the input TaxID against the NCBI taxonomy snapshot associated with
`start`. Deterministic reconciliation through the official `merged.dmp` is
allowed. Deleted or unresolved TaxIDs must not be guessed from a name or lineage.
The target must have an exact full-length, non-fragment UniProt match at `start`
in the approved resolved taxonomic context. `species_anchor` remains
classification and audit metadata; a shared anchor does not authorize merging
strain or substrain records with different resolved TaxIDs.

For the primary strict temporal benchmark, the same sequence/taxonomic entity
should also be traceable at `end`. Its absence at `end` is reported separately;
it is not evidence of newly acquired functional knowledge. If `start` TaxID
resolution fails, the exact sequence is absent, only fragment matches exist, or
the sequence exists only in an incompatible taxonomy, assign and report an
explicit status for user action. Do not silently discard or repair the target.

## Ambiguous residues

Hashing does not resolve biological ambiguity. Two sequences match exactly only
when their normalized character strings are identical. `U`, `O`, `B`, `Z`, `J`,
`X`, and `*` require explicit validation and provenance. Do not silently convert
ambiguous residues. A versioned optional policy may remove one terminal stop; an
internal stop remains a significant validation event.

## Single ontology snapshot

One pinned official `go.owl` snapshot is used throughout the benchmark
lifecycle. Both GOA releases are interpreted using the same vocabulary chosen for
that benchmark; the same snapshot is used for construction and evaluation.

`go-plus.owl` is not used for node navigation or the scoring hierarchy. The
master `go.owl` artifact contains the GO graph required by ProBE without the
additional cross-ontology structure that makes `go-plus.owl` unsuitable for
this workflow.

This supersedes the branch's earlier proposal to project between two ontology
snapshots. Consequences for obsolete, replacement, alternate, historical, and
newly introduced terms must be explicitly tested and reported.

Record the ontology URL, retrieval date, version IRI, SHA-256, parser version, and
relation-policy version. Never silently replace a published benchmark's ontology.

## Relations and propagation

Navigability is not annotation inheritance. The initial conservative propagation
policy uses `is_a` and `part_of`.

`regulates`, `positively_regulates`, and `negatively_regulates` are causal and may
be navigable, but propagation through them changes the protein-to-term meaning.
`has_part` is not an inverse propagation equivalent to `part_of`. Classify
`occurs_in`, `capable_of`, `capable_of_part_of`, `enables`, `involved_in`,
`located_in`, and other relations by stable IRI, domain, range, and semantics before
admitting them to closure.

ProBE may maintain both a conservative annotation-closure graph and a richer causal
or contextual graph. The graph used for scoring must be pinned in run metadata.

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
the `NOT` qualifier. Preserve excluded records for auditing.

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
parameters, and validation outcomes. Eligibility and exclusion results must be
available in a structured, auditable report or log. Ambiguous cases are
quarantined rather than silently selected or discarded.
