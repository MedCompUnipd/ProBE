"""Snakemake Stage 08 variant; explicit full GOA-scan authorization required."""

from pathlib import Path

from probe.stage07_variants import read_spec
from probe.stage08_collection_publish import publish_raw_annotation_collection
from probe.workflow.artifacts import validate_bundle

context = globals()["snakemake"]
spec = read_spec(Path(context.params.spec_path))
exp = Path(spec["run"]) / "experiments" / spec["experiment_id"]
marker = exp / "FULL_SCAN_AUTHORIZED"
if marker.is_symlink() or not marker.is_file():
    raise RuntimeError("Stage 08 variant requires explicit GOA scan authorization")
if marker.read_text(encoding="utf-8") != spec["experiment_id"] + "\n":
    raise RuntimeError("GOA scan authorization mismatch")

destination = Path(spec["paths"]["stage08"])
if Path(context.output[0]) != destination / "manifest.json":
    raise ValueError("Unexpected Stage 08 variant output location")
if destination.is_symlink() or (destination.exists() and not destination.is_dir()):
    raise FileExistsError("Unsafe Stage 08 variant destination")
if destination.exists() and any(destination.iterdir()):
    raise FileExistsError("Stage 08 variant refuses nonempty destination")

work = Path(spec["run"]) / "work/pipeline-v2"
expected = {
    "stage07_manifest": (Path(spec["paths"]["stage07"]) / "manifest.json"),
    "start_goa_manifest": work / "START/01B1/manifest.json",
    "end_goa_manifest": work / "END/01B1/manifest.json",
    "go_policy_manifest": work / "common/01B0/manifest.json",
}
for name, path in expected.items():
    actual = Path(getattr(context.input, name))
    if actual != path:
        raise ValueError(f"Stage 08 variant input path mismatch: {name}")

upstream = validate_bundle(Path(spec["paths"]["stage07"]), deep=False)
if upstream.parameters.get("policies") != spec["policy"]:
    raise ValueError("Stage 07 variant policy mismatch")
if destination.is_dir():
    destination.rmdir()  # Succeeds only for an empty Snakemake placeholder.
manifest = publish_raw_annotation_collection(
    stage07_bundle=Path(spec["paths"]["stage07"]),
    start_goa_bundle=work / "START/01B1",
    end_goa_bundle=work / "END/01B1",
    go_policy_bundle=work / "common/01B0",
    destination=destination,
    allow_invalidated_destination=False,
    implementation_sources={
        "scripts/pipeline/08_collect_annotations.py": (
            Path(spec["repo"]) / "scripts/pipeline/08_collect_annotations.py"
        ),
    },
)
if manifest.stage != "08" or manifest.status != "complete":
    raise ValueError("Variant M8 did not publish a certified bundle")
