"""Stage 2C TaxID gate: excluded discovery is not DP or donor evidence."""

from __future__ import annotations

import ast
import csv
import json
import subprocess
import sys
from pathlib import Path

import pytest

from probe.stage2c_alignment_completion import (
    OUTPUTS,
    _dp_budget,
    _dp_header,
    _select_dp_jobs,
)

ROOT = Path(__file__).resolve().parents[1]
FINALIZER = ROOT / "scripts/pipeline/06C1_finalize_directional_glocal.py"
JOBS = [
    "release", "target_id", "rid", "job_scope", "discovery_channel",
    "target_optional_accession", "candidate_primary_accession",
    "target_length", "candidate_length", "exact_sequence", "raw_ox",
]
PRIMARY = [
    "release", "target_id", "rid", "candidate_primary_accession",
    "raw_ox", "resolved_taxid", "species_taxid", "sequence_length",
    "discovery_channel",
]
AUDIT = [
    "release", "target_id", "rid", "candidate_primary_accession",
    "raw_ox", "target_resolved_taxid", "anchor_resolved_taxid",
    "anchor_species_taxid", "audit_alignment_allowed", "key_source_reason",
    "audit_class", "record_already_in_primary_union_for_target",
]
TAX = [
    "target_id", "raw_taxid", "resolution_status", "resolved_taxid",
    "resolved_rank", "species_taxid", "primary_bucket",
]


def _write(path: Path, fields: list[str], rows: list[list[str]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, delimiter="\t", lineterminator="\n")
        writer.writerow(fields)
        writer.writerows(rows)


def _read(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle, delimiter="\t"))


@pytest.fixture
def sample(tmp_path: Path) -> dict:
    primary = tmp_path / "05A2.tsv"
    canonical = tmp_path / "legacy.tsv"
    audit = tmp_path / "05C2.tsv"
    nonexact = tmp_path / "NONEXACT.tsv"
    all_jobs = tmp_path / "06A.tsv"
    rows = [
        ["A", "t1", "1", "PRIMARY", "SEQUENCE", "", "P1", "4", "4", "0", "11"],
        ["A", "t2", "2", "PRIMARY", "GO_NEW", "", "P2", "4", "4", "0", "22"],
        ["A", "t3", "3", "PRIMARY", "LEGACY", "", "P3", "4", "4", "0", "33"],
        ["A", "t4", "4", "PRIMARY", "GO_NEW", "", "P4", "4", "4", "1", "44"],
        [
            "A", "t5", "5", "TAXONOMY_AUDIT", "TAXONOMY_AUDIT",
            "", "P5", "4", "4", "0", "55",
        ],
        ["Z", "t2", "2", "PRIMARY", "GO_NEW", "", "P2", "4", "4", "0", "22"],
    ]
    _write(all_jobs, JOBS, rows)
    _write(nonexact, JOBS, [r for r in rows if r[9] == "0"])
    candidates = [
        ["A", "t1", "1", "P1", "11", "111", "100", "4", "SEQUENCE"],
        ["A", "t2", "2", "P2", "22", "112", "100", "4", "GO_NEW"],
        ["A", "t3", "3", "P3", "33", "112", "100", "4", "LEGACY"],
        ["A", "t4", "4", "P4", "44", "112", "100", "4", "GO_NEW"],
        ["Z", "t2", "2", "P2", "22", "112", "100", "4", "GO_NEW"],
    ]
    _write(primary, PRIMARY, candidates)
    _write(canonical, PRIMARY, [candidates[0], candidates[2]])
    _write(
        audit, AUDIT,
        [[
            "A", "t5", "5", "P5", "55", "111", "112", "100",
            "1", "SAME_SPECIES_WRONG_SUBTAXON", "TAXONOMY_RESCUE_AUDIT", "0",
        ]],
    )
    taxonomy = {}
    for release in ("A", "Z"):
        path = tmp_path / f"tax_{release}.tsv"
        _write(
            path, TAX,
            [
                [f"t{n}", "111", "CURRENT", "111", "strain", "100", "T:111"]
                for n in range(1, 6)
            ],
        )
        taxonomy[release] = path
    return {
        "nonexact_in": nonexact,
        "primary": primary,
        "audit": audit,
        "canonical": canonical,
        "taxonomy": taxonomy,
        "output": tmp_path / "selected.tsv",
        "excluded": tmp_path / "skipped.tsv",
        "database": tmp_path / "gate.sqlite3",
        "jobs": all_jobs,
    }


def _dp_result(path: Path, selected: list[dict[str, str]]) -> None:
    header = _dp_header()
    result = []
    for job in selected:
        row = {key: job[key] for key in header[:9]}
        for prefix in ("target_global", "candidate_global"):
            facts = {
                "status": "OK", "score": "5", "target_start0": "0",
                "target_end0_exclusive": "4", "candidate_start0": "0",
                "candidate_end0_exclusive": "4", "target_aligned_span": "4",
                "candidate_aligned_span": "4", "target_coverage": "1.0000000000",
                "candidate_coverage": "1.0000000000", "matches": "3",
                "mismatches": "1", "gap_in_target_residues": "0",
                "gap_in_candidate_residues": "0", "gap_openings": "0",
                "aligned_residue_pairs": "4", "alignment_columns": "4",
                "identity_aligned_pairs": "0.7500000000",
                "identity_alignment_columns": "0.7500000000",
                "cells_evaluated": "25",
            }
            row.update({f"{prefix}_{key}": value for key, value in facts.items()})
        result.append([row[key] for key in header])
    _write(path, header, result)


def test_gate_skips_all_cross_taxid_even_canonical_audit(sample: dict) -> None:
    assert _select_dp_jobs(**{k: v for k, v in sample.items() if k != "jobs"}) == (
        1, 4, 1, 1,
    )
    selected = _read(sample["output"])
    skips = _read(sample["excluded"])
    assert [(r["release"], r["target_id"]) for r in selected] == [
        ("A", "t1"),
    ]
    assert [(r["release"], r["target_id"]) for r in skips] == [
        ("A", "t2"), ("A", "t3"), ("A", "t5"), ("Z", "t2"),
    ]
    assert {r["reason"] for r in skips} == {"CROSS_TAXID_NONEXACT"}
    assert _dp_budget(sample["output"], max_pair_cells=50, max_total_cells=50) == (
        1, 50,
    )
    with pytest.raises(ValueError, match="INCOMPLETE_BUDGET"):
        _dp_budget(sample["output"], max_pair_cells=50, max_total_cells=49)

    dp = sample["database"].with_name("06C0.tsv")
    _dp_result(dp, selected)
    final = sample["database"].with_name("06C1.tsv")
    summary = sample["database"].with_name("06C1.json")
    subprocess.run(
        [
            sys.executable, str(FINALIZER), "--alignment-jobs", str(sample["jobs"]),
            "--nonexact-alignments", str(dp), "--policy-skipped",
            str(sample["excluded"]), "--output", str(final), "--summary",
            str(summary), "--start-release-id", "A", "--end-release-id", "Z",
        ],
        check=True,
    )
    finalized = _read(final)
    assert len(finalized) == 2
    assert [(r["target_id"], r["target_global_status"]) for r in finalized] == [
        ("t1", "OK"), ("t4", "OK_EXACT"),
    ]
    report = json.loads(summary.read_text(encoding="utf-8"))
    assert report["canonical_pair_count"] == 6
    assert report["aligned_nonexact_pair_count"] == 1
    assert report["policy_skipped_nonexact_count"] == 4
    assert report["directional_alignment_count"] == 4


def test_new_same_taxid_nonexact_is_selected_without_legacy(sample: dict) -> None:
    rows = _read(sample["primary"])
    rows[1]["resolved_taxid"] = "111"
    _write(sample["primary"], PRIMARY, [[r[k] for k in PRIMARY] for r in rows])
    result = _select_dp_jobs(
        **{key: value for key, value in sample.items() if key != "jobs"}
    )
    assert result == (2, 3, 1, 1)
    assert [(r["release"], r["target_id"]) for r in _read(sample["output"])] == [
        ("A", "t1"), ("A", "t2"),
    ]
    assert [(r["release"], r["target_id"]) for r in _read(sample["excluded"])] == [
        ("A", "t3"), ("A", "t5"), ("Z", "t2"),
    ]


def test_target_taxid_unknown_fails_closed(sample: dict) -> None:
    rows = _read(sample["taxonomy"]["A"])
    rows[0]["resolved_taxid"] = ""
    _write(sample["taxonomy"]["A"], TAX, [[r[k] for k in TAX] for r in rows])
    with pytest.raises(ValueError, match="Unverified resolved TaxID"):
        _select_dp_jobs(**{k: v for k, v in sample.items() if k != "jobs"})


def test_candidate_taxid_unknown_fails_closed(sample: dict) -> None:
    rows = _read(sample["primary"])
    rows[1]["resolved_taxid"] = ""
    _write(sample["primary"], PRIMARY, [[r[k] for k in PRIMARY] for r in rows])
    with pytest.raises(ValueError, match="Unverified resolved TaxID"):
        _select_dp_jobs(**{k: v for k, v in sample.items() if k != "jobs"})


def test_taxonomy_conflict_rejected(sample: dict) -> None:
    rows = _read(sample["taxonomy"]["A"])
    rows[4]["resolved_taxid"] = "113"
    _write(
        sample["taxonomy"]["A"], TAX, [[r[k] for k in TAX] for r in rows],
    )
    with pytest.raises(ValueError, match="Invalid canonical taxonomy audit"):
        _select_dp_jobs(**{k: v for k, v in sample.items() if k != "jobs"})


def test_legacy_provenance_conflict_rejected(sample: dict) -> None:
    rows = _read(sample["canonical"])
    rows[0]["candidate_primary_accession"] = "BAD"
    _write(sample["canonical"], PRIMARY, [[r[k] for k in PRIMARY] for r in rows])
    with pytest.raises(ValueError, match="Canonical 05A2 keys/identities"):
        _select_dp_jobs(**{k: v for k, v in sample.items() if k != "jobs"})


def test_05c2_primary_overlap_does_not_duplicate_candidate_or_dp(sample: dict) -> None:
    audit = _read(sample["audit"])
    # 24 historical audit-to-primary transitions are EXACT in V5.
    audit.append({
        "release": "A", "target_id": "t4", "rid": "4",
        "candidate_primary_accession": "P4", "raw_ox": "44",
        "target_resolved_taxid": "111", "anchor_resolved_taxid": "112",
        "anchor_species_taxid": "100", "audit_alignment_allowed": "1",
        "key_source_reason": "SAME_SPECIES_WRONG_SUBTAXON",
        "audit_class": "TAXONOMY_RESCUE_AUDIT",
        "record_already_in_primary_union_for_target": "1",
    })
    _write(sample["audit"], AUDIT, [[row[k] for k in AUDIT] for row in audit])
    assert _select_dp_jobs(
        **{k: v for k, v in sample.items() if k != "jobs"}
    ) == (1, 4, 1, 1)


def test_05c2_overlap_identity_conflict_fails_closed(sample: dict) -> None:
    audit = _read(sample["audit"])
    overlap = dict(audit[0])
    overlap.update(
        target_id="t4", rid="4", raw_ox="BAD",
        record_already_in_primary_union_for_target="1",
    )
    _write(sample["audit"], AUDIT, [[r[k] for k in AUDIT] for r in [*audit, overlap]])
    with pytest.raises(ValueError, match="overlap/PRIMARY identity conflict"):
        _select_dp_jobs(**{k: v for k, v in sample.items() if k != "jobs"})


def test_workflow_declares_all_completion_publication_files() -> None:
    """The workflow receipt must accept every producer output and its hash."""
    script = ROOT / "workflow/scripts/run_stage_2c_variant.py"
    tree = ast.parse(script.read_text(encoding="utf-8"))
    assignments = [
        node.value
        for node in tree.body
        if isinstance(node, ast.Assign)
        and any(
            isinstance(target, ast.Name) and target.id == "OUTPUTS"
            for target in node.targets
        )
    ]
    assert len(assignments) == 1
    declared = ast.literal_eval(assignments[0])["completion"]
    assert len(declared) == len(set(declared))
    assert set(declared) == set(OUTPUTS)
