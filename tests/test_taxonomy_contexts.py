import csv
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/pipeline/02_prepare_taxonomy_contexts.py"


def _node(taxid: str, parent: str, rank: str) -> str:
    return (
        f"{taxid}\t|\t{parent}\t|\t{rank}\t|\t\t|\t0\t|\t0\t|\t1\t|\t"
        "0\t|\t0\t|\t0\t|\t0\t|\t0\t|\t\t|\n"
    )


def _name(taxid: str, name: str) -> str:
    return f"{taxid}\t|\t{name}\t|\t\t|\tscientific name\t|\n"


def _taxonomy_snapshot(root: Path) -> Path:
    root.mkdir()

    (root / "nodes.dmp").write_text(
        _node("1", "1", "no rank")
        + _node("9606", "1", "species")
        + _node("12345", "9606", "strain"),
        encoding="utf-8",
    )

    (root / "names.dmp").write_text(
        _name("1", "root")
        + _name("9606", "Synthetic species")
        + _name("12345", "Synthetic strain"),
        encoding="utf-8",
    )

    (root / "merged.dmp").write_text(
        "9999\t|\t9606\t|\n",
        encoding="utf-8",
    )

    (root / "delnodes.dmp").write_text(
        "8888\t|\n",
        encoding="utf-8",
    )

    return root


def _read_tsv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle, delimiter="\t"))


def test_prepare_taxonomy_contexts(tmp_path: Path) -> None:
    taxonomy = _taxonomy_snapshot(tmp_path / "taxonomy")

    targets = tmp_path / "targets.tsv"
    targets.write_text(
        "target_id\traw_taxid\n"
        "T_SPECIES\t9606\n"
        "T_STRAIN\t12345\n"
        "T_MERGED\t9999\n"
        "T_DELETED\t8888\n"
        "T_UNKNOWN\t7777\n",
        encoding="utf-8",
    )

    ox_list = tmp_path / "raw_ox.txt"
    ox_list.write_text(
        "12345\n"
        "7777\n"
        "8888\n"
        "9606\n"
        "9999\n"
        "NA\n",
        encoding="utf-8",
    )

    index = tmp_path / "taxonomy.sqlite"
    target_output = tmp_path / "target_taxonomy.tsv"
    ox_output = tmp_path / "ox_taxonomy.tsv"
    stats = tmp_path / "stats.json"

    subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--nodes",
            str(taxonomy / "nodes.dmp"),
            "--names",
            str(taxonomy / "names.dmp"),
            "--merged",
            str(taxonomy / "merged.dmp"),
            "--delnodes",
            str(taxonomy / "delnodes.dmp"),
            "--snapshot-id",
            "SYNTHETIC-TAXONOMY",
            "--index",
            str(index),
            "--targets",
            str(targets),
            "--ox-list",
            str(ox_list),
            "--target-output",
            str(target_output),
            "--ox-output",
            str(ox_output),
            "--stats",
            str(stats),
        ],
        cwd=ROOT,
        check=True,
    )

    assert index.is_file()

    target_rows = {
        row["target_id"]: row
        for row in _read_tsv(target_output)
    }

    assert target_rows["T_SPECIES"]["resolved_taxid"] == "9606"
    assert target_rows["T_SPECIES"]["species_taxid"] == "9606"
    assert target_rows["T_SPECIES"]["primary_bucket"] == "S:9606"

    assert target_rows["T_STRAIN"]["resolved_taxid"] == "12345"
    assert target_rows["T_STRAIN"]["species_taxid"] == "9606"
    assert target_rows["T_STRAIN"]["primary_bucket"] == "T:12345"

    assert target_rows["T_MERGED"]["resolved_taxid"] == "9606"
    assert target_rows["T_MERGED"]["species_taxid"] == "9606"
    assert target_rows["T_MERGED"]["primary_bucket"] == "S:9606"

    assert target_rows["T_DELETED"]["resolved_taxid"] == ""
    assert target_rows["T_DELETED"]["primary_bucket"] == ""

    assert target_rows["T_UNKNOWN"]["resolved_taxid"] == ""
    assert target_rows["T_UNKNOWN"]["primary_bucket"] == ""

    ox_rows = _read_tsv(ox_output)
    assert [row["raw_ox"] for row in ox_rows] == [
        "12345",
        "7777",
        "8888",
        "9606",
        "9999",
        "NA",
    ]

    ox_by_raw = {
        row["raw_ox"]: row
        for row in ox_rows
    }

    assert ox_by_raw["12345"]["species_bucket"] == "S:9606"
    assert ox_by_raw["12345"]["resolved_bucket"] == "T:12345"

    assert ox_by_raw["9606"]["species_bucket"] == "S:9606"
    assert ox_by_raw["9606"]["resolved_bucket"] == "T:9606"

    assert ox_by_raw["9999"]["resolved_taxid"] == "9606"
    assert ox_by_raw["9999"]["species_bucket"] == "S:9606"

    assert ox_by_raw["8888"]["resolved_taxid"] == ""
    assert ox_by_raw["7777"]["resolved_taxid"] == ""
    assert ox_by_raw["NA"]["resolved_taxid"] == ""

    payload = json.loads(stats.read_text(encoding="utf-8"))

    assert payload["stage"] == "02B"
    assert payload["snapshot_id"] == "SYNTHETIC-TAXONOMY"
    assert payload["target_count"] == 5
    assert payload["distinct_raw_ox_count"] == 6

    provenance = payload["taxonomy_provenance"]
    assert provenance["snapshot_id"] == "SYNTHETIC-TAXONOMY"

    members = {
        Path(source["member"]).name
        for source in provenance["sources"]
    }
    assert members == {
        "nodes.dmp",
        "names.dmp",
        "merged.dmp",
        "delnodes.dmp",
    }


def test_unsorted_ox_list_is_rejected(tmp_path: Path) -> None:
    taxonomy = _taxonomy_snapshot(tmp_path / "taxonomy")

    targets = tmp_path / "targets.tsv"
    targets.write_text(
        "target_id\traw_taxid\n"
        "T1\t9606\n",
        encoding="utf-8",
    )

    ox_list = tmp_path / "raw_ox.txt"
    ox_list.write_text(
        "9606\n"
        "12345\n",
        encoding="utf-8",
    )

    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--taxon-dir",
            str(taxonomy),
            "--snapshot-id",
            "SYNTHETIC-TAXONOMY",
            "--index",
            str(tmp_path / "taxonomy.sqlite"),
            "--targets",
            str(targets),
            "--ox-list",
            str(ox_list),
            "--target-output",
            str(tmp_path / "target_taxonomy.tsv"),
            "--ox-output",
            str(tmp_path / "ox_taxonomy.tsv"),
            "--stats",
            str(tmp_path / "stats.json"),
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode != 0
    assert "OX list must be strictly sorted and unique" in result.stderr
