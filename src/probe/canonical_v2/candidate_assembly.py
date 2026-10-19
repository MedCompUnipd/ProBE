"""Build a lossless, disk-backed canonical V2 candidate-provenance ledger.

All four channels are inputs. No legacy 05A2/05C2/05D/06C1 is read.
This stage never chooses donors, promotes audit, or makes DP decisions.
The published ledger is NOT a replacement for the Stage 05A2 schema.
"""

from __future__ import annotations

import csv
import hashlib
import json
import os
import sqlite3
import tempfile
from collections import Counter
from collections.abc import Iterator
from pathlib import Path

from probe.canonical_v2.contracts import (
    DISCOVERY_REQUIRED,
    FULL_SOURCE_REQUIRED,
    GO_SOURCE_REQUIRED,
    LINEAGE_REQUIRED,
    OUTPUT_FIELDS,
    OUTPUT_NAME,
    RESCUE_REQUIRED,
    SCHEMA,
    SUMMARY_NAME,
)

CHANNELS = ("go", "full", "lineage", "rescue")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def _read(path: Path, required: frozenset[str]) -> Iterator[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as stream:
        reader = csv.DictReader(stream, delimiter="\t")
        header = reader.fieldnames
        if header is None or len(header) != len(set(header)):
            raise ValueError(f"Invalid TSV header: {path}")
        missing = required.difference(header)
        if missing:
            raise ValueError(f"Missing columns in {path}: {sorted(missing)}")
        for number, row in enumerate(reader, start=2):
            if None in row or any(value is None for value in row.values()):
                raise ValueError(f"Malformed TSV row: {path}:{number}")
            yield row


def _positive(value: str, label: str) -> str:
    if not value.isascii() or not value.isdecimal():
        raise ValueError(f"Invalid {label}: {value!r}")
    if int(value) < 1 or str(int(value)) != value:
        raise ValueError(f"Noncanonical {label}: {value!r}")
    return value


def _key(row: dict[str, str], release: str) -> tuple[str, str]:
    if "release" in row and row["release"] != release:
        raise ValueError("Unexpected source release")
    if not row["target_id"]:
        raise ValueError("Missing target identifier")
    return row["target_id"], _positive(row["rid"], "RID")


def _source_identity(
    row: dict[str, str], channel: str
) -> tuple[str, str, str, str, str]:
    if channel == "full":
        taxid = row["candidate_resolved_taxid"]
    else:
        taxid = row["resolved_taxid"]
    accession = (
        row["candidate_primary_accession"] if channel == "lineage" else row["accession"]
    )
    if not accession:
        raise ValueError("Missing candidate accession")
    if row["frag"] not in {"0", "1"}:
        raise ValueError("Invalid fragment flag")
    if channel == "lineage":
        # 04E preserves records before resolver taxonomy admissibility.
        return (
            accession,
            row["raw_ox"],
            row["resolved_taxid"],
            row["species_taxid"],
            row["frag"],
        )
    if row["frag"] == "1" and channel != "full":
        raise ValueError("Non-FULL sequence discovery cannot admit FRAG=1")
    return (
        accession,
        _positive(row["raw_ox"], "raw OX"),
        _positive(taxid, "candidate TaxID"),
        _positive(row["species_taxid"], "species TaxID"),
        row["frag"],
    )


def _attach(
    db: sqlite3.Connection,
    row: dict[str, str],
    channel: str,
    release: str,
) -> None:
    target, rid = _key(row, release)
    identity = _source_identity(row, channel)
    old = db.execute(
        "SELECT accession, raw_ox, candidate_tax, species, frag, "
        "go, full, lineage, rescue FROM candidate "
        "WHERE target=? AND rid=?",
        (target, rid),
    ).fetchone()
    payload = json.dumps(row, sort_keys=True, separators=(",", ":"))
    if old is None:
        db.execute(
            "INSERT INTO candidate "
            "(target, rid, accession, raw_ox, candidate_tax, species, "
            "frag, go, full, lineage, rescue) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, '', '', '', '')",
            (target, rid, *identity),
        )
    elif old[:5] != identity:
        raise ValueError(f"Conflicting identity for {(release, target, rid)}")
    if old is not None and old[5 + CHANNELS.index(channel)]:
        raise ValueError(f"Duplicate {channel} source for {(target, rid)}")
    # Column names are fixed constants, not user-provided input.
    db.execute(
        f"UPDATE candidate SET {channel}=? WHERE target=? AND rid=?",
        (payload, target, rid),
    )


def _check_discovery_source(
    source: dict[str, str],
    discovery: dict[str, str],
    channel: str,
) -> None:
    if channel == "go":
        fields = (
            ("accession", "candidate_accession"),
            ("raw_ox", "candidate_raw_ox"),
            ("resolved_taxid", "candidate_resolved_taxid"),
            ("species_taxid", "species_taxid"),
            ("record_number", "go_positive_record_number"),
            ("exact_sequence", "go_positive_exact"),
        )
        if discovery["go_positive_species_wide"] != "1":
            raise ValueError("Unexpected GO source without discovery flag")
    else:
        fields = (
            ("accession", "candidate_accession"),
            ("raw_ox", "candidate_raw_ox"),
            ("candidate_resolved_taxid", "candidate_resolved_taxid"),
            ("species_taxid", "species_taxid"),
            ("target_resolved_taxid", "target_resolved_taxid"),
            ("record_number", "full_record_number"),
        )
        if (
            discovery["full_exact_species_wide"] != "1"
            or source["exact_full_sequence"] != "1"
            or source["discovery_channel"] != "FULL_V2_EXACT"
        ):
            raise ValueError("Unexpected FULL exact source")
    if any(source[left] != discovery[right] for left, right in fields):
        raise ValueError(f"{channel} source contradicts unified discovery")


def _check_canonical_gate(row: dict[str, str]) -> None:
    """Trust a verified FULL scanner flag, not an unverified fragment hint."""
    for field in ("target_resolved_taxid", "candidate_resolved_taxid", "species_taxid"):
        _positive(row[field], field)
    for field in (
        "go_positive_species_wide",
        "full_exact_species_wide",
        "go_positive_exact",
    ):
        if row[field] not in {"0", "1"}:
            raise ValueError(f"Invalid discovery flag: {field}")
    full = row["full_exact_species_wide"] == "1"
    go = row["go_positive_species_wide"] == "1"
    if not (go or full):
        raise ValueError("Discovery lacks verified sequence source")
    cross = row["target_resolved_taxid"] != row["candidate_resolved_taxid"]
    exact = full or row["go_positive_exact"] == "1"
    expected = (
        "DENIED" if cross else "DEFER_TO_DOCUMENTARY",
        "ELIGIBLE"
        if cross and exact
        else "DENIED"
        if cross
        else "DEFER_TO_DOCUMENTARY",
    )
    if (row["strict_gate"], row["same_species_exact_gate"]) != expected:
        raise ValueError("Discovery candidate gates contradict provenance")


def _load_discovery(db: sqlite3.Connection, path: Path, release: str) -> int:
    count = 0
    for row in _read(path, DISCOVERY_REQUIRED):
        target, rid = _key(row, release)
        _check_canonical_gate(row)
        identity = (
            row["candidate_accession"],
            _positive(row["candidate_raw_ox"], "discovery OX"),
            _positive(row["candidate_resolved_taxid"], "discovery TaxID"),
            _positive(row["species_taxid"], "discovery species"),
        )
        if not identity[0]:
            raise ValueError("Missing discovery accession")
        try:
            db.execute(
                "INSERT INTO discovery VALUES (?, ?, ?)",
                (target, rid, json.dumps(row, sort_keys=True)),
            )
        except sqlite3.IntegrityError as exc:
            raise ValueError("Duplicate unified discovery key") from exc
        count += 1
    return count


def _load_channel(
    db: sqlite3.Connection,
    path: Path,
    channel: str,
    release: str,
) -> int:
    required = {
        "go": GO_SOURCE_REQUIRED,
        "full": FULL_SOURCE_REQUIRED,
        "lineage": LINEAGE_REQUIRED,
        "rescue": RESCUE_REQUIRED,
    }[channel]
    count = 0
    for row in _read(path, required):
        target, rid = _key(row, release)
        if channel in {"go", "full"}:
            match = db.execute(
                "SELECT payload FROM discovery WHERE target=? AND rid=?",
                (target, rid),
            ).fetchone()
            if match is None:
                raise ValueError("Scanner pair missing from verified union")
            _check_discovery_source(row, json.loads(match[0]), channel)
        if channel == "rescue" and row["record_number"] != rid:
            raise ValueError("Rescue FULL record number must equal RID")
        if channel == "lineage":
            _positive(row["record_number"], "lineage FULL ordinal")
        _attach(db, row, channel, release)
        count += 1
    return count


def _write_publication(
    db: sqlite3.Connection, output: Path, release: str
) -> dict[str, int]:
    counts: Counter[str] = Counter()
    with output.open("x", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(
            stream,
            fieldnames=OUTPUT_FIELDS,
            delimiter="\t",
            lineterminator="\n",
            extrasaction="raise",
        )
        writer.writeheader()
        query = (
            "SELECT target, rid, accession, raw_ox, candidate_tax, "
            "species, frag, go, full, lineage, rescue "
            "FROM candidate ORDER BY target, CAST(rid AS INTEGER)"
        )
        for row in db.execute(query):
            target, rid, accession, ox, tax, species, frag, *sources = row
            go, full, lineage, rescue = sources
            discovery = db.execute(
                "SELECT payload FROM discovery WHERE target=? AND rid=?",
                (target, rid),
            ).fetchone()
            details = json.loads(discovery[0]) if discovery else {}
            if details:
                if (go != "") != (details["go_positive_species_wide"] == "1") or (
                    full != ""
                ) != (details["full_exact_species_wide"] == "1"):
                    raise ValueError("Discovery and raw scanner counts disagree")
                if (
                    accession != details["candidate_accession"]
                    or ox != details["candidate_raw_ox"]
                    or tax != details["candidate_resolved_taxid"]
                    or species != details["species_taxid"]
                ):
                    raise ValueError("Discovery identity conflicts with source")
            if not any(sources):
                raise ValueError("Candidate without source evidence")
            flags = {
                "go_positive_species_wide": str(int(bool(go))),
                "full_exact_species_wide": str(int(bool(full))),
                "lineage_discovered": str(int(bool(lineage))),
                "full_v2_rescue_discovered": str(int(bool(rescue))),
            }
            writer.writerow(
                {
                    "release": release,
                    "target_id": target,
                    "rid": rid,
                    "candidate_accession": accession,
                    "candidate_raw_ox": ox,
                    "candidate_resolved_taxid": tax,
                    "species_taxid": species,
                    "frag": frag,
                    **flags,
                    "go_positive_exact": details.get("go_positive_exact", ""),
                    "go_positive_record_number": details.get(
                        "go_positive_record_number", ""
                    ),
                    "full_record_number": details.get("full_record_number", ""),
                    "strict_gate": details.get("strict_gate", ""),
                    "same_species_exact_gate": details.get(
                        "same_species_exact_gate", ""
                    ),
                    **{
                        f"{name}_source_json": source
                        for name, source in zip(CHANNELS, sources, strict=True)
                    },
                }
            )
            counts["unique_pairs"] += 1
            for name, enabled in flags.items():
                counts[name] += int(enabled)
            counts["multiple_sources"] += sum(bool(s) for s in sources) > 1
            if not go and not full and not rescue:
                counts["lineage_only"] += 1
            if rescue and (go or full or lineage):
                counts["rescue_overlaps"] += 1
    return dict(sorted(counts.items()))


def assemble_candidate_provenance(
    *,
    release: str,
    discovery_union: Path,
    go_positive_pairs: Path,
    full_exact_pairs: Path,
    lineage_pairs: Path,
    rescue_pairs: Path,
    output_dir: Path,
) -> dict:
    """Publish a complete candidate ledger, no donors or alignments.

    Every source must exist, including header-only files for empty channels.
    The output directory must not exist; legacy publications are never read.
    """
    if not release or not output_dir.parent.is_dir():
        raise ValueError("Missing release or output parent")
    if output_dir.exists() or output_dir.is_symlink():
        raise FileExistsError("Candidate ledger destination is occupied")
    inputs = {
        "discovery_union": discovery_union,
        "go_positive_pairs": go_positive_pairs,
        "full_exact_pairs": full_exact_pairs,
        "lineage_pairs": lineage_pairs,
        "rescue_pairs": rescue_pairs,
    }
    for name, path in inputs.items():
        if not path.is_file():
            raise FileNotFoundError(f"Missing {name}: {path}")
        if output_dir == path or output_dir in path.parents:
            raise ValueError("Input lies within output destination")
    with tempfile.TemporaryDirectory(
        prefix="probe-canonical-b1-", dir=output_dir.parent
    ) as temporary:
        staged = Path(temporary)
        with sqlite3.connect(str(staged / "candidate.sqlite3")) as db:
            db.execute(
                "CREATE TABLE discovery (target TEXT, rid TEXT, payload TEXT, "
                "PRIMARY KEY(target,rid))"
            )
            db.execute(
                "CREATE TABLE candidate ("
                "target TEXT, rid TEXT, accession TEXT, raw_ox TEXT, "
                "candidate_tax TEXT, species TEXT, frag TEXT, "
                "go TEXT, full TEXT, lineage TEXT, rescue TEXT, "
                "PRIMARY KEY(target,rid))"
            )
            discovery_count = _load_discovery(db, discovery_union, release)
            sources = {
                "go": go_positive_pairs,
                "full": full_exact_pairs,
                "lineage": lineage_pairs,
                "rescue": rescue_pairs,
            }
            input_counts = {
                name: _load_channel(db, path, name, release)
                for name, path in sources.items()
            }
            counts = _write_publication(db, staged / OUTPUT_NAME, release)
            represented = db.execute(
                "SELECT COUNT(*) FROM discovery d LEFT JOIN candidate c "
                "ON d.target=c.target AND d.rid=c.rid WHERE c.rid IS NULL"
            ).fetchone()[0]
            if represented:
                raise ValueError("Discovery keys without corresponding scanner rows")
        summary = {
            "schema": SCHEMA,
            "release": release,
            "go_donor_decisions": 0,
            "alignment_decisions": 0,
            "taxonomy_audit_promotions": 0,
            "input_counts": {
                "discovery_union": discovery_count,
                **input_counts,
            },
            "counts": counts,
            "input_sha256": {name: _sha256(path) for name, path in inputs.items()},
            "output_sha256": {OUTPUT_NAME: _sha256(staged / OUTPUT_NAME)},
        }
        with (staged / SUMMARY_NAME).open(
            "x", encoding="utf-8", newline="\n"
        ) as stream:
            json.dump(summary, stream, sort_keys=True, indent=2)
            stream.write("\n")
        # Exclude temporary SQLite pages from the immutable publication.
        (staged / "candidate.sqlite3").unlink()
        if output_dir.exists() or output_dir.is_symlink():
            raise FileExistsError("Publication was created concurrently")
        os.rename(staged, output_dir)
    return summary
