import subprocess
import sys
from pathlib import Path

from probe.workflow.artifacts import atomic_stage_bundle, validate_bundle
from probe.workflow.runtime import input_artifact, runtime_environment

context = globals()["snakemake"]

target_manifest_path = Path(context.input.target_manifest)
keys_manifest_path = Path(context.input.keys_manifest)
start_anchor_manifest_path = Path(context.input.start_anchor_manifest)
end_anchor_manifest_path = Path(context.input.end_anchor_manifest)
start_taxonomy_manifest_path = Path(context.input.start_taxonomy_manifest)
end_taxonomy_manifest_path = Path(context.input.end_taxonomy_manifest)
implementation_script = Path(context.input.implementation_script)

destination = Path(context.output[0]).parent

target_bundle = target_manifest_path.parent
keys_bundle = keys_manifest_path.parent
start_anchor_bundle = start_anchor_manifest_path.parent
end_anchor_bundle = end_anchor_manifest_path.parent
start_taxonomy_bundle = start_taxonomy_manifest_path.parent
end_taxonomy_bundle = end_taxonomy_manifest_path.parent

target_manifest = validate_bundle(target_bundle, deep=False)
keys_manifest = validate_bundle(keys_bundle, deep=False)
start_anchor_manifest = validate_bundle(start_anchor_bundle, deep=False)
end_anchor_manifest = validate_bundle(end_anchor_bundle, deep=False)
start_taxonomy_manifest = validate_bundle(start_taxonomy_bundle, deep=False)
end_taxonomy_manifest = validate_bundle(end_taxonomy_bundle, deep=False)

if target_manifest.stage != "01" or target_manifest.role is not None:
    raise ValueError(
        "04D1 requires common stage 01; "
        f"got stage={target_manifest.stage} role={target_manifest.role}"
    )

if keys_manifest.stage != "04C" or keys_manifest.role is not None:
    raise ValueError(
        "04D1 requires common stage 04C; "
        f"got stage={keys_manifest.stage} role={keys_manifest.role}"
    )

for label, manifest, expected_stage, expected_role in (
    ("START anchors", start_anchor_manifest, "04B", "START"),
    ("END anchors", end_anchor_manifest, "04B", "END"),
    ("START taxonomy", start_taxonomy_manifest, "02B", "START"),
    ("END taxonomy", end_taxonomy_manifest, "02B", "END"),
):
    if manifest.stage != expected_stage:
        raise ValueError(
            f"04D1 {label} expected stage {expected_stage}, got {manifest.stage}"
        )
    if manifest.role != expected_role:
        raise ValueError(
            f"04D1 {label} expected role {expected_role}, got {manifest.role}"
        )
    if manifest.release_id is None:
        raise ValueError(f"04D1 {label} requires release_id")

if start_anchor_manifest.release_id != start_taxonomy_manifest.release_id:
    raise ValueError(
        "04D1 START release_id mismatch: "
        f"04B={start_anchor_manifest.release_id} "
        f"02B={start_taxonomy_manifest.release_id}"
    )

if end_anchor_manifest.release_id != end_taxonomy_manifest.release_id:
    raise ValueError(
        "04D1 END release_id mismatch: "
        f"04B={end_anchor_manifest.release_id} "
        f"02B={end_taxonomy_manifest.release_id}"
    )

start_release_id = start_anchor_manifest.release_id
end_release_id = end_anchor_manifest.release_id

target_name = "01_targets.tsv"
keys_name = "04C_lineage_keys.tsv"
anchors_name = "04B_classified_direct_anchors.tsv"
taxonomy_name = "02B_target_taxonomy.tsv"

contracts = (
    ("01", target_manifest, target_name),
    ("04C", keys_manifest, keys_name),
    ("START 04B", start_anchor_manifest, anchors_name),
    ("END 04B", end_anchor_manifest, anchors_name),
    ("START 02B", start_taxonomy_manifest, taxonomy_name),
    ("END 02B", end_taxonomy_manifest, taxonomy_name),
)

for label, manifest, filename in contracts:
    declared = {item.path for item in manifest.outputs}
    if filename not in declared:
        raise ValueError(f"{label} bundle does not declare {filename}")

targets = target_bundle / target_name
keys = keys_bundle / keys_name
start_anchors = start_anchor_bundle / anchors_name
end_anchors = end_anchor_bundle / anchors_name
start_target_taxonomy = start_taxonomy_bundle / taxonomy_name
end_target_taxonomy = end_taxonomy_bundle / taxonomy_name

output_name = "04D1_lineage_scan_index.tsv"
summary_name = "04D1_summary.json"

script_artifact = input_artifact(implementation_script)
python_executable = str(Path(sys.executable).resolve())

recorded_command = [
    python_executable,
    str(implementation_script.resolve()),
    "--keys",
    str(keys.resolve()),
    "--targets",
    str(targets.resolve()),
    "--start-anchors",
    str(start_anchors.resolve()),
    "--end-anchors",
    str(end_anchors.resolve()),
    "--start-target-taxonomy",
    str(start_target_taxonomy.resolve()),
    "--end-target-taxonomy",
    str(end_target_taxonomy.resolve()),
    "--start-release-id",
    start_release_id,
    "--end-release-id",
    end_release_id,
    "--output",
    f"<stage-bundle>/{output_name}",
    "--summary",
    f"<stage-bundle>/{summary_name}",
]

parameters = {
    "start_release_id": start_release_id,
    "end_release_id": end_release_id,
    "command": recorded_command,
}

implementation = {
    "name": "probe-lineage-scan-index-v1",
    "version": "1",
    "language": "Python",
    "script_sha256": script_artifact.sha256,
}

inputs = (
    input_artifact(target_manifest_path),
    input_artifact(keys_manifest_path),
    input_artifact(start_anchor_manifest_path),
    input_artifact(end_anchor_manifest_path),
    input_artifact(start_taxonomy_manifest_path),
    input_artifact(end_taxonomy_manifest_path),
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
    stage="04D1",
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
        python_executable,
        str(implementation_script),
        "--keys",
        str(keys),
        "--targets",
        str(targets),
        "--start-anchors",
        str(start_anchors),
        "--end-anchors",
        str(end_anchors),
        "--start-target-taxonomy",
        str(start_target_taxonomy),
        "--end-target-taxonomy",
        str(end_target_taxonomy),
        "--start-release-id",
        start_release_id,
        "--end-release-id",
        end_release_id,
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
            "04D1 output contract violation: "
            f"missing={sorted(expected_outputs - produced)} "
            f"unexpected={sorted(produced - expected_outputs)}"
        )
