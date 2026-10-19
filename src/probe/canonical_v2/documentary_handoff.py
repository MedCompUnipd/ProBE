"""Attest candidate keys from release-local documentary lookup, without donors.

This module is a strict handoff from a discovery-only index. All matched
observations are retained; only literal, type-consistent, unique typed loci
are flagged as *potential* documentary support. No DP or GO decisions.
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

from probe.canonical_v2.documentary_lookup import (
    FIELDS as LOOKUP_FIELDS,
)
from probe.canonical_v2.documentary_lookup import (
    OUTPUT as LOOKUP_OUTPUT,
)
from probe.canonical_v2.documentary_lookup import (
    SCHEMA as LOOKUP_SCHEMA,
)
from probe.canonical_v2.documentary_lookup import (
    SUMMARY as LOOKUP_SUMMARY,
)
from probe.resolver import is_known_nonidentifying_locus

SCHEMA = "probe-canonical-v2-documentary-handoff-v1"
CANDIDATES = "documentary_candidate_handoff.tsv"
EVIDENCE = "documentary_evidence_observations.tsv"
SUMMARY = "documentary_handoff.summary.json"
CANDIDATE_FIELDS = (
    "release", "target_id", "rid", "candidate_accession", "candidate_raw_ox",
    "candidate_resolved_taxid", "target_resolved_taxid", "candidate_frag",
    "candidate_sequence_sha256", "evidence_observations", "literal_matches",
    "normalized_hints", "potential_unique_locus_matches", "potential_dp_input",
    "go_donor_authorized", "sources_json", "origin_releases_json",
)
EVIDENCE_FIELDS = (*LOOKUP_FIELDS, "potential_unique_locus")

IDENTITY = (
    "candidate_accession", "candidate_raw_ox", "candidate_resolved_taxid",
    "target_resolved_taxid", "candidate_frag", "candidate_sequence_sha256",
)
SOURCE_KINDS = {"EXACT", "INDEPENDENT_04D1"}
KEY_TYPES = {"ACCESSION", "GENE_NAME", "GENE_SYNONYM", "ORDERED_LOCUS", "ORF"}
LOCUS_TYPES = {"ORDERED_LOCUS", "ORF"}


def _sha(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def _positive(raw: str, label: str) -> int:
    if not raw or not raw.isascii() or not raw.isdecimal():
        raise ValueError(f"Invalid {label}: {raw!r}")
    value = int(raw)
    if value < 1 or str(value) != raw:
        raise ValueError(f"Noncanonical {label}: {raw!r}")
    return value


def _validate_bundle(directory: Path, release: str) -> tuple[Path, dict, str]:
    if not directory.is_dir() or directory.is_symlink():
        raise ValueError("Missing or unsafe documentary lookup bundle")
    payload = directory / LOOKUP_OUTPUT
    summary_path = directory / LOOKUP_SUMMARY
    if any(not p.is_file() or p.is_symlink() for p in (payload, summary_path)):
        raise ValueError("Missing or unsafe documentary lookup payload")
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    if (
        summary.get("schema") != LOOKUP_SCHEMA
        or summary.get("release") != release
        or summary.get("nonexact_dp_authorized") != 0
        or summary.get("go_donor_decisions") != 0
        or summary.get("documentary_query_mode")
        != "RETROSPECTIVE_START_END_UNION"
        or summary.get("output_sha256", {}).get(LOOKUP_OUTPUT) != _sha(payload)
    ):
        raise ValueError("Documentary lookup manifest or SHA256 mismatch")
    return payload, summary, _sha(summary_path)


def _locus_support(row: dict[str, str]) -> bool:
    kind = row["source_key_type"]
    return (
        row["match_class"] == "LITERAL"
        and kind == row["candidate_key_type"]
        and kind in LOCUS_TYPES
        and row["source_key_value"] == row["candidate_key_value"]
        and _positive(row["target_key_multiplicity"], "target multiplicity") == 1
        and _positive(row["candidate_key_multiplicity"], "candidate multiplicity")
        == 1
        and not is_known_nonidentifying_locus(kind, row["source_key_value"])
    )


def _verify_row(
    row: dict[str, str], release: str, allowed_releases: frozenset[str]
) -> None:
    if row["release"] != release or not row["target_id"]:
        raise ValueError("Documentary evidence crossed a release/target boundary")
    for key in ("candidate_rid", "candidate_resolved_taxid", "target_resolved_taxid"):
        _positive(row[key], key)
    if row["candidate_resolved_taxid"] != row["target_resolved_taxid"]:
        raise ValueError("Cross-TaxID NONEXACT documentary candidate")
    if row["candidate_frag"] not in {"0", "1"}:
        raise ValueError("Invalid fragment flag")
    if row["source_kind"] not in SOURCE_KINDS:
        raise ValueError("Unexpected documentary origin")
    if row["source_key_type"] not in KEY_TYPES:
        raise ValueError("Unexpected source key type")
    if row["candidate_key_type"] not in KEY_TYPES:
        raise ValueError("Unexpected candidate key type")
    if row["match_class"] not in {"LITERAL", "NORMALIZED_HINT"}:
        raise ValueError("Unexpected textual match classification")
    if row["exact_origin_release"] not in allowed_releases:
        raise ValueError("Unknown documentary source release")
    if (
        row["source_kind"] == "INDEPENDENT_04D1"
        and row["exact_origin_release"] != release
    ):
        raise ValueError("Cross-release independent source is forbidden")
    if not row["candidate_accession"]:
        raise ValueError("Missing candidate accession")
    digest = row["candidate_sequence_sha256"]
    if len(digest) != 64 or any(char not in "0123456789abcdef" for char in digest):
        raise ValueError("Invalid candidate sequence SHA256")
    if row["source_kind"] == "EXACT":
        _positive(row["exact_source_rid"], "EXACT source RID")
        if row["exact_source_tax_scope"] not in {"SAME_TAXID", "SAME_SPECIES"}:
            raise ValueError("Invalid EXACT source taxonomy scope")
    elif row["exact_source_rid"]:
        raise ValueError("Independent evidence contains an EXACT RID")


def _import_lookup(
    db: sqlite3.Connection,
    payload: Path,
    release: str,
    counters: Counter[str],
    allowed_releases: frozenset[str],
) -> None:
    with payload.open(encoding="utf-8", newline="") as stream:
        reader = csv.DictReader(stream, delimiter="\t")
        if tuple(reader.fieldnames or ()) != LOOKUP_FIELDS:
            raise ValueError("Documentary lookup header differs from pinned schema")
        for lineno, row in enumerate(reader, start=2):
            if None in row or any(value is None for value in row.values()):
                raise ValueError(f"Malformed documentary row at {payload}:{lineno}")
            _verify_row(row, release, allowed_releases)
            key = (release, row["target_id"], row["candidate_rid"])
            identity = json.dumps(
                [row[name] for name in IDENTITY], separators=(",", ":")
            )
            found = db.execute(
                "SELECT identity FROM candidate WHERE role=? AND target=? AND rid=?",
                key,
            ).fetchone()
            if found is None:
                db.execute(
                    "INSERT INTO candidate VALUES (?,?,?,?)",
                    (*key, identity),
                )
            elif found[0] != identity:
                raise ValueError(f"Conflicting candidate identity: {key}")
            supported = int(_locus_support(row))
            db.execute(
                "INSERT INTO evidence VALUES (?,?,?,?,?,?,?,?)",
                (
                    *key, row["source_kind"], row["exact_origin_release"],
                    row["match_class"], supported,
                    json.dumps(row, separators=(",", ":"), sort_keys=True),
                ),
            )
            counters["evidence_observations"] += 1
            counters[f"{release}_observations"] += 1
            if supported:
                counters["potential_unique_locus_observations"] += 1


def _publish(db: sqlite3.Connection, directory: Path) -> Counter[str]:
    counts: Counter[str] = Counter()
    cursor = db.execute(
        "SELECT role,target,rid,source,origin,match_class,locus,row "
        "FROM evidence ORDER BY role,target,CAST(rid AS INTEGER),row"
    )
    with (directory / EVIDENCE).open(
        "x", encoding="utf-8", newline=""
    ) as stream:
        writer = csv.DictWriter(
            stream, fieldnames=EVIDENCE_FIELDS, delimiter="\t",
            lineterminator="\n",
        )
        writer.writeheader()
        for _, _, _, _, _, _, locus, raw in cursor:
            source = json.loads(raw)
            record = {**source, "potential_unique_locus": str(locus)}
            writer.writerow(record)
            counts["evidence_rows"] += 1
    query = (
        "SELECT c.role,c.target,c.rid,c.identity,COUNT(e.row),"
        "SUM(e.match_class='LITERAL'),SUM(e.match_class='NORMALIZED_HINT'),"
        "SUM(e.locus),GROUP_CONCAT(DISTINCT e.source),"
        "GROUP_CONCAT(DISTINCT e.origin) FROM candidate c "
        "JOIN evidence e ON (e.role,e.target,e.rid)=(c.role,c.target,c.rid) "
        "GROUP BY c.role,c.target,c.rid ORDER BY c.role,c.target,"
        "CAST(c.rid AS INTEGER)"
    )
    with (directory / CANDIDATES).open(
        "x", encoding="utf-8", newline=""
    ) as stream:
        writer = csv.DictWriter(
            stream, fieldnames=CANDIDATE_FIELDS, delimiter="\t",
            lineterminator="\n",
        )
        writer.writeheader()
        for (
            role, target, rid, identity, n, literal, hint, locus,
            sources, origins,
        ) in db.execute(query):
            accession, raw_ox, tax, target_tax, frag, sha = json.loads(identity)
            record = {
                "release": role, "target_id": target, "rid": rid,
                "candidate_accession": accession, "candidate_raw_ox": raw_ox,
                "candidate_resolved_taxid": tax,
                "target_resolved_taxid": target_tax, "candidate_frag": frag,
                "candidate_sequence_sha256": sha,
                "evidence_observations": str(n), "literal_matches": str(literal),
                "normalized_hints": str(hint),
                "potential_unique_locus_matches": str(locus),
                "potential_dp_input": "1" if locus else "0",
                "go_donor_authorized": "0",
                "sources_json": json.dumps(sorted(sources.split(","))),
                "origin_releases_json": json.dumps(
                    sorted(origin for origin in origins.split(",") if origin)
                ),
            }
            writer.writerow(record)
            counts["candidate_rows"] += 1
            counts[f"{role}_candidates"] += 1
            if locus:
                counts["potential_dp_inputs"] += 1
    return counts


def prepare_documentary_handoff(
    *, start_lookup: Path, end_lookup: Path,
    start_release: str, end_release: str, destination: Path,
) -> dict:
    """Publish per-release documentary evidence, never a GO donor decision."""
    if not start_release or not end_release or start_release == end_release:
        raise ValueError("Distinct START and END release identities are required")
    if destination.exists() or destination.is_symlink():
        raise FileExistsError("Documentary handoff destination already occupied")
    if not destination.parent.is_dir() or destination.parent.is_symlink():
        raise ValueError("Documentary handoff output parent is unsafe")
    for source in (start_lookup, end_lookup):
        if source == destination or source in destination.parents:
            raise ValueError("Handoff output cannot nest inside source")
        if destination in source.parents:
            raise ValueError("Lookup source cannot nest inside handoff output")
    start, start_info, start_manifest = _validate_bundle(
        start_lookup, start_release
    )
    end, end_info, end_manifest = _validate_bundle(
        end_lookup, end_release
    )
    start_inputs = start_info.get("input_sha256", {})
    end_inputs = end_info.get("input_sha256", {})
    if (
        start_info.get("other_exact_release") != end_release
        or end_info.get("other_exact_release") != start_release
        or start_inputs.get("targets") != end_inputs.get("targets")
        or start_inputs.get("exact_register_summary")
        != end_inputs.get("other_exact_register_summary")
        or end_inputs.get("exact_register_summary")
        != start_inputs.get("other_exact_register_summary")
    ):
        raise ValueError("START/END documentary register provenance disagrees")
    counters: Counter[str] = Counter()
    with tempfile.TemporaryDirectory(
        prefix="probe-v2-documentary-handoff-", dir=destination.parent
    ) as temporary:
        product = Path(temporary) / "product"
        product.mkdir()
        db = sqlite3.connect(Path(temporary) / "handoff.sqlite3")
        try:
            db.execute(
                "CREATE TABLE candidate (role TEXT,target TEXT,rid TEXT,"
                "identity TEXT,PRIMARY KEY(role,target,rid))"
            )
            db.execute(
                "CREATE TABLE evidence (role TEXT,target TEXT,rid TEXT,"
                "source TEXT,origin TEXT,match_class TEXT,locus INTEGER,row TEXT)"
            )
            allowed = frozenset({start_release, end_release})
            _import_lookup(db, start, start_release, counters, allowed)
            _import_lookup(db, end, end_release, counters, allowed)
            counters.update(_publish(db, product))
        finally:
            db.close()
        summary = {
            "schema": SCHEMA,
            "releases": {"START": start_release, "END": end_release},
            "scope": "RELEASE_LOCAL_CANDIDATES_SHARED_EXACT_LOOKUP_ANCHORS",
            "go_donor_decisions": 0,
            "dp_executed": False,
            "dp_authorized": False,
            "matched_locus_does_not_prove_target_identity": True,
            "counts": dict(sorted(counters.items())),
            "input_sha256": {
                "start_lookup_summary": start_manifest,
                "end_lookup_summary": end_manifest,
            },
            "output_sha256": {
                name: _sha(product / name)
                for name in (CANDIDATES, EVIDENCE)
            },
        }
        if (
            _sha(start) != start_info["output_sha256"][LOOKUP_OUTPUT]
            or _sha(end) != end_info["output_sha256"][LOOKUP_OUTPUT]
            or _sha(start_lookup / LOOKUP_SUMMARY) != start_manifest
            or _sha(end_lookup / LOOKUP_SUMMARY) != end_manifest
        ):
            raise ValueError("Documentary lookup changed during handoff")
        (product / SUMMARY).write_text(
            json.dumps(summary, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        if destination.exists() or destination.is_symlink():
            raise FileExistsError("Handoff destination appeared concurrently")
        os.rename(product, destination)
    return summary
