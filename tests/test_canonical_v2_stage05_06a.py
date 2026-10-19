"""Synthetic cold-start tests: no historical 05A2/05C2/05D/06C1."""

from __future__ import annotations

import csv
import hashlib
import json
import os
from pathlib import Path

import pytest

from probe.canonical_v2.primary_projection import (
    OUTPUT_FIELDS,
    OUTPUT_NAME,
    SCHEMA,
    SUMMARY_NAME,
)
from probe.canonical_v2.stage05_06a import (
    AUDIT,
    JOBS,
    PRIMARY,
    STORES,
    build_stage05_06a,
)
from probe.resolver_candidates import _primary
from probe.workflow.artifacts import Artifact, StageManifest

ROOT = Path(os.environ.get(
    "PROBE_B3_REPO", Path(__file__).resolve().parents[1]
))


def _tsv(path: Path, fields: list[str], rows: list[dict]) -> None:
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


def _record(
    release: str, rid: int, *, lineage: bool = False,
    full: bool = False, exact: bool = False,
) -> dict[str, str]:
    row = dict.fromkeys(OUTPUT_FIELDS, "")
    row.update({
        "release": release, "target_id": "T1", "rid": str(rid),
        "discovery_channel": "LINEAGE_ONLY" if lineage else "SEQUENCE_ONLY",
        "sequence_discovered": "0" if lineage else "1",
        "go_positive_sequence_discovered": str(int(not lineage and not full)),
        "full_v2_rescue_sequence_discovered": "0",
        "lineage_discovered": str(int(lineage)),
        "full_exact_species_wide": str(int(full)),
        "full_ordinal_state": "CONSISTENT",
        "full_v2_record_number": str(rid),
        "candidate_primary_accession": f"A{rid}",
        "raw_ox": "111", "resolved_taxid": "111",
        "species_taxid": "100", "frag": "0",
        "entry": f"ENTRY{rid}", "sequence_length": "4",
        "pe": "1", "sv": "2", "sec": "B55", "gene": "abc",
        "seq_exact_sequence": str(int(exact)) if not lineage else "",
        "lin_distinct_lineage_key_count": "1" if lineage else "",
        "lin_key_collision_present": "0" if lineage else "",
        "strict_gate": "1", "same_species_exact_gate": "1",
    })
    return row


def _full_manifest(directory: Path, release: str, role: str,
                   sequences: list[str]) -> Path:
    directory.mkdir()
    fasta = directory / "01A_full_v2.fasta"
    with fasta.open("w", encoding="ascii") as stream:
        for rid, seq in enumerate(sequences, 1):
            stream.write(
                f">sp|A{rid}|ENTRY{rid} RID={rid} SRC=1 REC={rid} "
                f"LINE=11 OX={112 if rid == 4 else 111} "
                f"FRAG=0 LEN=4 PE=1 SV=2 "
                f"SEC=B55 GENE=abc\n{seq}\n"
            )
    digest = hashlib.sha256(fasta.read_bytes()).hexdigest()
    StageManifest(
        stage="01A_FULL", role=role, release_id=release,
        parameters={}, ontology=None,
        implementation={"name": "synthetic", "version": "1"},
        inputs=(),
        outputs=(Artifact("01A_full_v2.fasta", fasta.stat().st_size, digest),),
    ).write(directory / "manifest.json")
    return directory / "manifest.json"


def _fixture(tmp_path: Path, *, audit: bool = False) -> dict:
    releases = {"START": "R1", "END": "R2"}
    projections = {}
    taxonomies = {}
    anchors = {}
    manifests = {}
    for role, release in releases.items():
        source = tmp_path / f"b2c_{role}"
        source.mkdir()
        rows = (
            [_record("R1", 1, exact=True),
             _record("R1", 2, lineage=True),
             _record("R1", 3, full=True, exact=True)]
            if role == "START" else [_record("R2", 1, exact=True)]
        )
        _tsv(source / OUTPUT_NAME, list(OUTPUT_FIELDS), rows)
        sha = hashlib.sha256((source / OUTPUT_NAME).read_bytes()).hexdigest()
        (source / SUMMARY_NAME).write_text(json.dumps({
            "schema": SCHEMA, "release": release,
            "ready_for_06a": False,
            "counts": {"unique_pairs": len(rows)},
            "output_sha256": {OUTPUT_NAME: sha},
        }), encoding="utf-8")
        projections[role] = source
        taxonomy = tmp_path / f"taxonomy_{role}.tsv"
        _tsv(taxonomy, ["target_id", "resolved_taxid", "primary_bucket"], [
            {"target_id": "T1", "resolved_taxid": "111",
             "primary_bucket": "111"},
        ])
        taxonomies[role] = taxonomy
        anchor = tmp_path / f"anchors_{role}.tsv"
        anchor_fields = [
            "release", "target_id", "target_optional_accession",
            "direct_match_type", "direct_match_accession",
            "primary_accession", "entry", "rid", "raw_ox", "frag",
            "sequence_length", "pe", "sv", "sec", "gene",
            "target_raw_taxid", "target_resolved_taxid",
            "target_resolved_rank", "target_species_taxid",
            "target_primary_bucket", "anchor_resolved_taxid",
            "anchor_resolved_rank", "anchor_species_taxid",
            "taxonomy_relation", "key_source_eligible", "key_source_reason",
        ]
        anchor_rows = []
        if audit and role == "START":
            anchor_rows.append({
                "release": release, "target_id": "T1",
                "target_optional_accession": "B55",
                "direct_match_type": "SECONDARY",
                "direct_match_accession": "B55",
                "primary_accession": "A4", "entry": "ENTRY4",
                "rid": "4", "raw_ox": "112", "frag": "0",
                "sequence_length": "4", "pe": "1", "sv": "2",
                "sec": "B55", "gene": "abc",
                "target_raw_taxid": "111", "target_resolved_taxid": "111",
                "target_resolved_rank": "strain", "target_species_taxid": "100",
                "target_primary_bucket": "111",
                "anchor_resolved_taxid": "112",
                "anchor_resolved_rank": "strain",
                "anchor_species_taxid": "100",
                "taxonomy_relation": "SAME_SPECIES_WRONG_SUBTAXON",
                "key_source_eligible": "0",
                "key_source_reason": "SAME_SPECIES_WRONG_SUBTAXON",
            })
        _tsv(anchor, anchor_fields, anchor_rows)
        anchors[role] = anchor
        sequences = ("ACDE", "ACDF", "ACDE", "ACDF") if role == "START" else ("ACDE",)
        manifests[role] = _full_manifest(
            tmp_path / f"full_{role}", release, role, sequences
        )
    targets = tmp_path / "targets.tsv"
    _tsv(
        targets,
        ["target_id", "optional_accession", "normalized_sequence",
         "sequence_length"],
        [{"target_id": "T1", "optional_accession": "A1",
          "normalized_sequence": "ACDE", "sequence_length": "4"}],
    )
    return {
        "releases": releases, "projections": projections,
        "taxonomies": taxonomies, "anchors": anchors,
        "full_manifests": manifests, "targets": targets,
        "stage05c2_cli": (
            ROOT / "scripts/pipeline/05C2_freeze_taxonomy_audit_channel.py"
        ),
        "stage06a_cli": ROOT / "scripts/pipeline/06A_prepare_alignment_jobs.py",
        "output_dir": tmp_path / "canonical05_06a",
    }


def test_cold_start_from_b2c_without_legacy(tmp_path: Path) -> None:
    args = _fixture(tmp_path)
    summary = build_stage05_06a(**args)
    output = args["output_dir"]
    primary = _read(output / PRIMARY)
    assert len(primary) == 4
    assert primary[2]["discovery_channel"] == "STAGE2C_FULL_EXACT"
    assert primary[1]["lineage_discovered"] == "1"
    assert primary[1]["seq_exact_sequence"] == ""
    for row in primary:
        candidate = _primary(row, "synthetic", "A1")
        assert candidate.rid == row["rid"]
    jobs = _read(output / JOBS)
    assert len(jobs) == 4
    assert [row["exact_sequence"] for row in jobs] == ["1", "0", "1", "1"]
    assert len(_read(output / STORES["START"])) == 3
    assert _read(output / AUDIT) == []
    assert summary["go_donor_decisions"] == 0
    assert summary["dp_executions"] == 0
    assert summary["historical_05_07_inputs"] == 0
    with pytest.raises(ValueError, match="occupied"):
        build_stage05_06a(**args)


def test_corrupt_pinned_fasta_fails_closed(tmp_path: Path) -> None:
    args = _fixture(tmp_path)
    fasta = args["full_manifests"]["START"].parent / "01A_full_v2.fasta"
    fasta.write_bytes(fasta.read_bytes().replace(b"ACDF", b"ACDE"))
    with pytest.raises(ValueError, match="FULL FASTA digest"):
        build_stage05_06a(**args)
    assert not args["output_dir"].exists()


def test_provenance_cannot_disappear(tmp_path: Path) -> None:
    args = _fixture(tmp_path)
    facts = args["projections"]["START"] / OUTPUT_NAME
    rows = _read(facts)
    rows.pop(1)
    _tsv(facts.with_suffix(".new"), list(OUTPUT_FIELDS), rows)
    facts.write_bytes(facts.with_suffix(".new").read_bytes())
    facts.with_suffix(".new").unlink()
    with pytest.raises(ValueError, match="Unverified B2c"):
        build_stage05_06a(**args)
    assert not args["output_dir"].exists()


def test_independent_audit_preserved_without_dp(tmp_path: Path) -> None:
    args = _fixture(tmp_path, audit=True)
    summary = build_stage05_06a(**args)
    rows = _read(args["output_dir"] / AUDIT)
    assert len(rows) == 1
    assert rows[0]["audit_alignment_allowed"] == "1"
    assert rows[0]["audit_class"] == "TAXONOMY_RESCUE_AUDIT"
    jobs = _read(args["output_dir"] / JOBS)
    assert len(jobs) == 5
    assert jobs[3]["job_scope"] == "TAXONOMY_AUDIT"
    assert jobs[3]["exact_sequence"] == "0"
    assert len(_read(args["output_dir"] / STORES["START"])) == 4
    assert summary["dp_executions"] == 0
