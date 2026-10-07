import subprocess
from pathlib import Path

from probe.workflow.artifacts import atomic_stage_bundle, validate_bundle
from probe.workflow.runtime import input_artifact, runtime_environment

context = globals()["snakemake"]

role = str(context.wildcards.role)

source_manifest_path = Path(context.input.source_manifest)
binary = Path(context.input.binary)
destination = Path(context.output[0]).parent

source_bundle = source_manifest_path.parent
source_manifest = validate_bundle(source_bundle, deep=False)

if source_manifest.stage != "01A_FULL":
    raise ValueError(
        f"04D0a expected upstream stage 01A_FULL, got {source_manifest.stage}"
    )

if source_manifest.role != role:
    raise ValueError(
        f"04D0a role mismatch: expected {role}, got {source_manifest.role}"
    )

if source_manifest.release_id is None:
    raise ValueError("04D0a requires an upstream release_id")

source_outputs = {item.path for item in source_manifest.outputs}

fasta_name = "01A_full_v2.fasta"

if fasta_name not in source_outputs:
    raise ValueError(f"01A_FULL bundle does not declare {fasta_name}")

input_v2 = source_bundle / fasta_name

output_name = "04D0a_full_raw_ox.txt"
stats_name = "04D0a.stats.tsv"
progress_every = 0

binary_artifact = input_artifact(binary)

recorded_command = [
    str(binary.resolve()),
    "--input-v2",
    str(input_v2.resolve()),
    "--output",
    f"<stage-bundle>/{output_name}",
    "--stats",
    f"<stage-bundle>/{stats_name}",
    "--progress-every",
    str(progress_every),
]

parameters = {
    "progress_every": progress_every,
    "command": recorded_command,
}

implementation = {
    "name": "probe-raw-ox-extract-v1",
    "version": "1",
    "language": "C++20",
    "binary_sha256": binary_artifact.sha256,
    "build_task": "build-raw-ox-extract",
}

inputs = (
    input_artifact(source_manifest_path),
    binary_artifact,
)

expected_outputs = {
    output_name,
    stats_name,
}

log_path = Path(context.log[0])
log_path.parent.mkdir(parents=True, exist_ok=True)

with log_path.open("w", encoding="utf-8") as log, atomic_stage_bundle(
    destination,
    stage="04D0a",
    role=role,
    release_id=source_manifest.release_id,
    parameters=parameters,
    ontology=None,
    implementation=implementation,
    inputs=inputs,
    environment=runtime_environment(),
    allow_invalidated_destination=True,
) as staging:
    command = [
        str(binary),
        "--input-v2",
        str(input_v2),
        "--output",
        str(staging / output_name),
        "--stats",
        str(staging / stats_name),
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
            "04D0a output contract violation: "
            f"missing={sorted(expected_outputs - produced)} "
            f"unexpected={sorted(produced - expected_outputs)}"
        )
