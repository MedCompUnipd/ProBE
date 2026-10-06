import subprocess
import sys
from pathlib import Path

from probe.workflow.artifacts import atomic_stage_bundle, validate_bundle
from probe.workflow.runtime import input_artifact, runtime_environment

context = globals()["snakemake"]

role = str(context.wildcards.role)
snapshot_id = str(context.params.snapshot_id)

target_manifest_path = Path(context.input.target_manifest)
ox_manifest_path = Path(context.input.ox_manifest)

nodes = Path(context.input.nodes)
names = Path(context.input.names)
merged = Path(context.input.merged)
delnodes = Path(context.input.delnodes)

implementation_script = Path(context.input.implementation_script)
taxonomy_module = Path(context.input.taxonomy_module)

destination = Path(context.output[0]).parent

target_bundle = target_manifest_path.parent
ox_bundle = ox_manifest_path.parent

target_manifest = validate_bundle(target_bundle, deep=False)
ox_manifest = validate_bundle(ox_bundle, deep=False)

if target_manifest.stage != "01":
    raise ValueError(
        f"02B expected upstream stage 01, got {target_manifest.stage}"
    )

if target_manifest.role is not None:
    raise ValueError(
        f"02B expected common stage 01, got role {target_manifest.role}"
    )

if ox_manifest.stage != "02A":
    raise ValueError(
        f"02B expected upstream stage 02A, got {ox_manifest.stage}"
    )

if ox_manifest.role != role:
    raise ValueError(
        f"02B role mismatch: expected {role}, got {ox_manifest.role}"
    )

if ox_manifest.release_id is None:
    raise ValueError("02B requires an upstream release_id")

target_outputs = {item.path for item in target_manifest.outputs}
ox_outputs = {item.path for item in ox_manifest.outputs}

target_name = "01_targets.tsv"
ox_name = "02A_raw_ox.txt"

if target_name not in target_outputs:
    raise ValueError(f"01 bundle does not declare {target_name}")

if ox_name not in ox_outputs:
    raise ValueError(f"02A bundle does not declare {ox_name}")

targets = target_bundle / target_name
ox_list = ox_bundle / ox_name

index_name = "02B_taxonomy.sqlite"
target_output_name = "02B_target_taxonomy.tsv"
ox_output_name = "02B_ox_taxonomy.tsv"
stats_name = "02B.stats.json"

script_artifact = input_artifact(implementation_script)
taxonomy_module_artifact = input_artifact(taxonomy_module)

recorded_command = [
    str(sys.executable),
    str(implementation_script.resolve()),
    "--nodes",
    str(nodes.resolve()),
    "--names",
    str(names.resolve()),
    "--merged",
    str(merged.resolve()),
    "--delnodes",
    str(delnodes.resolve()),
    "--snapshot-id",
    snapshot_id,
    "--index",
    f"<stage-bundle>/{index_name}",
    "--targets",
    str(targets.resolve()),
    "--ox-list",
    str(ox_list.resolve()),
    "--target-output",
    f"<stage-bundle>/{target_output_name}",
    "--ox-output",
    f"<stage-bundle>/{ox_output_name}",
    "--stats",
    f"<stage-bundle>/{stats_name}",
]

parameters = {
    "snapshot_id": snapshot_id,
    "command": recorded_command,
}

implementation = {
    "name": "probe-taxonomy-contexts-v1",
    "version": "1",
    "language": "Python",
    "script_sha256": script_artifact.sha256,
    "taxonomy_module_sha256": taxonomy_module_artifact.sha256,
}

inputs = (
    input_artifact(target_manifest_path),
    input_artifact(ox_manifest_path),
    input_artifact(nodes),
    input_artifact(names),
    input_artifact(merged),
    input_artifact(delnodes),
    script_artifact,
    taxonomy_module_artifact,
)

expected_outputs = {
    index_name,
    target_output_name,
    ox_output_name,
    stats_name,
}

log_path = Path(context.log[0])
log_path.parent.mkdir(parents=True, exist_ok=True)

with log_path.open("w", encoding="utf-8") as log, atomic_stage_bundle(
    destination,
    stage="02B",
    role=role,
    release_id=ox_manifest.release_id,
    parameters=parameters,
    ontology=ox_manifest.ontology,
    implementation=implementation,
    inputs=inputs,
    environment=runtime_environment(),
    allow_invalidated_destination=True,
) as staging:
    command = [
        sys.executable,
        str(implementation_script),
        "--nodes",
        str(nodes),
        "--names",
        str(names),
        "--merged",
        str(merged),
        "--delnodes",
        str(delnodes),
        "--snapshot-id",
        snapshot_id,
        "--index",
        str(staging / index_name),
        "--targets",
        str(targets),
        "--ox-list",
        str(ox_list),
        "--target-output",
        str(staging / target_output_name),
        "--ox-output",
        str(staging / ox_output_name),
        "--stats",
        str(staging / stats_name),
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
            "02B output contract violation: "
            f"missing={sorted(expected_outputs - produced)} "
            f"unexpected={sorted(produced - expected_outputs)}"
        )
