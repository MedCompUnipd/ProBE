import json
import subprocess
import sys
from pathlib import Path

from probe.workflow.artifacts import atomic_stage_bundle, validate_bundle
from probe.workflow.outcome import write_stage_outcome
from probe.workflow.runtime import input_artifact, runtime_environment

context = globals()["snakemake"]

final_union_manifest_path = Path(context.input.final_union_manifest)
start_anchor_manifest_path = Path(context.input.start_anchor_manifest)
end_anchor_manifest_path = Path(context.input.end_anchor_manifest)
implementation_script = Path(context.input.implementation_script)

destination = Path(context.output[0]).parent


def load_manifest(path: Path):
    return validate_bundle(path.parent, deep=False)


final_union_manifest = load_manifest(final_union_manifest_path)
start_anchor_manifest = load_manifest(start_anchor_manifest_path)
end_anchor_manifest = load_manifest(end_anchor_manifest_path)

if final_union_manifest.stage != "05A2":
    raise ValueError(
        f"05C2 final union expected stage 05A2, got {final_union_manifest.stage}"
    )

if final_union_manifest.role is not None or final_union_manifest.release_id is not None:
    raise ValueError("05C2 Stage 05A2 input must be a common bundle")

for label, manifest, role in (
    ("START anchor", start_anchor_manifest, "START"),
    ("END anchor", end_anchor_manifest, "END"),
):
    if manifest.stage != "04B":
        raise ValueError(f"05C2 {label} expected stage 04B, got {manifest.stage}")
    if manifest.role != role:
        raise ValueError(f"05C2 {label} expected role {role}, got {manifest.role}")
    if manifest.release_id is None:
        raise ValueError(f"05C2 {label} requires a release_id")

start_release_id = start_anchor_manifest.release_id
end_release_id = end_anchor_manifest.release_id

if start_release_id == end_release_id:
    raise ValueError("05C2 START and END release IDs must be distinct")

union_parameters = dict(final_union_manifest.parameters)

if union_parameters.get("start_release_id") != start_release_id:
    raise ValueError(
        "05C2 START release mismatch: "
        f"05A2={union_parameters.get('start_release_id')} "
        f"04B={start_release_id}"
    )

if union_parameters.get("end_release_id") != end_release_id:
    raise ValueError(
        "05C2 END release mismatch: "
        f"05A2={union_parameters.get('end_release_id')} "
        f"04B={end_release_id}"
    )

union_name = "05A2_final_candidate_union.tsv"
anchors_name = "04B_classified_direct_anchors.tsv"

for label, manifest, filename in (
    ("05A2", final_union_manifest, union_name),
    ("START 04B", start_anchor_manifest, anchors_name),
    ("END 04B", end_anchor_manifest, anchors_name),
):
    declared = {item.path for item in manifest.outputs}
    if filename not in declared:
        raise ValueError(f"{label} bundle does not declare {filename}")

candidate_union = final_union_manifest_path.parent / union_name
start_anchors = start_anchor_manifest_path.parent / anchors_name
end_anchors = end_anchor_manifest_path.parent / anchors_name

output_name = "05C2_taxonomy_audit.tsv"
summary_name = "05C2_summary.json"
outcome_name = "stage_outcome.json"

script_artifact = input_artifact(implementation_script)
python_executable = str(Path(sys.executable).resolve())

recorded_command = [
    python_executable,
    str(implementation_script.resolve()),
    "--candidate-union",
    str(candidate_union.resolve()),
    "--start-anchors",
    str(start_anchors.resolve()),
    "--end-anchors",
    str(end_anchors.resolve()),
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
    "name": "probe-final-taxonomy-audit-v1",
    "version": "1",
    "language": "Python",
    "script_sha256": script_artifact.sha256,
}

inputs = (
    input_artifact(final_union_manifest_path),
    input_artifact(start_anchor_manifest_path),
    input_artifact(end_anchor_manifest_path),
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
        stage="05C2",
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
        "--candidate-union",
        str(candidate_union),
        "--start-anchors",
        str(start_anchors),
        "--end-anchors",
        str(end_anchors),
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

    if summary.get("stage") != "05C2":
        raise ValueError("05C2 summary has invalid stage")

    count_names = (
        "ineligible_direct_anchor_row_count",
        "audit_alignment_allowed_row_count",
        "cross_species_audit_row_count",
        "alignment_allowed_target_count",
        "alignment_allowed_rows_for_targets_without_primary_candidate",
        "rows_already_in_primary_union_for_same_target",
    )

    counts = {}
    for name in count_names:
        value = summary.get(name)
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise ValueError(f"05C2 summary has invalid {name}")
        counts[name] = value

    audit_rows = counts["ineligible_direct_anchor_row_count"]
    alignable_rows = counts["audit_alignment_allowed_row_count"]

    if alignable_rows > audit_rows:
        raise RuntimeError("05C2 alignment-allowed row count exceeds audit row count")

    if counts["cross_species_audit_row_count"] > audit_rows:
        raise RuntimeError("05C2 cross-species row count exceeds audit row count")

    if counts["alignment_allowed_target_count"] > alignable_rows:
        raise RuntimeError("05C2 alignment-allowed target count exceeds row count")

    if (
        counts["alignment_allowed_rows_for_targets_without_primary_candidate"]
        > alignable_rows
    ):
        raise RuntimeError("05C2 rows without primary candidates exceed alignable rows")

    policy = summary.get("policy")
    expected_policy = {
        "primary_candidate_universe_modified_here": False,
        "same_species_wrong_subtaxon_alignment_audit_allowed": True,
        "same_species_wrong_subtaxon_becomes_primary_candidate": False,
        "cross_species_sequence_test_allowed": False,
        "same_entity_decision_made_here": False,
    }

    if policy != expected_policy:
        raise RuntimeError("05C2 taxonomy-audit policy contract violation")

    outcome_counts = {
        "audit_rows": audit_rows,
        "alignment_allowed_rows": alignable_rows,
        "cross_species_audit_rows": counts["cross_species_audit_row_count"],
    }

    if audit_rows == 0:
        write_stage_outcome(
            staging / outcome_name,
            state="EMPTY_RESULT",
            reason_code="NO_TAXONOMY_AUDIT_ROWS",
            message=(
                "Final taxonomy audit completed successfully but no "
                "ineligible direct-anchor rows required audit."
            ),
            counts=outcome_counts,
        )
    else:
        write_stage_outcome(
            staging / outcome_name,
            state="COMPLETED",
            reason_code="TAXONOMY_AUDIT_ROWS_EMITTED",
            message=(
                "Final taxonomy audit completed successfully with "
                "audit-only direct-anchor rows preserved separately "
                "from the primary candidate universe."
            ),
            counts=outcome_counts,
        )

    log.write(f"audit_rows\t{audit_rows}\n")
    log.write(f"alignment_allowed_rows\t{alignable_rows}\n")
    log.write(f"cross_species_audit_rows\t{counts['cross_species_audit_row_count']}\n")

    produced = {
        path.relative_to(staging).as_posix()
        for path in staging.rglob("*")
        if path.is_file()
    }

    if produced != expected_outputs:
        raise RuntimeError(
            "05C2 output contract violation: "
            f"missing={sorted(expected_outputs - produced)} "
            f"unexpected={sorted(produced - expected_outputs)}"
        )
