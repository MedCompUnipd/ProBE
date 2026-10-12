# ProBE Stage 08 — Immutable evidence variants, first integrated increment

Status: implementation for repository validation, **not yet approved as a
production workflow**. Keep the PROGENY rename deferred.

## Scope and guarantees

This increment integrates the **M12 SCORE/NON_SCORE/EXCLUDED evidence-policy
variants** and the existing benchmark diagnostic into `workflow/Snakefile`.
It does not implement isolated Stage 07 resolver variants; changing
`partial_sequence`, `documentary_fragment`, or `subtaxon_policy` is explicitly
outside this increment. No original GOA or GAF is reread.

Each created experiment stores:

- `config.resolved.json`: a complete snapshot of effective pipeline settings,
  effective absolute input paths, release IDs, code SHA256, source SHA256,
  evidence policy hash, registry hash, filter settings and artifact paths.
- `policy.json`, `registry.json`: snapshotted original user evidence settings.
- `experiment.yaml`: additive config containing only an opt-in specification.
- `PARAMETERS.md`: human-readable identity and output locations.

The Snakemake variant DAG has two optional producing rules, using published
M10c as a file dependency rather than a Stage 07 or M8 workflow target:

1. `publish_stage_08_benchmark_variant`: M12 V2, full source identity checks,
   manifest and output publication performed by the existing validated M12
   implementation; fail closed if output destination already exists.
2. `publish_stage_08_diagnostics_variant`: existing benchmark profiler, with
   diagnostic filters that do not change M12. A permanent rule
   `stage_08_variant_diagnostics` targets the diagnostic manifest.

If a fully published fingerprinted artifact already exists, the relevant
producing rule is not defined for that invocation; it is reused as an input.
The previous pilot V1 and V2 output directories are never overwritten.

## CLI usage on thanos

The repository must be committed and clean before creating an experiment.

```bash
cd /data/PROGENY/ProBE-chatgpt
RUN=/data/PROGENY/ProBE-runs/pre-resolver-v2-2023_01-2026_03

pixi run python scripts/pipeline/08_manage_variants.py --run "$RUN" create \
  --name baseline-evidence \
  --policy config/stage08_evidence_classes_v2.example.json \
  --registry config/stage08_evidence_registry.example.json \
  --ontology /data/PROGENY/ProBE/TESTING/go.owl
```

Copy the returned `experiment_id` and inspect the plan *before* execution:

```bash
pixi run python scripts/pipeline/08_manage_variants.py \
  --run "$RUN" plan "$EXPERIMENT_ID"
```

Expected first-use work: only `publish_stage_08_benchmark_variant` and
`publish_stage_08_diagnostics_variant`, never `stage_07` or `stage_08`.
Only after reviewing the plan:

```bash
pixi run python scripts/pipeline/08_manage_variants.py \
  --run "$RUN" execute "$EXPERIMENT_ID" --cores 1
```

For a length-restricted *diagnostic only*, use another experiment name and
`--min-length 1023` on `create`. The M12 artifact key remains unchanged; only
its diagnostic key differs.

To select different evidence classifications, edit a **new policy copy** and
pass its path to `create`, leaving the previous experiment snapshot untouched.

To inventory and compare identities:

```bash
pixi run python scripts/pipeline/08_manage_variants.py --run "$RUN" list
pixi run python scripts/pipeline/08_manage_variants.py --run "$RUN" \
  compare "$EXPERIMENT_ID" "$OTHER_EXPERIMENT_ID"
```

`compare` reports identity, policy and filter differences, **not** yet a
quantitative protein/GO set diff. For the latter, compare the TSV diagnostics.

To preview deletion without changing anything:

```bash
pixi run python scripts/pipeline/08_manage_variants.py --run "$RUN" \
  remove "$EXPERIMENT_ID" --delete-unreferenced
```

Deletion needs the exact ID repeated as `--confirm-id "$EXPERIMENT_ID"`.
An artifact referenced by any other registered experiment is preserved.
Never remove experiment folders manually when artifacts are shared. The
pilot V1 and V2, M8 and M10c live outside this variant registry and are
never deletion candidates.

## Remaining work

- Stage 07 resolver variants require an isolated work/results publication
  root and upstream validation; do not route changed donor policies to the
  current canonical results directory.
- Build a canonical global GOA START IA branch, separate from the target-only
  exploratory IA program, then add an optional frozen-IA input to profiling.
- Add a numeric `compare` report for changing protein/GO sets and a disk-usage
  estimate in `list`.
- Add M13 scoring without changing truth/skip/neutral masks.

Fail-closed invariant: changing scientific inputs must change the artifact
fingerprint or refuse to reuse its outputs. The Snakemake dry-run on the real
server is required before declaring workflow integration validated.
