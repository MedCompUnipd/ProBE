import json
import subprocess
from pathlib import Path

from probe.workflow.artifacts import atomic_stage_bundle, validate_bundle
from probe.workflow.runtime import input_artifact, runtime_environment

context = globals()["snakemake"]

role = str(context.wildcards.role)

index_manifest_path = Path(context.input.index_manifest)
taxonomy_manifest_path = Path(context.input.taxonomy_manifest)
full_manifest_path = Path(context.input.full_manifest)
binary = Path(context.input.binary)

destination = Path(context.output[0]).parent

index_bundle = index_manifest_path.parent
taxonomy_bundle = taxonomy_manifest_path.parent
full_bundle = full_manifest_path.parent

index_manifest = validate_bundle(index_bundle, deep=False)
taxonomy_manifest = validate_bundle(taxonomy_bundle, deep=False)
full_manifest = validate_bundle(full_bundle, deep=False)

if index_manifest.stage != "04D1":
    raise ValueError(
        f"04D2 expected upstream stage 04D1, got {index_manifest.stage}"
    )

if index_manifest.role is not None:
    raise ValueError(
        f"04D2 expected common 04D1, got role {index_manifest.role}"
    )

if index_manifest.release_id is not None:
    raise ValueError(
        "04D2 expected common 04D1 release_id=None, "
        f"got {index_manifest.release_id}"
    )

if taxonomy_manifest.stage != "04D0b":
    raise ValueError(
        f"04D2 expected upstream stage 04D0b, got {taxonomy_manifest.stage}"
    )

if taxonomy_manifest.role != role:
    raise ValueError(
        "04D2 taxonomy role mismatch: "
        f"expected {role}, got {taxonomy_manifest.role}"
    )

if full_manifest.stage != "01A_FULL":
    raise ValueError(
        f"04D2 expected upstream stage 01A_FULL, got {full_manifest.stage}"
    )

if full_manifest.role != role:
    raise ValueError(
        f"04D2 FULL role mismatch: expected {role}, got {full_manifest.role}"
    )

release_id = full_manifest.release_id
if release_id is None:
    raise ValueError("04D2 requires an upstream release_id")

if taxonomy_manifest.release_id != release_id:
    raise ValueError(
        "04D2 upstream release_id mismatch: "
        f"01A_FULL={release_id} "
        f"04D0b={taxonomy_manifest.release_id}"
    )

index_data = json.loads(index_manifest_path.read_text(encoding="utf-8"))
index_parameters = index_data.get("parameters", {})

indexed_releases = {
    index_parameters.get("start_release_id"),
    index_parameters.get("end_release_id"),
}

if release_id not in indexed_releases:
    raise ValueError(
        "04D2 release_id is absent from the 04D1 frozen scan index contract: "
        f"{release_id}"
    )

index_name = "04D1_lineage_scan_index.tsv"
taxonomy_name = "full_ox_taxonomy.tsv"
fasta_name = "01A_full_v2.fasta"

contracts = (
    ("04D1", index_manifest, index_name),
    ("04D0b", taxonomy_manifest, taxonomy_name),
    ("01A_FULL", full_manifest, fasta_name),
)

for label, manifest, filename in contracts:
    declared = {item.path for item in manifest.outputs}
    if filename not in declared:
        raise ValueError(f"{label} bundle does not declare {filename}")

scan_index = index_bundle / index_name
ox_taxonomy = taxonomy_bundle / taxonomy_name
fasta = full_bundle / fasta_name

output_name = "04D2_lineage_evidence.tsv"
summary_name = "04D2_summary.tsv"
progress_every = 10_000_000

binary_artifact = input_artifact(binary)

recorded_command = [
    str(binary.resolve()),
    "--release",
    release_id,
    "--scan-index",
    str(scan_index.resolve()),
    "--ox-taxonomy",
    str(ox_taxonomy.resolve()),
    "--fasta",
    str(fasta.resolve()),
    "--output",
    f"<stage-bundle>/{output_name}",
    "--summary",
    f"<stage-bundle>/{summary_name}",
    "--progress-every",
    str(progress_every),
]

parameters = {
    "release_id": release_id,
    "progress_every": progress_every,
    "command": recorded_command,
}

implementation = {
    "name": "probe-onehop-lineage-scan-v1",
    "version": "1",
    "language": "C++20",
    "binary_sha256": binary_artifact.sha256,
    "build_task": "build-onehop-lineage-scan",
}

inputs = (
    input_artifact(index_manifest_path),
    input_artifact(taxonomy_manifest_path),
    input_artifact(full_manifest_path),
    binary_artifact,
)

expected_outputs = {
    output_name,
    summary_name,
}

log_path = Path(context.log[0])
log_path.parent.mkdir(parents=True, exist_ok=True)

with log_path.open("w", encoding="utf-8") as log, atomic_stage_bundle(
    destination,
    stage="04D2",
    role=role,
    release_id=release_id,
    parameters=parameters,
    ontology=None,
    implementation=implementation,
    inputs=inputs,
    environment=runtime_environment(),
    allow_invalidated_destination=True,
) as staging:
    command = [
        str(binary),
        "--release",
        release_id,
        "--scan-index",
        str(scan_index),
        "--ox-taxonomy",
        str(ox_taxonomy),
        "--fasta",
        str(fasta),
        "--output",
        str(staging / output_name),
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
            "04D2 output contract violation: "
            f"missing={sorted(expected_outputs - produced)} "
            f"unexpected={sorted(produced - expected_outputs)}"
        )
