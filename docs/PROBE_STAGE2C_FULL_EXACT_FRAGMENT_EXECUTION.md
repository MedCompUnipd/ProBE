# Stage 2C FULL EXACT: literal fragments in fresh execution

The canonical fresh workflow must discover a UniProt record marked `FRAG=1`
when its full stored sequence is literally identical to the target and its
resolved species is admissible. Discovery retains the record so that the
resolver can subsequently apply the configured scientific donor policy.
Discovery itself does not confer donor status.

The published `7250810` baseline already supports this behavior in the native
scanner and in `merge_discovery(..., allow_literal_fragments=True)`. Its FULL
plan and workflow wrappers did not request those capabilities: the plan pinned
`fragment=exclude`, both scanner command descriptions omitted the explicit
switch, and the union wrapper used the default `False`.

The correction changes four production files:

- `stage2c_full_exact.py` produces plan schema v4 and pins `fragment=include`.
- `run_stage_03_full_exact.py` passes that value in both the recorded command
  and the actual native invocation. The scanner summary must confirm the
  literal IUPAC26/same-species contract, consistent pair counts, the requested
  fragment policy, and zero automatically skipped fragments.
- `stage2c_union_plan.py` produces plan schema v3 and includes the literal
  fragment permission in its scientific fingerprint.
- `run_stage_03_discovery_union.py` explicitly enables literal FULL fragments
  and records this choice in the publication manifest.

The scanner C++ source, the union kernel, the discovery TSV schema, taxonomy
rules, literal alphabet semantics, resolver, and GO logic are unchanged.
The GO-positive similarity branch still excludes fragments. The generic union
API retains its previous default, and enabling the FULL permission does not
admit GO-positive fragments, NONEXACT FULL rows, or cross-species records.

Existing FULL v3 and union v2 plans cannot be reused under the new contracts.
Generate new plans after their pinned upstreams exist. Their new keys produce
new output directories; existing plans and scientific publications remain
physically unchanged.

`tests/test_stage2c_fragment_workflow_contract.py` checks the actual native
scanner through both release wrappers, the union wrapper, both command paths,
unchanged taxonomy gates, incorrect scanner-policy rejection before atomic
publication, stale plan rejection, fragment fingerprinting, and negative
controls for NONEXACT/cross-species/GO-positive fragment evidence. It uses
`unittest` and is also collected by the project's normal pytest suite.

This correction does not certify a complete fresh Snakemake run. That gate
requires the upstream reconstruction and end-to-end checks for the pinned
128-target cohort.
