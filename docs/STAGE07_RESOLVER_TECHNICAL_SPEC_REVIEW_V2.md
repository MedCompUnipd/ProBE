# Stage 07 — Resolver Technical Specification

**Status:** REVIEW V2 — technical reconciliation in progress; **not approved for implementation**.
**Scientific authority:** `docs/STAGE07_RESOLVER_DECISION_RULES.md`.
**Upstream boundary:** Stages 01–06C1 are production-integrated and frozen.
**Version:** proposed technical contract `stage07-resolver-v1`.

## 1. Scope and contract

Stage 07 is a deterministic **common** scientific stage. It classifies every candidate key `(release, target_id, rid)` in the disjoint union of Stage 05A2 PRIMARY and Stage 05C2 TAXONOMY_AUDIT, using independently generated documentary, taxonomy and directional alignment evidence. It makes *annotation-enrichment associations*, not assertions that two full biological products are identical. It never selects a single best ACCID, performs similarity discovery, expands lineage, recomputes alignments, or introduces empirical sequence cutoffs.

The resolver's scientific decision is independent of downstream donor eligibility. `UNRESOLVED` does not mean `DIFFERENT_ENTITY`. The default policy must reproduce the frozen decision dry-run; dataset-specific counts are regression checks, never programmed thresholds.

## 2. Upstream bundles and minimum logical data

All inputs are validated stage bundles, never TESTING runtime dependencies. Input paths are obtained by inspecting the upstream manifest's declared outputs; each bundle is verified with `validate_bundle(..., deep=False)` before use. Verify the common START/END release IDs and matching ontology provenance where recorded; null ontology provenance may be valid and must not be synthesized.

| Producer | Required file | Logical purpose |
|---|---|---|
| 01 common | `01_targets.tsv` | target ID, optional accession and literal sequence length |
| 02B START+END | `02B_target_taxonomy.tsv` | target resolved TaxID, rank, species and bucket |
| 04B START+END | `04B_classified_direct_anchors.tsv` | direct PRIMARY/SECONDARY match and taxonomy eligibility |
| 04D1 common | `04D1_lineage_scan_index.tsv` | authoritative release/bucket/key collision semantics |
| 04D2 START+END | `04D2_lineage_evidence.tsv` | individual typed matched documentary keys, source provenance, collision and candidate record |
| 05A2 common | `05A2_final_candidate_union.tsv` | PRIMARY candidate population and discovery provenance |
| 05C2 common | `05C2_taxonomy_audit.tsv` | separate taxonomy-audit population, including unaligned CROSS_SPECIES |
| 06A common | `06A_alignment_jobs.tsv` | exactness, job scope, lengths and candidate record metadata |
| 06C1 common | `06C1_canonical_directional_glocal.tsv` | both canonical alignment directions for each allowed job |

The above is a **conservative logical dependency proposal**. Stage 04C and 04E are not necessarily additional runtime inputs because key provenance is present in 04D2 and collapsed lineage context in 05A2. Stage 05D may be needed if a direct literal sequence check is selected during technical review; 06A plus 06C1 already provide the validated exactness/geometry for the frozen rules. Do not discard or rederive upstream evidence casually.

Join identities are `(release, target_id, rid)` for candidates, `(release, target_id)` for target taxonomy, and `(release, target_id, primary_bucket, key_type, key_value)` for 04D1 collision context. Stage 04D2 may contain multiple rows for a single candidate key; aggregate *only after* evaluating each distinct typed key individually. Never join using `rid` without `release`; record ordinals are release-specific.

Mandatory relational checks: 05A2 keys unique, 05C2 keys unique, no 05A2/05C2 overlap, all 06A and 06C1 job keys unique, every PRIMARY key has one matching 06A+06C1 record, every audit row with `audit_alignment_allowed=1` has one matching 06A+06C1 record, audit rows with `audit_alignment_allowed=0` have neither, and no alignment/job key exists outside the permitted union. Fail closed on mismatches, rather than silently dropping rows or fabricating absent geometry. Validate candidate accession and exactness consistency across joins.

## 3. Configuration contract

Suggested additive, strictly validated **top-level** sections (keep frozen `scientific` untouched):

```yaml
resolver:
  subtaxon_policy: strict  # strict | include_audited_same_species
annotation_donor:
  partial_sequence: allow        # allow | report_only
  documentary_fragment: allow   # allow | report_only
```

Defaults: `strict`, `allow`, `allow`. Unknown keys/values fail closed. **C1 proposal settled for review:** retain existing `schema_version: 1`; accept `resolver` and `annotation_donor` as *optional* additions to the top-level schema, filling their defaults in the validated normalized mapping when absent. Require exact allowed keys inside either supplied section; reject unknown fields and invalid values. Do not change the frozen `scientific` mapping or the interpretation of Stage 01–06C1 inputs. This provides old-config compatibility and reproducible default Stage 07 behavior. Test both omitted and explicitly supplied default sections. Record an explicit `stage07-resolver-v1` policy version in the Stage 07 bundle.

**C2 semantic contract (nondefault):** `include_audited_same_species` changes the Stage 07 taxonomy gate **only for materialized Stage 05C2 rows whose `key_source_reason=SAME_SPECIES_WRONG_SUBTAXON` and `audit_alignment_allowed=1`**. It cannot widen Stage 03/05R1 searches or fabricate lineage evidence. `CROSS_SPECIES` is never promoted. For promoted rows, verify the actual 05C2 direct PRIMARY/SECONDARY anchor and matching 04B provenance, rather than relying on a coincidentally equal accession string. The older 04B `key_source_eligible=0` is an upstream *strict key-donation policy* verdict and is not silently changed; promotion is an explicit **Stage 07-only** gate override based on the independently recorded audit reason. A confirmed exact direct accession then follows the *same* `DIRECT_PRIMARY_ACCESSION` or `DIRECT_SECONDARY_ACCESSION` acceptance rule as a primary candidate, even if resolved subtaxon differs. Association does not depend on exact resolved TaxID in this direct-continuity rule; the two stable-locus nonexact positive geometries **continue** to require exact TaxID equality. Donor eligibility is computed after acceptance by the unchanged donor policies, not granted by promotion alone. The observed 24 audit rows are exact and have direct accession types; the expected result in this particular nondefault run is therefore 24 accepted associations, **subject to row-level source-join verification**. Preserve their original audit origin in all cases.

The `multiple_accepted.donor_policy` setting belongs to downstream enrichment, not Stage 07 v1; Stage 07 outputs every accepted candidate and its per-target count. No user controls may govern identity, mismatch count, score, gap size, length ratio, collision cutoffs, cross-species transfer, best candidate selection, or similarity-only fragment inclusion.

## 4. Deterministic evidence predicates

Evaluate boolean predicates only with explicitly validated input values; missing/invalid fields are not false. `exact_sequence` is the literal exactness flag validated through 06A, not an alignment-score cutoff.

- `direct_primary` / `direct_secondary`: a matching direct anchor from 04B, with `direct_match_type=PRIMARY|SECONDARY`, candidate `(release,target_id,rid)` identity and eligibility under the applicable resolver taxonomy gate. Nonmatching string alone is not negative evidence.
- `nondirect_accession`: typed `ACCESSION` documentary evidence from 04D2 attached to the same candidate, **not** merely a direct 04B anchor. Keep direct and nondirect namespaces distinct.
- `stable_unique_locus`: there exists **one and the same** typed `(ORDERED_LOCUS|ORF, key_value)` supported by individual matched evidence for this pair, with `from_start_anchor=1` **and** `from_end_anchor=1`, `key_collision=0` in authoritative 04D1 context, non-generic/non-ontology-like key, and compatible taxonomy. Merely having an unrelated START key and END key is insufficient. High global fanout alone does not disqualify the key.
- `exact_resolved_taxid`: target and candidate resolution is valid and both resolved TaxIDs are equal (not merely their species ancestor).
- `exact_candidate_contiguous_subsequence`: `candidate_length < target_length`; canonical `CANDIDATE_GLOBAL` covers the **entire** candidate; mismatches=0 and both gap-residue counts=0; aligned spans and coordinate arithmetic prove a contiguous target segment of equal size. A zero-mismatch local segment that omits candidate residues is insufficient.
- `equal_length_substitutions_only`: positive equal target/candidate length; `TARGET_GLOBAL` complete and ungapped (both gap-residue counts=0), with mismatches >=1 and aligned pair/coordinate consistency. Do not apply a maximum mismatch threshold.
- `weak_or_colliding_documentary`: a lineage/documentary match is present but no stronger accepted predicate applies; collision is determined from 04D1, not global fanout. Generic token filtering must be deterministic and auditable.

**C3 recognizer contract (pending precise freeze):** evaluate an individual `(key_type,key_value)` after reversible decoding of upstream percent-encoded key representation (e.g. `SO%3A0001217` -> `SO:0001217`), without case-folding or changing otherwise literal key identity. At a minimum, a recognized Sequence Ontology CURIE `SO:` followed by its seven-digit term identifier is *non-identifying*, including `SO:0001217`, regardless of `key_collision=0`. Do not classify ordinary ORF/locus names as generic using their fanout or arbitrary name-pattern cutoffs. `FCAALL` is already disqualified in the canonical run by its authoritative bucket-aware collision. The exact versioned namespace/denylist recognizer and behavior on malformed encoding **must be approved and tested against the frozen dry-run before coding**; negative specificity cannot be inferred merely from zero collisions. When a key is disqualified, other independent documentary evidence can still accept the candidate.

## 5. Rule precedence (first matching rule)

| Priority | Scope/condition | Decision | `decision_basis` | Entity equivalence |
|---:|---|---|---|---|
| 0 | Audit CROSS_SPECIES (all modes) | AUDIT_ONLY | not applicable | NOT_APPLICABLE |
| 1 | Audit SAME_SPECIES_WRONG_SUBTAXON with `strict` | AUDIT_ONLY | not applicable | NOT_APPLICABLE |
| 2 | Eligible direct PRIMARY + exact | ACCEPTED | DIRECT_PRIMARY_ACCESSION | DOCUMENTARY_ACCESSION_CONTINUITY |
| 3 | Eligible direct SECONDARY + exact | ACCEPTED | DIRECT_SECONDARY_ACCESSION | DOCUMENTARY_ACCESSION_CONTINUITY |
| 4 | Eligible direct PRIMARY/SECONDARY + nonexact | ACCEPTED | DIRECT_ACCESSION_CONTINUITY | DOCUMENTARY_ACCESSION_CONTINUITY |
| 5 | Nondirect ACCESSION + exact | ACCEPTED | DOCUMENTARY_ACCESSION_PLUS_EXACT_SEQUENCE | NOT_ASSERTED |
| 6 | Nondirect ACCESSION + nonexact | UNRESOLVED | NONDIRECT_DOCUMENTARY_ACCESSION | NOT_ASSERTED |
| 7 | Stable unique locus + exact | ACCEPTED | STABLE_UNIQUE_LOCUS_PLUS_EXACT_SEQUENCE | NOT_ASSERTED |
| 8 | Stable unique locus + nonexact + exact TaxID + exact candidate subsequence | ACCEPTED | STABLE_UNIQUE_LOCUS_EXACT_SUBSEQUENCE | NOT_ASSERTED |
| 9 | Stable unique locus + nonexact + exact TaxID + equal-length substitutions only | ACCEPTED | STABLE_UNIQUE_LOCUS_EQUAL_LENGTH_VARIANT | NOT_ASSERTED |
| 10 | Stable unique locus + any other nonexact relation | UNRESOLVED | STABLE_UNIQUE_LOCUS_OTHER_SEQUENCE_RELATION | NOT_ASSERTED |
| 11 | Exact + only weak/colliding documentary evidence | UNRESOLVED | WEAK_OR_COLLIDING_DOCUMENTARY_PLUS_EXACT_SEQUENCE | NOT_ASSERTED |
| 12 | Exact + no documentary support | UNRESOLVED | EXACT_SEQUENCE_ONLY | NOT_ASSERTED |
| 13 | Nonexact + weak/colliding lineage | UNRESOLVED | WEAK_OR_COLLIDING_LINEAGE | NOT_ASSERTED |
| 14 | Nonexact + sequence-only | UNRESOLVED | SEQUENCE_ONLY | NOT_ASSERTED |

If no row matches, stop with a contract error; do **not** silently use `UNRESOLVED` as a catch-all for corrupt/missing evidence. For nondefault promoted audit rows, the taxonomy gate must be explicit and must not be represented as if the pair was originally discovered in PRIMARY.

Warnings (14 frozen atomic tokens): `ACCESSION_CONTINUITY_SEQUENCE_CHANGED`; `COLLIDING_DOCUMENTARY_KEY_PRESENT`; `CROSS_SPECIES`; `EXACT_SEQUENCE_WITHOUT_STRONG_DOCUMENTARY_CONTINUITY`; `EXACT_SEQUENCE_WITH_ONLY_WEAK_DOCUMENTARY_SUPPORT`; `FRAGMENT_RECORD`; `MULTIPLE_ACCEPTED_CANDIDATES`; `NONDIRECT_ACCESSION_WITH_NONEXACT_SEQUENCE`; `NO_STRONG_DOCUMENTARY_CONTINUITY`; `PARTIAL_SEQUENCE_EXACTLY_CONTAINED_IN_TARGET`; `SAME_SPECIES_WRONG_SUBTAXON`; `SEQUENCE_VARIANT`; `STABLE_LOCUS_SEQUENCE_RELATION_UNRESOLVED`; `WEAK_OR_COLLIDING_LINEAGE`. **C4 settled:** warnings are independent distinct tokens, comma-separated with `.` for none; Stage 07 serializes tokens in ascending lexical order for determinism. The frozen dry-run used comma-separated but not always alphabetized token order; scientific regression therefore compares warning **sets**, not raw strings. `FRAGMENT_RECORD` is a record-property warning for both accepted and unresolved fragment rows; `COLLIDING_DOCUMENTARY_KEY_PRESENT` may coexist with ACCEPTED when stronger independent evidence wins. `MULTIPLE_ACCEPTED_CANDIDATES` is emitted **only on ACCEPTED rows** belonging to groups with more than one ACCEPTED, not automatically on unresolved rows of the same group. The group-level integer accepted count is nevertheless repeated on all its output rows.

## 6. Output contract — proposed v1

Bundle directory: `<output-root>/common/07/` (Snakemake tracks `manifest.json`); atomic sibling-stage publication as upstream. Files:

- `07_resolved_candidate_associations.tsv` — exactly one row per union key.
- `07_summary.json` — deterministic metrics and config/provenance summary.
- `stage_outcome.json` — established production stage outcome.
- `manifest.json` — established bundle manifest, implementation digest, declared input manifests, outputs, parameters, environment.

Column order and types (**19 columns**, versus 17 in the historical dry-run):

| # | Field | Type / semantics |
|---:|---|---|
| 1 | release | nonempty concrete release ID |
| 2 | target_id | nonempty target key |
| 3 | rid | release-scoped FULL V2 record identifier |
| 4 | scope | **immutable input provenance**, `PRIMARY` for 05A2 or `TAXONOMY_AUDIT` for 05C2 |
| 5 | evaluation_scope | `PRIMARY` for rows eligible for Stage 07 rules, or `TAXONOMY_AUDIT` for audit-only rows; differs from `scope` only under optional promotion |
| 6 | target_accession | source target accession or `.` |
| 7 | candidate_accession | nonempty UniProt primary accession |
| 8 | exact_sequence | `0`/`1`; `.` only if alignment is forbidden by policy |
| 9 | decision | `ACCEPTED` / `UNRESOLVED` / `AUDIT_ONLY` |
| 10 | decision_basis | frozen basis token, including `TAXONOMY_AUDIT` for audit-only rows |
| 11 | entity_equivalence | `DOCUMENTARY_ACCESSION_CONTINUITY` / `NOT_ASSERTED` / `NOT_APPLICABLE` |
| 12 | annotation_donor | `YES` / `NO` |
| 13 | sequence_relation | one of six frozen enumerated relations; unaligned audit uses `NOT_ALIGNED_BY_POLICY` |
| 14 | accepted_candidates_for_target_release | nonnegative integer for the evaluated group; repeated on all rows for `(release,target_id)` |
| 15 | warnings | distinct atomic tokens in ascending lexical order joined by **comma**, `.` for none |
| 16 | audit_class | original 05C2 `audit_class`, else `.` |
| 17 | audit_reason | original 05C2 `key_source_reason`, else `.` |
| 18 | audit_alignment_allowed | original 05C2 `0`/`1`, else `.` |
| 19 | audit_outcome | explicit outcome for audit-origin rows, else `.` |

**C5 provenance contract:** `scope` is always the original population; `evaluation_scope` records the effective policy gate. Under strict default they are identical; under `include_audited_same_species`, the 24 eligible audit-origin rows retain `scope=TAXONOMY_AUDIT` and all original audit columns but receive `evaluation_scope=PRIMARY` and a normal resolver decision. This cannot be presented as upstream primary sequence discovery. `audit_outcome` values:

- Audit CROSS_SPECIES (any mode): `REJECTED_BY_TAXONOMY_POLICY`; `decision=AUDIT_ONLY`; donor `NO`; no geometry required.
- Audit SAME_SPECIES_WRONG_SUBTAXON (strict): `EXCLUDED_FROM_PRIMARY_BY_SUBTAXON_POLICY`; `decision=AUDIT_ONLY`; donor `NO`.
- Audit SAME_SPECIES_WRONG_SUBTAXON (promoted): `EVALUATED_UNDER_AUDITED_SUBTAXON_POLICY`; `decision` decided by ordinary precedence; donor eligibility follows acceptance and donor policy.
- Original PRIMARY: `.`.

Serialization: TSV UTF-8, tab-delimited, one header row, `\n` line endings, deterministic order START then END, lexical `target_id`, numeric `rid`. Missing/not-applicable scalar `.`; input optional accession empty is normalized to `.` only after validation that an empty field is genuinely absent. Boolean flags `0|1` (or `.` only where expressly permitted). No ambiguous empty strings.

**C6 six-value `sequence_relation` vocabulary (frozen reference counts, all 984,079 rows):**

| Value | Strict reference count |
|---|---:|
| `EXACT_SEQUENCE` | 319,397 |
| `CANDIDATE_EXACT_CONTIGUOUS_SUBSEQUENCE` | 8,266 |
| `EQUAL_LENGTH_SUBSTITUTIONS_ONLY` | 2,146 |
| `OTHER_NONEXACT_RELATION` | 32,598 |
| `NONEXACT_SEQUENCE_ONLY` | 621,671 |
| `NOT_ALIGNED_BY_POLICY` | 1 |

Positive geometry predicates are already defined in Section 4. **C6 remaining review gate:** a formal, exhaustive discriminator between `OTHER_NONEXACT_RELATION` and `NONEXACT_SEQUENCE_ONLY` must match the dry-run row-by-row; these are *descriptive* relation labels, not evidence for accepting similarity-only candidates. Do not merely equate `NONEXACT_SEQUENCE_ONLY` with discovery channel `SEQUENCE_ONLY`: the frozen matrix also has `SEQUENCE_ONLY` rows assigned `CANDIDATE_EXACT_CONTIGUOUS_SUBSEQUENCE` (1) and `OTHER_NONEXACT_RELATION` (6). Conversely, a non-direct documentary candidate with an exact subsequence remains `UNRESOLVED` under its documentary rule, demonstrating that geometry does not trump precedence.

### Annotation-donor computation

Default `YES` precisely for accepted association rows, subject to two independent switches: `STABLE_UNIQUE_LOCUS_EXACT_SUBSEQUENCE` + `partial_sequence=report_only` => `NO`; accepted documentary FRAG=1 + `documentary_fragment=report_only` => `NO`. `UNRESOLVED` and `AUDIT_ONLY` => always `NO`. A warning or `entity_equivalence=NOT_ASSERTED` does **not** by itself forbid donation. Both report-only rules may apply; always preserve `ACCEPTED`.

## 7. Summary JSON contract — proposed

```json
{
  "stage": "07",
  "schema_version": 1,
  "policy_version": "stage07-resolver-v1",
  "releases": {"START": "<start_id>", "END": "<end_id>"},
  "config": {
    "resolver": {"subtaxon_policy": "strict"},
    "annotation_donor": {"partial_sequence": "allow", "documentary_fragment": "allow"}
  },
    "input_counts": {"primary": 0, "taxonomy_audit": 0, "aligned": 0, "unaligned_audit": 0},
    "source_scope_counts": {"PRIMARY": 0, "TAXONOMY_AUDIT": 0},
    "evaluation_scope_counts": {"PRIMARY": 0, "TAXONOMY_AUDIT": 0},
  "decision_counts": {"ACCEPTED": 0, "UNRESOLVED": 0, "AUDIT_ONLY": 0},
  "decision_basis_counts": {},
  "audit_class_counts": {},
  "audit_outcome_counts": {},
  "annotation_donor_counts": {"YES": 0, "NO": 0},
  "warnings_counts": {},
  "target_release_groups_with_multiple_accepted": 0,
  "maximum_accepted_candidates_for_target_release": 0,
  "output_row_count": 0
}
```

All counts are actual observed integers (zeros here are illustrative placeholders); stable sorting and exact key sets must be tested. Summary metadata and manifest may duplicate config for readability but must agree. Manifest `stage=07`, `role=null`, `release_id=null`, `status=complete`; parameters include start/end release IDs, policy config, implementation command and version, and associated upstream manifest/script SHA256 provenance. Ontology provenance is inherited consistently without imposing a fixed reference digest.

## 8. Mandatory invariants and failure behavior

1. Exactly one output row for each `(release,target_id,rid)` in the disjoint 05A2+05C2 input union; no invented candidates.
2. No PRIMARY `REJECTED` in v1; strict TAXONOMY_AUDIT produces only `AUDIT_ONLY`.
3. `ACCEPTED` implies an explicitly enumerated positive predicate; exactness or similarity alone is insufficient.
4. Source direct-anchor eligibility must be verified, not inferred from accession string equality alone.
5. `stable_unique_locus` is proven per typed key, per release/bucket, per candidate, with both temporal documentary sources.
6. Unknown/generic locus keys cannot be treated as identifying; no string nonmatch is interpreted as a distinct-locus conflict.
7. All accepted candidate ACCIDs are preserved, even if many exist for one target/release; no ranking or collapse.
8. `annotation_donor=YES` requires `decision=ACCEPTED`; nondefault donor policies cannot change `decision`/`decision_basis`.
9. `accepted_candidates_for_target_release` equals the number of accepted output rows for that `(release,target_id)` group across the effective resolver population; the integer is repeated on all rows. `MULTIPLE_ACCEPTED_CANDIDATES` attaches only to ACCEPTED rows when that integer exceeds 1.
10. 06C1 provides two valid canonical directional geometries for every aligned pair; unaligned CROSS_SPECIES is an intentional exception, not missing input corruption.
11. Sorted deterministic outputs and summary, fail-closed unknown enumeration values, duplicates, foreign keys, malformed booleans, coordinate inconsistencies, taxonomy missing where required, or incompatible upstream provenance.
12. Stage 07 does not mutate upstream bundles and never reads `TESTING/frozen_reference` at runtime.

## 9. Synthetic test matrix

- Configuration: defaults, each allowed enum, invalid enums, unexpected keys, backward compatibility, differing releases.
- Gate/audit: strict SAME_SPECIES_WRONG_SUBTAXON; CROSS_SPECIES without alignment; one permitted audited-subtaxon promotion; no global search expansion; exact-TaxID restriction retained.
- Direct: exact PRIMARY, exact SECONDARY, nonexact PRIMARY, nonexact SECONDARY with drastic sequence changes; ineligible direct anchor cannot be silently accepted.
- Nondirect: exact accession accept, nonexact accession unresolved; overlap with direct precedence.
- Locus: unique typed key with same-key START+END; START/END on different keys fails; single-snapshot fails; bucket collision fails; different-bucket fanout alone does not fail; generic `SO:0001217` fails despite zero collision; ORF and ORDERED_LOCUS both covered.
- Geometry: exact locus; shorter exact candidate contiguous with full candidate coverage; wrong-direction-only evidence does not pass; equal-length substitution-only accepted for arbitrary mismatch count; equal-length exact bypasses variant; indel-only, mixed, longer candidate unresolved; same species but unequal exact TaxID fails the two geometry acceptances.
- Weak: exact-only unresolved; exact weak/colliding unresolved; nonexact weak lineage unresolved; nonexact sequence-only unresolved; do not invent PRIMARY REJECTED.
- Donors: partial allow/report_only; documentary fragment allow/report_only; similarity-only fragment never rediscovered; both policies applied; accepted/undonatable rows remain accepted.
- Multiplicity: 0/1/many accepted, accepted count on every row of the group, but `MULTIPLE_ACCEPTED_CANDIDATES` warning only on accepted rows in a multi-accepted group; no best-ACCID selection; record duplicate pair keys fail.
- Data integrity: missing 06C1 direction, unexpected aligned CROSS_SPECIES, audit missing alignment when required, malformed field, wrong release, wrong record identity, 04D1 collision-context mismatch, no silent default from missing evidence.
- Reproducibility: independent row-input order, byte-identical outputs; upstream manifest mismatch, bundle invalidation, atomic publish and restart, DAG terminal stage and `validate_artifacts` included.

## 10. Full-run acceptance oracle — strict defaults

Canonical run `2023_01` -> `2026_03`, with authoritative scientific snapshots. Expected PRIMARY 984,054; AUDIT 25; total 984,079. Stage 06A/06C1 aligned 984,078, with exactly one unaligned CROSS_SPECIES audit row. Default decisions: ACCEPTED 298,498, UNRESOLVED 685,556, AUDIT_ONLY 25, PRIMARY REJECTED 0. Classified rows 984,079, unclassified 0, duplicate keys 0, PRIMARY/AUDIT overlap 0. Multiple-acceptance groups 10,848; maximum observed per target/release 44. The 13 frozen per-basis counts in `docs/STAGE07_RESOLVER_DECISION_RULES.md` must match; no hardcoding of these counts.

Do **not** use a direct byte comparison of Stage 07 output with the historical 17-column dry-run. Compare a normalized key-based projection of `(release,target_id,rid,scope,exact_sequence,decision,decision_basis,entity_equivalence,annotation_donor,sequence_relation,accepted_candidates_for_target_release,warnings,audit_class,audit_reason,audit_alignment_allowed)` in strict defaults; compare `warnings` as **sets** to account for deliberate lexical canonicalization and `audit_outcome` separately against the documented audit policy. In strict defaults `evaluation_scope=scope` must hold on every row. Count equality is necessary but not sufficient: require row-level equality of the frozen scientific columns. Resolve the C3 specificity recognizer and C6 complete sequence-relation discriminator before authorizing production implementation.

## 11. Implementation boundaries and acceptance sequence

Only after review sign-off: add `scripts/pipeline/07_resolve_candidate_associations.py`, `workflow/scripts/run_stage_07.py`, focused tests, configuration validator changes, and Stage 07 DAG/registry updates. Workflow `rule all` is extended intentionally; `05R_POST` stays an independent final deliverable. `FINAL_STAGES` includes `07`; `validate_artifacts` includes `07`. Keep scientific upstream production implementation, test oracles and frozen bundles unchanged. Run focused pytest, complete pytest, Ruff, `git diff --check`, DAG dry-run, controlled strict full-run comparison and repeatability checks. No automatic commit.

## 12. Review gate status after canonical-run checks

- **C1 — provisionally resolved:** additive optional top-level config sections with validated defaults and `schema_version: 1` compatibility; no upstream scientific setting changes.
- **C2 — provisionally resolved:** optional promotion limited to 24 exact, aligned, same-species/wrong-subtaxon audit rows, with direct-anchor validation and explicit Stage 07-only taxonomy gate change. Final synthetic tests must verify behavior.
- **C3 — open:** freeze exact versioned ontology-like/non-identifying key recognizer and percent-decoding contract. Known cases: `SO:0001217` is generic despite `key_collision=0`; `FCAALL` collides within bucket.
- **C4 — resolved:** 14 atomic warning tokens, comma-separated, canonical lexical sort, deduplicated; reference comparison by warning sets; multiple-accepted warning only on accepted rows.
- **C5 — provisionally resolved:** immutable source `scope`, explicit `evaluation_scope`, retained original audit evidence, explicit promoted-audit outcome.
- **C6 — partly resolved:** six frozen relation values and counts established; exact discriminator for `NONEXACT_SEQUENCE_ONLY` vs `OTHER_NONEXACT_RELATION` remains to be frozen.
- **C7 — spot-check passed:** four representative stable-locus classes have matching per-key 04D1/04D2 documentary support; full-run joins and invariants remain part of implementation validation, not an invitation to redo scientific audits.

**Additional canonical-run taxonomy contract check:** 05C2 contains 25 distinct keys (24 `TAXONOMY_RESCUE_AUDIT`, one `CROSS_SPECIES_AUDIT`); none overlaps 05A2. All 24 permissible taxonomy-audit keys have one 06A job and one 06C1 pair, all exact; the cross-species key has neither alignment nor job. There are zero duplicate audit jobs and zero duplicate audit alignments. The check printed `CHECK_COMPLETE` with no contract mismatches. These counts are regression oracles for the historical run, not universally hard-coded input counts.

Scientific decision rules are unchanged. **Do not implement Stage 07 until C3 and C6 are specified exactly and this review draft is explicitly approved.** Do not modify frozen Stage 01–06C1 code or `TESTING/frozen_reference`; no automatic commit.
