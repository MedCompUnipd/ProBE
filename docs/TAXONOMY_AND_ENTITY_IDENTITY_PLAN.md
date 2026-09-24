# Taxonomy and annotation-aggregation identity plan

## Scope

This plan defines the minimal correction required to keep exact sequence
identity while preventing annotation union across distinct resolved NCBI
taxonomic contexts. It does not change ontology, evidence, truth-set, metric,
prediction-ingestion, or CLI policy. Implementation is deferred.

The two temporal roles are:

- `start`: knowledge and prediction cutoff snapshot;
- `end`: later benchmark-collection snapshot.

Release names and dates are configuration and provenance, not API concepts.

## 1. Current-state audit

| Area | Current behavior | Consequence |
| --- | --- | --- |
| `target_mapping.py` | Indexes targets and UniProt FASTA records by SHA-256, length, and verified normalized sequence; every matching accession is attached to the target. FASTA headers provide no authoritative taxonomy. | Identical cross-species sequences are indistinguishable and all matching accessions are returned together. |
| `identity.py` | `SequenceAlias` contains only identifier and source. `SequenceIndex` buckets only by exact normalized sequence. `IdentityMap.aliases_for(sequence_id)` unions aliases across every match with that sequence ID. | `sequence_id` is incorrectly usable as both sequence identity and annotation-aggregation scope. |
| `records.py` | `SequenceRecord` has no TaxID or UniProt record metadata. `AnnotationRecord.taxa` preserves raw GAF taxon fields. | GOA has taxon text, but it is not reconciled with an authoritative protein record or resolved to an NCBI species rank. |
| `preprocessing.py` | Streams GOA/FASTA and uses a disk-backed accession index. It filters by accession and ontology validity, not species. | It is suitable for large inputs but cannot establish species-scoped aliases. |
| `pipeline.py` | Each release is matched independently, then `_merge_identities()` unions aliases by `(target_id, sequence_id)` across releases. | Release-specific aliases with the same sequence become one unscoped alias set. |
| `comparison.py` | `_identity_lookup()` builds `subject_id -> sequence_id`; `_assertions()` groups by `(sequence_id, aspect, term_id)`. | GO annotations from all exact-sequence aliases can be merged before event classification. |
| `knowledge.py`, `truth.py`, `masking.py` | Direct states, events, prior knowledge, `K0`, neutral truth, and final per-target truth are keyed/grouped by `sequence_id` and aspect. | A taxonomically incompatible annotation can contaminate both the strict annotation union and the prior-known leakage mask. |
| Existing tests | Exact matching, release-specific sequence change, GOA filtering, and truth/mask behavior are covered. No fixture gives the same full-length sequence to different species. | The cross-species merge defect is not detected. |

No UniProt `.dat` parser or `.dat`-to-combined-FASTA workflow is present in the
current branch files. Before implementation, the existing workflow identified
by the project owner must be located and reused or extended; a competing FASTA
generation path must not be introduced.

## 2. Scientific invariants

1. Exact normalized full-length amino-acid sequence remains the sequence
   identity criterion. Its stable key includes normalized sequence, length,
   SHA-256, and normalization-policy version.
2. An accession is an alias, not a biological identity key.
3. Sequence identity and annotation-aggregation scope are distinct:
   `sequence_id` identifies the characters; `aggregation_key` identifies which
   annotations may be united.
4. `species_anchor` is required for lineage classification and audit, but does
   not by itself authorize annotation aggregation.
5. Strict annotation aggregation requires both exact full-length sequence and
   the same resolved NCBI TaxID under the applicable taxonomy snapshot.
6. Exact-sequence matches with different resolved TaxIDs are not merged, even
   when those TaxIDs are strains, substrains, isolates, or other below-species
   taxa sharing one species anchor.
7. Distinct accessions, loci, reviewed states, or UniProt sections do not block
   merging inside the same resolved taxonomic context.
8. Genus, family, order, or any higher rank never authorizes merging.
9. Every raw TaxID and its source remain in provenance.
10. Taxonomically non-mergeable exact matches may remain visible for audit or
    later predictive knowledge, but contribute neither assertions nor
    prior-known terms to the target's strict benchmark scope.
11. Taxonomy resolution is snapshot-specific, deterministic, and never falls
    back to taxon-name matching for deleted TaxIDs.
12. The same taxonomy-based aggregation rule applies uniformly to MF, BP, and
    CC; there are no aspect-specific merging rules.

## 3. Mergeable exact-sequence cluster and match dispositions

For normalized full-length target sequence `S`, resolved NCBI TaxID `R`, and one
snapshot role, define:

```text
C(S,R) = {r | normalized_full_length_sequence(r) = S
              and species_anchor(raw_taxid(r), taxonomy_snapshot).resolved_taxid = R}
```

The annotation aggregation key is conceptually:

```text
(sequence_identity, resolved_taxid)
```

`SAME_TAXON_EXACT_SEQUENCE` is the positive, mergeable disposition.
`SAME_SPECIES_DIFFERENT_SUBTAXON_EXACT_SEQUENCE` applies when the resolved
TaxIDs differ but their required species anchors are the same; it is not
mergeable in the strict benchmark. `CROSS_SPECIES_EXACT_SEQUENCE` applies when
the species anchors differ and is likewise not mergeable. `S` alone remains the
sequence identifier but is never the annotation-union key.

The species anchor remains required on every resolved match. It supports these
lineage classifications, audit output, and future sensitivity analyses; it does
not collapse different resolved TaxIDs into one strict aggregation scope.

The cluster is formed independently in `start` and `end`, using that role's
UniProt, GOA, and taxonomy snapshots. Temporal comparison links the two
role-specific clusters through the target and exact sequence; it must not first
form a global alias union.

## 4. Species-anchor resolution

`species_anchor(raw_taxid, taxonomy_snapshot)` returns a structured resolution,
not only an integer:

| Field | Meaning |
| --- | --- |
| `raw_taxid` | TaxID exactly as supplied by the source record |
| `resolved_taxid` | Current node after following `merged.dmp`, if applicable |
| `merge_path` | Ordered old-to-new TaxID chain |
| `species_anchor_taxid` | Nearest lineage node whose explicit NCBI rank is `species` |
| `species_scientific_name` | Scientific name from `names.dmp` for the anchor |
| `lineage` | Ordered node, rank, and name provenance under this snapshot |
| `status` | Resolved, merged, deleted-unresolved, absent, cyclic/invalid, or no-species-ancestor |
| `taxonomy_snapshot_id` | Stable identity of the pinned taxonomy dataset |

Resolution order is fixed: follow `merged.dmp` deterministically; reject a
cycle or ambiguous chain; reject a TaxID in `delnodes.dmp` without an explicit
merge; require the resolved node in `nodes.dmp`; walk parents until the nearest
node explicitly ranked `species`. Name similarity is never a recovery rule.

Strain, substrain, isolate, serotype, and other below-species records resolve to
the same anchor when their nearest ranked species ancestor is the same. Their
raw and resolved TaxIDs remain distinct in provenance. Sharing that anchor does
not authorize strict annotation merging when their resolved TaxIDs differ.

## 5. Taxonomically non-mergeable exact matches

An exact full-length match whose species anchor differs from the target anchor
receives `CROSS_SPECIES_EXACT_SEQUENCE`. It remains in the match audit with its
alias, raw TaxID, species anchor, source role, and sequence identity, but is not
admitted to `C(S,R)`.

An exact full-length match whose species anchor equals the target anchor but
whose resolved TaxID differs receives
`SAME_SPECIES_DIFFERENT_SUBTAXON_EXACT_SEQUENCE`. It retains the same audit and
lineage provenance and is also excluded from `C(S,R)`. This includes different
strains, substrains, isolates, serotypes, and other below-species contexts.

Consequently, assertions from either non-mergeable disposition must not
contribute to:

- the direct assertion union;
- event classification between `start` and `end`;
- prior-knowledge state;
- `K0` or any later truth/mask set.

Taxonomic lowest common ancestor above species is audit information only. It is
never a permission to merge. The same is true of a shared species anchor when
the resolved TaxIDs differ.

## 6. Fragment policy

Fragment handling is separate from full-length exact identity and must not
weaken it. The conservative fragment dispositions and confirmation threshold
remain unchanged by the strict full-length taxonomy correction.

| Disposition | Minimum interpretation | Annotation merge |
| --- | --- | --- |
| `SAME_TAXON_EXACT_SEQUENCE` | Exact normalized full-length sequence and same resolved NCBI TaxID | Yes |
| `CONFIRMED_SAME_PROTEIN_FRAGMENT` | Same species anchor, exact compatible region, and unambiguous protein association supported by locus/gene/record provenance or an explicit UniProt relationship | Yes, with fragment provenance |
| `AMBIGUOUS_FRAGMENT` | Containment match without an unambiguous protein association, or multiple compatible proteins | No |
| `CROSS_SPECIES_FRAGMENT` | Fragment and candidate full-length protein have different species anchors | No |

Sequence containment alone is insufficient. If the input target is itself a
fragment and maps ambiguously to multiple proteins, strict benchmark
construction quarantines the target instead of unioning their annotations.

## 7. `start`/`end` dataset model

Each role is an explicitly selected, immutable bundle:

| Component | `start` | `end` |
| --- | --- | --- |
| UniProtKB | Swiss-Prot `.dat.gz` plus TrEMBL `.dat.gz` | Independently frozen Swiss-Prot `.dat.gz` plus TrEMBL `.dat.gz` |
| GOA | Frozen historical or otherwise pinned snapshot | Independently frozen later snapshot |
| NCBI Taxonomy | Pinned `new_taxdump` appropriate to the selected role | Independently pinned `new_taxdump` |
| GO ontology | The one benchmark-wide immutable `go.owl` | The same bytes and identity |

The user/configuration selects all three role-specific biological sources.
Dates need not coincide, and ProBE must record the mismatch rather than infer or
silently substitute an allegedly aligned release.

## 8. UniProt `.dat` requirements

FASTA remains a useful derived sequence artifact, but is not the authoritative
source for identity-layer metadata. For both roles, future parsing of the two
UniProtKB `.dat.gz` sources must extract at least:

- primary and secondary accessions;
- original and normalized sequence, length, and sequence identity;
- raw NCBI TaxID;
- reviewed/unreviewed status and Swiss-Prot/TrEMBL origin;
- gene name, ordered-locus name, ORF name, and other available locus identifiers;
- explicit fragment flag and available record/isoform/fragment relationships;
- source UniProt release and source-record provenance.

Archive member paths are discovered from the selected release artifact and
recorded; they are not constants tied to one historical release. The established
combined-FASTA workflow must be extended to emit or join this metadata, rather
than duplicated.

## 9. NCBI taxonomy requirements

Each taxonomy snapshot must retain and checksum the original `new_taxdump`
archive and use at least:

- `nodes.dmp` for parent links and explicit ranks;
- `names.dmp` for the scientific name class;
- `merged.dmp` for deterministic TaxID replacement;
- `delnodes.dmp` for explicit unresolved deletion.

The parsed representation may be indexed on disk for scale. It must preserve
the archive identity, member checksums or archive checksum, parser version, and
every resolution decision. `start` records resolve only against the `start`
taxonomy; `end` records resolve only against the `end` taxonomy.

## 10. GOA snapshot requirements

`start` and `end` use independently frozen GOA artifacts. A mutable current URL
must never identify both. For each annotation, retain the source snapshot,
subject accession, all original GAF taxa, source line, and normal assertion
provenance.

The protein TaxID from UniProt `.dat` establishes alias metadata. The primary
gene-product TaxID in GOA must be resolved under the same role taxonomy and
checked for consistency before the assertion enters a cluster. Other GAF taxon
values remain provenance and must not silently replace the protein species.

## 11. Storage layout

Large biological data belong on a dedicated filesystem, not in the Git
repository. A storage area of at least approximately 1 TB should retain ample
working headroom for hundreds of GB of compressed sources, extracted members,
derived FASTA, indexes, and temporary files.

```text
<DATA_ROOT>/
  start/
    raw/uniprot/
    raw/goa/
    raw/taxonomy/
    extracted/uniprot/
    extracted/taxonomy/
    derived/uniprot/combined.fasta.gz
    derived/metadata/
    indexes/sequence/
    indexes/taxonomy/
    cache/
    manifests/snapshot.json
  end/
    raw/uniprot/
    raw/goa/
    raw/taxonomy/
    extracted/uniprot/
    extracted/taxonomy/
    derived/uniprot/combined.fasta.gz
    derived/metadata/
    indexes/sequence/
    indexes/taxonomy/
    cache/
    manifests/snapshot.json
  shared/
    ontology/raw/go.owl
    ontology/manifests/ontology.json
```

`raw` contains immutable downloaded bytes, including release archives.
`extracted` contains archive members when random access or tooling requires
them. `derived`, `indexes`, and `cache` are reproducible products and cannot be
mistaken for upstream files. Acquisition must never overwrite a raw artifact.

## 12. Reproducibility manifest

Each role has one versioned machine-readable manifest. At minimum it records:

| Group | Required fields |
| --- | --- |
| Identity | schema version, snapshot role (`start` or `end`), snapshot ID |
| Retrieval | source URL, local raw path, retrieval timestamp, upstream release identifier/date when available, byte size, SHA-256 for every downloaded artifact |
| UniProt | Swiss-Prot source record, TrEMBL source record, archive/member provenance, derived combined FASTA path and SHA-256 |
| GOA | independently selected source record and checksum |
| Taxonomy | `new_taxdump` source record and checksum; required member identities |
| Policies | parser versions, sequence-normalization-policy name/version, taxonomy-resolution-policy version |
| Derivation | tool/software version, input checksums, output paths/checksums, completion status |

Local paths may be absolute or resolved relative to a declared `DATA_ROOT`, but
must be unambiguous. A derived artifact is accepted only when its recorded input
checksums match the selected raw artifacts.

## 13. Minimal later module and API changes

The future change should introduce one explicit aggregation key and carry it
through existing transformations; it should not rewrite ontology or metrics.

| Module | Minimal later change |
| --- | --- |
| New taxonomy parser/service | Parse pinned `new_taxdump`; expose `species_anchor(raw_taxid, taxonomy_snapshot)` and auditable resolution records. |
| New UniProt `.dat` parser, or extension of a located existing workflow | Stream Swiss-Prot and TrEMBL records into release-specific protein metadata while reusing combined-FASTA derivation. |
| `records.py` | Add immutable protein/alias metadata, taxonomy resolution, fragment metadata, snapshot identity, and an `AnnotationAggregationKey(sequence_id, resolved_taxid)`. Preserve raw fields, including the required species anchor. |
| `target_mapping.py` | Keep its exact sequence algorithm; join matches to metadata, require/resolve a target taxonomic context and species anchor, and classify matches without returning different-subtaxon or cross-species accessions as mergeable. |
| `identity.py` | Keep sequence buckets; make aliases release- and taxon-aware. Replace global `aliases_for(sequence_id)` use in benchmark construction with scoped aliases for an aggregation key. |
| `preprocessing.py` | Preserve streaming and disk-backed behavior; record/join authoritative metadata and taxonomy consistency without loading release-scale inputs into RAM. |
| `pipeline.py` | Replace `t0`/`t1` public roles with `start`/`end`; accept the three explicit snapshot inputs per role; do not globally union role aliases before comparison. |
| `comparison.py` | Build subject lookups scoped by role and aggregation key. Change canonical assertion grouping from `(sequence_id, aspect, term_id)` to `(aggregation_key, aspect, term_id)`. Quarantine taxonomy conflicts. |
| `knowledge.py` | Carry the aggregation key through direct states, events, targets, and prior-knowledge classification. |
| `truth.py`, `masking.py` | Group `D_score`, `G_candidate`, `K0`, `D_neutral1`, `X1`, `M`, and `T` by aggregation key and aspect. Existing set formulas remain unchanged. |

The minimum conceptual interfaces are:

```text
species_anchor(raw_taxid, taxonomy_snapshot) -> TaxonResolution
match_target(target, role_protein_metadata, target_taxon_resolution) -> MatchAudit
mergeable_aliases(sequence_identity, resolved_taxid, role) -> aliases
annotation_aggregation_key = (sequence_identity, resolved_taxid)
```

Different-subtaxon and cross-species matches are separate audit collections, not
empty or weakened versions of the mergeable same-taxon cluster.

## 14. Synthetic tests required later

| Case | Required outcome |
| --- | --- |
| Two accessions, exact full-length sequence, same resolved TaxID | Merge annotations; status `SAME_TAXON_EXACT_SEQUENCE`. |
| Distinct loci, exact full-length sequence, same resolved TaxID | Merge annotations. |
| Distinct raw TaxIDs that resolve through `merged.dmp` to the same TaxID | Merge annotations; preserve both raw TaxIDs and merge paths. |
| Strain and substrain TaxIDs under one ranked species | Resolve to one species anchor but do not merge; status `SAME_SPECIES_DIFFERENT_SUBTAXON_EXACT_SEQUENCE`; preserve both raw and resolved TaxIDs. |
| Species-level TaxID and isolate-level TaxID under that species | Do not merge; status `SAME_SPECIES_DIFFERENT_SUBTAXON_EXACT_SEQUENCE`. |
| Exact full-length sequence in two species of one genus | Do not merge; retain cross-species audit. |
| Exact full-length sequence in two species of one family | Do not merge; never authorize via family LCA. |
| Cross-species `start` annotation only | It does not enter prior state or `K0`. |
| Cross-species `end` annotation only | It does not create an event, neutral truth, or scored truth for the target species. |
| TaxID in `merged.dmp` | Follow the recorded chain deterministically and preserve it. |
| TaxID only in `delnodes.dmp` | Quarantine as deleted-unresolved; no name fallback. |
| Same raw TaxID resolved under independent role snapshots | Preserve both role-specific resolutions and snapshot identities. |
| Target without an anchor and exact matches in multiple species | Quarantine under strict construction. |
| Confirmed same-protein fragment | Merge only when all approved metadata and region checks pass. |
| Containment-only or multiply mapped fragment | `AMBIGUOUS_FRAGMENT`; no merge. |
| Cross-species fragment | No merge. |
| Fragment target mapping to multiple proteins | Quarantine target; no annotation union. |
| Secondary accession and primary accession in same scoped cluster | Resolve as aliases with complete release provenance. |
| One accession changes sequence between roles | Keep role-specific sequence identities and aliases. |
| UniProt and GOA primary species conflict | Quarantine/audit according to the approved conflict policy. |
| Input-order permutation | Produce identical clusters, resolutions, and audit ordering. |

## 15. Unresolved decisions requiring human approval

1. **Target taxonomic-context source.** Decide whether every benchmark target
   must carry an authoritative raw TaxID, or whether a target may acquire a
   resolved taxonomic context only when all otherwise valid exact matches
   resolve uniquely to one TaxID. Multiple resolved TaxIDs must always cause
   quarantine, including when they share a species anchor; LCA inference is
   forbidden as merge authorization.
2. **Cross-snapshot species continuity.** Approve linking the independently
   resolved `start` and `end` clusters through the target, while retaining both
   role-specific resolved TaxIDs and anchors, or define an explicit versioned
   cross-snapshot TaxID reconciliation policy for taxa whose identifiers changed.
3. **UniProt–GOA TaxID conflict.** Decide whether any mismatch is fatal for the
   assertion or whether narrowly defined, audited exceptions are permitted.
4. **GAF multi-taxon semantics.** Approve using only the gene-product TaxID for
   the species anchor while preserving interacting-taxon fields as assertion
   context.
5. **Fragment confirmation threshold.** Specify the exact UniProt relationship
   and gene/locus metadata combinations sufficient for
   `CONFIRMED_SAME_PROTEIN_FRAGMENT`; until approved, all fragments remain
   non-mergeable in strict mode.
6. **Missing or deleted taxonomy.** Approve strict quarantine when no ranked
   species anchor can be resolved. This plan recommends no manual/name-based
   recovery in benchmark construction.
7. **Existing UniProt derivation workflow location.** Identify the current
   `.dat`-to-combined-FASTA workflow that must be reused; it is not present in
   this branch's tracked files.

## Statements requiring revision in other documents

This plan records the approved strict rule. The following documents remain to be
aligned in separately scoped changes:

| Document | Statement or contract to revise |
| --- | --- |
| `AGENTS.md` | “The internal key must be based on normalized protein sequence...” remains true for sequence identity, but must be followed by the resolved-taxonomic-context annotation-aggregation rule. “Associate each sequence identity with historical and current aliases” must require release and taxonomy scope. |
| `docs/SCIENTIFIC_DECISIONS.md` | In “Exact sequence identity,” “The same exact sequence may correspond to multiple aliases” must distinguish a global match/audit set from aliases mergeable only within one resolved TaxID. |
| `docs/BENCHMARK_SELECTION_AND_METRIC_SPEC.md` | Replace the configurable LCA rule, especially “Same-family matches may be accepted...”, with strict same-resolved-TaxID aggregation. Change annotation/assertion keys from `sequence_id` to the aggregation key, extend output tables with raw/resolved/anchor taxonomy provenance, and rename temporal roles to `start`/`end`. |
| `docs/TRUTH_MASK_IMPLEMENTATION_PLAN.md` | Replace “Let `p` be an exact sequence identity,” grouping “by sequence and aspect,” and the `sequence_id`-only records/functions with aggregation-key scope. Define `K0` from same-resolved-taxon `start` assertions only, and rename temporal roles to `start`/`end`. The ontology and set formulas do not otherwise change. |
