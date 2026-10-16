"""Recover pending FULL-V2 UniProt metadata in one verified FASTA pass.

The canonical 01A_FULL bundle and 05D stores remain read-only. A recovered
record is not a GO donor and its taxonomy still requires Stage 02B audit.
"""

from __future__ import annotations

import csv
import hashlib
import json
import os
import sqlite3
import tempfile
from collections import Counter
from pathlib import Path

from probe.stage2c_evidence_bridge import REQUEST_FIELDS
from probe.stage2c_metadata_reuse import REUSED_FIELDS
from probe.workflow.artifacts import validate_bundle

SCHEMA = "stage2c-pending-full-v2-recovery-v1"
SEQUENCE_FIELDS = (
    "release",
    "rid",
    "candidate_accession",
    "full_v2_record_number",
    "sequence_sha256",
    "sequence",
)
SUMMARY_NAME = "2C_pending_full_recovery.summary.json"
METADATA_NAME = "2C_recovered_full_metadata.tsv"
SEQUENCES_NAME = "2C_recovered_full_sequences.tsv"
_ALLOWED_AA = frozenset(b"ACDEFGHIKLMNPQRSTVWYUO")


def _canonical_positive(value: str, label: str) -> int:
    if not value or not value.isascii() or not value.isdecimal():
        raise ValueError(f"Invalid {label}: {value!r}")
    parsed = int(value)
    if parsed == 0 or str(parsed) != value:
        raise ValueError(f"Noncanonical {label}: {value!r}")
    return parsed


def _load_requests(
    db: sqlite3.Connection, requests: Path, release: str
) -> tuple[int, int]:
    db.execute(
        "CREATE TABLE request ("
        "rid INTEGER PRIMARY KEY, accession TEXT NOT NULL, "
        "raw_ox TEXT NOT NULL)"
    )
    count = 0
    maximum = 0
    with requests.open("r", encoding="utf-8", newline="") as stream:
        reader = csv.DictReader(stream, delimiter="\t")
        fields = reader.fieldnames
        if fields is None or len(fields) != len(set(fields)):
            raise ValueError("Missing or duplicate pending-request TSV headers")
        if not set(REQUEST_FIELDS).issubset(fields):
            raise ValueError("Pending request missing required fields")
        for line, row in enumerate(reader, start=2):
            if None in row or any(value is None for value in row.values()):
                raise ValueError(f"Malformed request row {line}")
            rid = _canonical_positive(row["rid"], "RID")
            if (
                row["release"] != release
                or row["full_record_number"] != row["rid"]
                or row["request_reason"] != "PINNED_FULL_V2_METADATA_VERIFICATION"
            ):
                raise ValueError(f"Invalid provenance in request row {line}")
            for name in (
                "candidate_raw_ox",
                "candidate_resolved_taxid",
                "species_taxid",
                "target_reference_count",
            ):
                _canonical_positive(row[name], name)
            accession = row["candidate_accession"]
            if not accession or any(char.isspace() for char in accession):
                raise ValueError(f"Invalid accession in request row {line}")
            try:
                db.execute(
                    "INSERT INTO request VALUES (?, ?, ?)",
                    (rid, accession, row["candidate_raw_ox"]),
                )
            except sqlite3.IntegrityError as exc:
                raise ValueError(f"Duplicate pending RID {rid}") from exc
            maximum = max(maximum, rid)
            count += 1
    return count, maximum


def _manifest_fasta(manifest_path: Path, release: str) -> tuple[Path, str]:
    if manifest_path.name != "manifest.json" or manifest_path.is_symlink():
        raise ValueError("Expected a canonical 01A_FULL manifest path")
    manifest = validate_bundle(manifest_path.parent, deep=False)
    if (
        manifest.stage != "01A_FULL"
        or manifest.release_id != release
        or manifest.role not in {"START", "END"}
    ):
        raise ValueError("Wrong Stage 01A_FULL manifest/release")
    entries = [item for item in manifest.outputs if item.path == "01A_full_v2.fasta"]
    if len(entries) != 1 or not entries[0].sha256:
        raise ValueError("FULL FASTA missing pinned output SHA256")
    return manifest_path.parent / entries[0].path, entries[0].sha256


def _parse_header(header: bytes, ordinal: int) -> dict[str, str]:
    try:
        parts = header.decode("ascii").split()
    except UnicodeDecodeError as exc:
        raise ValueError(f"Non-ASCII FULL header at RID {ordinal}") from exc
    if not parts or not parts[0].startswith(">"):
        raise ValueError(f"Missing FASTA header at RID {ordinal}")
    identifier = parts[0][1:].split("|")
    if len(identifier) != 3 or not all(identifier):
        raise ValueError(f"Malformed UniProt identifier at RID {ordinal}")
    values = {"primary_accession": identifier[1], "entry": identifier[2]}
    for token in parts[1:]:
        if "=" not in token:
            raise ValueError(f"Malformed token at RID {ordinal}")
        key, value = token.split("=", 1)
        if not key or not value or key in values:
            raise ValueError(f"Duplicate or empty token at RID {ordinal}")
        values[key] = value
    for required in ("RID", "OX", "FRAG", "LEN", "PE", "SV"):
        if required not in values:
            raise ValueError(f"Missing {required} at RID {ordinal}")
    if _canonical_positive(values["RID"], "header RID") != ordinal:
        raise ValueError(f"FULL record ordinal/RID mismatch at {ordinal}")
    return values


def _record(
    *,
    release: str,
    ordinal: int,
    header: bytes,
    sequence: bytes,
    accession: str,
    raw_ox: str,
) -> tuple[dict[str, str], dict[str, str]]:
    facts = _parse_header(header, ordinal)
    try:
        sequence_text = sequence.decode("ascii")
    except UnicodeDecodeError as exc:
        raise ValueError(f"Non-ASCII sequence at RID {ordinal}") from exc
    if (
        facts["primary_accession"] != accession
        or facts["OX"] != raw_ox
        or facts["FRAG"] != "0"
    ):
        raise ValueError(f"FULL candidate metadata conflict at RID {ordinal}")
    if (
        not sequence_text
        or any(residue not in _ALLOWED_AA for residue in sequence)
        or _canonical_positive(facts["LEN"], "LEN") != len(sequence)
    ):
        raise ValueError(f"Invalid FULL candidate sequence at RID {ordinal}")
    digest = hashlib.sha256(sequence).hexdigest()
    reused_fields = {
        "release": release,
        "rid": str(ordinal),
        "candidate_accession": accession,
        "candidate_raw_ox": raw_ox,
        "entry": facts["entry"],
        "frag": "0",
        "sequence_length": facts["LEN"],
        "pe": facts["PE"],
        "sv": facts["SV"],
        "sec": facts.get("SEC", ""),
        "gene": facts.get("GENE", ""),
        "sequence_sha256": digest,
        "source_full_v2_record_number": str(ordinal),
        "taxonomy_state": "REQUIRES_02B_REVALIDATION",
        "source_state": "PINNED_FULL_V2_METADATA_RECOVERED",
    }
    sequence_fields = {
        "release": release,
        "rid": str(ordinal),
        "candidate_accession": accession,
        "full_v2_record_number": str(ordinal),
        "sequence_sha256": digest,
        "sequence": sequence_text,
    }
    return reused_fields, sequence_fields


def _stream_full(
    *,
    fasta: Path,
    expected_sha256: str,
    db: sqlite3.Connection,
    release: str,
    metadata_writer: csv.DictWriter,
    sequence_writer: csv.DictWriter,
) -> dict[str, int]:
    cursor = db.execute("SELECT rid, accession, raw_ox FROM request ORDER BY rid")
    pending = cursor.fetchone()
    scanned = 0
    matched = 0
    total_residues = 0
    digest = hashlib.sha256()
    with fasta.open("rb", buffering=8 * 1024 * 1024) as stream:
        while header := stream.readline():
            sequence = stream.readline()
            if not sequence:
                raise ValueError("Truncated two-line FULL V2 FASTA record")
            digest.update(header)
            digest.update(sequence)
            scanned += 1
            if not header.startswith(b">") or not sequence.endswith(b"\n"):
                raise ValueError(f"Malformed FASTA record {scanned}")
            if not sequence or sequence.startswith(b">"):
                raise ValueError(f"Invalid FASTA sequence line {scanned}")
            raw_rid = header.partition(b" RID=")[2].partition(b" ")[0]
            if raw_rid != str(scanned).encode("ascii"):
                raise ValueError(f"Noncanonical FULL RID/ordinal at {scanned}")
            if pending is None or pending[0] != scanned:
                continue
            seq = sequence.removesuffix(b"\n").removesuffix(b"\r")
            meta, full = _record(
                release=release,
                ordinal=scanned,
                header=header.removesuffix(b"\n").removesuffix(b"\r"),
                sequence=seq,
                accession=pending[1],
                raw_ox=pending[2],
            )
            metadata_writer.writerow(meta)
            sequence_writer.writerow(full)
            total_residues += len(seq)
            matched += 1
            pending = cursor.fetchone()
    if digest.hexdigest() != expected_sha256:
        raise ValueError("Pinned 01A_FULL FASTA SHA256 mismatch")
    if pending is not None:
        raise ValueError(f"Pending FULL RID {pending[0]} does not exist")
    return {
        "full_v2_records_scanned": scanned,
        "recovered_unique_rids": matched,
        "recovered_sequence_residues": total_residues,
    }


def recover_pending_full_metadata(
    *,
    release: str,
    pending_requests: Path,
    full_v2_manifest: Path,
    output_dir: Path,
) -> dict:
    """Publish verified metadata and sequences without changing upstream stores.

    This explicit execution reads the complete, pinned FULL V2 FASTA once.
    No scanning happens merely by importing the module or running the CLI plan.
    """
    if not release or not output_dir.parent.is_dir():
        raise ValueError("Missing release or output parent")
    if output_dir.exists() or output_dir.is_symlink():
        raise FileExistsError("Output directory is occupied")
    if not pending_requests.is_file() or pending_requests.is_symlink():
        raise ValueError("Missing or symlinked pending-request TSV")
    fasta, pinned_sha256 = _manifest_fasta(full_v2_manifest, release)
    if pending_requests.resolve() == fasta.resolve():
        raise ValueError("Request input cannot be FULL FASTA")
    lock = output_dir.with_name(f".{output_dir.name}.publish-lock")
    lock.mkdir()
    try:
        return _recover_locked(
            release=release,
            pending_requests=pending_requests,
            fasta=fasta,
            pinned_sha256=pinned_sha256,
            output_dir=output_dir,
        )
    finally:
        lock.rmdir()


def _recover_locked(
    *,
    release: str,
    pending_requests: Path,
    fasta: Path,
    pinned_sha256: str,
    output_dir: Path,
) -> dict:
    with tempfile.TemporaryDirectory(
        prefix=".probe-2c-full-recovery-", dir=output_dir.parent
    ) as temporary:
        base = Path(temporary)
        db = sqlite3.connect(str(base / "request.sqlite3"))
        try:
            requested, max_rid = _load_requests(db, pending_requests, release)
            bundle = base / "bundle"
            bundle.mkdir()
            with (
                (bundle / METADATA_NAME).open(
                    "x", encoding="utf-8", newline=""
                ) as meta_out,
                (bundle / SEQUENCES_NAME).open(
                    "x", encoding="utf-8", newline=""
                ) as seq_out,
            ):
                meta_writer = csv.DictWriter(
                    meta_out,
                    fieldnames=REUSED_FIELDS,
                    delimiter="\t",
                    lineterminator="\n",
                )
                seq_writer = csv.DictWriter(
                    seq_out,
                    fieldnames=SEQUENCE_FIELDS,
                    delimiter="\t",
                    lineterminator="\n",
                )
                meta_writer.writeheader()
                seq_writer.writeheader()
                counts = Counter(
                    {"requested_unique_rids": requested, "max_requested_rid": max_rid}
                )
                if requested:
                    counts.update(
                        _stream_full(
                            fasta=fasta,
                            expected_sha256=pinned_sha256,
                            db=db,
                            release=release,
                            metadata_writer=meta_writer,
                            sequence_writer=seq_writer,
                        )
                    )
                else:
                    counts.update(
                        full_v2_records_scanned=0,
                        recovered_unique_rids=0,
                        recovered_sequence_residues=0,
                    )
            summary = {
                "schema": SCHEMA,
                "release": release,
                "counts": dict(sorted(counts.items())),
                "input_full_v2_sha256": pinned_sha256,
                "full_fasta_sha256_verified": requested > 0,
                "full_fasta_scanned": requested > 0,
                "go_donor_decisions": 0,
                "complete_stage05a2_replacement": False,
                "taxonomy_verified_for_recovered_rows": False,
            }
            (bundle / SUMMARY_NAME).write_text(
                json.dumps(summary, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            if output_dir.exists() or output_dir.is_symlink():
                raise FileExistsError("Output occupied during processing")
            os.replace(bundle, output_dir)
            return summary
        finally:
            db.close()


def preflight_full_recovery(
    *, release: str, pending_requests: Path, full_v2_manifest: Path
) -> dict:
    """Inspect pinned metadata and pending RID counts without FASTA scanning."""
    if not pending_requests.is_file() or pending_requests.is_symlink():
        raise ValueError("Missing or symlinked pending-request TSV")
    fasta, sha256 = _manifest_fasta(full_v2_manifest, release)
    with tempfile.TemporaryDirectory(prefix="probe-2c-full-plan-") as temporary:
        db = sqlite3.connect(str(Path(temporary) / "request.sqlite3"))
        try:
            count, max_rid = _load_requests(db, pending_requests, release)
        finally:
            db.close()
    return {
        "schema": SCHEMA,
        "release": release,
        "requested_unique_rids": count,
        "max_requested_rid": max_rid,
        "full_fasta_size_bytes": fasta.stat().st_size,
        "expected_full_v2_sha256": sha256,
        "full_fasta_scanned": False,
        "go_donor_decisions": 0,
    }
