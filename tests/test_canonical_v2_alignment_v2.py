"""Cold-start TaxID DP gate does not depend on legacy canonical 05A2."""

from __future__ import annotations

import csv
import hashlib
import json
import os
from pathlib import Path

import pytest

from probe.canonical_v2.alignment_v2 import (
    execute_alignment_v2,
    select_nonexact_dp,
)
from probe.canonical_v2.documentary_handoff import EVIDENCE_FIELDS
from probe.resolver_lineage import _D1_REQUIRED, _D2_REQUIRED
from probe.stage2c_alignment_completion import FINAL_OUTPUT, SUMMARY
from probe.stage2c_alignment_preview import (
    AUDIT_OUT,
    END_STORE,
    JOBS_OUT,
    START_STORE,
    UNION_OUT,
)

FIELDS = (
    "release",
    "target_id",
    "rid",
    "job_scope",
    "exact_sequence",
    "candidate_primary_accession",
    "raw_ox",
    "discovery_channel",
    "candidate_length",
)
PRIMARY = (
    "release",
    "target_id",
    "rid",
    "candidate_primary_accession",
    "raw_ox",
    "resolved_taxid",
    "sequence_length",
    "discovery_channel",
    "target_primary_bucket",
    "frag",
)
TAXONOMY = (
    "target_id",
    "raw_taxid",
    "resolution_status",
    "resolved_taxid",
    "resolved_rank",
    "species_taxid",
    "primary_bucket",
)
AUDIT = (
    "release",
    "target_id",
    "rid",
    "candidate_primary_accession",
    "raw_ox",
    "anchor_resolved_taxid",
    "audit_alignment_allowed",
    "record_already_in_primary_union_for_target",
    "key_source_reason",
    "audit_class",
)


def _write(path: Path, fields: tuple[str, ...], rows: list[dict]) -> None:
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(
            stream,
            fieldnames=fields,
            delimiter="\t",
            lineterminator="\n",
        )
        writer.writeheader()
        writer.writerows(rows)


def _read(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as stream:
        return list(csv.DictReader(stream, delimiter="\t"))


def _fixture(tmp_path: Path) -> dict:
    releases = {"START": "R1", "END": "R2"}
    taxonomy = {}
    for role in ("START", "END"):
        taxonomy[role] = tmp_path / f"taxonomy_{role}.tsv"
        _write(
            taxonomy[role],
            TAXONOMY,
            [
                {
                    "target_id": "T",
                    "raw_taxid": "111",
                    "resolution_status": "CURRENT",
                    "resolved_taxid": "111",
                    "resolved_rank": "strain",
                    "species_taxid": "100",
                    "primary_bucket": "111",
                }
            ],
        )
    rows = [
        {
            "release": "R1",
            "target_id": "T",
            "rid": "1",
            "job_scope": "PRIMARY",
            "exact_sequence": "0",
            "candidate_primary_accession": "P1",
            "raw_ox": "111",
            "discovery_channel": "CANONICAL_DOCUMENTARY_V2",
            "candidate_length": "5",
        },
        {
            "release": "R1",
            "target_id": "T",
            "rid": "2",
            "job_scope": "PRIMARY",
            "exact_sequence": "0",
            "candidate_primary_accession": "P2",
            "raw_ox": "112",
            "discovery_channel": "SEQUENCE_ONLY",
            "candidate_length": "5",
        },
        {
            "release": "R2",
            "target_id": "T",
            "rid": "1",
            "job_scope": "PRIMARY",
            "exact_sequence": "0",
            "candidate_primary_accession": "Q1",
            "raw_ox": "111",
            "discovery_channel": "SEQUENCE_ONLY",
            "candidate_length": "5",
        },
    ]
    primary = tmp_path / "primary.tsv"
    _write(
        primary,
        PRIMARY,
        [
            {
                **{
                    name: row[name]
                    for name in (
                        "release",
                        "target_id",
                        "rid",
                        "candidate_primary_accession",
                        "raw_ox",
                        "discovery_channel",
                    )
                },
                "resolved_taxid": "112" if row["rid"] == "2" else "111",
                "sequence_length": "5",
                "target_primary_bucket": "111",
                "frag": "0",
            }
            for row in rows
        ],
    )
    jobs = tmp_path / "jobs.tsv"
    _write(jobs, FIELDS, rows)
    audit = tmp_path / "audit.tsv"
    _write(audit, AUDIT, [])
    evidence = dict.fromkeys(EVIDENCE_FIELDS, "")
    evidence.update(
        {
            "release": "R1",
            "target_id": "T",
            "candidate_rid": "1",
            "candidate_accession": "P1",
            "candidate_raw_ox": "111",
            "candidate_resolved_taxid": "111",
            "target_resolved_taxid": "111",
            "candidate_frag": "0",
            "candidate_sequence_sha256": "a" * 64,
            "candidate_gene": "L:LOC_1",
            "candidate_key_origin": "GENE_L",
            "source_key_origin": "GENE_L",
            "source_kind": "EXACT",
            "exact_source_rid": "8",
            "exact_source_tax_scope": "SAME_TAXID",
            "exact_origin_release": "R2",
            "exact_origin_target_taxid": "111",
            "source_roles": "EXACT_VERIFIED",
            "source_key_type": "ORDERED_LOCUS",
            "source_key_value": "LOC_1",
            "candidate_key_type": "ORDERED_LOCUS",
            "candidate_key_value": "LOC_1",
            "match_class": "LITERAL",
            "target_key_multiplicity": "1",
            "candidate_key_multiplicity": "1",
            "potential_unique_locus": "1",
        }
    )
    documentary = tmp_path / "documentary.tsv"
    _write(documentary, EVIDENCE_FIELDS, [evidence])
    index = tmp_path / "index.tsv"
    _write(index, tuple(sorted(_D1_REQUIRED)), [])
    lineage = {}
    for release in releases.values():
        lineage[release] = tmp_path / f"lineage_{release}.tsv"
        _write(lineage[release], tuple(sorted(_D2_REQUIRED)), [])
    return {
        "jobs": jobs,
        "primary": primary,
        "audit": audit,
        "taxonomy": taxonomy,
        "releases": releases,
        "selected": tmp_path / "dp.tsv",
        "skipped": tmp_path / "skip.tsv",
        "documentary_evidence": documentary,
        "lineage_index": index,
        "lineage_paths": lineage,
    }


def test_only_same_taxid_nonexact_enters_dp(tmp_path: Path) -> None:
    args = _fixture(tmp_path)
    result = select_nonexact_dp(**args)
    assert result == {"nonexact": 3, "selected": 1, "skipped": 2}
    assert [(row["release"], row["rid"]) for row in _read(args["selected"])] == [
        ("R1", "1"),
    ]
    assert _read(args["skipped"]) == [
        {
            "release": "R1",
            "target_id": "T",
            "rid": "2",
            "reason": "CROSS_TAXID_NONEXACT",
        },
        {
            "release": "R2",
            "target_id": "T",
            "rid": "1",
            "reason": "NO_DOCUMENTARY_WITNESS",
        },
    ]


def test_fake_dp_candidate_is_rejected(tmp_path: Path) -> None:
    args = _fixture(tmp_path)
    jobs = _read(args["jobs"])
    jobs[0]["candidate_primary_accession"] = "FAKE"
    _write(args["jobs"], FIELDS, jobs)
    with pytest.raises(ValueError, match="identity disagreement"):
        select_nonexact_dp(**args)


def test_missing_taxonomy_rejected(tmp_path: Path) -> None:
    args = _fixture(tmp_path)
    _write(args["taxonomy"]["START"], TAXONOMY, [])
    with pytest.raises(ValueError, match="Missing 02B"):
        select_nonexact_dp(**args)


def test_duplicate_job_rejected(tmp_path: Path) -> None:
    args = _fixture(tmp_path)
    jobs = _read(args["jobs"])
    jobs.insert(1, dict(jobs[0]))
    _write(args["jobs"], FIELDS, jobs)
    with pytest.raises(ValueError, match="Duplicate"):
        select_nonexact_dp(**args)


@pytest.mark.parametrize("cross_taxid", [False, True])
def test_exact_only_completion_uses_frozen_finalizer(
    tmp_path: Path,
    cross_taxid: bool,
) -> None:
    """Exercise canonical execution without historical Stage 05 or any DP."""
    root = Path(
        os.environ.get("PROBE_SOURCE_REPO", Path(__file__).resolve().parents[1])
    )
    workload = tmp_path / "workload"
    workload.mkdir()
    exact = {
        "release": "R1",
        "target_id": "T",
        "rid": "1",
        "job_scope": "PRIMARY",
        "exact_sequence": "0" if cross_taxid else "1",
        "candidate_primary_accession": "P1",
        "raw_ox": "111",
        "discovery_channel": "STAGE2C_FULL_EXACT",
        "candidate_length": "4",
        "target_length": "4",
        "target_optional_accession": "X1",
    }
    _write(workload / JOBS_OUT, tuple(exact), [exact])
    _write(
        workload / UNION_OUT,
        PRIMARY,
        [
            {
                "release": "R1",
                "target_id": "T",
                "rid": "1",
                "candidate_primary_accession": "P1",
                "raw_ox": "111",
                "resolved_taxid": "112" if cross_taxid else "111",
                "sequence_length": "4",
                "discovery_channel": "STAGE2C_FULL_EXACT",
            }
        ],
    )
    _write(workload / AUDIT_OUT, AUDIT, [])
    (workload / START_STORE).write_text("source\n", encoding="utf-8")
    (workload / END_STORE).write_text("source\n", encoding="utf-8")
    names = (JOBS_OUT, UNION_OUT, AUDIT_OUT, START_STORE, END_STORE)
    hashes = {
        name: hashlib.sha256((workload / name).read_bytes()).hexdigest()
        for name in names
    }
    (workload / "2C_alignment_preview.summary.json").write_text(
        json.dumps(
            {
                "schema": "stage2c-alignment-preview-v1",
                "canonical_contract": "probe-canonical-v2-complete-workload-v1",
                "releases": {"START": "R1", "END": "R2"},
                "alignment_executed": False,
                "donor_decisions": 0,
                "production_stages_replaced": False,
                "output_sha256": hashes,
            }
        ),
        encoding="utf-8",
    )
    taxonomy = {}
    for role in ("START", "END"):
        path = tmp_path / f"tax_{role}.tsv"
        _write(
            path,
            TAXONOMY,
            [
                {
                    "target_id": "T",
                    "raw_taxid": "111",
                    "resolution_status": "CURRENT",
                    "resolved_taxid": "111",
                    "resolved_rank": "strain",
                    "species_taxid": "100",
                    "primary_bucket": "111",
                }
            ],
        )
        taxonomy[role] = path
    targets = tmp_path / "targets.tsv"
    targets.write_text("target_id\tnormalized_sequence\nT\tACDE\n")
    binary = tmp_path / "unused-dp"
    binary.write_text("#!/bin/sh\nexit 99\n")
    os.chmod(binary, 0o755)
    report = execute_alignment_v2(
        workload=workload,
        targets=targets,
        taxonomy=taxonomy,
        nonexact_script=(root / "scripts/pipeline/NONEXACT_select_alignment_jobs.py"),
        full_dp_binary=binary,
        finalize_script=(root / "scripts/pipeline/06C1_finalize_directional_glocal.py"),
        destination=tmp_path / "aligned",
    )
    assert report["dp_gate"] == {
        "nonexact": int(cross_taxid),
        "selected": 0,
        "skipped": int(cross_taxid),
    }
    assert report["dp_chunks"] == 0
    assert len(_read(tmp_path / "aligned" / FINAL_OUTPUT)) == (0 if cross_taxid else 1)
    assert (tmp_path / "aligned" / SUMMARY).is_file() is False
