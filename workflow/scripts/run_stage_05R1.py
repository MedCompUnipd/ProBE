import subprocess
import sys
from pathlib import Path

from probe.workflow.artifacts import atomic_stage_bundle, validate_bundle
from probe.workflow.candidate_scan import (
    preflight_candidate_scan,
    read_candidate_scan_summary,
    write_empty_candidate_scan_outputs,
)
from probe.workflow.outcome import write_stage_outcome
from probe.workflow.runtime import input_artifact, runtime_environment

context = globals()["snakemake"]

role = str(context.wildcards.role)
if role not in {"START", "END"}:
    raise ValueError(f"05R1 unknown role {role}")

policy = dict(context.params.candidate_policy)

rescue_manifest_path = Path(context.input.rescue_manifest)
full_taxonomy_manifest_path = Path(context.input.full_taxonomy_manifest)
full_v2_manifest_path = Path(context.input.full_v2_manifest)
binary = Path(context.input.binary)

destination = Path(context.output[0]).parent


def load_manifest(path: Path):
    return validate_bundle(path.parent, deep=False)


rescue_manifest = load_manifest(rescue_manifest_path)
full_taxonomy_manifest = load_manifest(full_taxonomy_manifest_path)
full_v2_manifest = load_manifest(full_v2_manifest_path)

for label, manifest, stage in (
    ("rescue", rescue_manifest, "05R0"),
    ("full taxonomy", full_taxonomy_manifest, "04D0b"),
    ("full V2", full_v2_manifest, "01A_FULL"),
):
    if manifest.stage != stage:
        raise ValueError(
            f"05R1 {label} expected stage {stage}, got {manifest.stage}"
        )
    if manifest.role != role:
        raise ValueError(
            f"05R1 {label} expected role {role}, got {manifest.role}"
        )
    if manifest.release_id is None:
        raise ValueError(f"05R1 {label} requires a release_id")

release_id = rescue_manifest.release_id

if full_taxonomy_manifest.release_id != release_id:
    raise ValueError(
        "05R1 upstream release mismatch: "
        f"05R0={release_id} "
        f"04D0b={full_taxonomy_manifest.release_id}"
    )

if full_v2_manifest.release_id != release_id:
    raise ValueError(
        "05R1 upstream release mismatch: "
        f"05R0={release_id} "
        f"01A_FULL={full_v2_manifest.release_id}"
    )

rescue_targets_name = "rescue_targets.tsv"
rescue_taxonomy_name = "rescue_target_taxonomy.tsv"
full_ox_taxonomy_name = "full_ox_taxonomy.tsv"
full_v2_fasta_name = "01A_full_v2.fasta"

for label, manifest, filenames in (
    (
        "05R0",
        rescue_manifest,
        (rescue_targets_name, rescue_taxonomy_name),
    ),
    (
        "04D0b",
        full_taxonomy_manifest,
        (full_ox_taxonomy_name,),
    ),
    (
        "01A_FULL",
        full_v2_manifest,
        (full_v2_fasta_name,),
    ),
):
    declared = {item.path for item in manifest.outputs}
    for filename in filenames:
        if filename not in declared:
            raise ValueError(
                f"{label} bundle does not declare {filename}"
            )

rescue_bundle = rescue_manifest_path.parent
full_taxonomy_bundle = full_taxonomy_manifest_path.parent
full_v2_bundle = full_v2_manifest_path.parent

targets = rescue_bundle / rescue_targets_name
target_taxonomy = rescue_bundle / rescue_taxonomy_name
ox_taxonomy = full_taxonomy_bundle / full_ox_taxonomy_name
fasta = full_v2_bundle / full_v2_fasta_name

output_prefix_name = "05R1_full_sequence_rescue"
pairs_name = f"{output_prefix_name}.pairs.tsv"
unsearchable_name = (
    f"{output_prefix_name}.unsearchable_targets.tsv"
)
summary_name = f"{output_prefix_name}.summary.tsv"
outcome_name = "stage_outcome.json"

preflight = preflight_candidate_scan(
    targets,
    target_taxonomy,
    taxonomy_policy=str(policy["taxonomy_policy"]),
)

binary_artifact = input_artifact(binary)

snapshot = f"FULL-RESCUE-{release_id}"

recorded_command = [
    str(binary.resolve()),
    "--targets",
    str(targets.resolve()),
    "--target-taxonomy",
    str(target_taxonomy.resolve()),
    "--ox-taxonomy",
    str(ox_taxonomy.resolve()),
    "--fasta",
    str(fasta.resolve()),
    "--snapshot",
    snapshot,
    "--out-prefix",
    f"<stage-bundle>/{output_prefix_name}",
    "--k",
    str(policy["k"]),
    "--max-required-shared",
    str(policy["max_required_shared"]),
    "--min-target-kmer-fraction",
    str(policy["min_target_kmer_fraction"]),
    "--fragment-policy",
    str(policy["fragment_policy"]),
    "--taxonomy-policy",
    str(policy["taxonomy_policy"]),
]

parameters = {
    "k": policy["k"],
    "max_required_shared": policy["max_required_shared"],
    "min_target_kmer_fraction": policy[
        "min_target_kmer_fraction"
    ],
    "fragment_policy": policy["fragment_policy"],
    "taxonomy_policy": policy["taxonomy_policy"],
    "candidate_universe": "FULL_V2",
    "rescue_algorithm": "SAME_AS_STAGE_03",
    "snapshot": snapshot,
    "command": recorded_command,
    "command_executed": preflight.eligible_target_count > 0,
}

implementation = {
    "name": "probe-primary-candidate-scan-v1",
    "version": "1",
    "language": "C++20",
    "binary_sha256": binary_artifact.sha256,
    "build_task": "build-primary-candidate-scan",
}

inputs = (
    input_artifact(rescue_manifest_path),
    input_artifact(full_taxonomy_manifest_path),
    input_artifact(full_v2_manifest_path),
    binary_artifact,
)

expected_outputs = {
    pairs_name,
    unsearchable_name,
    summary_name,
    outcome_name,
}

log_path = Path(context.log[0])
log_path.parent.mkdir(parents=True, exist_ok=True)

with log_path.open(
    "w",
    encoding="utf-8",
) as log, atomic_stage_bundle(
    destination,
    stage="05R1",
    role=role,
    release_id=release_id,
    parameters=parameters,
    ontology=None,
    implementation=implementation,
    inputs=inputs,
    environment=runtime_environment(),
    allow_invalidated_destination=True,
) as staging:
    output_prefix = staging / output_prefix_name
    outcome_path = staging / outcome_name

    outcome_counts = {
        "input_targets": preflight.target_count,
        "taxonomy_rows": preflight.taxonomy_count,
        "eligible_targets": preflight.eligible_target_count,
        "unresolved_targets": preflight.unresolved_target_count,
        "empty_primary_bucket_targets": preflight.empty_primary_bucket_count,
        "output_pairs": 0,
    }

    if preflight.target_count == 0:
        write_empty_candidate_scan_outputs(
            output_prefix,
            snapshot=snapshot,
            policy=policy,
        )

        write_stage_outcome(
            outcome_path,
            state="SKIPPED_EMPTY_INPUT",
            reason_code="NO_RESCUE_TARGETS",
            message=(
                "Stage 05R0 produced no rescue targets. "
                "The full-V2 rescue scan was not required."
            ),
            counts=outcome_counts,
        )

        warning = (
            f"WARNING [05R1 {role}]: "
            "Stage 05R0 produced no rescue targets; full-V2 rescue scan "
            "skipped as a valid empty outcome."
        )
        print(warning, file=log)
        print(warning, file=sys.stderr)

    elif preflight.eligible_target_count == 0:
        write_empty_candidate_scan_outputs(
            output_prefix,
            snapshot=snapshot,
            policy=policy,
        )

        write_stage_outcome(
            outcome_path,
            state="SKIPPED_NO_ELIGIBLE_TARGETS",
            reason_code="NO_TAXONOMICALLY_ELIGIBLE_TARGETS",
            message=(
                "Rescue targets were present, but none satisfied the "
                "configured taxonomy policy. "
                "The full-V2 rescue scan was not run."
            ),
            counts=outcome_counts,
        )

        warning = (
            f"WARNING [05R1 {role}]: "
            f"{preflight.target_count} target(s) present but none are "
            "taxonomically eligible; full-V2 rescue scan skipped "
            "as a valid empty outcome."
        )
        print(warning, file=log)
        print(warning, file=sys.stderr)

    else:
        command = [
            str(binary),
            "--targets",
            str(targets),
            "--target-taxonomy",
            str(target_taxonomy),
            "--ox-taxonomy",
            str(ox_taxonomy),
            "--fasta",
            str(fasta),
            "--snapshot",
            snapshot,
            "--out-prefix",
            str(output_prefix),
            "--k",
            str(policy["k"]),
            "--max-required-shared",
            str(policy["max_required_shared"]),
            "--min-target-kmer-fraction",
            str(policy["min_target_kmer_fraction"]),
            "--fragment-policy",
            str(policy["fragment_policy"]),
            "--taxonomy-policy",
            str(policy["taxonomy_policy"]),
        ]

        subprocess.run(
            command,
            stdout=log,
            stderr=subprocess.STDOUT,
            check=True,
        )

        scan_summary = read_candidate_scan_summary(staging / summary_name)

        if scan_summary.targets_loaded != preflight.eligible_target_count:
            raise RuntimeError(
                "05R1 preflight/scanner target-count mismatch: "
                f"preflight={preflight.eligible_target_count} "
                f"scanner={scan_summary.targets_loaded}"
            )

        outcome_counts["output_pairs"] = scan_summary.pairs_emitted

        if scan_summary.pairs_emitted == 0:
            write_stage_outcome(
                outcome_path,
                state="EMPTY_RESULT",
                reason_code="NO_SEQUENCE_CANDIDATES",
                message=(
                    "The full-V2 rescue scan completed successfully "
                    "but emitted no sequence candidate pairs."
                ),
                counts=outcome_counts,
            )

            warning = (
                f"WARNING [05R1 {role}]: full-V2 rescue scan completed "
                "successfully but emitted zero candidate pairs."
            )
            print(warning, file=log)
            print(warning, file=sys.stderr)

        else:
            write_stage_outcome(
                outcome_path,
                state="COMPLETED",
                reason_code="CANDIDATE_PAIRS_EMITTED",
                message=(
                    "The full-V2 rescue scan completed successfully "
                    "and emitted sequence candidate pairs."
                ),
                counts=outcome_counts,
            )

    produced = {
        path.relative_to(staging).as_posix()
        for path in staging.rglob("*")
        if path.is_file()
    }

    if produced != expected_outputs:
        raise RuntimeError(
            "05R1 output contract violation: "
            f"missing={sorted(expected_outputs - produced)} "
            f"unexpected={sorted(produced - expected_outputs)}"
        )
