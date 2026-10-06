# ProBE pre-resolver pipeline v2 — stage registry

## Scope

This registry defines the production pre-resolver workflow from target
normalization through canonical directional glocal alignments.

The workflow stops at Stage 06C1.

Stage 07 / SAME_ENTITY is intentionally outside this workflow.

`TESTING/candidate-pipeline-v2` is a read-only validation oracle and is not a
production runtime dependency.

START and END are workflow roles. They are not release identifiers. Concrete
release IDs are supplied through configuration.

---

## Global frozen scientific policy

### Target sequence search

- k-mer size: 7
- required shared distinct k-mers:
  `min(10, number_of_distinct_target_kmers)`
- minimum target shared-kmer fraction: 0.25
- fragment candidates excluded
- species-level targets:
  same resolved species bucket
- subtaxon targets:
  same resolved TaxID bucket
- primary sequence-search universe:
  GO-positive UniProt V2
- strict rescue universe:
  FULL UniProt V2
- rescue algorithm:
  exactly the same algorithm and thresholds as Stage 03

### Documentary lineage

Allowed key sources:

- target accession
- taxonomically eligible direct anchors

Allowed key types:

- ACCESSION
- ORDERED_LOCUS
- ORF

Lineage is one-hop and non-recursive.

Sequence-discovered candidates never donate lineage identifiers.

### Candidate union key

Canonical candidate identity within the pre-resolver workflow is:

`(release, target_id, rid)`

Discovery provenance remains separate:

- GO-positive sequence discovery
- FULL-V2 rescue sequence discovery
- lineage discovery

No SAME_ENTITY inference is performed.

### Taxonomy audit

- SAME_SPECIES_WRONG_SUBTAXON:
  taxonomy-rescue audit candidate; alignment allowed; never primary
- CROSS_SPECIES:
  audit only; alignment forbidden under the validated policy

### Canonical alignment

Directional glocal affine FULL dynamic programming:

- TARGET_GLOBAL
- CANDIDATE_GLOBAL
- match: +2
- mismatch: -1
- gap open: -3
- gap extension: -1
- no banding
- exact sequence pairs bypass DP mathematically

---

## Stage registry

| Stage | Scope | Purpose | Principal dependencies | Production status |
|---|---|---|---|---|
| 00 | common, optional | Source-specific raw target adapter to canonical target-input contract | configured raw source | foundation declared; adapter-specific implementation required |
| 01 | common | Validate/prepare canonical targets | Stage 00 or canonical target input | existing ProBE implementation; workflow migration pending |
| 01A_FULL | per release | Build enriched FULL UniProt V2 representation from Swiss-Prot then TrEMBL | configured UniProt DAT sources | validated oracle; production workflow migration pending |
| 01B0 | common | Compile GO normalization policy | user-supplied GO ontology snapshot | validated/frozen; migration pending |
| 01B1 | per release | Normalize GOA against GO policy and FULL V2 accession universe | 01A_FULL, 01B0, GOA | validated/frozen; migration pending |
| 01C | per release | Build GO-positive V2 fast-search universe | 01A_FULL, 01B1 | validated/frozen; migration pending |
| 02A | per release | Extract unique OX values from GO-positive V2 | 01C | validated/frozen; migration pending |
| 02B | per release | Build taxonomy index and target/GO-positive taxonomy contexts | 01, 02A, configured taxonomy snapshot | validated/frozen; migration pending |
| 03 | per release | Primary sequence-candidate discovery in GO-positive V2 | 01, 01C, 02B | validated/frozen; migration pending |
| 04A | per release | Find exact PRIMARY/SECONDARY accession anchors in FULL V2 | 01, 01A_FULL | validated/frozen; migration pending |
| 04B | per release | Classify direct anchors by taxonomy eligibility | 04A, 02B | validated/frozen; migration pending |
| 04C | common | Compile one-hop documentary lineage keys | 01, START+END 04B | validated/frozen; migration pending |
| 04D0a | per release | Extract unique OX values from FULL V2 | 01A_FULL | validated/frozen helper; migration pending |
| 04D0b | per release | Resolve FULL-V2 OX taxonomy contexts | 04D0a, 02B taxonomy index | validated/frozen helper; migration pending |
| 04D1 | common | Prepare release-aware lineage scan index | 01, 04C, START+END 04B/02B | validated/frozen; migration pending |
| 04D2 | per release | Scan FULL V2 for one-hop lineage evidence | 04D1, 04D0b, 01A_FULL | validated/frozen; migration pending |
| 04E | per release | Collapse lineage evidence to unique target-record pairs | 04D2 | validated/frozen; migration pending |
| 05A | common | Baseline union of GO-positive sequence and lineage channels | START+END 03, START+END 04E | validated/frozen; migration pending |
| 05B | common | Audit targets uncovered by baseline candidate union | 01, 05A, START+END 04B | validated/frozen; migration pending |
| 05R0 | per release | Select strict rescue targets: baseline-uncovered and k=7 searchable | 05B, 01, 02B | production-required helper contract; implementation pending |
| 05R1 | per release | Strict FULL-V2 sequence rescue using exactly Stage 03 algorithm/thresholds | 05R0, 04D0b, 01A_FULL | validated/frozen behavior; production implementation pending |
| 05A2 | common | Final candidate union including strict FULL-V2 rescue | 05A, START+END 05R1 | validated/frozen; migration pending |
| 05C2 | common | Final taxonomy audit channel based on final candidate union | 05A2, START+END 04B | validated/frozen; migration pending |
| 05R_POST | common | Explicit post-rescue unresolved-target report | 05B, 05R0, 05R1, 05A2, 05C2, taxonomy/anchor contexts | production helper contract; implementation pending |
| 05D | per release | Materialize candidate sequences from FULL V2 for primary and permitted audit rows | 05A2, 05C2, 01A_FULL | validated/frozen; migration pending |
| 06A | common | Build canonical alignment job table | 01, 05A2, 05C2, START+END 05D | validated/frozen; migration pending |
| NONEXACT | common | Select non-exact jobs requiring dynamic programming | 06A | production helper contract; implementation pending |
| 06C0 | common | Compute exact affine FULL-DP directional glocal alignments for NONEXACT jobs | NONEXACT, 01, START+END 05D | validated/frozen; migration pending |
| 06C1 | common | Combine synthesized exact results and 06C0 non-exact results into canonical pre-resolver output | 06A, 06C0 | validated/frozen; migration pending |

---

## Stage 00 contract

Stage 00 is optional.

### canonical mode

The supplied target FASTA and metadata already satisfy the canonical target
input contract. Stage 00 is skipped.

### adapter mode

A source-specific adapter converts source records to the canonical contract.

An adapter must:

- preserve the biological sequence literally;
- perform no residue repair;
- perform no candidate discovery or biological entity resolution;
- provide explicit TaxID according to the documented source contract;
- preserve source identifiers/accessions as provenance;
- generate deterministic internal IDs where required.

The historical TESTING testsuperset importer is an oracle/example only and is
not a CAFA-specific production dependency.

---

## GO ontology input and provenance

The GO ontology snapshot is a user-supplied experiment input.

Production ProBE does not require a particular GO release or a hard-coded
ontology SHA256.

For every run, ProBE computes the SHA256 of the supplied ontology and records
the observed digest in scientific provenance.

Configuration may optionally provide `expected_sha256`. When supplied, this is
an integrity assertion by the user and the observed file digest must match it.
When omitted/null, the ontology remains valid input and its observed digest is
recorded without imposing a predefined release.

`canonical_url`, `original_url`, `retrieval_date`, and `version_iri` are
optional provenance metadata. Unknown values remain explicitly null and must
never be invented.

### Validation oracle

The ontology used for the validated candidate-pipeline-v2 regression has
SHA256:

`42d7869b3a47cd4a9751d7f2f373acf737b64a1435e4b02b169d134b240dc666`

That digest belongs to the TESTING golden oracle only. It is not a production
restriction.

---

## Ordinal semantics

Ordinal fields are universe-specific and must never be conflated.

### GO-positive sequence search

`sequence_search_record_number`

is the ordinal of the record in the GO-positive V2 FASTA supplied to Stage 03.

### FULL-V2 lineage/rescue/materialization

`full_v2_record_number`

is the ordinal of the record in the FULL V2 release.

Strict rescue rows therefore have:

- GO-positive sequence ordinal: absent
- FULL-V2 record ordinal: present

The two record-number namespaces are not interchangeable.

---

## Candidate-channel semantics

Baseline Stage 05A contains only:

- SEQUENCE_ONLY
- LINEAGE_ONLY
- BOTH

Stage 05A2 adds the strict rescue provenance while preserving independent
discovery flags.

The validated final discovery dimensions are:

- `go_positive_sequence_discovered`
- `full_v2_rescue_sequence_discovered`
- `lineage_discovered`

A candidate discovered through strict FULL-V2 rescue must not be presented as a
GO-positive sequence-search hit.

---

## Rescue semantics

Strict rescue is production-required.

Trigger:

`baseline uncovered for the release AND target sequence length >= 7`

The scientific sequence-search algorithm, taxonomy eligibility, k-mer
thresholds, fragment policy, and sequence verification are identical to Stage
03.

Only the searched UniProt universe changes:

- Stage 03: GO-positive V2
- Stage 05R1: FULL V2

Sensitive probing with relaxed thresholds (historical Stage 05R2) was a
diagnostic experiment, was scientifically rejected, and must not appear in the
production DAG.

---

## Taxonomy audit semantics

The taxonomy audit is intentionally separate from the primary candidate union.

Taxonomy-audit rows never become primary merely because they are aligned.

Current validated policy:

- same species / wrong subtaxon:
  may be aligned for audit
- cross species:
  retained for audit evidence but not aligned

The historical pre-rescue Stage 05C is not a production stage. Production uses
the final post-rescue Stage 05C2.

---

## Alignment semantics

Stage 06A emits one canonical alignment job per final primary or permitted
taxonomy-audit target/candidate pair.

Exact sequence pairs are identified reproducibly before DP.

NONEXACT is an explicit production selection stage.

Stage 06C0 processes only NONEXACT jobs.

Stage 06C1 produces both canonical directions for every alignment job:

- TARGET_GLOBAL
- CANDIDATE_GLOBAL

Exact jobs receive mathematically synthesized exact results; non-exact jobs
receive the FULL-DP results from 06C0.

No banded alignment is canonical.

Historical Stage 06B is non-canonical and absent from the production DAG.

---

## Artifact and restart policy

Snakemake owns:

- DAG construction
- scheduling
- ordinary restart decisions
- dependency invalidation

ProBE owns:

- scientific stage contracts
- payload validation
- manifests
- provenance
- atomic publication

A completed scientific stage is represented by an immutable bundle whose
completion marker is:

`manifest.json`

Snakemake tracks the manifest rather than owning the scientific bundle
directory itself.

Stage bundles are constructed in a same-filesystem temporary sibling directory,
validated, manifested, and published by atomic directory rename.

Ordinary Snakemake restart logic must not repeatedly SHA256 large biological
artifacts.

SHA256 may be recorded at production time. Full payload hash verification is an
explicit deep-integrity operation.

---

## Production exclusions

The following are deliberately absent from the production pre-resolver DAG:

- Stage 05R2 sensitive probe
- Stage 06B approximate/non-canonical alignment path
- Stage 07
- SAME_ENTITY inference
- custom Python workflow scheduler
- custom restart/state database
- notebook-specific orchestration API

