"""Audit the candidate delta between immutable 05A2 and Stage 2C discovery.

This reconciliation never asserts identity, accepted association, or GO donor.
Only the newly discovered keys are emitted for later evidence reconstruction.
"""

from __future__ import annotations

import csv
import json
import sqlite3
import tempfile
from collections import Counter
from pathlib import Path

from probe.stage2c_candidate_union import OUTPUT_COLUMNS

LEGACY_REQUIRED = frozenset({"release", "target_id", "rid"})
DISCOVERY_REQUIRED = frozenset(OUTPUT_COLUMNS)
DELTA_COLUMNS = (*OUTPUT_COLUMNS, "integration_state")
SCHEMA = "stage2c-candidate-delta-v1"


def _rows(path: Path, required: frozenset[str]):
    with path.open(encoding="utf-8", newline="") as stream:
        reader = csv.DictReader(stream, delimiter="\t")
        fields = reader.fieldnames
        if fields is None or len(set(fields)) != len(fields):
            raise ValueError(f"Invalid TSV header: {path}")
        missing = required - set(fields)
        if missing:
            raise ValueError(f"Missing TSV columns in {path}: {sorted(missing)}")
        for line_number, row in enumerate(reader, start=2):
            if None in row or any(value is None for value in row.values()):
                raise ValueError(f"Malformed TSV row: {path}:{line_number}")
            yield row


def _key(row: dict[str, str]) -> tuple[str, str]:
    target = row["target_id"]
    rid = row["rid"]
    if not target or not rid.isascii() or not rid.isdecimal():
        raise ValueError("Invalid candidate key")
    if int(rid) < 1:
        raise ValueError("Invalid RID")
    return target, str(int(rid))


def _check_gate(row: dict[str, str]) -> None:
    for name in (
        "target_resolved_taxid",
        "candidate_resolved_taxid",
        "species_taxid",
    ):
        value = row[name]
        if not value.isascii() or not value.isdecimal() or int(value) < 1:
            raise ValueError(f"Invalid {name}")
    for name in (
        "go_positive_species_wide",
        "full_exact_species_wide",
        "go_positive_exact",
    ):
        if row[name] not in {"0", "1"}:
            raise ValueError(f"Invalid discovery provenance: {name}")
    cross = row["target_resolved_taxid"] != row["candidate_resolved_taxid"]
    full = row["full_exact_species_wide"] == "1"
    exact = full or row["go_positive_exact"] == "1"
    if row["go_positive_species_wide"] == "0" and not full:
        raise ValueError("Candidate without discovery evidence")
    if full and not cross:
        raise ValueError("FULL exact provenance on same-TaxID candidate")
    strict = "DENIED" if cross else "DEFER_TO_DOCUMENTARY"
    exact_gate = (
        "ELIGIBLE" if cross and exact else "DENIED" if cross else "DEFER_TO_DOCUMENTARY"
    )
    if (row["strict_gate"], row["same_species_exact_gate"]) != (
        strict,
        exact_gate,
    ):
        raise ValueError("Stage 2C candidate gates contradict provenance")


def reconcile_candidate_delta(
    *,
    release: str,
    legacy_union: Path,
    discovery_union: Path,
    output: Path,
    summary: Path,
) -> dict:
    """Emit new keys only; existing 05A2 keys remain in their original bundle.

    A successful result is NOT a Stage 05A2 replacement or a GO donor list.
    Downstream reconstruction must preserve lineage and rescue metadata.
    """
    if not release or output == summary:
        raise ValueError("Invalid release or colliding destinations")
    if not output.parent.is_dir() or not summary.parent.is_dir():
        raise ValueError("Missing destination parent")
    if any(path.exists() or path.is_symlink() for path in (output, summary)):
        raise FileExistsError("Refusing to overwrite candidate delta")
    if legacy_union.resolve() == discovery_union.resolve():
        raise ValueError("Legacy and discovery inputs must differ")
    if not legacy_union.is_file() or not discovery_union.is_file():
        raise FileNotFoundError("Missing source candidate TSV")
    counts: Counter[str] = Counter()
    with tempfile.TemporaryDirectory(prefix="probe-2c-delta-") as tmp:
        db = sqlite3.connect(str(Path(tmp) / "keys.db"))
        try:
            db.execute(
                "CREATE TABLE canonical ("
                "target TEXT NOT NULL, rid TEXT NOT NULL, "
                "accession TEXT NOT NULL, candidate_tax TEXT NOT NULL, "
                "PRIMARY KEY (target, rid))"
            )
            for row in _rows(legacy_union, LEGACY_REQUIRED):
                if row["release"] != release:
                    continue
                target, rid = _key(row)
                try:
                    db.execute(
                        "INSERT INTO canonical VALUES (?, ?, ?, ?)",
                        (
                            target,
                            rid,
                            row.get("candidate_primary_accession", ""),
                            row.get("resolved_taxid", ""),
                        ),
                    )
                except sqlite3.IntegrityError as exc:
                    raise ValueError("Duplicate legacy candidate key") from exc
                counts["legacy_candidates"] += 1
            db.execute(
                "CREATE TABLE seen ("
                "target TEXT NOT NULL, rid TEXT NOT NULL, "
                "PRIMARY KEY (target, rid))"
            )
            staged = Path(tmp) / "delta.tsv"
            with staged.open("x", encoding="utf-8", newline="") as stream:
                writer = csv.DictWriter(
                    stream,
                    fieldnames=DELTA_COLUMNS,
                    delimiter="\t",
                    lineterminator="\n",
                    extrasaction="raise",
                )
                writer.writeheader()
                for row in _rows(discovery_union, DISCOVERY_REQUIRED):
                    if row["release"] != release:
                        raise ValueError("Discovery release mismatch")
                    _check_gate(row)
                    target, rid = _key(row)
                    try:
                        db.execute(
                            "INSERT INTO seen VALUES (?, ?)",
                            (target, rid),
                        )
                    except sqlite3.IntegrityError as exc:
                        raise ValueError("Duplicate discovery candidate") from exc
                    previous = db.execute(
                        "SELECT accession, candidate_tax FROM canonical "
                        "WHERE target=? AND rid=?",
                        (target, rid),
                    ).fetchone()
                    if previous is not None:
                        accession, candidate_tax = previous
                        if accession and accession != row["candidate_accession"]:
                            raise ValueError("Conflicting accession on overlap")
                        if (
                            candidate_tax
                            and candidate_tax != row["candidate_resolved_taxid"]
                        ):
                            raise ValueError("Conflicting taxonomy on overlap")
                        counts["already_in_05a2"] += 1
                        continue
                    writer.writerow(
                        {
                            **{name: row[name] for name in OUTPUT_COLUMNS},
                            "integration_state": "NEEDS_EVIDENCE_RECONSTRUCTION",
                        }
                    )
                    counts["new_candidates"] += 1
                    cross = (
                        row["target_resolved_taxid"] != row["candidate_resolved_taxid"]
                    )
                    if cross:
                        counts["new_cross_strain"] += 1
                        if row["same_species_exact_gate"] == "ELIGIBLE":
                            counts["new_cross_strain_exact"] += 1
                        else:
                            counts["new_cross_strain_nonexact"] += 1
                    else:
                        counts["new_same_taxid"] += 1
                    if row["full_exact_species_wide"] == "1":
                        counts["new_from_full_exact"] += 1
            counts["discovery_candidates"] = (
                counts["already_in_05a2"] + counts["new_candidates"]
            )
            result = {
                "schema": SCHEMA,
                "release": release,
                "scientific_decision": "NO_GO_DONOR_DECISION",
                "delta_is_complete_stage05a2": False,
                "counts": dict(sorted(counts.items())),
            }
            staged_summary = Path(tmp) / "summary.json"
            staged_summary.write_text(
                json.dumps(result, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            # No published file is replaced. Verify both names remained free.
            if any(path.exists() or path.is_symlink() for path in (output, summary)):
                raise FileExistsError("Destination occupied during reconciliation")
            # Two named artifacts are intended for a staging bundle: the caller
            # must keep them unpublished until bundle-level validation completes.
            output.write_bytes(staged.read_bytes())
            summary.write_bytes(staged_summary.read_bytes())
            return result
        finally:
            db.close()
