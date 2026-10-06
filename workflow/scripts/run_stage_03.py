import subprocess
from pathlib import Path

from probe.workflow.artifacts import atomic_stage_bundle, validate_bundle
from probe.workflow.runtime import input_artifact, runtime_environment

context = globals()["snakemake"]

role = str(context.wildcards.role)
policy = dict(context.params.candidate_policy)

target_manifest_path = Path(context.input.target_manifest)
go_positive_manifest_path = Path(context.input.go_positive_manifest)
taxonomy_manifest_path = Path(context.input.taxonomy_manifest)
binary = Path(context.input.binary)

destination = Path(context.output[0]).parent

target_bundle = target_manifest_path.parent
go_positive_bundle = go_positive_manifest_path.parent
taxonomy_bundle = taxonomy_manifest_path.parent

target_manifest = validate_bundle(target_bundle, deep=False)
go_positive_manifest = validate_bundle(go_positive_bundle, deep=False)
taxonomy_manifest = validate_bundle(taxonomy_bundle, deep=False)

if target_manifest.stage != "01":
    raise ValueError(
        f"03 expected upstream stage 01, got {target_manifest.stage}"
    )

if target_manifest.role is not None:
    raise ValueError(
        f"03 expected common stage 01, got role {target_manifest.role}"
    )

if go_positive_manifest.stage != "01C":
    raise ValueError(
        "03 expected upstream stage 01C, "
        f"got {go_positive_manifest.stage}"
    )

if taxonomy_manifest.stage != "02B":
    raise ValueError(
        "03 expected upstream stage 02B, "
        f"got {taxonomy_manifest.stage}"
    )

if go_positive_manifest.role != role:
    raise ValueError(
        "03 GO-positive role mismatch: "
        f"expected {role}, got {go_positive_manifest.role}"
    )

if taxonomy_manifest.role != role:
    raise ValueError(
        "03 taxonomy role mismatch: "
        f"expected {role}, got {taxonomy_manifest.role}"
    )

release_id = go_positive_manifest.release_id
if release_id is None:
    raise ValueError("03 requires an upstream release_id")

if taxonomy_manifest.release_id != release_id:
    raise ValueError(
        "03 upstream release mismatch: "
        f"01C={release_id} 02B={taxonomy_manifest.release_id}"
    )

if go_positive_manifest.ontology != taxonomy_manifest.ontology:
    raise ValueError(
        "03 upstream ontology provenance mismatch between 01C and 02B"
    )

target_outputs = {item.path for item in target_manifest.outputs}
go_positive_outputs = {
    item.path for item in go_positive_manifest.outputs
}
taxonomy_outputs = {
    item.path for item in taxonomy_manifest.outputs
}

target_name = "01_targets.tsv"
fasta_name = "01C_go_positive_v2.fasta"
target_taxonomy_name = "02B_target_taxonomy.tsv"
ox_taxonomy_name = "02B_ox_taxonomy.tsv"

if target_name not in target_outputs:
    raise ValueError(
        f"01 bundle does not declare {target_name}"
    )

if fasta_name not in go_positive_outputs:
    raise ValueError(
        f"01C bundle does not declare {fasta_name}"
    )

for name in (target_taxonomy_name, ox_taxonomy_name):
    if name not in taxonomy_outputs:
        raise ValueError(
            f"02B bundle does not declare {name}"
        )

targets = target_bundle / target_name
fasta = go_positive_bundle / fasta_name
target_taxonomy = taxonomy_bundle / target_taxonomy_name
ox_taxonomy = taxonomy_bundle / ox_taxonomy_name

output_prefix_name = "03_primary_candidates"
pairs_name = f"{output_prefix_name}.pairs.tsv"
unsearchable_name = (
    f"{output_prefix_name}.unsearchable_targets.tsv"
)
summary_name = f"{output_prefix_name}.summary.tsv"

binary_artifact = input_artifact(binary)

snapshot = release_id

recorded_command = [
    str(binary.resolve()),
    "--targets",
    str(targets.resolve()),
    "--target-taxonomy",
    str(target_taxonomy.resolve()),
    "--ox-taxonomy",
    str(ox_taxonomy.resolve()),
    "--fasta",
    str(fasta.resolve()),
    "--snapshot",
    snapshot,
    "--out-prefix",
    f"<stage-bundle>/{output_prefix_name}",
    "--k",
    str(policy["k"]),
    "--max-required-shared",
    str(policy["max_required_shared"]),
    "--min-target-kmer-fraction",
    str(policy["min_target_kmer_fraction"]),
    "--fragment-policy",
    str(policy["fragment_policy"]),
    "--taxonomy-policy",
    str(policy["taxonomy_policy"]),
]

parameters = {
    "k": policy["k"],
    "max_required_shared": policy["max_required_shared"],
    "min_target_kmer_fraction": policy[
        "min_target_kmer_fraction"
    ],
    "fragment_policy": policy["fragment_policy"],
    "taxonomy_policy": policy["taxonomy_policy"],
    "snapshot": snapshot,
    "command": recorded_command,
}

implementation = {
    "name": "probe-primary-candidate-scan-v1",
    "version": "1",
    "language": "C++20",
    "binary_sha256": binary_artifact.sha256,
    "build_task": "build-primary-candidate-scan",
}

inputs = (
    input_artifact(target_manifest_path),
    input_artifact(go_positive_manifest_path),
    input_artifact(taxonomy_manifest_path),
    binary_artifact,
)

expected_outputs = {
    pairs_name,
    unsearchable_name,
    summary_name,
}

log_path = Path(context.log[0])
log_path.parent.mkdir(parents=True, exist_ok=True)

with log_path.open(
    "w",
    encoding="utf-8",
) as log, atomic_stage_bundle(
    destination,
    stage="03",
    role=role,
    release_id=release_id,
    parameters=parameters,
    ontology=go_positive_manifest.ontology,
    implementation=implementation,
    inputs=inputs,
    environment=runtime_environment(),
    allow_invalidated_destination=True,
) as staging:
    command = [
        str(binary),
        "--targets",
        str(targets),
        "--target-taxonomy",
        str(target_taxonomy),
        "--ox-taxonomy",
        str(ox_taxonomy),
        "--fasta",
        str(fasta),
        "--snapshot",
        snapshot,
        "--out-prefix",
        str(staging / output_prefix_name),
        "--k",
        str(policy["k"]),
        "--max-required-shared",
        str(policy["max_required_shared"]),
        "--min-target-kmer-fraction",
        str(policy["min_target_kmer_fraction"]),
        "--fragment-policy",
        str(policy["fragment_policy"]),
        "--taxonomy-policy",
        str(policy["taxonomy_policy"]),
    ]

    subprocess.run(
        command,
        stdout=log,
        stderr=subprocess.STDOUT,
        check=True,
    )

    produced = {
        path.relative_to(staging).as_posix()
        for path in staging.rglob("*")
        if path.is_file()
    }

    if produced != expected_outputs:
        raise RuntimeError(
            "03 output contract violation: "
            f"missing={sorted(expected_outputs - produced)} "
            f"unexpected={sorted(produced - expected_outputs)}"
        )
