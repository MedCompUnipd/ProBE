# ProBE Stage 2C — Snakemake integration contract (14 October 2026)

> **Status update (2026-10-16).** This is an installation snapshot from
> 2026-10-14. Its cautions that the scanners have not run and that exact-only
> donors lack documentary continuity are historical, **not** current facts.
> The verified final scientific policy, run fingerprint and result summary
> are in [`PROBE_STAGE2C_EXACT_DONOR_CONTRACT_V1.md`](PROBE_STAGE2C_EXACT_DONOR_CONTRACT_V1.md).
> The Stage 2C DAG remains opt-in; canonical outputs are unchanged.

## Baseline captured by the remote preflight (before installation)

- Branch `chatgpt/pre-resolver-v2`, HEAD `0ab110820d67e857186c0e5732a849ea894ab3bd`.
- 33 untracked files Stage 2C4C1–2C4E1, no modified tracked source.
- Existing Stage 2C discovery Snakemake dry-run passed.
- 261 input/output manifest references; 54 scientific TSV headers; zero manifest warnings.
- `workflow/Snakefile` original SHA256 `3c449a1f217a2aab8471af72cb9c53beb267783f1703c857506b84e4401c217a`.

## DAG and output contracts

- Shared discovery START/END: GO-positive species-wide scan, FULL exact scan, candidate union.
- Eight per-role evidence steps: delta, bridge, reuse, full recovery, reconciliation, projection, sequence coverage, GO sequence recovery. 16 role-scoped jobs total.
- One shared 05A2/05C2/05D/06A alignment preview and one shared NONEXACT/06C0/06C1 completion.
- Two Stage 07 resolver variants, `strict` and `same_species_exact`, with identical upstream receipts.
- One aggregation target, `stage_2c_variants_both`.
- New experiment plan hashes source files and frozen canonical manifests, and the existing Stage 2C union key. Variant output paths are content-addressed and isolated.
- Snakemake tracks SHA-stamped receipts **outside** the immutable published directories. No use of `directory()` on scientific publications; no canonically named `manifest.json` invented for ad hoc summaries.
- The runner checks every phase's summary schema and enumerated scientific output files. It verifies all output hashes declared in the summaries and records SHA256 hashes for every published file in its receipt.
- REAL execution is **OFF** by default (`stage2c_execute_variant=false`); installer explicitly performs dry-run only. Never set true before an independent scientific validation and resource review.

## Installation verification on thanos (14 October 2026)

The isolated Snakemake integration was installed and checked successfully:

- Installer verdict: `STAGE2C_SNAKEMAKE_ISOLATED_DAG_INSTALL_AND_DRYRUN_PASS`.
- Stage 2C focused regression: **235 passed**.
- Complete project regression: **1,215 passed, 1 skipped** (the optional local
  UniProt V2 Python oracle was unavailable).
- Ruff scoped verification and strict E501 (88 columns): **PASS**.
- Snakemake v9.27.0 dry-run: **29 jobs**; no scientific job was executed.
- DAG structure: 2 compiled binaries scheduled; 6 GO-positive/FULL/union
  discovery jobs (START/END); 16 evidence jobs (8 per role); 1 shared preview;
  1 shared alignment completion; 2 resolver policies; 1 aggregate target.
- `stage2c_execute_variant=false` was explicitly passed for validation.
- Validation: `STAGE2C_ISOLATED_DAG_VALIDATED=PASS`;
  `PUBLISHED_CANONICAL_MANIFESTS_UNCHANGED=PASS`;
  `NO_SCIENTIFIC_SCAN_NO_ALIGNMENT_NO_COMMIT_NO_PUSH=PASS`.
- Git HEAD remained `0ab110820d67e857186c0e5732a849ea894ab3bd`.
  Immediately after installation, the worktree contained 38 untracked files
  and one tracked modification to `workflow/Snakefile`.
- Integration plan key:
  `c9e9637b85506d8cbb72e9e043fc78cb83a52c4696acb56240bbf3b47aed786b`.
- Review archive on thanos:
  `/data/PROGENY/tmp/probe_2c_smk_7y18o0NR/review_package.tar.gz`.

The dry-run issued a nonfatal warning concerning missing Snakemake
provenance/metadata for `build_full_exact_cross_strain_v1`. Revisit this
before relying on automatic rerun detection; it is not evidence of a failed
DAG and did not trigger a scientific build.

## Known limitations and checkpoints before real data runs

1. **No new species-wide or FULL scanners have yet run on full releases.** The successful Snakemake DAG is a graph validation, not publication of evidence.
2. The FULL metadata recovery reads a complete pinned FASTA only when Stage 05D reuse leaves pending RID requests. GO recovery scans GO-positive FASTA only if pending candidates exist. Budget actual full runs independently.
3. **The Stage 2C4E1 resolver uses an exact-sequence gate to override a baseline decision.** Before authorizing real GO donor output, audit the complete scientific precedence, especially fragment provenance, unverified documentary evidence, and taxonomy audit scope. The installer **does not** authorize that stage's execution.
4. Canonical Stage 01–08 artifacts are immutable. Existing shared Stage 2C scanner and union plans must be passed explicitly. Do not allow Snakemake canonical Stage 05–07 rules as part of this DAG.
5. Local validation used a source snapshot from thanos. The actual Pixi Ruff checks, focused and full pytest suites, and Snakemake dry-run later passed on thanos. The installer has scoped rollback logic for failures; no rollback was required in the successful run.

## Installation command (already executed; do not repeat)

For reproducibility, `probe_stage2c_snakemake_integration.sh` was copied to `/data/PROGENY/tmp/` and executed on 14 October 2026:

```bash
bash /data/PROGENY/tmp/probe_stage2c_snakemake_integration.sh --apply
```

The installer performed source SHA preflight, Ruff in actual project context, Stage 2C and full tests, created a separate immutable integration plan, ran `snakemake --dry-run` with `--allowed-rules`, and verified canonical manifest SHA256 invariance. The resulting `REPORT_ARCHIVE`, installation log, and dry-run log were reviewed. Do not rerun the installer as a normal next step.
