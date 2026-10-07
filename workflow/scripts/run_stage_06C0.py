import csv
import json
import subprocess
from itertools import zip_longest
from pathlib import Path

from probe.workflow.artifacts import atomic_stage_bundle, validate_bundle
from probe.workflow.outcome import write_stage_outcome
from probe.workflow.runtime import input_artifact, runtime_environment

context = globals()["snakemake"]

target_manifest_path = Path(context.input.target_manifest)
nonexact_manifest_path = Path(context.input.nonexact_manifest)
start_store_manifest_path = Path(context.input.start_store_manifest)
end_store_manifest_path = Path(context.input.end_store_manifest)
binary = Path(context.input.binary)

destination = Path(context.output[0]).parent
threads = int(context.threads)
alignment_policy = dict(context.params.alignment_policy)

EXPECTED_ALIGNMENT_POLICY = {
    "modes": ["TARGET_GLOBAL", "CANDIDATE_GLOBAL"],
    "algorithm": "AFFINE_FULL_DP",
    "banding": False,
    "match": 2,
    "mismatch": -1,
    "gap_open": -3,
    "gap_extend": -1,
    "gap_cost": "open_plus_length_minus_one_extensions",
    "traceback_policy": "reference_06C0",
    "exact_pairs": "MATHEMATICAL_NO_DP",
}

BASE_FIELDS = [
    "release",
    "target_id",
    "rid",
    "job_scope",
    "discovery_channel",
    "target_optional_accession",
    "candidate_primary_accession",
    "target_length",
    "candidate_length",
]

DIR_SUFFIXES = [
    "status",
    "score",
    "target_start0",
    "target_end0_exclusive",
    "candidate_start0",
    "candidate_end0_exclusive",
    "target_aligned_span",
    "candidate_aligned_span",
    "target_coverage",
    "candidate_coverage",
    "matches",
    "mismatches",
    "gap_in_target_residues",
    "gap_in_candidate_residues",
    "gap_openings",
    "aligned_residue_pairs",
    "alignment_columns",
    "identity_aligned_pairs",
    "identity_alignment_columns",
    "cells_evaluated",
]

EXPECTED_FIELDS = (
    BASE_FIELDS
    + [f"target_global_{x}" for x in DIR_SUFFIXES]
    + [f"candidate_global_{x}" for x in DIR_SUFFIXES]
)


def require_common(manifest, stage):
    if manifest.stage != stage:
        raise ValueError(f"06C0 expected stage {stage}, got {manifest.stage}")

    if manifest.role is not None or manifest.release_id is not None:
        raise ValueError(f"06C0 Stage {stage} input must be a common bundle")


def require_release(manifest, stage, role):
    if manifest.stage != stage:
        raise ValueError(f"06C0 {role} expected stage {stage}, got {manifest.stage}")

    if manifest.role != role:
        raise ValueError(
            f"06C0 Stage {stage} expected role {role}, got {manifest.role}"
        )

    if not manifest.release_id:
        raise ValueError(f"06C0 {role} Stage {stage} missing release_id")


def require_artifact(manifest, manifest_path, filename):
    declared = {artifact.path for artifact in manifest.outputs}

    if filename not in declared:
        raise ValueError(
            f"06C0 Stage {manifest.stage} bundle does not declare {filename}"
        )

    return manifest_path.parent / filename


def positive_int(value, label):
    try:
        result = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label}: invalid integer {value!r}") from exc

    if result <= 0:
        raise ValueError(f"{label}: expected positive integer")

    return result


def nonnegative_int(value, label):
    try:
        result = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label}: invalid integer {value!r}") from exc

    if result < 0:
        raise ValueError(f"{label}: expected non-negative integer")

    return result


def preflight_nonexact(path, releases):
    required = {
        "release",
        "target_id",
        "rid",
        "job_scope",
        "discovery_channel",
        "target_optional_accession",
        "candidate_primary_accession",
        "target_length",
        "candidate_length",
        "exact_sequence",
    }

    count = 0
    directional_cells = 0
    previous_key = None

    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle, delimiter="\t")

        if reader.fieldnames is None:
            raise ValueError(f"{path}: missing header")

        missing = required.difference(reader.fieldnames)

        if missing:
            raise ValueError(f"{path}: missing columns {sorted(missing)}")

        for row in reader:
            release = row["release"]

            if release not in releases:
                raise ValueError(f"{path}: unexpected release {release!r}")

            if not row["target_id"]:
                raise ValueError(f"{path}: empty target_id")

            rid = positive_int(
                row["rid"],
                f"{path}: RID",
            )

            if str(rid) != row["rid"]:
                raise ValueError(f"{path}: non-canonical RID {row['rid']!r}")

            key = (
                release,
                row["target_id"],
                rid,
            )

            if previous_key is not None and key <= previous_key:
                raise ValueError(
                    "06C0 NONEXACT jobs are not "
                    "strictly sorted/unique: "
                    f"previous={previous_key} current={key}"
                )

            previous_key = key

            if row["exact_sequence"] != "0":
                raise ValueError(f"{path}: exact job reached 06C0: {key}")

            if row["job_scope"] not in {
                "PRIMARY",
                "TAXONOMY_AUDIT",
            }:
                raise ValueError(f"{path}: invalid job_scope {row['job_scope']!r}")

            target_length = positive_int(
                row["target_length"],
                f"{path}:{key}:target_length",
            )

            candidate_length = positive_int(
                row["candidate_length"],
                f"{path}:{key}:candidate_length",
            )

            count += 1
            directional_cells += 2 * (target_length + 1) * (candidate_length + 1)

    return count, directional_cells


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


def write_empty_alignment_table(path):
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(
            handle,
            delimiter="\t",
            lineterminator="\n",
        )
        writer.writerow(EXPECTED_FIELDS)


def write_zero_summary(path):
    rows = (
        ("job_count", "0"),
        ("completed_count", "0"),
        ("threads", "0"),
        ("directional_alignment_count", "0"),
        ("total_dp_cells_evaluated", "0"),
        ("status_OK", "0"),
    )

    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(
            handle,
            delimiter="\t",
            lineterminator="\n",
        )
        writer.writerow(("metric", "value"))
        writer.writerows(rows)


def validate_alignment_output(
    jobs_path,
    output_path,
    expected_job_count,
    expected_total_cells,
):
    observed_jobs = 0
    observed_cells = 0

    with (
        jobs_path.open(
            "r",
            encoding="utf-8",
            newline="",
        ) as jobs_handle,
        output_path.open(
            "r",
            encoding="utf-8",
            newline="",
        ) as output_handle,
    ):
        jobs_reader = csv.DictReader(
            jobs_handle,
            delimiter="\t",
        )
        output_reader = csv.DictReader(
            output_handle,
            delimiter="\t",
        )

        if output_reader.fieldnames != EXPECTED_FIELDS:
            raise ValueError("06C0 alignment output schema mismatch")

        for job, row in zip_longest(
            jobs_reader,
            output_reader,
        ):
            if job is None or row is None:
                raise RuntimeError("06C0 input/output row-count mismatch")

            observed_jobs += 1

            for field in BASE_FIELDS:
                if row[field] != job[field]:
                    raise RuntimeError(
                        "06C0 base metadata mismatch "
                        f"for field {field}: "
                        f"job={job[field]!r} "
                        f"output={row[field]!r}"
                    )

            n = positive_int(
                row["target_length"],
                "06C0 output target_length",
            )
            m = positive_int(
                row["candidate_length"],
                "06C0 output candidate_length",
            )

            cells = (n + 1) * (m + 1)

            for prefix in (
                "target_global",
                "candidate_global",
            ):
                if row[f"{prefix}_status"] != "OK":
                    raise RuntimeError(f"06C0 {prefix} status is not OK")

                actual_cells = nonnegative_int(
                    row[f"{prefix}_cells_evaluated"],
                    f"06C0 {prefix} cells",
                )

                if actual_cells != cells:
                    raise RuntimeError(
                        f"06C0 {prefix} DP-cell mismatch: "
                        f"expected={cells} "
                        f"observed={actual_cells}"
                    )

                observed_cells += actual_cells

            if (
                row["target_global_target_start0"] != "0"
                or row["target_global_target_end0_exclusive"] != str(n)
                or row["target_global_target_aligned_span"] != str(n)
                or row["target_global_target_coverage"] != "1.0000000000"
            ):
                raise RuntimeError("06C0 TARGET_GLOBAL invariant violation")

            if (
                row["candidate_global_candidate_start0"] != "0"
                or row["candidate_global_candidate_end0_exclusive"] != str(m)
                or row["candidate_global_candidate_aligned_span"] != str(m)
                or row["candidate_global_candidate_coverage"] != "1.0000000000"
            ):
                raise RuntimeError("06C0 CANDIDATE_GLOBAL invariant violation")

    if observed_jobs != expected_job_count:
        raise RuntimeError(
            "06C0 output job-count mismatch: "
            f"expected={expected_job_count} "
            f"observed={observed_jobs}"
        )

    if observed_cells != expected_total_cells:
        raise RuntimeError(
            "06C0 output DP-cell mismatch: "
            f"expected={expected_total_cells} "
            f"observed={observed_cells}"
        )


if alignment_policy != EXPECTED_ALIGNMENT_POLICY:
    raise ValueError(
        "06C0 production alignment policy differs "
        "from the frozen directional-glocal FULL-DP contract"
    )

if threads <= 0:
    raise ValueError("06C0 requires at least one thread")

target_manifest = validate_bundle(
    target_manifest_path.parent,
    deep=False,
)
nonexact_manifest = validate_bundle(
    nonexact_manifest_path.parent,
    deep=False,
)
start_store_manifest = validate_bundle(
    start_store_manifest_path.parent,
    deep=False,
)
end_store_manifest = validate_bundle(
    end_store_manifest_path.parent,
    deep=False,
)

require_common(target_manifest, "01")
require_common(nonexact_manifest, "NONEXACT")
require_release(start_store_manifest, "05D", "START")
require_release(end_store_manifest, "05D", "END")

start_release_id = start_store_manifest.release_id
end_release_id = end_store_manifest.release_id

if start_release_id == end_release_id:
    raise ValueError("06C0 START and END release IDs must be distinct")

nonexact_parameters = dict(nonexact_manifest.parameters)

if nonexact_parameters.get("start_release_id") != start_release_id:
    raise ValueError("06C0 NONEXACT START release mismatch")

if nonexact_parameters.get("end_release_id") != end_release_id:
    raise ValueError("06C0 NONEXACT END release mismatch")

targets = require_artifact(
    target_manifest,
    target_manifest_path,
    "01_targets.tsv",
)

nonexact_jobs = require_artifact(
    nonexact_manifest,
    nonexact_manifest_path,
    "NONEXACT_alignment_jobs.tsv",
)

nonexact_summary_path = require_artifact(
    nonexact_manifest,
    nonexact_manifest_path,
    "NONEXACT_summary.json",
)

start_store = require_artifact(
    start_store_manifest,
    start_store_manifest_path,
    "05D_candidate_sequences.tsv",
)

end_store = require_artifact(
    end_store_manifest,
    end_store_manifest_path,
    "05D_candidate_sequences.tsv",
)

nonexact_summary = json.loads(nonexact_summary_path.read_text(encoding="utf-8"))

if nonexact_summary.get("stage") != "NONEXACT":
    raise ValueError("06C0 upstream NONEXACT summary stage mismatch")

if nonexact_summary.get("releases") != {
    "START": start_release_id,
    "END": end_release_id,
}:
    raise RuntimeError("06C0 upstream NONEXACT release contract violation")

job_count, expected_total_cells = preflight_nonexact(
    nonexact_jobs,
    {
        start_release_id,
        end_release_id,
    },
)

upstream_job_count = nonexact_summary.get("nonexact_job_count")

if (
    isinstance(upstream_job_count, bool)
    or not isinstance(upstream_job_count, int)
    or upstream_job_count < 0
):
    raise ValueError("06C0 upstream NONEXACT count is invalid")

if upstream_job_count != job_count:
    raise RuntimeError(
        "06C0 NONEXACT preflight/summary mismatch: "
        f"summary={upstream_job_count} "
        f"preflight={job_count}"
    )

output_name = "06C0_nonexact_directional_glocal.tsv"
summary_name = "06C0_summary.tsv"
outcome_name = "stage_outcome.json"

binary_artifact = input_artifact(binary)

recorded_command = [
    str(binary.resolve()),
    "--targets",
    str(targets.resolve()),
    "--alignment-jobs",
    str(nonexact_jobs.resolve()),
    "--start-sequence-store",
    str(start_store.resolve()),
    "--end-sequence-store",
    str(end_store.resolve()),
    "--start-release-id",
    start_release_id,
    "--end-release-id",
    end_release_id,
    "--output",
    f"<stage-bundle>/{output_name}",
    "--summary",
    f"<stage-bundle>/{summary_name}",
    "--threads",
    str(threads),
]

parameters = {
    "start_release_id": start_release_id,
    "end_release_id": end_release_id,
    "alignment_policy": alignment_policy,
    "threads": threads,
    "command_executed": job_count > 0,
    "command": recorded_command,
}

implementation = {
    "name": "probe-directional-glocal-full-dp-v1",
    "version": "1",
    "language": "C++20/OpenMP",
    "binary_sha256": binary_artifact.sha256,
}

inputs = (
    input_artifact(target_manifest_path),
    input_artifact(nonexact_manifest_path),
    input_artifact(start_store_manifest_path),
    input_artifact(end_store_manifest_path),
    binary_artifact,
)

expected_outputs = {
    output_name,
    summary_name,
    outcome_name,
}

log_path = Path(context.log[0])
log_path.parent.mkdir(
    parents=True,
    exist_ok=True,
)

with (
    log_path.open("w", encoding="utf-8") as log,
    atomic_stage_bundle(
        destination,
        stage="06C0",
        role=None,
        release_id=None,
        parameters=parameters,
        ontology=nonexact_manifest.ontology,
        implementation=implementation,
        inputs=inputs,
        environment=runtime_environment(),
        allow_invalidated_destination=True,
    ) as staging,
):
    output_path = staging / output_name
    summary_path = staging / summary_name

    if job_count == 0:
        write_empty_alignment_table(output_path)
        write_zero_summary(summary_path)

    else:
        command = [
            str(binary),
            "--targets",
            str(targets),
            "--alignment-jobs",
            str(nonexact_jobs),
            "--start-sequence-store",
            str(start_store),
            "--end-sequence-store",
            str(end_store),
            "--start-release-id",
            start_release_id,
            "--end-release-id",
            end_release_id,
            "--output",
            str(output_path),
            "--summary",
            str(summary_path),
            "--threads",
            str(threads),
        ]

        subprocess.run(
            command,
            stdout=log,
            stderr=subprocess.STDOUT,
            check=True,
        )

    metrics = read_metric_summary(summary_path)

    required_metrics = {
        "job_count",
        "completed_count",
        "threads",
        "directional_alignment_count",
        "total_dp_cells_evaluated",
        "status_OK",
    }

    if set(metrics) != required_metrics:
        raise ValueError(
            f"06C0 summary metric contract violation: observed={sorted(metrics)}"
        )

    observed_job_count = nonnegative_int(
        metrics["job_count"],
        "06C0 summary job_count",
    )

    completed_count = nonnegative_int(
        metrics["completed_count"],
        "06C0 summary completed_count",
    )

    directional_count = nonnegative_int(
        metrics["directional_alignment_count"],
        "06C0 summary directional_alignment_count",
    )

    total_cells = nonnegative_int(
        metrics["total_dp_cells_evaluated"],
        "06C0 summary total_dp_cells_evaluated",
    )

    status_ok = nonnegative_int(
        metrics["status_OK"],
        "06C0 summary status_OK",
    )

    summary_threads = nonnegative_int(
        metrics["threads"],
        "06C0 summary threads",
    )

    expected_threads = threads if job_count else 0

    expected = {
        "job_count": job_count,
        "completed_count": job_count,
        "directional_alignment_count": job_count * 2,
        "total_dp_cells_evaluated": expected_total_cells,
        "status_OK": job_count * 2,
        "threads": expected_threads,
    }

    observed = {
        "job_count": observed_job_count,
        "completed_count": completed_count,
        "directional_alignment_count": directional_count,
        "total_dp_cells_evaluated": total_cells,
        "status_OK": status_ok,
        "threads": summary_threads,
    }

    if observed != expected:
        raise RuntimeError(
            f"06C0 summary invariant violation: expected={expected} observed={observed}"
        )

    validate_alignment_output(
        nonexact_jobs,
        output_path,
        job_count,
        expected_total_cells,
    )

    counts = {
        "nonexact_jobs": job_count,
        "directional_alignments": job_count * 2,
        "total_dp_cells_evaluated": expected_total_cells,
    }

    if job_count == 0:
        write_stage_outcome(
            staging / outcome_name,
            state="SKIPPED_EMPTY_INPUT",
            reason_code="NO_NONEXACT_ALIGNMENT_JOBS",
            message=(
                "Stage 06C0 received no NONEXACT jobs. "
                "No dynamic-programming alignment was required."
            ),
            counts=counts,
        )

    else:
        write_stage_outcome(
            staging / outcome_name,
            state="COMPLETED",
            reason_code=("NONEXACT_DIRECTIONAL_ALIGNMENTS_COMPUTED"),
            message=(
                "Stage 06C0 computed both exact full-DP "
                "directional-glocal alignments for every "
                "NONEXACT canonical pair."
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
            "06C0 output contract violation: "
            f"missing={sorted(expected_outputs - produced)} "
            f"unexpected={sorted(produced - expected_outputs)}"
        )
