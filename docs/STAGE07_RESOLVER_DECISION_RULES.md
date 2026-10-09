# Stage 07 Resolver Decision Rules

## Status

**Scientific design: frozen after full-run audit.**
**Implementation: completed, production-integrated, and validated.**

This document defines the scientific decision model for the ProBE candidate-pipeline-v2 Stage 07 resolver. Stages 01–06C1 are treated as frozen pre-resolver evidence production stages.

The resolver does not infer biological identity from sequence similarity alone. Its purpose is to determine whether a UniProt candidate can be **associated with a target for annotation enrichment**, while preserving uncertainty, documentary provenance, taxonomy policy, sequence geometry, and candidate multiplicity.

---

## 1. Core principles

1. **Taxonomy is a gate.** It is not a soft similarity score.
2. **Direct UniProt accession continuity is strong documentary evidence.**
3. **Sequence revision does not invalidate accession continuity.** A stable UniProt accession may undergo substantial sequence revision because of curation, record replacement, record merging, or other release-history events.
4. **Exact sequence equality is evidence, not automatic accession identity.**
5. **A locus match is positive evidence. A locus string non-match is not automatically negative evidence.**
6. **ORDERED_LOCUS and ORF identifiers are not universally standardized or guaranteed to be unique.**
7. **Generic ontology-like identifiers are not identifying locus keys.**
8. **Stage 04D1 bucket-aware collision semantics are authoritative.**
9. **Global key fanout is descriptive, not an identity rule.**
10. **UNRESOLVED is not equivalent to DIFFERENT_ENTITY.**
11. **The resolver must never choose an arbitrary best accession when multiple candidates are acceptable.**
12. **Association and annotation-donor eligibility are separate concepts.**
13. **No percentage-identity, mismatch-count, gap-size, alignment-score, or length-ratio threshold is used to decide association.**
14. **The output unit is `(release, target_id, rid)`.**
15. **Full UniProt-record or full-product equivalence must not be over-asserted.**

---

## 2. Evidence namespaces

The resolver consumes evidence already produced by the frozen pipeline:

- identity and target metadata;
- taxonomy context;
- sequence-discovery provenance;
- strict-rescue provenance;
- lineage/documentary provenance;
- direct-accession anchors;
- candidate metadata;
- exact literal sequence equality;
- canonical TARGET_GLOBAL and CANDIDATE_GLOBAL alignment evidence;
- taxonomy-audit evidence.

Missing/not-applicable values must remain distinct from explicit false evidence.

---

## 3. Taxonomy-audit namespace

Stage 05C2 is intentionally separate from the PRIMARY candidate universe.

### 3.1 CROSS_SPECIES

Default behavior:

- `scope = TAXONOMY_AUDIT`
- `decision = AUDIT_ONLY`
- `audit_outcome = REJECTED_BY_TAXONOMY_POLICY`
- `annotation_donor = NO`

Cross-species transfer is not user-configurable in v1.

### 3.2 SAME_SPECIES_WRONG_SUBTAXON

Default strict behavior:

- `scope = TAXONOMY_AUDIT`
- `decision = AUDIT_ONLY`
- `audit_outcome = EXCLUDED_FROM_PRIMARY_BY_SUBTAXON_POLICY`
- `annotation_donor = NO`

An optional audited-subtaxon policy is described in Section 11.

---

## 4. Direct accession continuity

A taxonomically eligible direct PRIMARY or SECONDARY accession anchor provides documentary continuity.

### 4.1 Exact direct PRIMARY

- `decision = ACCEPTED`
- `decision_basis = DIRECT_PRIMARY_ACCESSION`
- `entity_equivalence = DOCUMENTARY_ACCESSION_CONTINUITY`

### 4.2 Exact direct SECONDARY

- `decision = ACCEPTED`
- `decision_basis = DIRECT_SECONDARY_ACCESSION`
- `entity_equivalence = DOCUMENTARY_ACCESSION_CONTINUITY`

### 4.3 Nonexact direct accession

- `decision = ACCEPTED`
- `decision_basis = DIRECT_ACCESSION_CONTINUITY`
- `entity_equivalence = DOCUMENTARY_ACCESSION_CONTINUITY`
- warning: `ACCESSION_CONTINUITY_SEQUENCE_CHANGED`

The observed sequence geometry does not revoke direct documentary continuity.

The direct-anchor population may be used as an empirical positive reference for sequence revisions observed under known continuity, but it must **not** be converted into a universal sequence-divergence threshold.

---

## 5. Nondirect documentary ACCESSION evidence

A lineage ACCESSION key must not be silently equated with a direct accession anchor.

### 5.1 Nondirect ACCESSION + exact sequence

- `decision = ACCEPTED`
- `decision_basis = DOCUMENTARY_ACCESSION_PLUS_EXACT_SEQUENCE`
- `entity_equivalence = NOT_ASSERTED`

### 5.2 Nondirect ACCESSION + nonexact sequence

**Precedence constraint:** this rule applies only when there is no qualifying stable unique locus. When such locus evidence exists, evaluate the stable-locus rules first, including their nonexact sequence geometries. Exact nondirect ACCESSION evidence retains its earlier precedence.

- `decision = UNRESOLVED`
- `decision_basis = NONDIRECT_DOCUMENTARY_ACCESSION`
- warning: `NONDIRECT_ACCESSION_WITH_NONEXACT_SEQUENCE`

---

## 6. Stable unique locus

For Stage 07 v1, a stable unique locus requires all of the following:

1. matched key type is `ORDERED_LOCUS` or `ORF`;
2. the key is not ontology-like/generic;
3. `key_collision = 0` under Stage 04D1 bucket-aware semantics;
4. the key is supported by eligible direct-anchor evidence from both reference snapshots;
5. candidate taxonomy satisfies the resolver taxonomy policy.

The START+END implementation is evidence for the more general principle of **independent documentary locus continuity across reference snapshots**.

A different locus string is not automatically evidence of a different biological locus.

### 6.1 Stable locus + exact sequence

- `decision = ACCEPTED`
- `decision_basis = STABLE_UNIQUE_LOCUS_PLUS_EXACT_SEQUENCE`
- `entity_equivalence = NOT_ASSERTED`

### 6.2 Stable locus + exact contiguous candidate subsequence

Requirements:

- candidate shorter than target;
- exact resolved TaxID under the frozen v1 rule;
- CANDIDATE_GLOBAL spans the entire candidate;
- zero mismatches;
- zero aligned gap residues;
- the aligned candidate is an exact contiguous subsequence of the target.

Decision:

- `decision = ACCEPTED`
- `decision_basis = STABLE_UNIQUE_LOCUS_EXACT_SUBSEQUENCE`
- `entity_equivalence = NOT_ASSERTED`
- warning: `PARTIAL_SEQUENCE_EXACTLY_CONTAINED_IN_TARGET`

This rule accepts the association for annotation enrichment. It does not assert full-product equivalence.

### 6.3 Stable locus + equal-length substitution-only variant

Requirements:

- exact resolved TaxID under the frozen v1 rule;
- target length equals candidate length;
- TARGET_GLOBAL has no gaps;
- TARGET_GLOBAL contains one or more substitutions;
- no numeric identity or mismatch threshold.

Decision:

- `decision = ACCEPTED`
- `decision_basis = STABLE_UNIQUE_LOCUS_EQUAL_LENGTH_VARIANT`
- `entity_equivalence = NOT_ASSERTED`
- warning: `SEQUENCE_VARIANT`

### 6.4 Stable locus + any other nonexact sequence relation

Examples include:

- noncontiguous indel-only relations;
- mixed substitutions/indels;
- longer candidate products;
- other geometries not covered by a positive rule.

Decision:

- `decision = UNRESOLVED`
- `decision_basis = STABLE_UNIQUE_LOCUS_OTHER_SEQUENCE_RELATION`
- warning: `STABLE_LOCUS_SEQUENCE_RELATION_UNRESOLVED`

These candidates are retained for investigation. They are not automatically classified as different entities.

---

## 7. Exact sequence without strong documentary continuity

### 7.1 EXACT_SEQUENCE_ONLY

- `decision = UNRESOLVED`
- `decision_basis = EXACT_SEQUENCE_ONLY`
- warning: `EXACT_SEQUENCE_WITHOUT_STRONG_DOCUMENTARY_CONTINUITY`

Exact physical sequence equality is insufficient to select a specific UniProt accession when parallel records may exist.

### 7.2 Exact sequence with only weak/colliding documentary evidence

- `decision = UNRESOLVED`
- `decision_basis = WEAK_OR_COLLIDING_DOCUMENTARY_PLUS_EXACT_SEQUENCE`
- warning: `EXACT_SEQUENCE_WITH_ONLY_WEAK_DOCUMENTARY_SUPPORT`

---

## 8. Nonexact similarity without strong documentary continuity

### 8.1 SEQUENCE_ONLY

- `decision = UNRESOLVED`
- `decision_basis = SEQUENCE_ONLY`
- warning: `NO_STRONG_DOCUMENTARY_CONTINUITY`

### 8.2 Weak or colliding lineage

- `decision = UNRESOLVED`
- `decision_basis = WEAK_OR_COLLIDING_LINEAGE`
- warning: `WEAK_OR_COLLIDING_LINEAGE`

Sequence similarity is retained as evidence but does not independently establish an automatic annotation association.

---

## 9. Fragment policy

### 9.1 Candidate discovery

Frozen rule:

`FRAG=1 + similarity-only -> EXCLUDE`

Stage 03 and Stage 05R1 must continue to exclude similarity-only fragment records.

### 9.2 Documentary fragment evidence

A fragment discovered through independent documentary/lineage evidence is retained and may be resolved normally.

If accepted, add:

- warning: `FRAGMENT_RECORD`

Whether an already-accepted documentary fragment may be used as an annotation donor is a separate user policy; see Section 11.

---

## 10. Candidate multiplicity

The resolver must emit one row per:

`(release, target_id, rid)`

It must never collapse accepted candidates to a single “best” accession.

When more than one candidate is accepted for the same `(release, target_id)`:

- preserve every accepted ACCID;
- add warning: `MULTIPLE_ACCEPTED_CANDIDATES`;
- expose `accepted_candidates_for_target_release`.

A downstream annotation-enrichment policy may decide whether all accepted candidates are automatic donors, but that policy must not change the resolver decision.

---

## 11. User-configurable policies

Configuration must remain intentionally small. The scientific evidence hierarchy itself is not user-tunable.

### 11.1 Resolver subtaxon policy

Recommended v1 interface:

```yaml
resolver:
  subtaxon_policy: strict
```

#### `strict` — default

Preserve the current frozen PRIMARY universe.

`SAME_SPECIES_WRONG_SUBTAXON` remains:

- `scope = TAXONOMY_AUDIT`
- `decision = AUDIT_ONLY`

#### `include_audited_same_species`

Promote only the already-explicit, already-audited Stage 05C2 `SAME_SPECIES_WRONG_SUBTAXON` rows into resolver evaluation, then apply the same decision rules used for PRIMARY candidates.

Important limitation:

Stage 07 cannot retroactively broaden Stage 03/05R1 candidate discovery.

Therefore `include_audited_same_species` must **not** be described as “search all same-species subtaxa”.

A future globally broadened same-species candidate-search mode would require a separately validated upstream taxonomy/candidate-discovery configuration.

Cross-species inclusion is not configurable in v1.

### 11.2 Partial-sequence annotation donor policy

This policy changes donor eligibility, not resolver association.

```yaml
annotation_donor:
  partial_sequence: allow
```

Values:

- `allow` — default: an accepted `STABLE_UNIQUE_LOCUS_EXACT_SUBSEQUENCE` candidate may be an annotation donor;
- `report_only`: candidate remains `ACCEPTED`, but `annotation_donor = NO`.

### 11.3 Documentary-fragment annotation donor policy

This policy applies only to fragment records that reached the resolver through independent documentary evidence.

```yaml
annotation_donor:
  documentary_fragment: allow
```

Values:

- `allow` — default: an accepted documentary fragment may be a donor;
- `report_only`: candidate remains `ACCEPTED`, but `annotation_donor = NO`.

This setting must never re-enable Stage 03/05R1 similarity-only fragment discovery.

### 11.4 Multiple accepted donor policy

Preferably owned by the downstream annotation-enrichment stage, not by the resolver itself.

```yaml
multiple_accepted:
  donor_policy: all
```

Values:

- `all` — default: all accepted candidates may contribute annotations;
- `unique_only`: preserve all accepted ACCIDs, but do not perform automatic annotation transfer when a target/release has more than one accepted candidate.

The resolver decision remains unchanged.

---

## 12. Non-configurable scientific invariants

The following must not become user-tunable v1 parameters:

- sequence identity threshold;
- minimum alignment score;
- maximum mismatch count;
- maximum gap length;
- length-ratio threshold;
- lineage collision multiplicity cutoff;
- automatic acceptance of exact sequence;
- acceptance/rejection of SECONDARY direct accession evidence;
- cross-species transfer;
- best-candidate selection among accepted ACCIDs;
- generic ontology-like key acceptance;
- similarity-only fragment inclusion.

These would turn the resolver into an empirical threshold classifier rather than an evidence-based decision system.

---

## 13. Decision vocabulary

### PRIMARY v1

- `ACCEPTED`
- `UNRESOLVED`

### TAXONOMY_AUDIT v1

- `AUDIT_ONLY`

The conceptual model may reserve `REJECTED` for future cases where independent evidence positively establishes incompatibility, but Stage 07 v1 must not invent a PRIMARY rejection rule.

`CROSS_SPECIES` is rejected by taxonomy policy inside the `AUDIT_ONLY` namespace.

---

## 14. Entity-equivalence vocabulary

Recommended values:

- `DOCUMENTARY_ACCESSION_CONTINUITY`
- `NOT_ASSERTED`
- `NOT_APPLICABLE`

Association acceptance does not necessarily mean complete UniProt-record or product equivalence.

---

## 15. Warning vocabulary

Frozen/validated warning concepts:

- `ACCESSION_CONTINUITY_SEQUENCE_CHANGED`
- `COLLIDING_DOCUMENTARY_KEY_PRESENT`
- `CROSS_SPECIES`
- `EXACT_SEQUENCE_WITHOUT_STRONG_DOCUMENTARY_CONTINUITY`
- `EXACT_SEQUENCE_WITH_ONLY_WEAK_DOCUMENTARY_SUPPORT`
- `FRAGMENT_RECORD`
- `MULTIPLE_ACCEPTED_CANDIDATES`
- `NONDIRECT_ACCESSION_WITH_NONEXACT_SEQUENCE`
- `NO_STRONG_DOCUMENTARY_CONTINUITY`
- `PARTIAL_SEQUENCE_EXACTLY_CONTAINED_IN_TARGET`
- `SAME_SPECIES_WRONG_SUBTAXON`
- `SEQUENCE_VARIANT`
- `STABLE_LOCUS_SEQUENCE_RELATION_UNRESOLVED`
- `WEAK_OR_COLLIDING_LINEAGE`

More specific warnings such as `MAJOR_SEQUENCE_REVISION` should not be introduced until they can be defined without arbitrary numeric thresholds.

---

## 16. Proposed Stage 07 output concepts

The final production schema must be specified before implementation, but the core fields should include:

- `release`
- `target_id`
- `rid`
- `scope`
- `target_accession`
- `candidate_accession`
- `exact_sequence`
- `decision`
- `decision_basis`
- `entity_equivalence`
- `annotation_donor`
- `sequence_relation`
- `accepted_candidates_for_target_release`
- `warnings`
- `audit_class`
- `audit_reason`
- `audit_outcome`
- `audit_alignment_allowed`

The final filename, manifest contract, summary JSON, ordering, null representation, and exact schema must be frozen before production code is written.

---

## 17. Full-run validation reference

Canonical run:

`/data/PROGENY/ProBE-runs/pre-resolver-v2-2023_01-2026_03`

Evidence universe:

- PRIMARY: 984,054
- TAXONOMY_AUDIT: 25
- TOTAL: 984,079

PRIMARY exactness:

- EXACT: 319,373
- NONEXACT: 664,681

Final dry-run:

- ACCEPTED: 298,498
- UNRESOLVED: 685,556
- REJECTED: 0
- AUDIT_ONLY: 25

Decision-basis counts:

- `DIRECT_ACCESSION_CONTINUITY`: 952
- `DIRECT_PRIMARY_ACCESSION`: 282,681
- `DIRECT_SECONDARY_ACCESSION`: 8
- `DOCUMENTARY_ACCESSION_PLUS_EXACT_SEQUENCE`: 2,928
- `EXACT_SEQUENCE_ONLY`: 30,747
- `NONDIRECT_DOCUMENTARY_ACCESSION`: 4,092
- `SEQUENCE_ONLY`: 621,678
- `STABLE_UNIQUE_LOCUS_EQUAL_LENGTH_VARIANT`: 1,034
- `STABLE_UNIQUE_LOCUS_EXACT_SUBSEQUENCE`: 7,912
- `STABLE_UNIQUE_LOCUS_OTHER_SEQUENCE_RELATION`: 23,324
- `STABLE_UNIQUE_LOCUS_PLUS_EXACT_SEQUENCE`: 2,983
- `WEAK_OR_COLLIDING_DOCUMENTARY_PLUS_EXACT_SEQUENCE`: 26
- `WEAK_OR_COLLIDING_LINEAGE`: 5,689

Taxonomy audit:

- `CROSS_SPECIES`: 1
- `SAME_SPECIES_WRONG_SUBTAXON`: 24

Multiplicity:

- 10,848 target/release groups have more than one accepted candidate;
- maximum observed accepted multiplicity: 44.

Coverage checks:

- classified rows: 984,079
- unclassified rows: 0
- PRIMARY duplicate keys: 0
- PRIMARY/AUDIT key overlap: 0

These counts validate the rules on this run; they are not themselves universal biological constants.

---

## 18. Generality and dataset dependence

### General scientific structure

The following are intended to be general:

- evidence hierarchy;
- taxonomy gate;
- documentary accession continuity;
- exact sequence not implying automatic accession identity;
- positive locus evidence without treating string non-match as conflict;
- collision-aware documentary interpretation;
- separation of association from donor eligibility;
- explicit unresolved state;
- preservation of multiple accepted accessions;
- avoidance of empirical sequence cutoffs.

### Dataset-specific observations

The following are specific to the canonical validation run:

- class counts;
- class frequencies;
- individual examples;
- observed maximum multiplicities;
- the use of exactly two snapshots, START and END, as the concrete implementation of independent temporal documentary support.

The resolver should encode the general evidence model, not memorize validation-run frequencies.

---

## 19. Implementation constraints

Before writing Stage 07 production code:

1. freeze the input contract;
2. freeze the configuration contract;
3. freeze rule precedence;
4. freeze output ordering and null semantics;
5. freeze the warning representation;
6. freeze summary JSON contents;
7. define synthetic unit tests for every rule and precedence interaction;
8. define full-run acceptance counts;
9. preserve all pre-resolver artifacts unchanged.

No implementation should modify Stages 01–06C1 merely to simplify Stage 07.
