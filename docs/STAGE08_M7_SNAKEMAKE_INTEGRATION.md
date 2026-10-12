# Stage 08 M7 — Snakemake registration and explicit execution gate

Status: integration candidate for review; this is **not** a completed canonical
GOA collection. M1–M6 library tests and bounded M5 profiling have passed on the
server. Stage 01–07 and their published data bundles remain unchanged.

## Scope

M7 adds one `stage_08` rule in `workflow/Snakefile` and the Snakemake runner
`workflow/scripts/run_stage_08.py`. The rule produces the canonical
`results/pipeline-v2/common/08/manifest.json`, uses Stage 07 and both Stage
01B1 bundles plus the common Stage 01B0 policy manifest, and tracks the
implementation sources consumed by M4. `08` becomes a final-stage output
location; it is **not** added to `rule all` or `validate_artifacts`, so existing
normal workflow invocations do not request a 304-GB GAF scan.

The only new configuration key is optional:

```yaml
stage08_collection:
  execute_full_scan: false
```

`PipelineConfig` supplies `false` when absent, validates exact keys and a
literal boolean, and exposes `stage08_collection_parameters()`. No Stage 08
configuration is read from Stage 07's policy accessor or Snakemake rule. The
option is a **safety/authorization gate**, not an evidence or biological
parameter. A future evidence profile must be scoped downstream of raw
collection and must not invalidate this expensive bundle.

A Snakemake `--dry-run` is permitted with either setting. Actual invocation
with the default `false` stops before opening the GAFs, while setting it to
`true` explicitly authorizes a full START+END scan. This is distinct from the
M6 direct CLI's `--execute` flag. Never set `true` merely to test the dry-run.

## Dependencies and invalidation

Stage 08 input includes the Stage 07 completion manifest, both normalized GOA
completion manifests, the GO-policy completion manifest, its own runner and CLI,
and the relevant annotation and publication modules. Changes to Stage 08 code
or its gate are therefore isolated from Stage 07. Changing Stage 07 parameters
causes its manifest to be regenerated under the existing invalidation contract;
this invalidates Stage 08 transitively. No Stage 01–06C1 tasks should be
scheduled when only Stage 07 or Stage 08 parameters change.

M7 tests the above planning claims with **read-only Snakemake dry-runs** on the
canonical run, using temporary copies of the YAML configuration. This checks
planning without publishing anything. With no Stage 08 bundle yet, the dry-run
for either value of the gate naturally schedules Stage 08; it cannot prove
rerun behavior for a *pre-existing completed* Stage 08 bundle. That additional
acceptance test is deferred until after the actual Stage 08 production run.

The runner reuses M4 `publish_raw_annotation_collection()` and permits removal
of stale payloads only when Snakemake has already invalidated the Stage 08
completion manifest. It refuses a completed bundle, unknown stale files,
symlinks and noncanonical destinations. M4 guarantees same-filesystem atomic
publication only after both source scans pass EOF and full SHA256 verification.
The runner does not call the M6 direct CLI, which deliberately forbids
workflow-driven replacement.

## Provenance and follow-up requirements

M4's manifest fingerprints the M1–M4 data-path implementation. Snakemake tracks
the runner and CLI as rule inputs. M8a additionally captures their content SHA256
in the scientific manifest when invoked through the Snakemake runner, and
rechecks those hashes before atomic publication. The direct CLI records its
own implementation source SHA256.

Before any full source scan: verify configuration validation; focused and full
pytest; read-only canonical Snakemake dry-runs for baseline, Stage 08-only and
Stage 07-only changes; a synthetic publication/recovery trial; and disk space.
Do not trigger `stage_08` without `--dry-run` on the canonical run until the
results of those checks have been reviewed and a full scan is authorized.

Stage 08 raw collection does not interpret evidence codes, propagate GO terms,
apply NOT vetoes, construct K0, or select final benchmark truth.

## Pre-production provenance hardening (M8a)

Before the canonical full scan, M4 accepts explicitly supplied executable
sources and records their SHA256 in `manifest.implementation.source_sha256`.
The M7 Snakemake runner supplies both the CLI and the Snakemake wrapper;
the M6 direct CLI supplies its own source. M4 rechecks these files before
publishing, so changing a runner during the full scan fails closed. This
closes the wrapper-provenance gap documented above, without adding any
scientific annotation policy or dependency on a workflow engine to the library.
