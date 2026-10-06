"""Publish the common GO normalization policy as an atomic ProBE bundle."""

from pathlib import Path

from probe.go_policy import POLICY_VERSION, compile_go_policy
from probe.workflow.artifacts import atomic_stage_bundle
from probe.workflow.runtime import (
    input_artifact,
    ontology_provenance,
    runtime_environment,
)

context = globals()["snakemake"]

owl = Path(context.input.owl)
destination = Path(context.output[0]).parent
ontology_config = dict(context.params.ontology)
go_policy = str(context.params.go_policy)

owl_artifact = input_artifact(owl)
expected_sha256 = ontology_config["expected_sha256"]

command = [
    "python",
    "scripts/pipeline/01B0_compile_go_policy.py",
    "--owl",
    str(owl),
    "--output-dir",
    "<stage-bundle>",
]
if expected_sha256 is not None:
    command.extend(["--expected-owl-sha256", expected_sha256])

parameters = {
    "go_policy": go_policy,
    "expected_owl_sha256": expected_sha256,
    "command": command,
}

implementation = {
    "name": "probe.go_policy.compile_go_policy",
    "version": POLICY_VERSION,
    "entrypoint": "scripts/pipeline/01B0_compile_go_policy.py",
}

ontology = ontology_provenance(
    ontology_config,
    path=owl,
    observed_sha256=owl_artifact.sha256,
)

log_path = Path(context.log[0])
log_path.parent.mkdir(parents=True, exist_ok=True)

with log_path.open("w", encoding="utf-8") as log:
    try:
        with atomic_stage_bundle(
            destination,
            stage="01B0",
            role=None,
            release_id=None,
            parameters=parameters,
            ontology=ontology,
            implementation=implementation,
            inputs=(owl_artifact,),
            environment=runtime_environment(),
            allow_invalidated_destination=True,
        ) as staging:
            result = compile_go_policy(
                owl_path=owl,
                output_directory=staging,
                expected_owl_sha256=expected_sha256,
            )

            log.write(f"policy_rows\t{result.policy_row_count}\n")
            log.write(
                f"usable_resolutions\t{result.usable_resolution_count}\n"
            )
            log.write(
                f"remapped_resolutions\t"
                f"{result.remapped_resolution_count}\n"
            )
            log.write(f"ontology_sha256\t{owl_artifact.sha256}\n")
    except Exception as error:
        log.write(f"FAILED\t{type(error).__name__}: {error}\n")
        raise
