"""Publish both completed GOA donor scans as one atomic Stage 08 bundle.

This is a library boundary, not a Snakemake runner. It does not allow evidence
selection, implicit Stage 07 reruns, or writing over a completed bundle.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from probe.stage08_collection_inputs import CollectionInputs, prepare_collection_inputs
from probe.stage08_gaf_collection import GafScanResult, collect_one_normalized_gaf
from probe.workflow.artifacts import (
    StageManifest,
    atomic_stage_bundle,
    sha256_file,
    validate_bundle,
)
from probe.workflow.outcome import write_stage_outcome
from probe.workflow.runtime import runtime_environment

PUBLISH_SCHEMA = "stage08-raw-collection-bundle-v1"
IMPLEMENTATION_VERSION = "stage08-raw-collection-v1"
_IMPLEMENTATION_FILES = (
    "records.py",
    "stage08_donors.py",
    "stage08_annotations.py",
    "stage08_collection_inputs.py",
    "stage08_gaf_collection.py",
    "stage08_collection_publish.py",
    "workflow/artifacts.py",
    "workflow/outcome.py",
    "workflow/runtime.py",
)


_EXTRA_IMPLEMENTATION_NAMES = frozenset(
    {
        "scripts/pipeline/08_collect_annotations.py",
        "workflow/scripts/run_stage_08.py",
    }
)


def _source_metadata(
    extra_sources: Mapping[str, Path] | None = None,
) -> dict[str, Any]:
    base = Path(__file__).resolve().parent
    digests = {
        f"src/probe/{relative}": sha256_file(base / relative)
        for relative in _IMPLEMENTATION_FILES
    }
    for name, value in sorted((extra_sources or {}).items()):
        if name not in _EXTRA_IMPLEMENTATION_NAMES or name in digests:
            raise ValueError(f"invalid Stage 08 implementation source: {name!r}")
        path = Path(value)
        if path.is_symlink() or not path.is_file():
            raise ValueError(f"missing implementation source: {name}")
        digests[name] = sha256_file(path)
    return {
        "name": "08_collect_annotations",
        "version": IMPLEMENTATION_VERSION,
        "source_sha256": digests,
    }


def _file_stamp(path: Path) -> tuple[int, int, int, int, int]:
    stat = path.stat()
    return (
        stat.st_dev,
        stat.st_ino,
        stat.st_size,
        stat.st_mtime_ns,
        stat.st_ctime_ns,
    )


def _source_stamps(inputs: CollectionInputs) -> dict[str, tuple[int, ...]]:
    return {item.role: _file_stamp(Path(item.source.path)) for item in inputs.gafs}


def _verified_release_results(
    inputs: CollectionInputs,
    results: tuple[GafScanResult, GafScanResult],
) -> None:
    if tuple(item.role for item in inputs.gafs) != ("START", "END"):
        raise ValueError("collection input roles must be START and END")
    if tuple(item.role for item in results) != ("START", "END"):
        raise ValueError("scan results must be START and END")
    for gaf, result in zip(inputs.gafs, results, strict=True):
        if not result.coverage_certified:
            raise ValueError("cannot publish uncertified GAF coverage")
        if result.release != gaf.source.release:
            raise ValueError("scan release differs from authorized release")
        if result.normalized_gaf_sha256 != gaf.source.sha256:
            raise ValueError("scan digest differs from declared source")
        expected_yes = sum(
            donor.may_donate and donor.release == result.release
            for donor in inputs.donors.associations
        )
        if result.donor_yes_associations != expected_yes:
            raise ValueError("scan donor count differs from accepted associations")
        if result.donor_yes_without_rows > expected_yes:
            raise ValueError("scan missing-donor count exceeds eligible donors")


def publish_raw_annotation_collection(
    *,
    stage07_bundle: Path,
    start_goa_bundle: Path,
    end_goa_bundle: Path,
    go_policy_bundle: Path,
    destination: Path,
    allow_invalidated_destination: bool = False,
    implementation_sources: Mapping[str, Path] | None = None,
) -> StageManifest:
    """Collect the two complete GAFs and publish after successful EOF+SHA256.

    The caller must opt into the run only after performance validation. A workflow
    may authorize replacement only after invalidating the prior completion marker.
    Ordinary direct calls cannot replace an existing completed destination.
    """
    arguments = {
        "stage07_bundle": Path(stage07_bundle),
        "start_goa_bundle": Path(start_goa_bundle),
        "end_goa_bundle": Path(end_goa_bundle),
        "go_policy_bundle": Path(go_policy_bundle),
    }
    inputs = prepare_collection_inputs(**arguments)
    supplied_sources = dict(implementation_sources or {})
    implementation = _source_metadata(supplied_sources)
    before = _source_stamps(inputs)
    with atomic_stage_bundle(
        Path(destination),
        stage="08",
        role=None,
        release_id=None,
        parameters={
            "schema_version": PUBLISH_SCHEMA,
            "input_fingerprint": inputs.fingerprint,
            "collection": "all_retained_normalized_gaf_donor_rows",
            "evidence_selection": "none",
            "not_veto": "none",
            "donor_criterion": "annotation_donor=YES",
            "release_ids": {item.role: item.source.release for item in inputs.gafs},
        },
        ontology=json.loads(inputs.ontology_json),
        implementation=implementation,
        inputs=inputs.provenance_inputs,
        environment=runtime_environment(),
        allow_invalidated_destination=allow_invalidated_destination,
        hash_outputs=True,
    ) as staging:
        results = []
        for gaf in inputs.gafs:
            role_dir = staging / gaf.role
            role_dir.mkdir()
            results.append(
                collect_one_normalized_gaf(
                    gaf=gaf, donors=inputs.donors, staging_dir=role_dir
                )
            )
            prefix = f"08_{gaf.role.lower()}_"
            expected = {
                f"{prefix}source_assertions.tsv",
                f"{prefix}annotation_links.tsv",
                f"{prefix}donor_coverage.tsv",
                f"{prefix}evidence_inventory.tsv",
                f"{prefix}scan_summary.json",
            }
            actual = {item.name for item in role_dir.iterdir()}
            if actual != expected:
                raise ValueError("collector output files differ from expected set")
            for name in sorted(expected):
                source_file = role_dir / name
                if source_file.is_symlink() or not source_file.is_file():
                    raise ValueError("collector output is not a regular file")
                source_file.rename(staging / name)
            role_dir.rmdir()
        completed = (results[0], results[1])
        _verified_release_results(inputs, completed)
        if _source_stamps(inputs) != before:
            raise ValueError("normalized GAF changed across collection transaction")
        final_inputs = prepare_collection_inputs(**arguments)
        if final_inputs.fingerprint != inputs.fingerprint:
            raise ValueError("collection inputs changed before publication")
        if _source_stamps(final_inputs) != before:
            raise ValueError("normalized GAF changed before publication")
        if _source_metadata(supplied_sources) != implementation:
            raise ValueError("Stage 08 implementation changed during collection")
        summary = {
            "schema_version": PUBLISH_SCHEMA,
            "input_fingerprint": inputs.fingerprint,
            "annotation_coverage_certified": True,
            "by_role": {item.role: item.as_dict() for item in completed},
            "retrieved_source_rows": sum(
                item.retrieved_source_rows for item in completed
            ),
            "annotation_links": sum(item.annotation_links for item in completed),
            "donor_yes_without_rows": sum(
                item.donor_yes_without_rows for item in completed
            ),
        }
        (staging / "08_collection_summary.json").write_text(
            json.dumps(summary, sort_keys=True, indent=2, allow_nan=False) + "\n",
            encoding="utf-8",
        )
        row_count = summary["retrieved_source_rows"]
        write_stage_outcome(
            staging / "stage_outcome.json",
            state="COMPLETED" if row_count else "EMPTY_RESULT",
            reason_code=(
                "COLLECTION_COMPLETE" if row_count else "NO_RETAINED_DONOR_ROWS"
            ),
            message="Both normalized GOA releases scanned and verified",
            counts={
                "retrieved_source_rows": row_count,
                "annotation_links": summary["annotation_links"],
                "donor_yes_without_rows": summary["donor_yes_without_rows"],
            },
        )
    return validate_bundle(Path(destination), deep=False)
