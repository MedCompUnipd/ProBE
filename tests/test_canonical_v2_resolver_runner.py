"""Canonical V2 produces native Stage 07 inputs without fake legacy facts."""

from __future__ import annotations

import csv
from pathlib import Path

import pytest

from probe.canonical_v2.resolver_runner import (
    _sequence_proofs,
    _skip_keys,
    load_v2_candidate_universe,
)
from probe.resolver_candidates import CandidateRecord

PRIMARY = (
    "release", "target_id", "rid", "candidate_primary_accession",
    "raw_ox", "resolved_taxid", "species_taxid", "frag",
    "target_primary_bucket", "discovery_channel", "sequence_discovered",
    "go_positive_sequence_discovered", "full_v2_rescue_sequence_discovered",
    "lineage_discovered", "lin_distinct_lineage_key_count",
    "lin_key_collision_present",
)
AUDIT = (
    "release", "target_id", "rid", "target_raw_taxid",
    "target_optional_accession", "record_already_in_primary_union_for_target",
    "candidate_primary_accession", "raw_ox", "anchor_resolved_taxid",
    "audit_class", "audit_alignment_allowed", "key_source_reason",
    "direct_match_type", "direct_match_accession", "sec",
)
TARGET = ("target_id", "raw_taxid", "optional_accession", "normalized_sequence")


def _write(path: Path, fields: tuple[str, ...], rows: list[dict]) -> None:
    with path.open("w", encoding="utf-8", newline="") as output:
        writer = csv.DictWriter(
            output, fieldnames=fields, delimiter="\t", lineterminator="\n",
        )
        writer.writeheader()
        writer.writerows(rows)


def _fixture(tmp_path: Path):
    releases = {"START": "R1", "END": "R2"}
    targets = tmp_path / "targets.tsv"
    primary = tmp_path / "primary.tsv"
    audit = tmp_path / "audit.tsv"
    _write(targets, TARGET, [{
        "target_id": "T", "raw_taxid": "111", "optional_accession": "",
        "normalized_sequence": "AAAA",
    }])
    _write(primary, PRIMARY, [{
        "release": "R1", "target_id": "T", "rid": "5",
        "candidate_primary_accession": "X5", "raw_ox": "111",
        "resolved_taxid": "111", "species_taxid": "100", "frag": "0",
        "target_primary_bucket": "111",
        "discovery_channel": "CANONICAL_DOCUMENTARY_V2",
        "sequence_discovered": "0", "go_positive_sequence_discovered": "0",
        "full_v2_rescue_sequence_discovered": "0",
        "lineage_discovered": "0", "lin_distinct_lineage_key_count": "",
        "lin_key_collision_present": "",
    }])
    _write(audit, AUDIT, [])
    return {
        "targets_path": targets,
        "primary_path": primary,
        "audit_path": audit,
        "releases": releases,
    }


def test_documentary_only_candidate_is_preserved(tmp_path: Path) -> None:
    source = _fixture(tmp_path)
    candidates, old_flags = load_v2_candidate_universe(**source)
    candidate = candidates[("R1", "T", "5")]
    assert isinstance(candidate, CandidateRecord)
    assert not candidate.sequence_discovered
    assert not candidate.lineage_discovered
    assert candidate.discovery_channel == "CANONICAL_DOCUMENTARY_V2"
    assert old_flags[("R1", "T", "5")] == (False, 0, False)


def test_duplicate_candidate_is_rejected(tmp_path: Path) -> None:
    source = _fixture(tmp_path)
    with source["primary_path"].open("a", encoding="utf-8") as output:
        output.write(source["primary_path"].read_text().splitlines()[1] + "\n")
    with pytest.raises(ValueError, match="Duplicate canonical primary"):
        load_v2_candidate_universe(**source)


def test_wrong_release_is_rejected(tmp_path: Path) -> None:
    source = _fixture(tmp_path)
    original = source["primary_path"].read_text()
    source["primary_path"].write_text(original.replace("R1", "R3"))
    with pytest.raises(ValueError, match="Invalid V2 primary"):
        load_v2_candidate_universe(**source)


def test_policy_skip_is_explicit_and_taxid_guarded(tmp_path: Path) -> None:
    path = tmp_path / "skipped.tsv"
    cols = ("release", "target_id", "rid", "reason")
    _write(path, cols, [{
        "release": "R1", "target_id": "T", "rid": "5",
        "reason": "CROSS_TAXID_NONEXACT",
    }])
    assert _skip_keys(path, {"START": "R1", "END": "R2"}) == {
        ("R1", "T", "5")
    }
    _write(path, cols, [{
        "release": "R1", "target_id": "T", "rid": "5",
        "reason": "NOT_A_POLICY_REASON",
    }])
    with pytest.raises(ValueError, match="Invalid policy-skipped"):
        _skip_keys(path, {"START": "R1", "END": "R2"})


def test_sequences_are_not_shared_across_releases(tmp_path: Path) -> None:
    source = _fixture(tmp_path)
    stores = {}
    for release in ("R1", "R2"):
        path = tmp_path / f"{release}.tsv"
        _write(path, ("release", "rid", "sequence"), [{
            "release": release, "rid": "5",
            "sequence": "AAAA" if release == "R1" else "BBBB",
        }])
        stores[release] = path
    proof = _sequence_proofs(
        wanted={("R1", "T", "5"), ("R2", "T", "5")},
        targets_path=source["targets_path"], stores=stores,
    )
    assert proof[("R1", "T", "5")] == ("AAAA", "AAAA")
    assert proof[("R2", "T", "5")] == ("AAAA", "BBBB")
