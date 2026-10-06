"""Small runtime helpers shared by production stage wrappers."""

from __future__ import annotations

import platform
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from probe.workflow.artifacts import Artifact, sha256_file


def input_artifact(
    path: str | Path,
    *,
    checksum: bool = True,
) -> Artifact:
    """Describe one immutable source file used by a stage."""

    source = Path(path)
    if not source.is_file():
        raise FileNotFoundError(source)

    return Artifact(
        path=str(source.resolve()),
        size_bytes=source.stat().st_size,
        sha256=sha256_file(source) if checksum else None,
    )


def runtime_environment() -> dict[str, str]:
    """Minimal reproducible execution-environment metadata."""

    return {
        "python": sys.version.split()[0],
        "platform": platform.platform(),
    }


def ontology_provenance(
    config: Mapping[str, Any],
    *,
    path: str | Path,
    observed_sha256: str,
) -> dict[str, Any]:
    """Record the exact user-selected go.owl artifact used by a stage."""

    return {
        "path": str(Path(path).resolve()),
        "sha256": observed_sha256,
        "expected_sha256": config.get("expected_sha256"),
        "canonical_url": config.get("canonical_url"),
        "original_url": config.get("original_url"),
        "retrieval_date": config.get("retrieval_date"),
        "version_iri": config.get("version_iri"),
    }
