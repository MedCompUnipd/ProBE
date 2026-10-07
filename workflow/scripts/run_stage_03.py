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
policy = dict(context.params.candidate_policy)

target_manifest_path = Path(context.input.target_manifest)
go_positive_manifest_path = Path(context.input.go_positive_manifest)
taxonomy_manifest_path = Path(context.input.taxonomy_manifest)
binary = Path(context.input.binary)

destination = Path(context.output[0]).parent

target_bundle = target_manifest_path.parent
go_positive_bundle = go_positive_manifest_path.parent
taxonomy_bundle = taxonomy_manifest_path.parent

target_manifest = validate_bundle(target_bundle, deep=False)
go_positive_manifest = validate_bundle(go_positive_bundle, deep=False)
taxonomy_manifest = validate_bundle(taxonomy_bundle, deep=False)

if target_manifest.stage != "01":
    raise ValueError(
        f"03 expected upstream stage 01, got {target_manifest.stage}"
    )

if target_manifest.role is not None:
    raise ValueError(
        f"03 expected common stage 01, got role {target_manifest.role}"
    )

if go_positive_manifest.stage != "01C":
    raise ValueError(
        "03 expected upstream stage 01C, "
        f"got {go_positive_manifest.stage}"
    )

if taxonomy_manifest.stage != "02B":
    raise ValueError(
        "03 expected upstream stage 02B, "
        f"got {taxonomy_manifest.stage}"
    )

if go_positive_manifest.role != role:
    raise ValueError(
        "03 GO-positive role mismatch: "
        f"expected {role}, got {go_positive_manifest.role}"
    )

if taxonomy_manifest.role != role:
    raise ValueError(
        "03 taxonomy role mismatch: "
        f"expected {role}, got {taxonomy_manifest.role}"
    )

release_id = go_positive_manifest.release_id
if release_id is None:
    raise ValueError("03 requires an upstream release_id")

if taxonomy_manifest.release_id != release_id:
    raise ValueError(
        "03 upstream release mismatch: "
        f"01C={release_id} 02B={taxonomy_manifest.release_id}"
    )

if go_positive_manifest.ontology != taxonomy_manifest.ontology:
    raise ValueError(
        "03 upstream ontology provenance mismatch between 01C and 02B"
    )

target_outputs = {item.path for item in target_manifest.outputs}
go_positive_outputs = {
    item.path for item in go_positive_manifest.outputs
}
taxonomy_outputs = {
    item.path for item in taxonomy_manifest.outputs
}

target_name = "01_targets.tsv"
fasta_name = "01C_go_positive_v2.fasta"
target_taxonomy_name = "02B_target_taxonomy.tsv"
ox_taxonomy_name = "02B_ox_taxonomy.tsv"

if target_name not in target_outputs:
    raise ValueError(
        f"01 bundle does not declare {target_name}"
    )

if fasta_name not in go_positive_outputs:
    raise ValueError(
        f"01C bundle does not declare {fasta_name}"
    )

for name in (target_taxonomy_name, ox_taxonomy_name):
    if name not in taxonomy_outputs:
        raise ValueError(
            f"02B bundle does not declare {name}"
        )

targets = target_bundle / target_name
fasta = go_positive_bundle / fasta_name
target_taxonomy = taxonomy_bundle / target_taxonomy_name
ox_taxonomy = taxonomy_bundle / ox_taxonomy_name

output_prefix_name = "03_primary_candidates"
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

snapshot = release_id

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
    input_artifact(target_manifest_path),
    input_artifact(go_positive_manifest_path),
    input_artifact(taxonomy_manifest_path),
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
    stage="03",
    role=role,
    release_id=release_id,
    parameters=parameters,
    ontology=go_positive_manifest.ontology,
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
            reason_code="NO_INPUT_TARGETS",
            message=(
                "Stage 03 received no target proteins. "
                "The primary candidate scan was not required."
            ),
            counts=outcome_counts,
        )

        warning = (
            f"WARNING [03 {role}]: "
            "no input targets; primary candidate scan skipped "
            "as a valid empty outcome."
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
                "Target proteins were present, but none satisfied the "
                "configured taxonomy policy. "
                "The primary candidate scan was not run."
            ),
            counts=outcome_counts,
        )

        warning = (
            f"WARNING [03 {role}]: "
            f"{preflight.target_count} target(s) present but none are "
            "taxonomically eligible; primary candidate scan skipped "
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
                "03 preflight/scanner target-count mismatch: "
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
                    "The primary candidate scan completed successfully "
                    "but emitted no sequence candidate pairs."
                ),
                counts=outcome_counts,
            )

            warning = (
                f"WARNING [03 {role}]: primary candidate scan completed "
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
                    "The primary candidate scan completed successfully "
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
            "03 output contract violation: "
            f"missing={sorted(expected_outputs - produced)} "
            f"unexpected={sorted(produced - expected_outputs)}"
        )
