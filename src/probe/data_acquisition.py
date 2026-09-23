"""Reproducible acquisition of immutable benchmark source artifacts."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import shutil
import sys
import tarfile
import tomllib
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path, PurePosixPath
from typing import Any, BinaryIO, Literal

MANIFEST_SCHEMA_VERSION = 1
SNAPSHOT_ROLES = ("start", "end")
REQUIRED_TAXONOMY_MEMBERS = frozenset(
    {"nodes.dmp", "names.dmp", "merged.dmp", "delnodes.dmp"}
)
DOWNLOAD_CHUNK_SIZE = 8 * 1024 * 1024
PROGRESS_INTERVAL = 1024 * 1024 * 1024
MUTABLE_URL_MARKERS = (
    "current_release",
    "/current/",
    "new_taxdump.zip",
    "/goa/uniprot/goa_uniprot_all.gaf.gz",
)

Progress = Callable[[str], None]
SnapshotRole = Literal["start", "end"]


class AcquisitionError(RuntimeError):
    """Raised when acquisition cannot proceed without risking reproducibility."""


@dataclass(frozen=True, slots=True)
class SourceSpec:
    url: str
    expected_sha256: str | None = None
    filename: str | None = None


@dataclass(frozen=True, slots=True)
class UniProtConfig:
    release: str
    mode: Literal["archive", "direct"]
    archive: SourceSpec | None = None
    swissprot: SourceSpec | None = None
    trembl: SourceSpec | None = None
    swissprot_member: str | None = None
    trembl_member: str | None = None


@dataclass(frozen=True, slots=True)
class LabeledSourceConfig:
    label: str
    source: SourceSpec


@dataclass(frozen=True, slots=True)
class SnapshotConfig:
    role: SnapshotRole
    uniprot: UniProtConfig
    goa: LabeledSourceConfig
    taxonomy: LabeledSourceConfig
    ontology: LabeledSourceConfig | None = None


@dataclass(frozen=True, slots=True)
class AcquisitionConfig:
    data_root: Path
    minimum_free_bytes: int | None
    snapshots: Mapping[SnapshotRole, SnapshotConfig]


@dataclass(frozen=True, slots=True)
class DiskSpaceReport:
    filesystem_path: Path
    total_bytes: int
    used_bytes: int
    free_bytes: int
    minimum_free_bytes: int | None


@dataclass(frozen=True, slots=True)
class SnapshotPaths:
    root: Path
    uniprot_raw: Path
    uniprot_extracted: Path
    uniprot_derived: Path
    goa_raw: Path
    taxonomy_raw: Path
    taxonomy_extracted: Path
    ontology_raw: Path
    indexes: Path
    manifest: Path


@dataclass(frozen=True, slots=True)
class AcquisitionResult:
    role: SnapshotRole
    manifest_path: Path
    manifest: Mapping[str, Any]


def _require_table(value: object, field: str) -> Mapping[str, Any]:
    if not isinstance(value, dict):
        raise AcquisitionError(f"configuration field {field!r} must be a table")
    return value


def _require_text(table: Mapping[str, Any], field: str, context: str) -> str:
    value = table.get(field)
    if not isinstance(value, str) or not value.strip():
        raise AcquisitionError(f"{context}.{field} must be a non-empty string")
    return value.strip()


def _optional_text(table: Mapping[str, Any], field: str, context: str) -> str | None:
    value = table.get(field)
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise AcquisitionError(f"{context}.{field} must be a non-empty string")
    return value.strip()


def _validate_sha256(value: str | None, context: str) -> str | None:
    if value is None:
        return None
    normalized = value.lower()
    if len(normalized) != 64 or any(c not in "0123456789abcdef" for c in normalized):
        raise AcquisitionError(f"{context} must be a 64-character SHA-256 value")
    return normalized


def _source_spec(table: Mapping[str, Any], context: str) -> SourceSpec:
    url = _require_text(table, "url", context)
    scheme = urllib.parse.urlparse(url).scheme.lower()
    if scheme not in {"http", "https", "ftp", "file"}:
        raise AcquisitionError(
            f"{context}.url must use http, https, ftp, or file, got {url!r}"
        )
    return SourceSpec(
        url=url,
        expected_sha256=_validate_sha256(
            _optional_text(table, "sha256", context), f"{context}.sha256"
        ),
        filename=_optional_text(table, "filename", context),
    )


def _nested_source(
    table: Mapping[str, Any], field: str, context: str
) -> SourceSpec | None:
    value = table.get(field)
    if value is None:
        return None
    return _source_spec(
        _require_table(value, f"{context}.{field}"), f"{context}.{field}"
    )


def _snapshot_config(role: SnapshotRole, table: Mapping[str, Any]) -> SnapshotConfig:
    context = f"snapshots.{role}"
    uniprot_table = _require_table(table.get("uniprot"), f"{context}.uniprot")
    uniprot_context = f"{context}.uniprot"
    release = _require_text(uniprot_table, "release", uniprot_context)
    mode = _require_text(uniprot_table, "mode", uniprot_context)
    if mode not in {"archive", "direct"}:
        raise AcquisitionError(
            f"{uniprot_context}.mode must be 'archive' or 'direct', got {mode!r}"
        )
    archive = _nested_source(uniprot_table, "archive", uniprot_context)
    swissprot = _nested_source(uniprot_table, "swissprot", uniprot_context)
    trembl = _nested_source(uniprot_table, "trembl", uniprot_context)
    if mode == "archive" and (archive is None or swissprot or trembl):
        raise AcquisitionError(
            f"{uniprot_context} archive mode requires only an archive source"
        )
    if mode == "direct" and (archive or swissprot is None or trembl is None):
        raise AcquisitionError(
            f"{uniprot_context} direct mode requires swissprot and trembl sources"
        )
    uniprot = UniProtConfig(
        release=release,
        mode=mode,  # type: ignore[arg-type]
        archive=archive,
        swissprot=swissprot,
        trembl=trembl,
        swissprot_member=_optional_text(
            uniprot_table, "swissprot_member", uniprot_context
        ),
        trembl_member=_optional_text(uniprot_table, "trembl_member", uniprot_context),
    )
    if mode == "direct" and (uniprot.swissprot_member or uniprot.trembl_member):
        raise AcquisitionError(
            f"{uniprot_context} archive member names are invalid in direct mode"
        )

    def labeled_source(name: str) -> LabeledSourceConfig:
        source_table = _require_table(table.get(name), f"{context}.{name}")
        source_context = f"{context}.{name}"
        label_field = "snapshot_date" if name == "taxonomy" else "release"
        return LabeledSourceConfig(
            _require_text(source_table, label_field, source_context),
            _source_spec(source_table, source_context),
        )

    ontology: LabeledSourceConfig | None = None
    if "ontology" in table:
        ontology_table = _require_table(table["ontology"], f"{context}.ontology")
        ontology = LabeledSourceConfig(
            _require_text(ontology_table, "release", f"{context}.ontology"),
            _source_spec(ontology_table, f"{context}.ontology"),
        )
    return SnapshotConfig(
        role=role,
        uniprot=uniprot,
        goa=labeled_source("goa"),
        taxonomy=labeled_source("taxonomy"),
        ontology=ontology,
    )


def load_acquisition_config(path: str | Path) -> AcquisitionConfig:
    """Load and strictly validate an explicit TOML acquisition configuration."""

    config_path = Path(path)
    try:
        with config_path.open("rb") as handle:
            raw = tomllib.load(handle)
    except (OSError, tomllib.TOMLDecodeError) as error:
        raise AcquisitionError(
            f"cannot read configuration {config_path}: {error}"
        ) from error

    root_value = raw.get("data_root")
    if not isinstance(root_value, str) or not root_value.strip():
        raise AcquisitionError("data_root must be an explicit absolute path")
    data_root = Path(root_value).expanduser()
    if not data_root.is_absolute():
        raise AcquisitionError("data_root must be an explicit absolute path")
    minimum_free = raw.get("minimum_free_bytes")
    if minimum_free is not None and (
        not isinstance(minimum_free, int)
        or isinstance(minimum_free, bool)
        or minimum_free < 0
    ):
        raise AcquisitionError("minimum_free_bytes must be a non-negative integer")
    snapshots_table = _require_table(raw.get("snapshots"), "snapshots")
    missing = [role for role in SNAPSHOT_ROLES if role not in snapshots_table]
    if missing:
        raise AcquisitionError(
            "configuration must explicitly define both snapshots: " + ", ".join(missing)
        )
    unexpected = sorted(set(snapshots_table) - set(SNAPSHOT_ROLES))
    if unexpected:
        raise AcquisitionError(
            "unsupported snapshot role(s): " + ", ".join(unexpected)
        )
    snapshots = {
        role: _snapshot_config(
            role, _require_table(snapshots_table[role], f"snapshots.{role}")
        )
        for role in SNAPSHOT_ROLES
    }
    _reject_shared_mutable_sources(snapshots)
    _reject_repository_data_root(data_root)
    return AcquisitionConfig(data_root.resolve(), minimum_free, snapshots)


def _reject_shared_mutable_sources(
    snapshots: Mapping[SnapshotRole, SnapshotConfig],
) -> None:
    start = snapshots["start"]
    end = snapshots["end"]
    pairs = [
        ("GOA", start.goa.source.url, end.goa.source.url),
        ("taxonomy", start.taxonomy.source.url, end.taxonomy.source.url),
    ]
    if start.uniprot.mode == end.uniprot.mode == "direct":
        assert start.uniprot.swissprot and end.uniprot.swissprot
        assert start.uniprot.trembl and end.uniprot.trembl
        pairs.extend(
            [
                (
                    "UniProt Swiss-Prot",
                    start.uniprot.swissprot.url,
                    end.uniprot.swissprot.url,
                ),
                (
                    "UniProt TrEMBL",
                    start.uniprot.trembl.url,
                    end.uniprot.trembl.url,
                ),
            ]
        )
    for label, start_url, end_url in pairs:
        lowered = start_url.lower()
        shared_mutable = start_url == end_url and any(
            marker in lowered for marker in MUTABLE_URL_MARKERS
        )
        if shared_mutable:
            raise AcquisitionError(
                f"{label} uses the same mutable URL for start and end: {start_url}"
            )


def _reject_repository_data_root(data_root: Path) -> None:
    module_path = Path(__file__).resolve()
    repository: Path | None = None
    for parent in module_path.parents:
        if (parent / ".git").exists() and (parent / "pyproject.toml").exists():
            repository = parent
            break
    if repository is None:
        return
    resolved = data_root.resolve()
    if resolved == repository or repository in resolved.parents:
        raise AcquisitionError(
            f"data_root must be outside the Git repository ({repository})"
        )


def _nearest_existing_path(path: Path) -> Path:
    candidate = path
    while not candidate.exists():
        parent = candidate.parent
        if parent == candidate:
            raise AcquisitionError(f"cannot locate a filesystem for {path}")
        candidate = parent
    return candidate


def disk_space_preflight(
    data_root: str | Path, minimum_free_bytes: int | None = None
) -> DiskSpaceReport:
    """Report target-filesystem capacity and enforce an optional free-space floor."""

    root = Path(data_root)
    filesystem_path = _nearest_existing_path(root)
    usage = shutil.disk_usage(filesystem_path)
    report = DiskSpaceReport(
        filesystem_path,
        usage.total,
        usage.used,
        usage.free,
        minimum_free_bytes,
    )
    if minimum_free_bytes is not None and usage.free < minimum_free_bytes:
        raise AcquisitionError(
            "insufficient free space on "
            f"{filesystem_path}: {usage.free} bytes available, "
            f"{minimum_free_bytes} required"
        )
    return report


def snapshot_paths(data_root: Path, role: SnapshotRole) -> SnapshotPaths:
    root = data_root / role
    return SnapshotPaths(
        root=root,
        uniprot_raw=root / "uniprot" / "raw",
        uniprot_extracted=root / "uniprot" / "extracted",
        uniprot_derived=root / "uniprot" / "derived",
        goa_raw=root / "goa" / "raw",
        taxonomy_raw=root / "taxonomy" / "raw",
        taxonomy_extracted=root / "taxonomy" / "extracted",
        ontology_raw=root / "ontology" / "raw",
        indexes=root / "indexes",
        manifest=data_root / "manifests" / f"{role}.json",
    )


def create_snapshot_layout(data_root: Path, role: SnapshotRole) -> SnapshotPaths:
    paths = snapshot_paths(data_root, role)
    for directory in (
        paths.uniprot_raw,
        paths.uniprot_extracted,
        paths.uniprot_derived,
        paths.goa_raw,
        paths.taxonomy_raw,
        paths.taxonomy_extracted,
        paths.ontology_raw,
        paths.indexes,
        paths.manifest.parent,
    ):
        directory.mkdir(parents=True, exist_ok=True)
    return paths


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(DOWNLOAD_CHUNK_SIZE), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _relative(path: Path, root: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError as error:
        raise AcquisitionError(f"managed path is outside data_root: {path}") from error


def _safe_filename(spec: SourceSpec) -> str:
    candidate = spec.filename
    if candidate is None:
        url_path = urllib.parse.unquote(urllib.parse.urlparse(spec.url).path)
        candidate = Path(url_path).name
    if not candidate or candidate in {".", ".."} or Path(candidate).name != candidate:
        raise AcquisitionError(
            f"source URL needs a safe filename override: {spec.url!r}"
        )
    return candidate


def _compression(path: Path) -> str:
    lower = path.name.lower()
    if lower.endswith((".tar.gz", ".tgz")):
        return "tar+gzip"
    if lower.endswith(".zip"):
        return "zip"
    if lower.endswith(".gz"):
        return "gzip"
    return "none"


def _read_json(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise AcquisitionError(f"cannot read metadata {path}: {error}") from error
    if not isinstance(value, dict):
        raise AcquisitionError(f"metadata must contain a JSON object: {path}")
    return value


def _write_json_atomic(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".part")
    with temporary.open("w", encoding="utf-8", newline="\n") as handle:
        json.dump(value, handle, indent=2, sort_keys=True)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def _download_state_path(data_root: Path, destination: Path) -> Path:
    relative = destination.relative_to(data_root)
    return (
        data_root
        / "manifests"
        / ".state"
        / relative.parent
        / f"{relative.name}.json"
    )


def _tool_version() -> str:
    try:
        return version("probe-benchmark")
    except PackageNotFoundError:
        return "unknown"


def _timestamp() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _backup_conflict(path: Path, *, progress: Progress | None) -> Path:
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    candidate = path.with_name(f"{path.name}.conflict-{stamp}")
    number = 1
    while candidate.exists():
        candidate = path.with_name(f"{path.name}.conflict-{stamp}-{number}")
        number += 1
    path.rename(candidate)
    if progress:
        progress(f"preserved conflicting path as {candidate}")
    return candidate


def _artifact_record(
    *,
    key: str,
    url: str,
    path: Path,
    data_root: Path,
    sha256: str,
    retrieved_at: str,
    archive_member: str | None = None,
    source_artifact: str | None = None,
    content_path: Path | None = None,
) -> dict[str, Any]:
    stored = content_path or path
    record: dict[str, Any] = {
        "artifact": key,
        "compression": _compression(path),
        "local_path": _relative(path, data_root),
        "retrieval_timestamp": retrieved_at,
        "sha256": sha256,
        "size_bytes": stored.stat().st_size,
        "source_url": url,
    }
    if archive_member is not None:
        record["archive_member"] = archive_member
    if source_artifact is not None:
        record["source_artifact"] = source_artifact
    return record


def _record_matches_file(record: Mapping[str, Any], path: Path) -> bool:
    expected_size = record.get("size_bytes")
    expected_sha = record.get("sha256")
    return (
        path.is_file()
        and isinstance(expected_size, int)
        and path.stat().st_size == expected_size
        and isinstance(expected_sha, str)
        and _sha256(path) == expected_sha
    )


def _download(
    spec: SourceSpec,
    destination: Path,
    *,
    progress: Progress | None,
) -> tuple[str, int]:
    temporary = destination.with_name(destination.name + ".part")
    request = urllib.request.Request(
        spec.url,
        headers={"User-Agent": f"ProBE/{_tool_version()} data-acquisition"},
    )
    digest = hashlib.sha256()
    written = 0
    next_report = PROGRESS_INTERVAL
    if progress:
        progress(f"downloading {spec.url} -> {destination}")
    try:
        with urllib.request.urlopen(request, timeout=60) as response, temporary.open(
            "wb"
        ) as output:
            length_header = response.headers.get("Content-Length")
            expected_length = int(length_header) if length_header else None
            while chunk := response.read(DOWNLOAD_CHUNK_SIZE):
                output.write(chunk)
                digest.update(chunk)
                written += len(chunk)
                if progress and written >= next_report:
                    progress(f"downloaded {written} bytes for {destination.name}")
                    next_report += PROGRESS_INTERVAL
            output.flush()
            os.fsync(output.fileno())
        if expected_length is not None and written != expected_length:
            raise AcquisitionError(
                f"truncated download for {spec.url}: expected {expected_length} bytes, "
                f"received {written}"
            )
        actual_sha = digest.hexdigest()
        if spec.expected_sha256 and actual_sha != spec.expected_sha256:
            raise AcquisitionError(
                f"checksum mismatch for {spec.url}: expected {spec.expected_sha256}, "
                f"got {actual_sha}"
            )
        os.replace(temporary, destination)
        return actual_sha, written
    except (OSError, urllib.error.URLError, ValueError) as error:
        if isinstance(error, AcquisitionError):
            raise
        raise AcquisitionError(f"download failed for {spec.url}: {error}") from error
    finally:
        if temporary.exists() and destination.exists():
            temporary.unlink()


def _acquire_raw(
    key: str,
    spec: SourceSpec,
    directory: Path,
    data_root: Path,
    *,
    previous: Mapping[str, Any] | None,
    repair: bool,
    progress: Progress | None,
) -> dict[str, Any]:
    destination = directory / _safe_filename(spec)
    sidecar = _download_state_path(data_root, destination)
    reusable: Mapping[str, Any] | None = None
    if previous and previous.get("source_url") == spec.url:
        reusable = previous
    elif sidecar.exists():
        sidecar_value = _read_json(sidecar)
        if sidecar_value and sidecar_value.get("source_url") == spec.url:
            reusable = sidecar_value
    reusable_path_matches = bool(
        reusable
        and reusable.get("local_path") == _relative(destination, data_root)
    )
    if reusable_path_matches and reusable and _record_matches_file(
        reusable, destination
    ):
        if spec.expected_sha256 and reusable.get("sha256") != spec.expected_sha256:
            raise AcquisitionError(
                f"configured checksum conflicts with valid local artifact {destination}"
            )
        if progress:
            progress(f"reusing verified artifact {destination}")
        record = dict(reusable)
        if spec.expected_sha256:
            record["configured_expected_sha256"] = spec.expected_sha256
        return record
    if destination.exists():
        if spec.expected_sha256 and _sha256(destination) == spec.expected_sha256:
            record = _artifact_record(
                key=key,
                url=spec.url,
                path=destination,
                data_root=data_root,
                sha256=spec.expected_sha256,
                retrieved_at=_timestamp(),
            )
            record["configured_expected_sha256"] = spec.expected_sha256
            _write_json_atomic(sidecar, record)
            return record
        if not repair:
            raise AcquisitionError(
                f"unverified or checksum-mismatched artifact exists: {destination}; "
                "rerun with --repair to preserve it and redownload"
            )
        _backup_conflict(destination, progress=progress)
        if sidecar.exists():
            _backup_conflict(sidecar, progress=progress)
    actual_sha, size = _download(spec, destination, progress=progress)
    record = _artifact_record(
        key=key,
        url=spec.url,
        path=destination,
        data_root=data_root,
        sha256=actual_sha,
        retrieved_at=_timestamp(),
    )
    if spec.expected_sha256:
        record["configured_expected_sha256"] = spec.expected_sha256
    if record["size_bytes"] != size:
        raise AcquisitionError(f"download size changed unexpectedly for {destination}")
    _write_json_atomic(sidecar, record)
    return record


def _safe_archive_name(name: str) -> PurePosixPath:
    normalized = PurePosixPath(name.replace("\\", "/"))
    if normalized.is_absolute() or ".." in normalized.parts:
        raise AcquisitionError(f"unsafe archive path: {name!r}")
    parts = tuple(part for part in normalized.parts if part not in {"", "."})
    if not parts:
        raise AcquisitionError(f"empty archive path: {name!r}")
    return PurePosixPath(*parts)


def _copy_stream(source: BinaryIO, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("wb") as output:
        shutil.copyfileobj(source, output, length=DOWNLOAD_CHUNK_SIZE)
        output.flush()
        os.fsync(output.fileno())


def _uniprot_candidates(
    members: Iterable[tarfile.TarInfo], marker: str
) -> list[tarfile.TarInfo]:
    candidates = []
    for member in members:
        basename = PurePosixPath(member.name).name.lower()
        if member.isfile() and basename.endswith(".dat.gz") and marker in basename:
            candidates.append(member)
    return sorted(candidates, key=lambda item: item.name)


def _select_tar_member(
    members: tuple[tarfile.TarInfo, ...], explicit: str | None, marker: str
) -> tarfile.TarInfo:
    if explicit is not None:
        matches = [item for item in members if item.name == explicit and item.isfile()]
    else:
        matches = _uniprot_candidates(members, marker)
    if len(matches) != 1:
        names = ", ".join(item.name for item in matches) or "none"
        raise AcquisitionError(
            f"expected exactly one {marker} .dat.gz archive member; found {names}"
        )
    return matches[0]


def _valid_extraction_records(
    records: object, directory: Path, data_root: Path
) -> bool:
    if not isinstance(records, list) or not records:
        return False
    for record in records:
        if not isinstance(record, dict) or not isinstance(
            record.get("local_path"), str
        ):
            return False
        path = data_root / record["local_path"]
        if directory.resolve() not in path.resolve().parents:
            return False
        if not _record_matches_file(record, path):
            return False
    return True


def _prepare_extraction_directory(
    directory: Path,
    *,
    reusable_records: object,
    data_root: Path,
    repair: bool,
    progress: Progress | None,
) -> bool:
    if _valid_extraction_records(reusable_records, directory, data_root):
        if progress:
            progress(f"reusing verified extraction {directory}")
        return True
    if directory.exists() and any(directory.iterdir()):
        if not repair:
            raise AcquisitionError(
                f"unverified or mismatched extraction exists: {directory}; "
                "rerun with --repair to preserve and re-extract"
            )
        _backup_conflict(directory, progress=progress)
        directory.mkdir(parents=True)
    return False


def _extract_uniprot_archive(
    archive_record: Mapping[str, Any],
    config: UniProtConfig,
    paths: SnapshotPaths,
    data_root: Path,
    *,
    previous_records: object,
    repair: bool,
    progress: Progress | None,
) -> list[dict[str, Any]]:
    if _prepare_extraction_directory(
        paths.uniprot_extracted,
        reusable_records=previous_records,
        data_root=data_root,
        repair=repair,
        progress=progress,
    ):
        return [dict(record) for record in previous_records]  # type: ignore[arg-type]
    archive_path = data_root / str(archive_record["local_path"])
    staging = paths.uniprot_extracted.with_name(
        paths.uniprot_extracted.name + ".extracting"
    )
    if staging.exists():
        if not repair:
            raise AcquisitionError(
                f"incomplete extraction exists: {staging}; rerun with --repair"
            )
        _backup_conflict(staging, progress=progress)
    staging.mkdir(parents=True)
    try:
        with tarfile.open(archive_path, "r:*") as archive:
            members = tuple(archive.getmembers())
            for member in members:
                _safe_archive_name(member.name)
                if member.issym() or member.islnk():
                    raise AcquisitionError(
                        f"links are not permitted in UniProt archive: {member.name}"
                    )
            swiss = _select_tar_member(
                members, config.swissprot_member, "sprot"
            )
            trembl = _select_tar_member(members, config.trembl_member, "trembl")
            if swiss.name == trembl.name:
                raise AcquisitionError("Swiss-Prot and TrEMBL resolved to one member")
            selected = (("uniprot_swissprot", swiss), ("uniprot_trembl", trembl))
            records: list[dict[str, Any]] = []
            for key, member in selected:
                safe_name = _safe_archive_name(member.name)
                output = staging.joinpath(*safe_name.parts)
                stream = archive.extractfile(member)
                if stream is None:
                    raise AcquisitionError(f"cannot read archive member {member.name}")
                with stream:
                    _copy_stream(stream, output)
                final = paths.uniprot_extracted.joinpath(*safe_name.parts)
                records.append(
                    _artifact_record(
                        key=key,
                        url=str(archive_record["source_url"]),
                        path=final,
                        data_root=data_root,
                        sha256=_sha256(output),
                        retrieved_at=str(archive_record["retrieval_timestamp"]),
                        archive_member=member.name,
                        source_artifact=str(archive_record["artifact"]),
                        content_path=output,
                    )
                )
        if paths.uniprot_extracted.exists():
            paths.uniprot_extracted.rmdir()
        os.replace(staging, paths.uniprot_extracted)
        return records
    except (OSError, tarfile.TarError) as error:
        if isinstance(error, AcquisitionError):
            raise
        raise AcquisitionError(
            f"cannot extract UniProt archive {archive_path}: {error}"
        ) from error


def _zip_member_is_symlink(info: zipfile.ZipInfo) -> bool:
    return ((info.external_attr >> 16) & 0o170000) == 0o120000


def _extract_taxonomy(
    archive_record: Mapping[str, Any],
    paths: SnapshotPaths,
    data_root: Path,
    *,
    previous_records: object,
    repair: bool,
    progress: Progress | None,
) -> list[dict[str, Any]]:
    if _prepare_extraction_directory(
        paths.taxonomy_extracted,
        reusable_records=previous_records,
        data_root=data_root,
        repair=repair,
        progress=progress,
    ):
        return [dict(record) for record in previous_records]  # type: ignore[arg-type]
    archive_path = data_root / str(archive_record["local_path"])
    staging = paths.taxonomy_extracted.with_name(
        paths.taxonomy_extracted.name + ".extracting"
    )
    if staging.exists():
        if not repair:
            raise AcquisitionError(
                f"incomplete extraction exists: {staging}; rerun with --repair"
            )
        _backup_conflict(staging, progress=progress)
    staging.mkdir(parents=True)
    try:
        with zipfile.ZipFile(archive_path) as archive:
            infos = archive.infolist()
            safe_infos: list[tuple[zipfile.ZipInfo, PurePosixPath]] = []
            normalized_names: set[PurePosixPath] = set()
            for info in infos:
                safe_name = _safe_archive_name(info.filename)
                if safe_name in normalized_names:
                    raise AcquisitionError(
                        f"duplicate normalized taxonomy archive path: {info.filename}"
                    )
                normalized_names.add(safe_name)
                if _zip_member_is_symlink(info):
                    raise AcquisitionError(
                        f"links are not permitted in taxonomy archive: {info.filename}"
                    )
                safe_infos.append((info, safe_name))
            basenames = {
                safe_name.name for info, safe_name in safe_infos if not info.is_dir()
            }
            missing = sorted(REQUIRED_TAXONOMY_MEMBERS - basenames)
            if missing:
                raise AcquisitionError(
                    "taxonomy archive is missing required members: "
                    + ", ".join(missing)
                )
            records: list[dict[str, Any]] = []
            for info, safe_name in safe_infos:
                output = staging.joinpath(*safe_name.parts)
                if info.is_dir():
                    output.mkdir(parents=True, exist_ok=True)
                    continue
                with archive.open(info) as source:
                    _copy_stream(source, output)
                final = paths.taxonomy_extracted.joinpath(*safe_name.parts)
                records.append(
                    _artifact_record(
                        key=f"taxonomy_member:{info.filename}",
                        url=str(archive_record["source_url"]),
                        path=final,
                        data_root=data_root,
                        sha256=_sha256(output),
                        retrieved_at=str(archive_record["retrieval_timestamp"]),
                        archive_member=info.filename,
                        source_artifact=str(archive_record["artifact"]),
                        content_path=output,
                    )
                )
        if paths.taxonomy_extracted.exists():
            paths.taxonomy_extracted.rmdir()
        os.replace(staging, paths.taxonomy_extracted)
        return sorted(records, key=lambda record: str(record["archive_member"]))
    except (OSError, RuntimeError, zipfile.BadZipFile) as error:
        if isinstance(error, AcquisitionError):
            raise
        raise AcquisitionError(
            f"cannot extract taxonomy archive {archive_path}: {error}"
        ) from error


def _load_manifest(path: Path) -> dict[str, Any] | None:
    manifest = _read_json(path)
    if manifest is None:
        return None
    if manifest.get("manifest_schema_version") != MANIFEST_SCHEMA_VERSION:
        raise AcquisitionError(f"unsupported manifest schema in {path}")
    return manifest


def _requested(snapshot: SnapshotConfig) -> dict[str, Any]:
    value: dict[str, Any] = {
        "goa_release": snapshot.goa.label,
        "taxonomy_snapshot_date": snapshot.taxonomy.label,
        "uniprot_mode": snapshot.uniprot.mode,
        "uniprot_release": snapshot.uniprot.release,
    }
    if snapshot.ontology:
        value["ontology_release"] = snapshot.ontology.label
    if snapshot.uniprot.swissprot_member:
        value["uniprot_swissprot_member"] = snapshot.uniprot.swissprot_member
    if snapshot.uniprot.trembl_member:
        value["uniprot_trembl_member"] = snapshot.uniprot.trembl_member
    return value


def _previous_artifact(
    manifest: Mapping[str, Any] | None, key: str
) -> Mapping[str, Any] | None:
    artifacts = manifest.get("artifacts") if manifest else None
    if not isinstance(artifacts, dict):
        return None
    value = artifacts.get(key)
    return value if isinstance(value, dict) else None


def acquire_snapshot(
    config: AcquisitionConfig,
    role: SnapshotRole,
    *,
    repair: bool = False,
    progress: Progress | None = None,
) -> AcquisitionResult:
    """Acquire one explicitly configured snapshot and write its manifest."""

    snapshot = config.snapshots[role]
    paths = create_snapshot_layout(config.data_root, role)
    previous = _load_manifest(paths.manifest)
    artifacts: dict[str, Any] = {}
    relationships: list[dict[str, str]] = []
    warnings = [
        "Combined UniProt FASTA generation is deferred: no reusable .dat-to-FASTA "
        "implementation exists in the active repository."
    ]
    warnings.extend(
        f"Source URL for {key} appears mutable; these retrieved bytes are frozen "
        "locally and will not be silently refreshed."
        for key, source in sorted(_expected_sources(snapshot).items())
        if any(marker in source.url.lower() for marker in MUTABLE_URL_MARKERS)
    )

    if snapshot.uniprot.mode == "archive":
        assert snapshot.uniprot.archive is not None
        archive_record = _acquire_raw(
            "uniprot_archive",
            snapshot.uniprot.archive,
            paths.uniprot_raw,
            config.data_root,
            previous=_previous_artifact(previous, "uniprot_archive"),
            repair=repair,
            progress=progress,
        )
        artifacts["uniprot_archive"] = archive_record
        prior_archive = _previous_artifact(previous, "uniprot_archive")
        previous_extracted = (
            previous.get("uniprot_extracted")
            if previous
            and prior_archive
            and previous.get("requested") == _requested(snapshot)
            and prior_archive.get("sha256") == archive_record["sha256"]
            else None
        )
        extracted = _extract_uniprot_archive(
            archive_record,
            snapshot.uniprot,
            paths,
            config.data_root,
            previous_records=previous_extracted,
            repair=repair,
            progress=progress,
        )
        for record in extracted:
            relationships.append(
                {
                    "input": "uniprot_archive",
                    "output": str(record["artifact"]),
                    "operation": "safe_archive_extraction",
                }
            )
        uniprot_extracted: list[dict[str, Any]] = extracted
    else:
        assert snapshot.uniprot.swissprot is not None
        assert snapshot.uniprot.trembl is not None
        for key, spec in (
            ("uniprot_swissprot", snapshot.uniprot.swissprot),
            ("uniprot_trembl", snapshot.uniprot.trembl),
        ):
            artifacts[key] = _acquire_raw(
                key,
                spec,
                paths.uniprot_raw,
                config.data_root,
                previous=_previous_artifact(previous, key),
                repair=repair,
                progress=progress,
            )
        uniprot_extracted = []

    artifacts["goa"] = _acquire_raw(
        "goa",
        snapshot.goa.source,
        paths.goa_raw,
        config.data_root,
        previous=_previous_artifact(previous, "goa"),
        repair=repair,
        progress=progress,
    )
    taxonomy_record = _acquire_raw(
        "taxonomy_archive",
        snapshot.taxonomy.source,
        paths.taxonomy_raw,
        config.data_root,
        previous=_previous_artifact(previous, "taxonomy_archive"),
        repair=repair,
        progress=progress,
    )
    artifacts["taxonomy_archive"] = taxonomy_record
    prior_taxonomy = _previous_artifact(previous, "taxonomy_archive")
    previous_taxonomy = (
        previous.get("taxonomy_extracted")
        if previous
        and prior_taxonomy
        and prior_taxonomy.get("sha256") == taxonomy_record["sha256"]
        else None
    )
    taxonomy_extracted = _extract_taxonomy(
        taxonomy_record,
        paths,
        config.data_root,
        previous_records=previous_taxonomy,
        repair=repair,
        progress=progress,
    )
    for record in taxonomy_extracted:
        relationships.append(
            {
                "input": "taxonomy_archive",
                "output": str(record["artifact"]),
                "operation": "safe_archive_extraction",
            }
        )
    if snapshot.ontology:
        artifacts["ontology"] = _acquire_raw(
            "ontology",
            snapshot.ontology.source,
            paths.ontology_raw,
            config.data_root,
            previous=_previous_artifact(previous, "ontology"),
            repair=repair,
            progress=progress,
        )

    unchanged = bool(
        previous
        and previous.get("requested") == _requested(snapshot)
        and previous.get("artifacts") == artifacts
        and previous.get("uniprot_extracted") == uniprot_extracted
        and previous.get("taxonomy_extracted") == taxonomy_extracted
    )
    acquisition_timestamp = (
        previous.get("acquisition_timestamp") if unchanged else _timestamp()
    )
    manifest: dict[str, Any] = {
        "acquisition_timestamp": acquisition_timestamp,
        "artifacts": artifacts,
        "derived": [],
        "manifest_schema_version": MANIFEST_SCHEMA_VERSION,
        "relationships": sorted(
            relationships, key=lambda item: (item["input"], item["output"])
        ),
        "requested": _requested(snapshot),
        "snapshot_role": role,
        "taxonomy_extracted": taxonomy_extracted,
        "tool": {
            "name": "probe.data_acquisition",
            "python": platform.python_version(),
            "version": _tool_version(),
        },
        "uniprot_extracted": uniprot_extracted,
        "warnings": warnings,
    }
    _write_json_atomic(paths.manifest, manifest)
    return AcquisitionResult(role, paths.manifest, manifest)


def acquire_configured_snapshots(
    config: AcquisitionConfig,
    *,
    roles: Iterable[SnapshotRole] = SNAPSHOT_ROLES,
    repair: bool = False,
    progress: Progress | None = None,
) -> tuple[AcquisitionResult, ...]:
    """Acquire selected roles after one target-filesystem preflight."""

    disk_space_preflight(config.data_root, config.minimum_free_bytes)
    return tuple(
        acquire_snapshot(config, role, repair=repair, progress=progress)
        for role in roles
    )


def _expected_sources(snapshot: SnapshotConfig) -> dict[str, SourceSpec]:
    values = {
        "goa": snapshot.goa.source,
        "taxonomy_archive": snapshot.taxonomy.source,
    }
    if snapshot.uniprot.mode == "archive":
        assert snapshot.uniprot.archive is not None
        values["uniprot_archive"] = snapshot.uniprot.archive
    else:
        assert snapshot.uniprot.swissprot is not None
        assert snapshot.uniprot.trembl is not None
        values["uniprot_swissprot"] = snapshot.uniprot.swissprot
        values["uniprot_trembl"] = snapshot.uniprot.trembl
    if snapshot.ontology:
        values["ontology"] = snapshot.ontology.source
    return values


def validate_snapshot(
    config: AcquisitionConfig, role: SnapshotRole
) -> tuple[str, ...]:
    """Validate manifest, source choices, files, and extractions without writes."""

    snapshot = config.snapshots[role]
    paths = snapshot_paths(config.data_root, role)
    manifest = _load_manifest(paths.manifest)
    if manifest is None:
        raise AcquisitionError(f"manifest does not exist: {paths.manifest}")
    if manifest.get("snapshot_role") != role:
        raise AcquisitionError(f"manifest role mismatch in {paths.manifest}")
    if manifest.get("requested") != _requested(snapshot):
        raise AcquisitionError(
            f"manifest release choices differ from configuration: {role}"
        )
    artifacts = manifest.get("artifacts")
    if not isinstance(artifacts, dict):
        raise AcquisitionError(f"manifest has no artifact table: {paths.manifest}")
    messages: list[str] = []
    for key, expected_source in sorted(_expected_sources(snapshot).items()):
        record = artifacts.get(key)
        if not isinstance(record, dict):
            raise AcquisitionError(f"manifest is missing artifact {key!r}")
        if record.get("source_url") != expected_source.url:
            raise AcquisitionError(f"source URL mismatch for {role}/{key}")
        if (
            expected_source.expected_sha256
            and record.get("sha256") != expected_source.expected_sha256
        ):
            raise AcquisitionError(f"configured checksum mismatch for {role}/{key}")
        local_path = record.get("local_path")
        if not isinstance(local_path, str):
            raise AcquisitionError(f"manifest path is invalid for {role}/{key}")
        path = config.data_root / local_path
        if not _record_matches_file(record, path):
            raise AcquisitionError(f"artifact checksum or size mismatch: {path}")
        messages.append(f"verified {role}/{key}: {path}")
    if snapshot.uniprot.mode == "archive" and not _valid_extraction_records(
        manifest.get("uniprot_extracted"), paths.uniprot_extracted, config.data_root
    ):
        raise AcquisitionError(f"invalid UniProt extraction for {role}")
    if not _valid_extraction_records(
        manifest.get("taxonomy_extracted"), paths.taxonomy_extracted, config.data_root
    ):
        raise AcquisitionError(f"invalid taxonomy extraction for {role}")
    taxonomy_names = {
        PurePosixPath(str(record["archive_member"])).name
        for record in manifest["taxonomy_extracted"]
    }
    missing = sorted(REQUIRED_TAXONOMY_MEMBERS - taxonomy_names)
    if missing:
        raise AcquisitionError(
            f"taxonomy extraction for {role} is missing: {', '.join(missing)}"
        )
    return tuple(messages)


def _format_bytes(value: int) -> str:
    units = ("B", "KiB", "MiB", "GiB", "TiB", "PiB")
    amount = float(value)
    for unit in units:
        if amount < 1024 or unit == units[-1]:
            return f"{amount:.2f} {unit}"
        amount /= 1024
    raise AssertionError("unreachable")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Acquire immutable ProBE start/end source datasets."
    )
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--snapshot", choices=SNAPSHOT_ROLES)
    parser.add_argument(
        "--check",
        action="store_true",
        help="validate configuration, manifests, and local files without downloads",
    )
    parser.add_argument(
        "--repair",
        action="store_true",
        help="preserve conflicting paths and redownload or re-extract",
    )
    arguments = parser.parse_args(argv)
    if arguments.check and arguments.repair:
        parser.error("--check and --repair cannot be used together")
    try:
        config = load_acquisition_config(arguments.config)
        disk = disk_space_preflight(config.data_root, config.minimum_free_bytes)
        print(
            f"target filesystem: {disk.filesystem_path} | "
            f"free: {_format_bytes(disk.free_bytes)} | "
            f"total: {_format_bytes(disk.total_bytes)}"
        )
        roles: tuple[SnapshotRole, ...] = (
            (arguments.snapshot,) if arguments.snapshot else SNAPSHOT_ROLES
        )
        if arguments.check:
            for role in roles:
                for message in validate_snapshot(config, role):
                    print(message)
            return 0
        for result in acquire_configured_snapshots(
            config,
            roles=roles,
            repair=arguments.repair,
            progress=print,
        ):
            print(f"manifest written: {result.manifest_path}")
        return 0
    except AcquisitionError as error:
        print(f"acquisition error: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
