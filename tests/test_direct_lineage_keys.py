import csv
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "pipeline" / "04_compile_direct_lineage_keys.py"


def read_tsv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle, delimiter="\t"))


def write_targets(path: Path) -> None:
    path.write_text(
        "target_id\toptional_accession\n"
        "T1\tQTARGET\n"
        "T2\t\n",
        encoding="utf-8",
    )


def write_anchors(
    path: Path,
    rows: list[
        tuple[str, str, str, str, str, str, str, str, str]
    ],
) -> None:
    path.write_text(
        "release\ttarget_id\tdirect_match_type\tprimary_accession\t"
        "rid\tfrag\tsec\tgene\tkey_source_eligible\n"
        + "".join(
            "\t".join((*row,)) + "\n"
            for row in rows
        ),
        encoding="utf-8",
    )


def run_stage(
    tmp_path: Path,
    *,
    start_release: str = "REL-A",
    end_release: str = "REL-B",
    end_row_release: str = "REL-B",
):
    targets = tmp_path / "targets.tsv"
    start = tmp_path / "start.tsv"
    end = tmp_path / "end.tsv"
    output = tmp_path / "keys.tsv"
    summary = tmp_path / "summary.json"

    write_targets(targets)

    write_anchors(
        start,
        [
            (
                "REL-A",
                "T1",
                "PRIMARY",
                "QTARGET",
                "11",
                "1",
                "S1,S_SHARED",
                "N:IGNORED;S:IGNORED_SYN;L:LOC1;O:ORF1",
                "1",
            ),
            (
                "REL-A",
                "T1",
                "PRIMARY",
                "BAD_PRIMARY",
                "99",
                "0",
                "BAD_SEC",
                "L:BAD_LOCUS;O:BAD_ORF",
                "0",
            ),
        ],
    )

    write_anchors(
        end,
        [
            (
                end_row_release,
                "T1",
                "PRIMARY",
                "QTARGET",
                "12",
                "0",
                "S_SHARED,S2",
                "N:IGNORED_END;L:LOC1;O:ORF2",
                "1",
            ),
            (
                end_row_release,
                "T2",
                "PRIMARY",
                "P2",
                "20",
                "0",
                "",
                "N:IGNORED_ONLY",
                "1",
            ),
        ],
    )

    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--targets",
            str(targets),
            "--start-anchors",
            str(start),
            "--end-anchors",
            str(end),
            "--start-release-id",
            start_release,
            "--end-release-id",
            end_release,
            "--output",
            str(output),
            "--summary",
            str(summary),
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )

    return result, output, summary


def test_frozen_lineage_key_semantics_with_arbitrary_release_ids(tmp_path):
    result, output, summary_path = run_stage(tmp_path)

    assert result.returncode == 0, result.stdout + result.stderr

    rows = read_tsv(output)
    observed = {
        (row["target_id"], row["key_type"], row["key_value"]): row
        for row in rows
    }

    assert set(observed) == {
        ("T1", "ACCESSION", "QTARGET"),
        ("T1", "ACCESSION", "S1"),
        ("T1", "ACCESSION", "S_SHARED"),
        ("T1", "ACCESSION", "S2"),
        ("T1", "ORDERED_LOCUS", "LOC1"),
        ("T1", "ORF", "ORF1"),
        ("T1", "ORF", "ORF2"),
        ("T2", "ACCESSION", "P2"),
    }

    target_key = observed[("T1", "ACCESSION", "QTARGET")]
    assert target_key["from_target_accession"] == "1"
    assert target_key["from_start_anchor"] == "1"
    assert target_key["from_end_anchor"] == "1"
    assert target_key["source_match_types"] == "PRIMARY"
    assert target_key["source_primary_accessions"] == "QTARGET"
    assert target_key["source_rids"] == "11,12"
    assert target_key["source_frag_flags"] == "0,1"

    shared = observed[("T1", "ACCESSION", "S_SHARED")]
    assert shared["from_target_accession"] == "0"
    assert shared["from_start_anchor"] == "1"
    assert shared["from_end_anchor"] == "1"

    assert not any(
        row["key_value"]
        in {
            "IGNORED",
            "IGNORED_SYN",
            "IGNORED_END",
            "IGNORED_ONLY",
            "BAD_PRIMARY",
            "BAD_SEC",
            "BAD_LOCUS",
            "BAD_ORF",
        }
        for row in rows
    )

    summary = json.loads(summary_path.read_text(encoding="utf-8"))

    assert summary["stage"] == "04C"
    assert summary["release_ids"] == {
        "START": "REL-A",
        "END": "REL-B",
    }
    assert summary["input_anchor_rows"] == {
        "start_total": 2,
        "start_eligible": 1,
        "end_total": 2,
        "end_eligible": 2,
    }
    assert summary["target_count"] == 2
    assert summary["lineage_key_count"] == 8
    assert summary["lineage_key_type_counts"] == {
        "ACCESSION": 5,
        "ORDERED_LOCUS": 1,
        "ORF": 2,
    }
    assert summary["targets_with_anchor_derived_key"] == 2

    policy = summary["policy"]
    assert policy["keys_frozen_before_lineage_scan"] is True
    assert policy["recursive_key_expansion"] is False
    assert policy["sequence_candidates_generate_lineage_keys"] is False
    assert policy["gene_name_candidate_generating"] is False
    assert policy["gene_synonym_candidate_generating"] is False


def test_anchor_release_mismatch_fails_closed(tmp_path):
    result, _output, _summary = run_stage(
        tmp_path,
        end_row_release="WRONG-RELEASE",
    )

    assert result.returncode != 0
    assert "expected release REL-B" in result.stderr
    assert "found WRONG-RELEASE" in result.stderr


def test_production_script_has_no_historical_release_ids():
    text = SCRIPT.read_text(encoding="utf-8")

    assert "START-2023-01" not in text
    assert "END-2026-03" not in text
    assert "anchor_release" not in text
