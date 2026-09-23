# ProBE benchmark selection and metric evaluation specification

## Purpose

This document defines how ProBE should select Gene Ontology annotations for a
time-delayed benchmark and how predictions should be evaluated. It translates
the scientific rationale into explicit sets, policies, data structures, and test
cases suitable for implementation in the `codex/modifiche-main` branch.

The central objective is to measure acquisition of protein-function knowledge
after a prediction cutoff without penalizing a method for predicting annotations
that are known to be true but intentionally outside the scored benchmark.

## Review outcome

The proposed philosophy is scientifically sound, but four distinctions are
required for a correct implementation:

1. Benchmark events are defined per protein and GO aspect, while annotation
   changes are defined per protein, aspect, and GO term.
2. Prior knowledge at the prediction cutoff and true but non-scorable knowledge
   observed later require different masks.
3. Evidence codes must be retained as sets. One code must not be selected
   arbitrarily as the representative evidence.
4. CAFA semantic metrics use information accretion based on conditional
   probabilities, which is not the same quantity as marginal information
   content computed from term frequency relative to the ontology root.

The terms `blacklist` and `pruned ground truth` should be replaced in code by
`evaluation mask`, `prior-known mask`, and `neutral truth mask`. These names
describe the semantics and reduce the risk of applying a graph operation in the
wrong direction.

## Timepoints and immutable inputs

Define the following times and inputs:

- `t0`: prediction or knowledge cutoff;
- `t1`: benchmark collection time, with `t1 > t0`;
- `GOA0`: GO annotation snapshot at or before `t0`;
- `GOA1`: later GO annotation snapshot at `t1`;
- `UNI0`: sequence and alias release associated with `t0`;
- `UNI1`: sequence and alias release associated with `t1`;
- `O`: one immutable official GO ontology snapshot used throughout the run;
- `TG`: input target sequences.

The ontology `O` should normally be the ontology made available to predictors at
the prediction cutoff. A GO term introduced only after `t0` was not a valid
prediction target and should not become a false negative. Such terms are outside
the global terms-of-interest set and must be reported separately.

If a later ontology is intentionally used, that choice changes the scientific
question and must be identified as a separate benchmark profile.

Record the checksums and release identifiers of all inputs. Do not use the
mutable `go.owl` PURL as the identity of a published benchmark; pin the
retrieved file and record its version IRI and SHA-256.

## Protein identity and alias scope

Resolve targets by exact normalized sequence identity. The primary key is the
sequence hash plus sequence length and normalization-policy version. Accessions
are aliases.

Use both `UNI0` and `UNI1` when possible. Searching only `UNI1` can miss retired
identifiers, hide sequence changes under an accession, or incorrectly interpret
an identifier introduced after `t0` as prior knowledge.

Maintain release-specific alias provenance:

```text
sequence_identity -> source_release -> accession -> record metadata
```

An identical sequence can occur in more than one taxon. Therefore, a sequence
hash alone does not always determine species. If the target has an authoritative
taxon, use it to constrain alias resolution. Otherwise, resolve every alias taxon
through a pinned NCBI Taxonomy snapshot and calculate their lowest common
ancestor. Same-family matches may be accepted by a configurable taxonomic-scope
policy, but every taxon must remain in provenance. Matches extending beyond the
approved lineage rank are quarantined instead of assigning one species
arbitrarily.

## Independent evaluation by GO aspect

Perform selection and evaluation independently for:

- Molecular Function `MF`;
- Biological Process `BP`;
- Cellular Component `CC`.

A target can be eligible in one aspect and ineligible in another. All category
assignments, masks, ground-truth sets, thresholds, and metrics carry an explicit
aspect field.

Do not use annotations from one aspect as term-level ground truth for another.
Annotations in other aspects are used only to distinguish global from
aspect-specific absence of prior knowledge.

## Annotation normalization

Before temporal comparison:

1. Map alternate GO IDs to the active canonical ID in `O`.
2. Apply an unambiguous `replaced_by` mapping only when the policy permits it.
3. Discard from evaluation obsolete terms with no unique active replacement.
4. Discard from evaluation terms absent from `O`, including terms introduced at
   `t1` that were not valid prediction targets.
5. Preserve the original GO ID and the normalization decision.
6. Preserve relation, qualifiers, evidence, references, annotation extension,
   assigned-by source, date, and gene-product form.
7. Keep positive and `NOT` assertions separate.

Keep discarded records and reasons in the audit output even though they do not
enter target selection, masks, or metrics.

For benchmark selection, compare canonical GO membership using
`(sequence_id, aspect, canonical_term_id)`. If the term is present at both `t0`
and `t1`, changes in relation, qualifier, extension, reference, or product form
do not select the target. The only selecting change for an already present term
is an evidence transition that crosses the active evidence-profile threshold.
The other fields remain important provenance and validation data. In particular,
the current comparison key includes the annotation relation and must not turn a
relation-only change into one removal plus one acquisition.

## Evidence representation

For each normalized direct assertion, retain the complete set of evidence codes:

```text
(sequence_id, aspect, GO term, assertion context) -> {evidence codes}
```

Never choose one experimental code at random. The benchmark normally needs only
policy-level facts such as:

- whether any evidence accepted by the active profile exists;
- whether profile-accepted evidence already existed at `t0`;
- whether the assertion changed from electronic or curated to experimental;
- which exact codes and references support the decision.

Evidence priority is a configurable order over evidence tiers. It is not an
official GO ranking of the truth of individual codes. GO defines evidence
categories but does not prescribe one universal total confidence order. ProBE
uses the following operational tiers for reproducible benchmark expansion.

| Priority | Tier | Evidence codes | Default interpretation |
| ---: | --- | --- | --- |
| 1 | `EXPERIMENTAL_TRADITIONAL` | EXP, IDA, IPI, IMP, IGI, IEP | Direct or conventional experimental support |
| 2 | `EXPERIMENTAL_HIGH_THROUGHPUT` | HTP, HDA, HMP, HGI, HEP | Experimental support from high-throughput methods |
| 3 | `PHYLOGENETIC_CURATED` | IBA, IBD, IKR, IRD | Manually reviewed phylogenetic inference |
| 4 | `TRACEABLE_CURATED_STATEMENT` | IC, TAS | Traceable curator or author inference, not experimental |
| 5 | `COMPUTATIONAL_CURATED` | ISS, ISO, ISA, ISM, IGC, RCA | Computational analysis with varying curatorial input |
| 6 | `ELECTRONIC` | IEA | Automatically generated and not individually reviewed |
| excluded | `UNUSABLE_STATEMENT` | NAS, ND | Non-traceable statement or absence of biological data |

`IC` and `TAS` can be valuable curated evidence, but they must not be described
as experimental. `RCA` explicitly denotes reviewed computational analysis;
nevertheless, keeping the whole computational family together is simpler for the
first implementation. A future policy may split `RCA` after biological review.

Store all codes and calculate the best available tier as:

```text
best_tier(evidence_set) = minimum priority among usable codes
```

`NAS` and `ND` never contribute an accepted tier. If a record contains both an
excluded code and a usable higher-tier code, preserve both but classify it by the
usable tier.

Define cumulative acceptance profiles rather than replacing one evidence code
with another:

| Profile | Accepted tiers |
| --- | --- |
| `experimental_strict` | traditional experimental only |
| `experimental_all` | traditional plus high-throughput experimental |
| `curated_phylogenetic` | all experimental plus phylogenetic curated |
| `curated_traceable` | previous tiers plus IC and TAS |
| `curated_non_electronic` | previous tiers plus computational curated |

The profiles are nested. Start with `experimental_strict`; if it yields too few
target-aspects, expand one profile at a time and report target counts by aspect,
species, event superclass, and evidence tier. Do not choose a profile after
examining predictor performance.

For an active profile with maximum accepted priority `k`, an evidence upgrade
occurs when the best usable evidence at `t0` is outside that profile and the best
evidence at `t1` is inside it. Upward movements that remain entirely inside the
same accepted profile are recorded but do not create a new benchmark event.

## Orthogonal classification axes

The labels `KK`, `LK1`, `LK2`, `NK1`, and `NK2` mix two different questions:

1. How much knowledge accepted by the active evidence profile existed at `t0`?
2. What kind of annotation event occurred between `t0` and `t1`?

Store these as orthogonal axes and derive the familiar labels for reporting.

### Prior knowledge state

For protein `p` and aspect `a`, define:

- `GLOBAL_NONE`: no profile-accepted annotation in any GO aspect at `t0`;
- `ASPECT_NONE`: no profile-accepted annotation in aspect `a` at `t0`, but
  profile-accepted knowledge exists in another aspect;
- `ASPECT_PRESENT`: profile-accepted knowledge exists in aspect `a` at
  `t0`.

These correspond approximately to the intended distinction between `NK2`,
`NK1`, and `LK`, respectively. CAFA defines NK and LK at the ontology/aspect
level according to whether prior experimental annotation existed in that
ontology. `GLOBAL_NONE` is a useful stricter subdivision, but it should not be
presented as the only CAFA definition of NK.

### Event type

For a direct term that has profile-accepted evidence at `t1`, classify one
of the following:

- `UNCHANGED_ACCEPTED`: profile-accepted evidence already existed for
  the same normalized assertion at `t0`;
- `EVIDENCE_UPGRADE`: the same GO term existed at `t0` only with evidence outside
  the active profile and has profile-accepted evidence at
  `t1`;
- `BRANCH_ACQUISITION`: a new profile-accepted term is not entailed by the prior
  accepted closure and is not a descendant refinement of a prior direct
  term;
- `SPECIFICITY_REFINEMENT`: the new profile-accepted term is a descendant of a
  previously accepted term and therefore provides more specific knowledge;
- `REDUNDANT_ANCESTOR`: the new direct term was already entailed as an ancestor
  of prior knowledge;
- `ASSERTION_CONTEXT_CHANGE`: term identity is unchanged but relation,
  qualifier, extension, or product form changed;
- `AMBIGUOUS_OR_UNMAPPABLE`: the event cannot be interpreted safely.

`SPECIFICITY_REFINEMENT` is a meaningful gain and must not be discarded merely
because the new term has a parent-child relationship with an old term.
Conversely, `REDUNDANT_ANCESTOR` should not select a benchmark protein because
the term was already implied by prior knowledge.

### Derived reporting labels

Recommended reporting mappings are:

| Reporting label | Prior state | Eligible event |
| --- | --- | --- |
| `KK` | any | `UNCHANGED_ACCEPTED`; control only |
| `LK_BRANCH` | `ASPECT_PRESENT` | `BRANCH_ACQUISITION` |
| `LK_REFINEMENT` | `ASPECT_PRESENT` | `SPECIFICITY_REFINEMENT` |
| `LK_UPGRADE` | `ASPECT_PRESENT` | `EVIDENCE_UPGRADE` |
| `NK_ASPECT` | `ASPECT_NONE` | acquisition, refinement, or upgrade |
| `NK_GLOBAL` | `GLOBAL_NONE` | acquisition or upgrade |

If compatibility with `LK1`, `LK2`, `NK1`, and `NK2` is required, expose them as
derived aliases in exports. Do not use them as the only internal state.

A protein-aspect may contain more than one event type simultaneously. Store all
events and define a deterministic reporting precedence only for summary tables.

### Event superclasses

Expose a simpler superclass in addition to the detailed event:

| Superclass | Included detailed events | Shared interpretation |
| --- | --- | --- |
| `NOVEL_ASPECT_KNOWLEDGE` | eligible events with `GLOBAL_NONE` or `ASPECT_NONE` | First profile-accepted knowledge for that GO aspect |
| `EXTENDED_KNOWLEDGE` | `BRANCH_ACQUISITION`, `SPECIFICITY_REFINEMENT` with `ASPECT_PRESENT` | New functional content added to an already characterized aspect |
| `EVIDENCE_CONFIRMATION` | `EVIDENCE_UPGRADE` | Existing GO claim crosses the active evidence threshold |
| `KNOWN_CONTROL` | `UNCHANGED_ACCEPTED` | Already accepted at `t0`; not a benchmark target |
| `NON_EVALUABLE` | `REDUNDANT_ANCESTOR`, context-only change, absent/obsolete term, ambiguity | No defensible scored knowledge-gain event |

For high-level reporting, `NOVEL_ASPECT_KNOWLEDGE` can be called `NK`, while
`EXTENDED_KNOWLEDGE` can be called `LK`. Keep `EVIDENCE_CONFIRMATION` separate:
it asks whether a method anticipated stronger support for an existing claim,
which is not the same task as discovering a new function.

## Direct terms and graph closure

Let `C(S)` be the inclusive ancestor closure of a set `S` using only the pinned
annotation-propagation relation policy. The conservative default is:

```text
is_a
part_of
```

`regulates`, `positively_regulates`, `negatively_regulates`, `has_part`, and
contextual or annotation relations must not enter `C` unless a separate policy
is explicitly approved.

Keep direct terms and propagated terms in different fields. Every propagated
term must retain provenance to the direct term or terms that generated it.

## Three evaluation sets

For each protein `p` and aspect `a`, construct three conceptually different
sets.

### Scored direct truth

`D_score(p,a)` contains direct profile-accepted terms at `t1` that represent an
eligible knowledge-gain event and pass the benchmark eligibility policy.

The propagated candidate ground truth is:

```text
G_candidate(p,a) = C(D_score(p,a))
```

### Prior-known mask

`D_known0(p,a)` contains positive annotations that were publicly available at
`t0` under the chosen prior-knowledge policy. For a strict partial-knowledge
evaluation this should normally include every usable positive annotation at
`t0`, including IEA, because a predictor could already retrieve it.

```text
K0(p,a) = C(D_known0(p,a))
```

Terms in `K0` are never scored in the knowledge-gain evaluation, even when they
are also ancestors of a newly acquired term. Giving credit for them would reward
the predictor for restating knowledge already available at `t0`.

### Neutral truth mask

`D_neutral1(p,a)` contains terms accepted as true at `t1` but intentionally not
scored, for example a profile-accepted term removed only by an information or
specificity filter.

Its branch-exclusive neutral closure is:

```text
X1(p,a) = C(D_neutral1(p,a)) - C(D_score(p,a))
```

Subtracting the scored closure preserves ancestors shared with evaluated new
terms. This implements the intended behavior in the supplied diagrams when the
excluded branch is true at `t1` but was not prior knowledge at `t0`.

### Critical precedence rule

Prior-known and later-neutral annotations are not interchangeable:

```text
always masked:                 K0
masked unless shared by score: X1
```

If nodes 4 and 5 in the example were already known at `t0`, they must be masked
and cannot become true positives. If they are ancestors shared only with a
new-but-filtered term observed at `t1`, they remain scorable through the selected
new terms.

This distinction is the most important correction to the original single-
blacklist proposal.

## Global terms of interest and final per-target mask

For aspect `a`, define `Q(a)` as the global terms-of-interest set. It contains
active terms in the pinned ontology that were valid prediction targets and pass
global policy. Roots are normally excluded from metric calculations.

The final evaluable universe for a protein is:

```text
M(p,a) = Q(a) - K0(p,a) - X1(p,a)
```

The final scored ground truth is:

```text
T(p,a) = G_candidate(p,a) intersect M(p,a)
```

For a prediction threshold `tau`, propagate scores upward using the approved
relation policy and the maximum descendant score. Then define:

```text
P(p,a,tau) = propagated predictions at tau intersect M(p,a)
TP = P intersect T
FP = P - T
FN = T - P
```

A target-aspect with an empty `T(p,a)` after masking is not evaluable in that
profile and must be excluded with an explicit reason.

## Interpretation of the second graph example

If term 10 is predicted and its path is `10 -> 6 -> 4 -> 5`:

- term 10 is an FP if it is neither true nor masked;
- term 6 is ignored if it belongs to `M`'s excluded portion;
- terms 4 and 5 are TP only if they remain in `M` and in `T`;
- propagation does not stop at a masked node; masking applies after score
  propagation;
- a masked intermediate node must not prevent a prediction score from reaching a
  scorable ancestor.

This requires propagating scores first and applying the per-protein mask to the
resulting term scores, rather than deleting graph nodes before propagation.

## IEA policy

IEA requires separate treatment by timepoint.

### IEA present at t0

An IEA assertion available at the prediction cutoff is prior-accessible
knowledge. In partial-knowledge evaluation it should normally enter
`D_known0` and be masked. An exact IEA-to-higher-tier transition remains an
important benchmark event, but the exact term cannot simultaneously be both
masked and scored without choosing the scientific question.

Therefore publish two explicit profiles if this event is important:

- `novel_function`: masks all positive `t0` knowledge, including IEA; focuses on
  functions not already asserted at `t0`;
- `evidence_confirmation`: scores transitions that cross the active evidence
  profile threshold, including IEA-to-experimental transitions; it measures
  confirmation of a previous claim, not de novo prediction.

Do not mix these profiles into a single headline metric.

### IEA first appearing at t1

An IEA assertion first seen after the prediction cutoff was not prior knowledge
and is not direct experimental truth. In the primary strict benchmark it should
not automatically neutralize an FP. Offer a sensitivity profile in which such
assertions enter `D_neutral1`, and report how rankings change.

Neutralizing all `t1` IEA can favor tools that reproduce the same database
pipelines and can hide genuine false positives. This must remain configurable.

## Low-information and generic terms

Use a quantitative information threshold as the primary mechanism for removing
uninformative terms. Keep an optional manual GO-term file for expert overrides,
exceptional artifacts, or sensitivity experiments.

The filter configuration must contain:

- score type: `marginal_ic` or `total_information_accretion`;
- threshold value;
- optional thresholds per GO aspect;
- annotation corpus and evidence scope used to estimate the score;
- ontology and score-table checksums;
- optional manual include file;
- optional manual exclude file;
- whether filtering affects target eligibility, term scoring, or both.

Local IA for one node is the incremental information gained at that node. It is
not by itself the total specificity of the GO term. The recommended filtering
score is the total unique information over the term's inclusive closure:

```text
total_IA(g) = sum(IA(h) for h in C({g}) excluding roots)
```

Each ancestor is counted once even when multiple paths reach it. This total is
also the contribution of predicting or missing the complete propagated term in
CAFA-style semantic calculations. Marginal IC may be offered as an alternative,
but the chosen score type must be pinned in benchmark metadata.

Thresholds should normally be selected separately for MF, BP, and CC from
predefined distributional or scientific criteria. Do not select thresholds by
looking at predictor rankings.

CAFA3 removed proteins whose only newly gained annotation was `protein binding`,
rather than treating every occurrence of that term as proof that the entire
protein should be removed. A manual exclude file can reproduce this exact rule,
whereas the default ProBE policy can remain information-threshold based.

Recommended default:

1. Identify knowledge-gain events before the information filter.
2. Remove non-informative terms from `D_score`.
3. Place profile-accepted removed terms into `D_neutral1`.
4. Exclude the target-aspect if no scored truth remains.
5. Report the reason and the terms removed.

Do not neutralize descendants of a filtered term merely because the filtered term
is neutral. A descendant may be a specific unsupported prediction and should
remain an FP unless independently known to be true.

## Delayed imports and knowledge leakage

A row that first appears in `GOA1` is not necessarily newly discovered
biological knowledge. It may be a delayed migration from another database or an
older publication. CAFA3 explicitly removed a block of annotations that had been
public before the prediction deadline but migrated into GOA later.

For each candidate event retain and inspect:

- GOA assigned date;
- reference identifier and publication date when available;
- assigned-by source;
- source-database history;
- whether the assertion was publicly available outside GOA before `t0`.

Classify events as `new_to_GOA` and, where evidence permits,
`new_public_knowledge`. Use the latter for the strictest leakage-aware benchmark.
Events that cannot be resolved can remain in a standard GOA-delta profile but
must be flagged.

## Removed and contradictory annotations

Do not treat an annotation present at `t0` and absent at `t1` as a simple negative
or as reliable prior truth without qualification. Removal may reflect curation
correction, identifier migration, changed taxon, or changed annotation context.

Recommended handling:

- classify removals separately;
- exclude targets with material contradictory changes from the strict profile;
- define a quantitative annotation-loss threshold only after examining real
  distributions;
- retain the full audit trail.

A `NOT` assertion is not a neutral positive annotation. It is a contradiction
constraint. A prediction matching a valid `NOT` assertion should be reported as
contraindicated and normally count as an error under an explicit negative-truth
policy. Descendant propagation of `NOT` must use only relations for which the
semantics are valid.

## Metric focus

Report metrics independently for each GO aspect and benchmark stratum. Do not
combine MF, BP, and CC into one headline value.

### Primary metrics

- protein-centric macro `Fmax` over global score thresholds;
- `Smin` from remaining uncertainty and misinformation using information
  accretion;
- method coverage;
- number of evaluated target-aspects at the selected threshold.

Use full evaluation as the primary mode: all eligible benchmark proteins enter
recall and methods are penalized for missing targets. Partial evaluation can be
reported secondarily and must be clearly labeled.

### Secondary metrics

- weighted `Fmax` using information accretion;
- micro-averaged precision, recall, and F-score;
- precision-recall and RU-MI curves;
- fixed-threshold performance for calibration inspection;
- bootstrap confidence intervals sampled by protein;
- paired bootstrap differences for method comparisons.

A bootstrap confidence interval quantifies how much the reported score depends
on the particular proteins that happened to enter the benchmark. For example,
sample `N` benchmark proteins with replacement from the original `N` proteins,
recalculate `Fmax` or `Smin`, repeat this operation a prespecified number of
times, and report the 2.5th and 97.5th percentiles. This does not change term
selection or the primary score. It is an optional statistical uncertainty layer
and can be implemented after the core benchmark and metrics are stable.

Always report performance separately for at least:

- `NK_GLOBAL`;
- `NK_ASPECT`;
- `LK_BRANCH`;
- `LK_REFINEMENT`;
- `LK_UPGRADE` or `EVIDENCE_CONFIRMATION`.

Combining these strata can obscure large differences in difficulty.

## Information content and information accretion

The existing `GeneOntology.information_content()` computes a smoothed marginal
quantity based on cumulative term frequency relative to the namespace root. It
can be useful for descriptive analyses or a configurable specificity filter.

It must not silently be used as CAFA information accretion for `Smin` or weighted
CAFA metrics.

For CAFA-style semantic evaluation, use a pinned information-accretion table or
implement the documented conditional calculation:

```text
IA(g) = -log2 P(g | parents(g))
```

The annotation corpus, evidence scope, taxonomic scope, ontology snapshot,
propagation relations, smoothing, logarithm base, and checksum of the resulting
table are part of benchmark provenance.

Avoid calculating weights from only the benchmark targets because this can make
the metric unstable and benchmark-dependent. Use a predefined external annotation
corpus associated with the benchmark release.

Keep these concepts separate in code:

```text
marginal_information_content
information_accretion
benchmark_specificity_filter
semantic_metric_weights
total_information_accretion
```

## Metric aggregation requirements

At each global threshold `tau`:

1. Apply score validation and duplicate consolidation.
2. Propagate prediction scores using maximum score to approved ancestors.
3. Apply global terms of interest.
4. Apply the protein-specific evaluation mask.
5. Compute per-protein TP, FP, FN or their IA-weighted equivalents.
6. Macro-average per-protein precision and recall using the declared full or
   partial normalization.
7. Compute F-score, RU, MI, and semantic distance.

Do not choose an independent optimal threshold per protein. `Fmax` and `Smin`
select one global threshold per method, aspect, evaluation profile, and stratum.

Exclude ontology roots from metric counts unless a named compatibility profile
requires otherwise. Roots normally carry zero information and can inflate
unweighted scores.

## Prediction normalization

For prediction input:

- require scores in `[0,1]` or apply an explicitly declared transformation;
- merge duplicate target-term predictions using maximum score;
- resolve alternate IDs against `O`;
- report obsolete, unknown, cross-aspect, and invalid terms;
- propagate scores by maximum score to ancestors;
- do not silently discard invalid predictions without reporting their count;
- do not treat ontology terms unavailable at the prediction cutoff as FN.

A monotonic score transformation does not change ranking-based curves but can
change fixed-threshold and calibration analyses. Preserve both raw and normalized
scores when normalization is performed.

## Output model

Do not store the ground truth as one wide row with one BP, MF, and CC column.
Each protein may have many terms and many evidence records. Use normalized tables
or equivalent immutable records.

### Target table

```text
target_id
sequence_id
sequence_sha256
sequence_length
target_taxon_id
identity_status
```

### Alias table

```text
sequence_id
accession
source_database
source_release
taxon_id
canonical_or_isoform
reviewed_status
```

### Assertion table

```text
sequence_id
snapshot
aspect
term_id_original
term_id_canonical
relation
qualifiers
evidence_code
reference
with_from
annotation_extension
gene_product_form
assigned_by
annotation_date
source_line
```

### Benchmark event table

```text
sequence_id
target_id
aspect
term_id
prior_knowledge_state
event_type
reporting_category
qualifies
exclusion_reasons
old_evidence_codes
new_evidence_codes
```

### Evaluation mask table

```text
target_id
aspect
term_id
mask_kind
source_direct_terms
reason
```

Valid `mask_kind` values include `prior_known`, `neutral_new_truth`,
`global_out_of_scope`, `root`, and `invalid_or_unmappable`.

### Ground-truth table

```text
target_id
aspect
term_id
is_direct
source_direct_terms
event_types
reporting_categories
```

## Relationship to the current codebase

The current branch already provides useful foundations:

- one shared `GeneOntology` object is required for old and new snapshots;
- exact term acquisition and evidence upgrades are represented;
- evidence is stored as a set;
- navigable relations are separated from annotation-propagation relations;
- `is_a` and `part_of` are the conservative propagation default;
- alternate, replaced, obsolete, and unknown terms are represented;
- `NOT` handling and closure utilities have tests.

The next implementation should extend these foundations rather than replace them.

Recommended additions:

```text
src/probe/knowledge.py       prior state and event classification
src/probe/selection.py       eligibility and scored direct truth
src/probe/masking.py         K0, X1, Q, M construction
src/probe/information.py     IC and IA with distinct APIs
src/probe/evaluation.py      prediction propagation and metrics
src/probe/export.py          normalized benchmark artifacts
```

Do not add all modules at once. The suggested order is event classification,
mask construction, synthetic end-to-end tests, information accretion, then
metrics.

## Required synthetic test cases

Codex should implement small DAG fixtures with hand-computed expected sets.

### Selection tests

1. No prior annotation anywhere, new profile-accepted term: `NK_GLOBAL`.
2. No prior annotation in BP but prior MF annotation: BP `NK_ASPECT`.
3. Prior accepted BP term, new unrelated accepted BP branch: `LK_BRANCH`.
4. Prior accepted ancestor, new accepted descendant:
   `LK_REFINEMENT`.
5. Prior accepted descendant, newly asserted ancestor:
   `REDUNDANT_ANCESTOR`, not selected.
6. Exact IEA-to-experimental transition: `EVIDENCE_UPGRADE`.
7. Old assertion already has IEA and IMP; new assertion has IDA: not a first
   experimental upgrade.
8. Relation-only change: context change, not automatic acquisition.
9. Ambiguous obsolete replacement: quarantined.
10. Same sequence with aliases across releases: one biological event.
11. Multiple evidence codes retain the complete set and use the best usable tier.
12. `NAS` and `ND` alone never satisfy an evidence profile.
13. Expanding from `experimental_strict` to `experimental_all` only adds
    high-throughput-supported events.

### Mask tests

1. Prior-known branch remains excluded even where it overlaps the new closure.
2. New low-information true branch is neutralized.
3. Ancestors shared between scored and later-neutral new branches remain scored.
4. A predicted descendant of a neutral term remains FP unless independently
   neutral or true.
5. A masked intermediate node does not block score propagation to a scorable
   ancestor.
6. Empty truth after masking excludes the target-aspect with a reason.
7. `t0` IEA is masked in `novel_function_strict` but scored as an upgrade in
   `evidence_confirmation`.
8. A term below the total-IA threshold is neutralized and its reason recorded.
9. A manual include override can retain a below-threshold term.
10. A manual exclude override removes a term independently of its information
    score.

### Metric tests

1. Exact perfect prediction yields `Fmax=1` and `Smin=0`.
2. No prediction in full mode reduces recall and coverage.
3. A masked prediction changes neither TP, FP, FN, RU, nor MI.
4. An unmasked wrong term contributes FP and misinformation.
5. A missed high-IA term contributes more RU than a missed low-IA term.
6. Global thresholding is used, not a per-protein optimum.
7. MF, BP, and CC are evaluated independently.
8. Duplicate predictions use maximum score.
9. Roots do not inflate scores.
10. Results are invariant to input row order.

## Decisions still requiring project approval

The following choices should be configuration profiles or explicit decisions,
not hidden defaults:

1. Whether `O` is pinned at `t0` or another declared date.
2. Which positive evidence categories enter prior-known masking at `t0`.
3. Whether IEA first appearing at `t1` enters the neutral truth mask.
4. Which cumulative evidence profile is active for each exported benchmark.
5. The IA or IC threshold for each aspect and any manual include/exclude files.
6. Which corpus and formula produce marginal IC and CAFA IA.
7. How much annotation loss or contradiction excludes a target.
8. Which NCBI taxonomic rank defines an acceptable multi-species exact-sequence
   match when no authoritative target taxon is available.
9. Whether ambiguous taxon identity excludes a target or creates stratified
   records.
10. Whether headline evaluation is full mode only or full plus partial mode.

## Recommended primary profile

For a defensible first implementation:

```yaml
profile: novel_function_strict
ontology: pinned_at_prediction_cutoff
aspects: [MF, BP, CC]
propagation_relations: [is_a, part_of]
evidence_profile: experimental_strict
accepted_new_evidence:
  - EXP
  - IDA
  - IPI
  - IMP
  - IGI
  - IEP
prior_known_mask: all_positive_t0_annotations
score_threshold_crossing_upgrades: false
neutralize_excluded_new_experimental_terms: true
neutralize_new_iea_at_t1: false
informativeness_filter:
  score: total_information_accretion
  thresholds_by_aspect:
    MF: null
    BP: null
    CC: null
  manual_include_file: null
  manual_exclude_file: null
exclude_roots_from_metrics: true
primary_metrics: [Fmax, Smin, coverage]
evaluation_mode: full
```

Add an `experimental_all` profile when traditional experimental evidence yields
too few targets. Expand subsequently to `curated_phylogenetic`,
`curated_traceable`, and `curated_non_electronic`, always publishing the tier
composition. Add a separate `evidence_confirmation` profile for threshold-
crossing upgrades and a sensitivity profile that neutralizes positive IEA
observed at `t1`.

## Sources informing this specification

- GO Consortium, Relations in the Gene Ontology:
  https://geneontology.org/docs/ontology-relations/
- GO Consortium, Guide to GO evidence codes:
  https://geneontology.org/docs/guide-go-evidence-codes/
- Zhou et al., CAFA3 benchmark collection and protein-centric evaluation:
  https://pmc.ncbi.nlm.nih.gov/articles/PMC6864930/
- CAFA-evaluator reference implementation:
  https://github.com/BioComputingUP/CAFA-evaluator
- CAFA4+ partial-knowledge evaluator, including terms of interest and
  protein-specific known annotations:
  https://github.com/claradepaolis/CAFA-evaluator-PK

## Implementation handoff instruction for Codex

Codex should treat this document as a scientific specification, not as
authorization to implement every component in one change. It should first map
each requirement to the current public API, identify conflicts, propose the
smallest coherent milestone, add hand-computed synthetic tests, and only then
modify production code.
