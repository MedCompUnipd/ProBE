"""Read-only-input, opt-in Stage 2C evidence and resolver DAG runner.

Outputs are immutable publication directories. Snakemake tracks a separate
ready receipt, never an existing directory or its scientific summary files.
"""

import argparse
import json
import os
import tempfile
from pathlib import Path

from probe.stage2c_workflow_plan import (
    PHASES,
    POLICIES,
    ROLES,
    SUMMARY_NAMES,
    read_plan,
    sha,
)

ctx = globals()["snakemake"]
plan = read_plan(Path(ctx.params.plan_path))
phase = str(ctx.params.phase)
role = str(ctx.params.role)
if not ctx.params.enabled:
    raise ValueError("Stage 2C execution disabled; dry-run is permitted")
if phase in PHASES and role in ROLES:
    dest = Path(plan["paths"][role][phase])
elif phase in ("preview", "completion") and role == "common":
    dest = Path(plan["paths"]["common"][phase])
elif phase == "resolver" and role in POLICIES:
    dest = Path(plan["paths"]["resolver"][role])
else:
    raise ValueError("Invalid Stage 2C role/phase")

stamp = Path(ctx.output[0])
if stamp != dest.parent / f"{dest.name}.ready.json":
    raise ValueError("Incorrect Stage 2C ready-receipt path")
if dest.is_symlink() or stamp.is_symlink():
    raise ValueError("Symlinked Stage 2C publication is forbidden")
repo = Path(plan["repo"])
run = Path(plan["run"])
work = run / "work/pipeline-v2"
results = run / "results/pipeline-v2"
releases = plan["releases"]
role_release = releases.get(role)
source = {
    "targets": work / "common/01/01_targets.tsv",
    "legacy": results / "common/05A2/05A2_final_candidate_union.tsv",
    "audit": results / "common/05C2/05C2_taxonomy_audit.tsv",
    "go": work / f"{role}/01C/manifest.json",
    "full": work / f"{role}/01A_FULL/manifest.json",
    "taxonomy": work / f"{role}/02B/02B_target_taxonomy.tsv",
    "store": work / f"{role}/05D/05D_candidate_sequences.tsv",
}
union_plan = json.loads(Path(plan["union_spec"]).read_text(encoding="utf-8"))
union = Path(union_plan["paths"][role]) if role in ROLES else None
species = Path(union_plan["species_paths"][role]) if role in ROLES else None
full = Path(union_plan["full_paths"][role]) if role in ROLES else None
role_paths = plan["paths"].get(role, {})
summary = dest / SUMMARY_NAMES[phase]


def perform() -> None:
    """Invoke only the established, validated Stage 2C scientific API."""
    if phase == "delta":
        from probe.stage2c_candidate_delta import reconcile_candidate_delta

        with tempfile.TemporaryDirectory(
            prefix=".stage2c-delta-", dir=dest.parent
        ) as temp:
            publication = Path(temp) / "published"
            publication.mkdir()
            reconcile_candidate_delta(
                release=role_release,
                legacy_union=source["legacy"],
                discovery_union=union / "03_unified_discovery.tsv",
                output=publication / "2C_candidate_delta.tsv",
                summary=publication / SUMMARY_NAMES[phase],
            )
            if dest.exists() or dest.is_symlink():
                raise FileExistsError("Delta destination already exists")
            os.replace(publication, dest)
    elif phase == "bridge":
        from probe.stage2c_evidence_bridge import prepare_evidence_bridge

        prepare_evidence_bridge(
            release=role_release,
            delta=Path(role_paths["delta"]) / "2C_candidate_delta.tsv",
            go_positive_pairs=species / "03_primary_candidates.pairs.tsv",
            full_exact_pairs=full / "03_full_exact_cross_strain.pairs.tsv",
            output_dir=dest,
        )
    elif phase == "reuse":
        from probe.stage2c_metadata_reuse import reuse_05d_metadata

        reuse_05d_metadata(
            release=role_release,
            requests=Path(role_paths["bridge"]) / "2C_full_record_requests.tsv",
            store_05d=source["store"],
            output_dir=dest,
        )
    elif phase == "full_recovery":
        from probe.stage2c_full_metadata_recovery import (
            recover_pending_full_metadata,
        )

        recover_pending_full_metadata(
            release=role_release,
            pending_requests=Path(role_paths["reuse"]) / "2C_pending_full_metadata.tsv",
            full_v2_manifest=source["full"],
            output_dir=dest,
        )
    elif phase == "reconcile":
        from probe.stage2c_metadata_reconciliation import reconcile_full_metadata

        reconcile_full_metadata(
            release=role_release,
            requests=Path(role_paths["bridge"]) / "2C_full_record_requests.tsv",
            reuse_dir=Path(role_paths["reuse"]),
            canonical_05d_store=source["store"],
            recovery_dir=Path(role_paths["full_recovery"]),
            output_dir=dest,
        )
    elif phase == "projection":
        from probe.stage2c_primary_projection import prepare_primary_projection

        prepare_primary_projection(
            release=role_release,
            canonical_05a2=source["legacy"],
            bridge=Path(role_paths["bridge"]) / "2C_evidence_bridge.tsv",
            target_taxonomy=source["taxonomy"],
            prepared_targets=source["targets"],
            full_metadata_dir=Path(role_paths["reconcile"]),
            output_dir=dest,
        )
    elif phase == "coverage":
        from probe.stage2c_sequence_coverage import build_sequence_coverage

        build_sequence_coverage(
            release=role_release,
            projection_dir=Path(role_paths["projection"]),
            store_05d=source["store"],
            prepared_targets=source["targets"],
            full_metadata_dir=Path(role_paths["reconcile"]),
            output_dir=dest,
        )
    elif phase == "go_recovery":
        from probe.stage2c_go_sequence_recovery import recover_pending_go

        recover_pending_go(
            release=role_release,
            coverage_dir=Path(role_paths["coverage"]),
            go_manifest=source["go"],
            output_dir=dest,
        )
    elif phase == "preview":
        from probe.stage2c_alignment_preview import build_alignment_preview

        rp = plan["paths"]
        build_alignment_preview(
            start_release=releases["START"],
            end_release=releases["END"],
            canonical_05a2=results / "common/05A2/05A2_final_candidate_union.tsv",
            canonical_05c2=results / "common/05C2/05C2_taxonomy_audit.tsv",
            canonical_start_05d=work / "START/05D/05D_candidate_sequences.tsv",
            canonical_end_05d=work / "END/05D/05D_candidate_sequences.tsv",
            projection_start=Path(rp["START"]["projection"]),
            projection_end=Path(rp["END"]["projection"]),
            coverage_start=Path(rp["START"]["coverage"]),
            coverage_end=Path(rp["END"]["coverage"]),
            recovered_go_start=Path(rp["START"]["go_recovery"]),
            recovered_go_end=Path(rp["END"]["go_recovery"]),
            targets=work / "common/01/01_targets.tsv",
            stage06a_script=repo / "scripts/pipeline/06A_prepare_alignment_jobs.py",
            output_dir=dest,
        )
    elif phase == "completion":
        from probe.stage2c_alignment_completion import (
            complete_alignment_preview,
        )

        complete_alignment_preview(
            preview=Path(plan["paths"]["common"]["preview"]),
            targets=work / "common/01/01_targets.tsv",
            work_root=work,
            canonical_primary=results / "common/05A2/05A2_final_candidate_union.tsv",
            nonexact_script=repo / "scripts/pipeline/NONEXACT_select_alignment_jobs.py",
            full_dp_binary=repo
            / ".pixi/build/probe-directional-glocal-full-dp"
            / "probe-directional-glocal-full-dp-v1",
            finalize_script=repo
            / "scripts/pipeline/06C1_finalize_directional_glocal.py",
            output_dir=dest,
            threads=int(ctx.threads),
            max_pair_cells=50_000_000,
            max_total_cells=500_000_000,
            reuse_canonical=results / "common/06C1",
        )
    elif phase == "resolver":
        from probe.stage2c_alignment_completion import SUMMARY
        from probe.stage2c_alignment_preview import SUMMARY_OUT

        # These must exist and have been published before variant evaluation.
        preview = Path(plan["paths"]["common"]["preview"])
        completion = Path(plan["paths"]["common"]["completion"])
        if not (preview / SUMMARY_OUT).is_file():
            raise ValueError("Missing shared 06A evidence")
        if not (completion / SUMMARY).is_file():
            raise ValueError("Missing shared directional alignments")
        from importlib.util import module_from_spec, spec_from_file_location

        script = repo / "scripts/pipeline/07_resolve_stage2c_variant.py"
        spec = spec_from_file_location("probe_stage2c_resolver_cli", script)
        if spec is None or spec.loader is None:
            raise ValueError("Missing resolver CLI entrypoint")
        module = module_from_spec(spec)
        spec.loader.exec_module(module)
        module.run(
            argparse.Namespace(
                work_root=work,
                preview=preview,
                completion=completion,
                start_release_id=releases["START"],
                end_release_id=releases["END"],
                strain_policy=role,
                partial_sequence="allow",
                documentary_fragment="allow",
                output_dir=dest,
                execute=True,
            )
        )
    else:
        raise ValueError("Unknown Stage 2C integration phase")


if not dest.resolve().is_relative_to(Path(plan["root"]).resolve()):
    raise ValueError("Output escaped pinned Stage 2C experiment")
for parent in (dest, dest.parent, Path(plan["root"])):
    if parent.is_symlink():
        raise ValueError("Symlinked Stage 2C output destination")
if stamp.exists() or stamp.is_symlink():
    raise FileExistsError("Stage 2C receipt already exists")
stamp.parent.mkdir(parents=True, exist_ok=True)
if not dest.exists():
    dest.parent.mkdir(parents=True, exist_ok=True)
    perform()
OUTPUTS = {
    "delta": ("2C_candidate_delta.tsv",),
    "bridge": ("2C_evidence_bridge.tsv", "2C_full_record_requests.tsv"),
    "reuse": ("2C_reused_full_metadata.tsv", "2C_pending_full_metadata.tsv"),
    "full_recovery": (
        "2C_recovered_full_metadata.tsv",
        "2C_recovered_full_sequences.tsv",
    ),
    "reconcile": ("2C_all_full_metadata.tsv", "2C_all_full_sequences.tsv"),
    "projection": (
        "2C_05A2_primary_preview.tsv",
        "2C_05A2_new_candidates.tsv",
        "2C_05A2_projection_audit.tsv",
    ),
    "coverage": (
        "2C_05D_sequence_coverage.tsv",
        "2C_05D_new_sequence_annex.tsv",
        "2C_05D_pending_go_sequences.tsv",
    ),
    "go_recovery": ("2C_go_positive_sequence_annex.tsv",),
    "preview": (
        "2C_05A2_variant.tsv",
        "2C_05C2_variant.tsv",
        "2C_05D_START_variant.tsv",
        "2C_05D_END_variant.tsv",
        "2C_06A_alignment_jobs.tsv",
        "2C_06A_summary.json",
    ),
    "completion": (
        "2C_NONEXACT_alignment_jobs.tsv",
        "2C_NONEXACT_summary.json",
        "2C_policy_skipped_nonexact.tsv",
        "2C_06C0_nonexact_directional_glocal.tsv",
        "2C_06C0_summary.tsv",
        "2C_06C1_canonical_directional_glocal.tsv",
        "2C_06C1_summary.json",
    ),
    "resolver": ("2C_07_complete.tsv", "2C_07_accepted.tsv"),
}
SCHEMAS = {
    "delta": "stage2c-candidate-delta-v1",
    "bridge": "stage2c-evidence-bridge-v1",
    "reuse": "stage2c-full-metadata-reuse-v1",
    "full_recovery": "stage2c-pending-full-v2-recovery-v1",
    "reconcile": "stage2c-full-metadata-reconciliation-v1",
    "projection": "stage2c-primary-projection-v1",
    "coverage": "stage2c-sequence-coverage-v1",
    "go_recovery": "stage2c-go-pending-sequence-recovery-v1",
    "preview": "stage2c-alignment-preview-v1",
    "completion": "stage2c-alignment-completion-v1",
    "resolver": "stage2c-resolver-variant-v1",
}

if not dest.is_dir() or not summary.is_file():
    raise ValueError("Incomplete Stage 2C output publication")
report = json.loads(summary.read_text(encoding="utf-8"))
if not isinstance(report, dict) or report.get("schema") != SCHEMAS[phase]:
    raise ValueError("Malformed or unrecognized Stage 2C summary")
expected_files = set(OUTPUTS[phase]) | {SUMMARY_NAMES[phase]}
actual_files = {p.name for p in dest.iterdir() if p.is_file()}
if actual_files != expected_files:
    raise ValueError("Stage 2C publication contains unexpected files")
hashes = report.get("output_sha256", {})
if not isinstance(hashes, dict):
    raise ValueError("Invalid declared Stage 2C content checksums")
for name, expected in hashes.items():
    if name not in OUTPUTS[phase] or sha(dest / name) != expected:
        raise ValueError(f"Stage 2C output changed: {name}")
if phase == "resolver" and report.get("strain_policy") != role:
    raise ValueError("Resolver output policy mismatch")
if phase == "resolver" and report.get("discovery_reexecuted") is not False:
    raise ValueError("Resolver must reuse shared discovery")
if phase in PHASES and report.get("release") not in (None, role_release):
    raise ValueError("Role-scoped result has the wrong release")
receipt = {
    "schema": plan["schema"],
    "integration_key": plan["key"],
    "phase": phase,
    "role": role,
    "summary": str(summary),
    "summary_sha256": sha(summary),
    "publication_sha256": {name: sha(dest / name) for name in sorted(expected_files)},
}
with tempfile.NamedTemporaryFile(
    dir=stamp.parent, prefix=".stage2c-ready-", mode="w", delete=False
) as stream:
    temporary = Path(stream.name)
    json.dump(receipt, stream, indent=2, sort_keys=True)
    stream.write("\n")
try:
    os.link(temporary, stamp)
finally:
    temporary.unlink(missing_ok=True)
print(f"STAGE2C_VARIANT_READY={phase}:{role}")
