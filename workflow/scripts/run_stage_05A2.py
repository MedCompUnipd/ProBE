import json
import subprocess
import sys
from pathlib import Path

from probe.workflow.artifacts import atomic_stage_bundle, validate_bundle
from probe.workflow.outcome import write_stage_outcome
from probe.workflow.runtime import input_artifact, runtime_environment

context = globals()["snakemake"]

baseline_manifest_path = Path(context.input.baseline_manifest)
start_rescue_manifest_path = Path(context.input.start_rescue_manifest)
end_rescue_manifest_path = Path(context.input.end_rescue_manifest)
implementation_script = Path(context.input.implementation_script)

destination = Path(context.output[0]).parent


def load_manifest(path: Path):
    return validate_bundle(path.parent, deep=False)


baseline_manifest = load_manifest(baseline_manifest_path)
start_rescue_manifest = load_manifest(start_rescue_manifest_path)
end_rescue_manifest = load_manifest(end_rescue_manifest_path)

if baseline_manifest.stage != "05A":
    raise ValueError(f"05A2 baseline expected stage 05A, got {baseline_manifest.stage}")
if baseline_manifest.role is not None or baseline_manifest.release_id is not None:
    raise ValueError("05A2 Stage 05A input must be a common bundle")

for label, manifest, role in (
    ("START rescue", start_rescue_manifest, "START"),
    ("END rescue", end_rescue_manifest, "END"),
):
    if manifest.stage != "05R1":
        raise ValueError(f"05A2 {label} expected stage 05R1, got {manifest.stage}")
    if manifest.role != role:
        raise ValueError(f"05A2 {label} expected role {role}, got {manifest.role}")
    if manifest.release_id is None:
        raise ValueError(f"05A2 {label} requires a release_id")

start_release_id = start_rescue_manifest.release_id
end_release_id = end_rescue_manifest.release_id

if start_release_id == end_release_id:
    raise ValueError("05A2 START and END release IDs must be distinct")

baseline_parameters = dict(baseline_manifest.parameters)

if baseline_parameters.get("start_release_id") != start_release_id:
    raise ValueError(
        "05A2 START release mismatch: "
        f"05A={baseline_parameters.get('start_release_id')} "
        f"05R1={start_release_id}"
    )

if baseline_parameters.get("end_release_id") != end_release_id:
    raise ValueError(
        "05A2 END release mismatch: "
        f"05A={baseline_parameters.get('end_release_id')} "
        f"05R1={end_release_id}"
    )

baseline_name = "05A_candidate_union.tsv"
rescue_name = "05R1_full_sequence_rescue.pairs.tsv"

for label, manifest, filename in (
    ("05A", baseline_manifest, baseline_name),
    ("START 05R1", start_rescue_manifest, rescue_name),
    ("END 05R1", end_rescue_manifest, rescue_name),
):
    declared = {item.path for item in manifest.outputs}
    if filename not in declared:
        raise ValueError(f"{label} bundle does not declare {filename}")

baseline = baseline_manifest_path.parent / baseline_name
start_rescue = start_rescue_manifest_path.parent / rescue_name
end_rescue = end_rescue_manifest_path.parent / rescue_name

output_name = "05A2_final_candidate_union.tsv"
summary_name = "05A2_summary.json"
outcome_name = "stage_outcome.json"

script_artifact = input_artifact(implementation_script)
python_executable = str(Path(sys.executable).resolve())

recorded_command = [
    python_executable,
    str(implementation_script.resolve()),
    "--baseline-union",
    str(baseline.resolve()),
    "--start-rescue",
    str(start_rescue.resolve()),
    "--end-rescue",
    str(end_rescue.resolve()),
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
    "union_key": ["release", "target_id", "rid"],
    "command": recorded_command,
}

implementation = {
    "name": "probe-final-candidate-union-v1",
    "version": "1",
    "language": "Python",
    "script_sha256": script_artifact.sha256,
}

inputs = (
    input_artifact(baseline_manifest_path),
    input_artifact(start_rescue_manifest_path),
    input_artifact(end_rescue_manifest_path),
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
        stage="05A2",
        role=None,
        release_id=None,
        parameters=parameters,
        ontology=None,
        implementation=implementation,
        inputs=inputs,
        environment=runtime_environment(),
        allow_invalidated_destination=True,
    ) as staging,
):
    command = [
        python_executable,
        str(implementation_script),
        "--baseline-union",
        str(baseline),
        "--start-rescue",
        str(start_rescue),
        "--end-rescue",
        str(end_rescue),
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

    if summary.get("stage") != "05A2":
        raise ValueError("05A2 summary has invalid stage")

    count_names = (
        "baseline_candidate_union_row_count",
        "start_full_v2_rescue_row_count",
        "end_full_v2_rescue_row_count",
        "candidate_union_row_count",
    )

    counts = {}
    for name in count_names:
        value = summary.get(name)
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise ValueError(f"05A2 summary has invalid {name}")
        counts[name] = value

    expected_total = (
        counts["baseline_candidate_union_row_count"]
        + counts["start_full_v2_rescue_row_count"]
        + counts["end_full_v2_rescue_row_count"]
    )

    if counts["candidate_union_row_count"] != expected_total:
        raise RuntimeError("05A2 candidate-union arithmetic invariant violated")

    if summary.get("metadata_conflicts_detected") != 0:
        raise RuntimeError("05A2 reports metadata conflicts")

    if summary.get("baseline_overlap_with_strict_rescue") != 0:
        raise RuntimeError("05A2 reports overlap between baseline and strict rescue")

    rescue_targets = summary.get("strict_rescue_target_count_by_release")
    if not isinstance(rescue_targets, dict) or set(rescue_targets) != {
        start_release_id,
        end_release_id,
    }:
        raise ValueError("05A2 summary has invalid rescue release keys")

    final_count = counts["candidate_union_row_count"]

    outcome_counts = {
        "baseline_candidates": counts["baseline_candidate_union_row_count"],
        "start_rescue_candidates": counts["start_full_v2_rescue_row_count"],
        "end_rescue_candidates": counts["end_full_v2_rescue_row_count"],
        "final_candidates": final_count,
    }

    if final_count == 0:
        write_stage_outcome(
            staging / outcome_name,
            state="EMPTY_RESULT",
            reason_code="NO_FINAL_CANDIDATES",
            message=(
                "Final candidate union completed successfully but no "
                "candidate rows were available from baseline or rescue."
            ),
            counts=outcome_counts,
        )
    else:
        write_stage_outcome(
            staging / outcome_name,
            state="COMPLETED",
            reason_code="FINAL_CANDIDATE_UNION_EMITTED",
            message=(
                "Final candidate union completed successfully with "
                "baseline and strict-rescue provenance preserved."
            ),
            counts=outcome_counts,
        )

    log.write(f"baseline_candidates\t{outcome_counts['baseline_candidates']}\n")
    log.write(f"start_rescue_candidates\t{outcome_counts['start_rescue_candidates']}\n")
    log.write(f"end_rescue_candidates\t{outcome_counts['end_rescue_candidates']}\n")
    log.write(f"final_candidates\t{final_count}\n")

    produced = {
        path.relative_to(staging).as_posix()
        for path in staging.rglob("*")
        if path.is_file()
    }

    if produced != expected_outputs:
        raise RuntimeError(
            "05A2 output contract violation: "
            f"missing={sorted(expected_outputs - produced)} "
            f"unexpected={sorted(produced - expected_outputs)}"
        )
