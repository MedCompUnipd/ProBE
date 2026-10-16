"""Audit Stage 2C additions against materialized 05D/FULL sequences.

This creates an immutable sequence-readiness inventory and a FULL-derived
annex. It does not publish a production 05D store, perform alignments,
or make donor decisions. GO-only records absent from both sources remain
explicit pending requests.
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

from probe.stage2c_metadata_reconciliation import (
    METADATA_OUTPUT,
    SEQUENCES_OUTPUT,
    UNIFIED_SEQUENCE_FIELDS,
)
from probe.stage2c_metadata_reconciliation import (
    SUMMARY_OUTPUT as FULL_SUMMARY,
)
from probe.stage2c_metadata_reuse import REUSED_FIELDS, STORE_FIELDS
from probe.stage2c_primary_projection import (
    ADDITIONS_NAME,
    AUDIT_NAME,
    OUTPUT_NAME,
    PROVENANCE_FIELDS,
    SUMMARY_NAME,
)

SCHEMA = "stage2c-sequence-coverage-v1"
COVERAGE_NAME = "2C_05D_sequence_coverage.tsv"
ANNEX_NAME = "2C_05D_new_sequence_annex.tsv"
PENDING_NAME = "2C_05D_pending_go_sequences.tsv"
SUMMARY_NAME_OUT = "2C_05D_sequence_coverage.summary.json"
COVERAGE_FIELDS = (
    "release",
    "rid",
    "candidate_accession",
    "source_state",
    "new_primary_reference_count",
    "go_positive_reference_count",
    "full_exact_reference_count",
    "sequence_sha256",
    "sequence_length",
    "all_sequence_flags_verified",
)
ANNEX_FIELDS = (
    "release",
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
    "sequence",
    "sequence_sha256",
    "source_state",
)
PENDING_FIELDS = (
    "release",
    "rid",
    "candidate_accession",
    "raw_ox",
    "go_positive_record_number",
    "new_primary_reference_count",
    "reason",
)


def _sha(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            h.update(chunk)
    return h.hexdigest()


def _rid(text: str, label: str) -> int:
    if not text or not text.isascii() or not text.isdecimal():
        raise ValueError(f"Invalid {label}: {text!r}")
    value = int(text)
    if value < 1 or str(value) != text:
        raise ValueError(f"Noncanonical {label}: {text!r}")
    return value


def _rows(path: Path, required: set[str], *, legacy: bool = False):
    with path.open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle, delimiter="\t")
        fields = reader.fieldnames
        if fields is None or not required.issubset(fields):
            raise ValueError(f"Missing TSV columns: {path}")
        repeated = {key for key in fields if fields.count(key) > 1}
        if repeated and not (legacy and repeated == {"entry"}):
            raise ValueError(f"Duplicated TSV header: {path}")
        for line, row in enumerate(reader, 2):
            if None in row or any(value is None for value in row.values()):
                raise ValueError(f"Malformed TSV row {path}:{line}")
            yield row


def _verified_summary(
    directory: Path,
    name: str,
    schema: str,
    release: str,
    filenames: tuple[str, ...],
) -> dict:
    summary = json.loads((directory / name).read_text(encoding="utf-8"))
    if (
        summary.get("schema") != schema
        or summary.get("release") != release
        or summary.get("go_donor_decisions") != 0
    ):
        raise ValueError("Invalid provenance summary")
    hashes = summary.get("output_sha256", {})
    for filename in filenames:
        if hashes.get(filename) != _sha(directory / filename):
            raise ValueError(f"Input checksum mismatch: {filename}")
    return summary


def _metadata_key(row: dict) -> tuple[str, ...]:
    return tuple(
        row[name]
        for name in (
            "candidate_primary_accession",
            "entry",
            "raw_ox",
            "frag",
            "sequence_length",
            "pe",
            "sv",
            "sec",
            "gene",
        )
    )


def _record_key(row: dict) -> tuple[str, ...]:
    return tuple(
        row[name]
        for name in (
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
    )


def _full_key(row: dict) -> tuple[str, ...]:
    return tuple(
        row[name]
        for name in (
            "candidate_accession",
            "entry",
            "candidate_raw_ox",
            "frag",
            "sequence_length",
            "pe",
            "sv",
            "sec",
            "gene",
        )
    )


def _add_rid_lookup_index(db: sqlite3.Connection) -> None:
    """Index the RID-only lookups after insertion, without altering pair keys."""
    db.execute("CREATE INDEX stage2c_a_rid_idx ON a(rid)")


def _additions(db: sqlite3.Connection, directory: Path, release: str) -> None:
    db.execute(
        "CREATE TABLE a (target TEXT, rid INTEGER, payload TEXT NOT NULL, "
        "PRIMARY KEY(target,rid))"
    )
    db.execute(
        "CREATE TABLE unique_rid (rid INTEGER PRIMARY KEY, "
        "metadata TEXT NOT NULL, refs INTEGER NOT NULL, "
        "go_refs INTEGER NOT NULL, full_refs INTEGER NOT NULL, "
        "go_ordinal TEXT NOT NULL)"
    )
    required = {
        "release",
        "target_id",
        "rid",
        "candidate_primary_accession",
        "raw_ox",
        "entry",
        "frag",
        "sequence_length",
        "pe",
        "sv",
        "sec",
        "gene",
        "go_positive_sequence_discovered",
        "full_v2_record_number",
        "sequence_search_record_number",
        "seq_exact_sequence",
        "sequence_discovered",
    }
    for row in _rows(directory / ADDITIONS_NAME, required, legacy=True):
        if row["release"] != release or not row["target_id"]:
            raise ValueError("Addition release/target mismatch")
        rid = _rid(row["rid"], "addition RID")
        if row["frag"] != "0" or row["sequence_discovered"] != "1":
            raise ValueError("Stage 2C new candidate is not a full sequence")
        go = row["go_positive_sequence_discovered"]
        if go not in {"0", "1"} or row["seq_exact_sequence"] not in {"0", "1"}:
            raise ValueError("Invalid GO or sequence-identity flag")
        full = row["full_v2_record_number"] != ""
        if full and row["full_v2_record_number"] != row["rid"]:
            raise ValueError("FULL record number must equal RID")
        if not full and not int(go):
            raise ValueError("Untraceable Stage 2C candidate")
        if bool(row["sequence_search_record_number"]) != bool(int(go)):
            raise ValueError("GO record-number provenance mismatch")
        if go == "1":
            _rid(row["sequence_search_record_number"], "GO record number")
        for name in ("raw_ox", "sequence_length", "pe", "sv"):
            _rid(row[name], name)
        if not row["entry"] or not row["candidate_primary_accession"]:
            raise ValueError("Missing candidate identity")
        db.execute(
            "INSERT INTO a VALUES (?, ?, ?)",
            (row["target_id"], rid, json.dumps(row, sort_keys=True)),
        )
        existing = db.execute(
            "SELECT metadata, go_ordinal FROM unique_rid WHERE rid=?", (rid,)
        ).fetchone()
        if existing is None:
            db.execute(
                "INSERT INTO unique_rid VALUES (?, ?, 1, ?, ?, ?)",
                (
                    rid,
                    json.dumps(_metadata_key(row)),
                    int(go),
                    int(full),
                    row["sequence_search_record_number"] if int(go) else "",
                ),
            )
        else:
            if json.loads(existing[0]) != list(_metadata_key(row)):
                raise ValueError(f"Conflicting Stage 2C RID metadata: {rid}")
            ordinal = row["sequence_search_record_number"] if int(go) else ""
            if existing[1] and ordinal and existing[1] != ordinal:
                raise ValueError(f"Conflicting GO record ordinal for RID {rid}")
            db.execute(
                "UPDATE unique_rid SET refs=refs+1, go_refs=go_refs+?, "
                "full_refs=full_refs+?, "
                "go_ordinal=CASE WHEN go_ordinal='' THEN ? ELSE go_ordinal END "
                "WHERE rid=?",
                (int(go), int(full), ordinal, rid),
            )
    seen = set()
    for audit in _rows(directory / AUDIT_NAME, set(PROVENANCE_FIELDS)):
        if audit["release"] != release:
            raise ValueError("Projection audit release mismatch")
        key = (audit["target_id"], _rid(audit["rid"], "audit RID"))
        if key in seen:
            raise ValueError("Duplicate projection audit key")
        seen.add(key)
        stored = db.execute(
            "SELECT payload FROM a WHERE target=? AND rid=?", key
        ).fetchone()
        if stored is None:
            raise ValueError("Unknown projection audit key")
        row = json.loads(stored[0])
        if (
            audit["go_positive_species_wide"] != row["go_positive_sequence_discovered"]
            or audit["full_exact_species_wide"]
            != str(int(bool(row["full_v2_record_number"])))
            or audit["full_sequence_verified"]
            != str(int(bool(row["full_v2_record_number"])))
        ):
            raise ValueError("Projection audit provenance mismatch")
    count = db.execute("SELECT COUNT(*) FROM a").fetchone()[0]
    if len(seen) != count:
        raise ValueError("Projection audit does not cover all additions")
    matched = 0
    for row in _rows(
        directory / OUTPUT_NAME,
        {"release", "target_id", "rid"},
        legacy=True,
    ):
        if row["release"] != release:
            continue
        match = db.execute(
            "SELECT payload FROM a WHERE target=? AND rid=?",
            (row["target_id"], _rid(row["rid"], "preview RID")),
        ).fetchone()
        if match is not None:
            if row != json.loads(match[0]):
                raise ValueError("05A2 preview differs from new-candidate table")
            matched += 1
    if matched != count:
        raise ValueError("Preview does not contain every new candidate")


def _targets(path: Path, needed: set[str]) -> dict[str, str]:
    targets = {}
    for row in _rows(path, {"target_id", "normalized_sequence", "sequence_length"}):
        target = row["target_id"]
        if target not in needed:
            continue
        if target in targets:
            raise ValueError("Duplicate prepared target")
        sequence = row["normalized_sequence"]
        if len(sequence) != _rid(row["sequence_length"], "target length"):
            raise ValueError("Prepared target sequence length mismatch")
        targets[target] = sequence
    if set(targets) != needed:
        raise ValueError("Missing prepared target sequence")
    return targets


def _read_canonical_store(
    db: sqlite3.Connection,
    path: Path,
    release: str,
) -> None:
    db.execute("CREATE TABLE known (rid INTEGER PRIMARY KEY, payload TEXT)")
    db.execute("CREATE TABLE seen_05d (rid INTEGER PRIMARY KEY)")
    for row in _rows(path, set(STORE_FIELDS)):
        rid = _rid(row["rid"], "canonical 05D RID")
        if row["release"] != release or row["full_v2_record_number"] != str(rid):
            raise ValueError("Canonical 05D RID/release mismatch")
        sequence = row["sequence"]
        if len(sequence) != _rid(row["sequence_length"], "05D sequence length"):
            raise ValueError("Invalid 05D physical sequence length")
        try:
            db.execute("INSERT INTO seen_05d VALUES (?)", (rid,))
        except sqlite3.IntegrityError as exc:
            raise ValueError("Duplicate canonical 05D RID") from exc
        if db.execute("SELECT 1 FROM unique_rid WHERE rid=?", (rid,)).fetchone():
            db.execute(
                "INSERT INTO known VALUES (?, ?)",
                (rid, json.dumps(row, sort_keys=True)),
            )


def _read_full(db: sqlite3.Connection, directory: Path, release: str) -> None:
    db.execute("CREATE TABLE full (rid INTEGER PRIMARY KEY, payload TEXT)")
    for row in _rows(directory / METADATA_OUTPUT, set(REUSED_FIELDS)):
        rid = _rid(row["rid"], "FULL metadata RID")
        needed = db.execute(
            "SELECT full_refs FROM unique_rid WHERE rid=?", (rid,)
        ).fetchone()
        if needed is None or needed[0] == 0:
            raise ValueError("Unexpected FULL RID absent from projection")
        if (
            row["release"] != release
            or row["source_full_v2_record_number"] != str(rid)
            or row["frag"] != "0"
            or row["taxonomy_state"] != "REQUIRES_02B_REVALIDATION"
            or row["source_state"]
            not in {
                "CANONICAL_05D_METADATA_REUSED",
                "PINNED_FULL_V2_METADATA_RECOVERED",
            }
        ):
            raise ValueError("Invalid FULL metadata provenance")
        existing = db.execute(
            "SELECT metadata FROM unique_rid WHERE rid=?", (rid,)
        ).fetchone()
        if existing is None or _full_key(row) != tuple(json.loads(existing[0])):
            raise ValueError("FULL metadata disagrees with 05A2 projection")
        try:
            db.execute("INSERT INTO full VALUES (?, ?)", (rid, json.dumps(row)))
        except sqlite3.IntegrityError as exc:
            raise ValueError("Duplicate FULL metadata RID") from exc
    db.execute("CREATE TABLE physical (rid INTEGER PRIMARY KEY, payload TEXT)")
    for row in _rows(directory / SEQUENCES_OUTPUT, set(UNIFIED_SEQUENCE_FIELDS)):
        rid = _rid(row["rid"], "FULL sequence RID")
        match = db.execute("SELECT payload FROM full WHERE rid=?", (rid,)).fetchone()
        if match is None:
            raise ValueError("FULL sequence RID without metadata")
        meta = json.loads(match[0])
        sequence = row["sequence"]
        if (
            row["release"] != release
            or row["full_v2_record_number"] != str(rid)
            or row["candidate_accession"] != meta["candidate_accession"]
            or row["source_state"] != meta["source_state"]
            or len(sequence) != _rid(meta["sequence_length"], "FULL length")
            or _sha_sequence(sequence) != meta["sequence_sha256"]
            or _sha_sequence(sequence) != row["sequence_sha256"]
        ):
            raise ValueError("FULL sequence/provenance disagreement")
        try:
            db.execute("INSERT INTO physical VALUES (?, ?)", (rid, sequence))
        except sqlite3.IntegrityError as exc:
            raise ValueError("Duplicate FULL sequence RID") from exc
    if (
        db.execute("SELECT COUNT(*) FROM full").fetchone()[0]
        != db.execute("SELECT COUNT(*) FROM physical").fetchone()[0]
    ):
        raise ValueError("FULL metadata / sequence coverage mismatch")
    missing = db.execute(
        "SELECT COUNT(*) FROM unique_rid WHERE full_refs>0 "
        "AND rid NOT IN (SELECT rid FROM full)"
    ).fetchone()[0]
    if missing:
        raise ValueError("FULL record missing from reconciled sequence source")


def _sha_sequence(sequence: str) -> str:
    return hashlib.sha256(sequence.encode("ascii")).hexdigest()


def _writer(path: Path, fields: tuple[str, ...]):
    handle = path.open("x", encoding="utf-8", newline="")
    writer = csv.DictWriter(
        handle, fieldnames=fields, delimiter="\t", lineterminator="\n"
    )
    writer.writeheader()
    return handle, writer


def build_sequence_coverage(
    *,
    release: str,
    projection_dir: Path,
    store_05d: Path,
    prepared_targets: Path,
    output_dir: Path,
    full_metadata_dir: Path | None = None,
) -> dict:
    """Publish readiness evidence, never replace Stage 05D or Stage 06A."""
    if not release or not output_dir.parent.is_dir():
        raise ValueError("Invalid release or output parent")
    if output_dir.exists() or output_dir.is_symlink():
        raise FileExistsError("Sequence coverage output is occupied")
    if projection_dir.is_symlink() or not projection_dir.is_dir():
        raise ValueError("Missing projection bundle directory")
    if full_metadata_dir is not None and (
        full_metadata_dir.is_symlink() or not full_metadata_dir.is_dir()
    ):
        raise ValueError("Invalid FULL metadata directory")
    inputs = [store_05d, prepared_targets]
    if any(not path.is_file() for path in inputs):
        raise FileNotFoundError("Missing sequence coverage input")
    if len({path.resolve() for path in inputs}) != len(inputs):
        raise ValueError("Overlapping sequence coverage inputs")
    destinations = [projection_dir, store_05d.parent]
    if full_metadata_dir is not None:
        destinations.append(full_metadata_dir)
    if any(
        output_dir.resolve().is_relative_to(folder.resolve()) for folder in destinations
    ):
        raise ValueError("Output cannot be inside an input publication")
    projection = _verified_summary(
        projection_dir,
        SUMMARY_NAME,
        "stage2c-primary-projection-v1",
        release,
        (OUTPUT_NAME, ADDITIONS_NAME, AUDIT_NAME),
    )
    if (
        projection.get("production_05a2_replacement") is not False
        or projection.get("stage_05d_and_06a_ready") is not False
    ):
        raise ValueError("Unexpected primary projection readiness")
    if full_metadata_dir is not None:
        full_summary = _verified_summary(
            full_metadata_dir,
            FULL_SUMMARY,
            "stage2c-full-metadata-reconciliation-v1",
            release,
            (METADATA_OUTPUT, SEQUENCES_OUTPUT),
        )
        if (
            full_summary.get("taxonomy_verified") is not False
            or full_summary.get("complete_stage05a2_replacement") is not False
        ):
            raise ValueError("Unexpected FULL metadata policy")
    lock = output_dir.with_name(f".{output_dir.name}.publish-lock")
    lock.mkdir()
    try:
        with tempfile.TemporaryDirectory(
            prefix=".probe-2c-05d-coverage-", dir=output_dir.parent
        ) as temporary:
            root = Path(temporary)
            db = sqlite3.connect(root / "lookup.sqlite3")
            try:
                _additions(db, projection_dir, release)
                _add_rid_lookup_index(db)
                _read_canonical_store(db, store_05d, release)
                required = {
                    row[0] for row in db.execute("SELECT DISTINCT target FROM a")
                }
                targets = _targets(prepared_targets, required)
                if full_metadata_dir is not None:
                    _read_full(db, full_metadata_dir, release)
                else:
                    count = db.execute(
                        "SELECT COUNT(*) FROM unique_rid WHERE full_refs>0"
                    ).fetchone()[0]
                    if count:
                        raise ValueError("Missing FULL reconciled sequence source")
                    db.execute(
                        "CREATE TABLE full (rid INTEGER PRIMARY KEY, payload TEXT)"
                    )
                    db.execute(
                        "CREATE TABLE physical (rid INTEGER PRIMARY KEY, payload TEXT)"
                    )
                bundle = root / "bundle"
                bundle.mkdir()
                coverage_h, coverage_w = _writer(
                    bundle / COVERAGE_NAME, COVERAGE_FIELDS
                )
                annex_h, annex_w = _writer(bundle / ANNEX_NAME, ANNEX_FIELDS)
                pending_h, pending_w = _writer(bundle / PENDING_NAME, PENDING_FIELDS)
                counts: Counter[str] = Counter()
                try:
                    for rid, metadata, refs, go_refs, full_refs, ordinal in db.execute(
                        "SELECT rid, metadata, refs, go_refs, full_refs, "
                        "go_ordinal FROM unique_rid ORDER BY rid"
                    ):
                        candidate = list(json.loads(metadata))
                        canon = db.execute(
                            "SELECT payload FROM known WHERE rid=?", (rid,)
                        ).fetchone()
                        full = db.execute(
                            "SELECT payload FROM full WHERE rid=?", (rid,)
                        ).fetchone()
                        source = "PENDING_GO_SEQUENCE"
                        record = None
                        if canon is not None:
                            source = "CANONICAL_05D"
                            record = json.loads(canon[0])
                            if _record_key(record) != tuple(candidate):
                                raise ValueError(
                                    "Canonical 05D vs 05A2 metadata conflict"
                                )
                        if full is not None:
                            meta = json.loads(full[0])
                            sequence = db.execute(
                                "SELECT payload FROM physical WHERE rid=?", (rid,)
                            ).fetchone()[0]
                            if canon is not None and record["sequence"] != sequence:
                                raise ValueError(
                                    "Canonical 05D vs FULL sequence conflict"
                                )
                            else:
                                source = "FULL_RECONCILED_NEW_RID"
                                record = {
                                    "release": release,
                                    "rid": str(rid),
                                    "primary_accession": (meta["candidate_accession"]),
                                    "entry": meta["entry"],
                                    "raw_ox": meta["candidate_raw_ox"],
                                    "frag": meta["frag"],
                                    "sequence_length": (meta["sequence_length"]),
                                    "pe": meta["pe"],
                                    "sv": meta["sv"],
                                    "sec": meta["sec"],
                                    "gene": meta["gene"],
                                    "sequence": sequence,
                                }
                                annex_w.writerow(
                                    {
                                        **record,
                                        "sequence_sha256": _sha_sequence(sequence),
                                        "source_state": meta["source_state"],
                                    }
                                )
                        if record is None:
                            pending_w.writerow(
                                {
                                    "release": release,
                                    "rid": str(rid),
                                    "candidate_accession": candidate[0],
                                    "raw_ox": candidate[2],
                                    "go_positive_record_number": ordinal,
                                    "new_primary_reference_count": refs,
                                    "reason": "GO_POSITIVE_SEQUENCE_NOT_MATERIALIZED",
                                }
                            )
                            counts["pending_unique_rids"] += 1
                        else:
                            for target, payload in db.execute(
                                "SELECT target, payload FROM a WHERE rid=?", (rid,)
                            ):
                                row = json.loads(payload)
                                observed = str(
                                    int(targets[target] == record["sequence"])
                                )
                                if row["seq_exact_sequence"] != observed:
                                    raise ValueError(
                                        "Sequence-search exact flag differs from "
                                        "literal sequence bytes"
                                    )
                                counts["verified_target_rid_pairs"] += 1
                            counts["verified_unique_rids"] += 1
                        counts[f"source_{source}"] += 1
                        coverage_w.writerow(
                            {
                                "release": release,
                                "rid": str(rid),
                                "candidate_accession": candidate[0],
                                "source_state": source,
                                "new_primary_reference_count": refs,
                                "go_positive_reference_count": go_refs,
                                "full_exact_reference_count": full_refs,
                                "sequence_sha256": (
                                    _sha_sequence(record["sequence"]) if record else ""
                                ),
                                "sequence_length": candidate[4],
                                "all_sequence_flags_verified": str(
                                    int(record is not None)
                                ),
                            }
                        )
                        counts["new_primary_reference_rows"] += refs
                        counts["new_unique_rids"] += 1
                finally:
                    coverage_h.close()
                    annex_h.close()
                    pending_h.close()
                summary = {
                    "schema": SCHEMA,
                    "release": release,
                    "counts": dict(sorted(counts.items())),
                    "production_05d_replacement": False,
                    "production_06a_ready": False,
                    "taxonomy_audit_05c2_pending": True,
                    "go_donor_decisions": 0,
                    "input_sha256": {
                        "canonical_05d_store": _sha(store_05d),
                        "prepared_targets": _sha(prepared_targets),
                        "projection_summary": _sha(projection_dir / SUMMARY_NAME),
                        **(
                            {"full_summary": _sha(full_metadata_dir / FULL_SUMMARY)}
                            if full_metadata_dir is not None
                            else {}
                        ),
                    },
                    "output_sha256": {
                        name: _sha(bundle / name)
                        for name in (COVERAGE_NAME, ANNEX_NAME, PENDING_NAME)
                    },
                }
                (bundle / SUMMARY_NAME_OUT).write_text(
                    json.dumps(summary, indent=2, sort_keys=True) + "\n",
                    encoding="utf-8",
                )
                if output_dir.exists() or output_dir.is_symlink():
                    raise FileExistsError("Sequence coverage output became occupied")
                os.replace(bundle, output_dir)
                return summary
            finally:
                db.close()
    finally:
        lock.rmdir()
