"""Documentary-only workload rows retain the native Stage 02B target identity."""

from __future__ import annotations

import csv
import hashlib
import json
import sqlite3
from contextlib import closing
from pathlib import Path

import pytest

from probe.canonical_v2.primary_projection import OUTPUT_FIELDS
from probe.canonical_v2.release_workload import CHANNEL, _extras, _merged_primary
from probe.canonical_v2.resolver_runner import load_v2_candidate_universe
from probe.canonical_v2.stage05_06a import PRIMARY
from probe.resolver_candidates import _AUDIT_COLS

RELEASES = {"START": "2023_01", "END": "2026_03"}


def _tables(db: sqlite3.Connection) -> None:
    db.execute(
        "CREATE TABLE requests (role TEXT,rid INTEGER,identity TEXT,"
        "full TEXT,PRIMARY KEY(role,rid))"
    )
    db.execute(
        "CREATE TABLE extra (role TEXT,target TEXT,rid INTEGER,"
        "identity TEXT,species TEXT,PRIMARY KEY(role,target,rid))"
    )


def _insert(
    db: sqlite3.Connection,
    role: str,
    resolved: str | None,
    species: str | None,
    accession: str,
) -> None:
    sequence = "ACDF" if role == "START" else "ACDG"
    identity = json.dumps(
        [
            accession,
            "111",
            resolved,
            "1",
            hashlib.sha256(sequence.encode("ascii")).hexdigest(),
        ]
    )
    full = {
        "rid": "1",
        "primary_accession": accession,
        "raw_ox": "111",
        "entry": "DOCUMENTARY",
        "frag": "1",
        "sequence_length": "4",
        "pe": "1",
        "sv": "1",
        "sec": "",
        "gene": "L:LOC1",
        "sequence": sequence,
    }
    db.execute(
        "INSERT INTO requests VALUES (?,?,?,?)",
        (role, 1, identity, json.dumps(full)),
    )
    db.execute(
        "INSERT INTO extra VALUES (?,?,?,?,?)",
        (role, "T1", 1, identity, species),
    )


def _write(path: Path, fields, rows=()) -> None:
    with path.open("x", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(
            stream,
            fieldnames=fields,
            delimiter="\t",
            lineterminator="\n",
        )
        writer.writeheader()
        writer.writerows(rows)


def test_documentary_workload_roundtrips_native_resolver_identity(
    tmp_path: Path,
) -> None:
    targets = tmp_path / "targets.tsv"
    _write(
        targets,
        ("target_id", "raw_taxid", "optional_accession"),
        [
            {
                "target_id": "T1",
                "raw_taxid": "111",
                "optional_accession": "P99901",
            }
        ],
    )
    b3 = tmp_path / "b3"
    b3.mkdir()
    _write(b3 / PRIMARY, OUTPUT_FIELDS)
    primary = tmp_path / "complete_primary.tsv"
    audit = tmp_path / "audit.tsv"
    _write(audit, sorted(_AUDIT_COLS))

    with closing(sqlite3.connect(":memory:")) as db:
        _tables(db)
        # A release-local merge may resolve the same raw target TaxID to species.
        # Both identities are sameTax before they reach this workload boundary.
        _insert(db, "START", "111", "100", "P10001")
        _insert(db, "END", "100", "100", "P20001")
        assert _merged_primary(b3, db, RELEASES, primary) == 2

    candidates, original_flags = load_v2_candidate_universe(
        targets_path=targets,
        primary_path=primary,
        audit_path=audit,
        releases=RELEASES,
    )
    expected = {
        (RELEASES["START"], "T1", "1"): ("T:111", "111", "P10001"),
        (RELEASES["END"], "T1", "1"): ("S:100", "100", "P20001"),
    }
    assert candidates.keys() == expected.keys()
    for key, (bucket, resolved, accession) in expected.items():
        candidate = candidates[key]
        assert candidate.primary_bucket == bucket
        assert candidate.candidate_resolved_taxid == resolved
        assert candidate.candidate_species_taxid == "100"
        assert candidate.candidate_accession == accession
        assert candidate.target_accession == "P99901"
        assert candidate.fragment_record
        assert candidate.discovery_channel == CHANNEL
        assert not candidate.sequence_discovered
        assert not candidate.go_positive_sequence_discovered
        assert not candidate.full_v2_rescue_sequence_discovered
        assert not candidate.lineage_discovered
        assert candidate.audit is None
        assert original_flags[key] == (False, 0, False)


@pytest.mark.parametrize(
    "resolved,species",
    [
        (None, "100"),
        ("", "100"),
        ("0", "100"),
        ("0111", "100"),
        ("111", None),
        ("111", ""),
        ("111", "0"),
        ("111", "0100"),
    ],
)
def test_missing_or_noncanonical_taxonomy_cannot_make_a_target_bucket(
    resolved: str | None,
    species: str | None,
) -> None:
    with closing(sqlite3.connect(":memory:")) as db:
        _tables(db)
        _insert(db, "START", resolved, species, "P10001")
        with pytest.raises(ValueError, match="record ordinal"):
            list(_extras(db, RELEASES))
