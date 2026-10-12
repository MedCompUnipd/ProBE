# Stage 08 M10c2+c3: compact GO interpretation

Status: **implementation ready for server validation, not production-certified**.
Existing name `ProBE` retained; future rename to `PROGENY` is deferred.

## Purpose and design

Consume the **already published** M8 `08_<role>_source_assertions.tsv` and
`08_<role>_annotation_links.tsv` through the M10c1 adapter. Do not open
normalized GAFs or repeat the 304 GB START/END scans.

Instead of writing one propagated GO row for every donor-support and ancestor
or NOT descendant, M10c stores:

1. `direct_provenance.sqlite3`, table `direct_support`: one row per authorized
   M8 donor link. Columns preserve release, target ID, RID, original assertion
   ID and physical GAF line, donor accession, original GO/evidence, polarity,
   literal qualifier, and gene-product relation. This is an indexed compact
   pointer into M8's immutable 17-field original GAF assertion records.
2. `term_code_support`: one record per
   `(release,target,aspect,propagated_GO,polarity,evidence_code)`, with a
   **count** of underlying donor links and a distinct direct-support count.
   Its derivation groups direct supports by target/source-GO/polarity/evidence
   BEFORE traversing the ontology; redundant paths and donors cannot multiply
   term rows.
3. `propagated_terms.tsv`: exactly one row per
   `(release,target,aspect,GO)`, with separate positive and NOT counts, arrays
   of original evidence codes, separate direct support counts, and a conflict
   bit. The original GO identifier is retained in the index.
4. `manifest.json`, describing certified M8 source identities, GO snapshot,
   descriptive counts, checksum/size of published files and the fact that
   **no evidence filtering or benchmark truth policy has been applied**.

The `iter_term_provenance()` helper recovers all original donor-support rows
for a propagated term from the source GO by **reversing the propagation
closure**. For a positive propagated GO `g`, candidate original source GO
terms are `descendants(g)`; for an inherited negative `g`, originals are
`ancestors(g)`. This avoids materializing every donor-to-ancestor edge.

## Meaning of the outputs

`has_positive` and `has_not` are descriptive and can coexist. Contradictory
assertions are retained, not vetoed. No EVAL/SKIP or K0/END masking is
performed here. The selected GO inheritance is the pinned M10a graph:
`is_a` and existential `part_of` **within one subontology**. Positive
annotations propagate to ancestors, NOT to descendants. Source evidence codes
and raw qualifier fields are always retained. Source gene-product GAF relation
qualifiers are recorded but are not yet used for policy decisions.

The output `target_id` is still the internal target ID; later benchmark export
must restore the original sequence/query ID and split aspects F/P/C.

## Safety properties

- Default CLI mode is preflight only. Full interpretation requires `--execute`.
- Stage 07 donor selection and all four upstream bundle manifest identities
  are checked by `prepare_collection_inputs()` before any interpretation.
- M8 manifest, M8 summary, all role/release identities, GO OWL SHA256 and
  declared M8 output identities are checked before execution.
- **Both** source and link TSV bytes are checked against M8 published SHA256
  at successful EOF, along with source/link counts and input file metadata.
- All work is written in a private sibling staging directory. `manifest.json`
  is created only after full successful processing; existing destinations
  cannot be deliberately overwritten. On failure the staging directory is
  removed.
- The runner rechecks input file stamps, input fingerprints and critical
  implementation hashes immediately before publication.
- The SQLite provenance store is checked using `PRAGMA quick_check` and its
  direct row cardinality is matched against both M8 role link counts.
- No modifications to Stages 01-08, existing M8 bundle or Stage 07.

## Resource profile and limitations

SQLite keeps the provenance on disk with a bounded cache; the Python loop
holds one M8 source row plus its donor links, a limited SQL insertion batch,
and a cached GO closure. The internal unique-group expansion can be very large
for broad NOT terms: M10c does not claim a firm time/disk bound before a full
run. **Do not execute the full canonical transformation before reviewing the
server preflight and available scratch space.** It should be run via `nohup`
once preflight and filesystem capacity are accepted.

The GO closure caches are limited to 1,024 terms per direction by M10a, but
not by byte size. This should be monitored on the full corpus. The compact
source indexing is deliberately independent of the evidence policy and can be
reused for later profile selection.

No inference is drawn for donor associations with zero M8 source rows; they
remain in the certified M8 coverage table.

## Reproducibility

The CLI runner is `scripts/pipeline/08_interpret_compact.py`, with explicit
upstream bundle paths, verified ontology and a new destination outside the M8
bundle. Manifest inputs must be retained immutable. This is a new
**interpretation bundle**, not a modification of the raw M8 collection.
No upstream reruns, commits or pushes are performed by the installation script.
