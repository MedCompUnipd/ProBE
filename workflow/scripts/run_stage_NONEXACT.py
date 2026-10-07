import json
import subprocess
import sys
from pathlib import Path

from probe.workflow.artifacts import (
    atomic_stage_bundle,
    validate_bundle,
)
from probe.workflow.outcome import write_stage_outcome
from probe.workflow.runtime import (
    input_artifact,
    runtime_environment,
)

context = globals()["snakemake"]

alignment_manifest_path = Path(context.input.alignment_manifest)
implementation_script = Path(context.input.implementation_script)

destination = Path(context.output[0]).parent


alignment_manifest = validate_bundle(
    alignment_manifest_path.parent,
    deep=False,
)

if alignment_manifest.stage != "06A":
    raise ValueError(
        f"NONEXACT expected upstream stage 06A, got {alignment_manifest.stage}"
    )

if alignment_manifest.role is not None or alignment_manifest.release_id is not None:
    raise ValueError("NONEXACT Stage 06A input must be a common bundle")

parameters_06a = dict(alignment_manifest.parameters)

start_release_id = parameters_06a.get("start_release_id")
end_release_id = parameters_06a.get("end_release_id")

if not isinstance(start_release_id, str) or not start_release_id:
    raise ValueError("NONEXACT Stage 06A missing start_release_id")

if not isinstance(end_release_id, str) or not end_release_id:
    raise ValueError("NONEXACT Stage 06A missing end_release_id")

if start_release_id == end_release_id:
    raise ValueError("NONEXACT START and END release IDs must be distinct")

alignment_name = "06A_alignment_jobs.tsv"
upstream_summary_name = "06A_summary.json"

declared = {artifact.path for artifact in alignment_manifest.outputs}

for filename in (
    alignment_name,
    upstream_summary_name,
    "stage_outcome.json",
):
    if filename not in declared:
        raise ValueError(f"NONEXACT Stage 06A bundle does not declare {filename}")

alignment_jobs = alignment_manifest_path.parent / alignment_name

upstream_summary_path = alignment_manifest_path.parent / upstream_summary_name

upstream_summary = json.loads(upstream_summary_path.read_text(encoding="utf-8"))

if upstream_summary.get("stage") != "06A":
    raise ValueError("NONEXACT upstream summary stage mismatch")

if upstream_summary.get("releases") != {
    "START": start_release_id,
    "END": end_release_id,
}:
    raise RuntimeError("NONEXACT upstream release contract violation")

upstream_total = upstream_summary.get("alignment_job_count")

if (
    isinstance(upstream_total, bool)
    or not isinstance(upstream_total, int)
    or upstream_total < 0
):
    raise ValueError("NONEXACT invalid upstream alignment_job_count")

exact_maps = upstream_summary.get("release_exact_counts")

if not isinstance(exact_maps, dict):
    raise ValueError("NONEXACT invalid upstream release_exact_counts")

upstream_exact = 0
upstream_nonexact = 0

for release in (
    start_release_id,
    end_release_id,
):
    counts = exact_maps.get(release)

    if not isinstance(counts, dict):
        raise ValueError(f"NONEXACT missing upstream exact counts for {release}")

    exact = counts.get("EXACT", 0)
    nonexact = counts.get("NONEXACT", 0)

    for name, value in (
        ("EXACT", exact),
        ("NONEXACT", nonexact),
    ):
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise ValueError(f"NONEXACT invalid upstream {name} count for {release}")

    upstream_exact += exact
    upstream_nonexact += nonexact

if upstream_exact + upstream_nonexact != upstream_total:
    raise RuntimeError("NONEXACT upstream exact/nonexact arithmetic violation")

output_name = "NONEXACT_alignment_jobs.tsv"
summary_name = "NONEXACT_summary.json"
outcome_name = "stage_outcome.json"

script_artifact = input_artifact(implementation_script)

python_executable = str(Path(sys.executable).resolve())

recorded_command = [
    python_executable,
    str(implementation_script.resolve()),
    "--alignment-jobs",
    str(alignment_jobs.resolve()),
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
    "name": "probe-nonexact-job-selection-v1",
    "version": "1",
    "language": "Python",
    "script_sha256": script_artifact.sha256,
}

inputs = (
    input_artifact(alignment_manifest_path),
    script_artifact,
)

expected_outputs = {
    output_name,
    summary_name,
    outcome_name,
}

expected_policy = {
    "selection_only": True,
    "alignment_made_here": False,
    "scoring_made_here": False,
    "same_entity_decision_made_here": False,
    "exact_jobs_excluded": True,
    "nonexact_rows_preserved_from_06A": True,
}

log_path = Path(context.log[0])
log_path.parent.mkdir(
    parents=True,
    exist_ok=True,
)

with (
    log_path.open(
        "w",
        encoding="utf-8",
    ) as log,
    atomic_stage_bundle(
        destination,
        stage="NONEXACT",
        role=None,
        release_id=None,
        parameters=parameters,
        ontology=alignment_manifest.ontology,
        implementation=implementation,
        inputs=inputs,
        environment=runtime_environment(),
        allow_invalidated_destination=True,
    ) as staging,
):
    command = [
        python_executable,
        str(implementation_script),
        "--alignment-jobs",
        str(alignment_jobs),
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

    summary = json.loads((staging / summary_name).read_text(encoding="utf-8"))

    if summary.get("stage") != "NONEXACT":
        raise ValueError("NONEXACT summary has invalid stage")

    if summary.get("releases") != {
        "START": start_release_id,
        "END": end_release_id,
    }:
        raise RuntimeError("NONEXACT summary release contract violation")

    if summary.get("policy") != expected_policy:
        raise RuntimeError("NONEXACT policy contract violation")

    input_count = summary.get("input_alignment_job_count")
    exact_count = summary.get("exact_job_count")
    nonexact_count = summary.get("nonexact_job_count")

    for name, value in (
        ("input_alignment_job_count", input_count),
        ("exact_job_count", exact_count),
        ("nonexact_job_count", nonexact_count),
    ):
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise ValueError(f"NONEXACT summary has invalid {name}")

    if input_count != upstream_total:
        raise RuntimeError(
            "NONEXACT input-count mismatch: "
            f"06A={upstream_total} "
            f"NONEXACT={input_count}"
        )

    if exact_count != upstream_exact:
        raise RuntimeError(
            "NONEXACT exact-count mismatch: "
            f"06A={upstream_exact} "
            f"NONEXACT={exact_count}"
        )

    if nonexact_count != upstream_nonexact:
        raise RuntimeError(
            "NONEXACT nonexact-count mismatch: "
            f"06A={upstream_nonexact} "
            f"NONEXACT={nonexact_count}"
        )

    if input_count != exact_count + nonexact_count:
        raise RuntimeError("NONEXACT partition arithmetic violation")

    outcome_counts = {
        "input_alignment_jobs": input_count,
        "exact_jobs_excluded": exact_count,
        "nonexact_jobs": nonexact_count,
    }

    if input_count == 0:
        write_stage_outcome(
            staging / outcome_name,
            state="SKIPPED_EMPTY_INPUT",
            reason_code="NO_ALIGNMENT_JOBS",
            message=(
                "Stage NONEXACT received an empty "
                "Stage-06A alignment workload. "
                "No dynamic-programming jobs were "
                "selected."
            ),
            counts=outcome_counts,
        )

    elif nonexact_count == 0:
        write_stage_outcome(
            staging / outcome_name,
            state="EMPTY_RESULT",
            reason_code="ALL_ALIGNMENT_JOBS_EXACT",
            message=(
                "Stage NONEXACT completed successfully "
                "but every Stage-06A alignment job was "
                "an exact sequence pair."
            ),
            counts=outcome_counts,
        )

    else:
        write_stage_outcome(
            staging / outcome_name,
            state="COMPLETED",
            reason_code="NONEXACT_JOBS_SELECTED",
            message=(
                "Stage NONEXACT selected the canonical "
                "non-exact Stage-06A jobs requiring "
                "dynamic programming."
            ),
            counts=outcome_counts,
        )

    produced = {
        path.relative_to(staging).as_posix()
        for path in staging.rglob("*")
        if path.is_file()
    }

    if produced != expected_outputs:
        raise RuntimeError(
            "NONEXACT output contract violation: "
            f"missing={sorted(expected_outputs - produced)} "
            f"unexpected={sorted(produced - expected_outputs)}"
        )
