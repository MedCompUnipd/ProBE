# ProBE Stage 2C — Scientific resolver audit

Date: 2026-10-14. Scope: the isolated Stage 2C variant resolver, not the
frozen canonical Stage 07 or published START/END scientific artifacts.

> **Historical record, superseded 2026-10-16.** This document records
> an interim decision requiring documentary continuity for cross-subtaxon
> EXACT donors, including the numbered precedence below. That requirement
> was subsequently rejected for verified literal EXACT same-species pairs,
> and the same-TaxID EXACT gate was also corrected. The current authoritative
> contract is [`PROBE_STAGE2C_EXACT_DONOR_CONTRACT_V1.md`](PROBE_STAGE2C_EXACT_DONOR_CONTRACT_V1.md).
> Do **not** implement the old item 5 as a current donor admission rule.

## Finding

Stage 2C4E1 formerly called the cross-strain sequence/taxonomy eligibility
`ELIGIBLE` a sufficient condition for `ACCEPTED`. This could promote a
GO-positive, exact-sequence-only candidate rejected by the canonical Stage 07
resolver for missing documentary continuity. Literal sequence identity is not
independent evidence of lineage, accession or locus continuity.

The 2C4E1 logic also returned unchanged canonical decisions for unresolved
cross-strain taxonomy, and could preserve an obsolete
`MULTIPLE_ACCEPTED_CANDIDATES` warning after changing decisions.

## Corrected precedence

1. Run the frozen strict Stage 07 resolver using the verified candidate,
   anchor, lineage and alignment joins. Do not change canonical modules.
2. Preserve the canonical decision for *same resolved TaxID*, including
   eligible nonidentical documentary continuity under its existing rules.
3. Deny cross-strain annotation donation if either resolved TaxID is missing,
   if species are different/unverified, or when the selected policy is `strict`.
4. Under `same_species_exact`, require a complete literal amino-acid match,
   consistency with verified Stage 06A exactness, unambiguous residues and the
   independent documentary-fragment guard. This is an eligibility gate only.
5. **In addition**, require an `ACCEPTED` decision under the existing canonical
   documentary precedence. For PRIMARY candidates, use the strict baseline;
   for verified `TAXONOMY_AUDIT` candidates, independently evaluate only those
   original audits using Stage 07's existing
   `include_audited_same_species` policy. A GO-positive exact-only pair remains
   `UNRESOLVED`, not a GO donor.
6. Respect `documentary_fragment=report_only`: an accepted documentary
   fragment is not automatically `annotation_donor=YES`.
7. Recompute multiplicities and warnings from *final* candidate decisions.

## Regression contract

Test negative GO-positive exact-only pairs, positive verified direct and
independently audited direct pairs, nonexact/cross-species/unresolved taxonomy,
ambiguous residues, fragments and independent fragment policy, strict policy,
same-TaxID documentary nonexact, source preservation and multiplicity cleanup.

## Versioning / Snakemake

The Stage 2C evidence plan fingerprints `stage2c_resolver_variant.py`.
After applying the fix the previous immutable plan
`stage2c-evidence-c9e9637b85506d8c` no longer matches the source tree.
Create and verify a **new** content-addressed integration plan; do not edit or
reuse the old plan or its publication directories. Both policies use exactly
one common preview/completion chain; no scientific job may run during this
audit installation. Only an opt-in execution with separate resource and
scientific approval may run on real UniProt datasets.

## Validation status

The fix is validated locally by unit and scoped regression tests. Real Pixi
Ruff, full regression, new integration-plan validation, and Snakemake 29-job
dry-run are performed by the companion installation script on `thanos`. Do not
consider those checks passed until its server log reports PASS.
