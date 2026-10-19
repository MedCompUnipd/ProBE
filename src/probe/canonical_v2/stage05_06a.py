"""Build Stage 05A2/05C2/05D/06A from canonical B2c facts.

All Stage 01/02/04 inputs are immutable. No GO or donor decision is made.
This module does not invoke DP, the resolver, or any Stage 08 computation.
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
from collections.abc import Iterator
from contextlib import closing
from pathlib import Path

from probe.canonical_v2.primary_projection import (
    OUTPUT_FIELDS,
    OUTPUT_NAME,
    SCHEMA,
    SUMMARY_NAME,
)
from probe.stage2c_full_metadata_recovery import _parse_header
from probe.stage2c_metadata_reuse import STORE_FIELDS
from probe.workflow.artifacts import validate_bundle

STAGE_SCHEMA = "probe-canonical-v2-stage05-06a-v1"
PRIMARY = "05A2_final_candidate_union.tsv"
AUDIT = "05C2_taxonomy_audit.tsv"
JOBS = "06A_alignment_jobs.tsv"
SUMMARY = "canonical_stage05_06a.summary.json"
METADATA = ("entry", "sequence_length", "pe", "sv", "sec", "gene")
SOURCE_METADATA = ("candidate_primary_accession", "raw_ox", "frag")
STORES = {
    "START": "05D_START_candidate_sequences.tsv",
    "END": "05D_END_candidate_sequences.tsv",
}
# Preserve UniProt ambiguity codes literally; no wildcard or EXACT approval.
_ALLOWED = frozenset(b"ACDEFGHIKLMNPQRSTVWYUOBJXZ")


def _sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(8 * 1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _rows(path: Path, fields: set[str]) -> Iterator[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as stream:
        reader = csv.DictReader(stream, delimiter="\t")
        header = reader.fieldnames
        if header is None or len(header) != len(set(header)):
            raise ValueError(f"Invalid or duplicate TSV header: {path}")
        if not fields.issubset(header):
            raise ValueError(f"Missing TSV columns: {sorted(fields - set(header))}")
        for line, row in enumerate(reader, 2):
            if None in row or any(value is None for value in row.values()):
                raise ValueError(f"Malformed TSV row {path}:{line}")
            yield row


def _positive(value: str) -> int:
    if not value or not value.isascii() or not value.isdecimal():
        raise ValueError(f"Invalid RID: {value!r}")
    parsed = int(value)
    if parsed < 1 or str(parsed) != value:
        raise ValueError(f"Noncanonical RID: {value!r}")
    return parsed


def _tsv(path: Path, fields: tuple[str, ...], rows: Iterator[dict]) -> None:
    with path.open("x", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(
            stream,
            fieldnames=fields,
            delimiter="\t",
            lineterminator="\n",
            extrasaction="raise",
        )
        writer.writeheader()
        writer.writerows(rows)


def _target_buckets(path: Path) -> dict[str, str]:
    required = {"target_id", "primary_bucket", "resolved_taxid"}
    result = {}
    for row in _rows(path, required):
        target = row["target_id"]
        if not target or target in result:
            raise ValueError("Invalid or duplicate 02B target")
        if row["resolved_taxid"]:
            _positive(row["resolved_taxid"])
        result[target] = row["primary_bucket"]
    return result


def _full_fasta(manifest_path: Path, release: str, role: str) -> tuple[Path, str]:
    if manifest_path.name != "manifest.json" or manifest_path.is_symlink():
        raise ValueError("Expected 01A_FULL manifest.json")
    manifest = validate_bundle(manifest_path.parent, deep=False)
    if (
        manifest.stage != "01A_FULL"
        or manifest.release_id != release
        or manifest.role != role
    ):
        raise ValueError("Wrong FULL FASTA manifest role or release")
    matching = [
        item
        for item in manifest.outputs
        if item.path == "01A_full_v2.fasta" and item.sha256
    ]
    if len(matching) != 1:
        raise ValueError("Missing unique pinned FULL FASTA output")
    return manifest_path.parent / matching[0].path, matching[0].sha256


def _request(
    db: sqlite3.Connection,
    role: str,
    row: dict[str, str],
    *,
    primary: bool,
) -> None:
    rid = _positive(row["rid"])
    expected = {
        "accession": row["candidate_primary_accession"],
        "ox": row["raw_ox"],
        "frag": row["frag"],
        "entry": row["entry"],
        "length": row["sequence_length"],
        "pe": row["pe"],
        "sv": row["sv"],
        "sec": row["sec"],
        "gene": row["gene"],
    }
    if not expected["accession"] or not expected["ox"]:
        raise ValueError("Missing candidate accession or OX")
    if expected["frag"] not in {"0", "1"}:
        raise ValueError("Invalid candidate fragment flag")
    found = db.execute(
        "SELECT expected, primary_refs, audit_refs FROM request WHERE role=? AND rid=?",
        (role, rid),
    ).fetchone()
    if found is None:
        db.execute(
            "INSERT INTO request VALUES (?, ?, ?, ?, ?, NULL)",
            (role, rid, json.dumps(expected), int(primary), int(not primary)),
        )
        return
    merged = json.loads(found[0])
    for name, value in expected.items():
        if value and merged[name] and value != merged[name]:
            raise ValueError(f"Conflicting FULL metadata {role}/{rid}/{name}")
        if value:
            merged[name] = value
    db.execute(
        "UPDATE request SET expected=?, primary_refs=?, audit_refs=? "
        "WHERE role=? AND rid=?",
        (
            json.dumps(merged),
            found[1] + int(primary),
            found[2] + int(not primary),
            role,
            rid,
        ),
    )


def _prepare_primary(
    db: sqlite3.Connection,
    projection: Path,
    release: str,
    role: str,
    taxonomy: Path,
    destination: Path,
) -> int:
    source = projection / OUTPUT_NAME
    summary_path = projection / SUMMARY_NAME
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    if (
        summary.get("schema") != SCHEMA
        or summary.get("release") != release
        or summary.get("ready_for_06a") is not False
        or summary.get("output_sha256", {}).get(OUTPUT_NAME) != _sha(source)
    ):
        raise ValueError("Unverified B2c candidate projection")
    buckets = _target_buckets(taxonomy)
    count = 0
    with destination.open("x", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(
            stream,
            fieldnames=OUTPUT_FIELDS,
            delimiter="\t",
            lineterminator="\n",
            extrasaction="raise",
        )
        writer.writeheader()
        last: tuple[str, int] | None = None
        for row in _rows(source, set(OUTPUT_FIELDS)):
            if row["release"] != release:
                raise ValueError("Unexpected B2c release")
            key = row["target_id"], _positive(row["rid"])
            if not key[0] or (last is not None and key <= last):
                raise ValueError("Unsorted or duplicate B2c candidate")
            last = key
            bucket = buckets.get(key[0], "")
            if not bucket:
                raise ValueError(f"Unresolved target 02B bucket: {key[0]}")
            if row["full_ordinal_state"] == "CONFLICT":
                raise ValueError(f"Unresolved FULL ordinal conflict: {key}")
            if row["full_v2_record_number"] and (
                _positive(row["full_v2_record_number"]) != key[1]
            ):
                raise ValueError(f"FULL ordinal/RID mismatch: {key}")
            row["target_primary_bucket"] = bucket
            # The validated resolver recognizes FULL-only discovery through
            # a dedicated channel; mixed evidence retains the original flags.
            if (
                row["sequence_discovered"] == "1"
                and row["go_positive_sequence_discovered"] == "0"
                and row["full_v2_rescue_sequence_discovered"] == "0"
            ):
                if row["full_exact_species_wide"] != "1":
                    raise ValueError("Unexplained sequence-only candidate")
                row["discovery_channel"] = "STAGE2C_FULL_EXACT"
            _request(db, role, row, primary=True)
            writer.writerow(row)
            count += 1
    if count != summary.get("counts", {}).get("unique_pairs"):
        raise ValueError("B2c source count mismatch")
    return count


def _collect_audit(db: sqlite3.Connection, path: Path, releases: dict[str, str]) -> int:
    required = (
        set(SOURCE_METADATA)
        | set(METADATA)
        | {
            "release",
            "rid",
            "audit_alignment_allowed",
        }
    )
    count = 0
    for row in _rows(path, required):
        role = next(
            (r for r, value in releases.items() if value == row["release"]), None
        )
        if role is None:
            raise ValueError("Unexpected 05C2 release")
        if row["audit_alignment_allowed"] not in {"0", "1"}:
            raise ValueError("Invalid audit permission")
        if row["audit_alignment_allowed"] == "1":
            _request(db, role, row, primary=False)
            count += 1
    return count


def _scan_full(
    db: sqlite3.Connection,
    role: str,
    fasta: Path,
    expected_sha: str,
) -> tuple[int, int]:
    requests = db.execute(
        "SELECT rid, expected FROM request WHERE role=? ORDER BY rid", (role,)
    )
    pending = requests.fetchone()
    if pending is None:
        return 0, 0
    digest = hashlib.sha256()
    scanned = 0
    matched = 0
    with fasta.open("rb", buffering=8 * 1024 * 1024) as stream:
        while header := stream.readline():
            sequence = stream.readline()
            if not sequence or not sequence.endswith(b"\n"):
                raise ValueError("Malformed or truncated FULL FASTA")
            digest.update(header)
            digest.update(sequence)
            scanned += 1
            if not header.startswith(b">"):
                raise ValueError("Invalid FULL FASTA header")
            if pending is None or scanned != pending[0]:
                continue
            values = _parse_header(header, scanned)
            data = sequence.rstrip(b"\r\n")
            try:
                residues = data.decode("ascii")
            except UnicodeDecodeError as exc:
                raise ValueError("Non-ASCII FULL sequence") from exc
            if not data or any(letter not in _ALLOWED for letter in data):
                raise ValueError("Invalid FULL amino acid alphabet")
            if int(values["LEN"]) != len(data):
                raise ValueError("FULL sequence length mismatch")
            expected = json.loads(pending[1])
            actual = {
                "accession": values["primary_accession"],
                "ox": values["OX"],
                "frag": values["FRAG"],
                "entry": values["entry"],
                "length": values["LEN"],
                "pe": values["PE"],
                "sv": values["SV"],
                "sec": values.get("SEC", ""),
                "gene": values.get("GENE", ""),
            }
            for name, observed in expected.items():
                if observed and actual[name] != observed:
                    raise ValueError(f"FULL identity disagreement: {role}/{scanned}")
            db.execute(
                "UPDATE request SET full=? WHERE role=? AND rid=?",
                (json.dumps({**actual, "sequence": residues}), role, scanned),
            )
            matched += 1
            pending = requests.fetchone()
    if digest.hexdigest() != expected_sha or pending is not None:
        raise ValueError("FULL FASTA digest or request completeness mismatch")
    return scanned, matched


def _rewrite_primary(
    db: sqlite3.Connection,
    provisional: Path,
    destination: Path,
    role: str,
) -> None:
    release_fields = set(OUTPUT_FIELDS)

    def completed() -> Iterator[dict]:
        for row in _rows(provisional, release_fields):
            rid = _positive(row["rid"])
            saved = db.execute(
                "SELECT full FROM request WHERE role=? AND rid=?", (role, rid)
            ).fetchone()
            if saved is None or saved[0] is None:
                raise ValueError("Missing primary candidate sequence")
            full = json.loads(saved[0])
            row["candidate_primary_accession"] = full["accession"]
            row["raw_ox"] = full["ox"]
            row["frag"] = full["frag"]
            for name, source in (
                ("entry", "entry"),
                ("sequence_length", "length"),
                ("pe", "pe"),
                ("sv", "sv"),
                ("sec", "sec"),
                ("gene", "gene"),
            ):
                row[name] = full[source]
            row["full_v2_record_number"] = row["rid"]
            yield row

    _tsv(destination, OUTPUT_FIELDS, completed())


def _write_store(
    db: sqlite3.Connection, role: str, destination: Path, release: str
) -> int:
    cursor = db.execute(
        "SELECT rid, full, primary_refs, audit_refs FROM request "
        "WHERE role=? ORDER BY rid",
        (role,),
    )

    def records() -> Iterator[dict]:
        for rid, raw, primary, audit in cursor:
            if raw is None:
                raise ValueError(f"Missing requested FULL RID: {role}/{rid}")
            found = json.loads(raw)
            yield {
                "release": release,
                "full_v2_record_number": str(rid),
                "rid": str(rid),
                "primary_accession": found["accession"],
                "entry": found["entry"],
                "raw_ox": found["ox"],
                "frag": found["frag"],
                "sequence_length": found["length"],
                "pe": found["pe"],
                "sv": found["sv"],
                "sec": found["sec"],
                "gene": found["gene"],
                "needed_by_primary": str(int(primary > 0)),
                "needed_by_taxonomy_audit": str(int(audit > 0)),
                "primary_reference_count": str(primary),
                "audit_reference_count": str(audit),
                "sequence": found["sequence"],
            }

    _tsv(destination, STORE_FIELDS, records())
    return db.execute("SELECT COUNT(*) FROM request WHERE role=?", (role,)).fetchone()[
        0
    ]


def _run_cli(script: Path, arguments: list[str]) -> None:
    if not script.is_file():
        raise FileNotFoundError(script)
    subprocess.run([sys.executable, str(script), *arguments], check=True)


def build_stage05_06a(
    *,
    releases: dict[str, str],
    projections: dict[str, Path],
    taxonomies: dict[str, Path],
    anchors: dict[str, Path],
    full_manifests: dict[str, Path],
    targets: Path,
    stage05c2_cli: Path,
    stage06a_cli: Path,
    output_dir: Path,
) -> dict:
    """Build cold-start Stage 05–06A in a new immutable output directory."""
    if set(releases) != {"START", "END"} or len(set(releases.values())) != 2:
        raise ValueError("Exactly two distinct START/END releases required")
    if any(
        set(mapping) != set(releases)
        for mapping in (
            projections,
            taxonomies,
            anchors,
            full_manifests,
        )
    ):
        raise ValueError("Missing required START/END inputs")
    if not output_dir.parent.is_dir() or output_dir.exists():
        raise ValueError("Stage 05/06A output parent missing or occupied")
    if output_dir.is_symlink():
        raise ValueError("Refusing symlink output")
    sources = (
        targets,
        stage05c2_cli,
        stage06a_cli,
        *taxonomies.values(),
        *anchors.values(),
        *full_manifests.values(),
    )
    for source in sources:
        if not source.is_file() or source.is_symlink():
            raise ValueError(f"Missing or unsafe canonical input: {source}")
    for directory in projections.values():
        if not directory.is_dir() or directory.is_symlink():
            raise ValueError("Missing or unsafe B2c directory")
        if directory == output_dir or output_dir in directory.parents:
            raise ValueError("Refusing an output ancestor of a B2c source")
        if directory in output_dir.parents:
            raise ValueError("Refusing an output below a B2c source")
        for name in (OUTPUT_NAME, SUMMARY_NAME):
            if (directory / name).is_symlink():
                raise ValueError("Refusing symlink B2c input")
    expected_fastas = {
        role: _full_fasta(full_manifests[role], release, role)
        for role, release in releases.items()
    }
    counts: Counter[str] = Counter()
    with tempfile.TemporaryDirectory(
        dir=output_dir.parent, prefix="probe-canonical-v2-b3-"
    ) as temporary:
        staged = Path(temporary)
        with closing(sqlite3.connect(staged / "requests.sqlite3")) as db:
            db.execute(
                "CREATE TABLE request (role TEXT, rid INTEGER, expected TEXT, "
                "primary_refs INTEGER, audit_refs INTEGER, full TEXT, "
                "PRIMARY KEY(role,rid))"
            )
            for role, release in releases.items():
                counts[f"{role}_primary"] = _prepare_primary(
                    db,
                    projections[role],
                    release,
                    role,
                    taxonomies[role],
                    staged / f"{role}.provisional.tsv",
                )
            primary = staged / PRIMARY
            with primary.open("x", encoding="utf-8", newline="") as output:
                for index, role in enumerate(("START", "END")):
                    with (staged / f"{role}.provisional.tsv").open(
                        encoding="utf-8"
                    ) as source:
                        for lineno, line in enumerate(source):
                            if index > 0 and lineno == 0:
                                continue
                            output.write(line)
            _run_cli(
                stage05c2_cli,
                [
                    "--candidate-union",
                    str(primary),
                    "--start-anchors",
                    str(anchors["START"]),
                    "--end-anchors",
                    str(anchors["END"]),
                    "--start-release-id",
                    releases["START"],
                    "--end-release-id",
                    releases["END"],
                    "--output",
                    str(staged / AUDIT),
                    "--summary",
                    str(staged / "05C2.summary.json"),
                ],
            )
            counts["audit_alignment_references"] = _collect_audit(
                db, staged / AUDIT, releases
            )
            for role in ("START", "END"):
                fasta, digest = expected_fastas[role]
                scanned, matched = _scan_full(db, role, fasta, digest)
                counts[f"{role}_full_scanned"] = scanned
                counts[f"{role}_full_matched"] = matched
                _rewrite_primary(
                    db,
                    staged / f"{role}.provisional.tsv",
                    staged / f"{role}.completed.tsv",
                    role,
                )
                counts[f"{role}_unique_rids"] = _write_store(
                    db, role, staged / STORES[role], releases[role]
                )
            # Both releases must be emitted in deterministic START/END order.
            primary.unlink()
            with primary.open("x", encoding="utf-8", newline="") as output:
                for index, role in enumerate(("START", "END")):
                    with (staged / f"{role}.completed.tsv").open(
                        encoding="utf-8"
                    ) as source:
                        for lineno, line in enumerate(source):
                            if index > 0 and lineno == 0:
                                continue
                            output.write(line)
            _run_cli(
                stage06a_cli,
                [
                    "--targets",
                    str(targets),
                    "--candidate-union",
                    str(primary),
                    "--taxonomy-audit",
                    str(staged / AUDIT),
                    "--start-sequence-store",
                    str(staged / STORES["START"]),
                    "--end-sequence-store",
                    str(staged / STORES["END"]),
                    "--start-release-id",
                    releases["START"],
                    "--end-release-id",
                    releases["END"],
                    "--output",
                    str(staged / JOBS),
                    "--summary",
                    str(staged / "06A.summary.json"),
                ],
            )
            for role in ("START", "END"):
                (staged / f"{role}.provisional.tsv").unlink()
                (staged / f"{role}.completed.tsv").unlink()
            # The indexing database is build-only, not a scientific artifact.
        (staged / "requests.sqlite3").unlink()
        outputs = (
            PRIMARY,
            AUDIT,
            JOBS,
            *STORES.values(),
            "05C2.summary.json",
            "06A.summary.json",
        )
        summary = {
            "schema": STAGE_SCHEMA,
            "mode": "cold_start",
            "go_donor_decisions": 0,
            "dp_executions": 0,
            "historical_05_07_inputs": 0,
            "releases": releases,
            "counts": dict(sorted(counts.items())),
            "inputs_sha256": {
                **{
                    f"{role}_b2c": _sha(projections[role] / SUMMARY_NAME)
                    for role in ("START", "END")
                },
                **{f"{role}_02b": _sha(taxonomies[role]) for role in ("START", "END")},
                **{f"{role}_04b": _sha(anchors[role]) for role in ("START", "END")},
                **{
                    f"{role}_01a_manifest": _sha(full_manifests[role])
                    for role in ("START", "END")
                },
                "01_targets": _sha(targets),
                "05c2_cli": _sha(stage05c2_cli),
                "06a_cli": _sha(stage06a_cli),
            },
            "full_fasta_sha256_verified": {
                role: expected_fastas[role][1]
                for role in ("START", "END")
                if counts[f"{role}_full_matched"] > 0
            },
            "outputs_sha256": {name: _sha(staged / name) for name in outputs},
        }
        (staged / SUMMARY).write_text(
            json.dumps(summary, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        if output_dir.exists() or output_dir.is_symlink():
            raise FileExistsError("Concurrent Stage 05/06A publication")
        os.rename(staged, output_dir)
    return summary
