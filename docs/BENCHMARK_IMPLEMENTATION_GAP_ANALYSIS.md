# ProBE benchmark implementation gap analysis

## Scope and authority

This analysis describes the state of branch `codex/modifiche-main` on
2026-09-16. It maps the current implementation to
`BENCHMARK_SELECTION_AND_METRIC_SPEC.md`. It does not authorize benchmark
implementation or reinterpret unresolved scientific choices.

The sources were applied in this order:

1. `AGENTS.md` for project-wide working rules;
2. `docs/BENCHMARK_SELECTION_AND_METRIC_SPEC.md` for selection, evidence,
   masking, information, and metric semantics;
3. `docs/SCIENTIFIC_DECISIONS.md` for broader scientific policy;
4. `docs/Logica_di_lavoro_originale.docx` and
   `docs/SupplementaryMethods_ProBE.docx` as background and visual material.

All requested files were present and readable. The Word documents were read as
OOXML and visually inspected after rendering: three pages for the original
logic document and six pages for the supplementary methods. Page 4 of the
supplementary methods is blank. The abandoned repository was not accessed.

`reference/owlLibrary3.py` remains isolated: neither production code nor tests
import it. Its behaviors are not part of the public API.

## Baseline and current architecture

The package has a small, one-directional architecture:

- `source.py` and `validation.py`: local input abstraction, checksums, and
  structured issues;
- `records.py`: immutable FASTA and GAF boundary records;
- `parsing/`: streaming FASTA and GAF parsers and an RDFLib OWL loader;
- `identity.py`: normalized exact-sequence hashing, indexing, aliases, and
  matching;
- `ontology.py`: term resolution, relation-aware graph traversal, conservative
  annotation closure, marginal IC, and SimGIC;
- `snapshot.py`: annotations bound to one ontology object and release;
- `evidence.py`: evidence categories and one experimental acceptance policy;
- `comparison.py`: normalization, exclusions, direct-assertion comparison, and
  selected targets;
- `__init__.py`: notebook-facing public exports.

Pixi defines `lint`, `test`, `format`, and the composite `check` task. The
initial `lint` task passed. The ordinary `test` task collected 15 tests and ran
11, but four `tmp_path` setups failed because the configured Windows temporary
directory was inaccessible. Running the same installed Pytest directly with a
separate writable `--basetemp` completed with 15 passed. This is an execution
environment issue, not a test assertion failure, but `pixi run test` is not a
clean baseline in the present host configuration.

## Executive assessment

The current code is a useful vertical slice. It already protects exact
sequence matching, one-ontology normalization, alternate and replacement GO
IDs, direct evidence-set aggregation, conservative `is_a`/`part_of` closure,
and auditable `NOT` exclusions. It does not yet implement the benchmark model
defined by the specification.

The smallest critical correction is at the temporal comparison boundary. The
current assertion key is:

```text
(sequence_id, canonical_term_id, relation, aspect)
```

The scientific comparison key must be:

```text
(sequence_id, aspect, canonical_term_id)
```

Relation and other assertion-context fields must remain provenance. With the
current key, changing only `relation` creates one `ANNOTATION_REMOVED` and one
`TERM_ACQUIRED`; an accepted new evidence code can therefore select a target
for a relation-only change. This conflicts directly with the benchmark
specification.

The current `GeneOntology.information_content()` is also correctly identified
as marginal information content, not CAFA information accretion. It computes
smoothed `-ln((term_frequency + s) / (root_frequency + s))`. It does not compute
`-log2 P(term | parents(term))`, does not provide local IA or total IA over an
inclusive closure, and must not supply `Smin` or weighted CAFA metrics.

## Requirement-to-code map

Status meanings:

- **Implemented**: current behavior matches the specification at the stated
  scope;
- **Partial**: a usable foundation exists, but required fields or semantics are
  absent;
- **Missing**: no current production behavior implements the requirement;
- **Conflict**: current behavior can produce a scientifically different result.

### Identity, aliases, and taxonomy

| Requirement | Status | Current behavior and gap |
| --- | --- | --- |
| Exact normalized sequence identity | Partial | `SequenceIndex` keys buckets by SHA-256 and length and verifies the normalized string after lookup. `SequenceMatch.sequence_id` exposes the digest. The normalization-policy version is absent from the identity key. Original and normalized sequences, ambiguity flags, and a configurable terminal-stop policy are not retained together. |
| Release-specific aliases from `UNI0` and `UNI1` | Partial | `SequenceAlias(identifier, source)` can carry a release in an unstructured source string, and `from_fastas()` accepts multiple named inputs. There is no structured source database, release, canonical/isoform state, reviewed status, or historical primary/secondary accession relationship. |
| One accession with different sequences by release | Conflict | `SequenceIndex` can hold the records, but `_identity_lookup()` collapses aliases to one global `subject_id -> sequence_id` mapping and emits `AMBIGUOUS_SEQUENCE_ALIAS` when an accession maps to different sequences. It cannot interpret that case by release. |
| Multiple accessions for one sequence | Implemented | Exact matches retain a sorted tuple of `SequenceAlias` objects. |
| Taxonomic consistency of identical sequences | Missing | FASTA aliases have no taxon. There is no authoritative target taxon, pinned NCBI Taxonomy snapshot, lineage/LCA calculation, acceptable-rank policy, or quarantine for cross-lineage matches. |
| Deterministic identity behavior | Partial | SHA-256, length, sequence verification, and sorted aliases are deterministic. The policy identity and full normalization provenance are missing. |

### GO normalization and ontology semantics

| Requirement | Status | Current behavior and gap |
| --- | --- | --- |
| One immutable ontology for both GOA releases | Implemented at object level | `compare_annotations()` requires the same `GeneOntology` object by identity. The package records `versionIRI` and can checksum a `Source`, but it lacks one run manifest containing URL, retrieval date, digest, parser version, and relation-policy version. |
| Exclude terms absent from the pinned ontology | Implemented | Unknown terms do not enter comparison and create auditable `UNKNOWN_TERM` exclusions. |
| Canonical alternate IDs | Implemented | Alternate IDs resolve to the active primary ID before comparison, with `normalized_from` retained at change level. Raw per-assertion normalization provenance is not yet exported. |
| Unambiguous replacement | Partial | Exactly one active `replaced_by` target is accepted. Multiple or unusable replacements are excluded. The policy currently always permits the unique replacement and cannot be configured. `consider` values are candidates only and are not mapped. |
| Obsolete versus deprecated | Partial | Both states are represented and excluded when unresolved. OWL loading infers `obsolete` from a label beginning with `obsolete ` and `deprecated` from `owl:deprecated`; this needs real `go.owl` fixtures and metadata tests. |
| Stable relation identity | Implemented in parser | Known relations are mapped by IRI, including `part_of`, `has_part`, `occurs_in`, `located_in`, `regulates`, its positive/negative forms, `capable_of`, `capable_of_part_of`, `enables`, and `involved_in`. Unknown properties retain an IRI/OBO identifier. |
| `is_a` and `part_of` annotation propagation | Implemented | `ANNOTATION_PROPAGATION_RELATIONS` contains only these relations; `propagate()` uses inclusive ancestor closure. |
| Navigable but non-propagating relations | Partial | `regulates`, `positively_regulates`, and `negatively_regulates` are navigable but excluded from annotation propagation. Other parsed relations remain available in `edges` but are not admitted by the default navigation API. A richer graph policy object and relation metadata are missing. |
| `has_part` exclusion | Implemented | It is parsed and retained but excluded from both default navigation and propagation. |
| Direct versus propagated annotations | Partial | Temporal comparison uses direct assertions, and propagation is a separate ontology operation. Propagated terms do not retain their source direct terms, and no ground-truth record distinguishes `is_direct` with provenance. |
| Propagation cycles | Implemented for scoring graph | Validation rejects cycles in the `is_a`/`part_of` graph. Other semantic-relation cycles are allowed, and traversal terminates through visited sets. |
| Roots and aspect consistency | Partial | Canonical roots and aspect-to-namespace validation exist. Root exclusion from selection and metrics is not implemented. |

### GAF assertions, evidence, and temporal comparison

| Requirement | Status | Current behavior and gap |
| --- | --- | --- |
| Lossless GAF provenance | Partial | The 17 GAF columns are represented, including references, with/from, extension, product form, source, and line. `NOT` is retained as `negated`; other column-4 values are reduced to one `relation`, and multiple relations are rejected instead of retaining an assertion-context set. |
| Preserve all evidence codes | Implemented for the current key | Duplicate rows aggregate evidence into a set. No code is chosen arbitrarily. Because the key includes relation, evidence for one canonical term can remain split across contexts. |
| Complete evidence-code set | Implemented as a flat map | All codes in the specification are recognized. Traditional and high-throughput experimental codes are collapsed into one category. `NAS` and `ND` are mapped to author/curator categories rather than an explicit unusable tier. |
| Evidence priority tiers | Missing | There is no ordered tier model or `best_tier(evidence_set)`. |
| Cumulative evidence profiles | Missing | Only one `experimental()` policy exists; strict versus all-experimental and broader nested profiles are absent. |
| Exclude `NAS` and `ND` | Partial | They do not satisfy the present experimental policy, but they are not intrinsically unusable. A future policy accepting author or curator categories could accidentally accept them. |
| Threshold-crossing evidence upgrades | Partial | The current experimental policy selects a transition only if no experimental evidence was previously present and new experimental evidence is present. It cannot express tier-specific cumulative thresholds or distinguish traditional from high-throughput evidence. |
| Canonical membership comparison | Conflict | The current key contains relation. Relation-only changes can be emitted as acquisition/removal instead of context change. Qualifier, extension, product-form, and reference changes are not compared as explicit context events. |
| Removed annotations | Partial | Direct removals are emitted, but material loss/contradiction policy, loss thresholds, and strict-profile quarantine are missing. |
| `NOT` handling | Partial | `NOT` rows are auditable exclusions and exclude positive descendants through `is_a`/`part_of` for the same sequence and aspect. There is no separate negative-truth policy for scoring contraindicated predictions. |
| Direct event versus propagated ancestor | Implemented at current comparison level | Only direct GAF assertions create `TERM_ACQUIRED`; `propagate()` is not called by comparison. Later selection and export layers are missing. |

### Knowledge states and event taxonomy

| Requirement | Status | Current behavior and gap |
| --- | --- | --- |
| `GLOBAL_NONE`, `ASPECT_NONE`, `ASPECT_PRESENT` | Missing | No prior-knowledge-state model exists. |
| `NK_GLOBAL` and `NK_ASPECT` | Missing | `selected_targets` is a flat set without aspect-specific knowledge state. |
| `LK_BRANCH` | Missing | Acquisitions are not compared with the prior accepted closure. |
| `LK_REFINEMENT` | Missing | Descendant refinement is not distinguished from unrelated branch acquisition. |
| Redundant ancestor | Missing | A newly direct ancestor already implied at `t0` can currently be a qualifying acquisition. |
| `EVIDENCE_CONFIRMATION` | Missing as a profile/superclass | Evidence upgrades exist, but are mixed into one selected-target result and have no separate scientific profile or superclass. |
| Context-only change | Conflict | No context event exists, and relation-only change is misclassified. |
| Event superclasses | Missing | `NOVEL_ASPECT_KNOWLEDGE`, `EXTENDED_KNOWLEDGE`, `EVIDENCE_CONFIRMATION`, `KNOWN_CONTROL`, and `NON_EVALUABLE` are absent. |
| Multiple events per target-aspect | Partial | Multiple `AnnotationChange` records are possible, but there is no target-aspect aggregate, deterministic reporting precedence, or event superclass. |

### Selection, masks, and information filtering

| Requirement | Status | Current behavior and gap |
| --- | --- | --- |
| `D_score` and scored direct truth | Implemented | Named event profiles construct canonical aspect-specific direct truth without promoting `direct_candidates` to final truth. |
| Prior-known mask `K0` | Implemented | `all_usable_positive_t0` is independent of event evidence and includes IEA while excluding NAS/ND/NOT-derived states. |
| Neutral-truth mask `X1` | Implemented | Named neutral policies construct `D_neutral1`; `X1` is its closure minus `G_candidate`. |
| Branch-exclusive neutral closure | Missing | The required `C(D_neutral1) - C(D_score)` operation is absent. The original Word graph motivates this behavior, but current code has no mask layer. |
| Global terms of interest `Q` and final mask `M` | Implemented | Immutable aspect universes use approved preflight reachability; roots remain outside Q and `M = Q - K0 - X1`. |
| Prior-known versus neutral precedence | Missing | The code cannot distinguish terms always masked at `t0` from later neutral truth shared with scored closure. |
| Low-information filtering | Missing | There is no benchmark eligibility filter or per-aspect threshold configuration. |
| Manual include/exclude GO files | Missing | No parser, checksum, precedence rule, or audit record exists. |
| Marginal information content | Implemented with limitations | `information_content()` computes smoothed namespace-root marginal IC with natural logarithms from caller-supplied direct counts. Corpus identity, evidence/taxon scope, and table checksum are not represented. |
| Local information accretion | Missing | Conditional `-log2 P(g | parents(g))` is not implemented. |
| Total information accretion | Missing | There is no sum of unique local IA values over inclusive closure excluding roots. |
| Thresholds specific to MF/BP/CC | Missing | There is no aspect-specific filter configuration. |

### Predictions and metrics

| Requirement | Status | Current behavior and gap |
| --- | --- | --- |
| Prediction records and score validation | Missing | There is no prediction input model, score normalization, duplicate consolidation, or invalid-term report. |
| Score propagation before masking | Missing | Ontology propagation handles sets, not maximum prediction scores. No evaluation mask exists. |
| Masked intermediate traversal | Missing | No algorithm propagates through masked nodes and applies masks afterward. |
| Roots and out-of-vocabulary predictions | Missing | Root exclusion and auditable unknown/obsolete/cross-aspect prediction handling are absent. |
| Protein-centric macro `Fmax` | Missing | No threshold sweep or global threshold aggregation exists. |
| `Smin` using IA | Missing | No RU/MI calculation or IA table exists. Current marginal IC must not be substituted. |
| Coverage | Missing | No evaluated-target denominator or method coverage model exists. |
| Full versus partial evaluation | Missing | Neither normalization mode is represented or labeled. |
| Independent MF/BP/CC evaluation | Missing in metrics | Snapshot validation knows aspects, but there is no evaluator. |
| Deterministic metric output | Missing | Input-order invariance, fixed threshold ordering, and normalized artifact export need implementation and tests. |

### Audit and output

| Requirement | Status | Current behavior and gap |
| --- | --- | --- |
| Structured validation and exclusions | Partial | Validation issues and comparison exclusions are inspectable and include source/line where available. They do not yet cover every eligibility, mask, and prediction decision. |
| Input checksums | Partial | `Source.checksum()` exists, but there is no run manifest binding every input and policy. |
| Normalized target, alias, assertion, event, mask, and truth tables | Missing | Current immutable objects are foundations, but no normalized export model exists. |
| Deterministic and auditable output | Partial | Several tuples are sorted and records are immutable. There is no stable serialization, schema version, run ID, complete policy metadata, or row-level derivation links. |

## Documentation conflicts

The Markdown benchmark specification controls all conflicts below.

### `docs/Logica_di_lavoro_originale.docx`

- Page 1 searches only a new UniProt release. The specification requires
  release-specific aliases from both `UNI0` and `UNI1` when possible.
- Pages 1 and 3 define KK/LK1/LK2/NK1/NK2 as single categories. The
  specification separates prior knowledge state from event type and derives
  `NK_GLOBAL`, `NK_ASPECT`, `LK_BRANCH`, `LK_REFINEMENT`, and upgrade labels.
- Pages 1-3 describe one protein-specific "blacklist." The specification splits
  it into the always-applied prior-known mask `K0`, the branch-exclusive later
  neutral mask `X1`, and the global universe `Q`.
- The two graphs on page 2 correctly show that a masked intermediate must not
  stop propagation and that shared scored ancestors remain evaluable. The
  authoritative algorithm is to propagate prediction scores first and apply
  `M = Q - K0 - X1` afterward.
- Page 3 suggests retaining one highest-priority evidence code and choosing
  among experimental codes arbitrarily. The specification requires the full
  evidence set and a deterministic best tier.
- Page 3 proposes one wide ground-truth row with BP/MF/CC columns. The
  specification requires normalized assertion, event, mask, and ground-truth
  records with one aspect/term per record.
- The document refers to a command-line parameter. The current project is
  notebook-first and has no separate CLI; the policy should first be a Python
  configuration object.

### `docs/SupplementaryMethods_ProBE.docx`

- Pages 1-2 use DIAMOND for exact identity. The authoritative implementation
  uses deterministic normalized sequence hashing and direct sequence
  verification; similarity search remains a separate future operation.
- Pages 1-2 filter non-experimental annotations before temporal comparison.
  This would lose prior-known IEA and prevent evidence-threshold transitions.
  The specification retains assertions first and applies evidence profiles and
  masks afterward.
- Page 2 describes comparison of propagated annotation sets. The specification
  defines knowledge-gain events on normalized direct terms and uses propagation
  later for closure, masks, and evaluation.
- Pages 2-3 use one NK/LK partition. The specification requires aspect-specific
  prior states, detailed events, event superclasses, and separate reporting of
  evidence confirmation.
- Pages 5-6 define marginal root-relative IC and use it for weighted metrics and
  semantic distance. The specification permits marginal IC as an optional
  specificity score but requires conditional information accretion for CAFA
  `Smin` and weighted semantic calculations.
- Pages 5-6 do not define protein-centric macro averaging, full versus partial
  denominators, coverage, or one global threshold per method/aspect/profile/
  stratum. The Markdown specification supplies those semantics.
- The historical benchmark counts on pages 2-3 are background results, not
  fixtures or acceptance criteria for the current general library.

### Other repository documentation

- `README.md` describes CAFA targets as the normal workflow, while `AGENTS.md`
  and `SCIENTIFIC_DECISIONS.md` require arbitrary FASTA targets. The example is
  acceptable, but wording that implies CAFA specificity should be corrected in
  a later documentation change.
- `DEVELOPMENT.md` still lists prediction metrics as deferred. That is
  consistent with current code and this analysis; the benchmark specification
  defines future behavior but does not make it implemented.
- `DEVELOPMENT.md` describes the current experimental evidence preset as
  traditional plus high-throughput. The benchmark specification now requires a
  strict traditional profile and a separate cumulative all-experimental
  profile.

## Existing public interfaces to preserve

The next work should extend these interfaces without a broad rewrite:

- `Source`, `ValidationIssue`, `ValidationReport`, and `ValidationError`;
- immutable `SequenceRecord` and `AnnotationRecord` boundary records;
- `FastaParser.iter_records()`, `GafParser.iter_records()`, and
  `OwlLoader.load()`;
- `SequenceDataset`, `SequenceIndex`, `IdentityMap`, and `SequenceMatch`;
- `GeneOntology.resolve()`, `resolve_id()`, `parents()`, `ancestors()`,
  `descendants()`, `propagate()`, `excluded_by_not()`, and `validate()`;
- `AnnotationSnapshot.read()` and its shared-ontology invariant;
- `compare_annotations()` and `ComparisonResult` as a compatibility-level
  direct-delta API, while correcting its scientific key;
- explicit exports from `probe.__init__` for notebook use.

New benchmark selection records should consume these foundations. They should
not move parsing, ontology loading, selection, masks, and metrics into one
pipeline object.

## Existing scientific test protection

The current 15 tests protect the following behavior:

- FASTA normalization, exact sequence matching, multiple aliases, unmatched
  targets, invalid residues, and source-line reporting;
- full GAF row provenance, `NOT`, relation, reference, extension, and product
  form parsing;
- alternate-ID resolution and `is_a`/`part_of` propagation;
- unique obsolete replacement and rejection of ambiguous `consider` terms;
- navigable `regulates` without annotation propagation and exclusion of
  `has_part` from default navigation;
- downward `NOT` exclusion through the conservative closure;
- marginal IC and SimGIC over annotation closure;
- propagation-cycle validation;
- OWL class, relation, replacement, and version-IRI loading;
- acquisition, one experimental-threshold upgrade, evidence-set behavior,
  canonical replacement comparison, `NOT` exclusions, and enforcement of the
  same ontology object.

These tests should remain regression tests. The IC test protects the present
marginal formula only; it is not evidence that information accretion exists.

## Missing synthetic tests

Before a definitive benchmark, synthetic tests are needed for:

1. normalization-policy version and configurable terminal `*` handling;
2. the same exact sequence across releases with structured aliases;
3. one accession attached to different sequences in different releases;
4. authoritative target taxon, compatible same-lineage aliases, and
   cross-lineage quarantine;
5. term absence from `O`, alternate ID, unique permitted replacement, and
   ambiguous replacement in symmetric old/new inputs;
6. relation-only, qualifier-only, extension-only, reference-only, and product-
   form-only context changes that do not become acquisitions;
7. complete evidence sets, deterministic best tier, `NAS`/`ND` exclusion, and
   all cumulative profiles;
8. `NK_GLOBAL`, `NK_ASPECT`, `LK_BRANCH`, `LK_REFINEMENT`, redundant ancestor,
   and threshold-crossing upgrade;
9. direct versus propagated truth with source-direct-term provenance;
10. prior-known and neutral-truth mask precedence, including shared ancestors;
11. a neutral term whose unmasked descendant prediction remains an FP;
12. score propagation through a masked intermediate before masking;
13. marginal IC, local IA, and total IA as three different hand-computed
    quantities;
14. MF/BP/CC-specific thresholds and manual include/exclude precedence;
15. empty truth after masking with an explicit exclusion reason;
16. perfect, missing, wrong, duplicate, root, and out-of-vocabulary predictions;
17. `Fmax`, `Smin`, coverage, full/partial denominators, and one global
    threshold;
18. invariance to input order and stable audit/export ordering.

## Smallest coherent first implementation milestone

### Milestone: canonical direct-event classification

Implement only the direct temporal event layer. Do not implement masks,
information accretion, prediction ingestion, metrics, or exports in this
milestone.

The milestone should correct the comparison key, introduce explicit evidence
tiers/profiles, and classify aspect-specific prior state plus direct events.
This creates the stable input required by all later masking and metric work.

### Exact files to change

- `src/probe/evidence.py`: add ordered evidence tiers, unusable codes, and
  cumulative immutable profiles while preserving `EvidencePolicy.experimental()`
  as a compatibility constructor;
- `src/probe/comparison.py`: aggregate membership by
  `(sequence_id, aspect, canonical_term_id)`, retain all assertion contexts, and
  emit context changes separately from acquisitions/removals;
- `src/probe/knowledge.py`: new pure event-classification module;
- `src/probe/__init__.py`: export reviewed notebook-facing structures;
- `tests/test_evidence.py`: synthetic tier/profile tests;
- `tests/test_comparison.py`: relation/context regression tests and canonical
  evidence aggregation;
- `tests/test_knowledge.py`: hand-computed prior-state and event fixtures;
- `DEVELOPMENT.md`: record milestone scope and status after implementation.

No parser, ontology loader, mask, metric, storage, CLI, or dependency change is
needed for this milestone.

### Proposed public data structures

All should be frozen, typed, and independent of I/O:

```text
EvidenceTier
EvidenceProfile(name, accepted_tiers)
AssertionContext(relation, qualifiers, extensions, gene_product_form_id,
                 references, assigned_by, annotation_date)
DirectTermState(sequence_id, aspect, canonical_term_id, evidence_codes,
                contexts, source_assertions)
PriorKnowledgeState: GLOBAL_NONE | ASPECT_NONE | ASPECT_PRESENT
EventType: UNCHANGED_ACCEPTED | EVIDENCE_UPGRADE | BRANCH_ACQUISITION |
           SPECIFICITY_REFINEMENT | REDUNDANT_ANCESTOR |
           ASSERTION_CONTEXT_CHANGE | AMBIGUOUS_OR_UNMAPPABLE
EventSuperclass: NOVEL_ASPECT_KNOWLEDGE | EXTENDED_KNOWLEDGE |
                 EVIDENCE_CONFIRMATION | KNOWN_CONTROL | NON_EVALUABLE
BenchmarkEvent(sequence_id, target_ids, aspect, term_id, prior_state,
               event_type, event_superclass, old_evidence_codes,
               new_evidence_codes, contexts, qualifies, exclusion_reasons)
```

`source_assertions` may initially hold immutable `AnnotationRecord` references;
an export identifier can be added when serialization is designed.

### Algorithmic invariants

1. Biological membership is keyed only by sequence, aspect, and canonical GO
   term.
2. Relation, qualifiers, references, extensions, dates, assigned-by source, and
   product forms remain assertion context and never create a term acquisition.
3. Every evidence code is retained. Classification uses the best usable tier;
   it never chooses an arbitrary representative code.
4. `NAS` and `ND` have no accepted tier in every profile.
5. Evidence profiles are nested and deterministic.
6. An upgrade qualifies only when evidence crosses from outside to inside the
   active profile. Movement wholly inside a profile is recorded but does not
   create a benchmark event.
7. Prior state is calculated per sequence and aspect, with other aspects used
   only to distinguish `GLOBAL_NONE` from `ASPECT_NONE`.
8. Branch/refinement/redundant-ancestor classification uses only the pinned
   ontology's `is_a`/`part_of` closure.
9. Event classification uses direct normalized terms; propagated ancestors are
   never emitted as acquired direct terms.
10. Every ambiguous or unmappable record remains in audit output.
11. Results have a documented stable sort independent of input row order.
12. Existing notebook interfaces continue to work unless a scientifically
    incorrect result is being corrected and explicitly documented.

### Synthetic tests for this milestone

Use one small hand-built DAG and no external data:

1. no accepted `t0` annotation anywhere plus a new accepted BP term gives
   `NK_GLOBAL`/`NOVEL_ASPECT_KNOWLEDGE`;
2. accepted MF knowledge but no accepted BP knowledge gives BP `NK_ASPECT`;
3. an unrelated new BP branch with prior BP knowledge gives `LK_BRANCH`;
4. a new descendant of prior direct BP knowledge gives `LK_REFINEMENT`;
5. a newly direct ancestor already in prior closure is `REDUNDANT_ANCESTOR` and
   does not qualify;
6. exact IEA-to-traditional-experimental transition is an upgrade under the
   strict experimental profile;
7. old `{IEA, IMP}` to new `{IDA}` is not a first strict-experimental upgrade;
8. changing only relation yields one context event and no acquisition/removal;
9. changing extension or product form yields a context event only;
10. multiple evidence rows preserve all codes and select the deterministic best
    tier;
11. `NAS` and `ND` alone never satisfy any profile;
12. moving from strict to all-experimental adds only high-throughput-supported
    events;
13. alternate IDs and a permitted unique replacement compare as one canonical
    term, while an ambiguous replacement is quarantined;
14. aliases from two releases pointing to the same sequence yield one event;
15. row-order permutations yield identical event tuples.

### Acceptance criteria

- The 15 existing tests remain green.
- Every milestone test above passes using synthetic data only.
- A relation-only change cannot produce `TERM_ACQUIRED` or select a target.
- Old and new evidence sets are complete and profiles produce nested accepted
  event sets.
- `NK_GLOBAL`, `NK_ASPECT`, `LK_BRANCH`, `LK_REFINEMENT`, redundant ancestor,
  and evidence confirmation are distinguishable without consulting masks.
- Direct and propagated terms remain separate.
- No network access, new dependency, dataset, persistence layer, CLI, or metric
  implementation is introduced.
- `pixi run check` passes in an environment with a writable Pytest temporary
  directory.
- `DEVELOPMENT.md` records the completed scope and any still-open choices.

## Missing inputs and unclear operational inputs

No scientific dataset should be downloaded for the proposed milestone. A real
benchmark run will later require:

- pinned `TG`, `UNI0`, `UNI1`, `GOA0`, `GOA1`, and `O` artifacts with hashes and
  release metadata;
- target taxon provenance and a pinned NCBI Taxonomy snapshot;
- a pinned corpus and evidence/taxon scope for IC and IA;
- per-aspect information thresholds and optional manual include/exclude files;
- a declared annotation-loss/contradiction policy;
- a declared prior-knowledge and `t1` IEA policy;
- a prediction format, score policy, and evaluation profile.

These are not needed to implement and test the first synthetic event milestone.

## Scientific decisions still requiring approval

The following decisions remain open under the authoritative specification:

1. whether ontology `O` is pinned at `t0` or at another explicitly declared
   date;
2. which positive evidence categories enter prior-known masking at `t0`;
3. whether IEA first appearing at `t1` enters the neutral-truth mask;
4. which cumulative evidence profile is active for each exported benchmark;
5. the IA or IC threshold for MF, BP, and CC and the contents/precedence of any
   manual include and exclude files;
6. which annotation corpus, taxonomic scope, evidence scope, smoothing rule,
   logarithm base, and formula produce marginal IC and CAFA IA;
7. how much annotation loss or contradiction excludes a target;
8. which NCBI taxonomic rank is acceptable when an exact sequence maps to
   multiple species and the target has no authoritative taxon;
9. whether ambiguous taxonomic identity excludes a target or produces
   stratified records;
10. whether headline evaluation reports full mode only or both full and partial
    modes;
11. whether unambiguous `replaced_by` normalization is always enabled or is a
    named policy option;
12. whether and how valid `NOT` assertions contribute to a future negative-
    truth scoring profile;
13. whether relations beyond `regulates` and its positive/negative forms should
    be exposed as default navigable semantic edges. This does not change the
    approved conservative propagation set of `is_a` and `part_of`.

The first milestone can avoid deciding items 1-3 and 5-13. It needs approval of
the initial evidence profiles in item 4; the specification's recommended start
is `experimental_strict`, followed by `experimental_all` only when target counts
are insufficient and before examining predictor performance.

## Legacy reference assessment

`reference/owlLibrary3.py` should remain reference-only. It contains potentially
useful navigation ideas, but it is not safe to import or port wholesale. Among
the observed defects are label-based relation recognition, wildcard imports,
fragile obsolete/deprecated detection, misspelled variables in cumulative
frequency code, a `get_leaves()` error path that calls a set, character-wise
construction in `expanded = set(go)`, and a SimGIC branch that computes both
ancestor sets from `go_1`. Its IC is also marginal root-relative IC, not CAFA
information accretion. Current focused implementations and tests should be
extended behavior by behavior instead.
