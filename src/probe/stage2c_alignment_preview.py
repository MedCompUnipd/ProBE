"""Build isolated Stage 2C variants of 05A2, 05C2, 05D and 06A.

No canonical input is changed, and no GO donor decision is made. The
canonical 06A implementation checks literal sequence identity at execution.
"""

from __future__ import annotations

import csv
import hashlib
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
from collections import Counter
from pathlib import Path

from probe.stage2c_go_sequence_recovery import (
    ANNEX_NAME as GO_ANNEX,
)
from probe.stage2c_go_sequence_recovery import (
    SUMMARY_NAME as GO_SUMMARY,
)
from probe.stage2c_metadata_reuse import STORE_FIELDS
from probe.stage2c_primary_projection import (
    ADDITIONS_NAME,
    AUDIT_NAME,
    OUTPUT_NAME,
)
from probe.stage2c_primary_projection import (
    SUMMARY_NAME as PROJECTION_SUMMARY,
)
from probe.stage2c_sequence_coverage import (
    ANNEX_FIELDS,
    COVERAGE_FIELDS,
    COVERAGE_NAME,
    PENDING_NAME,
)
from probe.stage2c_sequence_coverage import (
    ANNEX_NAME as FULL_ANNEX,
)
from probe.stage2c_sequence_coverage import (
    SUMMARY_NAME_OUT as COVERAGE_SUMMARY,
)

SCHEMA = "stage2c-alignment-preview-v1"
UNION_OUT = "2C_05A2_variant.tsv"
AUDIT_OUT = "2C_05C2_variant.tsv"
START_STORE = "2C_05D_START_variant.tsv"
END_STORE = "2C_05D_END_variant.tsv"
JOBS_OUT = "2C_06A_alignment_jobs.tsv"
JOBS_SUMMARY = "2C_06A_summary.json"
SUMMARY_OUT = "2C_alignment_preview.summary.json"
STORE_META = (
    "primary_accession",
    "entry",
    "raw_ox",
    "frag",
    "sequence_length",
    "pe",
    "sv",
    "sec",
    "gene",
)


def _hash(path: Path) -> str:
    sha = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            sha.update(chunk)
    return sha.hexdigest()


def _positive(raw: str, label: str) -> str:
    if not raw.isascii() or not raw.isdecimal():
        raise ValueError(f"Invalid {label}: {raw!r}")
    parsed = int(raw)
    if parsed < 1 or str(parsed) != raw:
        raise ValueError(f"Noncanonical {label}: {raw!r}")
    return raw


def _read(path: Path, required: set[str], *, legacy: bool = False):
    with path.open("r", encoding="utf-8", newline="") as stream:
        reader = csv.DictReader(stream, delimiter="\t")
        header = reader.fieldnames
        if header is None or not required.issubset(header):
            raise ValueError(f"Invalid TSV schema: {path}")
        duplicates = {name for name in header if header.count(name) > 1}
        if duplicates and not (legacy and duplicates == {"entry"}):
            raise ValueError(f"Duplicate TSV fields: {path}")
        for number, row in enumerate(reader, 2):
            if None in row or any(value is None for value in row.values()):
                raise ValueError(f"Malformed TSV row: {path}:{number}")
            yield row


def _fields(path: Path) -> list[str]:
    with path.open("r", encoding="utf-8", newline="") as stream:
        return next(csv.reader(stream, delimiter="\t"), [])


def _writer(path: Path, fields: list[str]):
    stream = path.open("x", encoding="utf-8", newline="")
    writer = csv.DictWriter(
        stream, fieldnames=fields, delimiter="\t", lineterminator="\n"
    )
    writer.writeheader()
    return stream, writer


def _verify_summary(
    directory: Path,
    filename: str,
    schema: str,
    release: str,
    declared: tuple[str, ...],
) -> dict:
    data = json.loads((directory / filename).read_text(encoding="utf-8"))
    if (
        data.get("schema") != schema
        or data.get("release") != release
        or data.get("go_donor_decisions") != 0
    ):
        raise ValueError(f"Invalid Stage 2C summary: {filename}")
    hashes = data.get("output_sha256")
    if not isinstance(hashes, dict):
        raise ValueError(f"Missing checksums: {filename}")
    for name in declared:
        if hashes.get(name) != _hash(directory / name):
            raise ValueError(f"Stage 2C artifact hash mismatch: {name}")
    return data


def _check_sources(
    release: str,
    projection: Path,
    coverage: Path,
    recovered_go: Path,
    canonical_store: Path,
) -> None:
    _verify_summary(
        projection,
        PROJECTION_SUMMARY,
        "stage2c-primary-projection-v1",
        release,
        (OUTPUT_NAME, ADDITIONS_NAME, AUDIT_NAME),
    )
    cov = _verify_summary(
        coverage,
        COVERAGE_SUMMARY,
        "stage2c-sequence-coverage-v1",
        release,
        (COVERAGE_NAME, FULL_ANNEX, PENDING_NAME),
    )
    go = _verify_summary(
        recovered_go,
        GO_SUMMARY,
        "stage2c-go-pending-sequence-recovery-v1",
        release,
        (GO_ANNEX,),
    )
    if (
        cov.get("input_sha256", {}).get("projection_summary")
        != _hash(projection / PROJECTION_SUMMARY)
        or cov.get("input_sha256", {}).get("canonical_05d_store")
        != _hash(canonical_store)
        or go.get("input_sha256", {}).get("coverage_summary")
        != _hash(coverage / COVERAGE_SUMMARY)
    ):
        raise ValueError("Stage 2C chain of custody mismatch")
    pending = sum(1 for _ in _read(coverage / PENDING_NAME, {"rid"}))
    if go.get("counts", {}).get("requested_unique_rids") != pending:
        raise ValueError("GO recovery count disagrees with pending requests")


def _primary(
    db: sqlite3.Connection,
    destination: Path,
    canonical: Path,
    projections: dict[str, Path],
    releases: tuple[str, str],
) -> Counter[str]:
    db.execute(
        "CREATE TABLE primary_key (release TEXT, target TEXT, rid TEXT, "
        "PRIMARY KEY(release,target,rid))"
    )
    db.execute(
        "CREATE TABLE primary_rid (release TEXT, rid TEXT, refs INTEGER, "
        "PRIMARY KEY(release,rid))"
    )
    db.execute(
        "CREATE TABLE covered (release TEXT, target TEXT, PRIMARY KEY(release,target))"
    )
    db.execute(
        "CREATE TABLE primary_meta (release TEXT, rid TEXT, "
        "metadata TEXT, PRIMARY KEY(release,rid))"
    )
    db.execute(
        "CREATE TABLE new_refs (release TEXT, rid TEXT, refs INTEGER, "
        "PRIMARY KEY(release,rid))"
    )
    header = _fields(canonical)
    required = {"release", "target_id", "rid", "seq_exact_sequence"}
    if not required.issubset(header):
        raise ValueError("Canonical 05A2 missing required fields")
    counts: Counter[str] = Counter()
    output, writer = _writer(destination, header)
    try:
        for path, expected_release in (
            (canonical, None),
            *((projections[r] / ADDITIONS_NAME, r) for r in releases),
        ):
            if _fields(path) != header:
                raise ValueError(f"05A2 variants have incompatible headers: {path}")
            for row in _read(path, required, legacy=True):
                release = row["release"]
                rid = _positive(row["rid"], "05A2 RID")
                target = row["target_id"]
                if (
                    release not in releases
                    or not target
                    or (expected_release is not None and release != expected_release)
                ):
                    raise ValueError("05A2 variant release/target mismatch")
                db.execute(
                    "INSERT INTO primary_key VALUES (?, ?, ?)",
                    (release, target, rid),
                )
                metadata = (
                    row["candidate_primary_accession"],
                    *(row[name] for name in STORE_META[1:]),
                )
                existing = db.execute(
                    "SELECT metadata FROM primary_meta WHERE release=? AND rid=?",
                    (release, rid),
                ).fetchone()
                if existing is not None and json.loads(existing[0]) != list(metadata):
                    raise ValueError("Conflicting primary record metadata")
                if existing is None:
                    db.execute(
                        "INSERT INTO primary_meta VALUES (?, ?, ?)",
                        (release, rid, json.dumps(metadata)),
                    )
                db.execute(
                    "INSERT INTO covered VALUES (?, ?) ON CONFLICT DO NOTHING",
                    (release, target),
                )
                db.execute(
                    "INSERT INTO primary_rid VALUES (?, ?, 1) "
                    "ON CONFLICT(release,rid) DO UPDATE SET refs=refs+1",
                    (release, rid),
                )
                if expected_release is not None:
                    db.execute(
                        "INSERT INTO new_refs VALUES (?, ?, 1) "
                        "ON CONFLICT(release,rid) DO UPDATE SET refs=refs+1",
                        (release, rid),
                    )
                writer.writerow(row)
                counts["canonical" if expected_release is None else release] += 1
    finally:
        output.close()
    return counts


def _audit(
    db: sqlite3.Connection,
    canonical: Path,
    destination: Path,
    releases: tuple[str, str],
) -> Counter[str]:
    db.execute(
        "CREATE TABLE audit_rid (release TEXT, rid TEXT, refs INTEGER, "
        "PRIMARY KEY(release,rid))"
    )
    header = _fields(canonical)
    required = {
        "release",
        "target_id",
        "rid",
        "audit_alignment_allowed",
        "record_already_in_primary_union_for_target",
        "target_has_primary_candidate",
    }
    if not required.issubset(header):
        raise ValueError("Canonical 05C2 lacks required audit fields")
    seen = set()
    output, writer = _writer(destination, header)
    counts: Counter[str] = Counter()
    try:
        for row in _read(canonical, required):
            release = row["release"]
            rid = _positive(row["rid"], "05C2 RID")
            key = (release, row["target_id"], rid)
            if release not in releases or not key[1] or key in seen:
                raise ValueError("Invalid/duplicate 05C2 audit key")
            seen.add(key)
            primary = (
                db.execute(
                    "SELECT 1 FROM primary_key WHERE release=? AND target=? AND rid=?",
                    key,
                ).fetchone()
                is not None
            )
            covered = (
                db.execute(
                    "SELECT 1 FROM covered WHERE release=? AND target=?",
                    (release, key[1]),
                ).fetchone()
                is not None
            )
            row["record_already_in_primary_union_for_target"] = str(int(primary))
            row["target_has_primary_candidate"] = str(int(covered))
            allowed = row["audit_alignment_allowed"]
            if allowed not in {"0", "1"}:
                raise ValueError("Invalid 05C2 audit allowance")
            if allowed == "1":
                db.execute(
                    "INSERT INTO audit_rid VALUES (?, ?, 1) "
                    "ON CONFLICT(release,rid) DO UPDATE SET refs=refs+1",
                    (release, rid),
                )
            counts["audit_rows"] += 1
            counts["primary_overlap"] += int(primary)
            writer.writerow(row)
    finally:
        output.close()
    return counts


def _annex(
    directory: Path,
    filename: str,
    release: str,
    result: dict[str, dict[str, str]],
) -> None:
    for row in _read(directory / filename, set(ANNEX_FIELDS)):
        rid = _positive(row["rid"], "05D annex RID")
        allowed_source = (
            {"PINNED_GO_POSITIVE_01C_RECOVERED"}
            if filename == GO_ANNEX
            else {
                "CANONICAL_05D_METADATA_REUSED",
                "PINNED_FULL_V2_METADATA_RECOVERED",
            }
        )
        if (
            row["release"] != release
            or row["source_state"] not in allowed_source
            or row["frag"] != "0"
            or row["sequence_sha256"]
            != hashlib.sha256(row["sequence"].encode("ascii")).hexdigest()
            or row["sequence_length"] != str(len(row["sequence"]))
        ):
            raise ValueError("Invalid annex release or sequence integrity")
        if rid in result:
            raise ValueError("Duplicate RID across GO/FULL annexes")
        result[rid] = row


def _store(
    db: sqlite3.Connection,
    output: Path,
    canonical: Path,
    coverage: Path,
    recovered_go: Path,
    release: str,
) -> Counter[str]:
    incoming: dict[str, dict[str, str]] = {}
    _annex(coverage, FULL_ANNEX, release, incoming)
    _annex(recovered_go, GO_ANNEX, release, incoming)
    coverage_rows = {}
    for row in _read(coverage / COVERAGE_NAME, set(COVERAGE_FIELDS)):
        rid = _positive(row["rid"], "coverage RID")
        if rid in coverage_rows or row["release"] != release:
            raise ValueError("Duplicate or foreign coverage RID")
        coverage_rows[rid] = row
    new_refs = {
        rid: refs
        for rid, refs in db.execute(
            "SELECT rid, refs FROM new_refs WHERE release=?", (release,)
        )
    }
    if set(new_refs) != set(coverage_rows):
        raise ValueError("Stage 2C coverage RID universe differs from additions")
    for rid, row in coverage_rows.items():
        if row["new_primary_reference_count"] != str(new_refs[rid]):
            raise ValueError("Stage 2C coverage reference count mismatch")
    output_stream, writer = _writer(output, list(STORE_FIELDS))
    existing: set[str] = set()
    counts: Counter[str] = Counter()
    try:
        if _fields(canonical) != list(STORE_FIELDS):
            raise ValueError("Canonical 05D schema mismatch")
        for row in _read(canonical, set(STORE_FIELDS)):
            rid = _positive(row["rid"], "canonical store RID")
            if rid in existing or row["release"] != release:
                raise ValueError("Duplicate or foreign canonical 05D RID")
            existing.add(rid)
            if row["full_v2_record_number"] != rid:
                raise ValueError("Canonical 05D full record ordinal mismatch")
            if len(row["sequence"]) != int(row["sequence_length"]):
                raise ValueError("Canonical 05D sequence length mismatch")
            cov = coverage_rows.get(rid)
            if cov is not None and (
                cov["source_state"]
                not in {
                    "CANONICAL_05D",
                    "FULL_RECONCILED_NEW_RID",
                }
                or cov["sequence_sha256"]
                != hashlib.sha256(row["sequence"].encode("ascii")).hexdigest()
                or cov["candidate_accession"] != row["primary_accession"]
            ):
                raise ValueError("Coverage contradicts canonical 05D")
            if rid in incoming:
                duplicate = incoming.pop(rid)
                if any(row[key] != duplicate[key] for key in STORE_META):
                    raise ValueError("Existing RID annex metadata conflict")
                if row["sequence"] != duplicate["sequence"]:
                    raise ValueError("Existing RID annex sequence conflict")
            _write_store_row(db, writer, release, rid, row)
            counts["canonical_rids"] += 1
        for rid in sorted(incoming, key=int):
            row = incoming[rid]
            cov = coverage_rows.get(rid)
            if cov is None or cov["candidate_accession"] != row["primary_accession"]:
                raise ValueError("Annex RID lacks matching coverage")
            if (
                cov["sequence_sha256"]
                and cov["sequence_sha256"] != row["sequence_sha256"]
            ):
                raise ValueError("Annex/coverage sequence conflict")
            if (
                not cov["sequence_sha256"]
                and cov["source_state"] != "PENDING_GO_SEQUENCE"
            ):
                raise ValueError("Missing coverage sequence SHA256")
            record = {name: row[name] for name in STORE_META}
            record.update(
                {
                    "release": release,
                    "rid": rid,
                    "full_v2_record_number": rid,
                    "sequence": row["sequence"],
                }
            )
            _write_store_row(db, writer, release, rid, record)
            counts["annex_rids"] += 1
        expected = {
            rid
            for (rid,) in db.execute(
                "SELECT rid FROM primary_rid WHERE release=?", (release,)
            )
        } | {
            rid
            for (rid,) in db.execute(
                "SELECT rid FROM audit_rid WHERE release=?", (release,)
            )
        }
        if expected != existing | set(incoming):
            raise ValueError("05D variant RID set differs from requests")
        if not set(coverage_rows).issubset(expected):
            raise ValueError("Stage 2C coverage has unexpected RID")
        for rid, row in coverage_rows.items():
            if (
                row["all_sequence_flags_verified"] not in {"0", "1"}
                or not row["candidate_accession"]
                or row["all_sequence_flags_verified"] == "1"
                and rid not in expected
            ):
                raise ValueError("Invalid Stage 2C sequence coverage status")
    finally:
        output_stream.close()
    return counts


def _write_store_row(
    db: sqlite3.Connection,
    writer,
    release: str,
    rid: str,
    row: dict[str, str],
) -> None:
    primary = db.execute(
        "SELECT refs FROM primary_rid WHERE release=? AND rid=?",
        (release, rid),
    ).fetchone()
    audit = db.execute(
        "SELECT refs FROM audit_rid WHERE release=? AND rid=?",
        (release, rid),
    ).fetchone()
    count_primary = primary[0] if primary else 0
    count_audit = audit[0] if audit else 0
    expected = db.execute(
        "SELECT metadata FROM primary_meta WHERE release=? AND rid=?",
        (release, rid),
    ).fetchone()
    if expected is not None:
        observed = tuple(row[name] for name in STORE_META)
        if list(observed) != json.loads(expected[0]):
            raise ValueError("05D metadata contradicts 05A2 candidate identity")
    writer.writerow(
        {
            **row,
            "needed_by_primary": str(int(count_primary > 0)),
            "needed_by_taxonomy_audit": str(int(count_audit > 0)),
            "primary_reference_count": str(count_primary),
            "audit_reference_count": str(count_audit),
        }
    )


def build_alignment_preview(
    *,
    start_release: str,
    end_release: str,
    canonical_05a2: Path,
    canonical_05c2: Path,
    canonical_start_05d: Path,
    canonical_end_05d: Path,
    projection_start: Path,
    projection_end: Path,
    coverage_start: Path,
    coverage_end: Path,
    recovered_go_start: Path,
    recovered_go_end: Path,
    targets: Path,
    stage06a_script: Path,
    output_dir: Path,
) -> dict:
    """Prepare a noncanonical 05A2/05C2/05D/06A evidence preview."""
    releases = (start_release, end_release)
    if not all(releases) or start_release == end_release:
        raise ValueError("START and END release IDs must be distinct")
    if output_dir.exists() or output_dir.is_symlink():
        raise FileExistsError("Alignment preview destination already exists")
    inputs = [
        canonical_05a2,
        canonical_05c2,
        canonical_start_05d,
        canonical_end_05d,
        targets,
        stage06a_script,
    ]
    folders = [
        projection_start,
        projection_end,
        coverage_start,
        coverage_end,
        recovered_go_start,
        recovered_go_end,
    ]
    if any(not path.is_file() for path in inputs):
        raise FileNotFoundError("Missing alignment preview input file")
    if any(not path.is_dir() or path.is_symlink() for path in folders):
        raise ValueError("Stage 2C publication folder missing or symlinked")
    if len({path.resolve() for path in inputs}) != len(inputs):
        raise ValueError("Input files cannot alias each other")
    if not output_dir.parent.is_dir():
        raise FileNotFoundError("Alignment preview parent missing")
    if any(
        output_dir.resolve().is_relative_to(path.resolve())
        for path in [*folders, *(item.parent for item in inputs)]
    ):
        raise ValueError("Output cannot be nested under an input directory")
    sources = {
        start_release: (projection_start, coverage_start, recovered_go_start),
        end_release: (projection_end, coverage_end, recovered_go_end),
    }
    stores = {
        start_release: canonical_start_05d,
        end_release: canonical_end_05d,
    }
    for release in releases:
        projection, coverage, recovered_go = sources[release]
        _check_sources(release, projection, coverage, recovered_go, stores[release])
    lock = output_dir.with_name(f".{output_dir.name}.publish-lock")
    lock.mkdir()
    try:
        with tempfile.TemporaryDirectory(
            prefix=".probe-2c-alignment-", dir=output_dir.parent
        ) as tmp:
            root = Path(tmp)
            db = sqlite3.connect(root / "lookups.sqlite3")
            try:
                product = root / "publication"
                product.mkdir()
                primary = _primary(
                    db,
                    product / UNION_OUT,
                    canonical_05a2,
                    {r: sources[r][0] for r in releases},
                    releases,
                )
                audit = _audit(db, canonical_05c2, product / AUDIT_OUT, releases)
                start = _store(
                    db,
                    product / START_STORE,
                    canonical_start_05d,
                    coverage_start,
                    recovered_go_start,
                    start_release,
                )
                end = _store(
                    db,
                    product / END_STORE,
                    canonical_end_05d,
                    coverage_end,
                    recovered_go_end,
                    end_release,
                )
                command = [
                    sys.executable,
                    str(stage06a_script),
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
                    start_release,
                    "--end-release-id",
                    end_release,
                    "--output",
                    str(product / JOBS_OUT),
                    "--summary",
                    str(product / JOBS_SUMMARY),
                ]
                try:
                    subprocess.run(
                        command,
                        check=True,
                        capture_output=True,
                        text=True,
                    )
                except subprocess.CalledProcessError as exc:
                    detail = (exc.stdout or "") + (exc.stderr or "")
                    raise RuntimeError(
                        f"Canonical Stage 06A contract failed: {detail[-2000:]}"
                    ) from exc
                jobs = json.loads((product / JOBS_SUMMARY).read_text(encoding="utf-8"))
                if (
                    jobs.get("stage") != "06A"
                    or jobs.get("releases")
                    != {"START": start_release, "END": end_release}
                    or jobs.get("policy", {}).get("same_entity_decision_made_here")
                    is not False
                ):
                    raise ValueError("Stage 06A returned invalid policy/summary")
                summary = {
                    "schema": SCHEMA,
                    "releases": {"START": start_release, "END": end_release},
                    "canonical_rows_preserved": True,
                    "audit_provenance_preserved": True,
                    "alignment_executed": False,
                    "donor_decisions": 0,
                    "production_stages_replaced": False,
                    "counts": {
                        "primary": dict(primary),
                        "audit": dict(audit),
                        "store_start": dict(start),
                        "store_end": dict(end),
                        "stage_06a_jobs": jobs["alignment_job_count"],
                    },
                    "input_sha256": {str(item): _hash(item) for item in inputs},
                    "output_sha256": {
                        name: _hash(product / name)
                        for name in (
                            UNION_OUT,
                            AUDIT_OUT,
                            START_STORE,
                            END_STORE,
                            JOBS_OUT,
                            JOBS_SUMMARY,
                        )
                    },
                }
                (product / SUMMARY_OUT).write_text(
                    json.dumps(summary, indent=2, sort_keys=True) + "\n",
                    encoding="utf-8",
                )
                if output_dir.exists() or output_dir.is_symlink():
                    raise FileExistsError("Destination occupied before publication")
                os.replace(product, output_dir)
                return summary
            finally:
                db.close()
    finally:
        lock.rmdir()
