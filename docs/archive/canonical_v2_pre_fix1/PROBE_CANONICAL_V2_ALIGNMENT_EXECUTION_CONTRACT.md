# ProBE V2 — independent canonical alignment execution (06C)

This increment runs the **existing** `NONEXACT_select_alignment_jobs.py`,
full directional-glocal DP executable, and `06C1_finalize_directional_glocal.py`
without requiring any historic Stage 05A2 publication.

Its inputs are the installed V2 Complete Workload and the release-specific
02B resolved taxonomy tables. It validates all 06A jobs against the same
release's V2 candidate table and independently verifies nonexact target and
candidate resolved TaxID from source records. Cross-TaxID NONEXACT records
are retained in a sidecar (`2C_policy_skipped_nonexact.tsv`) and never enter
full DP. SAME-TaxID NONEXACT jobs use the frozen numerical DP executable;
EXACT jobs are handled algebraically by the unchanged 06C1 finalizer.

* It does not assert equivalence of gene loci, accept/documentarily approve
  candidates, decide GO donors, or read GOA files.
* START and END candidate and GO release identities are never combined.
* `CANONICAL_DOCUMENTARY_V2` is a provenance channel, **not** a donor gate.
* The upstream V2 outputs and SHA256 are checked before use; outputs are
  published atomically to a new directory, with their own SHA manifest.
* Full DP budgets are fail-closed; optional chunking calls the previously
  validated scientific 06C0 kernel and preserves pair boundaries.
* The downstream V2 resolver MUST reconstruct nonexact cross-TaxID skipped
  candidates as ineligible from the published sidecar; their absence from the
  06C1 aligned table must not be interpreted as candidate disappearance.
* Frozen `eeac66878bdfb8e1b96fdff6b297a675214912db699d0a715101c5a079ab7990`
  publications, source scripts and SHA256 remain unchanged.

This is a standalone 06C integration component, **not** a finished Snakemake
DAG or scientifically validated Stage 07 resolver. Production execution is
explicit (`--execute`); preflight is the default CLI action.

Synthetic local verification: six gate/DP tests and an integration run using
an independently compiled copy of the repository's actual C++ DP source,
which produced five 06C1 alignments for three EXACT and two NONEXACT pairs.
Final acceptance still requires Pixi Ruff and regression tests on `thanos`.
