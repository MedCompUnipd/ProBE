import subprocess
from pathlib import Path

from probe.workflow.artifacts import atomic_stage_bundle, validate_bundle
from probe.workflow.runtime import input_artifact, runtime_environment

context = globals()["snakemake"]

role = str(context.wildcards.role)

release_manifest_path = Path(context.input.release_manifest)
positive_manifest_path = Path(context.input.positive_manifest)
binary = Path(context.input.binary)

destination = Path(context.output[0]).parent

release_bundle = release_manifest_path.parent
positive_bundle = positive_manifest_path.parent

release_manifest = validate_bundle(release_bundle, deep=False)
positive_manifest = validate_bundle(positive_bundle, deep=False)

if release_manifest.stage != "01A_FULL":
    raise ValueError(
        f"01C expected upstream stage 01A_FULL, got {release_manifest.stage}"
    )

if positive_manifest.stage != "01B1":
    raise ValueError(
        f"01C expected upstream stage 01B1, got {positive_manifest.stage}"
    )

if release_manifest.role != role:
    raise ValueError(
        f"01C release role mismatch: expected {role}, got {release_manifest.role}"
    )

if positive_manifest.role != role:
    raise ValueError(
        f"01C GO-positive role mismatch: expected {role}, got {positive_manifest.role}"
    )

if release_manifest.release_id is None:
    raise ValueError("01C requires an upstream release_id")

if positive_manifest.release_id != release_manifest.release_id:
    raise ValueError(
        "01C upstream release mismatch: "
        f"01A_FULL={release_manifest.release_id} "
        f"01B1={positive_manifest.release_id}"
    )

release_outputs = {item.path for item in release_manifest.outputs}
positive_outputs = {item.path for item in positive_manifest.outputs}

v2_name = "01A_full_v2.fasta"
positive_name = "goa.positive_accessions.txt"

if v2_name not in release_outputs:
    raise ValueError(f"01A_FULL bundle does not declare {v2_name}")

if positive_name not in positive_outputs:
    raise ValueError(f"01B1 bundle does not declare {positive_name}")

input_v2 = release_bundle / v2_name
positive_accessions = positive_bundle / positive_name

output_name = "01C_go_positive_v2.fasta"
stats_name = "01C_go_positive_v2.stats.tsv"
progress_every = 0

binary_artifact = input_artifact(binary)

recorded_command = [
    str(binary.resolve()),
    "--input-v2",
    str(input_v2.resolve()),
    "--positive-accessions",
    str(positive_accessions.resolve()),
    "--output-v2",
    f"<stage-bundle>/{output_name}",
    "--stats",
    f"<stage-bundle>/{stats_name}",
    "--progress-every",
    str(progress_every),
]

parameters = {
    "membership": "exact_primary_accession",
    "fragment_filter": False,
    "progress_every": progress_every,
    "command": recorded_command,
}

implementation = {
    "name": "probe-go-positive-filter-v1",
    "version": "1",
    "language_standard": "C++20",
    "binary_sha256": binary_artifact.sha256,
    "build_task": "build-go-positive-filter",
}

inputs = (
    input_artifact(release_manifest_path),
    input_artifact(positive_manifest_path),
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
    stage="01C",
    role=role,
    release_id=release_manifest.release_id,
    parameters=parameters,
    ontology=positive_manifest.ontology,
    implementation=implementation,
    inputs=inputs,
    environment=runtime_environment(),
    allow_invalidated_destination=True,
) as staging:
    command = [
        str(binary),
        "--input-v2",
        str(input_v2),
        "--positive-accessions",
        str(positive_accessions),
        "--output-v2",
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
            "01C output contract violation: "
            f"missing={sorted(expected_outputs - produced)} "
            f"unexpected={sorted(produced - expected_outputs)}"
        )
