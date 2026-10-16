"""Conservatively bridge Stage 2C candidate delta to evidence reconstruction.

Never creates Stage 05A2 rows or makes a donor/association decision.
FULL-only records require a separate, verified source of UniProt metadata.
"""

from __future__ import annotations

import csv
import json
import os
import sqlite3
import tempfile
from collections import Counter
from pathlib import Path

from probe.stage2c_candidate_delta import DELTA_COLUMNS, _check_gate
from probe.stage2c_candidate_union import FULL_REQUIRED, GO_REQUIRED

GO_FACTS = (
    "target_bucket",
    "entry",
    "candidate_length",
    "pe",
    "sv",
    "sec",
    "gene",
)
GO_SEQ = (
    "target_length",
    "candidate_length",
    "length_ratio",
    "candidate_over_target_length",
    "target_distinct_kmers",
    "shared_distinct_kmers",
    "target_shared_fraction",
    "exact_sequence",
    "shared_unique_positional_kmers",
    "target_seed_span_fraction",
    "candidate_seed_span_fraction",
    "target_seed_residue_coverage",
    "candidate_seed_residue_coverage",
    "lis_count",
    "lis_fraction",
    "diagonal_min",
    "diagonal_max",
    "diagonal_range",
)
BRIDGE_EXTRA = (
    "evidence_state",
    "target_primary_bucket",
    "entry",
    "sequence_length",
    "pe",
    "sv",
    "sec",
    "gene",
    *(f"seq_{name}" for name in GO_SEQ),
)
BRIDGE_FIELDS = (*DELTA_COLUMNS, *BRIDGE_EXTRA)
REQUEST_FIELDS = (
    "release",
    "rid",
    "candidate_accession",
    "candidate_raw_ox",
    "candidate_resolved_taxid",
    "species_taxid",
    "full_record_number",
    "target_reference_count",
    "request_reason",
)
SCHEMA = "stage2c-evidence-bridge-v1"


def _read(path: Path, required: set[str]):
    with path.open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle, delimiter="\t")
        fields = reader.fieldnames
        if fields is None or len(fields) != len(set(fields)):
            raise ValueError(f"Invalid TSV header: {path}")
        missing = required - set(fields)
        if missing:
            raise ValueError(f"Missing columns in {path}: {sorted(missing)}")
        for lineno, row in enumerate(reader, start=2):
            if None in row or any(value is None for value in row.values()):
                raise ValueError(f"Malformed TSV row: {path}:{lineno}")
            yield row


def _rid(value: str) -> str:
    if not value.isascii() or not value.isdecimal():
        raise ValueError(f"Invalid RID {value!r}")
    if str(int(value)) != value or int(value) < 1:
        raise ValueError(f"Noncanonical RID {value!r}")
    return value


def _match_go(delta: dict, go: dict) -> None:
    fields = (
        ("rid", "rid"),
        ("candidate_accession", "accession"),
        ("candidate_raw_ox", "raw_ox"),
        ("candidate_resolved_taxid", "resolved_taxid"),
        ("species_taxid", "species_taxid"),
        ("go_positive_record_number", "record_number"),
        ("go_positive_exact", "exact_sequence"),
    )
    if any(delta[a] != go[b] for a, b in fields):
        raise ValueError("GO-positive record contradicts discovery delta")
    if go["frag"] != "0":
        raise ValueError("Fragment in GO-positive sequence-only discovery")


def _match_full(delta: dict, full: dict, release: str) -> None:
    fields = (
        ("rid", "rid"),
        ("candidate_accession", "accession"),
        ("candidate_raw_ox", "raw_ox"),
        ("target_resolved_taxid", "target_resolved_taxid"),
        ("candidate_resolved_taxid", "candidate_resolved_taxid"),
        ("species_taxid", "species_taxid"),
        ("full_record_number", "record_number"),
    )
    if any(delta[a] != full[b] for a, b in fields):
        raise ValueError("FULL exact record contradicts discovery delta")
    if (
        full["release"] != release
        or full["frag"] != "0"
        or full["exact_full_sequence"] != "1"
        or full["discovery_channel"] != "FULL_V2_EXACT"
        or full["length_aa"] == ""
    ):
        raise ValueError("Invalid FULL exact provenance")
    _rid(full["length_aa"])
    if full["record_number"] != full["rid"]:
        raise ValueError("FULL record number differs from canonical RID")


def _load_delta(db: sqlite3.Connection, path: Path, release: str) -> int:
    count = 0
    for row in _read(path, set(DELTA_COLUMNS)):
        if row["release"] != release:
            raise ValueError("Delta release mismatch")
        if row["integration_state"] != "NEEDS_EVIDENCE_RECONSTRUCTION":
            raise ValueError("Unexpected delta integration state")
        if not row["target_id"]:
            raise ValueError("Empty target ID")
        rid = _rid(row["rid"])
        _check_gate(row)
        go_flag = row["go_positive_species_wide"]
        full_flag = row["full_exact_species_wide"]
        if go_flag not in {"0", "1"} or full_flag not in {"0", "1"}:
            raise ValueError("Invalid discovery flags")
        if go_flag == full_flag == "0":
            raise ValueError("Delta candidate lacks discovery evidence")
        for flag, field in (
            (go_flag, "go_positive_record_number"),
            (full_flag, "full_record_number"),
        ):
            if flag == "1":
                _rid(row[field])
            elif row[field]:
                raise ValueError("Unexpected record number without provenance")
        try:
            db.execute(
                "INSERT INTO delta (target, rid, payload) VALUES (?, ?, ?)",
                (row["target_id"], rid, json.dumps(row)),
            )
        except sqlite3.IntegrityError as exc:
            raise ValueError("Duplicate delta target/RID") from exc
        count += 1
    return count


def _attach(
    db: sqlite3.Connection,
    path: Path,
    *,
    source: str,
    release: str,
) -> None:
    required = (
        set(GO_REQUIRED) | set(GO_FACTS) | set(GO_SEQ)
        if source == "go"
        else set(FULL_REQUIRED)
    )
    column = "go_row" if source == "go" else "full_row"
    flag = "go_positive_species_wide" if source == "go" else "full_exact_species_wide"
    for row in _read(path, required):
        if source == "full" and row["release"] != release:
            raise ValueError("FULL exact source release mismatch")
        key = (row["target_id"], _rid(row["rid"]))
        match = db.execute(
            "SELECT payload, go_row, full_row FROM delta WHERE target=? AND rid=?",
            key,
        ).fetchone()
        if match is None:
            continue
        delta = json.loads(match[0])
        if delta[flag] != "1":
            raise ValueError("Scanner provenance contradicts delta")
        if source == "go":
            _match_go(delta, row)
        else:
            _match_full(delta, row, release)
        old = match[1] if source == "go" else match[2]
        if old is not None:
            raise ValueError("Duplicate scanner target/RID")
        db.execute(
            f"UPDATE delta SET {column}=? WHERE target=? AND rid=?",
            (json.dumps(row), *key),
        )


def prepare_evidence_bridge(
    *,
    release: str,
    delta: Path,
    go_positive_pairs: Path,
    full_exact_pairs: Path,
    output_dir: Path,
) -> dict:
    """Publish a new small bridge bundle, never mutate upstream artifacts."""
    if not release or not output_dir.parent.is_dir():
        raise ValueError("Invalid release or output directory parent")
    if output_dir.exists() or output_dir.is_symlink():
        raise FileExistsError("Evidence bridge destination already exists")
    for path in (delta, go_positive_pairs, full_exact_pairs):
        if not path.is_file():
            raise FileNotFoundError(path)
    if len({p.resolve() for p in (delta, go_positive_pairs, full_exact_pairs)}) != 3:
        raise ValueError("Bridge inputs must be distinct")
    with tempfile.TemporaryDirectory(
        prefix=".probe-2c-bridge-", dir=output_dir.parent
    ) as tmp:
        base = Path(tmp)
        db = sqlite3.connect(str(base / "bridge.sqlite3"))
        db.execute(
            "CREATE TABLE delta (target TEXT NOT NULL, rid TEXT NOT NULL, "
            "payload TEXT NOT NULL, go_row TEXT, full_row TEXT, "
            "PRIMARY KEY (target, rid))"
        )
        db.execute(
            "CREATE TABLE request (rid TEXT PRIMARY KEY, accession TEXT, "
            "raw_ox TEXT, candidate_tax TEXT, species TEXT, "
            "ordinal TEXT, refs INTEGER NOT NULL)"
        )
        counts: Counter[str] = Counter()
        try:
            counts["delta_candidates"] = _load_delta(db, delta, release)
            _attach(db, go_positive_pairs, source="go", release=release)
            _attach(db, full_exact_pairs, source="full", release=release)
            bundle = base / "bundle"
            bundle.mkdir()
            bridge_path = bundle / "2C_evidence_bridge.tsv"
            requests_path = bundle / "2C_full_record_requests.tsv"
            with (
                bridge_path.open("x", encoding="utf-8", newline="") as bridge,
                requests_path.open("x", encoding="utf-8", newline="") as requests,
            ):
                bridge_writer = csv.DictWriter(
                    bridge,
                    fieldnames=BRIDGE_FIELDS,
                    delimiter="\t",
                    lineterminator="\n",
                )
                request_writer = csv.DictWriter(
                    requests,
                    fieldnames=REQUEST_FIELDS,
                    delimiter="\t",
                    lineterminator="\n",
                )
                bridge_writer.writeheader()
                request_writer.writeheader()
                for raw, go_json, full_json in db.execute(
                    "SELECT payload, go_row, full_row FROM delta "
                    "ORDER BY target, CAST(rid AS INTEGER)"
                ):
                    row = json.loads(raw)
                    go = json.loads(go_json) if go_json is not None else None
                    full = json.loads(full_json) if full_json is not None else None
                    if (go is None) != (row["go_positive_species_wide"] == "0"):
                        raise ValueError("Missing or unexpected GO source pair")
                    if (full is None) != (row["full_exact_species_wide"] == "0"):
                        raise ValueError("Missing or unexpected FULL source pair")
                    result = {name: row[name] for name in DELTA_COLUMNS}
                    result.update({name: "" for name in BRIDGE_EXTRA})
                    if go is not None:
                        result.update(
                            {
                                "evidence_state": "GO_SCANNER_FACTS_VERIFIED",
                                "target_primary_bucket": go["target_bucket"],
                                "entry": go["entry"],
                                "sequence_length": go["candidate_length"],
                                "pe": go["pe"],
                                "sv": go["sv"],
                                "sec": go["sec"],
                                "gene": go["gene"],
                                **{f"seq_{key}": go[key] for key in GO_SEQ},
                            }
                        )
                        counts["go_metadata_verified"] += 1
                    else:
                        result["evidence_state"] = "FULL_RECORD_LOOKUP_REQUIRED"
                        counts["full_metadata_pending"] += 1
                    bridge_writer.writerow(result)
                    if full is not None:
                        rid = row["rid"]
                        identity = (
                            row["candidate_accession"],
                            row["candidate_raw_ox"],
                            row["candidate_resolved_taxid"],
                            row["species_taxid"],
                            row["full_record_number"],
                        )
                        existing = db.execute(
                            "SELECT accession, raw_ox, candidate_tax, "
                            "species, ordinal FROM request WHERE rid=?",
                            (rid,),
                        ).fetchone()
                        if existing is None:
                            db.execute(
                                "INSERT INTO request VALUES (?, ?, ?, ?, ?, ?, 1)",
                                (rid, *identity),
                            )
                        elif tuple(existing) != identity:
                            raise ValueError("Conflicting FULL record metadata")
                        else:
                            db.execute(
                                "UPDATE request SET refs=refs+1 WHERE rid=?",
                                (rid,),
                            )
                for request in db.execute(
                    "SELECT rid, accession, raw_ox, candidate_tax, "
                    "species, ordinal, refs FROM request "
                    "ORDER BY CAST(rid AS INTEGER)"
                ):
                    rid, accession, ox, tax, species, ordinal, refs = request
                    request_writer.writerow(
                        {
                            "release": release,
                            "rid": rid,
                            "candidate_accession": accession,
                            "candidate_raw_ox": ox,
                            "candidate_resolved_taxid": tax,
                            "species_taxid": species,
                            "full_record_number": ordinal,
                            "target_reference_count": refs,
                            "request_reason": "PINNED_FULL_V2_METADATA_VERIFICATION",
                        }
                    )
                counts["unique_full_record_requests"] = db.execute(
                    "SELECT COUNT(*) FROM request"
                ).fetchone()[0]
            result = {
                "schema": SCHEMA,
                "release": release,
                "go_donor_decisions": 0,
                "complete_stage05a2_replacement": False,
                "full_fasta_scanned": False,
                "counts": dict(sorted(counts.items())),
            }
            (bundle / "2C_evidence_bridge.summary.json").write_text(
                json.dumps(result, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            db.close()
            if output_dir.exists() or output_dir.is_symlink():
                raise FileExistsError("Evidence bridge destination occupied")
            os.replace(bundle, output_dir)
            return result
        finally:
            db.close()
