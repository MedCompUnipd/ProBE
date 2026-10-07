import subprocess
import sys
from pathlib import Path

from probe.workflow.artifacts import atomic_stage_bundle, validate_bundle
from probe.workflow.runtime import input_artifact, runtime_environment

context = globals()["snakemake"]

targets_manifest_path = Path(context.input.targets_manifest)
candidate_union_manifest_path = Path(context.input.candidate_union_manifest)
start_anchor_manifest_path = Path(context.input.start_anchor_manifest)
end_anchor_manifest_path = Path(context.input.end_anchor_manifest)
implementation_script = Path(context.input.implementation_script)

policy = context.params.policy
k = int(policy["k"])

destination = Path(context.output[0]).parent


def load_manifest(path: Path):
    return validate_bundle(path.parent, deep=False)


targets_manifest = load_manifest(targets_manifest_path)
candidate_union_manifest = load_manifest(candidate_union_manifest_path)
start_anchor_manifest = load_manifest(start_anchor_manifest_path)
end_anchor_manifest = load_manifest(end_anchor_manifest_path)

if targets_manifest.stage != "01":
    raise ValueError(
        f"05B targets expected stage 01, got {targets_manifest.stage}"
    )
if targets_manifest.role is not None or targets_manifest.release_id is not None:
    raise ValueError("05B Stage 01 input must be a common bundle")

if candidate_union_manifest.stage != "05A":
    raise ValueError(
        "05B candidate union expected stage 05A, "
        f"got {candidate_union_manifest.stage}"
    )
if (
    candidate_union_manifest.role is not None
    or candidate_union_manifest.release_id is not None
):
    raise ValueError("05B Stage 05A input must be a common bundle")

for label, manifest, role in (
    ("START anchor", start_anchor_manifest, "START"),
    ("END anchor", end_anchor_manifest, "END"),
):
    if manifest.stage != "04B":
        raise ValueError(
            f"05B {label} expected stage 04B, got {manifest.stage}"
        )
    if manifest.role != role:
        raise ValueError(
            f"05B {label} expected role {role}, got {manifest.role}"
        )
    if manifest.release_id is None:
        raise ValueError(f"05B {label} requires a release_id")

start_release_id = start_anchor_manifest.release_id
end_release_id = end_anchor_manifest.release_id

union_parameters = candidate_union_manifest.parameters

if union_parameters.get("start_release_id") != start_release_id:
    raise ValueError(
        "05B START release mismatch: "
        f"05A={union_parameters.get('start_release_id')} "
        f"04B={start_release_id}"
    )

if union_parameters.get("end_release_id") != end_release_id:
    raise ValueError(
        "05B END release mismatch: "
        f"05A={union_parameters.get('end_release_id')} "
        f"04B={end_release_id}"
    )

targets_name = "01_targets.tsv"
union_name = "05A_candidate_union.tsv"
anchors_name = "04B_classified_direct_anchors.tsv"

for label, manifest, filename in (
    ("01", targets_manifest, targets_name),
    ("05A", candidate_union_manifest, union_name),
    ("START 04B", start_anchor_manifest, anchors_name),
    ("END 04B", end_anchor_manifest, anchors_name),
):
    declared = {item.path for item in manifest.outputs}
    if filename not in declared:
        raise ValueError(f"{label} bundle does not declare {filename}")

targets = targets_manifest_path.parent / targets_name
candidate_union = candidate_union_manifest_path.parent / union_name
start_anchors = start_anchor_manifest_path.parent / anchors_name
end_anchors = end_anchor_manifest_path.parent / anchors_name

output_name = "05B_uncovered_targets.tsv"
summary_name = "05B_summary.json"

script_artifact = input_artifact(implementation_script)
python_executable = str(Path(sys.executable).resolve())

recorded_command = [
    python_executable,
    str(implementation_script.resolve()),
    "--targets",
    str(targets.resolve()),
    "--candidate-union",
    str(candidate_union.resolve()),
    "--start-anchors",
    str(start_anchors.resolve()),
    "--end-anchors",
    str(end_anchors.resolve()),
    "--start-release-id",
    start_release_id,
    "--end-release-id",
    end_release_id,
    "--k",
    str(k),
    "--output",
    f"<stage-bundle>/{output_name}",
    "--summary",
    f"<stage-bundle>/{summary_name}",
]

parameters = {
    "k": k,
    "start_release_id": start_release_id,
    "end_release_id": end_release_id,
    "command": recorded_command,
}

implementation = {
    "name": "probe-uncovered-target-audit-v1",
    "version": "1",
    "language": "Python",
    "script_sha256": script_artifact.sha256,
}

inputs = (
    input_artifact(targets_manifest_path),
    input_artifact(candidate_union_manifest_path),
    input_artifact(start_anchor_manifest_path),
    input_artifact(end_anchor_manifest_path),
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
    stage="05B",
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
        "--targets",
        str(targets),
        "--candidate-union",
        str(candidate_union),
        "--start-anchors",
        str(start_anchors),
        "--end-anchors",
        str(end_anchors),
        "--start-release-id",
        start_release_id,
        "--end-release-id",
        end_release_id,
        "--k",
        str(k),
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
            "05B output contract violation: "
            f"missing={sorted(expected_outputs - produced)} "
            f"unexpected={sorted(produced - expected_outputs)}"
        )
