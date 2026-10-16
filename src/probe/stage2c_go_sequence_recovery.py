"""Recover pending Stage 2C GO-positive sequences from pinned Stage 01C.

This is read-only against 01C and every canonical scientific stage. GO FASTA
record ordinal and UniProt FULL RID are deliberately treated as distinct IDs.
Recovery verifies sequence bytes, not GO donor eligibility or taxonomy policy.
"""

from __future__ import annotations

import csv
import hashlib
import json
import os
import sqlite3
import tempfile
from pathlib import Path

from probe.stage2c_sequence_coverage import (
    ANNEX_FIELDS,
    COVERAGE_NAME,
    PENDING_FIELDS,
    PENDING_NAME,
    SUMMARY_NAME_OUT,
)
from probe.workflow.artifacts import validate_bundle

SCHEMA = "stage2c-go-pending-sequence-recovery-v1"
ANNEX_NAME = "2C_go_positive_sequence_annex.tsv"
SUMMARY_NAME = "2C_go_positive_sequence_recovery.summary.json"
# Preserve UniProt ambiguity codes literally; no wildcard matching is implied.
_ALLOWED = frozenset(b"ACDEFGHIKLMNPQRSTVWYUOBJXZ")


def _sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while data := source.read(8 * 1024 * 1024):
            digest.update(data)
    return digest.hexdigest()


def _positive(value: str, label: str) -> int:
    if not value or not value.isascii() or not value.isdecimal():
        raise ValueError(f"Invalid {label}: {value!r}")
    number = int(value)
    if number == 0 or str(number) != value:
        raise ValueError(f"Noncanonical {label}: {value!r}")
    return number


def _rows(path: Path, required: set[str]):
    with path.open(encoding="utf-8", newline="") as source:
        reader = csv.DictReader(source, delimiter="\t")
        header = reader.fieldnames
        if header is None or len(header) != len(set(header)):
            raise ValueError(f"Invalid TSV header: {path}")
        if not required.issubset(header):
            raise ValueError(f"Missing TSV fields: {path}")
        for line, row in enumerate(reader, 2):
            if None in row or any(value is None for value in row.values()):
                raise ValueError(f"Malformed TSV row: {path}:{line}")
            yield row


def _pinned_01c(manifest_path: Path, release: str) -> tuple[Path, str]:
    if manifest_path.name != "manifest.json" or manifest_path.is_symlink():
        raise ValueError("Expected Stage 01C manifest.json")
    manifest = validate_bundle(manifest_path.parent, deep=False)
    if (
        manifest.stage != "01C"
        or manifest.release_id != release
        or manifest.role not in {"START", "END"}
    ):
        raise ValueError("Wrong Stage 01C bundle/release")
    items = [
        item for item in manifest.outputs if item.path == "01C_go_positive_v2.fasta"
    ]
    if len(items) != 1 or not items[0].sha256:
        raise ValueError("Missing pinned GO-positive FASTA SHA256")
    return manifest_path.parent / items[0].path, items[0].sha256


def _requests(db: sqlite3.Connection, pending: Path, release: str) -> int:
    db.execute(
        "CREATE TABLE request ("
        "ordinal INTEGER PRIMARY KEY, rid INTEGER UNIQUE NOT NULL, "
        "accession TEXT NOT NULL, ox TEXT NOT NULL)"
    )
    count = 0
    for row in _rows(pending, set(PENDING_FIELDS)):
        if (
            row["release"] != release
            or row["reason"] != "GO_POSITIVE_SEQUENCE_NOT_MATERIALIZED"
        ):
            raise ValueError("Invalid GO request release or provenance")
        ordinal = _positive(row["go_positive_record_number"], "GO ordinal")
        rid = _positive(row["rid"], "UniProt RID")
        _positive(row["raw_ox"], "candidate OX")
        _positive(row["new_primary_reference_count"], "reference count")
        accession = row["candidate_accession"]
        if not accession or any(value.isspace() for value in accession):
            raise ValueError("Invalid requested accession")
        try:
            db.execute(
                "INSERT INTO request VALUES (?, ?, ?, ?)",
                (ordinal, rid, accession, row["raw_ox"]),
            )
        except sqlite3.IntegrityError as exc:
            raise ValueError("Duplicate GO ordinal or RID in requests") from exc
        count += 1
    return count


def _header(header: bytes, ordinal: int) -> dict[str, str]:
    try:
        parts = header.decode("ascii").split()
    except UnicodeDecodeError as exc:
        raise ValueError(f"Non-ASCII GO header at {ordinal}") from exc
    if not parts or not parts[0].startswith(">"):
        raise ValueError(f"Missing GO FASTA header at ordinal {ordinal}")
    identifier = parts[0][1:].split("|")
    if len(identifier) != 3 or not all(identifier):
        raise ValueError(f"Invalid GO FASTA ID at ordinal {ordinal}")
    values = {"primary_accession": identifier[1], "entry": identifier[2]}
    for token in parts[1:]:
        if "=" not in token:
            raise ValueError(f"Malformed GO header token at {ordinal}")
        key, value = token.split("=", 1)
        if not key or not value or key in values:
            raise ValueError(f"Repeated/empty GO header token at {ordinal}")
        values[key] = value
    for key in ("RID", "OX", "FRAG", "LEN", "PE", "SV"):
        if key not in values:
            raise ValueError(f"Missing GO FASTA {key} at {ordinal}")
    return values


def _scan(
    *,
    fasta: Path,
    expected_hash: str,
    db: sqlite3.Connection,
    release: str,
    writer: csv.DictWriter,
) -> dict[str, int]:
    cursor = db.execute(
        "SELECT ordinal, rid, accession, ox FROM request ORDER BY ordinal"
    )
    pending = cursor.fetchone()
    scanned = 0
    found = 0
    digest = hashlib.sha256()
    with fasta.open("rb", buffering=8 * 1024 * 1024) as source:
        while header := source.readline():
            sequence = source.readline()
            if not sequence:
                raise ValueError("Truncated two-line Stage 01C FASTA")
            digest.update(header)
            digest.update(sequence)
            scanned += 1
            if not header.startswith(b">") or not sequence.endswith(b"\n"):
                raise ValueError("Malformed GO FASTA record")
            if sequence.startswith(b">"):
                raise ValueError("Missing GO sequence line")
            if pending is None or scanned != pending[0]:
                continue
            info = _header(header, scanned)
            residues = sequence.removesuffix(b"\n").removesuffix(b"\r")
            if (
                _positive(info["RID"], "FASTA RID") != pending[1]
                or info["primary_accession"] != pending[2]
                or info["OX"] != pending[3]
                or info["FRAG"] != "0"
                or not residues
                or any(residue not in _ALLOWED for residue in residues)
                or _positive(info["LEN"], "FASTA LEN") != len(residues)
            ):
                raise ValueError("GO-positive RID, metadata or sequence conflict")
            _positive(info["PE"], "PE")
            _positive(info["SV"], "SV")
            writer.writerow(
                {
                    "release": release,
                    "rid": str(pending[1]),
                    "primary_accession": info["primary_accession"],
                    "entry": info["entry"],
                    "raw_ox": info["OX"],
                    "frag": "0",
                    "sequence_length": str(len(residues)),
                    "pe": info["PE"],
                    "sv": info["SV"],
                    "sec": info.get("SEC", ""),
                    "gene": info.get("GENE", ""),
                    "sequence": residues.decode("ascii"),
                    "sequence_sha256": hashlib.sha256(residues).hexdigest(),
                    "source_state": "PINNED_GO_POSITIVE_01C_RECOVERED",
                }
            )
            found += 1
            pending = cursor.fetchone()
    if digest.hexdigest() != expected_hash:
        raise ValueError("Pinned Stage 01C FASTA SHA256 mismatch")
    if pending is not None:
        raise ValueError("Missing requested GO ordinal in pinned FASTA")
    return {"fasta_records_scanned": scanned, "recovered_unique_rids": found}


def preflight_pending_go(
    *,
    release: str,
    coverage_dir: Path,
    go_manifest: Path,
) -> dict:
    """Check pinned provenance and request consistency without reading FASTA."""
    summary_file = coverage_dir / SUMMARY_NAME_OUT
    source = json.loads(summary_file.read_text(encoding="utf-8"))
    if (
        source.get("schema") != "stage2c-sequence-coverage-v1"
        or source.get("release") != release
        or source.get("go_donor_decisions") != 0
    ):
        raise ValueError("Unverified GO-positive coverage summary")
    hashes = source.get("output_sha256", {})
    if not isinstance(hashes, dict):
        raise ValueError("Invalid GO-positive coverage checksums")
    for name in (PENDING_NAME, COVERAGE_NAME):
        if hashes.get(name) != _sha(coverage_dir / name):
            raise ValueError(f"GO-positive coverage checksum mismatch: {name}")
    fasta, expected_hash = _pinned_01c(go_manifest, release)
    with tempfile.TemporaryDirectory(prefix="probe-2c-go-preflight-") as tmp:
        db = sqlite3.connect(Path(tmp) / "request.sqlite3")
        try:
            count = _requests(db, coverage_dir / PENDING_NAME, release)
        finally:
            db.close()
    return {
        "release": release,
        "mode": "preflight_only",
        "requested_unique_rids": count,
        "pinned_fasta_size_bytes": fasta.stat().st_size,
        "pinned_fasta_sha256": expected_hash,
        "go_fasta_scanned": False,
    }


def recover_pending_go(
    *,
    release: str,
    coverage_dir: Path,
    go_manifest: Path,
    output_dir: Path,
) -> dict:
    """Publish a verified GO-only 05D annex; never write canonical stages."""
    if not release or not output_dir.parent.is_dir():
        raise ValueError("Invalid release or output parent")
    if output_dir.exists() or output_dir.is_symlink():
        raise FileExistsError("GO sequence recovery output is occupied")
    if coverage_dir.is_symlink() or not coverage_dir.is_dir():
        raise ValueError("Missing Stage 2C sequence coverage directory")
    if any(
        output_dir.resolve().is_relative_to(folder.resolve())
        for folder in (coverage_dir, go_manifest.parent)
    ):
        raise ValueError("Output cannot be inside an input publication")
    summary_file = coverage_dir / SUMMARY_NAME_OUT
    coverage_summary = json.loads(summary_file.read_text(encoding="utf-8"))
    if (
        coverage_summary.get("schema") != "stage2c-sequence-coverage-v1"
        or coverage_summary.get("release") != release
        or coverage_summary.get("go_donor_decisions") != 0
    ):
        raise ValueError("Unverified Stage 2C sequence coverage summary")
    hashes = coverage_summary.get("output_sha256", {})
    if not isinstance(hashes, dict):
        raise ValueError("Missing Stage 2C coverage output hashes")
    for name in (COVERAGE_NAME, PENDING_NAME):
        if hashes.get(name) != _sha(coverage_dir / name):
            raise ValueError(f"Sequence coverage checksum mismatch: {name}")
    fasta, expected_hash = _pinned_01c(go_manifest, release)
    lock = output_dir.with_name(f".{output_dir.name}.publish-lock")
    lock.mkdir()
    try:
        with tempfile.TemporaryDirectory(
            prefix=".probe-2c-go-recovery-", dir=output_dir.parent
        ) as tmp:
            work = Path(tmp)
            db = sqlite3.connect(work / "requests.sqlite3")
            try:
                count = _requests(db, coverage_dir / PENDING_NAME, release)
                with (work / ANNEX_NAME).open(
                    "x", encoding="utf-8", newline=""
                ) as stream:
                    writer = csv.DictWriter(
                        stream,
                        fieldnames=ANNEX_FIELDS,
                        delimiter="\t",
                        lineterminator="\n",
                    )
                    writer.writeheader()
                    if count:
                        stats = _scan(
                            fasta=fasta,
                            expected_hash=expected_hash,
                            db=db,
                            release=release,
                            writer=writer,
                        )
                    else:
                        stats = {
                            "fasta_records_scanned": 0,
                            "recovered_unique_rids": 0,
                        }
                if count != stats["recovered_unique_rids"]:
                    raise ValueError("GO recovery incomplete")
                summary = {
                    "schema": SCHEMA,
                    "release": release,
                    "counts": {
                        "requested_unique_rids": count,
                        **stats,
                    },
                    "go_fasta_scanned": count > 0,
                    "go_fasta_sha256_verified": count > 0,
                    "production_05d_replacement": False,
                    "taxonomy_audit_05c2_pending": True,
                    "go_donor_decisions": 0,
                    "input_sha256": {
                        "coverage_summary": _sha(summary_file),
                        "01c_manifest": _sha(go_manifest),
                        "01c_fasta_pinned": expected_hash,
                    },
                    "output_sha256": {ANNEX_NAME: _sha(work / ANNEX_NAME)},
                }
                (work / SUMMARY_NAME).write_text(
                    json.dumps(summary, sort_keys=True, indent=2) + "\n",
                    encoding="utf-8",
                )
                bundle = work / "bundle"
                bundle.mkdir()
                for name in (ANNEX_NAME, SUMMARY_NAME):
                    os.replace(work / name, bundle / name)
                if output_dir.exists() or output_dir.is_symlink():
                    raise FileExistsError("GO recovery destination became occupied")
                os.replace(bundle, output_dir)
                return summary
            finally:
                db.close()
    finally:
        lock.rmdir()
