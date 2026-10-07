"""Publish Stage 01 canonical prepared targets as an atomic ProBE bundle."""

from pathlib import Path

from probe.target_preparation import (
    CANONICAL_SEQUENCE_POLICY_VERSION,
    prepare_targets,
)
from probe.workflow.artifacts import atomic_stage_bundle
from probe.workflow.outcome import write_stage_outcome
from probe.workflow.runtime import input_artifact, runtime_environment

context = globals()["snakemake"]

target_mode = str(context.params.target_mode)
target_policy = str(context.params.target_policy)

if target_mode != "canonical":
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
    "target_mode": target_mode,
    "target_policy": target_policy,
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
            allow_invalidated_destination=True,
        ) as staging:
            result = prepare_targets(
                target_fasta=target_fasta,
                metadata_tsv=target_metadata,
                output_directory=staging,
            )

            log.write(f"accepted_targets\t{result.accepted_count}\n")
            log.write(f"rejected_targets\t{result.rejected_count}\n")

            outcome_counts = {
                "accepted_targets": result.accepted_count,
                "rejected_targets": result.rejected_count,
            }

            if result.accepted_count == 0:
                write_stage_outcome(
                    staging / "stage_outcome.json",
                    state="EMPTY_RESULT",
                    reason_code="NO_ACCEPTED_TARGETS",
                    message=(
                        "Canonical target preparation completed successfully "
                        "but no targets satisfied the target contract."
                    ),
                    counts=outcome_counts,
                )
            else:
                write_stage_outcome(
                    staging / "stage_outcome.json",
                    state="COMPLETED",
                    reason_code="TARGETS_ACCEPTED",
                    message=(
                        "Canonical target preparation completed successfully "
                        "and accepted targets for downstream processing."
                    ),
                    counts=outcome_counts,
                )
    except Exception as error:
        log.write(f"FAILED\t{type(error).__name__}: {error}\n")
        raise
