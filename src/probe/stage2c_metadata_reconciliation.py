"""Reconcile Stage 2C FULL metadata from pinned 05D and FULL V2 sources.

Input publications are read-only. All RID/release/provenance checks are
fail-closed; no donor eligibility or GO annotation is inferred here.
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
from probe.stage2c_full_metadata_recovery import (
    METADATA_NAME,
    SEQUENCE_FIELDS,
    SEQUENCES_NAME,
    SUMMARY_NAME,
)
from probe.stage2c_metadata_reuse import REUSED_FIELDS, STORE_FIELDS

SCHEMA = "stage2c-full-metadata-reconciliation-v1"
METADATA_OUTPUT = "2C_all_full_metadata.tsv"
SEQUENCES_OUTPUT = "2C_all_full_sequences.tsv"
SUMMARY_OUTPUT = "2C_all_full_metadata.summary.json"
UNIFIED_SEQUENCE_FIELDS = (*SEQUENCE_FIELDS, "source_state")
REUSE_STATE = "CANONICAL_05D_METADATA_REUSED"
RECOVERY_STATE = "PINNED_FULL_V2_METADATA_RECOVERED"
TAXONOMY_STATE = "REQUIRES_02B_REVALIDATION"
REQUEST_REASON = "PINNED_FULL_V2_METADATA_VERIFICATION"


def _positive(raw: str, label: str) -> int:
    if not raw or not raw.isascii() or not raw.isdecimal():
        raise ValueError(f"Invalid {label}: {raw!r}")
    value = int(raw)
    if value < 1 or str(value) != raw:
        raise ValueError(f"Noncanonical {label}: {raw!r}")
    return value


def _read(path: Path, required: tuple[str, ...]):
    if not path.is_file():
        raise FileNotFoundError(path)
    with path.open(encoding="utf-8", newline="") as stream:
        reader = csv.DictReader(stream, delimiter="\t")
        fields = reader.fieldnames
        if fields is None or len(fields) != len(set(fields)):
            raise ValueError(f"Malformed TSV header: {path}")
        if not set(required).issubset(fields):
            raise ValueError(f"Missing TSV columns: {path}")
        for line, row in enumerate(reader, start=2):
            if None in row or any(value is None for value in row.values()):
                raise ValueError(f"Malformed TSV row: {path}:{line}")
            yield row


def _summary(path: Path, schema: str, release: str) -> dict:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"Malformed source summary: {path}")
    if data.get("schema") != schema or data.get("release") != release:
        raise ValueError(f"Wrong source summary schema/release: {path}")
    if data.get("go_donor_decisions") != 0:
        raise ValueError("Upstream metadata summary grants donor decisions")
    if not isinstance(data.get("counts"), dict):
        raise ValueError("Source summary lacks counts")
    return data


def _expect_count(summary: dict, name: str, expected: int) -> None:
    if summary["counts"].get(name, 0) != expected:
        raise ValueError(f"Source summary count mismatch: {name}")


def _load_requests(db: sqlite3.Connection, path: Path, release: str) -> int:
    count = 0
    for row in _read(path, REQUEST_FIELDS):
        rid = _positive(row["rid"], "request RID")
        if (
            row["release"] != release
            or row["full_record_number"] != str(rid)
            or row["request_reason"] != REQUEST_REASON
        ):
            raise ValueError("Invalid original FULL request provenance")
        for name in (
            "candidate_raw_ox",
            "candidate_resolved_taxid",
            "species_taxid",
            "target_reference_count",
        ):
            _positive(row[name], name)
        if not row["candidate_accession"] or any(
            char.isspace() for char in row["candidate_accession"]
        ):
            raise ValueError("Invalid requested accession")
        try:
            db.execute(
                "INSERT INTO r(rid, accession, raw_ox, request_json) "
                "VALUES (?, ?, ?, ?)",
                (
                    rid,
                    row["candidate_accession"],
                    row["candidate_raw_ox"],
                    json.dumps(row, sort_keys=True),
                ),
            )
        except sqlite3.IntegrityError as exc:
            raise ValueError(f"Duplicate original request RID {rid}") from exc
        count += 1
    return count


def _validate_metadata(
    row: dict[str, str],
    requested: tuple[str, str],
    release: str,
    state: str,
) -> None:
    rid = _positive(row["rid"], "metadata RID")
    if (
        row["release"] != release
        or row["source_full_v2_record_number"] != str(rid)
        or row["candidate_accession"] != requested[0]
        or row["candidate_raw_ox"] != requested[1]
        or row["frag"] != "0"
        or row["taxonomy_state"] != TAXONOMY_STATE
        or row["source_state"] != state
    ):
        raise ValueError(f"Metadata identity/provenance conflict at RID {rid}")
    _positive(row["sequence_length"], "sequence length")
    for name in ("pe", "sv"):
        _positive(row[name], name)
    if not row["entry"]:
        raise ValueError(f"Missing UniProt entry at RID {rid}")
    digest = row["sequence_sha256"]
    if len(digest) != 64 or any(char not in "0123456789abcdef" for char in digest):
        raise ValueError(f"Invalid sequence SHA256 at RID {rid}")


def _attach_metadata(
    db: sqlite3.Connection,
    path: Path,
    release: str,
    state: str,
    expected_origin: str,
) -> int:
    count = 0
    for row in _read(path, REUSED_FIELDS):
        rid = _positive(row["rid"], "metadata RID")
        original = db.execute(
            "SELECT accession, raw_ox, origin FROM r WHERE rid=?", (rid,)
        ).fetchone()
        if original is None or original[2] != expected_origin:
            raise ValueError(f"Unexpected or overlapping metadata RID {rid}")
        _validate_metadata(row, original[:2], release, state)
        db.execute(
            "UPDATE r SET origin=?, metadata=? WHERE rid=?",
            (state, json.dumps(row, sort_keys=True), rid),
        )
        count += 1
    return count


def _attach_pending(
    db: sqlite3.Connection,
    path: Path,
    release: str,
) -> int:
    count = 0
    for row in _read(path, REQUEST_FIELDS):
        rid = _positive(row["rid"], "pending RID")
        original = db.execute(
            "SELECT request_json, origin FROM r WHERE rid=?", (rid,)
        ).fetchone()
        if original is None or original[1] is not None:
            raise ValueError(f"Unexpected, overlapping or duplicate pending RID {rid}")
        if row["release"] != release or row != json.loads(original[0]):
            raise ValueError(f"Pending request conflict at RID {rid}")
        db.execute("UPDATE r SET origin='PENDING' WHERE rid=?", (rid,))
        count += 1
    missing = db.execute("SELECT COUNT(*) FROM r WHERE origin IS NULL").fetchone()
    if missing[0]:
        raise ValueError("Reused and pending sets do not cover all requests")
    return count


def _attach_recovered_sequences(
    db: sqlite3.Connection,
    path: Path,
    release: str,
) -> int:
    count = 0
    for row in _read(path, SEQUENCE_FIELDS):
        rid = _positive(row["rid"], "recovered sequence RID")
        existing = db.execute(
            "SELECT accession, metadata, sequence FROM r WHERE rid=?", (rid,)
        ).fetchone()
        if (
            existing is None
            or existing[1] is None
            or existing[2] is not None
            or json.loads(existing[1])["source_state"] != RECOVERY_STATE
        ):
            raise ValueError(f"Unexpected/duplicate recovered sequence RID {rid}")
        metadata = json.loads(existing[1])
        sequence = row["sequence"]
        digest = hashlib.sha256(sequence.encode("ascii")).hexdigest()
        if (
            row["release"] != release
            or row["candidate_accession"] != existing[0]
            or row["full_v2_record_number"] != str(rid)
            or row["sequence_sha256"] != metadata["sequence_sha256"]
            or digest != metadata["sequence_sha256"]
            or len(sequence) != int(metadata["sequence_length"])
        ):
            raise ValueError(f"Recovered sequence conflict at RID {rid}")
        db.execute("UPDATE r SET sequence=? WHERE rid=?", (sequence, rid))
        count += 1
    return count


def _attach_reused_sequences(
    db: sqlite3.Connection,
    store: Path,
    release: str,
) -> int:
    expected = db.execute(
        "SELECT COUNT(*) FROM r WHERE origin=?", (REUSE_STATE,)
    ).fetchone()[0]
    if not expected:
        return 0
    count = 0
    for record in _read(store, STORE_FIELDS):
        if record["release"] != release:
            raise ValueError("Canonical 05D store release mismatch")
        rid = _positive(record["rid"], "canonical store RID")
        if record["full_v2_record_number"] != str(rid):
            raise ValueError("Canonical 05D store ordinal mismatch")
        existing = db.execute(
            "SELECT accession, raw_ox, origin, metadata, sequence FROM r WHERE rid=?",
            (rid,),
        ).fetchone()
        if existing is None or existing[2] != REUSE_STATE:
            continue
        if existing[4] is not None:
            raise ValueError(f"Duplicate canonical 05D RID {rid}")
        metadata = json.loads(existing[3])
        sequence = record["sequence"]
        digest = hashlib.sha256(sequence.encode("ascii")).hexdigest()
        corresponding = (
            ("primary_accession", "candidate_accession"),
            ("raw_ox", "candidate_raw_ox"),
            ("entry", "entry"),
            ("frag", "frag"),
            ("sequence_length", "sequence_length"),
            ("pe", "pe"),
            ("sv", "sv"),
            ("sec", "sec"),
            ("gene", "gene"),
        )
        if (
            any(record[a] != metadata[b] for a, b in corresponding)
            or record["primary_accession"] != existing[0]
            or record["raw_ox"] != existing[1]
            or len(sequence) != int(metadata["sequence_length"])
            or digest != metadata["sequence_sha256"]
        ):
            raise ValueError(f"Canonical 05D sequence conflict at RID {rid}")
        db.execute("UPDATE r SET sequence=? WHERE rid=?", (sequence, rid))
        count += 1
    if count != expected:
        raise ValueError("Missing canonical 05D sequences for reused RIDs")
    return count


def _write(db: sqlite3.Connection, bundle: Path, release: str) -> None:
    with (
        (bundle / METADATA_OUTPUT).open("x", encoding="utf-8", newline="") as meta,
        (bundle / SEQUENCES_OUTPUT).open("x", encoding="utf-8", newline="") as seq,
    ):
        meta_writer = csv.DictWriter(
            meta, fieldnames=REUSED_FIELDS, delimiter="\t", lineterminator="\n"
        )
        seq_writer = csv.DictWriter(
            seq,
            fieldnames=UNIFIED_SEQUENCE_FIELDS,
            delimiter="\t",
            lineterminator="\n",
        )
        meta_writer.writeheader()
        seq_writer.writeheader()
        for rid, metadata_json, sequence in db.execute(
            "SELECT rid, metadata, sequence FROM r ORDER BY rid"
        ):
            if metadata_json is None or sequence is None:
                raise ValueError(f"Incomplete FULL metadata/sequence at RID {rid}")
            metadata = json.loads(metadata_json)
            meta_writer.writerow(metadata)
            seq_writer.writerow(
                {
                    "release": release,
                    "rid": str(rid),
                    "candidate_accession": metadata["candidate_accession"],
                    "full_v2_record_number": str(rid),
                    "sequence_sha256": metadata["sequence_sha256"],
                    "sequence": sequence,
                    "source_state": metadata["source_state"],
                }
            )


def reconcile_full_metadata(
    *,
    release: str,
    requests: Path,
    reuse_dir: Path,
    canonical_05d_store: Path,
    output_dir: Path,
    recovery_dir: Path | None = None,
) -> dict:
    """Publish exact-once FULL RID/metadata/sequence reconciliation.

    The original request universe is authoritative. A complete result is
    published only if every requested RID is exactly once in one source.
    """
    if not release or not output_dir.parent.is_dir():
        raise ValueError("Missing release or destination parent")
    if output_dir.exists() or output_dir.is_symlink():
        raise FileExistsError("Metadata reconciliation output already exists")
    inputs = [requests, canonical_05d_store]
    if not reuse_dir.is_dir() or reuse_dir.is_symlink():
        raise ValueError("Missing or symlinked reuse bundle")
    inputs.extend(
        reuse_dir / name
        for name in (
            "2C_reused_full_metadata.tsv",
            "2C_pending_full_metadata.tsv",
            "2C_full_metadata_reuse.summary.json",
        )
    )
    if recovery_dir is not None:
        if not recovery_dir.is_dir() or recovery_dir.is_symlink():
            raise ValueError("Missing or symlinked recovery bundle")
        inputs.extend(
            recovery_dir / name
            for name in (METADATA_NAME, SEQUENCES_NAME, SUMMARY_NAME)
        )
    if any(not item.is_file() for item in inputs):
        raise FileNotFoundError("Missing FULL reconciliation input")
    if len({item.resolve() for item in inputs}) != len(inputs):
        raise ValueError("FULL reconciliation inputs must be distinct")
    if output_dir.resolve() in {item.resolve() for item in inputs}:
        raise ValueError("Output cannot overlap reconciliation inputs")

    lock = output_dir.with_name(f".{output_dir.name}.publish-lock")
    lock.mkdir()
    try:
        return _reconcile_locked(
            release=release,
            requests=requests,
            reuse_dir=reuse_dir,
            canonical_05d_store=canonical_05d_store,
            recovery_dir=recovery_dir,
            output_dir=output_dir,
        )
    finally:
        lock.rmdir()


def _reconcile_locked(
    *,
    release: str,
    requests: Path,
    reuse_dir: Path,
    canonical_05d_store: Path,
    recovery_dir: Path | None,
    output_dir: Path,
) -> dict:
    with tempfile.TemporaryDirectory(
        prefix=".probe-2c-reconcile-", dir=output_dir.parent
    ) as temporary:
        base = Path(temporary)
        db = sqlite3.connect(str(base / "records.sqlite3"))
        try:
            db.execute(
                "CREATE TABLE r (rid INTEGER PRIMARY KEY, accession TEXT, "
                "raw_ox TEXT, request_json TEXT, origin TEXT, "
                "metadata TEXT, sequence TEXT)"
            )
            counts: Counter[str] = Counter()
            counts["requested_unique_rids"] = _load_requests(db, requests, release)
            counts["reused_unique_rids"] = _attach_metadata(
                db,
                reuse_dir / "2C_reused_full_metadata.tsv",
                release,
                REUSE_STATE,
                expected_origin=None,
            )
            counts["pending_unique_rids"] = _attach_pending(
                db, reuse_dir / "2C_pending_full_metadata.tsv", release
            )
            reuse_summary = _summary(
                reuse_dir / "2C_full_metadata_reuse.summary.json",
                "stage2c-full-metadata-reuse-v1",
                release,
            )
            for name in (
                "requested_unique_rids",
                "reused_unique_rids",
                "pending_unique_rids",
            ):
                _expect_count(reuse_summary, name, counts[name])
            if reuse_summary.get("full_uniprot_fasta_scanned") is not False:
                raise ValueError("Unexpected FULL scan during canonical reuse")
            if counts["pending_unique_rids"] and recovery_dir is None:
                raise ValueError("Missing recovery bundle for pending FULL RIDs")
            if recovery_dir is not None:
                counts["recovered_unique_rids"] = _attach_metadata(
                    db,
                    recovery_dir / METADATA_NAME,
                    release,
                    RECOVERY_STATE,
                    expected_origin="PENDING",
                )
                counts["recovered_sequences"] = _attach_recovered_sequences(
                    db,
                    recovery_dir / SEQUENCES_NAME,
                    release,
                )
                recovery_summary = _summary(
                    recovery_dir / SUMMARY_NAME,
                    "stage2c-pending-full-v2-recovery-v1",
                    release,
                )
                for name in ("requested_unique_rids", "recovered_unique_rids"):
                    required = (
                        "pending_unique_rids"
                        if name == "requested_unique_rids"
                        else "recovered_unique_rids"
                    )
                    _expect_count(recovery_summary, name, counts[required])
                scanned = counts["pending_unique_rids"] > 0
                if (
                    recovery_summary.get("full_fasta_scanned") is not scanned
                    or recovery_summary.get("full_fasta_sha256_verified") is not scanned
                ):
                    raise ValueError("Recovery FASTA integrity state conflicts")
            else:
                counts["recovered_unique_rids"] = 0
                counts["recovered_sequences"] = 0
            if counts["recovered_unique_rids"] != counts["pending_unique_rids"]:
                raise ValueError("Recovered RID coverage incomplete")
            if counts["recovered_sequences"] != counts["recovered_unique_rids"]:
                raise ValueError("Missing or duplicate recovered sequences")
            counts["reused_sequences"] = _attach_reused_sequences(
                db,
                canonical_05d_store,
                release,
            )
            if counts["reused_sequences"] != counts["reused_unique_rids"]:
                raise ValueError("Reused sequence coverage incomplete")
            total = counts["reused_unique_rids"] + counts["recovered_unique_rids"]
            if total != counts["requested_unique_rids"]:
                raise ValueError("FULL metadata is not an exact partition")
            bundle = base / "bundle"
            bundle.mkdir()
            _write(db, bundle, release)
            result = {
                "schema": SCHEMA,
                "release": release,
                "counts": dict(sorted(counts.items())),
                "source_metadata_states": [REUSE_STATE, RECOVERY_STATE],
                "taxonomy_verified": False,
                "go_donor_decisions": 0,
                "complete_stage05a2_replacement": False,
                "input_sha256": {
                    "original_requests": _hash(requests),
                    "reuse_summary": _hash(
                        reuse_dir / "2C_full_metadata_reuse.summary.json"
                    ),
                    "recovery_summary": (
                        _hash(recovery_dir / SUMMARY_NAME)
                        if recovery_dir is not None
                        else None
                    ),
                },
                "output_sha256": {
                    METADATA_OUTPUT: _hash(bundle / METADATA_OUTPUT),
                    SEQUENCES_OUTPUT: _hash(bundle / SEQUENCES_OUTPUT),
                },
            }
            (bundle / SUMMARY_OUTPUT).write_text(
                json.dumps(result, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            if output_dir.exists() or output_dir.is_symlink():
                raise FileExistsError("Reconciliation output occupied during run")
            os.replace(bundle, output_dir)
            return result
        finally:
            db.close()


def _hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(1024 * 1024):
            digest.update(block)
    return digest.hexdigest()
