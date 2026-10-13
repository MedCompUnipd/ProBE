"""Lossless 2C discovery union; NEVER asserts GO donation.

Input paths are independent, pinned Stage 03 products. The union is
species-agnostic: the resolved *species* TaxID is the only taxonomic scope.
UniProt RID is release-scoped; GO-positive and FULL record numbers are NOT.
"""

from __future__ import annotations

import csv
import json
import sqlite3
import tempfile
from collections import Counter
from pathlib import Path

GO_REQUIRED = frozenset(
    {
        "target_id",
        "rid",
        "accession",
        "raw_ox",
        "resolved_taxid",
        "species_taxid",
        "frag",
        "exact_sequence",
        "record_number",
    }
)
FULL_REQUIRED = frozenset(
    {
        "release",
        "target_id",
        "rid",
        "accession",
        "raw_ox",
        "target_resolved_taxid",
        "candidate_resolved_taxid",
        "species_taxid",
        "frag",
        "exact_full_sequence",
        "record_number",
        "length_aa",
        "discovery_channel",
    }
)
TAX_REQUIRED = frozenset({"target_id", "resolved_taxid", "species_taxid"})
OUTPUT_COLUMNS = (
    "release",
    "target_id",
    "rid",
    "candidate_accession",
    "candidate_raw_ox",
    "target_resolved_taxid",
    "candidate_resolved_taxid",
    "species_taxid",
    "go_positive_species_wide",
    "full_exact_species_wide",
    "go_positive_exact",
    "go_positive_record_number",
    "full_record_number",
    "strict_gate",
    "same_species_exact_gate",
)
SCHEMA = "stage2c-unified-discovery-v1"


def _rows(path: Path, required: frozenset[str]):
    with path.open(encoding="utf-8", newline="") as stream:
        reader = csv.DictReader(stream, delimiter="\t")
        header = reader.fieldnames
        if header is None or len(set(header)) != len(header):
            raise ValueError(f"Invalid TSV header: {path}")
        missing = required - set(header)
        if missing:
            raise ValueError(f"Missing {sorted(missing)} in {path}")
        for line, row in enumerate(reader, start=2):
            if None in row or any(row[k] is None for k in required):
                raise ValueError(f"Malformed TSV row: {path}:{line}")
            yield row


def _taxid(value: str) -> bool:
    return value.isascii() and value.isdecimal() and int(value) > 0


def _positive(value: str, label: str) -> str:
    if not _taxid(value):
        raise ValueError(f"Invalid {label}: {value!r}")
    return value


def _taxonomy(path: Path) -> dict[str, tuple[str, str]]:
    result = {}
    for row in _rows(path, TAX_REQUIRED):
        key = row["target_id"]
        if not key or key in result:
            raise ValueError("Missing or repeated target taxonomy identifier")
        if not row["resolved_taxid"] or not row["species_taxid"]:
            continue
        result[key] = (
            _positive(row["resolved_taxid"], "target TaxID"),
            _positive(row["species_taxid"], "species TaxID"),
        )
    return result


def _check_candidate(
    row: dict, target: tuple[str, str], *, full: bool
) -> tuple[str, str, str, str, str, str]:
    tax = row["candidate_resolved_taxid"] if full else row["resolved_taxid"]
    tax = _positive(tax, "candidate TaxID")
    species = _positive(row["species_taxid"], "candidate species TaxID")
    if species != target[1]:
        raise ValueError("Cross-species record in species-wide discovery")
    if row["frag"] != "0":
        raise ValueError("FRAG=1 is forbidden in sequence-only discovery")
    if not row["accession"] or not row["raw_ox"]:
        raise ValueError("Missing candidate accession or OX")
    rid = _positive(row["rid"], "UniProt RID")
    ordinal = _positive(row["record_number"], "FASTA record number")
    if full and row["target_resolved_taxid"] != target[0]:
        raise ValueError("FULL exact target taxonomy mismatch")
    return (
        rid,
        row["accession"],
        row["raw_ox"],
        tax,
        species,
        ordinal,
    )


def merge_discovery(
    *,
    release: str,
    target_taxonomy: Path,
    go_positive_pairs: Path,
    full_exact_pairs: Path,
    output: Path,
    summary: Path,
) -> dict:
    """Merge by (target, RID) using disk-backed SQLite, never GO donor YES.

    The same-TaxID channel retains candidates even when nonidentical.
    Cross-strain differences remain discoverable but deny GO transfer.
    A gate marked ELIGIBLE still requires downstream donor validation.
    """
    if not release or not output.parent.is_dir():
        raise ValueError("Invalid release or missing output parent")
    if output == summary or any(
        p.exists() or p.is_symlink() for p in (output, summary)
    ):
        raise FileExistsError("Discovery publication destination is occupied")
    targets = _taxonomy(target_taxonomy)
    counters: Counter[str] = Counter()
    with tempfile.TemporaryDirectory(prefix="probe-2c-union-") as directory:
        db = sqlite3.connect(str(Path(directory) / "union.sqlite3"))
        try:
            db.execute("""
                CREATE TABLE candidate (
                    target_id TEXT NOT NULL,
                    rid TEXT NOT NULL,
                    accession TEXT NOT NULL,
                    raw_ox TEXT NOT NULL,
                    target_tax TEXT NOT NULL,
                    candidate_tax TEXT NOT NULL,
                    species TEXT NOT NULL,
                    go_positive INTEGER NOT NULL,
                    full_exact INTEGER NOT NULL,
                    go_exact INTEGER NOT NULL,
                    go_ordinal TEXT NOT NULL,
                    full_ordinal TEXT NOT NULL,
                    PRIMARY KEY (target_id, rid)
                )
            """)
            for row in _rows(go_positive_pairs, GO_REQUIRED):
                target_id = row["target_id"]
                if target_id not in targets:
                    raise ValueError("GO-positive pair has unknown target")
                parts = _check_candidate(row, targets[target_id], full=False)
                if row["exact_sequence"] not in {"0", "1"}:
                    raise ValueError("Invalid GO-positive exact_sequence")
                try:
                    db.execute(
                        "INSERT INTO candidate VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                        (
                            target_id,
                            parts[0],
                            parts[1],
                            parts[2],
                            targets[target_id][0],
                            parts[3],
                            parts[4],
                            1,
                            0,
                            int(row["exact_sequence"]),
                            parts[5],
                            "",
                        ),
                    )
                except sqlite3.IntegrityError as exc:
                    raise ValueError("Duplicate GO-positive target/RID") from exc
                counters["go_positive_pairs"] += 1

            for row in _rows(full_exact_pairs, FULL_REQUIRED):
                if row["release"] != release:
                    raise ValueError("FULL exact release mismatch")
                target_id = row["target_id"]
                if target_id not in targets:
                    raise ValueError("FULL exact pair has unknown target")
                parts = _check_candidate(row, targets[target_id], full=True)
                if (
                    row["exact_full_sequence"] != "1"
                    or row["discovery_channel"] != "FULL_V2_EXACT"
                    or parts[3] == targets[target_id][0]
                ):
                    raise ValueError("Invalid cross-strain FULL exact evidence")
                old = db.execute(
                    "SELECT accession, raw_ox, target_tax, candidate_tax, "
                    "species, go_exact, full_exact FROM candidate "
                    "WHERE target_id=? AND rid=?",
                    (target_id, parts[0]),
                ).fetchone()
                if old is None:
                    db.execute(
                        "INSERT INTO candidate VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                        (
                            target_id,
                            parts[0],
                            parts[1],
                            parts[2],
                            targets[target_id][0],
                            parts[3],
                            parts[4],
                            0,
                            1,
                            1,
                            "",
                            parts[5],
                        ),
                    )
                else:
                    if old[:5] != (
                        parts[1],
                        parts[2],
                        targets[target_id][0],
                        parts[3],
                        parts[4],
                    ):
                        raise ValueError("Conflicting candidate identity across scans")
                    if not old[5] or old[6]:
                        raise ValueError("FULL exact contradicts GO-positive match")
                    db.execute(
                        "UPDATE candidate SET full_exact=1, full_ordinal=? "
                        "WHERE target_id=? AND rid=?",
                        (parts[5], target_id, parts[0]),
                    )
                    counters["both_channels"] += 1
                counters["full_exact_pairs"] += 1

            with output.open("x", encoding="utf-8", newline="") as stream:
                writer = csv.writer(stream, delimiter="\t", lineterminator="\n")
                writer.writerow(OUTPUT_COLUMNS)
                for row in db.execute(
                    "SELECT * FROM candidate ORDER BY target_id, CAST(rid AS INTEGER)"
                ):
                    (
                        target_id,
                        rid,
                        accession,
                        raw_ox,
                        target_tax,
                        candidate_tax,
                        species,
                        go,
                        full,
                        go_exact,
                        go_ordinal,
                        full_ordinal,
                    ) = row
                    cross_strain = target_tax != candidate_tax
                    exact = bool(full or (go and go_exact))
                    # No need to load raw sequences here: literal equality is
                    # a verified, pinned scanner result, not a digest guess.
                    if cross_strain:
                        strict_gate = "DENIED"
                        exact_gate = "ELIGIBLE" if exact else "DENIED"
                    else:
                        strict_gate = "DEFER_TO_DOCUMENTARY"
                        exact_gate = "DEFER_TO_DOCUMENTARY"
                    writer.writerow(
                        (
                            release,
                            target_id,
                            rid,
                            accession,
                            raw_ox,
                            target_tax,
                            candidate_tax,
                            species,
                            int(go),
                            int(full),
                            int(go_exact),
                            go_ordinal,
                            full_ordinal,
                            strict_gate,
                            exact_gate,
                        )
                    )
                    counters["unique_pairs"] += 1
                    if cross_strain:
                        counters["cross_strain_pairs"] += 1
                        if exact:
                            counters["cross_strain_exact"] += 1
                        else:
                            counters["cross_strain_nonexact"] += 1
            result = {"schema": SCHEMA, "release": release, **dict(counters)}
            with summary.open("x", encoding="utf-8") as stream:
                json.dump(result, stream, sort_keys=True, indent=2)
                stream.write("\n")
            return result
        finally:
            db.close()
