# ProBE development instructions

## Authoritative codebase

The authoritative starting point is the current Git branch `codex/modifiche-main`.
The historical ProBE implementation must not be used as the architectural or
behavioral baseline.

Do not inspect, reproduce, port, patch, or compare against the historical ProBE
codebase unless the user explicitly requests it. The only historical component
retained for possible reuse is `reference/owlLibrary3.py`.

This file is reference material. It must not be imported into production code
until its behavior has been evaluated, tested, and explicitly approved.

## Project objective

ProBE is a general, notebook-first Python library for constructing time-resolved
protein-function benchmarks. It is not specific to CAFA4 or CAFA5.

The system must:

1. Read arbitrary target proteins from FASTA.
2. Establish exact sequence identities using deterministic hashing.
3. Associate each sequence identity with historical and current aliases.
4. Inspect annotations associated with those aliases in two GOA releases.
5. Identify newly acquired annotations and evidence changes.
6. Apply explicit evidence-code policies.
7. Use one official immutable GO ontology snapshot.
8. Construct an auditable benchmark.
9. Later evaluate prediction tools using the same ontology snapshot and policy.

## Existing architecture

Preserve the structured architecture already present under `src/probe`, including
the identity, evidence, ontology, snapshot, comparison, parsing, records, source,
and validation modules.

Do not rewrite these modules merely because substantial changes are expected.
First inspect their interfaces, tests, and scientific invariants. Refactor or
replace a component only when supported by a written reason and regression tests.
Preserve the notebook-first API unless a reviewed design decision changes it.

## Protein identity

Exact sequence identity is the primary identity criterion. The internal key must
be based on normalized protein sequence, sequence length, SHA-256 digest, and
normalization-policy version. Database accessions are aliases, not biological
identity keys.

The model must support one sequence identity associated with multiple accessions,
one accession associated with different sequences in different releases, UniProt
primary and secondary accessions, UniProtKB and UniParc identifiers, canonical
proteins and isoforms, historical aliases, source database and release, reviewed
status, and taxonomy metadata.

Similarity matching, including 98% identity, is a separate future operation and
must never be treated as exact identity.

## Sequence normalization

Before exact hashing:

- remove FASTA formatting and whitespace;
- convert sequence letters to uppercase;
- preserve the biological sequence;
- handle a terminal `*` only through an explicit configurable policy;
- do not silently remove or substitute internal `*`;
- do not silently transform `U`, `O`, `B`, `Z`, `J`, or `X`;
- retain ambiguity and validation information;
- retain the original and normalized sequences;
- record the applied normalization policy and its version.

The hash identifies an exact normalized character sequence. It does not resolve
biological ambiguity.

## Ontology policy

Use one official immutable snapshot of
`https://purl.obolibrary.org/obo/go/extensions/go-plus.owl`.

The same snapshot must be used for interpreting both GOA releases, benchmark
construction, ground-truth propagation, prediction evaluation, and metrics.
Do not use separate old and new ontology snapshots in the definitive benchmark.

The selected OWL file must be pinned locally and accompanied by its original URL,
retrieval date, ontology version IRI, SHA-256 digest, parser version, and relation
policy version. Do not silently replace the ontology during ordinary analysis.

## Ontology relations

Separate these concepts: a property is present in the OWL, its edge can be
traversed, and a GO annotation can be safely inherited through it. These are not
equivalent. Identify relations through stable IRIs, never only through labels.

Inspect and represent at least `is_a`, `part_of`, `has_part`, `regulates`,
`positively_regulates`, `negatively_regulates`, `occurs_in`, `capable_of`,
`capable_of_part_of`, `enables`, `involved_in`, and `located_in`.

The initial conservative benchmark closure uses only:

- `rdfs:subClassOf` / `is_a`;
- `BFO:0000050` / `part_of`.

Do not silently use causal, inverse mereological, contextual, annotation, or
cross-ontology relations as ordinary parents. They may be retained in a separate
semantic graph. Before changing the propagation set, add tests and explain the
biological meaning of every candidate relation.

## owlLibrary3.py

`reference/owlLibrary3.py` is the only retained component from historical ProBE.
Treat it as a source of potentially useful ontology-navigation logic, not as an
authoritative implementation.

Evaluate its OWL parsing, recognized and valid edges, use of labels versus IRIs,
namespace handling, alternate and obsolete IDs, cycles, ancestor and descendant
traversal, closure, information content, SimGIC behavior, compatibility with
`go-plus.owl`, and resource usage.

For every reusable behavior, create a focused test before adapting it. Do not port
unrelated legacy functions. Prefer extending the existing `src/probe/parsing/owl.py`
and `src/probe/ontology.py` architecture over creating a parallel implementation.

## Evidence policies

Strict experimental evidence consists of `EXP`, `IDA`, `IPI`, `IMP`, `IGI`,
`IEP`, `HTP`, `HDA`, `HMP`, `HGI`, and `HEP`. High-throughput codes are
experimental but must remain identifiable as their own subcategory.

A broader optional curated policy may consider `IBA`, `IBD`, `IKR`, `IRD`, `ISS`,
`ISO`, `ISA`, `ISM`, `IGC`, `RCA`, `TAS`, and `IC`. These must not be described
as direct experimental evidence.

Exclude `IEA`, `ND`, `NAS`, and annotations carrying the `NOT` qualifier from
positive strict ground truth by default. Preserve excluded assertions and their
exclusion reasons. Evidence presets and upgrades must be explicit policies, not a
universal confidence ranking.

## Annotation comparison

Compare annotations only after resolving sequence identity and aliases. Track
new, unchanged, and removed direct annotations; evidence, reference, qualifier,
extension, and product-form changes; evidence-policy upgrades; accession migration
with identical sequence; sequence changes under the same accession; alternate and
obsolete GO identifiers; policy exclusions; and unresolved identities.

Keep direct assertions separate from ontology-propagated terms. Never report a
new propagated ancestor as a new direct experimental annotation.

## Benchmark traceability

Every selected record must be traceable to its target FASTA record, normalized
sequence and hash, matched aliases, database release, old and new GOA assertions,
evidence, qualifier, reference, date, assignment source, ontology snapshot,
propagation policy, and selection or exclusion decision. Report ambiguous cases
separately rather than silently admitting them.

## Engineering rules

Continue using Python 3.11+, the existing `src/` layout, Pixi, `pyproject.toml`,
type annotations, pytest, Ruff, structured validation, streaming parsers, immutable
records where appropriate, pure transformations, and explicit I/O.

Do not introduce pandas as the internal model, a separate CLI implementation,
persistence technology before profiling realistic releases, or dependencies without
a documented need. Tests must not depend on mutable network resources.

## Working method

Before changing scientific behavior: inspect current code and tests; describe the
current and intended behavior; identify biological and software risks; add tests;
make the smallest coherent change; run `pixi run check`; and update documentation
and the development ledger.

Do not change stable decisions incidentally. Document ambiguous alternatives and
ask the user rather than silently deciding. Do not claim completion without tests.
