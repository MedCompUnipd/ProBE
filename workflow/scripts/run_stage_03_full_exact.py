"""Immutable FULL UniProt exact cross-strain discovery, without GO transfer."""

import csv
import subprocess
from pathlib import Path

from probe.stage2c_full_exact import (
    OUTPUT_BASENAME,
    read_plan,
    sha256,
    validate_scanner_summary,
)
from probe.workflow.artifacts import atomic_stage_bundle, validate_bundle
from probe.workflow.runtime import input_artifact, runtime_environment

context = globals()["snakemake"]
role = str(context.params.role)
if role not in {"START", "END"}:
    raise ValueError("Invalid FULL exact role")
plan = read_plan(Path(context.params.plan_path))
destination = Path(context.output[0]).parent
if destination != Path(plan["paths"][role]):
    raise ValueError("Wrong FULL exact publication destination")

files = {
    "targets": Path(context.input.target_manifest),
    "full": Path(context.input.full_manifest),
    "taxonomy": Path(context.input.taxonomy_manifest),
    "ox": Path(context.input.ox_manifest),
}
manifest = {}
expected_stages = {
    "targets": "01",
    "full": "01A_FULL",
    "taxonomy": "02B",
    "ox": "04D0b",
}
for label, path in files.items():
    pinned = plan["sources"][role][label]
    if str(path.resolve(strict=True)) != pinned["resolved"]:
        raise ValueError(f"FULL exact unexpected {label} manifest")
    if sha256(path) != pinned["sha256"]:
        raise ValueError(f"FULL exact changed {label} manifest")
    manifest[label] = validate_bundle(path.parent, deep=False)
    if manifest[label].stage != expected_stages[label]:
        raise ValueError(f"FULL exact wrong upstream stage: {label}")

release = plan["sources"][role]["release_id"]
for label in ("full", "taxonomy", "ox"):
    if manifest[label].release_id != release:
        raise ValueError(f"FULL exact upstream release mismatch: {label}")

binary = Path(context.input.binary)
if str(binary.resolve(strict=True)) != plan["binary"]:
    raise ValueError("Unexpected FULL exact scanner binary")
if sha256(binary) != plan["binary_sha256"]:
    raise ValueError("FULL exact scanner binary changed")

paths = {
    "targets": files["targets"].parent / "01_targets.tsv",
    "target_taxonomy": files["taxonomy"].parent / "02B_target_taxonomy.tsv",
    "ox_taxonomy": files["ox"].parent / "full_ox_taxonomy.tsv",
    "fasta": files["full"].parent / "01A_full_v2.fasta",
}
for path in paths.values():
    if not path.is_file():
        raise FileNotFoundError(path)

pairs_name = f"{OUTPUT_BASENAME}.pairs.tsv"
summary_name = f"{OUTPUT_BASENAME}.summary.tsv"
inputs = tuple(input_artifact(path) for path in files.values()) + (
    input_artifact(binary),
)
parameters = {
    **plan["parameters"],
    "release_id": release,
    "command": [
        str(binary),
        "--targets",
        str(paths["targets"]),
        "--target-taxonomy",
        str(paths["target_taxonomy"]),
        "--ox-taxonomy",
        str(paths["ox_taxonomy"]),
        "--fasta",
        str(paths["fasta"]),
        "--output",
        f"<stage-bundle>/{pairs_name}",
        "--summary",
        f"<stage-bundle>/{summary_name}",
        "--release-id",
        release,
        "--fragment-policy",
        plan["parameters"]["fragment"],
    ],
}
implementation = {
    "name": "probe-full-exact-cross-strain-v1",
    "version": "1",
    "language": "C++20",
    "binary_sha256": plan["binary_sha256"],
    "discovery_fingerprint": plan["key"],
    "build_task": "build-full-exact-cross-strain",
}
log_path = Path(context.log[0])
log_path.parent.mkdir(parents=True, exist_ok=True)
with (
    log_path.open("w", encoding="utf-8") as log,
    atomic_stage_bundle(
        destination,
        stage="03_FULL_EXACT",
        role=role,
        release_id=release,
        parameters=parameters,
        ontology=None,
        implementation=implementation,
        inputs=inputs,
        environment=runtime_environment(),
        allow_invalidated_destination=False,
    ) as staging,
):
    command = [
        str(binary),
        "--targets",
        str(paths["targets"]),
        "--target-taxonomy",
        str(paths["target_taxonomy"]),
        "--ox-taxonomy",
        str(paths["ox_taxonomy"]),
        "--fasta",
        str(paths["fasta"]),
        "--output",
        str(staging / pairs_name),
        "--summary",
        str(staging / summary_name),
        "--release-id",
        release,
        "--fragment-policy",
        plan["parameters"]["fragment"],
    ]
    subprocess.run(command, stdout=log, stderr=subprocess.STDOUT, check=True)
    with (staging / summary_name).open(encoding="utf-8") as stream:
        reader = csv.reader(stream, delimiter="\t")
        if next(reader, None) != ["metric", "value"]:
            raise ValueError("Invalid FULL exact scanner summary header")
        metrics = dict(reader)
    validate_scanner_summary(metrics, release=release)
    if (
        metrics.get("fragment_policy") != plan["parameters"]["fragment"]
        or metrics.get("fragments_skipped") != "0"
    ):
        raise ValueError("FULL exact scanner fragment policy mismatch")
    expected = {pairs_name, summary_name}
    actual = {
        file.relative_to(staging).as_posix()
        for file in staging.rglob("*")
        if file.is_file()
    }
    if actual != expected:
        raise ValueError("FULL exact scanner produced unexpected files")
