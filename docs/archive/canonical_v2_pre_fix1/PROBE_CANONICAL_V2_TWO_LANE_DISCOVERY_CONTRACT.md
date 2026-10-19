# ProBE canonical V2 — START/END cross-release documentary anchors

Status: **new code; no canonical DAG or GO donor publication is activated**.
This package supersedes the UNINSTALLED 16 October two-lane discovery v1
archive. The old `04D1/04D2` implementations, EXACT donor v1 resolver,
Stage 08 algorithms and historical frozen publications remain unchanged.

## One documentary query universe; two separate evaluation releases

For each target, obtain and verify all EXACT candidates independently in
START and END, using their release-local FULL V2. Extract original accession,
secondary accession, gene name/synonym, ordered locus and ORF observations
from every accepted literal EXACT candidate, including the verified same
biological species candidates carrying different resolved TaxIDs. Store each
observation with its original source release, RID, TaxIDs, and key provenance.
Do **not** choose a unique genomic locus for the target.

Each release's NONEXACT discovery uses:

- both EXACT registers as **lookup anchors only**, irrespective of which
  release supplied an alias;
- independently documented candidate keys from the evaluation release;
- the evaluation release's own FULL V2 FASTA and resolved OX/target TaxIDs.

A candidate is emitted only if it is nonidentical to the target's literal
sequence and has the **identical resolved TaxID** in the evaluation release.
Cross-subtaxon NONEXACT alignment and GO donation remain forbidden.
An EXACT source's same-species scope describes the source record, not a
waiver of the candidate's same-resolved-TaxID requirement.

Neither END GO terms nor END candidate sequence/TaxID data enter START.
Likewise START GO terms and candidate sequence/TaxIDs do not enter END.
Only **documentary search keys** are shared. GOA imports and temporal
interpretation remain release-local and downstream. The present consumer
produces neither GO decisions nor DP admissions. Literal typed matches and
normalized typographic hints are recorded separately. Generic locus IDs
are not used as specific locus search keys. Hits and collisions are retained
without pretending to prove the target's unique genomic locus.

## Deterministic execution order and provenance

1. Generate the START and END policy-independent EXACT discoveries.
2. Produce both SHA-bound release-local EXACT documentary registers.
3. Run documentary NONEXACT lookup for START with START+END registers,
   and for END with END+START registers. A single FULL V2 scan per
   evaluation release is sufficient.
4. Feed the versioned candidate observations to the later canonical
   qualification/DP/resolver, preserving independent documentary evidence.
   Do **not** inject the new observations into legacy 04D1/04D2 tables or
   claim that a lookup match by itself authorizes donation.
5. Stage 07/M8/M10c/Temporal V2 consume only the selected release-local
   resolved and admissible donor sets with policy selection downstream.

The CLI `03_lookup_canonical_documentary_nonexact.py` requires both
`--exact-register` (evaluation release), `--other-exact-register` (other
release), and `--other-release`. The Python API also accepts single-release
calls for regression compatibility; the **canonical operating mode** is
`RETROSPECTIVE_START_END_UNION` and is explicitly encoded in the summary.
The schema is `probe-canonical-v2-documentary-lookup-v2`.

Output columns `exact_origin_release` and `exact_origin_target_taxid` disambiguate
same-numbered RIDs across releases. Every output row also records the
**evaluation** `release`, candidate RID, candidate resolved TaxID, original
keys, match kind, candidate sequence digest and evidence origin. Source
summaries must pass recorded output SHA256 verification. Both source
registers must match the exact input target sequence file digest; a release
mismatch, reused register, missing target, or altered source fails closed.
Summary inputs pin both source register summary SHA256 digests and the
release-specific FULL/TaxID inputs.

The historical Stage 2C frozen key
`eeac66878bdfb8e1b96fdff6b297a675214912db699d0a715101c5a079ab7990`
is a **read-only comparison**, never an output identity for this new schema.
The future canonical DAG must have a distinct content-based fingerprint.
This code does not yet implement the full DAG, new documentary-v2 eligibility,
or policy-specific Stage 08 execution: these still require end-to-end tests.
