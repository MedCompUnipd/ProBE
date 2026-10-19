"""Verified bridge from canonical B3 Stage 05/06A into Stage 2C completion.

This bridge never executes alignments or changes scientific acceptability.
All payload files are copied byte-for-byte to a new, immutable directory.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
from pathlib import Path

from probe.canonical_v2.stage05_06a import (
    AUDIT,
    JOBS,
    PRIMARY,
    STAGE_SCHEMA,
    STORES,
    SUMMARY,
)
from probe.stage2c_alignment_preview import (
    AUDIT_OUT,
    END_STORE,
    JOBS_OUT,
    JOBS_SUMMARY,
    SCHEMA,
    START_STORE,
    SUMMARY_OUT,
    UNION_OUT,
)

SOURCE_TO_PREVIEW = {
    PRIMARY: UNION_OUT,
    AUDIT: AUDIT_OUT,
    JOBS: JOBS_OUT,
    STORES["START"]: START_STORE,
    STORES["END"]: END_STORE,
    "06A.summary.json": JOBS_SUMMARY,
}


def _sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _json(path: Path) -> dict:
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"Invalid input file: {path}")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"Expected JSON object: {path}")
    return value


def validate_b3_stage(b3: Path, releases: dict[str, str]) -> dict:
    """Verify B3 publication and its closed payload-file universe."""
    if not b3.is_dir() or b3.is_symlink():
        raise ValueError("Missing or symlinked B3 directory")
    if set(releases) != {"START", "END"}:
        raise ValueError("Invalid role set")
    if len(set(releases.values())) != 2 or not all(releases.values()):
        raise ValueError("Invalid release IDs")
    summary = _json(b3 / SUMMARY)
    if (
        summary.get("schema") != STAGE_SCHEMA
        or summary.get("mode") != "cold_start"
        or summary.get("releases") != releases
        or summary.get("go_donor_decisions") != 0
        or summary.get("dp_executions") != 0
        or summary.get("historical_05_07_inputs") != 0
    ):
        raise ValueError("B3 stage contract or release mismatch")
    expected = summary.get("outputs_sha256")
    if not isinstance(expected, dict):
        raise ValueError("Missing B3 output checksums")
    required = {
        *SOURCE_TO_PREVIEW,
        "05C2.summary.json",
    }
    if set(expected) != required:
        raise ValueError("Unexpected B3 output-file universe")
    actual_files = {path.name for path in b3.iterdir()}
    if actual_files != required | {SUMMARY}:
        raise ValueError("Unexpected B3 publication content")
    for name, digest in expected.items():
        source = b3 / name
        if source.is_symlink() or not source.is_file():
            raise ValueError(f"Missing or symlinked B3 payload: {name}")
        if digest != _sha(source):
            raise ValueError(f"B3 output SHA256 mismatch: {name}")
    jobs = _json(b3 / "06A.summary.json")
    if (
        jobs.get("stage") != "06A"
        or jobs.get("releases") != releases
        or jobs.get("policy", {}).get("same_entity_decision_made_here")
        is not False
    ):
        raise ValueError("Stage 06A scientific contract mismatch")
    return summary


def publish_completion_input(
    *,
    b3: Path,
    releases: dict[str, str],
    destination: Path,
) -> dict:
    """Publish exact B3 bytes under the Stage 2C preview schema."""
    summary = validate_b3_stage(b3, releases)
    if destination.exists() or destination.is_symlink():
        raise FileExistsError("Completion-input destination occupied")
    if destination.parent.is_symlink() or not destination.parent.is_dir():
        raise ValueError("Completion-input parent must exist and be physical")
    if destination == b3 or destination in b3.parents or b3 in destination.parents:
        raise ValueError("Source/destination nesting forbidden")
    with tempfile.TemporaryDirectory(
        prefix="probe-canonical-completion-input-",
        dir=destination.parent,
    ) as directory:
        product = Path(directory) / "product"
        product.mkdir()
        for source_name, output_name in SOURCE_TO_PREVIEW.items():
            shutil.copyfile(b3 / source_name, product / output_name)
            if _sha(b3 / source_name) != _sha(product / output_name):
                raise ValueError("Completion-input copy digest mismatch")
        preview_summary = {
            "schema": SCHEMA,
            "releases": releases,
            "canonical_rows_preserved": True,
            "audit_provenance_preserved": True,
            "alignment_executed": False,
            "donor_decisions": 0,
            "production_stages_replaced": False,
            "source_schema": STAGE_SCHEMA,
            "input_sha256": {
                str(b3 / PRIMARY): summary["outputs_sha256"][PRIMARY],
                str(b3 / SUMMARY): _sha(b3 / SUMMARY),
            },
            "output_sha256": {
                output: _sha(product / output)
                for output in SOURCE_TO_PREVIEW.values()
            },
        }
        (product / SUMMARY_OUT).write_text(
            json.dumps(preview_summary, sort_keys=True, indent=2) + "\n",
            encoding="utf-8",
        )
        if _sha(b3 / SUMMARY) != preview_summary["input_sha256"][
            str(b3 / SUMMARY)
        ]:
            raise ValueError("B3 publication changed during adaptation")
        if destination.exists() or destination.is_symlink():
            raise FileExistsError("Concurrent completion-input publication")
        os.rename(product, destination)
    return preview_summary
