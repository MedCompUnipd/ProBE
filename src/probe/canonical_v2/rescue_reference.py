"""Derive historical-equivalent rescue coverage without old Stage 05 artifacts.

This module reads primary GO-positive species-wide discovery, Stage 04E
lineage, and shared Stage 01/02B/04B inputs. It does not scan proteins,
filter donors, execute alignment, or use old 05A/05B/05R0 publications.

The reference projection is conditional on independently attested scanner
identity (GO FASTA, target set, shared k-mer settings, and v1/v2 algorithms).
Its contract JSON records that requirement; the DAG must attest manifests.
"""

from __future__ import annotations

import csv
import hashlib
import json
import os
import sqlite3
import tempfile
from collections import Counter
from collections.abc import Iterator
from pathlib import Path

from probe.canonical_v2.contracts import LINEAGE_REQUIRED
from probe.stage2c_candidate_union import GO_REQUIRED

SCHEMA = "probe-canonical-v2-rescue-reference-v1"
CONTRACT_SCHEMA = "probe-canonical-v2-scan-equivalence-v1"
COVERAGE_NAME = "rescue_reference_coverage.tsv"
TARGETS_NAME = "rescue_targets.tsv"
TAXONOMY_NAME = "rescue_target_taxonomy.tsv"
IDS_NAME = "rescue_target_ids.txt"
SUMMARY_NAME = "rescue_reference.summary.json"
SCAN_FIELDS = (
    "k",
    "max_required_shared",
    "min_target_kmer_fraction",
    "fragment_policy",
    "go_positive_fasta_sha256",
    "target_input_sha256",
    "target_taxonomy_sha256",
    "ox_taxonomy_sha256",
)
COVERAGE_FIELDS = (
    "release",
    "target_id",
    "sequence_length",
    "kmer_sequence_searchable",
    "species_wide_pairs",
    "reference_go_pairs",
    "lineage_pairs",
    "eligible_direct_anchor_rows",
    "reference_covered",
    "selection_reason",
)


def _digest(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _read(
    path: Path, required: frozenset[str] | set[str]
) -> Iterator[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as source:
        reader = csv.DictReader(source, delimiter="\t")
        fields = reader.fieldnames
        if fields is None or len(fields) != len(set(fields)):
            raise ValueError(f"Invalid TSV header: {path}")
        missing = required.difference(fields)
        if missing:
            raise ValueError(f"Missing columns: {path}: {sorted(missing)}")
        for number, row in enumerate(reader, start=2):
            if None in row or any(value is None for value in row.values()):
                raise ValueError(f"Malformed TSV row: {path}:{number}")
            yield row


def _positive(value: str, label: str) -> int:
    if not value.isascii() or not value.isdecimal():
        raise ValueError(f"Invalid {label}: {value!r}")
    parsed = int(value)
    if parsed < 1 or str(parsed) != value:
        raise ValueError(f"Noncanonical {label}: {value!r}")
    return parsed


def _indexed(
    path: Path, fields: set[str], key_column: str
) -> tuple[list[str], dict[str, dict[str, str]]]:
    with path.open(encoding="utf-8", newline="") as stream:
        reader = csv.DictReader(stream, delimiter="\t")
        header = reader.fieldnames
        if header is None or len(header) != len(set(header)):
            raise ValueError(f"Invalid index header: {path}")
        if missing := fields.difference(header):
            raise ValueError(f"Missing index columns: {sorted(missing)}")
        records: dict[str, dict[str, str]] = {}
        for row in reader:
            key = row[key_column]
            if None in row or not key or key in records:
                raise ValueError(f"Malformed or repeated index key in {path}")
            if any(value is None for value in row.values()):
                raise ValueError(f"Malformed index row in {path}")
            records[key] = row
    return list(header), records


def _contract(
    path: Path,
    targets: Path,
    taxonomy: Path,
    ox_taxonomy: Path,
) -> tuple[str, int, dict]:
    contract = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(contract, dict) or contract.get("schema") != CONTRACT_SCHEMA:
        raise ValueError("Unrecognized scan-equivalence contract")
    scanner = contract.get("species_wide")
    reference = contract.get("reference")
    if not isinstance(scanner, dict) or not isinstance(reference, dict):
        raise ValueError("Missing scan-equivalence policy")
    if scanner.get("taxonomy_policy") != "species_wide":
        raise ValueError("Expected a species-wide source scan")
    policy = reference.get("taxonomy_policy")
    if policy not in {"rank_aware", "strict_taxon"}:
        raise ValueError("Unexpected reference taxonomy policy")
    for key in SCAN_FIELDS:
        if key not in scanner or scanner[key] != reference.get(key):
            raise ValueError(f"Non-equivalent scanner setting: {key}")
    for name in ("k", "max_required_shared"):
        number = scanner[name]
        if not isinstance(number, int) or isinstance(number, bool) or number < 1:
            raise ValueError(f"Invalid scan parameter: {name}")
    fraction = scanner["min_target_kmer_fraction"]
    if (
        not isinstance(fraction, (int, float))
        or isinstance(fraction, bool)
        or not 0 <= fraction <= 1
    ):
        raise ValueError("Invalid scanner k-mer fraction")
    if scanner["fragment_policy"] != "exclude":
        raise ValueError("Sequence-only fragment policy must be exclude")
    for key in SCAN_FIELDS[-4:]:
        digest = scanner[key]
        if not isinstance(digest, str) or len(digest) != 64:
            raise ValueError(f"Invalid pinned digest for {key}")
        if any(char not in "0123456789abcdef" for char in digest):
            raise ValueError(f"Invalid pinned digest for {key}")
    for key, source in (
        ("target_input_sha256", targets),
        ("target_taxonomy_sha256", taxonomy),
        ("ox_taxonomy_sha256", ox_taxonomy),
    ):
        if _digest(source) != scanner[key]:
            raise ValueError(f"Upstream input changed: {key}")
    return policy, scanner["k"], contract


def _seen(db: sqlite3.Connection, channel: str, row: dict) -> None:
    _positive(row["rid"], "RID")
    try:
        db.execute(
            "INSERT INTO seen VALUES (?, ?, ?)",
            (channel, row["target_id"], row["rid"]),
        )
    except sqlite3.IntegrityError as exc:
        raise ValueError(f"Repeated {channel} target/RID") from exc


def _primary_reference_bucket(
    target: dict[str, str], policy: str
) -> str:
    tax = target["resolved_taxid"]
    species = target["species_taxid"]
    if not tax or not species:
        if target["primary_bucket"]:
            raise ValueError("Unresolved target has a primary bucket")
        return ""
    _positive(tax, "target TaxID")
    _positive(species, "target species TaxID")
    expected = {f"T:{tax}", f"S:{species}"}
    primary = target["primary_bucket"]
    if primary and primary not in expected:
        raise ValueError("Target primary bucket contradicts taxonomy")
    if policy == "strict_taxon":
        return f"T:{tax}"
    return primary


def _open_writer(path: Path, fields: list[str] | tuple[str, ...]):
    stream = path.open("x", encoding="utf-8", newline="")
    writer = csv.DictWriter(
        stream, fieldnames=fields, delimiter="\t",
        lineterminator="\n", extrasaction="raise",
    )
    writer.writeheader()
    return stream, writer


def plan_reference_rescue(
    *,
    release: str,
    scan_contract: Path,
    targets_path: Path,
    target_taxonomy_path: Path,
    ox_taxonomy_path: Path,
    go_pairs_path: Path,
    lineage_path: Path,
    direct_anchors_path: Path,
    output_dir: Path,
) -> dict:
    """Select old-05B-equivalent rescue targets from shared V2 inputs.

    The caller must separately attest that scanner manifests share the
    declared GO-positive FASTA and k-mer algorithms. No historic 05A read.
    """
    if not release or not output_dir.parent.is_dir():
        raise ValueError("Invalid release or output directory parent")
    if output_dir.exists() or output_dir.is_symlink():
        raise FileExistsError("Rescue reference publication already exists")
    sources = {
        "scan_contract": scan_contract,
        "targets": targets_path,
        "target_taxonomy": target_taxonomy_path,
        "ox_taxonomy": ox_taxonomy_path,
        "go_pairs": go_pairs_path,
        "lineage": lineage_path,
        "direct_anchors": direct_anchors_path,
    }
    for label, path in sources.items():
        if not path.is_file() or path.is_symlink():
            raise ValueError(f"Missing/unsafe source {label}: {path}")
        if output_dir == path or output_dir in path.parents:
            raise ValueError("Source inside output directory")
    policy, k, contract = _contract(
        scan_contract, targets_path, target_taxonomy_path, ox_taxonomy_path
    )
    target_fields, targets = _indexed(
        targets_path, {"target_id", "sequence_length"}, "target_id"
    )
    taxonomy_fields, taxonomy = _indexed(
        target_taxonomy_path,
        {"target_id", "resolved_taxid", "species_taxid", "primary_bucket"},
        "target_id",
    )
    _, ox_map = _indexed(
        ox_taxonomy_path,
        {"raw_ox", "resolved_taxid", "species_taxid", "species_bucket",
         "resolved_bucket"},
        "raw_ox",
    )
    if targets.keys() != taxonomy.keys():
        raise ValueError("Target and taxonomy domains differ")
    for row in taxonomy.values():
        _primary_reference_bucket(row, policy)
    for row in ox_map.values():
        if row["resolved_taxid"] and row["species_taxid"]:
            _positive(row["resolved_taxid"], "OX resolved TaxID")
            _positive(row["species_taxid"], "OX species TaxID")
            if row["species_bucket"] != f"S:{row['species_taxid']}":
                raise ValueError("Inconsistent OX species bucket")
            if row["resolved_bucket"] != f"T:{row['resolved_taxid']}":
                raise ValueError("Inconsistent OX resolved bucket")
    counts: dict[str, Counter[str]] = {
        name: Counter() for name in ("species", "reference", "lineage", "anchor")
    }
    with tempfile.TemporaryDirectory(
        prefix="probe-b2a-", dir=output_dir.parent
    ) as temporary:
        staged = Path(temporary)
        with sqlite3.connect(str(staged / "seen.sqlite3")) as db:
            db.execute(
                "CREATE TABLE seen (channel TEXT, target TEXT, rid TEXT, "
                "PRIMARY KEY (channel, target, rid))"
            )
            for row in _read(
                go_pairs_path, GO_REQUIRED | frozenset({"target_bucket"})
            ):
                target_id = row["target_id"]
                if target_id not in targets:
                    raise ValueError("GO-positive pair refers to unknown target")
                if row.get("release", release) != release:
                    raise ValueError("GO-positive scanner release mismatch")
                if row["frag"] != "0":
                    raise ValueError("Sequence-only GO-positive fragment")
                _positive(row["record_number"], "GO-positive ordinal")
                _seen(db, "go", row)
                target_tax = taxonomy[target_id]
                ox = ox_map.get(row["raw_ox"])
                if ox is None:
                    raise ValueError("GO-positive OX absent from 02B")
                for name in ("resolved_taxid", "species_taxid"):
                    if row[name] != ox[name]:
                        raise ValueError("GO-positive scanner taxonomy conflict")
                if not ox["species_taxid"]:
                    raise ValueError("GO-positive candidate lacks species TaxID")
                if target_tax["species_taxid"] != ox["species_taxid"]:
                    raise ValueError("Species-wide pair crosses species")
                if row["target_bucket"] != f"S:{ox['species_taxid']}":
                    raise ValueError("Species-wide scanner target bucket invalid")
                counts["species"][target_id] += 1
                bucket = _primary_reference_bucket(target_tax, policy)
                eligible = (
                    ox["species_bucket"] == bucket
                    if bucket.startswith("S:")
                    else ox["resolved_bucket"] == bucket and bool(bucket)
                )
                if eligible:
                    counts["reference"][target_id] += 1
            for row in _read(lineage_path, LINEAGE_REQUIRED):
                if row["release"] != release:
                    raise ValueError("04E release mismatch")
                if row["target_id"] not in targets:
                    raise ValueError("04E contains unknown target")
                _positive(row["record_number"], "04E FULL ordinal")
                _seen(db, "lineage", row)
                counts["lineage"][row["target_id"]] += 1
            required = frozenset({
                "release", "target_id", "rid", "key_source_eligible"
            })
            for row in _read(direct_anchors_path, required):
                if row["release"] != release:
                    raise ValueError("04B release mismatch")
                target_id = row["target_id"]
                if target_id not in targets:
                    raise ValueError("04B contains unknown target")
                if row["key_source_eligible"] not in {"0", "1"}:
                    raise ValueError("Invalid 04B eligibility flag")
                if row["key_source_eligible"] == "1":
                    _positive(row["rid"], "04B RID")
                    if not counts["lineage"][target_id]:
                        raise ValueError("Eligible 04B anchor missing in 04E")
                    counts["anchor"][target_id] += 1
        coverage_file, coverage = _open_writer(
            staged / COVERAGE_NAME, COVERAGE_FIELDS
        )
        target_file, rescue_targets = _open_writer(
            staged / TARGETS_NAME, target_fields
        )
        tax_file, rescue_tax = _open_writer(
            staged / TAXONOMY_NAME, taxonomy_fields
        )
        stats: Counter[str] = Counter()
        with coverage_file, target_file, tax_file, (
            staged / IDS_NAME
        ).open("x", encoding="utf-8", newline="\n") as ids:
            for target_id, target in sorted(targets.items()):
                length = _positive(target["sequence_length"], "target length")
                covered = bool(
                    counts["reference"][target_id]
                    or counts["lineage"][target_id]
                )
                searchable = length >= k
                if covered:
                    reason = "REFERENCE_COVERED"
                elif searchable:
                    reason = "UNCOVERED_K_SEARCHABLE"
                    rescue_targets.writerow(target)
                    rescue_tax.writerow(taxonomy[target_id])
                    ids.write(target_id + "\n")
                    stats["selected"] += 1
                else:
                    reason = "UNCOVERED_LEN_LT_K"
                coverage.writerow({
                    "release": release,
                    "target_id": target_id,
                    "sequence_length": str(length),
                    "kmer_sequence_searchable": str(int(searchable)),
                    "species_wide_pairs": counts["species"][target_id],
                    "reference_go_pairs": counts["reference"][target_id],
                    "lineage_pairs": counts["lineage"][target_id],
                    "eligible_direct_anchor_rows": counts["anchor"][target_id],
                    "reference_covered": str(int(covered)),
                    "selection_reason": reason,
                })
                stats[reason] += 1
                stats["targets"] += 1
        (staged / "seen.sqlite3").unlink()
        summary = {
            "schema": SCHEMA,
            "release": release,
            "reference_policy": policy,
            "coverage_equivalence": "CONDITIONAL_MANIFEST_ATTESTATION_REQUIRED",
            "scanner_contract_schema": contract["schema"],
            "scan_parameters": contract,
            "counts": dict(sorted(stats.items())),
            "source_sha256": {
                name: _digest(path) for name, path in sources.items()
            },
            "output_sha256": {
                name: _digest(staged / name)
                for name in (
                    COVERAGE_NAME, TARGETS_NAME, TAXONOMY_NAME, IDS_NAME
                )
            },
        }
        with (staged / SUMMARY_NAME).open(
            "x", encoding="utf-8", newline="\n"
        ) as stream:
            json.dump(summary, stream, sort_keys=True, indent=2)
            stream.write("\n")
        if output_dir.exists() or output_dir.is_symlink():
            raise FileExistsError("Rescue reference output created concurrently")
        os.rename(staged, output_dir)
    return summary
