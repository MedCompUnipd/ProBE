import subprocess
import sys
from pathlib import Path

from probe.workflow.artifacts import atomic_stage_bundle, validate_bundle
from probe.workflow.runtime import input_artifact, runtime_environment

context = globals()["snakemake"]

role = str(context.wildcards.role)
if role not in {"START", "END"}:
    raise ValueError(f"05R0 unknown role {role}")

uncovered_manifest_path = Path(context.input.uncovered_manifest)
targets_manifest_path = Path(context.input.targets_manifest)
target_taxonomy_manifest_path = Path(
    context.input.target_taxonomy_manifest
)
implementation_script = Path(context.input.implementation_script)

destination = Path(context.output[0]).parent


def load_manifest(path: Path):
    return validate_bundle(path.parent, deep=False)


uncovered_manifest = load_manifest(uncovered_manifest_path)
targets_manifest = load_manifest(targets_manifest_path)
target_taxonomy_manifest = load_manifest(target_taxonomy_manifest_path)

if uncovered_manifest.stage != "05B":
    raise ValueError(
        f"05R0 expected Stage 05B, got {uncovered_manifest.stage}"
    )
if uncovered_manifest.role is not None or uncovered_manifest.release_id is not None:
    raise ValueError("05R0 Stage 05B input must be a common bundle")

if targets_manifest.stage != "01":
    raise ValueError(
        f"05R0 expected Stage 01, got {targets_manifest.stage}"
    )
if targets_manifest.role is not None or targets_manifest.release_id is not None:
    raise ValueError("05R0 Stage 01 input must be a common bundle")

if target_taxonomy_manifest.stage != "02B":
    raise ValueError(
        "05R0 target taxonomy expected Stage 02B, "
        f"got {target_taxonomy_manifest.stage}"
    )
if target_taxonomy_manifest.role != role:
    raise ValueError(
        f"05R0 expected 02B role {role}, "
        f"got {target_taxonomy_manifest.role}"
    )
if target_taxonomy_manifest.release_id is None:
    raise ValueError("05R0 Stage 02B input requires a release_id")

release_id = target_taxonomy_manifest.release_id

release_parameter = {
    "START": "start_release_id",
    "END": "end_release_id",
}[role]

upstream_release_id = uncovered_manifest.parameters.get(release_parameter)
if upstream_release_id != release_id:
    raise ValueError(
        f"05R0 {role} release mismatch: "
        f"05B={upstream_release_id} 02B={release_id}"
    )

uncovered_name = "05B_uncovered_targets.tsv"
targets_name = "01_targets.tsv"
taxonomy_name = "02B_target_taxonomy.tsv"

for label, manifest, filename in (
    ("05B", uncovered_manifest, uncovered_name),
    ("01", targets_manifest, targets_name),
    ("02B", target_taxonomy_manifest, taxonomy_name),
):
    declared = {item.path for item in manifest.outputs}
    if filename not in declared:
        raise ValueError(f"{label} bundle does not declare {filename}")

uncovered = uncovered_manifest_path.parent / uncovered_name
targets = targets_manifest_path.parent / targets_name
target_taxonomy = target_taxonomy_manifest_path.parent / taxonomy_name

ids_name = "rescue_ids.txt"
targets_output_name = "rescue_targets.tsv"
taxonomy_output_name = "rescue_target_taxonomy.tsv"
summary_name = "05R0_summary.json"

script_artifact = input_artifact(implementation_script)
python_executable = str(Path(sys.executable).resolve())

recorded_command = [
    python_executable,
    str(implementation_script.resolve()),
    "--uncovered",
    str(uncovered.resolve()),
    "--targets",
    str(targets.resolve()),
    "--target-taxonomy",
    str(target_taxonomy.resolve()),
    "--release-id",
    release_id,
    "--output-ids",
    f"<stage-bundle>/{ids_name}",
    "--output-targets",
    f"<stage-bundle>/{targets_output_name}",
    "--output-target-taxonomy",
    f"<stage-bundle>/{taxonomy_output_name}",
    "--summary",
    f"<stage-bundle>/{summary_name}",
]

parameters = {
    "rescue_trigger": "STAGE05B_UNCOVERED_AND_K_SEARCHABLE",
    "command": recorded_command,
}

implementation = {
    "name": "probe-rescue-target-preparation-v1",
    "version": "1",
    "language": "Python",
    "script_sha256": script_artifact.sha256,
}

inputs = (
    input_artifact(uncovered_manifest_path),
    input_artifact(targets_manifest_path),
    input_artifact(target_taxonomy_manifest_path),
    script_artifact,
)

expected_outputs = {
    ids_name,
    targets_output_name,
    taxonomy_output_name,
    summary_name,
}

log_path = Path(context.log[0])
log_path.parent.mkdir(parents=True, exist_ok=True)

with log_path.open("w", encoding="utf-8") as log, atomic_stage_bundle(
    destination,
    stage="05R0",
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
        python_executable,
        str(implementation_script),
        "--uncovered",
        str(uncovered),
        "--targets",
        str(targets),
        "--target-taxonomy",
        str(target_taxonomy),
        "--release-id",
        release_id,
        "--output-ids",
        str(staging / ids_name),
        "--output-targets",
        str(staging / targets_output_name),
        "--output-target-taxonomy",
        str(staging / taxonomy_output_name),
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
            "05R0 output contract violation: "
            f"missing={sorted(expected_outputs - produced)} "
            f"unexpected={sorted(produced - expected_outputs)}"
        )
