import subprocess
import sys
from pathlib import Path

from probe.workflow.artifacts import atomic_stage_bundle, validate_bundle
from probe.workflow.runtime import input_artifact, runtime_environment

context = globals()["snakemake"]

start_sequence_manifest_path = Path(
    context.input.start_sequence_manifest
)
end_sequence_manifest_path = Path(
    context.input.end_sequence_manifest
)
start_lineage_manifest_path = Path(
    context.input.start_lineage_manifest
)
end_lineage_manifest_path = Path(
    context.input.end_lineage_manifest
)
implementation_script = Path(context.input.implementation_script)

destination = Path(context.output[0]).parent


def load_manifest(path: Path):
    return validate_bundle(path.parent, deep=False)


start_sequence_manifest = load_manifest(start_sequence_manifest_path)
end_sequence_manifest = load_manifest(end_sequence_manifest_path)
start_lineage_manifest = load_manifest(start_lineage_manifest_path)
end_lineage_manifest = load_manifest(end_lineage_manifest_path)

expected = (
    ("START sequence", start_sequence_manifest, "03", "START"),
    ("END sequence", end_sequence_manifest, "03", "END"),
    ("START lineage", start_lineage_manifest, "04E", "START"),
    ("END lineage", end_lineage_manifest, "04E", "END"),
)

for label, manifest, stage, role in expected:
    if manifest.stage != stage:
        raise ValueError(
            f"05A {label} expected stage {stage}, got {manifest.stage}"
        )
    if manifest.role != role:
        raise ValueError(
            f"05A {label} expected role {role}, got {manifest.role}"
        )
    if manifest.release_id is None:
        raise ValueError(f"05A {label} requires a release_id")

start_release_id = start_sequence_manifest.release_id
end_release_id = end_sequence_manifest.release_id


if start_lineage_manifest.release_id != start_release_id:
    raise ValueError(
        "05A START release mismatch: "
        f"03={start_release_id} "
        f"04E={start_lineage_manifest.release_id}"
    )

if end_lineage_manifest.release_id != end_release_id:
    raise ValueError(
        "05A END release mismatch: "
        f"03={end_release_id} "
        f"04E={end_lineage_manifest.release_id}"
    )

sequence_name = "03_primary_candidates.pairs.tsv"
lineage_name = "04E_lineage_candidates.tsv"

for label, manifest, filename in (
    ("START 03", start_sequence_manifest, sequence_name),
    ("END 03", end_sequence_manifest, sequence_name),
    ("START 04E", start_lineage_manifest, lineage_name),
    ("END 04E", end_lineage_manifest, lineage_name),
):
    declared = {item.path for item in manifest.outputs}
    if filename not in declared:
        raise ValueError(f"{label} bundle does not declare {filename}")

start_sequence = start_sequence_manifest_path.parent / sequence_name
end_sequence = end_sequence_manifest_path.parent / sequence_name
start_lineage = start_lineage_manifest_path.parent / lineage_name
end_lineage = end_lineage_manifest_path.parent / lineage_name

output_name = "05A_candidate_union.tsv"
summary_name = "05A_summary.json"

script_artifact = input_artifact(implementation_script)
python_executable = str(Path(sys.executable).resolve())

recorded_command = [
    python_executable,
    str(implementation_script.resolve()),
    "--start-sequence",
    str(start_sequence.resolve()),
    "--end-sequence",
    str(end_sequence.resolve()),
    "--start-lineage",
    str(start_lineage.resolve()),
    "--end-lineage",
    str(end_lineage.resolve()),
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
    "name": "probe-candidate-channel-union-v1",
    "version": "1",
    "language": "Python",
    "script_sha256": script_artifact.sha256,
}

inputs = (
    input_artifact(start_sequence_manifest_path),
    input_artifact(end_sequence_manifest_path),
    input_artifact(start_lineage_manifest_path),
    input_artifact(end_lineage_manifest_path),
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
    stage="05A",
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
        "--start-sequence",
        str(start_sequence),
        "--end-sequence",
        str(end_sequence),
        "--start-lineage",
        str(start_lineage),
        "--end-lineage",
        str(end_lineage),
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
            "05A output contract violation: "
            f"missing={sorted(expected_outputs - produced)} "
            f"unexpected={sorted(produced - expected_outputs)}"
        )
