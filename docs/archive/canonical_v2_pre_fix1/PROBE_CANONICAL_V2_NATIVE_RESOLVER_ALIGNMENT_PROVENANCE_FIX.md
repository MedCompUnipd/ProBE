# ProBE canonical V2: alignment provenance and documentary enrichment

The original Stage 06A job carries original discovery fields. Stage 07's
`collect_alignments` checks this original 06A identity against the candidate
universe as supplied by the complete V2 workload. Canonical Stage 07 later
binds newly qualified documentary evidence to the same candidate identities,
and the scientific resolver kernel consumes those enriched facts.

Do not pass enriched `CandidateRecord` flags back into the frozen alignment
join: for a documentary-only NONEXACT candidate, `lineage_discovered` changes
from 0 (original Stage 06A source) to 1 (new independent documentary proof).
That change MUST NOT invalidate its original 06A job.

Fix: only the `collect_alignments` candidates argument changes, from
`{key: row.candidate for key, row in provenance.items()}` to `candidates`,
the original result of `load_v2_candidate_universe`. Scientific decision
remains based on the verified enriched `provenance` and on exact original
06C alignment measurements. No change to the policy, DP, discoverer, M8,
M10c, Temporal V2, or manifest formats.

Regression `test_canonical_v2_native_resolver_m8.py` runs a fully synthetic,
versioned 2023_01/2026_03 source in a temporary directory: 2 EXACT donor
candidates (one per release), a same-TaxID NONEXACT documentary candidate
with an independently dual-attested ORF locus and an actual C++ full DP,
canonical Stage 07, then a real M8 scan over tiny synthetic GAFs. It requires
an available full DP binary and does not touch the pinned GOA publications.
The independently attested locus is intentional; inherited EXACT source
metadata alone does NOT qualify for donor authorization.

Acceptance requires Ruff, staged original-source preservation, synthetic
native integration and canonical regression tests on thanos; the installer
must refuse unexpected HEAD or original file digest. No commit/push, real
science, or canonical/frozen publication writes.
