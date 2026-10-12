"""Snakemake-only Stage 08 runner with explicit full-scan authorization.

The runner consumes immutable Stage 07 and Stage 01B1/01B0 bundles. It never
reruns upstream stages or publishes when the scan gate is disabled.

Do not add a future import: Snakemake may prepend Python code to this script.
"""

from pathlib import Path

from probe.stage08_collection_publish import publish_raw_annotation_collection

context = globals()["snakemake"]

_OUTPUT_NAMES = frozenset(
    {
        "08_start_source_assertions.tsv",
        "08_start_annotation_links.tsv",
        "08_start_donor_coverage.tsv",
        "08_start_evidence_inventory.tsv",
        "08_start_scan_summary.json",
        "08_end_source_assertions.tsv",
        "08_end_annotation_links.tsv",
        "08_end_donor_coverage.tsv",
        "08_end_evidence_inventory.tsv",
        "08_end_scan_summary.json",
        "08_collection_summary.json",
        "stage_outcome.json",
    }
)


def _required_bundle(name: str, *, root: Path, suffix: str) -> Path:
    """Reject incorrect or spoofed source-manifest locations."""
    observed = Path(getattr(context.input, name))
    expected = root / suffix / "manifest.json"
    if observed.resolve() != expected.resolve():
        raise ValueError(f"Stage 08 input path mismatch: {name}")
    if observed.is_symlink() or not observed.is_file():
        raise ValueError(f"Stage 08 input is missing or a symlink: {name}")
    return observed.parent


def _check_destination(destination: Path) -> None:
    """Permit only a workflow-invalidated Stage 08 bundle directory."""
    if destination.is_symlink():
        raise FileExistsError("Stage 08 destination cannot be a symlink")
    if not destination.exists():
        return
    if not destination.is_dir():
        raise FileExistsError("Stage 08 destination is not a directory")
    manifest = destination / "manifest.json"
    if manifest.exists() or manifest.is_symlink():
        raise FileExistsError("Stage 08 completed bundle cannot be replaced")
    for item in destination.iterdir():
        if item.name not in _OUTPUT_NAMES or item.is_symlink() or not item.is_file():
            raise FileExistsError(
                f"Stage 08 invalidated directory has unexpected item: {item}"
            )


def run() -> None:
    settings = context.params.stage08_collection
    if not isinstance(settings, dict) or set(settings) != {"execute_full_scan"}:
        raise ValueError("Invalid Stage 08 workflow collection settings")
    if settings["execute_full_scan"] is not True:
        raise RuntimeError(
            "Stage 08 full collection is disabled. This rule only runs when "
            "stage08_collection.execute_full_scan=true is explicitly configured."
        )

    raw_work = Path(context.params.work_root)
    raw_results = Path(context.params.results_root)
    if raw_work.is_symlink() or raw_results.is_symlink():
        raise ValueError("Stage 08 work/results roots cannot be symlinks")
    work = raw_work.resolve()
    results = raw_results.resolve()
    if work == results or work in results.parents or results in work.parents:
        raise ValueError("Stage 08 work/results roots must be disjoint")
    if not results.is_dir():
        raise ValueError("Stage 08 results root must be a physical directory")

    destination = Path(context.output[0]).parent
    expected = results / "common" / "08"
    if destination.resolve() != expected.resolve():
        raise ValueError("Stage 08 output must be the canonical common/08 bundle")
    _check_destination(destination)

    inputs = {
        "stage07_bundle": _required_bundle(
            "stage07_manifest", root=results, suffix="common/07"
        ),
        "start_goa_bundle": _required_bundle(
            "start_goa_manifest", root=work, suffix="START/01B1"
        ),
        "end_goa_bundle": _required_bundle(
            "end_goa_manifest", root=work, suffix="END/01B1"
        ),
        "go_policy_bundle": _required_bundle(
            "go_policy_manifest", root=work, suffix="common/01B0"
        ),
    }
    # M4 rechecks every manifest and the GAFs before publishing. The option
    # is reserved for the workflow invalidation contract, never the direct CLI.
    manifest = publish_raw_annotation_collection(
        **inputs,
        destination=destination,
        allow_invalidated_destination=True,
        implementation_sources={
            "scripts/pipeline/08_collect_annotations.py": Path(
                context.input.implementation_script
            ),
            "workflow/scripts/run_stage_08.py": Path(context.input.runner_script),
        },
    )
    if manifest.stage != "08" or manifest.status != "complete":
        raise ValueError("Stage 08 publisher did not return a complete manifest")


run()
