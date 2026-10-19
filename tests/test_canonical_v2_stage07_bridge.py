"""Canonical Stage 07 bridge tests using real source and manifest schemas."""

from __future__ import annotations

import csv
import hashlib
import json
from pathlib import Path

import pytest

from probe.canonical_v2.stage07_bridge import (
    ACCEPTED,
    SELECTION,
    SOURCE,
    SOURCE_COMPLETE,
    SOURCE_SUMMARY,
    publish_stage07_adapter,
)
from probe.resolver_assembly import STAGE07_COLUMNS
from probe.stage08_collection_inputs import _selection
from probe.workflow.artifacts import validate_bundle


def _source(tmp_path: Path, *, donor: str = "YES") -> Path:
    resolver = tmp_path / "resolver"
    resolver.mkdir()
    row = dict.fromkeys(STAGE07_COLUMNS, ".")
    row.update({
        "release": "R1", "target_id": "T1", "rid": "1",
        "scope": "PRIMARY", "evaluation_scope": "PRIMARY",
        "target_accession": "A1", "candidate_accession": "A1",
        "decision": "ACCEPTED", "annotation_donor": donor,
        "exact_sequence": "1", "decision_basis": "LITERAL_EXACT_SEQUENCE",
        "sequence_relation": "EXACT_SEQUENCE",
        "accepted_candidates_for_target_release": "1",
    })
    for name in (SOURCE, SOURCE_COMPLETE):
        with (resolver / name).open("w", encoding="utf-8", newline="") as stream:
            writer = csv.DictWriter(
                stream, fieldnames=STAGE07_COLUMNS,
                delimiter="\t", lineterminator="\n"
            )
            writer.writeheader()
            writer.writerow(row)
    digests = {
        name: hashlib.sha256((resolver / name).read_bytes()).hexdigest()
        for name in (SOURCE, SOURCE_COMPLETE)
    }
    (resolver / SOURCE_SUMMARY).write_text(
        json.dumps({
            "schema": "stage2c-resolver-variant-v1",
            "strain_policy": "same_species_exact",
            "release_order": ["R1", "R2"],
            "discovery_reexecuted": False,
            "partial_sequence": "allow",
            "documentary_fragment": "allow",
            "decisions": {"ACCEPTED": 1},
            "annotation_donor_yes": int(donor == "YES"),
            "output_sha256": digests,
        }),
        encoding="utf-8",
    )
    return resolver


def test_stage08_contract_consumes_canonical_adapter(tmp_path: Path) -> None:
    source = _source(tmp_path)
    destination = tmp_path / "07"
    result = publish_stage07_adapter(
        resolver=source,
        releases={"START": "R1", "END": "R2"},
        policy="same_species_exact", destination=destination,
    )
    assert result == {
        "accepted": 1, "donors": 1, "policy": "same_species_exact"
    }
    manifest = validate_bundle(destination, deep=True)
    assert set(item.path for item in manifest.outputs) == {ACCEPTED, SELECTION}
    assert _selection(destination / SELECTION, manifest) == ("R1", "R2")


def test_nondonor_not_removed(tmp_path: Path) -> None:
    source = _source(tmp_path, donor="NO")
    destination = tmp_path / "07"
    report = publish_stage07_adapter(
        resolver=source,
        releases={"START": "R1", "END": "R2"},
        policy="same_species_exact", destination=destination,
    )
    assert report["accepted"] == 1
    assert report["donors"] == 0
    assert "\tNO\t" in (destination / ACCEPTED).read_text()


def test_tampered_resolver_rejected(tmp_path: Path) -> None:
    source = _source(tmp_path)
    with (source / SOURCE).open("a", encoding="utf-8") as stream:
        stream.write("broken\n")
    with pytest.raises(ValueError, match="SHA256 mismatch"):
        publish_stage07_adapter(
            resolver=source,
            releases={"START": "R1", "END": "R2"},
            policy="same_species_exact", destination=tmp_path / "07",
        )


def test_no_overwrite(tmp_path: Path) -> None:
    source = _source(tmp_path)
    with pytest.raises(FileExistsError, match="occupied"):
        publish_stage07_adapter(
            resolver=source,
            releases={"START": "R1", "END": "R2"},
            policy="same_species_exact", destination=source,
        )


def test_summary_donor_count_disagreement_rejected(tmp_path: Path) -> None:
    source = _source(tmp_path)
    path = source / SOURCE_SUMMARY
    summary = json.loads(path.read_text(encoding="utf-8"))
    summary["annotation_donor_yes"] = 0
    path.write_text(json.dumps(summary), encoding="utf-8")
    with pytest.raises(ValueError, match="accepted/donor count mismatch"):
        publish_stage07_adapter(
            resolver=source,
            releases={"START": "R1", "END": "R2"},
            policy="same_species_exact", destination=tmp_path / "07",
        )


def test_wrong_policy_rejected(tmp_path: Path) -> None:
    source = _source(tmp_path)
    with pytest.raises(ValueError, match="contract mismatch"):
        publish_stage07_adapter(
            resolver=source,
            releases={"START": "R1", "END": "R2"},
            policy="strict", destination=tmp_path / "07",
        )
