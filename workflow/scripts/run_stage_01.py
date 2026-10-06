"""Publish Stage 01 canonical prepared targets as an atomic ProBE bundle."""

from pathlib import Path

from probe.target_preparation import (
    CANONICAL_SEQUENCE_POLICY_VERSION,
    prepare_targets,
)
from probe.workflow.artifacts import atomic_stage_bundle
from probe.workflow.runtime import input_artifact, runtime_environment

context = globals()["snakemake"]

if context.params.target_contract["mode"] != "canonical":
    raise RuntimeError(
        "Stage 01 adapter handoff is not implemented yet; "
        "Stage 00 must first provide canonical target inputs"
    )

sources = [Path(path) for path in context.input.sources]
if len(sources) != 2:
    raise ValueError("Stage 01 requires canonical FASTA and metadata TSV")

target_fasta, target_metadata = sources
destination = Path(context.output[0]).parent

command = [
    "python",
    "scripts/pipeline/01_prepare_targets.py",
    "--target-fasta",
    str(target_fasta),
    "--target-metadata",
    str(target_metadata),
    "--output-directory",
    "<stage-bundle>",
]

inputs = tuple(input_artifact(path) for path in sources)

parameters = {
    "scientific": dict(context.params.scientific),
    "target_contract": dict(context.params.target_contract),
    "command": command,
}

implementation = {
    "name": "probe.target_preparation.prepare_targets",
    "version": CANONICAL_SEQUENCE_POLICY_VERSION,
    "entrypoint": "scripts/pipeline/01_prepare_targets.py",
}

log_path = Path(context.log[0])
log_path.parent.mkdir(parents=True, exist_ok=True)

with log_path.open("w", encoding="utf-8") as log:
    try:
        with atomic_stage_bundle(
            destination,
            stage="01",
            role=None,
            release_id=None,
            parameters=parameters,
            ontology=None,
            implementation=implementation,
            inputs=inputs,
            environment=runtime_environment(),
        ) as staging:
            result = prepare_targets(
                target_fasta=target_fasta,
                metadata_tsv=target_metadata,
                output_directory=staging,
            )

            log.write(f"accepted_targets\t{result.accepted_count}\n")
            log.write(f"rejected_targets\t{result.rejected_count}\n")
    except Exception as error:
        log.write(f"FAILED\t{type(error).__name__}: {error}\n")
        raise
