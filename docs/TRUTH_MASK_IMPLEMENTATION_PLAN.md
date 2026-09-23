# ProBE ontology preflight, truth-set, and evaluation-mask implementation plan

## 1. Scope and authority

This document records the design and implementation contract for the second milestone,
**ontology preflight, truth-set, and evaluation-mask construction**, for branch
`codex/modifiche-main`. Milestones 2A and 2B are now implemented against
synthetic fixtures; a definitive benchmark profile and real-release execution
still require the approvals listed in section 18.

The governing sources, in order, are:

1. `AGENTS.md`;
2. `docs/BENCHMARK_SELECTION_AND_METRIC_SPEC.md`;
3. `docs/SCIENTIFIC_DECISIONS.md`;
4. `docs/BENCHMARK_IMPLEMENTATION_GAP_ANALYSIS.md`;
5. `DEVELOPMENT.md` and the current public code and tests.

The work is divided into two approval gates. Milestone 2A establishes the
ontology context and runs preflight on the real pinned release. Milestone 2B
consumes the approved scoring projection and the canonical direct assertions
and events from the completed first milestone. Neither milestone includes
prediction ingestion, information content or information accretion, metrics,
bootstrap, a CLI, persistence, or a final export format.

## Milestone division and approval gates

### Milestone 2A — ontology context and preflight

Milestone 2A contains only:

- stable ontology identity and fingerprinting;
- preservation of the supplied RDF graph;
- object-property inventory;
- edge extraction with verifiable axiom provenance;
- relation policy and per-edge disposition;
- classification of active GO, external, obsolete, and deprecated classes;
- own-aspect root reachability after edge filtering;
- cycle analysis on explicitly defined projections;
- deterministic ontology preflight reporting;
- synthetic loader, relation, and preflight tests;
- execution against the real, locally pinned `go.owl` release.

Milestone 2A does not construct Q, closure truth, K0, D_score, D_neutral1, X1,
M, or T. It may define stable handoff interfaces required by 2B. Completion of
synthetic tests alone is insufficient: the real preflight report and scoring
projection require human review and approval.

### Milestone 2B — truth and masks

Milestone 2B begins only after the real 2A preflight and scoring projection are
approved. It contains:

- Q for MF, BP, and CC;
- inclusive closure with bounded provenance;
- D_score and D_neutral1;
- K0 and X1;
- M and final truth T;
- target-aspect eligibility and audit records;
- synthetic truth and mask tests.

Prediction ingestion, score propagation, information accretion, metrics, and
final export remain later milestones.

## 2. Current foundations and gaps

The current implementation already provides:

- one `GeneOntology` object shared by the old and new snapshots;
- canonical resolution of primary IDs, alternate IDs, and unique active
  `replaced_by` targets;
- auditable exclusion of unknown, unresolved obsolete, deprecated, `NOT`, and
  true-path-violating assertions;
- canonical direct states keyed by `(sequence_id, aspect, canonical_term_id)`;
- complete evidence-code sets and cumulative evidence profiles;
- policy-specific canonical events;
- inclusive propagation through `is_a` and `part_of`;
- canonical BP, MF, and CC roots and ontology namespaces.

The following list was the implementation gap at design time and is retained as
historical rationale. The ontology/preflight and truth/mask items are now
implemented; information, prediction, metrics, and export remain future work:

- a lossless representation of the complete OWL graph beside the GO scoring
  projection;
- an IRI-based inventory and policy for every observed object property;
- deterministic ontology preflight and aspect-root reachability validation;
- an explicit ontology context carried from comparison into mask construction;
- closure provenance from every propagated term to every source direct term;
- the aspect-specific universe `Q`;
- an evidence policy for prior-accessible knowledge independent of the event
  scoring policy;
- `D_score`, `D_neutral1`, `K0`, `X1`, `M`, and final truth `T`;
- explicit target-aspect exclusion when `T` is empty;
- typed, deterministic audit records for every inclusion and exclusion.

Two existing APIs must not be reused with the wrong meaning:

1. `determine_prior_knowledge()` describes prior state relative to the active
   event evidence profile. It cannot construct `K0`, because an IEA-only term is
   deliberately absent from strict experimental prior state but must normally
   be present in the prior-known mask.
2. `GeneOntology.propagate()` returns the correct inclusive set, but it does not
   retain which direct term generated each propagated term.

`ComparisonResult` does not currently retain a stable ontology fingerprint.
Although `compare_annotations()` presently checks Python object identity, that
check is only an optional same-process guard and is not reproducible across two
loads of identical bytes. The implementation should add the stable fingerprint
needed by later layers rather than require the same in-memory object.

## 3. Official ontology source and present inspection status

The required provenance source is:

```text
http://purl.obolibrary.org/obo/go.owl
```

This PURL is mobile and is not itself a reproducible pin. Milestone 2A must
receive a local file plus `expected_sha256`, verify the checksum before parsing,
and leave the file unchanged for the entire run. The implementation must not
silently substitute `go-basic.owl`, `go-plus.owl`, a different date, or a
separately downloaded import. Multiple projections of one
loaded graph remain projections of the same ontology; they are not independent
ontologies and must carry the same ontology identity.

As of 2026-09-19, an ignored local `data/go.owl` artifact is available for
engineering tests and ontology preflight. It is the selected master ontology;
`go-plus.owl` is explicitly outside the navigation and scoring workflow. Its
deterministic report is recorded in `docs/ONTOLOGY_PREFLIGHT_PROVISIONAL.md`.
Acceptance requires pinning the exact `go.owl` bytes and human review of its
report.

When the file is supplied, the preflight must perform these checks without
network access:

1. read the exact local bytes and calculate SHA-256 before parsing;
2. parse the file once into an RDF graph;
3. identify the `owl:Ontology` node, ontology IRI, `owl:versionIRI`, and declared
   `owl:imports`;
4. obtain the release date from explicit ontology metadata or a documented
   version-IRI parser; never infer it from GOA0 or GOA1;
5. record loader configuration, parser and RDFLib versions, import policy,
   relation-policy version, and axiom mode;
6. enumerate all class and object-property IRIs before constructing projections;
7. inventory all extracted edges, including unknown relations and external
   endpoints;
8. run property, cycle, aspect, and root-reachability validation;
9. refuse benchmark construction if the checksum differs, required fingerprint
   fields are absent, or the
   propagation graph has fatal validation issues.

The ontology identity must include:

- source URL and local source name;
- ontology IRI;
- version IRI;
- declared release date;
- retrieval date;
- SHA-256 of the exact OWL file;
- declared import IRIs and their local-definition status;
- loader configuration and software versions;
- axiom mode, without claiming that RDFLib can determine whether asserted
  triples were produced by an earlier materialization process;
- relation-policy version.

The first preflight import policy is `DECLARATION_ONLY_NO_NETWORK`:

- record every `owl:imports` IRI;
- do not download imports automatically;
- preserve all axioms actually present in the supplied file;
- report external classes or properties that are not defined locally;
- do not claim that the graph equals the complete import closure;
- make a missing import fatal only when the missing definition is necessary to
  interpret the scoring hierarchy or another mandatory validation.

`PINNED_LOCAL_IMPORT_CLOSURE` remains a future policy requiring separate local
artifacts and checksums. The initial milestone uses asserted triples and only
the explicitly supported structural reconstruction described below. It performs
no implicit downloads and introduces no reasoner. No official release is
approved until this preflight has run on the real pinned file.

## 4. Complete graph and derived projections

### 4.1 Complete ontology graph

The complete graph is the lossless RDF/OWL representation of the supplied
`go.owl` bytes. It retains:

- every class, including external imported classes;
- every object property and its IRI;
- every RDF edge and relevant anonymous expression;
- `rdfs:subClassOf`, `owl:equivalentClass`, restrictions, intersections, and
  other OWL axioms even when ProBE cannot interpret them for propagation;
- relations from GO classes to external chemical, anatomical, taxonomic, or
  other ontology classes;
- unknown properties and unknown axiom shapes;
- ontology and import declarations.

Preservation does not mean scientific approval. Unsupported expressions remain
queryable and countable and produce preflight audit entries. They do not enter a
scoring projection by default.

The current `GeneOntology` is not a complete ontology graph. It stores only GO
classes and extracted GO-to-GO edges. The proposed loader should therefore
return an `OntologyContext` containing both the complete RDF graph and the one
`GeneOntology` instance used by snapshots, events, masks, and later evaluation.
The existing `OwlLoader.load()` can remain as a compatibility wrapper returning
that context's `GeneOntology` projection.

### 4.2 Annotation propagation graph

The annotation propagation graph is the conservative child-to-parent
projection used by:

- direct-event branch/refinement classification;
- inclusive annotation closure;
- K0 and X1;
- propagated candidate and final truth;
- future prediction-score propagation and metrics.

Its initial exact IRI allow-list contains only:

- `rdfs:subClassOf`, represented operationally as `is_a`;
- `BFO:0000050`, represented operationally as `part_of`.

An edge enters this graph only when both endpoints are active GO terms in the
same aspect and its relation IRI is explicitly approved. `has_part`, regulatory,
causal, temporal, input/output, contextual, cross-aspect, and external-class
edges are excluded. Unknown relations are excluded. No fallback relation is
used to connect a term to a root.

### 4.3 Semantic navigation graph

The semantic navigation graph supports explicit exploration, audit, and future
visualization. It may project any retained edge, including non-propagating and
external edges. Every traversal request must declare:

- the exact relation IRIs or an explicitly named relation category;
- direction: asserted, inverse, child-to-parent, or parent-to-child;
- whether cross-aspect edges are allowed;
- whether external classes are allowed;
- stopping conditions and maximum traversal policy.

The API must not use `parent` or `ancestor` for arbitrary navigation. Those
words remain reserved for the annotation propagation graph. A relation being
navigable does not make it annotation-propagating.

## 5. Relation inventory and policy

### 5.1 Property policy and per-edge disposition

A relation IRI does not receive one universal category for all of its edges.
`RelationDescriptor` describes the property. `RelationPolicy` declares its
potentially permitted uses. `EdgeDisposition` records the effective decision for
one extracted edge after inspecting its endpoints and axiom provenance.

The allowed edge dispositions are:

- `PROPAGATING`;
- `NAVIGABLE_ONLY`;
- `EXTERNAL_CONTEXT`;
- `CROSS_ASPECT`;
- `UNKNOWN_OR_UNSUPPORTED`.

The decision function depends at least on:

- canonical relation IRI;
- source and target class kinds;
- source and target aspects when they are GO terms;
- axiom origin;
- relation-policy version and fingerprint.

Only exact `is_a` or `part_of` edges between active GO terms in the same aspect
can receive `PROPAGATING` in the initial policy. For example:

- `part_of` from active BP to active BP may be `PROPAGATING`;
- `part_of` from GO to an external class is `EXTERNAL_CONTEXT`;
- `part_of` between different GO aspects is `CROSS_ASPECT`;
- an unsupported axiom shape using `part_of` is
  `UNKNOWN_OR_UNSUPPORTED` until an extractor is approved.

The following properties have no initially permitted propagating use unless a
later scientific decision changes an exact IRI rule:

- `has_part`;
- `regulates`, `positively_regulates`, and `negatively_regulates`;
- `occurs_in`;
- causal and temporal relations;
- input/output relations;
- relations to chemical, anatomical, taxonomic, or other external entities;
- every unapproved or unknown relation.

Unknown relations are retained under their canonical IRI, included in counts
and examples, and assigned `UNKNOWN_OR_UNSUPPORTED` per edge. An explicit
navigation request may inspect them, but they are excluded from propagation and
must not crash the loader.

### 5.2 RelationDescriptor inventory

For each relation IRI observed in an edge or declared as an object property,
preflight records, when present:

- canonical IRI and compact RO/BFO/OBO identifier;
- labels without using them as identity;
- direct and transitive `rdfs:subPropertyOf` IRIs;
- inverse-property IRI;
- asserted transitivity and symmetry;
- domain and range expressions;
- potentially permitted uses under the active `RelationPolicy`;
- total extracted edge count;
- GO-to-GO and GO-to-external edge counts;
- source/target aspect pairs;
- observed asserted direction;
- counts by `EdgeDisposition`;
- membership of its edges in each explicitly analyzed class-edge projection;
- deterministic representative examples.

Counts distinguish declarations from extracted class edges. Property metadata
alone does not create an annotation edge.

### 5.3 Subproperty hierarchy

`rdfs:subPropertyOf` is preserved and its acyclic or cyclic closure is reported.
The default policy is **no automatic permitted-use inheritance**. In particular,
a subproperty of `part_of` does not become potentially propagation-safe, and a
transitive property does not become propagating, unless its exact IRI and edge
conditions are approved.

A future policy may contain explicit, versioned inheritance rules such as:

```text
inherit navigation permission from exact superproperty RO:...
do not inherit propagation permission
```

Each rule must name the superproperty IRI, permitted descendant IRIs or closure
mode, inherited use, direction, endpoint restrictions, and policy version.
`positively_regulates` and `negatively_regulates` may be explicitly permitted
for navigation; their being subproperties of `regulates` is recorded but is not
itself the authorization. Effective disposition is still decided edge by edge.

## 6. Active terms and root-reachability policy

An **active GO term** is a named class that:

- has a canonical GO identifier;
- belongs to exactly one of BP, MF, or CC;
- is not obsolete or deprecated;
- resolves in the supplied official ontology;
- is not an external imported class.

For every class, preflight assigns all applicable reachability findings:

- `ROOT_TERM`;
- `VALID_IS_A_ROOT_PATH`;
- `NO_IS_A_ROOT_PATH_BUT_SAFE_PART_OF_PATH`;
- `NO_SAFE_PATH_TO_ASPECT_ROOT`;
- `CROSS_ASPECT_EDGE_IGNORED`;
- `NO_OWN_ASPECT_ROOT_AFTER_EDGE_FILTERING`;
- `EXTERNAL_CLASS`;
- `OBSOLETE_OR_DEPRECATED`.

For each active non-root term `g` in aspect `a`, calculate separately:

1. whether the own-aspect root is in the filtered intra-aspect closure using
   only `is_a`;
2. when it is not, whether an intra-aspect `part_of` path supplies a safe route;
3. which candidate `is_a`/`part_of` edges were ignored as cross-aspect;
4. whether a root is reachable only through non-propagating relations;
5. whether all observed outgoing semantic links target external classes;
6. whether the term has any direct `is_a` parent.

`VALID_IS_A_ROOT_PATH` is structurally eligible for the future Q, subject to the
remaining ontology policy. A term with no `is_a` route but a safe intra-aspect
`part_of` route receives `NO_IS_A_ROOT_PATH_BUT_SAFE_PART_OF_PATH` and is
quarantined in 2A. It is neither admitted to nor permanently excluded from Q
before the real release counts and examples are reviewed. The case may indicate
incomplete parsing, an uninterpreted equivalent-class axiom, missing reasoning,
or a real release property.

An active term without any safe own-aspect route receives
`NO_SAFE_PATH_TO_ASPECT_ROOT`. A cross-aspect candidate edge is removed from the
scoring hierarchy and reported as `CROSS_ASPECT_EDGE_IGNORED`; its existence does
not invalidate the source term when another intra-aspect `is_a` path reaches the
correct root. If filtering cross-aspect edges leaves no own-aspect route, the
term additionally receives `NO_OWN_ASPECT_ROOT_AFTER_EDGE_FILTERING`.
Non-propagating or external edges never rescue root reachability.

The three roots are traversable but are never members of Q:

- `GO:0008150` biological process;
- `GO:0003674` molecular function;
- `GO:0005575` cellular component.

## 7. OWL loader assessment and preflight algorithm

### 7.1 Current loader behavior

The current `src/probe/parsing/owl.py`:

- parses RDF/XML with RDFLib;
- reads named GO `owl:Class` nodes;
- reads labels, namespaces, `owl:deprecated`, alternate IDs,
  `IAO:0100001` replacements, and `consider` candidates;
- extracts named `rdfs:subClassOf` GO targets as `is_a`;
- extracts `owl:someValuesFrom` restrictions when their target is a GO class;
- recursively extracts supported members of `owl:intersectionOf`;
- inspects both `rdfs:subClassOf` and `owl:equivalentClass` expressions;
- retains an unknown GO-to-GO restriction property as an OBO ID or full IRI;
- records one `owl:versionIRI`.

Existing synthetic tests cover named subclass edges, a `regulates` restriction,
alternate IDs, a unique replacement, deprecation, and version IRI.

### 7.2 Current loader limits

The current loader cannot satisfy ontology preflight because it discards the RDF
graph after constructing `GeneOntology`. Specifically, it does not preserve or
report:

- the ontology IRI, imports, release-date metadata, source SHA-256, or loader
  configuration;
- external classes or any GO-to-external edge;
- complete object-property declarations, labels, subproperty hierarchy,
  inverses, transitivity, symmetry, domains, or ranges;
- unsupported anonymous class expressions or property-chain axioms;
- the original axiom shape and whether an edge came from subclass,
  equivalent-class, intersection, or restriction syntax;
- a verifiable origin for every extracted edge;
- unknown relations whose target is not a GO class;
- cycles in explicitly defined property and class-edge projections.

It interprets a named GO class in an `owl:equivalentClass` expression as a
one-directional `is_a` edge and extracts supported intersection members. This is
a useful structural approximation, but it loses equivalence direction and axiom
provenance. It supports only `someValuesFrom` restrictions; other restriction
types and general OWL expressions are retained nowhere. RDFLib parsing does not
by itself supply OWL reasoning or guarantee that declared imports are fetched.

The implementation specification distinguishes:

| Availability | Examples | Initial treatment |
| --- | --- | --- |
| Directly present in the file | named `subClassOf`, explicit restrictions, property declarations, `subPropertyOf`, imports, deprecation and ID metadata | Preserve exactly with source-axiom provenance; do not infer how the producing tool created the triple. |
| Reconstructible without a reasoner | supported `intersectionOf` members, compact identifiers, explicit property-hierarchy closure, graph reachability and cycles | Derive deterministically and label as reconstructed, never inferred. |
| Requires reasoning for general validity | arbitrary equivalent-class consequences, property chains, inferred subclass hierarchy, complex class expressions, imported axioms absent from the file | Retain the source axioms and report the gap; do not add the inferred edge in this milestone. |

### 7.3 Axiom modes and reasoning boundary

Each extracted edge must carry an `AxiomOrigin`, at minimum:

- `ASSERTED_NAMED_SUBCLASS`;
- `ASSERTED_RESTRICTION`;
- `ASSERTED_EQUIVALENT_CLASS`;
- `RECONSTRUCTED_INTERSECTION_MEMBER`;
- `INFERRED_BY_PINNED_REASONER`, reserved for future use.

The first implementation uses asserted and locally reconstructible expressions
only. It must not label reconstructed intersection edges as reasoner output.
RDFLib cannot determine whether a triple present in the file was originally
asserted by an author or materialized by an upstream tool, so
`MATERIALIZED_IN_SOURCE` is not assigned to individual edges.

A reasoner is not introduced in this milestone. Future reasoning would require
pinning the reasoner, version, import closure, profile, configuration, generated
axiom checksum, and failure policy. Different reasoners or versions may differ
in supported OWL profiles, import handling, classification, equivalence
materialization, property chains, and resource limits. Such output would define
a new versioned ontology projection and must be compared against asserted-only
preflight before approval.

### 7.4 Deterministic preflight algorithm

Given one locally pinned `go.owl`:

1. construct and validate `OntologyFingerprint` from bytes, ontology metadata,
   axiom mode, import policy, relation-policy fingerprint, and loader-
   configuration fingerprint;
2. retain the complete RDF graph and declared import list without implicit
   network retrieval;
3. enumerate named classes and classify GO versus external classes;
4. enumerate all object properties and build the property hierarchy;
5. extract class edges with relation IRI, endpoints, axiom origin, direction,
   and source triple/expression identity;
6. retain unsupported expressions as counted validation issues rather than
   silently dropping them;
7. build the relation inventory, apply exact-IRI `RelationPolicy` permissions,
   and assign an `EdgeDisposition` to every extracted edge;
8. derive the annotation propagation and semantic navigation projections from
   the same context;
9. classify active, obsolete/deprecated, and external classes;
10. calculate direct-parent and root-reachability findings for every active GO
    term;
11. detect cycles independently in the property `subPropertyOf` graph, each
    declared extracted GO class-edge graph, the selected semantic-navigation
    projection, and the annotation-propagation/scoring hierarchy; do not compute
    or interpret generic RDF cycles;
12. report structurally eligible, part_of-only quarantined, and structurally
    excluded terms without constructing Q;
13. sort every count, example, issue, edge disposition, and descriptor
    deterministically;
14. fail strict preflight on a scoring-hierarchy cycle, missing canonical root,
    fingerprint mismatch, or a missing import definition proven necessary for
    an obligatory scoring-hierarchy interpretation. A merely unresolved import
    declaration is reported and is not automatically fatal.

`OntologyPreflightReport` must contain at least:

- active GO-term counts per aspect;
- obsolete and deprecated term counts;
- external class count;
- every observed relation descriptor;
- edge counts per relation, intra-aspect, cross-aspect, and GO-to-external;
- active terms without a direct `is_a` parent;
- active terms without an `is_a` root path;
- active terms without a safe `is_a`/`part_of` root path;
- terms with only non-propagating edges;
- terms linked only to external classes;
- property-hierarchy cycles;
- cycles in each named extracted GO class-edge relation set;
- cycles in the selected navigation projection;
- cycles in the annotation-propagation/scoring hierarchy;
- unknown relations and unsupported axiom shapes;
- bounded, deterministic representative examples for every anomaly class;
- fatal, warning, and informational validation issues.

## 8. Mathematical model

### 8.1 Indices and ontology

Let:

- `p` be an exact sequence identity;
- `a` be one GO aspect in `{MF, BP, CC}`;
- `O` be one explicitly supplied, immutable, official `GeneOntology` instance;
- `V_a(O)` be the active canonical GO terms in `O` whose namespace corresponds
  to `a`;
- `r_a` be the canonical root for `a`;
- `R = {is_a, part_of}` be the only propagation relations in this milestone.

The aspect mapping is fixed:

| Public aspect | Current GAF code | Ontology namespace | Root |
| --- | --- | --- | --- |
| MF | `F` | `molecular_function` | `GO:0003674` |
| BP | `P` | `biological_process` | `GO:0008150` |
| CC | `C` | `cellular_component` | `GO:0005575` |

The date of `O` is not inferred from `t0` or `t1`. The caller supplies `O` and
its identity explicitly. Choosing a prediction-cutoff ontology or another
declared ontology date remains a benchmark-profile decision.

### 8.2 Canonicalization

Define `canon_O(g)` as:

- the active primary ID when `g` is active;
- the active primary ID for an alternate ID;
- the unique active replacement when the current replacement policy permits it;
- undefined for unknown, ambiguous, or otherwise unusable terms.

Canonicalization occurs before every set operation. Undefined terms are never
added to `O`, `Q`, a closure, or a mask. They produce audit exclusions.

### 8.3 Inclusive closure with provenance

For a canonical term `s` in aspect `a`, define:

```text
C_a({s}) = {s} union {v in V_a(O) : s reaches v upward through zero or more
                                  edges in R and every node on the path is in V_a(O)}
```

For preflight only, define `C_R_raw({s})` over all GO endpoints before the
aspect-homogeneous projection. It detects safe-relation edges or paths reaching
a foreign-aspect root. Such paths are validation findings; they never enter
`C_a`.

For a set `S`:

```text
C_a(S) = union(C_a({s}) for s in canon_O(S))
```

The direct term is included. Roots may be reached and retained in closure
provenance, but are removed from the evaluable universe.

For every propagated term `v`, provenance is:

```text
sources_a(v, S) = {s in canon_O(S) : v in C_a({s})}
```

The implementation must therefore calculate the closure of each direct term
and aggregate the results. It must not enumerate all paths in the GO DAG. For
each reachable pair `(s, v)`, store only:

- reachability;
- minimum edge distance;
- the set of relation IRIs encountered while establishing reachability;
- one deterministic witness path: shortest first, with lexicographic tie-break
  over relation IRI and term ID.

This retains every source direct term supporting `v` without combinatorial path
expansion. Calling `GeneOntology.propagate(S)` once is insufficient for source
provenance, while storing every possible route is prohibited.

### 8.4 Global universe Q

Before future information or manual filters, define:

```text
Q_base(a) = {g in V_a(O) : g != r_a,
                             g is allowed by the ontology policy,
                             and root_eligibility_policy(g) = ADMITTED}
```

Milestone 2A does not evaluate this formula. It reports the inputs to
`root_eligibility_policy`:

- `VALID_IS_A_ROOT_PATH` is structurally admissible;
- `NO_IS_A_ROOT_PATH_BUT_SAFE_PART_OF_PATH` is quarantined pending review of
  real counts and examples;
- `NO_SAFE_PATH_TO_ASPECT_ROOT`,
  `NO_OWN_ASPECT_ROOT_AFTER_EDGE_FILTERING`, `ROOT_TERM`, `EXTERNAL_CLASS`, and
  `OBSOLETE_OR_DEPRECATED` are not admissible.

`CROSS_ASPECT_EDGE_IGNORED` is an edge-level anomaly, not automatic term
exclusion. A term remains structurally admissible when the filtered hierarchy
still gives it an intra-aspect `is_a` path to its own root.

After human approval of the preflight and root policy, milestone 2B defines:

```text
Q(a) = Q_base(a)
```

`Q(MF)`, `Q(BP)`, and `Q(CC)` are distinct immutable objects. Alternate IDs are
not separate members. An obsolete source ID with a permitted unique replacement
contributes only its active canonical replacement.

Future information thresholds and manual include/exclude policies may restrict
`Q_base`, but milestone 2B exposes only an extension point. It does not read
those files or calculate information values.

### 8.5 Prior-positive direct terms and K0

Let `D0_positive(p,a)` contain direct t0 terms that satisfy all of the following:

- canonical and resolvable in `O`;
- active and in the namespace for `a`;
- represented by at least one recognized usable positive evidence tier,
  including IEA;
- not supported solely by `NAS` and/or `ND`;
- not a `NOT` assertion or removed by a valid `NOT` constraint.

Then:

```text
K0(p,a) = C_a(D0_positive(p,a))
```

The K0 evidence policy is independent of the event scoring policy. The proposed
named policy is `all_usable_positive_t0`, accepting evidence tiers 1 through 6
and intrinsically rejecting `NAS` and `ND`. It must be passed explicitly to mask
construction; `experimental_strict` must never be inherited implicitly.

This proposal is consistent with the benchmark specification. Its rationale is
exposure: a predictor could have observed a positive t0 assertion even when its
evidence was IEA. K0 is therefore a knowledge-availability mask, not a claim that
all evidence tiers have equal scientific strength.

Potential counterexamples do not justify silently weakening K0:

- a t0 IEA may later be removed as erroneous;
- a positive t0 assertion may conflict with a later `NOT` assertion;
- a term may disappear because of curation or identifier migration.

In these cases, unmasking the term would make previously accessible information
look novel. The safer policy is to retain K0, record the contradiction or loss,
and let a separate future loss/contradiction policy exclude or stratify the
target-aspect.

### 8.6 Scored direct truth D_score

Let `E_profile(p,a)` be the canonical direct events created under the explicitly
selected scoring evidence policy. Define four event-selection profiles:

```text
new-knowledge         = {BRANCH_ACQUISITION}
refinement            = {SPECIFICITY_REFINEMENT}
combined              = {BRANCH_ACQUISITION, SPECIFICITY_REFINEMENT}
evidence-confirmation = {EVIDENCE_UPGRADE}
```

For profile `b`:

```text
D_score^b(p,a) = {event.term_id : event in E_profile(p,a),
                                  event.type in selected_types(b),
                                  event is otherwise valid}
```

The main knowledge-gain run must use one of `new-knowledge`, `refinement`, or
`combined`. `EVIDENCE_UPGRADE` is not silently mixed into these profiles.
`evidence-confirmation` is a separate analysis with a separate profile name and
mask semantics.

The propagated candidate truth remains distinct:

```text
G_candidate^b(p,a) = C_a(D_score^b(p,a))
```

The event-selection profile and the scoring evidence profile are independent
configuration axes. For example, `combined` may be constructed under
`experimental_strict` or `experimental_all`. A `ComparisonResult` must declare
the evidence policy used to create its events, and truth construction must reject
an incompatible requested evidence policy rather than reinterpret existing
events.

### 8.7 The IEA-to-IDA conflict in evidence confirmation

For the main knowledge-gain profile, an IEA term at t0 correctly belongs to K0.
If the same term becomes IDA at t1, it is an `EVIDENCE_UPGRADE`, but:

```text
D_score_evidence = {g}
g in K0
T = C({g}) intersect (Q - K0 - X1) = empty
```

This is scientifically correct for de novo function prediction: the function
was already asserted. It prevents mixing confirmation with novelty.

For the separate `evidence-confirmation` profile, the proposed configurable
solution is `unmask_upgraded_direct_only`:

```text
K0_confirmation(p,a) = K0(p,a) - D_score_evidence(p,a)
```

Only the exact upgraded direct term is released from K0. Its already-known
ancestors remain masked. This measures whether a predictor assigned the upgraded
claim without awarding credit for restating its known ancestors.

Two alternatives must remain documented but are not recommended as defaults:

- subtracting `C(D_score_evidence)` from K0 would also unmask all known
  ancestors and over-credit prior knowledge;
- disabling K0 entirely would turn evidence confirmation into ordinary full
  function evaluation.

`unmask_upgraded_direct_only` changes only the named evidence-confirmation
analysis. The main benchmark always uses the full K0 defined above.

### 8.8 Neutral direct truth D_neutral1 and X1

The broad proposal can be written as:

```text
D_neutral1(p,a) = D1_positive_valid(p,a) - D_score(p,a)
X1(p,a) = C_a(D_neutral1(p,a)) - C_a(D_score(p,a))
```

The subtraction in X1 is mandatory. If a propagated ancestor is supported by a
scored direct term and a neutral direct term, it is not neutral on that basis.

Not every non-selected t1 row can enter `D_neutral1`. The positive-valid set
must exclude:

- `NOT` assertions and positive assertions removed by a valid `NOT` constraint;
- `NAS`-only and `ND`-only assertions;
- unknown, unresolved obsolete, ambiguous replacement, and deprecated terms;
- terms whose ontology namespace disagrees with the stated aspect;
- non-GO or ontology-policy-disallowed terms;
- assertions quarantined for an unresolved contradiction.

Roots need not be removed from the closure, but they are outside `Q` and can
never be evaluated.

Profile-accepted t1 terms removed only by event selection belong in neutral
truth. Curated usable terms outside the scoring evidence profile may also enter
neutral truth under a broader explicit positive-truth policy. Terms later
removed by an information threshold should enter neutral truth unless an
explicit manual override says otherwise; those filters are future work.

New t1 IEA is the material ambiguity. The authoritative specification warns
that automatically neutralizing every new IEA can favor predictors that mirror
the same electronic pipelines and can hide genuine false positives. Therefore
the implementation should provide:

```text
NeutralTruthPolicy.include_new_iea: bool
```

Recommended named policies are:

- `primary_non_electronic_neutral`: usable t1 truth except IEA-only terms;
- `all_usable_neutral`: includes new IEA and serves as a declared sensitivity
  profile.

This plan does not select between them. The choice requires scientific approval
before a headline benchmark profile is fixed.

### 8.9 Final mask and evaluable truth

For a knowledge-gain profile:

```text
M(p,a) = Q(a) - K0(p,a) - X1(p,a)
T(p,a) = G_candidate(p,a) intersection M(p,a)
```

The objects remain separate:

- `D_score`: selected direct event terms;
- `G_candidate`: their inclusive propagated closure;
- `K0`: all prior-accessible positive knowledge;
- `D_neutral1`: later positive direct truth intentionally not scored;
- `X1`: branch-exclusive neutral closure;
- `Q`: global aspect universe;
- `M`: per-target evaluable universe;
- `T`: final propagated evaluable truth.

Set subtraction is mathematically commutative, but audit reasons have this
precedence:

1. `OUTSIDE_Q` or `ROOT` means the term is globally non-evaluable;
2. `PRIOR_KNOWN` is the primary per-target reason whenever the term is in K0;
3. `NEUTRAL_T1` applies only to terms in X1 that are not already in K0.

If `T(p,a)` is empty, the result status is
`EXCLUDED_EMPTY_EVALUABLE_TRUTH`. The target-aspect is not passed to later metric
code as an empty truth case. Its audit record must distinguish at least:

- no direct event selected;
- all candidate truth masked by K0;
- all remaining candidate truth neutralized by X1;
- all candidate truth outside Q, including roots.

## 9. Confirmed decisions

The following decisions are already consistent across the user proposal,
project instructions, and benchmark specification:

1. `O` is one explicitly supplied, pinned release of official `go.owl`; it
   is not inferred from either annotation date and is never silently replaced by
   `go-basic.owl` or another ontology.
2. The complete graph and every projection derive from an ontology context with
   the same stable `OntologyFingerprint`.
3. Old and new snapshots, event classification, closure, masks, prediction
   evaluation, and metrics require compatible fingerprints. Separate loads of
   identical bytes and canonical configuration are compatible; Python object
   identity is only an optional internal assertion.
4. Complete graph preservation, semantic navigability, and annotation
   propagation are separate concepts and APIs.
5. Unknown relations and external edges are retained and audited, while only
   exact approved `is_a` and `part_of` IRIs propagate annotations.
6. Unknown or unresolved terms are audited and excluded, never inserted into
   the graph.
7. Alternate IDs and permitted unique replacements are canonicalized before set
   operations.
8. Closure is inclusive and uses only `is_a` and `part_of`.
9. Closure retains every source direct term plus bounded provenance per
   source/propagated pair: minimum distance, encountered relations, and one
   deterministic witness path. It never enumerates all DAG paths.
10. MF, BP, and CC have separate universes and target-aspect results.
11. Roots may be traversed but do not belong to Q or final truth.
12. A non-root term without a safe path to its own aspect root is excluded from
    Q; non-propagating relations are never a fallback.
13. K0 and the event scoring evidence policy are independent.
14. NAS, ND, and NOT do not establish positive K0 or neutral truth.
15. Branch acquisition, refinement, combined knowledge gain, and evidence
    confirmation are separate named event profiles.
16. `X1 = C(D_neutral1) - C(D_score)`.
17. `M = Q - K0 - X1`, with prior-known audit precedence.
18. Empty final truth excludes the target-aspect with an explicit reason.
19. Prediction-score propagation must precede application of M.
20. Parsing, complete ontology representation, relation policy, preflight,
    annotation closure, comparison, events, truth selection, masking,
    predictions, and metrics remain separate layers.

## 10. Ambiguities and scientific counterexamples

| Question | Counterexample or risk | Planned treatment |
| --- | --- | --- |
| Should every new t1 IEA be neutral? | It may encode true knowledge, but automatic neutralization can favor methods reproducing the same electronic pipeline and hide false positives. | Named configurable neutral policies; no hidden default. |
| Should every t0 positive assertion remain in K0 after later removal? | Removal may correct an error or reflect migration. Unmasking would nevertheless treat publicly accessible prior information as novel. | Keep K0; audit the loss and let a future contradiction policy exclude or stratify the target-aspect. |
| Can NAS or ND be neutral truth? | Neither supplies a usable positive evidence tier. | Exclude with an audit reason even though the GAF row is positive in form. |
| Can an unresolved or cross-aspect t1 term be neutral? | It cannot be interpreted in O or in the target aspect. | Exclude from all sets and audit. |
| Should all ancestors of an upgraded IEA term be unmasked for confirmation? | That rewards a method for predicting already-known generic ancestors. | Proposed confirmation mode unmasks only the upgraded direct term. |
| Can a root be selected directly? | It is resolvable but uninformative and can inflate scores. | Preserve its input and closure audit, exclude it from Q, and exclude the target-aspect if no other truth remains. |
| Can an unselected event be neutral even when it is valid? | In a branch-only run, a valid refinement is true but deliberately outside D_score. | Include it in D_neutral1 under the selected neutral policy. |
| What if one term has both IEA and a usable curated code? | Treating the row as IEA-only would discard stronger evidence. | Use the complete evidence set; `include_new_iea=False` excludes only states whose usable support is exclusively electronic. |
| Is a transitive or root-directed relation safe to propagate? | Transitivity and graph direction do not preserve the gene-product annotation meaning. | Exact-IRI policy only; no classification from topology, label, or transitivity. |
| Should subproperties inherit their superproperty category? | Automatic inheritance could make an unreviewed subproperty propagate. | Default to no inheritance; allow only explicit versioned rules. |
| Is a term with no `is_a` path invalid when `part_of` reaches its root? | This may be legitimate modeling or may expose missing inferred subclass axioms. | Report `NO_IS_A_ROOT_PATH_BUT_SAFE_PART_OF_PATH` and quarantine it in 2A; decide Q admission only after real counts and examples. |
| Should OWL imports be downloaded automatically? | Mutable network imports break reproducibility and can change the graph. | Use `DECLARATION_ONLY_NO_NETWORK` in 2A; keep a pinned local import closure as a future policy. |
| Should a reasoner repair missing root paths? | Reasoners and OWL profiles can produce different inferred edges and resource costs. | No reasoner in this milestone; audit missing paths and approve a pinned reasoning profile separately. |

## 11. Proposed immutable data structures

All records should be frozen dataclasses with slots. Set-valued fields should be
`frozenset`; ordered collections should be deterministically sorted tuples.

```python
class GOAspect(StrEnum):
    MF = "F"
    BP = "P"
    CC = "C"

class SnapshotOrigin(StrEnum):
    T0 = "t0"
    T1 = "t1"

class EdgeDispositionKind(StrEnum):
    PROPAGATING = "propagating"
    NAVIGABLE_ONLY = "navigable_only"
    EXTERNAL_CONTEXT = "external_context"
    CROSS_ASPECT = "cross_aspect"
    UNKNOWN_OR_UNSUPPORTED = "unknown_or_unsupported"

class AxiomOrigin(StrEnum):
    ASSERTED_NAMED_SUBCLASS = "asserted_named_subclass"
    ASSERTED_RESTRICTION = "asserted_restriction"
    ASSERTED_EQUIVALENT_CLASS = "asserted_equivalent_class"
    RECONSTRUCTED_INTERSECTION_MEMBER = "reconstructed_intersection_member"
    INFERRED_BY_PINNED_REASONER = "inferred_by_pinned_reasoner"  # future mode

@dataclass(frozen=True, slots=True)
class PolicyIdentity:
    name: str
    version: str
    canonical_configuration: str
    fingerprint: str

@dataclass(frozen=True, slots=True)
class OntologyFingerprint:
    value: str
    source_sha256: str
    ontology_iri: str
    version_iri: str
    parser_name: str
    parser_version: str
    parser_configuration_fingerprint: str
    import_closure_fingerprint: str
    axiom_mode: str

@dataclass(frozen=True, slots=True)
class OntologyMetadata:
    source_url: str
    local_source_path: str
    retrieval_date: str
    release_date: str
    declared_imports: tuple[str, ...]
    rdflib_version: str
    fingerprint: OntologyFingerprint
    import_policy: PolicyIdentity

@dataclass(frozen=True, slots=True)
class OntologyContext:
    ontology: GeneOntology
    complete_graph: CompleteOntologyGraph
    metadata: OntologyMetadata
    fingerprint: OntologyFingerprint
    relation_policy: RelationPolicy

@dataclass(frozen=True, slots=True)
class RelationDescriptor:
    iri: str
    compact_id: str | None
    labels: tuple[str, ...]
    direct_superproperties: tuple[str, ...]
    all_superproperties: tuple[str, ...]
    inverse_iri: str | None
    transitive: bool
    symmetric: bool
    domains: tuple[str, ...]
    ranges: tuple[str, ...]
    total_edge_count: int
    go_to_go_edge_count: int
    go_to_external_edge_count: int
    aspect_pairs: tuple[tuple[str, str], ...]
    observed_directions: tuple[str, ...]
    examples: tuple[RelationExample, ...]

@dataclass(frozen=True, slots=True)
class RelationPolicy:
    identity: PolicyIdentity
    permitted_uses: tuple[RelationPermission, ...]
    subproperty_inheritance_rules: tuple[SubpropertyInheritanceRule, ...]
    cross_aspect_rule: str
    external_endpoint_rule: str

@dataclass(frozen=True, slots=True)
class ExtractedOntologyEdge:
    edge_id: str
    child_iri: str
    parent_iri: str
    child_kind: str
    parent_kind: str
    child_aspect: GOAspect | None
    parent_aspect: GOAspect | None
    relation_iri: str
    axiom_origin: AxiomOrigin
    disposition: EdgeDispositionKind
    disposition_reason: str
    relation_policy_fingerprint: str

@dataclass(frozen=True, slots=True)
class OntologyValidationIssue:
    severity: Severity
    code: str
    message: str
    subject_iri: str | None
    relation_iri: str | None
    examples: tuple[str, ...]

@dataclass(frozen=True, slots=True)
class OntologyPreflightReport:
    report_id: str
    ontology_fingerprint: str
    import_policy_fingerprint: str
    relation_policy_fingerprint: str
    active_terms_by_aspect: tuple[tuple[GOAspect, int], ...]
    obsolete_count: int
    deprecated_count: int
    external_class_count: int
    relations: tuple[RelationDescriptor, ...]
    edge_disposition_counts: tuple[tuple[EdgeDispositionKind, int], ...]
    reachability: tuple[TermReachability, ...]
    subproperty_cycles: tuple[CycleRecord, ...]
    extracted_relation_cycles: tuple[RelationCycleRecord, ...]
    semantic_navigation_cycles: tuple[CycleRecord, ...]
    scoring_hierarchy_cycles: tuple[CycleRecord, ...]
    unknown_relations: tuple[str, ...]
    unsupported_axiom_counts: tuple[tuple[str, int], ...]
    issues: tuple[OntologyValidationIssue, ...]

@dataclass(frozen=True, slots=True)
class PositiveAssertionPolicy:
    identity: PolicyIdentity
    accepted_tiers: frozenset[EvidenceTier]
    excluded_codes: frozenset[str]
    include_iea_only: bool

@dataclass(frozen=True, slots=True)
class TruthSelectionProfile:
    identity: PolicyIdentity
    event_types: frozenset[EventType]
    evidence_policy_fingerprint: str
    confirmation_mask_policy_fingerprint: str

@dataclass(frozen=True, slots=True)
class EvaluationUniversePolicy:
    identity: PolicyIdentity
    admitted_root_statuses: frozenset[str]
    exclude_aspect_roots: bool

@dataclass(frozen=True, slots=True)
class NeutralTruthPolicy:
    identity: PolicyIdentity
    include_iea_only: bool
    include_non_scored_curated: bool

@dataclass(frozen=True, slots=True)
class ConfirmationMaskPolicy:
    identity: PolicyIdentity
    mode: str

@dataclass(frozen=True, slots=True)
class DirectTruthTerm:
    sequence_id: str
    aspect: GOAspect
    term_id: str
    origin: SnapshotOrigin
    evidence_codes: frozenset[str]
    selecting_event: EventType | None
    source_assertions: tuple[AnnotationRecord, ...]

@dataclass(frozen=True, slots=True)
class ClosureEntry:
    sequence_id: str
    aspect: GOAspect
    direct_term_id: str
    propagated_term_id: str
    origin: SnapshotOrigin
    selecting_event: EventType | None
    evidence_codes: frozenset[str]
    minimum_distance: int
    relation_iris: frozenset[str]
    witness_path: tuple[RelationStep, ...]
    ontology_fingerprint: str
    propagation_policy_fingerprint: str

@dataclass(frozen=True, slots=True)
class ClosureResult:
    closure_id: str
    terms: frozenset[str]
    entries: tuple[ClosureEntry, ...]
    sources_by_term: tuple[tuple[str, tuple[str, ...]], ...]
    ontology_fingerprint: str
    propagation_policy_fingerprint: str

@dataclass(frozen=True, slots=True)
class DirectTruthSet:
    sequence_id: str
    aspect: GOAspect
    truth_selection_policy_fingerprint: str
    terms: frozenset[str]
    records: tuple[DirectTruthTerm, ...]

@dataclass(frozen=True, slots=True)
class PropagatedTruthSet:
    sequence_id: str
    aspect: GOAspect
    direct_terms: frozenset[str]
    terms: frozenset[str]
    closure_entries: tuple[ClosureEntry, ...]
    ontology_fingerprint: str
    propagation_policy_fingerprint: str

@dataclass(frozen=True, slots=True)
class EvaluationUniverse:
    universe_id: str
    aspect: GOAspect
    terms: frozenset[str]
    root_term_id: str
    excluded_terms: tuple[OntologyValidationIssue, ...]
    ontology_fingerprint: str
    universe_policy_fingerprint: str
    preflight_report_id: str

@dataclass(frozen=True, slots=True)
class PriorKnownMask:
    sequence_id: str
    aspect: GOAspect
    direct_terms: frozenset[str]
    terms: frozenset[str]
    closure_entries: tuple[ClosureEntry, ...]
    prior_policy_fingerprint: str
    ontology_fingerprint: str

@dataclass(frozen=True, slots=True)
class NeutralTruthMask:
    sequence_id: str
    aspect: GOAspect
    direct_terms: frozenset[str]
    raw_closure: frozenset[str]
    terms: frozenset[str]
    closure_entries: tuple[ClosureEntry, ...]
    neutral_policy_fingerprint: str
    ontology_fingerprint: str

class MaskReason(StrEnum):
    ROOT = "root"
    OUTSIDE_Q = "outside_q"
    PRIOR_KNOWN = "prior_known"
    NEUTRAL_T1 = "neutral_t1"
    INVALID_OR_UNMAPPABLE = "invalid_or_unmappable"
    CROSS_ASPECT = "cross_aspect"

@dataclass(frozen=True, slots=True)
class MaskDecision:
    sequence_id: str
    aspect: GOAspect
    direct_term_id: str | None
    propagated_term_id: str
    primary_reason: MaskReason
    all_reasons: frozenset[MaskReason]
    source_direct_terms: tuple[str, ...]
    origin: SnapshotOrigin | None
    event_type: EventType | None
    evidence_codes: frozenset[str]
    closure_entry_ids: tuple[str, ...]
    inclusion: bool
    inclusion_or_exclusion_reason: str
    ontology_fingerprint: str
    policy_fingerprints: tuple[str, ...]

class TargetAspectStatus(StrEnum):
    EVALUABLE = "evaluable"
    EXCLUDED_NO_SCORED_DIRECT_TRUTH = "excluded_no_scored_direct_truth"
    EXCLUDED_EMPTY_EVALUABLE_TRUTH = "excluded_empty_evaluable_truth"

@dataclass(frozen=True, slots=True)
class TargetAspectTruth:
    sequence_id: str
    target_ids: tuple[str, ...]
    aspect: GOAspect
    truth_selection_policy_fingerprint: str
    d_score: DirectTruthSet
    g_candidate: PropagatedTruthSet
    d_neutral1: DirectTruthSet
    k0: PriorKnownMask
    x1: NeutralTruthMask
    evaluation_universe_id: str
    m: frozenset[str]
    truth: frozenset[str]
    decisions: tuple[MaskDecision, ...]
    audit: tuple[TruthAuditRecord, ...]
    preflight_report_id: str
    ontology_fingerprint: str
    run_context_id: str
    status: TargetAspectStatus
```

`TruthAuditRecord` should contain at least `sequence_id`, `aspect`, original and
canonical term IDs, origin, evidence set, source direct terms, selecting event,
inclusion/exclusion decision, reason, the ontology fingerprint, and the relevant
policy fingerprints. It should reference existing `AnnotationExclusion` data
rather than duplicate or discard it.

`CompleteOntologyGraph`, `RelationExample`, `SubpropertyInheritanceRule`,
`TermReachability`, `CycleRecord`, `RelationCycleRecord`, and `RelationStep` are
small typed records or read-only graph views, not persistence formats. A
`RelationStep` stores source, relation IRI, target, direction, and axiom origin.
Each direct-to-propagated pair has one deterministic shortest witness, chosen
lexicographically when shortest paths tie. The implementation must not enumerate
all paths.

`OntologyContext` is frozen, but its graph fields are references. The runtime
contract is that a pinned context is not mutated. Scientific compatibility is
based on equal stable `OntologyFingerprint` values plus the relevant policy
fingerprints. Two separately loaded `GeneOntology` objects with equal
fingerprints are compatible. Python object identity may be used only as an
optional same-process optimization.

The full `OntologyMetadata` and `OntologyPreflightReport` are shared run-level
objects. Closures, masks, decisions, and per-target results carry only stable IDs
and fingerprints. `EvaluationUniverse` is shared per aspect and policy; target
results reference its `universe_id` rather than embedding a copy.

## 12. Proposed public function signatures

The notebook-facing API should remain functional and composable:

```python
def load_ontology_context(
    source: Path,
    *,
    expected_sha256: str,
    source_purl: str,
    retrieval_date: str,
    loader_config: OwlLoaderConfig,
    import_policy: ImportPolicy,
    relation_policy: RelationPolicy,
) -> OntologyContext: ...

def inventory_relations(
    ontology: OntologyContext,
) -> tuple[RelationDescriptor, ...]: ...

def classify_edge(
    edge: ExtractedOntologyEdge,
    *,
    ontology: OntologyContext,
    relation_policy: RelationPolicy,
) -> EdgeDisposition: ...

def preflight_ontology(
    ontology: OntologyContext,
    *,
    strict: bool = True,
) -> OntologyPreflightReport: ...

def navigate_semantics(
    ontology: OntologyContext,
    start_iris: Iterable[str],
    *,
    relation_iris: frozenset[str],
    direction: TraversalDirection,
    allow_cross_aspect: bool,
    allow_external_classes: bool,
    stop: TraversalStopPolicy,
) -> SemanticTraversalResult: ...

def build_evaluation_universe(
    ontology: OntologyContext,
    *,
    aspect: GOAspect,
    approved_preflight: OntologyPreflightReport,
    policy: OntologyUniversePolicy,
) -> EvaluationUniverse: ...

def inclusive_ancestor_closure(
    direct_terms: Iterable[DirectTruthTerm],
    *,
    ontology: OntologyContext,
    propagation_policy: RelationPolicy,
) -> ClosureResult: ...

def select_scored_direct_truth(
    comparison: ComparisonResult,
    *,
    ontology: OntologyContext,
    profile: TruthSelectionProfile,
) -> DirectTruthSet: ...

def build_prior_known_mask(
    old_assertions: Iterable[DirectTermState],
    *,
    ontology: OntologyContext,
    policy: PositiveAssertionPolicy,
) -> PriorKnownMask: ...

def select_neutral_direct_truth(
    new_assertions: Iterable[DirectTermState],
    scored_direct_truth: Iterable[DirectTruthTerm],
    *,
    ontology: OntologyContext,
    policy: NeutralTruthPolicy,
) -> DirectTruthSet: ...

def construct_truth_and_masks(
    comparison: ComparisonResult,
    *,
    ontology: OntologyContext,
    truth_profile: TruthSelectionProfile,
    prior_policy: PositiveAssertionPolicy,
    neutral_policy: NeutralTruthPolicy,
    confirmation_mask_policy: ConfirmationMaskPolicy,
    approved_preflight: OntologyPreflightReport,
    universes: Mapping[GOAspect, EvaluationUniverse],
) -> tuple[TargetAspectTruth, ...]: ...
```

The high-level function composes the pure lower-level functions; it is not a
stateful pipeline object. It must require all scientific policies explicitly or
use named constructors whose full policy identities appear in run-level
metadata. A readable name alone is insufficient: version, canonical
configuration, and deterministic fingerprint are required.

`ComparisonResult` should gain an additive `ontology_fingerprint: str | None`
field, populated by `compare_annotations()`. The mask builder rejects a missing
or unequal fingerprint with a structured validation error. The default keeps
manual construction of the existing result source-compatible, while truth
construction requires the populated field. Separate loads with the same stable
fingerprint are compatible; Python object identity is not a scientific
requirement.

## 13. Algorithm order and invariants

### 13.1 Milestone 2A algorithm

1. Accept a local OWL path and an independently supplied expected SHA-256.
2. Verify the bytes before parsing. A mismatch is fatal; no fallback download or
   silent replacement is permitted.
3. Parse with `DECLARATION_ONLY_NO_NETWORK`, read ontology/version IRIs, record
   the PURL as provenance, and construct the stable ontology fingerprint.
4. Inventory classes, object properties, axioms, imports, and extracted edges.
5. Build property descriptors without assigning them one universal operational
   category.
6. Assign every extracted edge a disposition from relation IRI, endpoint kinds,
   endpoint aspects, axiom origin, and the versioned relation policy.
7. Check cycles only in the subproperty graph, per-relation extracted GO class
   graph, selected semantic navigation projection, and scoring hierarchy. A
   scoring-hierarchy cycle is fatal; a navigation-only cycle is reported.
8. Compute root-reachability statuses after ignoring cross-aspect and external
   edges. Quarantine `NO_IS_A_ROOT_PATH_BUT_SAFE_PART_OF_PATH`; do not construct
   Q yet.
9. Emit a deterministic report with counts and examples. Stop for human review
   of the real pinned `go.owl` report.

### 13.2 Milestone 2B algorithm

Milestone 2B starts only after approval of the 2A report and scoring projection.

1. Validate equal ontology and policy fingerprints among the comparison,
   approved preflight, universes, and run context.
2. Build shared `Q(MF)`, `Q(BP)`, and `Q(CC)` objects from the approved root
   eligibility policy; never admit quarantined terms implicitly.
3. Group canonical old assertions, new assertions, and events by sequence and
   aspect using stable sorted keys.
4. Resolve every term and validate its aspect before any set operation.
5. Select `D_score` from the event types and evidence policy in the truth
   profile.
6. Build `G_candidate`; for every direct-to-propagated pair retain reachability,
   minimum distance, relation set, and one deterministic shortest witness.
7. Select `D0_positive` using `all_usable_positive_t0` and build K0 independently
   of the scoring evidence profile.
8. Apply `unmask_upgraded_direct_only` only in the separate
   evidence-confirmation analysis.
9. Select `D_neutral1` under the named neutral policy, then calculate
   `X1 = C(D_neutral1) - G_candidate`.
10. Calculate `M = Q - K0 - X1` and `T = G_candidate intersection M`.
11. Exclude a target-aspect with empty T using an explicit status and audit
    reason.
12. Return target-aspect records sorted by sequence, aspect, and truth-policy
    fingerprint; sort all term, source, and witness tuples deterministically.

Algorithmic invariants:

- all sets contain canonical IDs only;
- every projection and result carries a compatible ontology fingerprint and the
  relevant policy fingerprints;
- unknown relations and unsupported axioms remain in the complete graph and
  preflight report but never enter propagation;
- propagation contains only exact approved `is_a` and `part_of` IRIs;
- every set is aspect-homogeneous;
- `T subseteq G_candidate subseteq C(D_score)`;
- `M subseteq Q`;
- `M` is disjoint from K0 and X1;
- `X1` is disjoint from `G_candidate`;
- roots are absent from Q, M, and T;
- K0 contains every valid prior IEA term under the main profile;
- event selection never changes K0 membership;
- multiple direct terms may generate one propagated term, and all sources are
  retained;
- closure provenance never enumerates all paths; it retains one deterministic
  shortest witness per direct-to-propagated pair;
- input order cannot change sets, records, statuses, or audit ordering.

## 14. Future prediction-order contract

Prediction code is outside this milestone. Its future public flow must obey:

```python
validated = canonicalize_predictions(raw_predictions, ontology=context)
propagated = propagate_prediction_scores(validated, ontology=context, reducer=max)
evaluated = apply_evaluation_mask(propagated, target_truth.m)
```

For a direct predicted term `g` with score `s`, every term in `C({g})` receives
at least `s`; duplicate paths and descendants combine by maximum score. Graph
traversal uses O and does not inspect M. Only the fully propagated score map is
intersected with M.

Consequently, a masked intermediate node never stops propagation to an
evaluable ancestor. Prediction validation must audit unknown, obsolete,
ambiguous replacement, cross-aspect, root, and invalid-score inputs before
propagation. No part of this interface is implemented in the second milestone.

## 15. Complete synthetic test plan

All implemented milestone tests use one hand-built acyclic ontology and no
network data. Expected sets must be written explicitly in assertions.

| ID | Test | Hand-computed expectation | Planned file |
| --- | --- | --- | --- |
| PF01 | Named `is_a` | Named subclass edge is retained with canonical `rdfs:subClassOf` identity, enters propagation, and reaches the aspect root. | `tests/test_ontology_preflight.py` |
| PF02 | `part_of` restriction | `BFO:0000050 some GO:...` is retained with restriction origin and enters safe propagation child-to-parent. | `tests/test_ontology_preflight.py` |
| PF03 | `has_part` | `BFO:0000051` is inventoried and navigable only when explicitly requested; it never enters propagation. | `tests/test_relations.py` |
| PF04 | `regulates` | `RO:0002211` is inventoried as non-propagating and does not create a root path for Q. | `tests/test_relations.py` |
| PF05 | `positively_regulates` | `RO:0002213` retains its subproperty link to `regulates` but does not inherit propagation. | `tests/test_relations.py` |
| PF06 | `negatively_regulates` | `RO:0002212` is retained and explicitly non-propagating in the same way. | `tests/test_relations.py` |
| PF07 | `occurs_in` | `BFO:0000066` remains in the complete graph and explicit navigation, outside annotation closure. | `tests/test_relations.py` |
| PF08 | Causal or temporal property | A synthetic causal/temporal IRI is inventoried without label-based classification and excluded from propagation. | `tests/test_relations.py` |
| PF09 | Cross-aspect edge with valid own path | A BP-to-MF edge receives `CROSS_ASPECT`, is ignored by scoring, and yields `CROSS_ASPECT_EDGE_IGNORED`; the BP term remains structurally eligible because its separate intra-BP `is_a` path reaches the BP root. | `tests/test_ontology_preflight.py` |
| PF10 | GO-to-external edge | The external class and edge are counted with `EXTERNAL_CONTEXT` and remain inspectable under an explicit navigation policy, while the target is absent from GO propagation. | `tests/test_ontology_preflight.py` |
| PF11 | Unknown relation | The canonical unknown IRI, edge, counts, and example survive loading; the edge receives `UNKNOWN_OR_UNSUPPORTED`; no crash or propagation occurs. | `tests/test_relations.py` |
| PF12 | RO subproperty | A subproperty declaration is retained; permitted uses do not propagate by default; an explicit synthetic inheritance rule changes only the derived edge disposition covered by that rule. | `tests/test_relations.py` |
| PF13 | Mixed parent relations | A term with `is_a`, `part_of`, `regulates`, and external parents propagates only over the first two while all four remain auditable. | `tests/test_ontology_preflight.py` |
| PF14 | Only non-propagating edges | The term is reported as having only non-propagating links and receives `NO_SAFE_PATH_TO_ASPECT_ROOT`; it is outside Q. | `tests/test_ontology_preflight.py` |
| PF15 | No `is_a`, safe `part_of` route | The term receives `NO_IS_A_ROOT_PATH_BUT_SAFE_PART_OF_PATH` and is quarantined in 2A; it is not admitted to Q without a later approved root-eligibility policy. | `tests/test_ontology_preflight.py` |
| PF16 | No safe root route | A term connected to a root only through `regulates` remains outside Q with `NO_SAFE_PATH_TO_ASPECT_ROOT`. | `tests/test_ontology_preflight.py` |
| PF17 | Root terms | Each root receives `ROOT_TERM`, can appear in closure, and is absent from Q. | `tests/test_ontology_preflight.py` |
| PF18 | Obsolete/deprecated term | It receives `OBSOLETE_OR_DEPRECATED`, is counted, and is excluded from active terms and Q. | `tests/test_ontology_preflight.py` |
| PF19 | Alternate ID | Alternate ID resolves to one active primary term before reachability, Q, or masks; both IDs are not counted as separate classes. | `tests/test_ontology_preflight.py` |
| PF20 | Navigation-only cycle | A cycle in the selected semantic navigation projection is reported but is not fatal when absent from the scoring hierarchy. No generic RDF-graph cycle test is performed. | `tests/test_ontology_preflight.py` |
| PF21 | Scoring-hierarchy cycle | An `is_a`/`part_of` cycle in the scoring hierarchy is a fatal strict-preflight issue. | `tests/test_ontology_preflight.py` |
| PF22 | Equivalent intersection | Supported members are extracted with axiom provenance; unsupported members remain counted and retained in the complete graph. | `tests/test_owl_loader.py` |
| PF23 | Import declaration | Under `DECLARATION_ONLY_NO_NETWORK`, `owl:imports` is recorded without retrieval; an unresolved import is fatal only if a scoring edge or mandatory validation depends on it. | `tests/test_owl_loader.py` |
| PF24 | Deterministic report | Reordered RDF triples produce identical sorted descriptors, counts, examples, reachability records, and issues. | `tests/test_ontology_preflight.py` |
| PF25 | Cross-aspect edge removes only route | After the cross-aspect edge is ignored, a term without an intra-aspect root path receives `NO_OWN_ASPECT_ROOT_AFTER_EDGE_FILTERING` and is quarantined or excluded as configured. | `tests/test_ontology_preflight.py` |
| TM01 | Inclusive closure | `C({child})` contains child, `is_a` ancestors, `part_of` ancestors, and root; excludes `regulates` and `has_part` targets. | `tests/test_masking.py` |
| TM02 | Aspect separation | BP closure and Q contain no MF/CC terms; analogous checks for MF and CC. | `tests/test_masking.py` |
| TM03 | Roots excluded from Q | All three roots are traversable and present in relevant closures but absent from Q, M, and T. | `tests/test_masking.py` |
| TM04 | t0 IEA in K0 | An IEA-only valid t0 term and its ancestors are in K0 under `all_usable_positive_t0`, even when event scoring is `experimental_strict`. | `tests/test_masking.py` |
| TM05 | NAS, ND, and NOT excluded from positive K0 | NAS-only, ND-only, and NOT assertions do not seed K0; exclusions retain their reasons. | `tests/test_masking.py` |
| TM06 | New t1 term in D_score | A valid branch acquisition enters `D_score` for `new-knowledge` and `combined`, then generates `G_candidate`. | `tests/test_truth.py` |
| TM07 | New t1 IEA in D_neutral1 | It enters under `all_usable_neutral` and does not enter under `primary_non_electronic_neutral`. | `tests/test_truth.py` |
| TM08 | Shared scored/neutral ancestor | If scored `s` and neutral `n` share ancestor `a`, then `a` is removed from X1 because `a in C({s})`. | `tests/test_masking.py` |
| TM09 | Exclusively neutral branch | A term and ancestors found only in the neutral branch enter X1 unless already in K0 or outside Q. | `tests/test_masking.py` |
| TM10 | K0 precedence over X1 | A term present in both raw closures receives primary reason `PRIOR_KNOWN`; it is absent from M. | `tests/test_masking.py` |
| TM11 | Alternate ID before masking | A t0 alternate ID and its primary ID collapse to one canonical K0 term with original-ID provenance. | `tests/test_masking.py` |
| TM12 | Term absent from O | The term enters no direct truth, closure, Q, or mask set and produces `INVALID_OR_UNMAPPABLE` audit. | `tests/test_truth.py` |
| TM13 | Multiple direct-term provenance | Two direct children sharing an ancestor yield two propagation records and two source direct terms for that ancestor. | `tests/test_masking.py` |
| TM14 | Order invariance | Permuting old states, new states, events, and source assertions yields identical immutable results. | `tests/test_truth.py` |
| TM15 | Empty truth after masking | If every candidate term is in K0 or outside Q, status is `EXCLUDED_EMPTY_EVALUABLE_TRUTH`; it is not evaluable. | `tests/test_masking.py` |
| TM16 | Future propagation through masked node | Contract fixture: prediction `10 -> 6 -> 4`, with 6 masked and 4 evaluable, must give 4 the score of 10 before M is applied. Mark as a future prediction test; do not create prediction code in this milestone. | future `tests/test_predictions.py` |
| TM17 | Ontology fingerprint compatibility | Separately loaded ontology objects with equal stable fingerprints are accepted; any differing fingerprint is rejected before truth construction. | `tests/test_truth.py` |
| TM18 | Unique replacement | A permitted replaced ID is canonicalized before D_score/K0/X1; an ambiguous replacement is audited and excluded. | `tests/test_truth.py` |
| TM19 | Event profiles | Branch, refinement, combined, and evidence-confirmation select exactly their declared event types. | `tests/test_truth.py` |
| TM20 | IEA-to-IDA confirmation | Main K0 masks the upgraded term; confirmation mode unmasks only the direct term while its known ancestors stay masked. | `tests/test_masking.py` |
| TM21 | Nonselected refinement becomes neutral | In a branch-only run, a valid refinement is absent from D_score and present in D_neutral1 under the chosen neutral policy. | `tests/test_truth.py` |
| TM22 | Mixed `{IEA, curated}` state | With `include_new_iea=False`, a term with another accepted usable tier remains eligible for neutral truth. | `tests/test_truth.py` |
| TM23 | Cross-aspect assertion | A BP term labeled as MF enters no aspect set and receives `CROSS_ASPECT`. | `tests/test_truth.py` |
| TM24 | Explicit set identities | Assert `X1 = C(D_neutral1)-C(D_score)`, `M = Q-K0-X1`, and `T = C(D_score) intersect M` on one complete DAG. | `tests/test_masking.py` |
| TM25 | Diamond DAG provenance | Two equal-length routes from one direct term to one ancestor produce one semantic closure entry with the correct minimum distance, the union of encountered relation IRIs, and the lexicographically chosen shortest witness; no all-path collection is built. | `tests/test_masking.py` |
| TM26 | Policy identity | Reordered equivalent configuration produces the same policy fingerprint; a semantic configuration change changes it. Names and versions remain human-readable. | `tests/test_truth.py` |

Existing ontology, evidence, comparison, and knowledge tests remain regression
tests. TM16 is a documented future test design only and is not added during this
milestone.

## 16. Files proposed for the implementation milestone

### 16.1 Milestone 2A files

- `src/probe/parsing/owl.py` — preserve the full RDF graph, ontology/import
  metadata, property declarations, external classes, unknown relations, exact
  axiom origins, and parser configuration while preserving `load()`
  compatibility;
- `src/probe/ontology.py` — define stable ontology fingerprinting and expose the
  existing scoring graph as an explicit derived projection;
- `src/probe/relations.py` — new immutable property descriptors, edge records,
  exact-IRI relation policy, dispositions, and semantic traversal controls;
- `src/probe/ontology_validation.py` — new deterministic inventory, precise
  projection cycle checks, root statuses, and preflight report;
- `src/probe/__init__.py` — reviewed notebook-facing 2A exports;
- `tests/test_owl_loader.py`, `tests/test_relations.py`, and
  `tests/test_ontology_preflight.py` — PF01–PF25;
- `DEVELOPMENT.md` — record the 2A policy identities, real-artifact report, and
  approval state.

Milestone 2A does not create `truth.py`, `masking.py`, Q, K0, X1, M, or target
records. Its final deliverable is the deterministic preflight report produced
from the supplied local `go.owl`, followed by human approval or revision.

### 16.2 Milestone 2B files

After that approval, the smallest coherent 2B change is:

- `src/probe/truth.py` — new aspect, profile, direct-truth, audit, and
  target-aspect result records; D_score and D_neutral1 selection;
- `src/probe/masking.py` — bounded-provenance closure, shared Q, K0, X1, M, T,
  and empty-truth status construction;
- `src/probe/comparison.py` — additive ontology fingerprint on
  `ComparisonResult` and structured cross-aspect exclusion if needed;
- `src/probe/evidence.py` — only if a named all-usable positive constructor is
  shared instead of being represented entirely by `PositiveAssertionPolicy`;
- `src/probe/__init__.py` — reviewed notebook-facing 2B exports;
- `tests/test_truth.py` and `tests/test_masking.py` — TM01–TM15 and TM17–TM26;
- existing comparison tests only for fingerprint and cross-aspect regression;
- `DEVELOPMENT.md` — record 2B status and remaining scientific choices.

TM16 remains a documented future prediction test. Neither milestone changes
GAF/FASTA parsing, prediction, information, metric, export, CLI, dependency, or
lock files. `reference/owlLibrary3.py` remains isolated.

`parsing/owl.py` decodes RDF/OWL without choosing scientific propagation.
`relations.py` owns relation and edge policy. `ontology_validation.py` audits a
loaded context. `truth.py` selects and describes scientific truth. `masking.py`
performs closure and set construction. None becomes a monolithic pipeline.

## 17. Acceptance criteria

### 17.1 Milestone 2A acceptance

Milestone 2A is acceptable when:

1. a local `go.owl` and expected SHA-256 are required; mismatch fails
   before parsing, and no network retrieval or silent replacement occurs;
2. ontology/version IRIs, PURL provenance, imports, external classes,
   properties, unknown relations, unsupported expressions, and exact supported
   axiom origins remain inspectable;
3. every policy has a readable name, version, canonical configuration, and
   deterministic fingerprint;
4. property metadata remains separate from per-edge disposition, and every
   disposition is justified by relation IRI, endpoints, aspects, axiom origin,
   and policy version;
5. the four specified graph projections receive their own cycle checks;
   scoring-hierarchy cycles are fatal and navigation-only cycles are report-only;
6. cross-aspect edges are ignored without invalidating a term that retains an
   intra-aspect `is_a` root path;
7. part-of-only root paths are quarantined and never admitted to Q implicitly;
8. preflight output is deterministic under RDF-triple permutation;
9. the real pinned file is run through preflight and its counts, examples,
   quarantines, unknowns, unsupported constructs, imports, and cycles are
   reviewed by a human;
10. no 2B truth or mask implementation begins before that approval;
11. existing tests, all implemented PF tests, Ruff, format check,
    `git diff --check`, and `pixi run check` pass.

### 17.2 Milestone 2B acceptance

Milestone 2B is acceptable when:

1. equal stable ontology fingerprints from separate loads are accepted and any
   mismatch is rejected before set construction;
2. Q is shared, deterministic, aspect-specific, root-free, and follows the
   approved root-eligibility policy;
3. every old/new term is canonicalized and aspect-validated before set use;
4. closure is inclusive, limited to `is_a`/`part_of`, retains every direct
   source, and uses bounded deterministic witness provenance rather than all
   paths;
5. K0 follows the approved `all_usable_positive_t0` policy, including IEA and
   excluding NAS, ND, NOT, unresolved, and cross-aspect assertions;
6. K0 remains independent of the scoring evidence profile;
7. event-selection profiles produce only their declared D_score;
8. evidence confirmation is separate and may unmask only the upgraded direct
   term; it never weakens K0 for the main benchmark;
9. the primary and sensitivity neutral policies remain separately identifiable;
10. X1, M, and T follow the exact formulas and remain distinct objects;
11. roots and terms outside Q cannot enter M or T;
12. empty T excludes the target-aspect with an explicit reason;
13. all records use lightweight IDs/fingerprints and preserve required audit
    provenance without copying run-level ontology metadata or reports;
14. results are identical under input permutation;
15. existing tests, all implemented TM tests except future TM16, Ruff, format
    check, `git diff --check`, and `pixi run check` pass;
16. no prediction, information, metric, bootstrap, CLI, persistence, final
    export, dependency, or lock-file work is introduced.

## 18. Scientific decisions requiring approval

The following choices must be approved before or during implementation. They
must not be selected from predictor performance.

1. **Official release:** choose and pin the exact `go.owl` release. Its date
   remains independent of GOA0 and GOA1.
2. **Import policy:** decide whether the supplied `go.owl` must be a fully
   materialized distribution, whether pinned local imports are mandatory, or
   whether declaration-only audit is acceptable. No implicit network retrieval
   is permitted.
3. **Axiom mode:** approve asserted plus locally reconstructible axioms for the
   first benchmark, or require a separately pinned reasoner/materialization
   profile before Q is final.
4. **Property-category inheritance:** approve the proposed default of no
   automatic inheritance, including no propagation inheritance through
   `rdfs:subPropertyOf`.
5. **Safe-root eligibility:** confirm that a term with no `is_a` path but a valid
   same-aspect `part_of` path may enter Q, while a term with no safe path or a
   cross-aspect safe-root path cannot.
6. **K0 policy:** approve `all_usable_positive_t0`, including IEA and excluding
   NAS/ND/NOT, as the main prior-known policy. This plan recommends approval and
   finds it consistent with the authoritative specification.
7. **New t1 IEA:** choose whether the headline profile uses
   `primary_non_electronic_neutral` or `all_usable_neutral`. The specification
   recommends the former for the primary run and the latter as sensitivity
   analysis; the broader proposal in this task would choose the latter.
8. **Evidence-confirmation masking:** approve
   `unmask_upgraded_direct_only`, which preserves masking of known ancestors,
   or select another explicitly named confirmation design.
9. **Headline event profile:** choose `new-knowledge`, `refinement`, or
   `combined`. All can be implemented without choosing one as the permanent
   default. Evidence confirmation remains separate in every case.
10. **Neutral usable evidence scope:** confirm that recognized positive curated
   evidence outside the scoring profile may neutralize a term, while unknown
   evidence codes are quarantined rather than treated as truth.
11. **Loss and contradiction:** define later whether material t0-to-t1 loss or a
   later `NOT` excludes, stratifies, or only flags a target-aspect. This milestone
   can retain K0 and emit audit data without deciding the final benchmark rule.
12. **Replacement policy:** decide whether unique `replaced_by` normalization is
   mandatory or a named option.
13. **Future Q filters:** per-aspect information thresholds and manual include/
   exclude precedence remain deferred and require approval before they restrict
   Q.

Items 1 through 10 affect ontology preflight or truth/mask synthetic
expectations. Items 11 through 13 can remain explicit deferred configuration
points without blocking the core set-construction implementation.
