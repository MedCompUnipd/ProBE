"""Release-local candidate handoff from shared cross-release lookup anchors."""

from __future__ import annotations

import csv
import hashlib
import json
from pathlib import Path

import pytest
from test_canonical_v2_cross_release_lookup import _end_register, _fixture

from probe.canonical_v2.documentary_handoff import (
    CANDIDATES,
    EVIDENCE,
    SUMMARY,
    prepare_documentary_handoff,
)
from probe.canonical_v2.documentary_lookup import (
    OUTPUT as LOOKUP_OUTPUT,
)
from probe.canonical_v2.documentary_lookup import (
    SUMMARY as LOOKUP_SUMMARY,
)
from probe.canonical_v2.documentary_lookup import discover_documentary_nonexact


def _read(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as stream:
        return list(csv.DictReader(stream, delimiter="\t"))


def _prepare(tmp_path: Path) -> dict:
    start = _fixture(tmp_path)
    end_register, end_index = _end_register(
        tmp_path / "end", start["targets"]
    )
    start["other_exact_register"] = end_register
    start["other_release"] = "R2"
    discover_documentary_nonexact(**start)
    end_directory = tmp_path / "end" / "matches"
    discover_documentary_nonexact(
        release="R2",
        targets=start["targets"],
        target_taxonomy=start["target_taxonomy"],
        exact_register=end_register,
        other_exact_register=start["exact_register"],
        other_release="R1",
        independent_index=end_index,
        ox_taxonomy=start["ox_taxonomy"],
        full_manifest=tmp_path / "end" / "full" / "manifest.json",
        destination=end_directory,
    )
    return {
        "start_lookup": start["destination"],
        "end_lookup": end_directory,
        "start_release": "R1",
        "end_release": "R2",
        "destination": tmp_path / "handoff",
    }


def test_two_releases_separated_and_sources_preserved(tmp_path: Path) -> None:
    paths = _prepare(tmp_path)
    summary = prepare_documentary_handoff(**paths)
    rows = _read(paths["destination"] / CANDIDATES)
    evidence = _read(paths["destination"] / EVIDENCE)
    assert {row["release"] for row in rows} == {"R1", "R2"}
    assert {(row["release"], row["target_id"], row["rid"])
            for row in rows} == {
        (row["release"], row["target_id"], row["candidate_rid"])
        for row in evidence
    }
    assert len(rows) < len(evidence)
    assert all(row["go_donor_authorized"] == "0" for row in rows)
    assert all(row["candidate_resolved_taxid"]
               == row["target_resolved_taxid"] for row in rows)
    assert summary["dp_executed"] is False
    assert summary["dp_authorized"] is False
    assert json.loads((paths["destination"] / SUMMARY).read_text()) == summary


def test_end_origin_lookup_stays_start_release(tmp_path: Path) -> None:
    paths = _prepare(tmp_path)
    prepare_documentary_handoff(**paths)
    evidence = _read(paths["destination"] / EVIDENCE)
    assert any(
        row["release"] == "R1"
        and row["exact_origin_release"] == "R2"
        for row in evidence
    )
    assert all(row["release"] in {"R1", "R2"} for row in evidence)


def test_normalized_hints_never_claim_unique_locus(tmp_path: Path) -> None:
    paths = _prepare(tmp_path)
    prepare_documentary_handoff(**paths)
    evidence = _read(paths["destination"] / EVIDENCE)
    hints = [row for row in evidence if row["match_class"] == "NORMALIZED_HINT"]
    assert hints
    assert all(row["potential_unique_locus"] == "0" for row in hints)


def test_unique_literal_locus_is_only_potential_dp(tmp_path: Path) -> None:
    paths = _prepare(tmp_path)
    prepare_documentary_handoff(**paths)
    rows = _read(paths["destination"] / CANDIDATES)
    supported = [row for row in rows if row["potential_dp_input"] == "1"]
    assert supported
    assert all(row["go_donor_authorized"] == "0" for row in supported)


def test_no_exact_still_has_independent_documentary_input(tmp_path: Path) -> None:
    paths = _prepare(tmp_path)
    prepare_documentary_handoff(**paths)
    rows = _read(paths["destination"] / CANDIDATES)
    evidence = _read(paths["destination"] / EVIDENCE)
    assert any(row["target_id"] == "NO" for row in rows)
    assert any(
        row["target_id"] == "NO"
        and row["source_kind"] == "INDEPENDENT_04D1"
        for row in evidence
    )


def test_tampered_source_sha_rejected(tmp_path: Path) -> None:
    paths = _prepare(tmp_path)
    data = paths["start_lookup"] / LOOKUP_OUTPUT
    data.write_bytes(data.read_bytes() + b"corrupted\n")
    with pytest.raises(ValueError, match="SHA256"):
        prepare_documentary_handoff(**paths)
    assert not paths["destination"].exists()


def test_cross_taxid_is_rejected_not_silently_skipped(tmp_path: Path) -> None:
    paths = _prepare(tmp_path)
    payload = paths["start_lookup"] / LOOKUP_OUTPUT
    rows = _read(payload)
    rows[0]["candidate_resolved_taxid"] = "999"
    with payload.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]), delimiter="\t")
        writer.writeheader()
        writer.writerows(rows)
    summary = paths["start_lookup"] / LOOKUP_SUMMARY
    data = json.loads(summary.read_text())
    data["output_sha256"][LOOKUP_OUTPUT] = hashlib.sha256(
        payload.read_bytes()
    ).hexdigest()
    summary.write_text(json.dumps(data))
    with pytest.raises(ValueError, match="Cross-TaxID"):
        prepare_documentary_handoff(**paths)
    assert not paths["destination"].exists()


def test_output_is_immutable(tmp_path: Path) -> None:
    paths = _prepare(tmp_path)
    prepare_documentary_handoff(**paths)
    with pytest.raises(FileExistsError, match="occupied"):
        prepare_documentary_handoff(**paths)
