"""Release-local candidate provenance: no cross-release pair aggregation."""

from __future__ import annotations

import csv
import hashlib
import json
from pathlib import Path

import pytest
from test_canonical_v2_stage05_06a import _fixture as b3_fixture

from probe.canonical_v2 import documentary_handoff
from probe.canonical_v2.release_candidate_boundary import (
    EVIDENCE_OUTPUT,
    ROWS,
    SCHEMA,
    SUMMARY,
    prepare_release_candidate_boundary,
)
from probe.canonical_v2.stage05_06a import build_stage05_06a


def _write(path: Path, fields: tuple[str, ...], rows: list[dict]) -> None:
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(
            stream, fieldnames=fields, delimiter="\t",
            lineterminator="\n", extrasaction="ignore",
        )
        writer.writeheader()
        writer.writerows(rows)


def _read(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as stream:
        return list(csv.DictReader(stream, delimiter="\t"))


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _setup(tmp_path: Path) -> dict:
    args = b3_fixture(tmp_path)
    build_stage05_06a(**args)
    handoff = tmp_path / "handoff"
    handoff.mkdir()
    sha = hashlib.sha256(b"ACDF").hexdigest()
    candidate = dict.fromkeys(documentary_handoff.CANDIDATE_FIELDS, "")
    candidate.update({
        "release": "R1", "target_id": "T1", "rid": "2",
        "candidate_accession": "A2", "candidate_raw_ox": "111",
        "candidate_resolved_taxid": "111", "target_resolved_taxid": "111",
        "candidate_frag": "0", "candidate_sequence_sha256": sha,
        "evidence_observations": "1", "literal_matches": "1",
        "normalized_hints": "0", "potential_unique_locus_matches": "0",
        "potential_dp_input": "0", "go_donor_authorized": "0",
        "sources_json": '["EXACT"]', "origin_releases_json": '["R2"]',
    })
    extra = {**candidate, "rid": "5", "candidate_accession": "A5"}
    _write(
        handoff / documentary_handoff.CANDIDATES,
        documentary_handoff.CANDIDATE_FIELDS, [candidate, extra],
    )
    evidence = dict.fromkeys(documentary_handoff.EVIDENCE_FIELDS, "")
    evidence.update({
        "release": "R1", "target_id": "T1", "candidate_rid": "2",
        "candidate_accession": "A2", "candidate_raw_ox": "111",
        "candidate_resolved_taxid": "111", "target_resolved_taxid": "111",
        "candidate_frag": "0", "candidate_sequence_sha256": sha,
        "source_kind": "EXACT", "exact_source_rid": "1",
        "exact_source_tax_scope": "SAME_TAXID",
        "source_key_type": "GENE_NAME", "source_key_value": "LOC1",
        "candidate_key_type": "GENE_NAME",
        "candidate_key_value": "LOC1", "match_class": "LITERAL",
        "exact_origin_release": "R2", "potential_unique_locus": "0",
    })
    more_evidence = {
        **evidence, "candidate_rid": "5", "candidate_accession": "A5",
    }
    _write(
        handoff / documentary_handoff.EVIDENCE,
        documentary_handoff.EVIDENCE_FIELDS, [evidence, more_evidence],
    )
    (handoff / documentary_handoff.SUMMARY).write_text(json.dumps({
        "schema": documentary_handoff.SCHEMA, "releases": args["releases"],
        "go_donor_decisions": 0, "dp_authorized": False,
        "dp_executed": False,
        "output_sha256": {
            documentary_handoff.CANDIDATES: _sha(
                handoff / documentary_handoff.CANDIDATES
            ),
            documentary_handoff.EVIDENCE: _sha(
                handoff / documentary_handoff.EVIDENCE
            ),
        },
    }), encoding="utf-8")
    return {
        "b3": args["output_dir"], "handoff": handoff,
        "releases": args["releases"],
        "destination": tmp_path / "boundary",
    }


def test_release_local_join_preserves_overlaps_and_new_candidates(
    tmp_path: Path,
) -> None:
    args = _setup(tmp_path)
    summary = prepare_release_candidate_boundary(**args)
    start = _read(args["destination"] / ROWS["START"])
    end = _read(args["destination"] / ROWS["END"])
    assert len(start) == 4
    assert len(end) == 1
    overlap = next(row for row in start if row["rid"] == "2")
    assert overlap["primary_present"] == "1"
    assert overlap["documentary_present"] == "1"
    assert overlap["exact_origin_releases_json"] == '["R2"]'
    new = next(row for row in start if row["rid"] == "5")
    assert new["primary_present"] == "0"
    assert new["needs_full_materialization"] == "1"
    assert new["go_donor_authorized"] == "0"
    assert all(row["release"] == "R1" for row in start)
    assert all(row["release"] == "R2" for row in end)
    assert len(_read(args["destination"] / EVIDENCE_OUTPUT)) == 2
    assert summary["schema"] == SCHEMA
    assert summary["go_donor_decisions"] == 0
    assert summary["dp_executed"] is False
    assert json.loads((args["destination"] / SUMMARY).read_text()) == summary


def test_overlap_sequence_conflict_fails_closed(tmp_path: Path) -> None:
    args = _setup(tmp_path)
    handoff = args["handoff"]
    source = handoff / documentary_handoff.CANDIDATES
    rows = _read(source)
    rows[0]["candidate_sequence_sha256"] = "a" * 64
    _write(source, documentary_handoff.CANDIDATE_FIELDS, rows)
    # Also alter the associated observation, to expose FULL overlap checking.
    evidence_path = handoff / documentary_handoff.EVIDENCE
    observations = _read(evidence_path)
    observations[0]["candidate_sequence_sha256"] = "a" * 64
    _write(evidence_path, documentary_handoff.EVIDENCE_FIELDS, observations)
    summary_path = handoff / documentary_handoff.SUMMARY
    info = json.loads(summary_path.read_text())
    info["output_sha256"] = {
        documentary_handoff.CANDIDATES: _sha(source),
        documentary_handoff.EVIDENCE: _sha(evidence_path),
    }
    summary_path.write_text(json.dumps(info))
    with pytest.raises(ValueError, match="FULL sequence conflict"):
        prepare_release_candidate_boundary(**args)
    assert not args["destination"].exists()


def test_handoff_tamper_fails_closed(tmp_path: Path) -> None:
    args = _setup(tmp_path)
    path = args["handoff"] / documentary_handoff.EVIDENCE
    path.write_bytes(path.read_bytes() + b"tampered\n")
    with pytest.raises(ValueError, match="SHA256"):
        prepare_release_candidate_boundary(**args)
    assert not args["destination"].exists()


def test_cross_taxid_documentary_rejected(tmp_path: Path) -> None:
    args = _setup(tmp_path)
    handoff = args["handoff"]
    path = handoff / documentary_handoff.CANDIDATES
    rows = _read(path)
    rows[0]["candidate_resolved_taxid"] = "112"
    _write(path, documentary_handoff.CANDIDATE_FIELDS, rows)
    summary_path = handoff / documentary_handoff.SUMMARY
    info = json.loads(summary_path.read_text())
    info["output_sha256"][documentary_handoff.CANDIDATES] = _sha(path)
    summary_path.write_text(json.dumps(info))
    with pytest.raises(ValueError, match="Cross-TaxID"):
        prepare_release_candidate_boundary(**args)
    assert not args["destination"].exists()


def test_no_implicit_doc_donation(tmp_path: Path) -> None:
    args = _setup(tmp_path)
    path = args["handoff"] / documentary_handoff.CANDIDATES
    rows = _read(path)
    rows[1]["go_donor_authorized"] = "1"
    _write(path, documentary_handoff.CANDIDATE_FIELDS, rows)
    summary_path = args["handoff"] / documentary_handoff.SUMMARY
    info = json.loads(summary_path.read_text())
    info["output_sha256"][documentary_handoff.CANDIDATES] = _sha(path)
    summary_path.write_text(json.dumps(info))
    with pytest.raises(ValueError, match="authorize GO donation"):
        prepare_release_candidate_boundary(**args)
    assert not args["destination"].exists()
