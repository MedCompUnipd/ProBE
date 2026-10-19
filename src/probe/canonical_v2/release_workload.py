"""Build release-local complete 06A inputs from the two-lane V2 boundary.

Documentary lookup expands candidate search, not GO donor authorization.
Only literal documentary matches join alignment workload; normalized hints
are retained as evidence but do not, alone, authorize DP.
"""

from __future__ import annotations

import csv
import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
from collections import Counter
from contextlib import closing
from pathlib import Path

from probe.canonical_v2.interop import validate_b3_stage
from probe.canonical_v2.primary_projection import OUTPUT_FIELDS
from probe.canonical_v2.release_candidate_boundary import (
    EVIDENCE_OUTPUT,
    ROWS,
    prepare_release_candidate_boundary,
)
from probe.canonical_v2.stage05_06a import (
    AUDIT,
    PRIMARY,
    STORES,
    _full_fasta,
)
from probe.stage2c_alignment_preview import (
    AUDIT_OUT,
    END_STORE,
    JOBS_OUT,
    JOBS_SUMMARY,
    START_STORE,
    SUMMARY_OUT,
    UNION_OUT,
)
from probe.stage2c_full_metadata_recovery import _parse_header
from probe.stage2c_metadata_reuse import STORE_FIELDS

SCHEMA = "probe-canonical-v2-complete-workload-v1"
CHANNEL = "CANONICAL_DOCUMENTARY_V2"
DEFERRED = "documentary_normalized_only_deferred.tsv"
EVIDENCE = "documentary_evidence_preserved.tsv"
# Preserve UniProt ambiguity codes literally; no wildcard or EXACT approval.
_ALLOWED = frozenset(b"ACDEFGHIKLMNPQRSTVWYUOBJXZ")


def _sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _read(path: Path, required: set[str]):
    if not path.is_file() or path.is_symlink():
        raise ValueError(f"Missing or unsafe input: {path}")
    with path.open(encoding="utf-8", newline="") as stream:
        reader = csv.DictReader(stream, delimiter="\t")
        fields = reader.fieldnames
        if fields is None or len(fields) != len(set(fields)):
            raise ValueError(f"Invalid TSV header: {path}")
        if not required.issubset(fields):
            raise ValueError(f"Missing TSV columns in {path}")
        for number, row in enumerate(reader, 2):
            if None in row or any(value is None for value in row.values()):
                raise ValueError(f"Malformed TSV {path}:{number}")
            yield row


def _number(value: str) -> int:
    if not value or not value.isascii() or not value.isdecimal():
        raise ValueError(f"Invalid record ordinal: {value!r}")
    number = int(value)
    if number < 1 or str(number) != value:
        raise ValueError(f"Noncanonical record ordinal: {value!r}")
    return number


def _order(row: dict[str, str], releases: dict[str, str]):
    role = next(
        (name for name in ("START", "END") if releases[name] == row["release"]), None
    )
    if role is None or not row["target_id"]:
        raise ValueError("Candidate crossed a release/target boundary")
    return (0 if role == "START" else 1, row["target_id"], _number(row["rid"]))


def _write(path: Path, fields, rows) -> int:
    count = 0
    with path.open("x", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(
            stream,
            fieldnames=fields,
            delimiter="\t",
            lineterminator="\n",
            extrasaction="raise",
        )
        writer.writeheader()
        for row in rows:
            writer.writerow(row)
            count += 1
    return count


def _collect_pairs(
    db: sqlite3.Connection,
    boundary: Path,
    releases: dict[str, str],
    deferred: Path,
    counts: Counter[str],
) -> None:
    def pending():
        for role in ("START", "END"):
            for row in _read(
                boundary / ROWS[role],
                {
                    "release",
                    "target_id",
                    "rid",
                    "primary_present",
                    "documentary_present",
                    "documentary_literal_matches",
                    "candidate_accession",
                    "candidate_raw_ox",
                    "candidate_resolved_taxid",
                    "frag",
                    "sequence_sha256",
                    "documentary_normalized_hints",
                    "go_donor_authorized",
                },
            ):
                if row["release"] != releases[role]:
                    raise ValueError("Release boundary contains foreign candidate")
                if row["go_donor_authorized"] != "0":
                    raise ValueError("Discovery pretended to authorize GO")
                if row["primary_present"] == "1":
                    continue
                if row["documentary_present"] != "1":
                    raise ValueError("Orphan documentary candidate")
                literal = int(row["documentary_literal_matches"])
                if literal == 0:
                    counts[f"{role}_deferred_hints"] += 1
                    yield row
                    continue
                rid = _number(row["rid"])
                identity = (
                    row["candidate_accession"],
                    row["candidate_raw_ox"],
                    row["candidate_resolved_taxid"],
                    row["frag"],
                    row["sequence_sha256"],
                )
                if not all(identity[:3]) or identity[3] not in {"0", "1"}:
                    raise ValueError("Invalid documentary FULL record identity")
                saved = db.execute(
                    "SELECT identity FROM requests WHERE role=? AND rid=?",
                    (role, rid),
                ).fetchone()
                encoded = json.dumps(identity)
                if saved is not None and saved[0] != encoded:
                    raise ValueError("Conflicting documentary identity for RID")
                db.execute(
                    "INSERT OR IGNORE INTO requests VALUES (?,?,?,NULL)",
                    (role, rid, encoded),
                )
                db.execute(
                    "INSERT INTO extra (role,target,rid,identity) VALUES (?,?,?,?)",
                    (role, row["target_id"], rid, encoded),
                )
                counts[f"{role}_literal_candidates"] += 1

    fields = (
        "release",
        "target_id",
        "rid",
        "candidate_accession",
        "documentary_normalized_hints",
        "documentary_literal_matches",
    )
    _write(
        deferred, fields, ({name: row[name] for name in fields} for row in pending())
    )
    for row in _read(
        boundary / EVIDENCE_OUTPUT,
        {
            "release",
            "target_id",
            "candidate_rid",
            "candidate_species_taxid",
        },
    ):
        role = next(
            (name for name in releases if releases[name] == row["release"]), None
        )
        if role is None:
            raise ValueError("Foreign documentary evidence release")
        species = row["candidate_species_taxid"]
        if species:
            _number(species)
        found = db.execute(
            "SELECT species FROM extra WHERE role=? AND target=? AND rid=?",
            (role, row["target_id"], _number(row["candidate_rid"])),
        ).fetchone()
        if found is None:
            continue
        if found[0] and species and found[0] != species:
            raise ValueError("Contradictory documentary species taxonomy")
        if species:
            db.execute(
                "UPDATE extra SET species=? WHERE role=? AND target=? AND rid=?",
                (species, role, row["target_id"], int(row["candidate_rid"])),
            )
    missing = db.execute("SELECT COUNT(*) FROM extra WHERE species IS NULL").fetchone()[
        0
    ]
    if missing:
        raise ValueError("Documentary FULL species taxonomy not observed")


def _full_row(header: bytes, sequence: bytes, rid: int) -> dict[str, str]:
    fields = _parse_header(header, rid)
    residues = sequence.rstrip(b"\r\n")
    if (
        fields["RID"] != str(rid)
        or fields["LEN"] != str(len(residues))
        or not residues
        or any(letter not in _ALLOWED for letter in residues)
    ):
        raise ValueError(f"Invalid FULL FASTA record {rid}")
    return {
        "rid": str(rid),
        "primary_accession": fields["primary_accession"],
        "entry": fields["entry"],
        "raw_ox": fields["OX"],
        "frag": fields["FRAG"],
        "sequence_length": fields["LEN"],
        "pe": fields["PE"],
        "sv": fields["SV"],
        "sec": fields.get("SEC", ""),
        "gene": fields.get("GENE", ""),
        "sequence": residues.decode("ascii"),
    }


def _accept_full(
    db: sqlite3.Connection,
    role: str,
    rid: int,
    row: dict[str, str],
) -> None:
    saved = db.execute(
        "SELECT identity,full FROM requests WHERE role=? AND rid=?",
        (role, rid),
    ).fetchone()
    if saved is None:
        return
    accession, ox, tax, frag, digest = json.loads(saved[0])
    if (
        accession != row["primary_accession"]
        or ox != row["raw_ox"]
        or frag != row["frag"]
        or digest != hashlib.sha256(row["sequence"].encode("ascii")).hexdigest()
    ):
        raise ValueError(f"Documentary FULL identity mismatch: {role}/{rid}")
    _number(tax)
    if saved[1] is not None:
        raise ValueError(f"Duplicate FULL sequence for {role}/{rid}")
    db.execute(
        "UPDATE requests SET full=? WHERE role=? AND rid=?",
        (json.dumps(row), role, rid),
    )


def _recover_full(
    db: sqlite3.Connection,
    b3: Path,
    releases: dict[str, str],
    manifests: dict[str, Path],
    counts: Counter[str],
) -> None:
    for role in ("START", "END"):
        for row in _read(b3 / STORES[role], set(STORE_FIELDS)):
            if row["release"] != releases[role]:
                raise ValueError("Original sequence store crossed release")
            if db.execute(
                "SELECT 1 FROM requests WHERE role=? AND rid=?",
                (role, _number(row["rid"])),
            ).fetchone():
                _accept_full(db, role, int(row["rid"]), row)
                counts[f"{role}_reused_rid"] += 1
        need = db.execute(
            "SELECT COUNT(*) FROM requests WHERE role=? AND full IS NULL",
            (role,),
        ).fetchone()[0]
        if need == 0:
            continue
        fasta, expected = _full_fasta(manifests[role], releases[role], role)
        digest = hashlib.sha256()
        pending = db.execute(
            "SELECT rid FROM requests WHERE role=? AND full IS NULL ORDER BY rid",
            (role,),
        )
        wanted = pending.fetchone()
        with fasta.open("rb") as source:
            rid = 0
            while header := source.readline():
                sequence = source.readline()
                if not sequence or not sequence.endswith(b"\n"):
                    raise ValueError("Truncated FULL FASTA record")
                rid += 1
                digest.update(header)
                digest.update(sequence)
                if wanted is not None and wanted[0] == rid:
                    row = _full_row(header, sequence, rid)
                    _accept_full(db, role, rid, row)
                    counts[f"{role}_recovered_rid"] += 1
                    wanted = pending.fetchone()
        if digest.hexdigest() != expected:
            raise ValueError("FULL FASTA source SHA256 mismatch")
        if wanted is not None:
            raise ValueError("Documentary RID is beyond FULL FASTA EOF")
        remaining = db.execute(
            "SELECT COUNT(*) FROM requests WHERE role=? AND full IS NULL",
            (role,),
        ).fetchone()[0]
        if remaining:
            raise ValueError("Unresolved documentary FULL RID")


def _extras(db: sqlite3.Connection, releases: dict[str, str]):
    cursor = db.execute(
        "SELECT role,target,rid,identity,species FROM extra "
        "ORDER BY CASE role WHEN 'START' THEN 0 ELSE 1 END,target,rid"
    )
    for role, target, rid, encoded, species in cursor:
        accession, ox, tax, frag, _ = json.loads(encoded)
        _number(tax)
        _number(species)
        # The verified documentary handoff requires candidate TaxID == target
        # TaxID. With release-local species evidence, use the native 02B bucket:
        # S:<species> at species rank, otherwise T:<resolved target TaxID>.
        target_bucket = f"S:{species}" if tax == species else f"T:{tax}"
        full = json.loads(
            db.execute(
                "SELECT full FROM requests WHERE role=? AND rid=?",
                (role, rid),
            ).fetchone()[0]
        )
        row = dict.fromkeys(OUTPUT_FIELDS, "")
        row.update(
            {
                "release": releases[role],
                "target_id": target,
                "rid": str(rid),
                "discovery_channel": CHANNEL,
                "sequence_discovered": "0",
                "go_positive_sequence_discovered": "0",
                "full_v2_rescue_sequence_discovered": "0",
                "lineage_discovered": "0",
                "full_exact_species_wide": "0",
                "full_v2_record_number": str(rid),
                "full_ordinal_state": "CONSISTENT",
                "candidate_primary_accession": accession,
                "raw_ox": ox,
                "resolved_taxid": tax,
                "frag": frag,
                "species_taxid": species,
                "target_primary_bucket": target_bucket,
                "metadata_source": "FULL_V2_DOCUMENTARY",
                "sequence_evidence_state": "FULL_V2_VERIFIED",
                "requires_full_sequence_materialization": "0",
            }
        )
        for column, name in (
            ("entry", "entry"),
            ("sequence_length", "sequence_length"),
            ("pe", "pe"),
            ("sv", "sv"),
            ("sec", "sec"),
            ("gene", "gene"),
        ):
            row[column] = full[name]
        yield row


def _merged_primary(
    b3: Path,
    db: sqlite3.Connection,
    releases: dict[str, str],
    destination: Path,
) -> int:
    def records():
        extras = iter(_extras(db, releases))
        next_extra = next(extras, None)
        previous = None
        for row in _read(b3 / PRIMARY, set(OUTPUT_FIELDS)):
            key = _order(row, releases)
            if previous is not None and key <= previous:
                raise ValueError("Unsorted B3 primary candidate input")
            while next_extra is not None and _order(next_extra, releases) < key:
                added = _order(next_extra, releases)
                if previous is not None and added <= previous:
                    raise ValueError("Duplicated documentary candidate")
                previous = added
                yield next_extra
                next_extra = next(extras, None)
            if next_extra is not None and _order(next_extra, releases) == key:
                raise ValueError("Boundary missed primary/documentary overlap")
            previous = key
            yield row
        while next_extra is not None:
            added = _order(next_extra, releases)
            if previous is not None and added <= previous:
                raise ValueError("Unsorted documentary candidate")
            previous = added
            yield next_extra
            next_extra = next(extras, None)

    return _write(destination, OUTPUT_FIELDS, records())


def _store_rows(
    db: sqlite3.Connection,
    b3: Path,
    role: str,
    release: str,
):
    cursor = db.execute(
        "SELECT rid,full FROM requests WHERE role=? ORDER BY rid", (role,)
    )
    existing = iter(_read(b3 / STORES[role], set(STORE_FIELDS)))
    current = next(existing, None)
    last = 0
    for rid, raw in cursor:
        while current is not None and _number(current["rid"]) < rid:
            number = _number(current["rid"])
            if number <= last:
                raise ValueError("Unsorted B3 FULL store")
            last = number
            yield current
            current = next(existing, None)
        count = db.execute(
            "SELECT COUNT(*) FROM extra WHERE role=? AND rid=?", (role, rid)
        ).fetchone()[0]
        if current is not None and _number(current["rid"]) == rid:
            last = rid
            record = dict(current)
            record["needed_by_primary"] = "1"
            record["primary_reference_count"] = str(
                int(record["primary_reference_count"]) + count
            )
            yield record
            current = next(existing, None)
            continue
        if rid <= last:
            raise ValueError("Non-monotone added FULL store record")
        last = rid
        record = json.loads(raw)
        yield {
            **{name: "0" for name in STORE_FIELDS},
            **record,
            "release": release,
            "full_v2_record_number": str(rid),
            "needed_by_primary": "1",
            "needed_by_taxonomy_audit": "0",
            "primary_reference_count": str(count),
            "audit_reference_count": "0",
        }
    while current is not None:
        number = _number(current["rid"])
        if number <= last:
            raise ValueError("Duplicate or unsorted B3 FULL record")
        last = number
        yield current
        current = next(existing, None)


def _merged_stores(
    b3: Path,
    db: sqlite3.Connection,
    releases: dict[str, str],
    product: Path,
) -> None:
    for role, name in (("START", START_STORE), ("END", END_STORE)):
        _write(
            product / name,
            STORE_FIELDS,
            _store_rows(db, b3, role, releases[role]),
        )


def _audit_copy(
    b3: Path,
    db: sqlite3.Connection,
    releases: dict[str, str],
    destination: Path,
) -> None:
    with (b3 / AUDIT).open(encoding="utf-8", newline="") as stream:
        reader = csv.DictReader(stream, delimiter="\t")
        fields = reader.fieldnames
    if not fields or "record_already_in_primary_union_for_target" not in fields:
        raise ValueError("Invalid taxonomy audit schema")

    def records():
        for row in _read(b3 / AUDIT, set(fields)):
            role = next((r for r in releases if releases[r] == row["release"]), None)
            if role is None:
                raise ValueError("Foreign taxonomy audit release")
            present = db.execute(
                "SELECT 1 FROM extra WHERE role=? AND target=? AND rid=?",
                (role, row["target_id"], _number(row["rid"])),
            ).fetchone()
            if present is not None:
                row["record_already_in_primary_union_for_target"] = "1"
            yield row

    _write(destination, fields, records())


def _run_06a(
    cli: Path,
    targets: Path,
    releases: dict[str, str],
    product: Path,
) -> None:
    subprocess.run(
        [
            sys.executable,
            str(cli),
            "--targets",
            str(targets),
            "--candidate-union",
            str(product / UNION_OUT),
            "--taxonomy-audit",
            str(product / AUDIT_OUT),
            "--start-sequence-store",
            str(product / START_STORE),
            "--end-sequence-store",
            str(product / END_STORE),
            "--start-release-id",
            releases["START"],
            "--end-release-id",
            releases["END"],
            "--output",
            str(product / JOBS_OUT),
            "--summary",
            str(product / JOBS_SUMMARY),
        ],
        check=True,
    )


def build_release_workload(
    *,
    b3: Path,
    handoff: Path,
    releases: dict[str, str],
    targets: Path,
    full_manifests: dict[str, Path],
    stage06a_cli: Path,
    destination: Path,
) -> dict:
    """Publish a complete 06A preview, without authorizing documentary GO."""
    if (
        set(releases) != {"START", "END"}
        or len(set(releases.values())) != 2
        or set(full_manifests) != set(releases)
    ):
        raise ValueError("Invalid START/END release specification")
    for source in (targets, stage06a_cli, *full_manifests.values()):
        if not source.is_file() or source.is_symlink():
            raise ValueError(f"Unsafe canonical input: {source}")
    b3_summary = validate_b3_stage(b3, releases)
    pins = b3_summary["inputs_sha256"]
    if (
        pins.get("01_targets") != _sha(targets)
        or pins.get("06a_cli") != _sha(stage06a_cli)
        or any(
            pins.get(f"{role}_01a_manifest") != _sha(full_manifests[role])
            for role in ("START", "END")
        )
    ):
        raise ValueError("B3 upstream input SHA256 changed")
    if destination.exists() or destination.is_symlink():
        raise FileExistsError("Canonical workload destination occupied")
    if not destination.parent.is_dir() or destination.parent.is_symlink():
        raise ValueError("Unsafe output parent directory")
    for folder in (b3, handoff):
        if (
            destination.resolve() == folder.resolve()
            or destination.resolve().is_relative_to(folder.resolve())
            or folder.resolve().is_relative_to(destination.resolve())
        ):
            raise ValueError("Unsafe source/destination nesting")
    counts: Counter[str] = Counter()
    with tempfile.TemporaryDirectory(
        prefix="probe-v2-complete-workload-", dir=destination.parent
    ) as temporary:
        staged = Path(temporary)
        boundary = staged / "boundary"
        prepare_release_candidate_boundary(
            b3=b3,
            handoff=handoff,
            releases=releases,
            destination=boundary,
        )
        product = staged / "product"
        product.mkdir()
        with closing(sqlite3.connect(staged / "candidate.sqlite3")) as db:
            db.execute(
                "CREATE TABLE requests (role TEXT,rid INTEGER,identity TEXT,"
                "full TEXT,PRIMARY KEY(role,rid))"
            )
            db.execute(
                "CREATE TABLE extra (role TEXT,target TEXT,rid INTEGER,"
                "identity TEXT,species TEXT,"
                "PRIMARY KEY(role,target,rid))"
            )
            _collect_pairs(db, boundary, releases, product / DEFERRED, counts)
            _recover_full(db, b3, releases, full_manifests, counts)
            counts["total_candidate_pairs"] = _merged_primary(
                b3, db, releases, product / UNION_OUT
            )
            _merged_stores(b3, db, releases, product)
            _audit_copy(b3, db, releases, product / AUDIT_OUT)
        shutil.copyfile(boundary / EVIDENCE_OUTPUT, product / EVIDENCE)
        _run_06a(stage06a_cli, targets, releases, product)
        checksums = {
            name: _sha(product / name)
            for name in (
                UNION_OUT,
                AUDIT_OUT,
                START_STORE,
                END_STORE,
                JOBS_OUT,
                JOBS_SUMMARY,
                DEFERRED,
                EVIDENCE,
            )
        }
        summary = {
            "schema": "stage2c-alignment-preview-v1",
            "canonical_contract": SCHEMA,
            "releases": releases,
            "alignment_executed": False,
            "donor_decisions": 0,
            "production_stages_replaced": False,
            "documentary_donor_authorized": False,
            "documentary_sources_preserved": True,
            "legacy_resolver_compatible": False,
            "counts": dict(sorted(counts.items())),
            "input_sha256": {
                str(destination / UNION_OUT): checksums[UNION_OUT],
                "b3_summary": _sha(b3 / "canonical_stage05_06a.summary.json"),
                "handoff_summary": _sha(handoff / "documentary_handoff.summary.json"),
                "targets": _sha(targets),
            },
            "output_sha256": checksums,
        }
        (product / SUMMARY_OUT).write_text(
            json.dumps(summary, sort_keys=True, indent=2) + "\n",
            encoding="utf-8",
        )
        if destination.exists() or destination.is_symlink():
            raise FileExistsError("Concurrent canonical workload publication")
        os.rename(product, destination)
    return summary
