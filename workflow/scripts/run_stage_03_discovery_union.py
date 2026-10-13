"""Publish a policy-independent Stage 2C candidate union atomically."""

from pathlib import Path

from probe.stage2c_candidate_union import merge_discovery
from probe.stage2c_union_plan import PAIRS, SUMMARY, read_plan
from probe.workflow.artifacts import (
    StageManifest,
    atomic_stage_bundle,
    sha256_file,
    validate_bundle,
)
from probe.workflow.runtime import input_artifact, runtime_environment

context = globals()["snakemake"]
role = str(context.params.role)
if role not in {"START", "END"}:
    raise ValueError("Unknown discovery union role")
plan = read_plan(Path(context.params.plan_path))
destination = Path(context.output.union_manifest).parent
if destination != Path(plan["paths"][role]):
    raise ValueError("Unexpected union publication destination")

manifests = {
    "go": Path(context.input.go_manifest),
    "full": Path(context.input.full_manifest),
    "taxonomy": Path(context.input.taxonomy_manifest),
}
expected_dirs = {
    "go": Path(plan["species_paths"][role]),
    "full": Path(plan["full_paths"][role]),
}
for label in ("go", "full"):
    if manifests[label].parent != expected_dirs[label]:
        raise ValueError(f"Wrong Stage 2C scan input: {label}")
if (
    str(manifests["taxonomy"].resolve(strict=True))
    != (plan["taxonomy"][role]["resolved"])
):
    raise ValueError("Unexpected Stage 2C taxonomy input")

upstream = {
    label: validate_bundle(path.parent, deep=False)
    for label, path in manifests.items()
    if label != "taxonomy"
}
taxonomy = StageManifest.read(manifests["taxonomy"])
if sha256_file(manifests["taxonomy"]) != (plan["taxonomy"][role]["sha256"]):
    raise ValueError("Pinned Stage 2C taxonomy manifest changed")
if (
    upstream["go"].stage != "03"
    or upstream["full"].stage != "03_FULL_EXACT"
    or taxonomy.stage != "02B"
):
    raise ValueError("Wrong Stage 2C upstream stages")
for label, key in (("go", "species_key"), ("full", "full_key")):
    if upstream[label].implementation.get("discovery_fingerprint") != (plan[key]):
        raise ValueError(f"Upstream discovery fingerprint mismatch: {label}")
release = plan["releases"][role]
for label, manifest in (*upstream.items(), ("taxonomy", taxonomy)):
    if manifest.role != role or manifest.release_id != release:
        raise ValueError(f"Wrong Stage 2C release/role: {label}")

required = {
    "go": "03_primary_candidates.pairs.tsv",
    "full": "03_full_exact_cross_strain.pairs.tsv",
    "taxonomy": "02B_target_taxonomy.tsv",
}
for label, name in required.items():
    manifest = taxonomy if label == "taxonomy" else upstream[label]
    if name not in {item.path for item in manifest.outputs}:
        raise ValueError(f"Missing declared Stage 2C output: {label}")

inputs = tuple(input_artifact(path) for path in manifests.values())
implementation = {
    "name": "probe-stage2c-discovery-union",
    "version": "1",
    "language": "Python",
    "discovery_fingerprint": plan["key"],
    "species_fingerprint": plan["species_key"],
    "full_fingerprint": plan["full_key"],
}
parameters = {
    "scientific_policy": "species-wide-independent-of-donor-policy",
    "release": release,
    "go_positive_scan": plan["species_key"],
    "full_exact_scan": plan["full_key"],
}
log_path = Path(context.log[0])
log_path.parent.mkdir(parents=True, exist_ok=True)
with (
    log_path.open("w", encoding="utf-8") as log,
    atomic_stage_bundle(
        destination,
        stage="03_UNION",
        role=role,
        release_id=release,
        parameters=parameters,
        ontology=upstream["go"].ontology,
        implementation=implementation,
        inputs=inputs,
        environment=runtime_environment(),
        allow_invalidated_destination=False,
    ) as staging,
):
    report = merge_discovery(
        release=release,
        target_taxonomy=manifests["taxonomy"].parent / required["taxonomy"],
        go_positive_pairs=manifests["go"].parent / required["go"],
        full_exact_pairs=manifests["full"].parent / required["full"],
        output=staging / PAIRS,
        summary=staging / SUMMARY,
    )
    log.write(str(report) + "\n")
