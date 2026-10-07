import subprocess
from pathlib import Path

from probe.workflow.artifacts import atomic_stage_bundle, validate_bundle
from probe.workflow.runtime import input_artifact, runtime_environment

context = globals()["snakemake"]

role = str(context.wildcards.role)

target_manifest_path = Path(context.input.target_manifest)
full_manifest_path = Path(context.input.full_manifest)
binary = Path(context.input.binary)

destination = Path(context.output[0]).parent

target_bundle = target_manifest_path.parent
full_bundle = full_manifest_path.parent

target_manifest = validate_bundle(target_bundle, deep=False)
full_manifest = validate_bundle(full_bundle, deep=False)

if target_manifest.stage != "01":
    raise ValueError(
        f"04A expected upstream stage 01, got {target_manifest.stage}"
    )

if target_manifest.role is not None:
    raise ValueError(
        f"04A expected common stage 01, got role {target_manifest.role}"
    )

if full_manifest.stage != "01A_FULL":
    raise ValueError(
        f"04A expected upstream stage 01A_FULL, got {full_manifest.stage}"
    )

if full_manifest.role != role:
    raise ValueError(
        f"04A role mismatch: expected {role}, got {full_manifest.role}"
    )

if full_manifest.release_id is None:
    raise ValueError("04A requires an upstream release_id")

target_name = "01_targets.tsv"
full_name = "01A_full_v2.fasta"

target_outputs = {item.path for item in target_manifest.outputs}
full_outputs = {item.path for item in full_manifest.outputs}

if target_name not in target_outputs:
    raise ValueError(f"01 bundle does not declare {target_name}")

if full_name not in full_outputs:
    raise ValueError(f"01A_FULL bundle does not declare {full_name}")

targets = target_bundle / target_name
full_fasta = full_bundle / full_name

anchors_name = "04A_direct_accession_anchors.tsv"
summary_name = "04A_direct_accession_anchors.summary.tsv"

binary_artifact = input_artifact(binary)

progress_every = 0

recorded_command = [
    str(binary.resolve()),
    "--targets",
    str(targets.resolve()),
    "--fasta",
    str(full_fasta.resolve()),
    "--release",
    full_manifest.release_id,
    "--output",
    f"<stage-bundle>/{anchors_name}",
    "--summary",
    f"<stage-bundle>/{summary_name}",
    "--progress-every",
    str(progress_every),
]

parameters = {
    "release_id": full_manifest.release_id,
    "progress_every": progress_every,
    "command": recorded_command,
}

implementation = {
    "name": "probe-direct-accession-anchors-v1",
    "version": "1",
    "language": "C++20",
    "binary_sha256": binary_artifact.sha256,
    "build_task": "build-direct-accession-anchors",
}

inputs = (
    input_artifact(target_manifest_path),
    input_artifact(full_manifest_path),
    binary_artifact,
)

expected_outputs = {
    anchors_name,
    summary_name,
}

log_path = Path(context.log[0])
log_path.parent.mkdir(parents=True, exist_ok=True)

with log_path.open("w", encoding="utf-8") as log, atomic_stage_bundle(
    destination,
    stage="04A",
    role=role,
    release_id=full_manifest.release_id,
    parameters=parameters,
    ontology=None,
    implementation=implementation,
    inputs=inputs,
    environment=runtime_environment(),
    allow_invalidated_destination=True,
) as staging:
    command = [
        str(binary),
        "--targets",
        str(targets),
        "--fasta",
        str(full_fasta),
        "--release",
        full_manifest.release_id,
        "--output",
        str(staging / anchors_name),
        "--summary",
        str(staging / summary_name),
        "--progress-every",
        str(progress_every),
    ]

    subprocess.run(
        command,
        stdout=log,
        stderr=subprocess.STDOUT,
        check=True,
    )

    produced = {
        path.relative_to(staging).as_posix()
        for path in staging.rglob("*")
        if path.is_file()
    }

    if produced != expected_outputs:
        raise RuntimeError(
            "04A output contract violation: "
            f"missing={sorted(expected_outputs - produced)} "
            f"unexpected={sorted(produced - expected_outputs)}"
        )
