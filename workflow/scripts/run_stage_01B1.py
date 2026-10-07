import json
import subprocess
from pathlib import Path

from probe.workflow.artifacts import atomic_stage_bundle, validate_bundle
from probe.workflow.outcome import write_stage_outcome
from probe.workflow.runtime import input_artifact, runtime_environment

context = globals()["snakemake"]

role = str(context.wildcards.role)

goa = Path(context.input.goa)
release_manifest_path = Path(context.input.release_manifest)
policy_manifest_path = Path(context.input.policy_manifest)
binary = Path(context.input.binary)

destination = Path(context.output[0]).parent

release_bundle = release_manifest_path.parent
policy_bundle = policy_manifest_path.parent

release_manifest = validate_bundle(release_bundle, deep=False)
policy_manifest = validate_bundle(policy_bundle, deep=False)

if release_manifest.stage != "01A_FULL":
    raise ValueError(
        f"01B1 expected upstream stage 01A_FULL, got {release_manifest.stage}"
    )

if release_manifest.role != role:
    raise ValueError(
        f"01B1 role mismatch: expected {role}, got {release_manifest.role}"
    )

if release_manifest.release_id is None:
    raise ValueError("01B1 requires an upstream release_id")

if policy_manifest.stage != "01B0":
    raise ValueError(f"01B1 expected upstream stage 01B0, got {policy_manifest.stage}")

if policy_manifest.role is not None:
    raise ValueError("01B0 must be a common ontology-policy bundle")

release_outputs = {item.path for item in release_manifest.outputs}
policy_outputs = {item.path for item in policy_manifest.outputs}

v2_name = "01A_full_v2.fasta"
policy_name = "01B0_go_policy.tsv"

if v2_name not in release_outputs:
    raise ValueError(f"01A_FULL bundle does not declare {v2_name}")

if policy_name not in policy_outputs:
    raise ValueError(f"01B0 bundle does not declare {policy_name}")

v2_fasta = release_bundle / v2_name
policy = policy_bundle / policy_name

goa_artifact = input_artifact(goa)
release_manifest_artifact = input_artifact(release_manifest_path)
policy_manifest_artifact = input_artifact(policy_manifest_path)
binary_artifact = input_artifact(binary)

progress_every = 0

recorded_command = [
    str(binary.resolve()),
    "--policy",
    str(policy.resolve()),
    "--goa",
    str(goa.resolve()),
    "--uniprot-v2",
    str(v2_fasta.resolve()),
    "--output-dir",
    "<stage-bundle>",
    "--progress-every",
    str(progress_every),
]

parameters = {
    "progress_every": progress_every,
    "command": recorded_command,
}

implementation = {
    "name": "probe-goa-normalize-v1",
    "version": "1",
    "language_standard": "C++20",
    "binary_sha256": binary_artifact.sha256,
    "build_task": "build-goa-normalize",
}

inputs = (
    goa_artifact,
    release_manifest_artifact,
    policy_manifest_artifact,
    binary_artifact,
)

expected_outputs = {
    "goa.normalized.gaf",
    "goa.positive_accessions.txt",
    "goa.only_not_accessions.txt",
    "goa.missing_accessions.txt",
    "goa.discard_reason_counts.tsv",
    "goa.stats.json",
    "stage_outcome.json",
}

log_path = Path(context.log[0])
log_path.parent.mkdir(parents=True, exist_ok=True)

with (
    log_path.open("w", encoding="utf-8") as log,
    atomic_stage_bundle(
        destination,
        stage="01B1",
        role=role,
        release_id=release_manifest.release_id,
        parameters=parameters,
        ontology=policy_manifest.ontology,
        implementation=implementation,
        inputs=inputs,
        environment=runtime_environment(),
        allow_invalidated_destination=True,
    ) as staging,
):
    command = [
        str(binary),
        "--policy",
        str(policy),
        "--goa",
        str(goa),
        "--uniprot-v2",
        str(v2_fasta),
        "--output-dir",
        str(staging),
        "--progress-every",
        str(progress_every),
    ]

    subprocess.run(
        command,
        stdout=log,
        stderr=subprocess.STDOUT,
        check=True,
    )

    stats_path = staging / "goa.stats.json"
    stats = json.loads(stats_path.read_text(encoding="utf-8"))

    positive_accession_count = stats.get("positive_accession_count")
    if (
        isinstance(positive_accession_count, bool)
        or not isinstance(positive_accession_count, int)
        or positive_accession_count < 0
    ):
        raise ValueError("01B1 summary has invalid positive_accession_count")

    outcome_counts = {
        "positive_accessions": positive_accession_count,
    }

    if positive_accession_count == 0:
        write_stage_outcome(
            staging / "stage_outcome.json",
            state="EMPTY_RESULT",
            reason_code="NO_POSITIVE_GOA_ACCESSIONS",
            message=(
                "GOA normalization completed successfully but produced "
                "no positive accessions for the GO-positive search universe."
            ),
            counts=outcome_counts,
        )
    else:
        write_stage_outcome(
            staging / "stage_outcome.json",
            state="COMPLETED",
            reason_code="POSITIVE_GOA_ACCESSIONS_RETAINED",
            message=(
                "GOA normalization completed successfully and retained "
                "positive accessions for the GO-positive search universe."
            ),
            counts=outcome_counts,
        )

    log.write(f"positive_accessions\\t{positive_accession_count}\\n")

    produced = {
        path.relative_to(staging).as_posix()
        for path in staging.rglob("*")
        if path.is_file()
    }

    if produced != expected_outputs:
        raise RuntimeError(
            "01B1 output contract violation: "
            f"missing={sorted(expected_outputs - produced)} "
            f"unexpected={sorted(produced - expected_outputs)}"
        )
