"""Read-only, lossless attestation of 04D2 -> 04E -> canonical B1 ledger.

The long-form 04D2 evidence is authoritative. This module never decides
same-entity, donor eligibility, exactness, fragment policy, or DP admission.
SQLite is a temporary external index, never a scientific publication.
"""

from __future__ import annotations

import csv
import hashlib
import itertools
import json
import sqlite3
import tempfile
from collections import Counter
from collections.abc import Iterator
from pathlib import Path

from probe.canonical_v2.contracts import LINEAGE_REQUIRED

FACT_FIELDS = (
    "release",
    "target_id",
    "target_primary_bucket",
    "record_number",
    "candidate_primary_accession",
    "entry",
    "rid",
    "raw_ox",
    "resolved_taxid",
    "species_taxid",
    "frag",
    "sequence_length",
    "pe",
    "sv",
    "sec",
    "gene",
)
LONG_FIELDS = frozenset(FACT_FIELDS) | frozenset({
    "key_type", "key_value", "record_match_role", "source_roles",
    "from_target_accession", "from_start_anchor", "from_end_anchor",
    "key_collision", "key_target_multiplicity",
})
LEDGER_FIELDS = frozenset({
    "release", "target_id", "rid", "candidate_accession",
    "candidate_raw_ox", "candidate_resolved_taxid", "species_taxid",
    "frag", "lineage_discovered", "lineage_source_json",
    "go_positive_exact", "full_exact_species_wide",
})
SIGNATURE_ORDER = (
    ("target", "TARGET_ACCESSION"),
    ("primary", "ACCESSION_PRIMARY"),
    ("secondary", "ACCESSION_SECONDARY"),
    ("locus", "ORDERED_LOCUS"),
    ("orf", "ORF"),
    ("start", "START_ANCHOR_DERIVED"),
    ("end", "END_ANCHOR_DERIVED"),
    ("collision", "KEY_COLLISION_PRESENT"),
)
ROLE_SETS = {
    "ACCESSION_PRIMARY": "primary",
    "ACCESSION_SECONDARY": "secondary",
    "ORDERED_LOCUS": "locus",
    "ORF": "orf",
}


def _sha256(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def _rows(path: Path, required: frozenset[str]) -> Iterator[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as stream:
        reader = csv.DictReader(stream, delimiter="\t")
        columns = reader.fieldnames
        if columns is None or len(columns) != len(set(columns)):
            raise ValueError(f"Invalid TSV header: {path}")
        if missing := required.difference(columns):
            raise ValueError(f"Missing columns in {path}: {sorted(missing)}")
        for number, row in enumerate(reader, start=2):
            if None in row or any(value is None for value in row.values()):
                raise ValueError(f"Malformed TSV: {path}:{number}")
            yield row


def _key(row: dict[str, str], release: str) -> tuple[str, str]:
    if row["release"] != release or not row["target_id"]:
        raise ValueError("Lineage release or target identity mismatch")
    rid = row["rid"]
    if not rid.isascii() or not rid.isdecimal():
        raise ValueError("Non-numeric RID")
    if int(rid) < 1 or str(int(rid)) != rid:
        raise ValueError("Noncanonical RID")
    return row["target_id"], rid


def _fold(rows: list[dict[str, str]]) -> dict[str, str]:
    """Reconstruct each declared 04E field independently of 04E output."""
    if not rows:
        raise ValueError("Empty lineage pair")
    facts = {key: rows[0][key] for key in FACT_FIELDS}
    grouped: dict[str, set] = {
        key: set() for key in (
            "typed", "roles", "sources", "target", "start", "end",
            "collision", "primary", "secondary", "locus", "orf",
        )
    }
    maximum = 0
    for row in rows:
        if any(row[name] != value for name, value in facts.items()):
            raise ValueError("Conflicting 04D2 facts within a candidate")
        role = row["record_match_role"]
        if role not in ROLE_SETS:
            raise ValueError(f"Invalid 04D2 match role: {role}")
        typed = (row["key_type"], row["key_value"])
        if not all(typed):
            raise ValueError("Empty 04D2 typed lineage key")
        grouped["typed"].add(typed)
        grouped["roles"].add(role)
        grouped[ROLE_SETS[role]].add(row["key_value"])
        grouped["sources"].update(
            role for role in row["source_roles"].split(",") if role
        )
        for field, name in (
            ("from_target_accession", "target"),
            ("from_start_anchor", "start"),
            ("from_end_anchor", "end"),
            ("key_collision", "collision"),
        ):
            if row[field] not in {"0", "1"}:
                raise ValueError(f"Invalid 04D2 boolean {field}")
            if row[field] == "1":
                grouped[name].add(typed)
        value = row["key_target_multiplicity"]
        if not value.isascii() or not value.isdecimal() or int(value) < 1:
            raise ValueError("Invalid 04D2 target multiplicity")
        maximum = max(maximum, int(value))

    def simple(name: str) -> str:
        return ",".join(sorted(grouped[name]))

    def typed(name: str) -> str:
        return ",".join(
            f"{kind}:{value}" for kind, value in sorted(grouped[name])
        )

    signature = ",".join(
        label for group, label in SIGNATURE_ORDER if grouped[group]
    )
    return {
        **facts,
        "lineage_evidence_row_count": str(len(rows)),
        "distinct_lineage_key_count": str(len(grouped["typed"])),
        "lineage_fact_signature": signature,
        "record_match_roles": simple("roles"),
        "source_roles": simple("sources"),
        "target_accession_match": str(int(bool(grouped["target"]))),
        "from_start_anchor": str(int(bool(grouped["start"]))),
        "from_end_anchor": str(int(bool(grouped["end"]))),
        "has_accession_primary_match": str(int(bool(grouped["primary"]))),
        "has_accession_secondary_match": str(int(bool(grouped["secondary"]))),
        "has_ordered_locus_match": str(int(bool(grouped["locus"]))),
        "has_orf_match": str(int(bool(grouped["orf"]))),
        "key_collision_present": str(int(bool(grouped["collision"]))),
        "collision_key_count": str(len(grouped["collision"])),
        "max_key_target_multiplicity": str(maximum),
        "matched_typed_keys": typed("typed"),
        "target_accession_keys": typed("target"),
        "accession_primary_keys": simple("primary"),
        "accession_secondary_keys": simple("secondary"),
        "ordered_locus_keys": simple("locus"),
        "orf_keys": simple("orf"),
        "collision_keys": typed("collision"),
    }


def _write_index(
    db: sqlite3.Connection, path: Path, release: str, kind: str
) -> int:
    required = LINEAGE_REQUIRED if kind == "collapsed" else LEDGER_FIELDS
    count = 0
    for row in _rows(path, required):
        target, rid = _key(row, release)
        payload = json.dumps(row, sort_keys=True, separators=(",", ":"))
        try:
            db.execute(
                f"INSERT INTO {kind} VALUES (?, ?, ?)",
                (target, rid, payload),
            )
        except sqlite3.IntegrityError as exc:
            raise ValueError(f"Duplicate {kind} target/RID") from exc
        count += 1
    return count


def attest_lineage_conservation(
    *,
    release: str,
    long_form: Path,
    collapsed: Path,
    candidate_ledger: Path,
    scratch_dir: Path,
) -> dict:
    """Validate complete documentary preservation; never write to inputs."""
    sources = (long_form, collapsed, candidate_ledger)
    if not release or not scratch_dir.is_dir():
        raise ValueError("Invalid release or scratch directory")
    for source in sources:
        if not source.is_file() or source.is_symlink():
            raise ValueError(f"Missing or unsafe lineage input: {source}")
    if len({source.resolve() for source in sources}) != len(sources):
        raise ValueError("Lineage inputs must be distinct")
    stats: Counter[str] = Counter()
    with tempfile.TemporaryDirectory(
        prefix="probe-b2b-lineage-", dir=scratch_dir
    ) as temporary, sqlite3.connect(str(Path(temporary) / "index.sqlite3")) as db:
        for kind in ("collapsed", "ledger"):
            db.execute(
                f"CREATE TABLE {kind} (target TEXT, rid TEXT, "
                "payload TEXT, PRIMARY KEY(target, rid))"
            )
        stats["collapsed_pairs"] = _write_index(
            db, collapsed, release, "collapsed"
        )
        stats["ledger_pairs"] = _write_index(
            db, candidate_ledger, release, "ledger"
        )
        db.execute(
            "CREATE TABLE evidence (target TEXT, rid TEXT, payload TEXT)"
        )
        for row in _rows(long_form, LONG_FIELDS):
            target, rid = _key(row, release)
            db.execute(
                "INSERT INTO evidence VALUES (?, ?, ?)",
                (target, rid, json.dumps(row, sort_keys=True)),
            )
            stats["long_form_rows"] += 1
        cursor = db.execute(
            "SELECT target, rid, payload FROM evidence "
            "ORDER BY target, CAST(rid AS INTEGER)"
        )
        seen_pairs = 0
        for key, records in itertools.groupby(
            cursor, key=lambda record: record[:2]
        ):
            rows = [json.loads(record[2]) for record in records]
            expected = _fold(rows)
            saved = db.execute(
                "SELECT payload FROM collapsed WHERE target=? AND rid=?",
                key,
            ).fetchone()
            if saved is None:
                raise ValueError(f"04D2 pair absent from 04E: {key}")
            actual = json.loads(saved[0])
            if any(actual[name] != value for name, value in expected.items()):
                differing = [
                    name for name, value in expected.items()
                    if actual[name] != value
                ]
                raise ValueError(f"04D2/04E mismatch {key}: {differing}")
            ledger = db.execute(
                "SELECT payload FROM ledger WHERE target=? AND rid=?",
                key,
            ).fetchone()
            if ledger is None:
                raise ValueError(f"04E pair absent from B1 ledger: {key}")
            projected = json.loads(ledger[0])
            if projected["lineage_discovered"] != "1":
                raise ValueError(f"04E lineage flag lost in ledger: {key}")
            try:
                preserved = json.loads(projected["lineage_source_json"])
            except (json.JSONDecodeError, TypeError) as exc:
                raise ValueError("Invalid ledger lineage JSON") from exc
            if preserved != actual:
                raise ValueError(f"04E details changed in ledger: {key}")
            for left, right in (
                ("candidate_accession", "candidate_primary_accession"),
                ("candidate_raw_ox", "raw_ox"),
                ("candidate_resolved_taxid", "resolved_taxid"),
                ("species_taxid", "species_taxid"),
                ("frag", "frag"),
            ):
                if projected[left] != actual[right]:
                    raise ValueError(f"Ledger lineage identity mismatch: {key}")
            if projected["go_positive_exact"] == "1" or (
                projected["full_exact_species_wide"] == "1"
            ):
                stats["lineage_pairs_with_exact"] += 1
            seen_pairs += 1
        stats["long_form_pairs"] = seen_pairs
        if seen_pairs != stats["collapsed_pairs"]:
            raise ValueError("04E contains pairs absent from 04D2")
        # Verify the reverse mapping even for header-only 04D2 inputs.
        for (payload,) in db.execute("SELECT payload FROM ledger"):
            row = json.loads(payload)
            pair = (row["target_id"], row["rid"])
            if row["lineage_discovered"] not in {"0", "1"}:
                raise ValueError("Invalid ledger lineage flag")
            if row["lineage_discovered"] == "1":
                exists = db.execute(
                    "SELECT 1 FROM collapsed WHERE target=? AND rid=?",
                    pair,
                ).fetchone()
                if exists is None:
                    raise ValueError(f"Spurious lineage in ledger: {pair}")
                stats["ledger_lineage_pairs"] += 1
            elif row["lineage_source_json"]:
                raise ValueError(f"Lineage payload without flag: {pair}")
        if stats["ledger_lineage_pairs"] != seen_pairs:
            raise ValueError("Ledger/04E lineage domain mismatch")
    return {
        "schema": "probe-canonical-v2-lineage-attestation-v1",
        "release": release,
        "status": "PASS",
        "no_donor_or_dp_decisions": True,
        "counts": dict(sorted(stats.items())),
        "input_sha256": {
            "04D2": _sha256(long_form),
            "04E": _sha256(collapsed),
            "ledger": _sha256(candidate_ledger),
        },
    }
