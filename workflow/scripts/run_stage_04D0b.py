import subprocess
import sys
from pathlib import Path

from probe.workflow.artifacts import atomic_stage_bundle, validate_bundle
from probe.workflow.runtime import input_artifact, runtime_environment

context = globals()["snakemake"]

role = str(context.wildcards.role)

ox_manifest_path = Path(context.input.ox_manifest)
taxonomy_manifest_path = Path(context.input.taxonomy_manifest)
implementation_script = Path(context.input.implementation_script)
taxonomy_module = Path(context.input.taxonomy_module)
destination = Path(context.output[0]).parent

ox_bundle = ox_manifest_path.parent
taxonomy_bundle = taxonomy_manifest_path.parent

ox_manifest = validate_bundle(ox_bundle, deep=False)
taxonomy_manifest = validate_bundle(taxonomy_bundle, deep=False)

if ox_manifest.stage != "04D0a":
    raise ValueError(
        f"04D0b expected upstream stage 04D0a, got {ox_manifest.stage}"
    )

if taxonomy_manifest.stage != "02B":
    raise ValueError(
        f"04D0b expected taxonomy stage 02B, got {taxonomy_manifest.stage}"
    )

if ox_manifest.role != role:
    raise ValueError(
        f"04D0b OX role mismatch: expected {role}, got {ox_manifest.role}"
    )

if taxonomy_manifest.role != role:
    raise ValueError(
        "04D0b taxonomy role mismatch: "
        f"expected {role}, got {taxonomy_manifest.role}"
    )

if ox_manifest.release_id is None:
    raise ValueError("04D0b requires an OX upstream release_id")

if taxonomy_manifest.release_id is None:
    raise ValueError("04D0b requires a taxonomy upstream release_id")

if ox_manifest.release_id != taxonomy_manifest.release_id:
    raise ValueError(
        "04D0b upstream release_id mismatch: "
        f"04D0a={ox_manifest.release_id} "
        f"02B={taxonomy_manifest.release_id}"
    )

ox_outputs = {item.path for item in ox_manifest.outputs}
taxonomy_outputs = {item.path for item in taxonomy_manifest.outputs}

ox_name = "04D0a_full_raw_ox.txt"
taxonomy_name = "02B_taxonomy.sqlite"

if ox_name not in ox_outputs:
    raise ValueError(f"04D0a bundle does not declare {ox_name}")

if taxonomy_name not in taxonomy_outputs:
    raise ValueError(f"02B bundle does not declare {taxonomy_name}")

ox_list = ox_bundle / ox_name
taxonomy_index = taxonomy_bundle / taxonomy_name

output_name = "full_ox_taxonomy.tsv"
summary_name = "04D0b_summary.json"

implementation_artifact = input_artifact(implementation_script)
taxonomy_module_artifact = input_artifact(taxonomy_module)

python_executable = str(Path(sys.executable).resolve())

recorded_command = [
    python_executable,
    str(implementation_script.resolve()),
    "--ox-list",
    str(ox_list.resolve()),
    "--taxonomy-index",
    str(taxonomy_index.resolve()),
    "--output",
    f"<stage-bundle>/{output_name}",
    "--summary",
    f"<stage-bundle>/{summary_name}",
]

parameters = {
    "command": recorded_command,
}

implementation = {
    "name": "04D0_prepare_full_ox_taxonomy",
    "version": "1",
    "language": "Python",
    "script_sha256": implementation_artifact.sha256,
    "taxonomy_module_sha256": taxonomy_module_artifact.sha256,
}

inputs = (
    input_artifact(ox_manifest_path),
    input_artifact(taxonomy_manifest_path),
    implementation_artifact,
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
    stage="04D0b",
    role=role,
    release_id=ox_manifest.release_id,
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
        "--ox-list",
        str(ox_list),
        "--taxonomy-index",
        str(taxonomy_index),
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
            "04D0b output contract violation: "
            f"missing={sorted(expected_outputs - produced)} "
            f"unexpected={sorted(produced - expected_outputs)}"
        )
