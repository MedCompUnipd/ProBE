import subprocess
import sys
from pathlib import Path

from probe.workflow.artifacts import atomic_stage_bundle, validate_bundle
from probe.workflow.runtime import input_artifact, runtime_environment

context = globals()["snakemake"]

role = str(context.wildcards.role)

evidence_manifest_path = Path(context.input.evidence_manifest)
implementation_script = Path(context.input.implementation_script)

destination = Path(context.output[0]).parent
evidence_bundle = evidence_manifest_path.parent

evidence_manifest = validate_bundle(evidence_bundle, deep=False)

if evidence_manifest.stage != "04D2":
    raise ValueError(
        f"04E expected upstream stage 04D2, got {evidence_manifest.stage}"
    )

if evidence_manifest.role != role:
    raise ValueError(
        "04E upstream role mismatch: "
        f"expected {role}, got {evidence_manifest.role}"
    )

release_id = evidence_manifest.release_id
if release_id is None:
    raise ValueError("04E requires an upstream release_id")

evidence_name = "04D2_lineage_evidence.tsv"

declared_outputs = {item.path for item in evidence_manifest.outputs}
if evidence_name not in declared_outputs:
    raise ValueError(f"04D2 bundle does not declare {evidence_name}")

evidence = evidence_bundle / evidence_name

output_name = "04E_lineage_candidates.tsv"
summary_name = "04E_summary.json"

script_artifact = input_artifact(implementation_script)
python_executable = str(Path(sys.executable).resolve())

recorded_command = [
    python_executable,
    str(implementation_script.resolve()),
    "--input",
    str(evidence.resolve()),
    "--output",
    f"<stage-bundle>/{output_name}",
    "--summary",
    f"<stage-bundle>/{summary_name}",
]

parameters = {
    "release_id": release_id,
    "command": recorded_command,
}

implementation = {
    "name": "probe-lineage-evidence-collapse-v1",
    "version": "1",
    "language": "Python",
    "script_sha256": script_artifact.sha256,
}

inputs = (
    input_artifact(evidence_manifest_path),
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
    stage="04E",
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
        "--input",
        str(evidence),
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
            "04E output contract violation: "
            f"missing={sorted(expected_outputs - produced)} "
            f"unexpected={sorted(produced - expected_outputs)}"
        )
