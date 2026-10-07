import csv
import json
import subprocess
import sys
from pathlib import Path

from probe.workflow.artifacts import atomic_stage_bundle, validate_bundle
from probe.workflow.outcome import write_stage_outcome
from probe.workflow.runtime import input_artifact, runtime_environment

context = globals()["snakemake"]

alignment_manifest_path = Path(context.input.alignment_manifest)
full_dp_manifest_path = Path(context.input.full_dp_manifest)
implementation_script = Path(context.input.implementation_script)

destination = Path(context.output[0]).parent


def require_common(manifest, stage):
    if manifest.stage != stage:
        raise ValueError(f"06C1 expected upstream stage {stage}, got {manifest.stage}")

    if manifest.role is not None or manifest.release_id is not None:
        raise ValueError(f"06C1 Stage {stage} input must be a common bundle")


def require_artifact(manifest, manifest_path, filename):
    declared = {artifact.path for artifact in manifest.outputs}

    if filename not in declared:
        raise ValueError(
            f"06C1 Stage {manifest.stage} bundle does not declare {filename}"
        )

    return manifest_path.parent / filename


def nonnegative_int(value, label):
    if isinstance(value, bool):
        raise ValueError(f"{label}: boolean is not an integer count")

    try:
        result = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label}: invalid integer {value!r}") from exc

    if result < 0:
        raise ValueError(f"{label}: expected non-negative integer")

    return result


def exact_counts(summary, releases):
    data = summary.get("release_exact_counts")

    if not isinstance(data, dict):
        raise ValueError("06C1 Stage-06A summary missing release_exact_counts")

    exact = 0
    nonexact = 0

    for release in releases:
        bucket = data.get(release, {})

        if not isinstance(bucket, dict):
            raise ValueError(f"06C1 invalid release_exact_counts for {release}")

        exact += nonnegative_int(
            bucket.get("EXACT", 0),
            f"06C1 {release} EXACT count",
        )

        nonexact += nonnegative_int(
            bucket.get("NONEXACT", 0),
            f"06C1 {release} NONEXACT count",
        )

        unexpected = set(bucket).difference({"EXACT", "NONEXACT"})

        if unexpected:
            raise ValueError(
                f"06C1 unexpected exactness classes for {release}: {sorted(unexpected)}"
            )

    return exact, nonexact


def read_metric_summary(path):
    metrics = {}

    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle, delimiter="\t")

        if reader.fieldnames != ["metric", "value"]:
            raise ValueError(f"{path}: unexpected summary schema")

        for row in reader:
            metric = row["metric"]

            if metric in metrics:
                raise ValueError(f"{path}: duplicate metric {metric}")

            metrics[metric] = row["value"]

    return metrics


alignment_manifest = validate_bundle(
    alignment_manifest_path.parent,
    deep=False,
)

full_dp_manifest = validate_bundle(
    full_dp_manifest_path.parent,
    deep=False,
)

require_common(alignment_manifest, "06A")
require_common(full_dp_manifest, "06C0")

if alignment_manifest.ontology != full_dp_manifest.ontology:
    raise ValueError("06C1 ontology provenance mismatch between 06A and 06C0")

alignment_parameters = dict(alignment_manifest.parameters)
full_dp_parameters = dict(full_dp_manifest.parameters)

start_release_id = alignment_parameters.get("start_release_id")
end_release_id = alignment_parameters.get("end_release_id")

if (
    not isinstance(start_release_id, str)
    or not start_release_id
    or not isinstance(end_release_id, str)
    or not end_release_id
):
    raise ValueError("06C1 Stage-06A release provenance is missing")

if start_release_id == end_release_id:
    raise ValueError("06C1 START and END release IDs must be distinct")

if (
    full_dp_parameters.get("start_release_id") != start_release_id
    or full_dp_parameters.get("end_release_id") != end_release_id
):
    raise ValueError("06C1 release provenance mismatch between 06A and 06C0")

alignment_jobs = require_artifact(
    alignment_manifest,
    alignment_manifest_path,
    "06A_alignment_jobs.tsv",
)

alignment_summary_path = require_artifact(
    alignment_manifest,
    alignment_manifest_path,
    "06A_summary.json",
)

nonexact_alignments = require_artifact(
    full_dp_manifest,
    full_dp_manifest_path,
    "06C0_nonexact_directional_glocal.tsv",
)

full_dp_summary_path = require_artifact(
    full_dp_manifest,
    full_dp_manifest_path,
    "06C0_summary.tsv",
)

alignment_summary = json.loads(alignment_summary_path.read_text(encoding="utf-8"))

if alignment_summary.get("stage") != "06A":
    raise ValueError("06C1 Stage-06A summary stage mismatch")

if alignment_summary.get("releases") != {
    "START": start_release_id,
    "END": end_release_id,
}:
    raise ValueError("06C1 Stage-06A release summary mismatch")

canonical_pair_count = nonnegative_int(
    alignment_summary.get("alignment_job_count"),
    "06C1 Stage-06A alignment_job_count",
)

exact_pair_count, nonexact_pair_count = exact_counts(
    alignment_summary,
    (start_release_id, end_release_id),
)

if exact_pair_count + nonexact_pair_count != canonical_pair_count:
    raise RuntimeError(
        "06C1 Stage-06A exact/nonexact partition mismatch: "
        f"total={canonical_pair_count} "
        f"exact={exact_pair_count} "
        f"nonexact={nonexact_pair_count}"
    )

full_dp_metrics = read_metric_summary(full_dp_summary_path)

required_metrics = {
    "job_count",
    "completed_count",
    "threads",
    "directional_alignment_count",
    "total_dp_cells_evaluated",
    "status_OK",
}

if set(full_dp_metrics) != required_metrics:
    raise ValueError("06C1 Stage-06C0 summary metric contract violation")

full_dp_job_count = nonnegative_int(
    full_dp_metrics["job_count"],
    "06C1 Stage-06C0 job_count",
)

full_dp_completed_count = nonnegative_int(
    full_dp_metrics["completed_count"],
    "06C1 Stage-06C0 completed_count",
)

full_dp_directional_count = nonnegative_int(
    full_dp_metrics["directional_alignment_count"],
    "06C1 Stage-06C0 directional_alignment_count",
)

full_dp_status_ok = nonnegative_int(
    full_dp_metrics["status_OK"],
    "06C1 Stage-06C0 status_OK",
)

full_dp_cells = nonnegative_int(
    full_dp_metrics["total_dp_cells_evaluated"],
    "06C1 Stage-06C0 total_dp_cells_evaluated",
)

if full_dp_job_count != nonexact_pair_count:
    raise RuntimeError(
        "06C1 Stage-06C0/NONEXACT job-count mismatch: "
        f"expected={nonexact_pair_count} "
        f"observed={full_dp_job_count}"
    )

if full_dp_completed_count != nonexact_pair_count:
    raise RuntimeError("06C1 Stage-06C0 completed-count mismatch")

if full_dp_directional_count != nonexact_pair_count * 2:
    raise RuntimeError("06C1 Stage-06C0 directional-count mismatch")

if full_dp_status_ok != nonexact_pair_count * 2:
    raise RuntimeError("06C1 Stage-06C0 OK-status count mismatch")

output_name = "06C1_canonical_directional_glocal.tsv"
summary_name = "06C1_summary.json"
outcome_name = "stage_outcome.json"

script_artifact = input_artifact(implementation_script)

recorded_command = [
    sys.executable,
    str(implementation_script.resolve()),
    "--alignment-jobs",
    str(alignment_jobs.resolve()),
    "--nonexact-alignments",
    str(nonexact_alignments.resolve()),
    "--output",
    f"<stage-bundle>/{output_name}",
    "--summary",
    f"<stage-bundle>/{summary_name}",
    "--start-release-id",
    start_release_id,
    "--end-release-id",
    end_release_id,
]

parameters = {
    "start_release_id": start_release_id,
    "end_release_id": end_release_id,
    "canonical_pair_count": canonical_pair_count,
    "exact_pair_count": exact_pair_count,
    "nonexact_pair_count": nonexact_pair_count,
    "nonexact_directional_full_dp_cell_count": full_dp_cells,
    "exact_pairs_dp_bypassed": True,
    "command": recorded_command,
}

implementation = {
    "name": "06C1_finalize_directional_glocal",
    "version": "1",
    "language": "Python",
    "script_sha256": script_artifact.sha256,
}

inputs = (
    input_artifact(alignment_manifest_path),
    input_artifact(full_dp_manifest_path),
    script_artifact,
)

expected_outputs = {
    output_name,
    summary_name,
    outcome_name,
}

log_path = Path(context.log[0])
log_path.parent.mkdir(parents=True, exist_ok=True)

with (
    log_path.open("w", encoding="utf-8") as log,
    atomic_stage_bundle(
        destination,
        stage="06C1",
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
    output_path = staging / output_name
    summary_path = staging / summary_name

    command = [
        sys.executable,
        str(implementation_script),
        "--alignment-jobs",
        str(alignment_jobs),
        "--nonexact-alignments",
        str(nonexact_alignments),
        "--output",
        str(output_path),
        "--summary",
        str(summary_path),
        "--start-release-id",
        start_release_id,
        "--end-release-id",
        end_release_id,
    ]

    subprocess.run(
        command,
        stdout=log,
        stderr=subprocess.STDOUT,
        check=True,
    )

    summary = json.loads(summary_path.read_text(encoding="utf-8"))

    if summary.get("stage") != "06C1":
        raise RuntimeError("06C1 output summary stage mismatch")

    if summary.get("releases") != {
        "START": start_release_id,
        "END": end_release_id,
    }:
        raise RuntimeError("06C1 output summary release mismatch")

    observed = {
        "canonical_pair_count": nonnegative_int(
            summary.get("canonical_pair_count"),
            "06C1 canonical_pair_count",
        ),
        "exact_pair_count": nonnegative_int(
            summary.get("exact_pair_count"),
            "06C1 exact_pair_count",
        ),
        "nonexact_pair_count": nonnegative_int(
            summary.get("nonexact_pair_count"),
            "06C1 nonexact_pair_count",
        ),
        "directional_alignment_count": nonnegative_int(
            summary.get("directional_alignment_count"),
            "06C1 directional_alignment_count",
        ),
        "nonexact_directional_full_dp_cell_count": nonnegative_int(
            summary.get("nonexact_directional_full_dp_cell_count"),
            "06C1 nonexact DP cells",
        ),
    }

    expected = {
        "canonical_pair_count": canonical_pair_count,
        "exact_pair_count": exact_pair_count,
        "nonexact_pair_count": nonexact_pair_count,
        "directional_alignment_count": canonical_pair_count * 2,
        "nonexact_directional_full_dp_cell_count": full_dp_cells,
    }

    if observed != expected:
        raise RuntimeError(
            "06C1 output summary invariant violation: "
            f"expected={expected} observed={observed}"
        )

    counts = {
        "canonical_pairs": canonical_pair_count,
        "exact_pairs": exact_pair_count,
        "nonexact_pairs": nonexact_pair_count,
        "directional_alignments": canonical_pair_count * 2,
        "nonexact_dp_cells": full_dp_cells,
    }

    if canonical_pair_count == 0:
        write_stage_outcome(
            staging / outcome_name,
            state="EMPTY_RESULT",
            reason_code="NO_CANONICAL_ALIGNMENT_PAIRS",
            message=(
                "Stage 06C1 finalized an empty canonical "
                "directional-glocal alignment table because "
                "Stage 06A contained no alignment pairs."
            ),
            counts=counts,
        )
    else:
        write_stage_outcome(
            staging / outcome_name,
            state="COMPLETED",
            reason_code=("CANONICAL_DIRECTIONAL_ALIGNMENTS_FINALIZED"),
            message=(
                "Stage 06C1 finalized the canonical "
                "directional-glocal alignment table, "
                "synthesizing exact pairs mathematically and "
                "using Stage 06C0 full-DP results for NONEXACT "
                "pairs."
            ),
            counts=counts,
        )

    produced = {
        path.relative_to(staging).as_posix()
        for path in staging.rglob("*")
        if path.is_file()
    }

    if produced != expected_outputs:
        raise RuntimeError(
            "06C1 output contract violation: "
            f"missing={sorted(expected_outputs - produced)} "
            f"unexpected={sorted(produced - expected_outputs)}"
        )
