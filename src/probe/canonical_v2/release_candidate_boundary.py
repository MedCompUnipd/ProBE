"""Lossless release-local boundary between B3 and documentary V2 candidates.

This stage does NOT decide documentary eligibility, execute DP, or choose donors.
It cannot replace the eventual canonical Stage 06/07 implementation.
"""

from __future__ import annotations

import csv
import hashlib
import json
import os
import sqlite3
import tempfile
from collections import Counter
from contextlib import closing
from pathlib import Path

from probe.canonical_v2 import documentary_handoff
from probe.canonical_v2.interop import validate_b3_stage
from probe.canonical_v2.stage05_06a import PRIMARY, STORES

SCHEMA = "probe-canonical-v2-release-boundary-v1"
SUMMARY = "release_candidate_boundary.summary.json"
EVIDENCE_OUTPUT = "documentary_evidence_preserved.tsv"
ROWS = {
    "START": "START_candidate_boundary.tsv",
    "END": "END_candidate_boundary.tsv",
}
FIELDS = (
    "release", "target_id", "rid", "candidate_accession",
    "candidate_raw_ox", "candidate_resolved_taxid", "frag",
    "primary_present", "documentary_present", "primary_discovery_channel",
    "sequence_sha256", "documentary_evidence_observations",
    "documentary_literal_matches", "documentary_normalized_hints",
    "documentary_potential_locus_matches", "needs_full_materialization",
    "dp_completed", "documentary_approved", "go_donor_authorized",
    "documentary_sources_json", "exact_origin_releases_json",
)
_PRIMARY_REQUIRED = {
    "release", "target_id", "rid", "candidate_primary_accession",
    "raw_ox", "resolved_taxid", "frag", "discovery_channel",
}
_STORE_REQUIRED = {
    "release", "rid", "primary_accession", "raw_ox", "frag", "sequence",
}


def _sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _rows(path: Path, required: set[str]):
    if not path.is_file() or path.is_symlink():
        raise ValueError(f"Missing or unsafe input: {path}")
    with path.open(encoding="utf-8", newline="") as stream:
        reader = csv.DictReader(stream, delimiter="\t")
        header = reader.fieldnames
        if header is None or len(header) != len(set(header)):
            raise ValueError(f"Invalid input header: {path}")
        if not required.issubset(header):
            raise ValueError(f"Missing columns in {path}")
        for line, row in enumerate(reader, 2):
            if None in row or any(value is None for value in row.values()):
                raise ValueError(f"Malformed TSV row {path}:{line}")
            yield row


def _positive(value: str) -> None:
    if (
        not value
        or not value.isascii()
        or not value.isdecimal()
        or int(value) < 1
        or str(int(value)) != value
    ):
        raise ValueError(f"Invalid record identifier: {value!r}")


def _identity(row: dict[str, str], *, documentary: bool) -> tuple[str, ...]:
    if documentary:
        return tuple(row[name] for name in (
            "candidate_accession", "candidate_raw_ox",
            "candidate_resolved_taxid", "candidate_frag",
        ))
    return tuple(row[name] for name in (
        "candidate_primary_accession", "raw_ox", "resolved_taxid", "frag",
    ))


def _insert_primary(
    db: sqlite3.Connection, path: Path, releases: dict[str, str],
    counts: Counter[str],
) -> None:
    for row in _rows(path, _PRIMARY_REQUIRED):
        role = next(
            (name for name, release in releases.items()
             if release == row["release"]),
            None,
        )
        if role is None or not row["target_id"]:
            raise ValueError("Primary candidate has wrong release or target")
        _positive(row["rid"])
        identity = _identity(row, documentary=False)
        if not all(identity[:2]) or identity[3] not in {"0", "1"}:
            raise ValueError("Invalid primary candidate identity")
        try:
            db.execute(
                "INSERT INTO candidates (role,target,rid,identity,channel) "
                "VALUES (?,?,?,?,?)",
                (role, row["target_id"], row["rid"],
                 json.dumps(identity), row["discovery_channel"]),
            )
        except sqlite3.IntegrityError as error:
            raise ValueError("Duplicate primary candidate key") from error
        counts[f"{role}_primary"] += 1


def _insert_documentary(
    db: sqlite3.Connection, path: Path, releases: dict[str, str],
    counts: Counter[str],
) -> None:
    for row in _rows(path, set(documentary_handoff.CANDIDATE_FIELDS)):
        role = next(
            (name for name, release in releases.items()
             if release == row["release"]),
            None,
        )
        if role is None or not row["target_id"]:
            raise ValueError("Documentary candidate has wrong release/target")
        _positive(row["rid"])
        _positive(row["target_resolved_taxid"])
        _positive(row["candidate_resolved_taxid"])
        if row["target_resolved_taxid"] != row["candidate_resolved_taxid"]:
            raise ValueError("Cross-TaxID documentary candidate")
        if row["go_donor_authorized"] != "0":
            raise ValueError("Discovery attempted to authorize GO donation")
        if row["potential_dp_input"] not in {"0", "1"}:
            raise ValueError("Invalid potential DP hint")
        identity = _identity(row, documentary=True)
        if not all(identity[:3]) or identity[3] not in {"0", "1"}:
            raise ValueError("Invalid documentary candidate identity")
        digest = row["candidate_sequence_sha256"]
        if len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
            raise ValueError("Invalid documentary sequence SHA256")
        numeric = (
            "evidence_observations", "literal_matches", "normalized_hints",
            "potential_unique_locus_matches",
        )
        for field in numeric:
            value = row[field]
            if not value.isascii() or not value.isdecimal():
                raise ValueError(f"Invalid documentary count: {field}")
        if int(row["evidence_observations"]) < 1:
            raise ValueError("Candidate without documentary evidence")
        if (
            int(row["literal_matches"]) + int(row["normalized_hints"])
            != int(row["evidence_observations"])
        ):
            raise ValueError("Inconsistent literal and normalized counts")
        if (int(row["potential_unique_locus_matches"]) > 0) != (
            row["potential_dp_input"] == "1"
        ):
            raise ValueError("Inconsistent potential locus flag")
        key = (role, row["target_id"], row["rid"])
        found = db.execute(
            "SELECT identity,documentary FROM candidates WHERE role=? AND target=? "
            "AND rid=?", key,
        ).fetchone()
        if found is not None and found[1] is not None:
            raise ValueError(f"Duplicate documentary candidate: {key}")
        if found is not None and tuple(json.loads(found[0])) != identity:
            raise ValueError(f"Conflicting primary/documentary identity: {key}")
        if found is None:
            db.execute(
                "INSERT INTO candidates (role,target,rid,identity,channel) "
                "VALUES (?,?,?,?,?)", (*key, json.dumps(identity), ""),
            )
        db.execute(
            "UPDATE candidates SET documentary=?,seq_sha=? "
            "WHERE role=? AND target=? AND rid=?",
            (json.dumps(row, sort_keys=True), digest, *key),
        )
        counts[f"{role}_documentary"] += 1
        if found is not None:
            counts[f"{role}_overlap"] += 1


def _validate_evidence(
    db: sqlite3.Connection, path: Path, releases: dict[str, str],
    destination: Path, counts: Counter[str],
) -> None:
    with destination.open("x", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(
            stream, fieldnames=documentary_handoff.EVIDENCE_FIELDS, delimiter="\t",
            lineterminator="\n", extrasaction="raise",
        )
        writer.writeheader()
        for row in _rows(path, set(documentary_handoff.EVIDENCE_FIELDS)):
            role = next(
                (name for name, release in releases.items()
                 if release == row["release"]),
                None,
            )
            if role is None or not row["target_id"]:
                raise ValueError("Documentary observation crossed release")
            _positive(row["candidate_rid"])
            key = (role, row["target_id"], row["candidate_rid"])
            documentary_handoff._verify_row(
                row, releases[role], frozenset(releases.values())
            )
            fact = db.execute(
                "SELECT documentary FROM candidates "
                "WHERE role=? AND target=? AND rid=?", key,
            ).fetchone()
            if fact is None or not fact[0]:
                raise ValueError("Orphan documentary observation")
            document = json.loads(fact[0])
            for left, right in (
                ("candidate_accession", "candidate_accession"),
                ("candidate_raw_ox", "candidate_raw_ox"),
                ("candidate_resolved_taxid", "candidate_resolved_taxid"),
                ("candidate_sequence_sha256", "candidate_sequence_sha256"),
            ):
                if row[left] != document[right]:
                    raise ValueError("Documentary observation identity conflict")
            expected = str(int(documentary_handoff._locus_support(row)))
            if row["potential_unique_locus"] != expected:
                raise ValueError("Documentary locus hint misrepresented")
            db.execute(
                "INSERT INTO evidence_count VALUES (?,?,?,?,?,?,?) "
                "ON CONFLICT(role,target,rid) DO UPDATE SET "
                "total=total+1,literal=literal+excluded.literal,"
                "hint=hint+excluded.hint,locus=locus+excluded.locus",
                (*key, 1, int(row["match_class"] == "LITERAL"),
                 int(row["match_class"] == "NORMALIZED_HINT"), int(expected)),
            )
            writer.writerow(row)
            counts["documentary_observations"] += 1
    for role, target, rid, payload in db.execute(
        "SELECT role,target,rid,documentary FROM candidates "
        "WHERE documentary IS NOT NULL"
    ):
        expected = json.loads(payload)
        actual = db.execute(
            "SELECT total,literal,hint,locus FROM evidence_count "
            "WHERE role=? AND target=? AND rid=?", (role, target, rid),
        ).fetchone()
        counts_expected = tuple(int(expected[field]) for field in (
            "evidence_observations", "literal_matches", "normalized_hints",
            "potential_unique_locus_matches",
        ))
        if actual != counts_expected:
            raise ValueError("Handoff candidate/evidence count mismatch")


def _sha_sequence(sequence: str) -> str:
    return hashlib.sha256(sequence.encode("ascii")).hexdigest()


def _verify_overlap_sequence(
    db: sqlite3.Connection, b3: Path, releases: dict[str, str],
) -> None:
    for role, release in releases.items():
        path = b3 / STORES[role]
        for row in _rows(path, _STORE_REQUIRED):
            if row["release"] != release:
                raise ValueError("Sequence store crossed release")
            _positive(row["rid"])
            matches = db.execute(
                "SELECT identity,seq_sha FROM candidates WHERE role=? AND rid=? "
                "AND channel!='' AND documentary IS NOT NULL",
                (role, row["rid"]),
            )
            for identity_raw, digest in matches:
                identity = json.loads(identity_raw)
                if (
                    identity[0] != row["primary_accession"]
                    or identity[1] != row["raw_ox"]
                    or identity[3] != row["frag"]
                    or _sha_sequence(row["sequence"]) != digest
                ):
                    raise ValueError("B3/documentary FULL sequence conflict")
            db.execute(
                "UPDATE candidates SET overlap_verified=1 WHERE role=? "
                "AND rid=? AND channel!='' AND documentary IS NOT NULL",
                (role, row["rid"]),
            )
            db.execute(
                "UPDATE candidates SET seq_sha=COALESCE(seq_sha,?) "
                "WHERE role=? AND rid=? AND channel!=''",
                (_sha_sequence(row["sequence"]), role, row["rid"]),
            )
    remaining = db.execute(
        "SELECT COUNT(*) FROM candidates WHERE channel!='' "
        "AND (seq_sha IS NULL OR (documentary IS NOT NULL "
        "AND overlap_verified=0))"
    ).fetchone()[0]
    if remaining:
        raise ValueError("B3 candidate lacks FULL sequence attestation")


def _write(
    db: sqlite3.Connection, path: Path, role: str, release: str,
) -> int:
    written = 0
    with path.open("x", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(
            stream, fieldnames=FIELDS, delimiter="\t", lineterminator="\n",
            extrasaction="raise",
        )
        writer.writeheader()
        query = (
            "SELECT target,rid,identity,channel,documentary,seq_sha "
            "FROM candidates WHERE role=? ORDER BY target,CAST(rid AS INTEGER)"
        )
        for target, rid, encoded, channel, raw, digest in db.execute(query, (role,)):
            accession, ox, tax, frag = json.loads(encoded)
            doc = {} if raw is None else json.loads(raw)
            record = {
                "release": release, "target_id": target, "rid": rid,
                "candidate_accession": accession, "candidate_raw_ox": ox,
                "candidate_resolved_taxid": tax, "frag": frag,
                "primary_present": str(int(bool(channel))),
                "documentary_present": str(int(bool(doc))),
                "primary_discovery_channel": channel,
                "sequence_sha256": digest or "",
                "documentary_evidence_observations": doc.get(
                    "evidence_observations", "0"
                ),
                "documentary_literal_matches": doc.get("literal_matches", "0"),
                "documentary_normalized_hints": doc.get("normalized_hints", "0"),
                "documentary_potential_locus_matches": doc.get(
                    "potential_unique_locus_matches", "0"
                ),
                "needs_full_materialization": str(int(not channel)),
                "dp_completed": "0", "documentary_approved": "0",
                "go_donor_authorized": "0",
                "documentary_sources_json": doc.get("sources_json", "[]"),
                "exact_origin_releases_json": doc.get(
                    "origin_releases_json", "[]"
                ),
            }
            writer.writerow(record)
            written += 1
    return written


def prepare_release_candidate_boundary(
    *, b3: Path, handoff: Path, releases: dict[str, str], destination: Path,
) -> dict:
    """Join candidate identity per release, preserving every documentary row."""
    if set(releases) != {"START", "END"}:
        raise ValueError("Expected START and END releases")
    validate_b3_stage(b3, releases)
    if not handoff.is_dir() or handoff.is_symlink():
        raise ValueError("Unsafe documentary handoff directory")
    summary_path = handoff / documentary_handoff.SUMMARY
    data = json.loads(summary_path.read_text(encoding="utf-8"))
    if (
        data.get("schema") != documentary_handoff.SCHEMA
        or data.get("releases") != releases
        or data.get("go_donor_decisions") != 0
        or data.get("dp_authorized") is not False
        or data.get("dp_executed") is not False
        or data.get("output_sha256", {}).get(documentary_handoff.CANDIDATES)
        != _sha(handoff / documentary_handoff.CANDIDATES)
        or data.get("output_sha256", {}).get(documentary_handoff.EVIDENCE)
        != _sha(handoff / documentary_handoff.EVIDENCE)
    ):
        raise ValueError("Unverified documentary handoff schema or SHA256")
    if destination.exists() or destination.is_symlink():
        raise FileExistsError("Release-boundary publication already exists")
    if not destination.parent.is_dir() or destination.parent.is_symlink():
        raise ValueError("Unsafe output parent")
    if any(destination in source.parents or source in destination.parents
           or destination == source for source in (handoff, b3)):
        raise ValueError("Unsafe source/destination nesting")
    counts: Counter[str] = Counter()
    with tempfile.TemporaryDirectory(
        dir=destination.parent, prefix="probe-canonical-boundary-"
    ) as temporary:
        staged = Path(temporary)
        output = staged / "product"
        output.mkdir()
        with closing(sqlite3.connect(staged / "scratch.sqlite3")) as db:
            db.execute(
                "CREATE TABLE candidates (role TEXT,target TEXT,rid TEXT,"
                "identity TEXT,channel TEXT,documentary TEXT,seq_sha TEXT,"
                "overlap_verified INTEGER DEFAULT 0,"
                "PRIMARY KEY(role,target,rid))"
            )
            db.execute(
                "CREATE TABLE evidence_count (role TEXT,target TEXT,rid TEXT,"
                "total INTEGER,literal INTEGER,hint INTEGER,locus INTEGER,"
                "PRIMARY KEY(role,target,rid))"
            )
            _insert_primary(db, b3 / PRIMARY, releases, counts)
            _insert_documentary(
                db, handoff / documentary_handoff.CANDIDATES, releases, counts
            )
            _validate_evidence(
                db, handoff / documentary_handoff.EVIDENCE, releases,
                output / EVIDENCE_OUTPUT, counts,
            )
            _verify_overlap_sequence(db, b3, releases)
            for role in ("START", "END"):
                counts[f"{role}_unique"] = _write(
                    db, output / ROWS[role], role, releases[role],
                )
                if counts[f"{role}_unique"] != (
                    counts[f"{role}_primary"]
                    + counts[f"{role}_documentary"]
                    - counts[f"{role}_overlap"]
                ):
                    raise ValueError("Candidate union cardinality mismatch")
        summary = {
            "schema": SCHEMA,
            "releases": releases,
            "boundary": "RELEASE_LOCAL_CANDIDATES_ONLY",
            "documentary_approval_performed": False,
            "dp_executed": False,
            "go_donor_decisions": 0,
            "counts": dict(sorted(counts.items())),
            "inputs_sha256": {
                "b3_summary": _sha(b3 / "canonical_stage05_06a.summary.json"),
                "handoff_summary": _sha(summary_path),
            },
            "outputs_sha256": {
                name: _sha(output / name)
                for name in (*ROWS.values(), EVIDENCE_OUTPUT)
            },
        }
        if (
            summary["inputs_sha256"]["b3_summary"]
            != _sha(b3 / "canonical_stage05_06a.summary.json")
            or summary["inputs_sha256"]["handoff_summary"]
            != _sha(summary_path)
        ):
            raise ValueError("Publication inputs changed during processing")
        (output / SUMMARY).write_text(
            json.dumps(summary, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        if destination.exists() or destination.is_symlink():
            raise FileExistsError("Concurrent release-boundary publication")
        os.rename(output, destination)
    return summary
