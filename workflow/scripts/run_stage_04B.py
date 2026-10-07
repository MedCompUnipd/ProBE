import subprocess
import sys
from pathlib import Path

from probe.workflow.artifacts import atomic_stage_bundle, validate_bundle
from probe.workflow.runtime import input_artifact, runtime_environment

context = globals()["snakemake"]

role = str(context.wildcards.role)
policy = dict(context.params.classification_policy)
taxonomy_policy = str(policy["taxonomy_policy"])

anchor_manifest_path = Path(context.input.anchor_manifest)
taxonomy_manifest_path = Path(context.input.taxonomy_manifest)
implementation_script = Path(context.input.implementation_script)
taxonomy_module = Path(context.input.taxonomy_module)

destination = Path(context.output[0]).parent

anchor_bundle = anchor_manifest_path.parent
taxonomy_bundle = taxonomy_manifest_path.parent

anchor_manifest = validate_bundle(anchor_bundle, deep=False)
taxonomy_manifest = validate_bundle(taxonomy_bundle, deep=False)

if anchor_manifest.stage != "04A":
    raise ValueError(
        f"04B expected upstream stage 04A, got {anchor_manifest.stage}"
    )

if anchor_manifest.role != role:
    raise ValueError(
        f"04B anchor role mismatch: expected {role}, got {anchor_manifest.role}"
    )

if taxonomy_manifest.stage != "02B":
    raise ValueError(
        f"04B expected upstream stage 02B, got {taxonomy_manifest.stage}"
    )

if taxonomy_manifest.role != role:
    raise ValueError(
        f"04B taxonomy role mismatch: expected {role}, got {taxonomy_manifest.role}"
    )

if anchor_manifest.release_id is None:
    raise ValueError("04B requires 04A release_id")

if taxonomy_manifest.release_id is None:
    raise ValueError("04B requires 02B release_id")

if anchor_manifest.release_id != taxonomy_manifest.release_id:
    raise ValueError(
        "04B upstream release mismatch: "
        f"04A={anchor_manifest.release_id}, "
        f"02B={taxonomy_manifest.release_id}"
    )

anchor_outputs = {item.path for item in anchor_manifest.outputs}
taxonomy_outputs = {item.path for item in taxonomy_manifest.outputs}

anchor_name = "04A_direct_accession_anchors.tsv"
target_taxonomy_name = "02B_target_taxonomy.tsv"
taxonomy_index_name = "02B_taxonomy.sqlite"

if anchor_name not in anchor_outputs:
    raise ValueError(f"04A bundle does not declare {anchor_name}")

for name in (target_taxonomy_name, taxonomy_index_name):
    if name not in taxonomy_outputs:
        raise ValueError(f"02B bundle does not declare {name}")

anchors = anchor_bundle / anchor_name
target_taxonomy = taxonomy_bundle / target_taxonomy_name
taxonomy_index = taxonomy_bundle / taxonomy_index_name

output_name = "04B_classified_direct_anchors.tsv"
summary_name = "04B_summary.json"

script_artifact = input_artifact(implementation_script)
taxonomy_module_artifact = input_artifact(taxonomy_module)

recorded_command = [
    str(sys.executable),
    str(implementation_script.resolve()),
    "--anchors",
    str(anchors.resolve()),
    "--target-taxonomy",
    str(target_taxonomy.resolve()),
    "--taxonomy-index",
    str(taxonomy_index.resolve()),
    "--taxonomy-policy",
    taxonomy_policy,
    "--output",
    f"<stage-bundle>/{output_name}",
    "--summary",
    f"<stage-bundle>/{summary_name}",
]

parameters = {
    "taxonomy_policy": taxonomy_policy,
    "command": recorded_command,
}

implementation = {
    "name": "probe-direct-anchor-taxonomy-classification-v1",
    "version": "1",
    "language": "Python",
    "script_sha256": script_artifact.sha256,
    "taxonomy_module_sha256": taxonomy_module_artifact.sha256,
}

inputs = (
    input_artifact(anchor_manifest_path),
    input_artifact(taxonomy_manifest_path),
    script_artifact,
    taxonomy_module_artifact,
)

expected_outputs = {
    output_name,
    summary_name,
}

log_path = Path(context.log[0])
log_path.parent.mkdir(parents=True, exist_ok=True)

with log_path.open("w", encoding="utf-8") as log, atomic_stage_bundle(
    destination,
    stage="04B",
    role=role,
    release_id=anchor_manifest.release_id,
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
        "--anchors",
        str(anchors),
        "--target-taxonomy",
        str(target_taxonomy),
        "--taxonomy-index",
        str(taxonomy_index),
        "--taxonomy-policy",
        taxonomy_policy,
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
            "04B output contract violation: "
            f"missing={sorted(expected_outputs - produced)} "
            f"unexpected={sorted(produced - expected_outputs)}"
        )
