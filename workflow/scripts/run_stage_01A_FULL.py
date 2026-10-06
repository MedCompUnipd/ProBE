"""Publish one full UniProt enriched-FASTA-v2 release bundle."""

import json
import subprocess
from pathlib import Path

from probe.workflow.artifacts import atomic_stage_bundle
from probe.workflow.runtime import input_artifact, runtime_environment

context = globals()["snakemake"]

role = str(context.wildcards.role)
release_id = str(context.params.release_id)

sources = [Path(path) for path in context.input.sources]
binary = Path(context.input.binary)
destination = Path(context.output[0]).parent

source_artifacts = tuple(input_artifact(path) for path in sources)
binary_artifact = input_artifact(binary)

output_prefix_name = "01A_full_v2"
progress_every = 0

command = [str(binary)]
for source in sources:
    command.extend(["--input", str(source)])
command.extend(
    [
        "--output-prefix",
        "<stage-bundle>/01A_full_v2",
        "--release-id",
        release_id,
        "--progress-every",
        str(progress_every),
    ]
)

parameters = {
    "release_id": release_id,
    "source_order": [str(path.resolve()) for path in sources],
    "progress_every": progress_every,
    "command": command,
}

implementation = {
    "name": "probe-release-fasta-v2",
    "version": "2",
    "binary_sha256": binary_artifact.sha256,
    "build_task": "build-release-fasta",
}

log_path = Path(context.log[0])
log_path.parent.mkdir(parents=True, exist_ok=True)

with log_path.open("w", encoding="utf-8") as log:
    try:
        with atomic_stage_bundle(
            destination,
            stage="01A_FULL",
            role=role,
            release_id=release_id,
            parameters=parameters,
            ontology=None,
            implementation=implementation,
            inputs=(*source_artifacts, binary_artifact),
            environment=runtime_environment(),
            allow_invalidated_destination=True,
        ) as staging:
            actual_command = [str(binary)]
            for source in sources:
                actual_command.extend(["--input", str(source)])
            actual_command.extend(
                [
                    "--output-prefix",
                    str(staging / output_prefix_name),
                    "--release-id",
                    release_id,
                    "--progress-every",
                    str(progress_every),
                ]
            )

            subprocess.run(
                actual_command,
                check=True,
                stdout=log,
                stderr=subprocess.STDOUT,
                text=True,
            )

            summary_path = staging / f"{output_prefix_name}.summary.json"
            summary = json.loads(summary_path.read_text(encoding="utf-8"))
            summary["outputs"] = {
                "fasta": f"{output_prefix_name}.fasta",
                "issues": f"{output_prefix_name}.issues.tsv",
                "summary": f"{output_prefix_name}.summary.json",
            }
            summary_path.write_text(
                json.dumps(summary, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
    except Exception as error:
        log.write(f"FAILED\t{type(error).__name__}: {error}\n")
        raise
