import subprocess
import sys
from pathlib import Path

from probe.workflow.artifacts import atomic_stage_bundle, validate_bundle
from probe.workflow.runtime import input_artifact, runtime_environment

context = globals()["snakemake"]

target_manifest_path = Path(context.input.target_manifest)
start_manifest_path = Path(context.input.start_anchor_manifest)
end_manifest_path = Path(context.input.end_anchor_manifest)
implementation_script = Path(context.input.implementation_script)

destination = Path(context.output[0]).parent

target_bundle = target_manifest_path.parent
start_bundle = start_manifest_path.parent
end_bundle = end_manifest_path.parent

target_manifest = validate_bundle(target_bundle, deep=False)
start_manifest = validate_bundle(start_bundle, deep=False)
end_manifest = validate_bundle(end_bundle, deep=False)

if target_manifest.stage != "01":
    raise ValueError(
        f"04C expected upstream stage 01, got {target_manifest.stage}"
    )

if target_manifest.role is not None:
    raise ValueError(
        f"04C expected common stage 01, got role {target_manifest.role}"
    )

if start_manifest.stage != "04B":
    raise ValueError(
        f"04C expected START upstream stage 04B, got {start_manifest.stage}"
    )

if start_manifest.role != "START":
    raise ValueError(
        f"04C expected START 04B role, got {start_manifest.role}"
    )

if end_manifest.stage != "04B":
    raise ValueError(
        f"04C expected END upstream stage 04B, got {end_manifest.stage}"
    )

if end_manifest.role != "END":
    raise ValueError(
        f"04C expected END 04B role, got {end_manifest.role}"
    )

if start_manifest.release_id is None:
    raise ValueError("04C requires START release_id")

if end_manifest.release_id is None:
    raise ValueError("04C requires END release_id")

target_outputs = {item.path for item in target_manifest.outputs}
start_outputs = {item.path for item in start_manifest.outputs}
end_outputs = {item.path for item in end_manifest.outputs}

target_name = "01_targets.tsv"
anchor_name = "04B_classified_direct_anchors.tsv"

if target_name not in target_outputs:
    raise ValueError(f"01 bundle does not declare {target_name}")

if anchor_name not in start_outputs:
    raise ValueError(f"START 04B bundle does not declare {anchor_name}")

if anchor_name not in end_outputs:
    raise ValueError(f"END 04B bundle does not declare {anchor_name}")

targets = target_bundle / target_name
start_anchors = start_bundle / anchor_name
end_anchors = end_bundle / anchor_name

output_name = "04C_lineage_keys.tsv"
summary_name = "04C_summary.json"

script_artifact = input_artifact(implementation_script)

recorded_command = [
    str(sys.executable),
    str(implementation_script.resolve()),
    "--targets",
    str(targets.resolve()),
    "--start-anchors",
    str(start_anchors.resolve()),
    "--end-anchors",
    str(end_anchors.resolve()),
    "--start-release-id",
    start_manifest.release_id,
    "--end-release-id",
    end_manifest.release_id,
    "--output",
    f"<stage-bundle>/{output_name}",
    "--summary",
    f"<stage-bundle>/{summary_name}",
]

parameters = {
    "start_release_id": start_manifest.release_id,
    "end_release_id": end_manifest.release_id,
    "command": recorded_command,
}

implementation = {
    "name": "probe-direct-lineage-keys-v1",
    "version": "1",
    "language": "Python",
    "script_sha256": script_artifact.sha256,
}

inputs = (
    input_artifact(target_manifest_path),
    input_artifact(start_manifest_path),
    input_artifact(end_manifest_path),
    script_artifact,
)

expected_outputs = {
    output_name,
    summary_name,
}

log_path = Path(context.log[0])
log_path.parent.mkdir(parents=True, exist_ok=True)

with log_path.open("w", encoding="utf-8") as log, atomic_stage_bundle(
    destination,
    stage="04C",
    role=None,
    release_id=None,
    parameters=parameters,
    ontology=None,
    implementation=implementation,
    inputs=inputs,
    environment=runtime_environment(),
    allow_invalidated_destination=True,
) as staging:
    command = [
        sys.executable,
        str(implementation_script),
        "--targets",
        str(targets),
        "--start-anchors",
        str(start_anchors),
        "--end-anchors",
        str(end_anchors),
        "--start-release-id",
        start_manifest.release_id,
        "--end-release-id",
        end_manifest.release_id,
        "--output",
        str(staging / output_name),
        "--summary",
        str(staging / summary_name),
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
            "04C output contract violation: "
            f"missing={sorted(expected_outputs - produced)} "
            f"unexpected={sorted(produced - expected_outputs)}"
        )
