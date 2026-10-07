import csv
import json
import subprocess
import sys
from pathlib import Path

import pytest

from probe.workflow.lineage_scan import (
    preflight_lineage_scan_index,
    read_lineage_scan_summary,
    write_empty_lineage_scan_outputs,
)

ROOT = Path(__file__).resolve().parents[1]
INDEX_SCRIPT = ROOT / "scripts/pipeline/04D1_prepare_lineage_scan_index.py"
RUNNER = ROOT / "workflow/scripts/run_stage_04D2.py"


INDEX_HEADER = (
    "release\ttarget_id\tprimary_bucket\tkey_type\tkey_value"
    "\tkey_target_multiplicity\tkey_collision"
    "\tfrom_target_accession\tfrom_start_anchor\tfrom_end_anchor"
    "\tsource_roles\n"
)


def test_lineage_scan_preflight_allows_zero_postings_for_one_release(tmp_path):
    index = tmp_path / "index.tsv"
    index.write_text(
        INDEX_HEADER + "REL-B\tT1\tS:9606\tACCESSION\tP1"
        "\t1\t0\t1\t0\t0\tTARGET_ACCESSION\n",
        encoding="utf-8",
    )

    preflight = preflight_lineage_scan_index(
        index,
        release="REL-A",
        allowed_releases={"REL-A", "REL-B"},
    )

    assert preflight.posting_count == 0
    assert preflight.total_posting_count == 1


def test_lineage_scan_preflight_rejects_invalid_bucket(tmp_path):
    index = tmp_path / "index.tsv"
    index.write_text(
        INDEX_HEADER + "REL-A\tT1\tBAD\tACCESSION\tP1"
        "\t1\t0\t1\t0\t0\tTARGET_ACCESSION\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="invalid primary_bucket"):
        preflight_lineage_scan_index(
            index,
            release="REL-A",
            allowed_releases={"REL-A", "REL-B"},
        )


def test_empty_lineage_scan_outputs_are_canonical(tmp_path):
    output = tmp_path / "evidence.tsv"
    summary = tmp_path / "summary.tsv"

    write_empty_lineage_scan_outputs(
        output,
        summary,
        release="REL-A",
    )

    with output.open("r", encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle, delimiter="\t"))

    assert rows == []

    parsed = read_lineage_scan_summary(
        summary,
        expected_release="REL-A",
    )
    assert parsed.postings_loaded == 0
    assert parsed.evidence_rows == 0


def test_04d1_omits_only_release_with_missing_target_bucket(tmp_path):
    targets = tmp_path / "targets.tsv"
    keys = tmp_path / "keys.tsv"
    start_anchors = tmp_path / "start_anchors.tsv"
    end_anchors = tmp_path / "end_anchors.tsv"
    start_taxonomy = tmp_path / "start_taxonomy.tsv"
    end_taxonomy = tmp_path / "end_taxonomy.tsv"
    output = tmp_path / "index.tsv"
    summary = tmp_path / "summary.json"

    targets.write_text(
        "target_id\toptional_accession\nT1\tA1\nT2\tA2\n",
        encoding="utf-8",
    )

    keys.write_text(
        "target_id\tkey_type\tkey_value"
        "\tfrom_target_accession\tfrom_start_anchor\tfrom_end_anchor\n"
        "T1\tACCESSION\tA1\t1\t0\t0\n"
        "T2\tACCESSION\tA2\t1\t0\t0\n",
        encoding="utf-8",
    )

    anchor_header = (
        "release\ttarget_id\tprimary_accession\tsec\tgene\tkey_source_eligible\n"
    )
    start_anchors.write_text(anchor_header, encoding="utf-8")
    end_anchors.write_text(anchor_header, encoding="utf-8")

    start_taxonomy.write_text(
        "target_id\tprimary_bucket\nT1\tS:1\nT2\t\n",
        encoding="utf-8",
    )
    end_taxonomy.write_text(
        "target_id\tprimary_bucket\nT1\tS:1\nT2\tS:2\n",
        encoding="utf-8",
    )

    result = subprocess.run(
        [
            sys.executable,
            str(INDEX_SCRIPT),
            "--keys",
            str(keys),
            "--targets",
            str(targets),
            "--start-anchors",
            str(start_anchors),
            "--end-anchors",
            str(end_anchors),
            "--start-target-taxonomy",
            str(start_taxonomy),
            "--end-target-taxonomy",
            str(end_taxonomy),
            "--start-release-id",
            "REL-A",
            "--end-release-id",
            "REL-B",
            "--output",
            str(output),
            "--summary",
            str(summary),
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stdout + result.stderr

    with output.open("r", encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle, delimiter="\t"))

    assert {(row["release"], row["target_id"]) for row in rows} == {
        ("REL-A", "T1"),
        ("REL-B", "T1"),
        ("REL-B", "T2"),
    }

    payload = json.loads(summary.read_text(encoding="utf-8"))

    assert payload["scan_index_row_count_by_release"] == {
        "REL-A": 1,
        "REL-B": 2,
    }
    assert payload["frozen_keys_without_candidate_bucket_by_release"] == {
        "REL-A": 1,
        "REL-B": 0,
    }
    assert payload["targets_without_candidate_bucket_by_release"] == {
        "REL-A": 1,
        "REL-B": 0,
    }


def test_04d2_runner_declares_structured_empty_outcomes():
    text = RUNNER.read_text(encoding="utf-8")

    assert 'outcome_name = "stage_outcome.json"' in text
    assert 'state="SKIPPED_NO_ELIGIBLE_TARGETS"' in text
    assert 'reason_code="NO_LINEAGE_SCAN_POSTINGS"' in text
    assert 'state="EMPTY_RESULT"' in text
    assert 'reason_code="NO_LINEAGE_EVIDENCE"' in text
    assert 'state="COMPLETED"' in text
    assert '"command_executed": preflight.posting_count > 0' in text
