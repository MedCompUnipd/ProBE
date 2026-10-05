"""Versioned manifests and atomic bundles, independent of any workflow engine."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import tempfile
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

MANIFEST_NAME = "manifest.json"
_DIGEST = re.compile(r"[0-9a-f]{64}")


def _utc_now() -> str:
    return (
        datetime.now(UTC)
        .isoformat()
        .replace("+00:00", "Z")
    )


def _validate_utc_timestamp(value: str) -> None:
    if not isinstance(value, str) or not value:
        raise ValueError("created_at_utc must be a nonempty timestamp")
    try:
        parsed = datetime.fromisoformat(
            value.replace("Z", "+00:00")
        )
    except ValueError as error:
        raise ValueError("invalid created_at_utc timestamp") from error
    if parsed.tzinfo is None:
        raise ValueError("created_at_utc must include a timezone")


def _fsync_directory(path: Path) -> None:
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    fd = os.open(path, flags)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def sha256_file(path: Path) -> str:
    """Stream a checksum at production time or during explicit deep validation."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(8 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


@dataclass(frozen=True)
class Artifact:
    path: str
    size_bytes: int
    sha256: str | None

    def validate(self, *, relative: bool = False) -> None:
        if not isinstance(self.path, str) or not self.path:
            raise ValueError("artifact path must be nonempty")
        path = Path(self.path)
        if relative and (path.is_absolute() or ".." in path.parts or path == Path(".")):
            raise ValueError("output artifact path must stay inside its bundle")
        if type(self.size_bytes) is not int or self.size_bytes < 0:
            raise ValueError("artifact size must be nonnegative")
        if self.sha256 is not None and (
            not isinstance(self.sha256, str) or not _DIGEST.fullmatch(self.sha256)
        ):
            raise ValueError("invalid artifact SHA256")


@dataclass(frozen=True)
class StageManifest:
    """Schema v1: completion describes evidence production, never entity identity.

    Inputs refer to source files or upstream manifests. Missing historical ontology
    metadata remains null. Implementation includes version/build fingerprints;
    parameters include scientific policy and operational settings for audit.
    """

    stage: str
    role: str | None
    release_id: str | None
    parameters: Mapping[str, Any]
    ontology: Mapping[str, Any]
    implementation: Mapping[str, Any]
    inputs: tuple[Artifact, ...]
    outputs: tuple[Artifact, ...]
    environment: Mapping[str, Any] = field(default_factory=dict)
    created_at_utc: str = field(default_factory=_utc_now)
    schema_version: int = 1
    status: str = "complete"

    def validate(self) -> None:
        if type(self.schema_version) is not int or self.schema_version != 1:
            raise ValueError("unsupported manifest schema")
        if (
            self.status != "complete"
            or not isinstance(self.stage, str)
            or not self.stage
        ):
            raise ValueError("manifest must identify a completed stage")
        if self.role not in {None, "START", "END"}:
            raise ValueError("manifest role must be START, END, or null")
        if (self.role is None) != (self.release_id is None):
            raise ValueError("role and release_id must both be supplied or both null")
        if self.release_id is not None and (
            not isinstance(self.release_id, str) or not self.release_id.strip()
        ):
            raise ValueError("release_id must be nonempty")
        for value in (
            self.parameters,
            self.ontology,
            self.implementation,
            self.environment,
        ):
            if not isinstance(value, Mapping):
                raise ValueError("manifest metadata must be mappings")
        _validate_utc_timestamp(self.created_at_utc)
        digest = self.ontology.get("sha256")
        if not isinstance(digest, str) or not _DIGEST.fullmatch(digest):
            raise ValueError("ontology SHA256 is required")
        required_go = {"original_url", "retrieval_date", "version_iri"}
        if not required_go.issubset(self.ontology):
            raise ValueError("unknown ontology provenance must be explicit null fields")
        for key in ("name", "version"):
            if (
                not isinstance(self.implementation.get(key), str)
                or not self.implementation[key]
            ):
                raise ValueError("implementation name and version are required")
        if not self.outputs:
            raise ValueError("a complete bundle must declare outputs")
        for item in self.inputs:
            item.validate()
        names = []
        for item in self.outputs:
            item.validate(relative=True)
            names.append(item.path)
        if len(names) != len(set(names)) or MANIFEST_NAME in names:
            raise ValueError("duplicate or reserved output path")
        json.dumps(asdict(self), allow_nan=False)

    def write(self, path: Path) -> None:
        self.validate()
        path.write_text(
            json.dumps(asdict(self), indent=2, sort_keys=True, allow_nan=False) + "\n",
            encoding="utf-8",
        )

    @classmethod
    def read(cls, path: Path) -> StageManifest:
        data = json.loads(path.read_text(encoding="utf-8"))
        try:
            data["inputs"] = tuple(Artifact(**item) for item in data["inputs"])
            data["outputs"] = tuple(Artifact(**item) for item in data["outputs"])
            result = cls(**data)
            result.validate()
        except (KeyError, TypeError) as error:
            raise ValueError("invalid stage manifest schema") from error
        return result


def validate_bundle(bundle: Path, *, deep: bool = False) -> StageManifest:
    """Validate the exact declared payload of an immutable stage bundle.

    File sizes are always checked. Payload SHA256 values are checked only when
    ``deep=True``; normal Snakemake restart decisions do not re-hash large
    biological artifacts.
    """
    bundle = Path(bundle)

    if bundle.is_symlink() or (bundle / MANIFEST_NAME).is_symlink():
        raise ValueError("bundle and manifest must not be symlinks")

    manifest = StageManifest.read(bundle / MANIFEST_NAME)

    actual: set[str] = set()

    for path in bundle.rglob("*"):
        if path.is_symlink():
            raise ValueError("stage bundles cannot contain symlinks")
        if path.is_file():
            relative = path.relative_to(bundle).as_posix()
            if relative != MANIFEST_NAME:
                actual.add(relative)

    declared = {item.path for item in manifest.outputs}

    if actual != declared:
        missing = sorted(declared - actual)
        unexpected = sorted(actual - declared)
        raise ValueError(
            "bundle payload differs from manifest: "
            f"missing={missing} unexpected={unexpected}"
        )

    for item in manifest.outputs:
        path = bundle / item.path

        if not path.is_file() or path.stat().st_size != item.size_bytes:
            raise ValueError(
                f"missing or size-mismatched artifact: {item.path}"
            )

        if deep:
            if item.sha256 is None:
                raise ValueError(
                    f"missing SHA256 for deep validation: {item.path}"
                )
            if sha256_file(path) != item.sha256:
                raise ValueError(
                    f"mismatched SHA256: {item.path}"
                )

    return manifest


@contextmanager
def atomic_stage_bundle(
    destination: Path,
    *,
    stage: str,
    role: str | None,
    release_id: str | None,
    parameters: Mapping[str, Any],
    ontology: Mapping[str, Any],
    implementation: Mapping[str, Any],
    inputs: tuple[Artifact, ...] = (),
    environment: Mapping[str, Any] | None = None,
    hash_outputs: bool = True,
) -> Iterator[Path]:
    """Publish a new immutable bundle by one same-filesystem directory rename.

    A sibling lock prevents concurrent publishers; no existing bundle is replaced.
    After process death, an operator must verify no writer survives before clearing
    its orphan lock/temp directory. This is publication protection, not job state.
    """
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    lock = destination.with_name(f".{destination.name}.publish-lock")
    lock.mkdir()  # Exclusive across cooperating writers.
    staging: Path | None = None
    try:
        if destination.exists() or destination.is_symlink():
            raise FileExistsError(destination)
        staging = Path(
            tempfile.mkdtemp(prefix=f".{destination.name}.", dir=destination.parent)
        )
        yield staging
        artifacts = []
        for path in sorted(staging.rglob("*")):
            if path.is_symlink():
                raise ValueError("stage bundles cannot contain symlinks")
            if path.is_file():
                artifacts.append(
                    Artifact(
                        path.relative_to(staging).as_posix(),
                        path.stat().st_size,
                        sha256_file(path) if hash_outputs else None,
                    )
                )
        manifest = StageManifest(
            stage=stage,
            role=role,
            release_id=release_id,
            parameters=parameters,
            ontology=ontology,
            implementation=implementation,
            inputs=inputs,
            outputs=tuple(artifacts),
            environment={} if environment is None else environment,
        )
        manifest.write(staging / MANIFEST_NAME)
        for path in [staging / item.path for item in artifacts] + [
            staging / MANIFEST_NAME
        ]:
            with path.open("rb") as handle:
                os.fsync(handle.fileno())
        # Persist the staging directory entries before publishing it.
        _fsync_directory(staging)

        if destination.exists() or destination.is_symlink():
            raise FileExistsError(destination)

        staging.rename(destination)

        # Persist the directory rename itself.
        _fsync_directory(destination.parent)
    finally:
        if staging is not None and staging.exists():
            shutil.rmtree(staging)
        lock.rmdir()
