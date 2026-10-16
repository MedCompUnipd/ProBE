"""Reuse verified canonical Stage 05D metadata for Stage 2C FULL RIDs.

This process neither scans full UniProt FASTA nor determines GO donor status.
Missing RIDs remain explicit requests and are never populated heuristically.
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

STORE_FIELDS = (
    "release",
    "full_v2_record_number",
    "rid",
    "primary_accession",
    "entry",
    "raw_ox",
    "frag",
    "sequence_length",
    "pe",
    "sv",
    "sec",
    "gene",
    "needed_by_primary",
    "needed_by_taxonomy_audit",
    "primary_reference_count",
    "audit_reference_count",
    "sequence",
)
REUSED_FIELDS = (
    "release",
    "rid",
    "candidate_accession",
    "candidate_raw_ox",
    "entry",
    "frag",
    "sequence_length",
    "pe",
    "sv",
    "sec",
    "gene",
    "sequence_sha256",
    "source_full_v2_record_number",
    "taxonomy_state",
    "source_state",
)
SCHEMA = "stage2c-full-metadata-reuse-v1"


def _rid(value: str) -> str:
    if not value.isascii() or not value.isdecimal():
        raise ValueError(f"Invalid RID: {value!r}")
    if int(value) < 1 or str(int(value)) != value:
        raise ValueError(f"Noncanonical RID: {value!r}")
    return value


def _read(path: Path, required: set[str]):
    with path.open("r", encoding="utf-8", newline="") as stream:
        reader = csv.DictReader(stream, delimiter="\t")
        fields = reader.fieldnames
        if fields is None or len(fields) != len(set(fields)):
            raise ValueError(f"Invalid header: {path}")
        if not required.issubset(fields):
            raise ValueError(f"Missing columns: {sorted(required - set(fields))}")
        for number, row in enumerate(reader, start=2):
            if None in row or any(value is None for value in row.values()):
                raise ValueError(f"Malformed TSV row: {path}:{number}")
            yield row


def _write_rows(path: Path, fields: tuple[str, ...], rows) -> None:
    with path.open("x", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(
            stream, fieldnames=fields, delimiter="\t", lineterminator="\n"
        )
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def reuse_05d_metadata(
    *,
    release: str,
    requests: Path,
    store_05d: Path,
    output_dir: Path,
) -> dict:
    """Resolve available FULL metadata once from an existing Stage 05D store.

    A complete UniProt record lookup is still needed for every pending RID.
    The original 05D store and all previous publications remain unchanged.
    """
    if not release or not output_dir.parent.is_dir():
        raise ValueError("Invalid release or destination parent")
    if output_dir.exists() or output_dir.is_symlink():
        raise FileExistsError("Metadata reuse destination already occupied")
    if not requests.is_file() or not store_05d.is_file():
        raise FileNotFoundError("Missing request TSV or canonical Stage 05D TSV")
    if requests.resolve() == store_05d.resolve():
        raise ValueError("Request and 05D store must be different files")

    with tempfile.TemporaryDirectory(
        prefix=".probe-2c-reuse-", dir=output_dir.parent
    ) as temporary:
        base = Path(temporary)
        db = sqlite3.connect(str(base / "requests.sqlite3"))
        counts: Counter[str] = Counter()
        try:
            db.execute(
                "CREATE TABLE request ("
                "rid TEXT PRIMARY KEY, accession TEXT NOT NULL, "
                "raw_ox TEXT NOT NULL, candidate_tax TEXT NOT NULL, "
                "species TEXT NOT NULL, ordinal TEXT NOT NULL, "
                "refs TEXT NOT NULL, reused TEXT)"
            )
            for row in _read(requests, set(REQUEST_FIELDS)):
                rid = _rid(row["rid"])
                if (
                    row["release"] != release
                    or row["full_record_number"] != rid
                    or row["request_reason"] != "PINNED_FULL_V2_METADATA_VERIFICATION"
                ):
                    raise ValueError("Invalid FULL request provenance")
                for field in (
                    "candidate_raw_ox",
                    "candidate_resolved_taxid",
                    "species_taxid",
                    "target_reference_count",
                ):
                    _rid(row[field])
                if not row["candidate_accession"]:
                    raise ValueError("Missing candidate accession")
                try:
                    db.execute(
                        "INSERT INTO request VALUES (?, ?, ?, ?, ?, ?, ?, NULL)",
                        (
                            rid,
                            row["candidate_accession"],
                            row["candidate_raw_ox"],
                            row["candidate_resolved_taxid"],
                            row["species_taxid"],
                            row["full_record_number"],
                            row["target_reference_count"],
                        ),
                    )
                except sqlite3.IntegrityError as exc:
                    raise ValueError("Duplicate requested RID") from exc
                counts["requested_unique_rids"] += 1

            for record in _read(store_05d, set(STORE_FIELDS)):
                counts["canonical_05d_store_rows_scanned"] += 1
                rid = _rid(record["rid"])
                if record["release"] != release:
                    raise ValueError("Canonical 05D release mismatch")
                if record["full_v2_record_number"] != rid:
                    raise ValueError("Canonical 05D ordinal mismatch")
                match = db.execute(
                    "SELECT accession, raw_ox, reused FROM request WHERE rid=?", (rid,)
                ).fetchone()
                if match is None:
                    continue
                if match[2] is not None:
                    raise ValueError("Duplicate matched RID in canonical 05D")
                if (
                    record["primary_accession"] != match[0]
                    or record["raw_ox"] != match[1]
                    or record["frag"] != "0"
                ):
                    raise ValueError("Canonical 05D metadata conflicts with FULL")
                declared_length = _rid(record["sequence_length"])
                if int(declared_length) != len(record["sequence"]):
                    raise ValueError("Canonical 05D sequence length mismatch")
                metadata = {
                    "release": release,
                    "rid": rid,
                    "candidate_accession": record["primary_accession"],
                    "candidate_raw_ox": record["raw_ox"],
                    "entry": record["entry"],
                    "frag": record["frag"],
                    "sequence_length": declared_length,
                    "pe": record["pe"],
                    "sv": record["sv"],
                    "sec": record["sec"],
                    "gene": record["gene"],
                    "sequence_sha256": hashlib.sha256(
                        record["sequence"].encode("ascii")
                    ).hexdigest(),
                    "source_full_v2_record_number": rid,
                    "taxonomy_state": "REQUIRES_02B_REVALIDATION",
                    "source_state": "CANONICAL_05D_METADATA_REUSED",
                }
                db.execute(
                    "UPDATE request SET reused=? WHERE rid=?",
                    (json.dumps(metadata, sort_keys=True), rid),
                )
                counts["reused_unique_rids"] += 1

            bundle = base / "bundle"
            bundle.mkdir()
            _write_rows(
                bundle / "2C_reused_full_metadata.tsv",
                REUSED_FIELDS,
                (
                    json.loads(row[0])
                    for row in db.execute(
                        "SELECT reused FROM request WHERE reused IS NOT NULL "
                        "ORDER BY CAST(rid AS INTEGER)"
                    )
                ),
            )
            _write_rows(
                bundle / "2C_pending_full_metadata.tsv",
                REQUEST_FIELDS,
                (
                    {
                        "release": release,
                        "rid": rid,
                        "candidate_accession": accession,
                        "candidate_raw_ox": ox,
                        "candidate_resolved_taxid": candidate_tax,
                        "species_taxid": species,
                        "full_record_number": ordinal,
                        "target_reference_count": refs,
                        "request_reason": "PINNED_FULL_V2_METADATA_VERIFICATION",
                    }
                    for (
                        rid,
                        accession,
                        ox,
                        candidate_tax,
                        species,
                        ordinal,
                        refs,
                    ) in db.execute(
                        "SELECT rid, accession, raw_ox, candidate_tax, "
                        "species, ordinal, refs FROM request WHERE reused IS NULL "
                        "ORDER BY CAST(rid AS INTEGER)"
                    )
                ),
            )
            counts["pending_unique_rids"] = (
                counts["requested_unique_rids"] - counts["reused_unique_rids"]
            )
            result = {
                "schema": SCHEMA,
                "release": release,
                "counts": dict(sorted(counts.items())),
                "full_uniprot_fasta_scanned": False,
                "go_donor_decisions": 0,
                "complete_stage05a2_replacement": False,
                "taxonomy_verified_for_reused_rows": False,
            }
            (bundle / "2C_full_metadata_reuse.summary.json").write_text(
                json.dumps(result, sort_keys=True, indent=2) + "\n",
                encoding="utf-8",
            )
            if output_dir.exists() or output_dir.is_symlink():
                raise FileExistsError("Destination occupied during processing")
            os.replace(bundle, output_dir)
            return result
        finally:
            db.close()
