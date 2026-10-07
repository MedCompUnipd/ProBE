import json
import subprocess
import sys
from pathlib import Path

from probe.workflow.artifacts import atomic_stage_bundle, validate_bundle
from probe.workflow.outcome import write_stage_outcome
from probe.workflow.runtime import input_artifact, runtime_environment

context = globals()["snakemake"]

target_manifest_path = Path(context.input.target_manifest)
final_union_manifest_path = Path(context.input.final_union_manifest)
taxonomy_audit_manifest_path = Path(context.input.taxonomy_audit_manifest)
start_store_manifest_path = Path(context.input.start_store_manifest)
end_store_manifest_path = Path(context.input.end_store_manifest)
implementation_script = Path(context.input.implementation_script)

destination = Path(context.output[0]).parent


def load_manifest(path: Path):
    return validate_bundle(path.parent, deep=False)


def require_common(manifest, stage: str) -> None:
    if manifest.stage != stage:
        raise ValueError(f"06A expected upstream stage {stage}, got {manifest.stage}")

    if manifest.role is not None or manifest.release_id is not None:
        raise ValueError(f"06A Stage {stage} input must be a common bundle")


def require_release(manifest, stage: str, role: str) -> None:
    if manifest.stage != stage:
        raise ValueError(f"06A {role} expected stage {stage}, got {manifest.stage}")

    if manifest.role != role:
        raise ValueError(f"06A {stage} expected role {role}, got {manifest.role}")

    if manifest.release_id is None:
        raise ValueError(f"06A {role} Stage {stage} requires release_id")


target_manifest = load_manifest(target_manifest_path)
final_union_manifest = load_manifest(final_union_manifest_path)
taxonomy_audit_manifest = load_manifest(taxonomy_audit_manifest_path)
start_store_manifest = load_manifest(start_store_manifest_path)
end_store_manifest = load_manifest(end_store_manifest_path)

require_common(target_manifest, "01")
require_common(final_union_manifest, "05A2")
require_common(taxonomy_audit_manifest, "05C2")

require_release(start_store_manifest, "05D", "START")
require_release(end_store_manifest, "05D", "END")

start_release_id = start_store_manifest.release_id
end_release_id = end_store_manifest.release_id

if start_release_id == end_release_id:
    raise ValueError("06A START and END release IDs must be distinct")

for label, manifest in (
    ("05A2", final_union_manifest),
    ("05C2", taxonomy_audit_manifest),
):
    parameters = dict(manifest.parameters)

    if parameters.get("start_release_id") != start_release_id:
        raise ValueError(f"06A {label} START release mismatch")

    if parameters.get("end_release_id") != end_release_id:
        raise ValueError(f"06A {label} END release mismatch")

if start_store_manifest.parameters.get("release_id") != start_release_id:
    raise ValueError("06A START 05D parameter release mismatch")

if end_store_manifest.parameters.get("release_id") != end_release_id:
    raise ValueError("06A END 05D parameter release mismatch")

files = (
    (
        "01",
        target_manifest,
        target_manifest_path,
        "01_targets.tsv",
    ),
    (
        "05A2",
        final_union_manifest,
        final_union_manifest_path,
        "05A2_final_candidate_union.tsv",
    ),
    (
        "05C2",
        taxonomy_audit_manifest,
        taxonomy_audit_manifest_path,
        "05C2_taxonomy_audit.tsv",
    ),
    (
        "START 05D",
        start_store_manifest,
        start_store_manifest_path,
        "05D_candidate_sequences.tsv",
    ),
    (
        "END 05D",
        end_store_manifest,
        end_store_manifest_path,
        "05D_candidate_sequences.tsv",
    ),
)

resolved = {}

for label, manifest, manifest_path, filename in files:
    declared = {artifact.path for artifact in manifest.outputs}

    if filename not in declared:
        raise ValueError(f"06A {label} bundle does not declare {filename}")

    resolved[filename] = manifest_path.parent / filename

targets = resolved["01_targets.tsv"]
candidate_union = resolved["05A2_final_candidate_union.tsv"]
taxonomy_audit = resolved["05C2_taxonomy_audit.tsv"]

start_store = start_store_manifest_path.parent / "05D_candidate_sequences.tsv"

end_store = end_store_manifest_path.parent / "05D_candidate_sequences.tsv"

output_name = "06A_alignment_jobs.tsv"
summary_name = "06A_summary.json"
outcome_name = "stage_outcome.json"

script_artifact = input_artifact(implementation_script)
python_executable = str(Path(sys.executable).resolve())

recorded_command = [
    python_executable,
    str(implementation_script.resolve()),
    "--targets",
    str(targets.resolve()),
    "--candidate-union",
    str(candidate_union.resolve()),
    "--taxonomy-audit",
    str(taxonomy_audit.resolve()),
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
]

parameters = {
    "start_release_id": start_release_id,
    "end_release_id": end_release_id,
    "command": recorded_command,
}

implementation = {
    "name": "probe-alignment-job-preparation-v1",
    "version": "1",
    "language": "Python",
    "script_sha256": script_artifact.sha256,
}

inputs = (
    input_artifact(target_manifest_path),
    input_artifact(final_union_manifest_path),
    input_artifact(taxonomy_audit_manifest_path),
    input_artifact(start_store_manifest_path),
    input_artifact(end_store_manifest_path),
    script_artifact,
)

expected_outputs = {
    output_name,
    summary_name,
    outcome_name,
}

expected_policy = {
    "same_entity_decision_made_here": False,
    "alignment_made_here": False,
    "exact_string_comparison_only": True,
    "exact_pairs_may_bypass_dynamic_programming_later": True,
    "taxonomy_audit_kept_separate_from_primary": True,
    "primary_taxonomy_audit_pair_overlap_emits_primary_only": True,
}

log_path = Path(context.log[0])
log_path.parent.mkdir(parents=True, exist_ok=True)

with (
    log_path.open("w", encoding="utf-8") as log,
    atomic_stage_bundle(
        destination,
        stage="06A",
        role=None,
        release_id=None,
        parameters=parameters,
        ontology=final_union_manifest.ontology,
        implementation=implementation,
        inputs=inputs,
        environment=runtime_environment(),
        allow_invalidated_destination=True,
    ) as staging,
):
    command = [
        python_executable,
        str(implementation_script),
        "--targets",
        str(targets),
        "--candidate-union",
        str(candidate_union),
        "--taxonomy-audit",
        str(taxonomy_audit),
        "--start-sequence-store",
        str(start_store),
        "--end-sequence-store",
        str(end_store),
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

    if summary.get("stage") != "06A":
        raise ValueError("06A summary has invalid stage")

    if summary.get("releases") != {
        "START": start_release_id,
        "END": end_release_id,
    }:
        raise RuntimeError("06A summary release contract violation")

    if summary.get("policy") != expected_policy:
        raise RuntimeError("06A policy contract violation")

    releases = (
        start_release_id,
        end_release_id,
    )

    def release_counts(name):
        value = summary.get(name)

        if not isinstance(value, dict):
            raise ValueError(f"06A summary has invalid {name}")

        if set(value) != set(releases):
            raise ValueError(f"06A summary {name} release keys mismatch")

        result = {}

        for release in releases:
            count = value[release]

            if isinstance(count, bool) or not isinstance(count, int) or count < 0:
                raise ValueError(f"06A summary has invalid {name}[{release!r}]")

            result[release] = count

        return result

    primary = release_counts("primary_job_counts")

    audit_allowed = release_counts("taxonomy_audit_alignment_allowed_row_counts")

    audit_overlap = release_counts("taxonomy_audit_primary_overlap_counts")

    audit_jobs = release_counts("taxonomy_audit_job_counts")

    for release in releases:
        if audit_allowed[release] != audit_overlap[release] + audit_jobs[release]:
            raise RuntimeError(
                f"06A {release} taxonomy-audit summary arithmetic violation"
            )

    alignment_job_count = summary.get("alignment_job_count")

    if (
        isinstance(alignment_job_count, bool)
        or not isinstance(alignment_job_count, int)
        or alignment_job_count < 0
    ):
        raise ValueError("06A summary has invalid alignment_job_count")

    expected_alignment_jobs = sum(primary.values()) + sum(audit_jobs.values())

    if alignment_job_count != expected_alignment_jobs:
        raise RuntimeError(
            "06A alignment-job arithmetic violation: "
            f"summary={alignment_job_count} "
            f"expected={expected_alignment_jobs}"
        )

    exact_maps = summary.get("release_exact_counts")

    if not isinstance(exact_maps, dict):
        raise ValueError("06A summary has invalid release_exact_counts")

    exact_jobs = 0
    nonexact_jobs = 0

    for release in releases:
        counts = exact_maps.get(release)

        if not isinstance(counts, dict):
            raise ValueError(f"06A missing exact counts for {release}")

        exact = counts.get("EXACT", 0)
        nonexact = counts.get("NONEXACT", 0)

        for name, value in (
            ("EXACT", exact),
            ("NONEXACT", nonexact),
        ):
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError(f"06A invalid {name} count for {release}")

        exact_jobs += exact
        nonexact_jobs += nonexact

    if exact_jobs + nonexact_jobs != alignment_job_count:
        raise RuntimeError("06A exact/nonexact arithmetic violation")

    outcome_counts = {
        "primary_jobs": sum(primary.values()),
        "taxonomy_audit_jobs": sum(audit_jobs.values()),
        "taxonomy_audit_primary_overlaps": sum(audit_overlap.values()),
        "alignment_jobs": alignment_job_count,
        "exact_jobs": exact_jobs,
        "nonexact_jobs": nonexact_jobs,
    }

    if alignment_job_count == 0:
        write_stage_outcome(
            staging / outcome_name,
            state="EMPTY_RESULT",
            reason_code="NO_ALIGNMENT_JOBS",
            message=(
                "Stage 06A completed successfully but the final "
                "primary and alignment-permitted taxonomy-audit "
                "universes produced no alignment jobs."
            ),
            counts=outcome_counts,
        )
    else:
        write_stage_outcome(
            staging / outcome_name,
            state="COMPLETED",
            reason_code="ALIGNMENT_JOBS_PREPARED",
            message=(
                "Stage 06A prepared the canonical unique alignment "
                "workload without performing alignment or making "
                "SAME_ENTITY decisions."
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
            "06A output contract violation: "
            f"missing={sorted(expected_outputs - produced)} "
            f"unexpected={sorted(produced - expected_outputs)}"
        )
