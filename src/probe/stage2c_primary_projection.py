"""Prepare an isolated 05A2-shaped Stage 2C candidate projection.

Canonical 05A2 rows remain authoritative. This is NOT a production 05A2
bundle, an audit clearance, or a GO donor decision. FULL-only seed metrics
are intentionally left missing until later evidence reconstruction.
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

from probe.stage2c_candidate_delta import _check_gate
from probe.stage2c_evidence_bridge import BRIDGE_FIELDS, GO_SEQ
from probe.stage2c_metadata_reconciliation import (
    METADATA_OUTPUT,
    SEQUENCES_OUTPUT,
    UNIFIED_SEQUENCE_FIELDS,
)
from probe.stage2c_metadata_reconciliation import (
    SUMMARY_OUTPUT as FULL_SUMMARY_OUTPUT,
)
from probe.stage2c_metadata_reuse import REUSED_FIELDS

SCHEMA = "stage2c-primary-projection-v1"
OUTPUT_NAME = "2C_05A2_primary_preview.tsv"
ADDITIONS_NAME = "2C_05A2_new_candidates.tsv"
AUDIT_NAME = "2C_05A2_projection_audit.tsv"
SUMMARY_NAME = "2C_05A2_projection.summary.json"
SEQUENCE_FIELDS = tuple(f"seq_{name}" for name in GO_SEQ)
PROVENANCE_FIELDS = (
    "release",
    "target_id",
    "rid",
    "go_positive_species_wide",
    "full_exact_species_wide",
    "strict_gate",
    "same_species_exact_gate",
    "metric_state",
    "full_sequence_verified",
    "full_metadata_source",
)
REQUIRED_BASE = frozenset(
    {
        "release",
        "target_id",
        "rid",
        "discovery_channel",
        "sequence_discovered",
        "go_positive_sequence_discovered",
        "full_v2_rescue_sequence_discovered",
        "lineage_discovered",
        "sequence_search_record_number",
        "full_v2_record_number",
        "target_primary_bucket",
        "candidate_primary_accession",
        "entry",
        "raw_ox",
        "resolved_taxid",
        "species_taxid",
        "frag",
        "sequence_length",
        "pe",
        "sv",
        "sec",
        "gene",
        *SEQUENCE_FIELDS,
    }
)


def _positive(value: str, name: str) -> int:
    if not value.isascii() or not value.isdecimal():
        raise ValueError(f"Invalid {name}: {value!r}")
    number = int(value)
    if number < 1 or str(number) != value:
        raise ValueError(f"Noncanonical {name}: {value!r}")
    return number


def _reader(path: Path, required: set[str], *, legacy: bool = False):
    with path.open(encoding="utf-8", newline="") as stream:
        reader = csv.DictReader(stream, delimiter="\t")
        fields = reader.fieldnames
        if fields is None or not required.issubset(fields):
            raise ValueError(f"Missing TSV fields: {path}")
        repeated = [name for name in set(fields) if fields.count(name) > 1]
        if repeated and (not legacy or repeated != ["entry"]):
            raise ValueError(f"Repeated TSV fields: {path}: {repeated}")
        for number, row in enumerate(reader, 2):
            if None in row or any(value is None for value in row.values()):
                raise ValueError(f"Malformed TSV row: {path}:{number}")
            yield row


def _source_header(path: Path) -> list[str]:
    with path.open(encoding="utf-8", newline="") as handle:
        header = next(csv.reader(handle, delimiter="\t"), [])
    if not REQUIRED_BASE.issubset(header):
        raise ValueError("Canonical Stage 05A2 header lacks required fields")
    if any(header.count(name) > 1 and name != "entry" for name in header):
        raise ValueError("Unexpected repeated canonical Stage 05A2 column")
    return header


def _load_targets(path: Path, needed: set[str]) -> dict[str, tuple[str, str]]:
    result = {}
    required = {"target_id", "normalized_sequence", "sequence_length"}
    for row in _reader(path, required):
        key = row["target_id"]
        if key not in needed:
            continue
        if key in result:
            raise ValueError(f"Duplicate prepared target: {key}")
        sequence = row["normalized_sequence"]
        if len(sequence) != _positive(row["sequence_length"], "target length"):
            raise ValueError(f"Invalid target sequence length: {key}")
        result[key] = (sequence, row["sequence_length"])
    if set(result) != needed:
        raise ValueError(f"Missing prepared targets: {sorted(needed - set(result))}")
    return result


def _load_taxonomy(path: Path, needed: set[str]) -> dict[str, dict]:
    result = {}
    required = {"target_id", "resolved_taxid", "species_taxid", "primary_bucket"}
    for row in _reader(path, required):
        key = row["target_id"]
        if key in needed:
            if key in result:
                raise ValueError(f"Duplicate target taxonomy: {key}")
            _positive(row["resolved_taxid"], "target TaxID")
            _positive(row["species_taxid"], "target species")
            if not row["primary_bucket"]:
                raise ValueError("Missing target taxonomy bucket")
            result[key] = row
    if set(result) != needed:
        raise ValueError("Missing target taxonomy for Stage 2C bridge")
    return result


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def _verify_full_summary(directory: Path, release: str) -> None:
    path = directory / FULL_SUMMARY_OUTPUT
    result = json.loads(path.read_text(encoding="utf-8"))
    if (
        result.get("schema") != "stage2c-full-metadata-reconciliation-v1"
        or result.get("release") != release
        or result.get("go_donor_decisions") != 0
        or result.get("taxonomy_verified") is not False
        or result.get("complete_stage05a2_replacement") is not False
    ):
        raise ValueError("Unverified FULL reconciliation summary")
    hashes = result.get("output_sha256")
    if not isinstance(hashes, dict):
        raise ValueError("Missing FULL reconciliation checksums")
    for name in (METADATA_OUTPUT, SEQUENCES_OUTPUT):
        if hashes.get(name) != _sha256(directory / name):
            raise ValueError(f"FULL reconciliation checksum mismatch: {name}")


def _attach_full_records(db: sqlite3.Connection, directory: Path, release: str):
    metadata = directory / METADATA_OUTPUT
    sequences = directory / SEQUENCES_OUTPUT
    for row in _reader(metadata, set(REUSED_FIELDS)):
        rid = _positive(row["rid"], "FULL metadata RID")
        if (
            row["release"] != release
            or row["source_full_v2_record_number"] != str(rid)
            or row["frag"] != "0"
            or row["taxonomy_state"] != "REQUIRES_02B_REVALIDATION"
        ):
            raise ValueError("Invalid FULL metadata provenance")
        try:
            db.execute(
                "INSERT INTO full (rid, metadata, sequence) VALUES (?, ?, NULL)",
                (rid, json.dumps(row, sort_keys=True)),
            )
        except sqlite3.IntegrityError as exc:
            raise ValueError(f"Duplicate FULL metadata RID {rid}") from exc
    for row in _reader(sequences, set(UNIFIED_SEQUENCE_FIELDS)):
        rid = _positive(row["rid"], "FULL sequence RID")
        metadata_row = db.execute(
            "SELECT metadata, sequence FROM full WHERE rid=?", (rid,)
        ).fetchone()
        if metadata_row is None or metadata_row[1] is not None:
            raise ValueError("Unexpected or duplicate FULL sequence")
        meta = json.loads(metadata_row[0])
        sequence = row["sequence"]
        sha = hashlib.sha256(sequence.encode("ascii")).hexdigest()
        if (
            row["release"] != release
            or row["full_v2_record_number"] != str(rid)
            or row["candidate_accession"] != meta["candidate_accession"]
            or row["source_state"] != meta["source_state"]
            or row["sequence_sha256"] != sha
            or meta["sequence_sha256"] != sha
            or len(sequence) != _positive(meta["sequence_length"], "FULL length")
        ):
            raise ValueError("FULL sequence identity/provenance mismatch")
        db.execute("UPDATE full SET sequence=? WHERE rid=?", (sequence, rid))
    missing = db.execute("SELECT COUNT(*) FROM full WHERE sequence IS NULL").fetchone()[
        0
    ]
    if missing:
        raise ValueError(f"Missing {missing} FULL sequences")


def _new_row(
    source: dict[str, str],
    header: list[str],
    taxonomy: dict,
    target: tuple[str, str],
    full: tuple[str, str] | None,
) -> tuple[dict[str, str], dict[str, str]]:
    _check_gate(source)
    if source["integration_state"] != "NEEDS_EVIDENCE_RECONSTRUCTION":
        raise ValueError("Unexpected bridge integration state")
    if (
        source["target_resolved_taxid"] != taxonomy["resolved_taxid"]
        or source["species_taxid"] != taxonomy["species_taxid"]
    ):
        raise ValueError("Stage 2C target taxonomy conflicts with 02B")
    go = source["go_positive_species_wide"] == "1"
    full_flag = source["full_exact_species_wide"] == "1"
    _positive(source["candidate_resolved_taxid"], "candidate TaxID")
    _positive(source["candidate_raw_ox"], "candidate OX")
    if not source["candidate_accession"] or source["species_taxid"] == "":
        raise ValueError("Incomplete candidate identity")
    if go:
        _positive(source["go_positive_record_number"], "GO ordinal")
    elif source["go_positive_record_number"]:
        raise ValueError("Unexpected GO ordinal")
    if full_flag:
        if source["full_record_number"] != source["rid"]:
            raise ValueError("FULL record/RID mismatch")
    elif source["full_record_number"]:
        raise ValueError("Unexpected FULL ordinal")
    if go and source["evidence_state"] != "GO_SCANNER_FACTS_VERIFIED":
        raise ValueError("GO source facts are not verified")
    if not go and source["evidence_state"] != "FULL_RECORD_LOOKUP_REQUIRED":
        raise ValueError("Unexpected FULL-only bridge state")
    if go:
        species_bucket = f"S:{taxonomy['species_taxid']}"
        if source["target_primary_bucket"] != species_bucket:
            raise ValueError("GO species-wide bucket conflicts with Stage 02B species")
    if go:
        for name in ("entry", "sequence_length", "pe", "sv"):
            if not source[name]:
                raise ValueError(f"Missing verified GO metadata: {name}")
        for name in SEQUENCE_FIELDS:
            if source[name] == "":
                raise ValueError(f"Missing GO scanner evidence: {name}")
        if source["seq_exact_sequence"] != source["go_positive_exact"]:
            raise ValueError("GO sequence identity flag conflicts")
        if source["seq_candidate_length"] != source["sequence_length"]:
            raise ValueError("GO candidate length mismatch")
        if source["seq_target_length"] != target[1]:
            raise ValueError("GO target length mismatch")
    # The reconciliation stores one FULL record per RID globally, while
    # FULL-exact provenance belongs to a specific (target, RID) pair.
    # A GO-only pair may share its RID with another FULL-exact pair.
    if full_flag and full is None:
        raise ValueError("FULL-exact pair lacks reconciled metadata")
    if full is not None and not full_flag:
        global_meta = json.loads(full[0])
        if (
            global_meta["candidate_accession"] != source["candidate_accession"]
            or global_meta["candidate_raw_ox"] != source["candidate_raw_ox"]
        ):
            raise ValueError("GO-only RID conflicts with global FULL identity")
    if full_flag:
        meta = json.loads(full[0])
        if (
            meta["candidate_accession"] != source["candidate_accession"]
            or meta["candidate_raw_ox"] != source["candidate_raw_ox"]
            or meta["source_full_v2_record_number"] != source["rid"]
        ):
            raise ValueError("FULL metadata conflicts with bridge")
        if full[1] != target[0]:
            raise ValueError("FULL cross-strain sequence not literally identical")
        if go:
            for name in ("entry", "sequence_length", "pe", "sv", "sec", "gene"):
                if source[name] != meta[name]:
                    raise ValueError(f"GO/FULL metadata conflict: {name}")
            if source["go_positive_exact"] != "1":
                raise ValueError("GO and FULL literal sequence status differs")
    else:
        meta = source
    if source["strict_gate"] != (
        "DENIED"
        if source["target_resolved_taxid"] != source["candidate_resolved_taxid"]
        else "DEFER_TO_DOCUMENTARY"
    ):
        raise ValueError("Unexpected strict gate")
    out = dict.fromkeys(header, "")
    out.update(
        {
            "release": source["release"],
            "target_id": source["target_id"],
            "rid": source["rid"],
            "discovery_channel": (
                "STAGE2C_GO_AND_FULL"
                if go and full_flag
                else "STAGE2C_GO_SPECIES_WIDE"
                if go
                else "STAGE2C_FULL_EXACT"
            ),
            "sequence_discovered": "1",
            "go_positive_sequence_discovered": str(int(go)),
            "full_v2_rescue_sequence_discovered": "0",
            "lineage_discovered": "0",
            "sequence_search_record_number": (
                source["go_positive_record_number"] if go else ""
            ),
            "full_v2_record_number": source["rid"] if full_flag else "",
            "target_primary_bucket": taxonomy["primary_bucket"],
            "candidate_primary_accession": source["candidate_accession"],
            "entry": meta["entry"],
            "raw_ox": source["candidate_raw_ox"],
            "resolved_taxid": source["candidate_resolved_taxid"],
            "species_taxid": source["species_taxid"],
            "frag": "0",
            "sequence_length": meta["sequence_length"],
            "pe": meta["pe"],
            "sv": meta["sv"],
            "sec": meta["sec"],
            "gene": meta["gene"],
        }
    )
    if go:
        out.update({name: source[name] for name in SEQUENCE_FIELDS})
    else:
        out["seq_exact_sequence"] = "1"
    audit = {
        "release": source["release"],
        "target_id": source["target_id"],
        "rid": source["rid"],
        "go_positive_species_wide": str(int(go)),
        "full_exact_species_wide": str(int(full_flag)),
        "strict_gate": source["strict_gate"],
        "same_species_exact_gate": source["same_species_exact_gate"],
        "metric_state": (
            "GO_SCANNER_METRICS_VERIFIED" if go else "FULL_EXACT_WITHOUT_KMER_METRICS"
        ),
        "full_sequence_verified": str(int(full_flag)),
        "full_metadata_source": meta.get("source_state", "GO_SCANNER"),
    }
    return out, audit


def prepare_primary_projection(
    *,
    release: str,
    canonical_05a2: Path,
    bridge: Path,
    target_taxonomy: Path,
    prepared_targets: Path,
    output_dir: Path,
    full_metadata_dir: Path | None = None,
) -> dict:
    """Build an immutable preview, never mutate production Stage 05A2."""
    if not release or not output_dir.parent.is_dir():
        raise ValueError("Invalid release or output parent")
    if output_dir.exists() or output_dir.is_symlink():
        raise FileExistsError("Projection output destination is occupied")
    inputs = [canonical_05a2, bridge, target_taxonomy, prepared_targets]
    if full_metadata_dir is not None:
        if not full_metadata_dir.is_dir() or full_metadata_dir.is_symlink():
            raise ValueError("Missing FULL metadata directory")
        inputs += [
            full_metadata_dir / METADATA_OUTPUT,
            full_metadata_dir / SEQUENCES_OUTPUT,
            full_metadata_dir / FULL_SUMMARY_OUTPUT,
        ]
    if any(not item.is_file() for item in inputs):
        raise FileNotFoundError("Missing primary projection input")
    if len({item.resolve() for item in inputs}) != len(inputs):
        raise ValueError("Overlapping primary projection inputs")
    header = _source_header(canonical_05a2)
    lock = output_dir.with_name(f".{output_dir.name}.publish-lock")
    lock.mkdir()
    try:
        with tempfile.TemporaryDirectory(
            prefix=".probe-2c-primary-", dir=output_dir.parent
        ) as temporary:
            root = Path(temporary)
            db = sqlite3.connect(str(root / "keys.sqlite3"))
            try:
                db.execute(
                    "CREATE TABLE canonical (release TEXT, target TEXT, rid INT, "
                    "PRIMARY KEY (release, target, rid))"
                )
                db.execute(
                    "CREATE TABLE b (target TEXT, rid INT, payload TEXT, "
                    "PRIMARY KEY (target, rid))"
                )
                db.execute(
                    "CREATE TABLE full (rid INT PRIMARY KEY, "
                    "metadata TEXT, sequence TEXT)"
                )
                counts: Counter[str] = Counter()
                for row in _reader(canonical_05a2, set(REQUIRED_BASE), legacy=True):
                    rid = _positive(row["rid"], "canonical RID")
                    if not row["release"] or not row["target_id"]:
                        raise ValueError("Invalid canonical 05A2 key")
                    try:
                        db.execute(
                            "INSERT INTO canonical VALUES (?, ?, ?)",
                            (row["release"], row["target_id"], rid),
                        )
                    except sqlite3.IntegrityError as exc:
                        raise ValueError("Duplicate canonical candidate") from exc
                    counts["canonical_05a2_rows"] += 1
                needed: set[str] = set()
                full_needed: set[int] = set()
                for row in _reader(bridge, set(BRIDGE_FIELDS)):
                    if row["release"] != release or not row["target_id"]:
                        raise ValueError("Bridge release or target mismatch")
                    rid = _positive(row["rid"], "bridge RID")
                    key = (release, row["target_id"], rid)
                    if db.execute(
                        "SELECT 1 FROM canonical WHERE release=? "
                        "AND target=? AND rid=?",
                        key,
                    ).fetchone():
                        raise ValueError("Stage 2C delta overlaps canonical 05A2")
                    try:
                        db.execute(
                            "INSERT INTO b VALUES (?, ?, ?)",
                            (row["target_id"], rid, json.dumps(row)),
                        )
                    except sqlite3.IntegrityError as exc:
                        raise ValueError("Duplicate bridge target/RID") from exc
                    needed.add(row["target_id"])
                    if row["full_exact_species_wide"] == "1":
                        full_needed.add(rid)
                    counts["new_candidate_rows"] += 1
                taxonomy = _load_taxonomy(target_taxonomy, needed)
                targets = _load_targets(prepared_targets, needed)
                if full_needed and full_metadata_dir is None:
                    raise ValueError("FULL records require reconciled metadata")
                if full_metadata_dir is not None:
                    _verify_full_summary(full_metadata_dir, release)
                    _attach_full_records(db, full_metadata_dir, release)
                seen_rids = {number for (number,) in db.execute("SELECT rid FROM full")}
                if seen_rids != full_needed:
                    raise ValueError("FULL reconciliation RID universe mismatch")
                bundle = root / "bundle"
                bundle.mkdir()
                with (
                    (bundle / OUTPUT_NAME).open(
                        "x", newline="", encoding="utf-8"
                    ) as preview,
                    (bundle / ADDITIONS_NAME).open(
                        "x", newline="", encoding="utf-8"
                    ) as additions,
                    (bundle / AUDIT_NAME).open(
                        "x", newline="", encoding="utf-8"
                    ) as audit_handle,
                ):
                    primary_writer = csv.DictWriter(
                        preview,
                        fieldnames=header,
                        delimiter="\t",
                        lineterminator="\n",
                        extrasaction="raise",
                    )
                    additions_writer = csv.DictWriter(
                        additions,
                        fieldnames=header,
                        delimiter="\t",
                        lineterminator="\n",
                        extrasaction="raise",
                    )
                    audit_writer = csv.DictWriter(
                        audit_handle,
                        fieldnames=PROVENANCE_FIELDS,
                        delimiter="\t",
                        lineterminator="\n",
                    )
                    primary_writer.writeheader()
                    additions_writer.writeheader()
                    audit_writer.writeheader()
                    for row in _reader(canonical_05a2, set(REQUIRED_BASE), legacy=True):
                        primary_writer.writerow(row)
                    for target_id, rid, text in db.execute(
                        "SELECT target, rid, payload FROM b ORDER BY target, rid"
                    ):
                        source = json.loads(text)
                        record = db.execute(
                            "SELECT metadata, sequence FROM full WHERE rid=?",
                            (rid,),
                        ).fetchone()
                        result, evidence = _new_row(
                            source,
                            header,
                            taxonomy[target_id],
                            targets[target_id],
                            record,
                        )
                        primary_writer.writerow(result)
                        additions_writer.writerow(result)
                        audit_writer.writerow(evidence)
                        counts[evidence["metric_state"]] += 1
                summary = {
                    "schema": SCHEMA,
                    "release": release,
                    "counts": dict(sorted(counts.items())),
                    "canonical_preserved": True,
                    "full_only_seed_metrics_computed": False,
                    "production_05a2_replacement": False,
                    "stage_05d_and_06a_ready": False,
                    "taxonomy_source": "STAGE_02B_TARGET_CONTEXT",
                    "full_exact_literal_sequence_verified": True,
                    "go_donor_decisions": 0,
                    "output_sha256": {
                        name: _sha256(bundle / name)
                        for name in (OUTPUT_NAME, ADDITIONS_NAME, AUDIT_NAME)
                    },
                }
                (bundle / SUMMARY_NAME).write_text(
                    json.dumps(summary, indent=2, sort_keys=True) + "\n",
                    encoding="utf-8",
                )
                if output_dir.exists() or output_dir.is_symlink():
                    raise FileExistsError("Output occupied before publication")
                os.replace(bundle, output_dir)
                return summary
            finally:
                db.close()
    finally:
        lock.rmdir()
