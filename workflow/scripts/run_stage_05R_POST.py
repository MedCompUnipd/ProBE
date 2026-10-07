import json
import subprocess
import sys
from pathlib import Path

from probe.workflow.artifacts import atomic_stage_bundle, validate_bundle
from probe.workflow.outcome import write_stage_outcome
from probe.workflow.runtime import input_artifact, runtime_environment

context = globals()["snakemake"]

uncovered_manifest_path = Path(context.input.uncovered_manifest)
start_r0_manifest_path = Path(context.input.start_r0_manifest)
end_r0_manifest_path = Path(context.input.end_r0_manifest)
start_r1_manifest_path = Path(context.input.start_r1_manifest)
end_r1_manifest_path = Path(context.input.end_r1_manifest)
final_union_manifest_path = Path(context.input.final_union_manifest)
taxonomy_audit_manifest_path = Path(context.input.taxonomy_audit_manifest)
start_taxonomy_manifest_path = Path(context.input.start_taxonomy_manifest)
end_taxonomy_manifest_path = Path(context.input.end_taxonomy_manifest)
start_anchor_manifest_path = Path(context.input.start_anchor_manifest)
end_anchor_manifest_path = Path(context.input.end_anchor_manifest)
implementation_script = Path(context.input.implementation_script)

destination = Path(context.output[0]).parent


def load_manifest(path: Path):
    return validate_bundle(path.parent, deep=False)


uncovered_manifest = load_manifest(uncovered_manifest_path)
start_r0_manifest = load_manifest(start_r0_manifest_path)
end_r0_manifest = load_manifest(end_r0_manifest_path)
start_r1_manifest = load_manifest(start_r1_manifest_path)
end_r1_manifest = load_manifest(end_r1_manifest_path)
final_union_manifest = load_manifest(final_union_manifest_path)
taxonomy_audit_manifest = load_manifest(taxonomy_audit_manifest_path)
start_taxonomy_manifest = load_manifest(start_taxonomy_manifest_path)
end_taxonomy_manifest = load_manifest(end_taxonomy_manifest_path)
start_anchor_manifest = load_manifest(start_anchor_manifest_path)
end_anchor_manifest = load_manifest(end_anchor_manifest_path)

for label, manifest, stage in (
    ("05B", uncovered_manifest, "05B"),
    ("05A2", final_union_manifest, "05A2"),
    ("05C2", taxonomy_audit_manifest, "05C2"),
):
    if manifest.stage != stage:
        raise ValueError(
            f"05R_POST {label} expected stage {stage}, got {manifest.stage}"
        )
    if manifest.role is not None or manifest.release_id is not None:
        raise ValueError(f"05R_POST {label} must be a common bundle")

role_manifests = (
    ("START 05R0", start_r0_manifest, "05R0", "START"),
    ("END 05R0", end_r0_manifest, "05R0", "END"),
    ("START 05R1", start_r1_manifest, "05R1", "START"),
    ("END 05R1", end_r1_manifest, "05R1", "END"),
    ("START 02B", start_taxonomy_manifest, "02B", "START"),
    ("END 02B", end_taxonomy_manifest, "02B", "END"),
    ("START 04B", start_anchor_manifest, "04B", "START"),
    ("END 04B", end_anchor_manifest, "04B", "END"),
)

for label, manifest, stage, role in role_manifests:
    if manifest.stage != stage:
        raise ValueError(
            f"05R_POST {label} expected stage {stage}, got {manifest.stage}"
        )
    if manifest.role != role:
        raise ValueError(f"05R_POST {label} expected role {role}, got {manifest.role}")
    if manifest.release_id is None:
        raise ValueError(f"05R_POST {label} requires a release_id")

start_release_id = start_r0_manifest.release_id
end_release_id = end_r0_manifest.release_id

if start_release_id == end_release_id:
    raise ValueError("05R_POST START and END release IDs must be distinct")

for label, manifest, expected in (
    ("START 05R1", start_r1_manifest, start_release_id),
    ("START 02B", start_taxonomy_manifest, start_release_id),
    ("START 04B", start_anchor_manifest, start_release_id),
    ("END 05R1", end_r1_manifest, end_release_id),
    ("END 02B", end_taxonomy_manifest, end_release_id),
    ("END 04B", end_anchor_manifest, end_release_id),
):
    if manifest.release_id != expected:
        raise ValueError(
            f"05R_POST {label} release mismatch: "
            f"expected {expected}, got {manifest.release_id}"
        )

for label, manifest in (
    ("05B", uncovered_manifest),
    ("05A2", final_union_manifest),
    ("05C2", taxonomy_audit_manifest),
):
    parameters = dict(manifest.parameters)

    if parameters.get("start_release_id") != start_release_id:
        raise ValueError(f"05R_POST {label} START release mismatch")

    if parameters.get("end_release_id") != end_release_id:
        raise ValueError(f"05R_POST {label} END release mismatch")

files = {
    "uncovered": (
        uncovered_manifest,
        uncovered_manifest_path,
        "05B_uncovered_targets.tsv",
    ),
    "start_rescue_ids": (
        start_r0_manifest,
        start_r0_manifest_path,
        "rescue_ids.txt",
    ),
    "end_rescue_ids": (
        end_r0_manifest,
        end_r0_manifest_path,
        "rescue_ids.txt",
    ),
    "start_rescue_pairs": (
        start_r1_manifest,
        start_r1_manifest_path,
        "05R1_full_sequence_rescue.pairs.tsv",
    ),
    "end_rescue_pairs": (
        end_r1_manifest,
        end_r1_manifest_path,
        "05R1_full_sequence_rescue.pairs.tsv",
    ),
    "final_union": (
        final_union_manifest,
        final_union_manifest_path,
        "05A2_final_candidate_union.tsv",
    ),
    "taxonomy_audit": (
        taxonomy_audit_manifest,
        taxonomy_audit_manifest_path,
        "05C2_taxonomy_audit.tsv",
    ),
    "start_target_taxonomy": (
        start_taxonomy_manifest,
        start_taxonomy_manifest_path,
        "02B_target_taxonomy.tsv",
    ),
    "end_target_taxonomy": (
        end_taxonomy_manifest,
        end_taxonomy_manifest_path,
        "02B_target_taxonomy.tsv",
    ),
    "start_anchors": (
        start_anchor_manifest,
        start_anchor_manifest_path,
        "04B_classified_direct_anchors.tsv",
    ),
    "end_anchors": (
        end_anchor_manifest,
        end_anchor_manifest_path,
        "04B_classified_direct_anchors.tsv",
    ),
}

resolved = {}

for key, (manifest, manifest_path, filename) in files.items():
    declared = {item.path for item in manifest.outputs}
    if filename not in declared:
        raise ValueError(f"05R_POST upstream bundle does not declare {filename}")
    resolved[key] = manifest_path.parent / filename

output_name = "05R_POST_unresolved_targets.tsv"
summary_name = "05R_POST_summary.json"
outcome_name = "stage_outcome.json"

script_artifact = input_artifact(implementation_script)
python_executable = str(Path(sys.executable).resolve())

argument_pairs = [
    ("--uncovered", resolved["uncovered"]),
    ("--start-rescue-ids", resolved["start_rescue_ids"]),
    ("--end-rescue-ids", resolved["end_rescue_ids"]),
    ("--start-rescue-pairs", resolved["start_rescue_pairs"]),
    ("--end-rescue-pairs", resolved["end_rescue_pairs"]),
    ("--final-union", resolved["final_union"]),
    ("--taxonomy-audit", resolved["taxonomy_audit"]),
    ("--start-target-taxonomy", resolved["start_target_taxonomy"]),
    ("--end-target-taxonomy", resolved["end_target_taxonomy"]),
    ("--start-anchors", resolved["start_anchors"]),
    ("--end-anchors", resolved["end_anchors"]),
]

recorded_command = [
    python_executable,
    str(implementation_script.resolve()),
]

for argument, path in argument_pairs:
    recorded_command.extend((argument, str(path.resolve())))

recorded_command.extend(
    (
        "--start-release-id",
        start_release_id,
        "--end-release-id",
        end_release_id,
        "--output",
        f"<stage-bundle>/{output_name}",
        "--summary",
        f"<stage-bundle>/{summary_name}",
    )
)

parameters = {
    "start_release_id": start_release_id,
    "end_release_id": end_release_id,
    "command": recorded_command,
}

implementation = {
    "name": "probe-post-rescue-unresolved-report-v1",
    "version": "1",
    "language": "Python",
    "script_sha256": script_artifact.sha256,
}

inputs = tuple(
    input_artifact(path)
    for path in (
        uncovered_manifest_path,
        start_r0_manifest_path,
        end_r0_manifest_path,
        start_r1_manifest_path,
        end_r1_manifest_path,
        final_union_manifest_path,
        taxonomy_audit_manifest_path,
        start_taxonomy_manifest_path,
        end_taxonomy_manifest_path,
        start_anchor_manifest_path,
        end_anchor_manifest_path,
    )
) + (script_artifact,)

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
        stage="05R_POST",
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
    ]

    for argument, path in argument_pairs:
        command.extend((argument, str(path)))

    command.extend(
        (
            "--start-release-id",
            start_release_id,
            "--end-release-id",
            end_release_id,
            "--output",
            str(staging / output_name),
            "--summary",
            str(staging / summary_name),
        )
    )

    subprocess.run(
        command,
        stdout=log,
        stderr=subprocess.STDOUT,
        check=True,
    )

    summary = json.loads((staging / summary_name).read_text(encoding="utf-8"))

    if summary.get("stage") != "05R_POST":
        raise ValueError("05R_POST summary has invalid stage")

    releases = summary.get("releases")
    if releases != {
        "START": start_release_id,
        "END": end_release_id,
    }:
        raise RuntimeError("05R_POST summary release contract violation")

    count_names = (
        "baseline_uncovered_target_release_count",
        "post_rescue_unresolved_target_release_count",
        "post_rescue_unresolved_in_both_releases_count",
    )

    counts = {}
    for name in count_names:
        value = summary.get(name)
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise ValueError(f"05R_POST summary has invalid {name}")
        counts[name] = value

    unresolved = counts["post_rescue_unresolved_target_release_count"]

    if unresolved > counts["baseline_uncovered_target_release_count"]:
        raise RuntimeError("05R_POST unresolved count exceeds baseline uncovered count")

    expected_policy = {
        "report_only": True,
        "candidate_universe_modified_here": False,
        "taxonomy_audit_modifies_primary_status": False,
        "same_entity_decision_made_here": False,
        "strict_rescue_membership_taken_from_stage05r0": True,
        "final_primary_status_taken_from_stage05a2": True,
    }

    if summary.get("policy") != expected_policy:
        raise RuntimeError("05R_POST policy contract violation")

    outcome_counts = {
        "baseline_uncovered_target_release_pairs": counts[
            "baseline_uncovered_target_release_count"
        ],
        "post_rescue_unresolved_target_release_pairs": unresolved,
        "post_rescue_unresolved_in_both_releases": counts[
            "post_rescue_unresolved_in_both_releases_count"
        ],
    }

    if unresolved == 0:
        write_stage_outcome(
            staging / outcome_name,
            state="EMPTY_RESULT",
            reason_code="NO_POST_RESCUE_UNRESOLVED_TARGETS",
            message=(
                "Post-rescue reporting completed successfully and no "
                "target/release pair remained without a primary candidate."
            ),
            counts=outcome_counts,
        )
    else:
        write_stage_outcome(
            staging / outcome_name,
            state="COMPLETED",
            reason_code="POST_RESCUE_UNRESOLVED_TARGETS_REPORTED",
            message=(
                "Post-rescue reporting completed successfully with "
                "target/release pairs that remain without a primary candidate."
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
            "05R_POST output contract violation: "
            f"missing={sorted(expected_outputs - produced)} "
            f"unexpected={sorted(produced - expected_outputs)}"
        )
