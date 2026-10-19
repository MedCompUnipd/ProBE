"""Cold-start tests: EXACT never suppresses distinct documentary pairs."""

from __future__ import annotations

import csv
import os
import subprocess
import sys
from pathlib import Path

import pytest

from probe.canonical_v2.candidate_assembly import assemble_candidate_provenance
from probe.canonical_v2.contracts import (
    DISCOVERY_REQUIRED,
    FULL_SOURCE_REQUIRED,
    GO_SOURCE_REQUIRED,
    LINEAGE_REQUIRED,
    RESCUE_REQUIRED,
)
from probe.canonical_v2.lineage_attestation import (
    FACT_FIELDS,
    LONG_FIELDS,
    attest_lineage_conservation,
)


def _write(path: Path, fields: frozenset[str], rows: list[dict]) -> None:
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(
            stream, fieldnames=sorted(fields), delimiter="\t",
            lineterminator="\n",
        )
        writer.writeheader()
        for row in rows:
            writer.writerow({name: row.get(name, "") for name in fields})


def _rows(tmp_path: Path) -> dict[str, dict[str, str]]:
    path = tmp_path / "published" / "candidate_provenance.tsv"
    with path.open(encoding="utf-8", newline="") as stream:
        return {
            row["rid"]: row for row in csv.DictReader(stream, delimiter="\t")
        }


def _scanner(rid: str, accession: str, *, exact: str) -> dict:
    record = {field: "1" for field in GO_SOURCE_REQUIRED}
    record.update(
        target_id="T", rid=rid, accession=accession, raw_ox="111",
        resolved_taxid="111", species_taxid="100", frag="0",
        record_number=rid, target_bucket="S:100", exact_sequence=exact,
        candidate_length="15", target_length="15",
    )
    return record


def _discovery(rid: str, *, full: bool) -> dict:
    row = {field: "" for field in DISCOVERY_REQUIRED}
    row.update(
        release="R1", target_id="T", rid=rid,
        candidate_accession=f"A{rid}", candidate_raw_ox="112" if full else "111",
        target_resolved_taxid="111",
        candidate_resolved_taxid="112" if full else "111",
        species_taxid="100", go_positive_species_wide=str(int(not full)),
        full_exact_species_wide=str(int(full)),
        go_positive_exact="0" if full else "1",
        go_positive_record_number="" if full else "101",
        full_record_number="9" if full else "",
        strict_gate="DENIED" if full else "DEFER_TO_DOCUMENTARY",
        same_species_exact_gate=(
            "ELIGIBLE" if full else "DEFER_TO_DOCUMENTARY"
        ),
    )
    return row


def _fixtures(tmp_path: Path) -> dict[str, Path]:
    paths = {
        key: tmp_path / f"{key}.tsv"
        for key in (
            "discovery_union", "go_positive_pairs", "full_exact_pairs",
            "lineage_pairs", "rescue_pairs",
        )
    }
    _write(
        paths["discovery_union"], DISCOVERY_REQUIRED,
        [_discovery("101", full=False), _discovery("102", full=True)],
    )
    go = _scanner("101", "A101", exact="1")
    go["record_number"] = "101"
    _write(paths["go_positive_pairs"], GO_SOURCE_REQUIRED, [go])
    full = {field: "1" for field in FULL_SOURCE_REQUIRED}
    full.update(
        release="R1", target_id="T", rid="102", record_number="9",
        accession="A102", raw_ox="112", target_resolved_taxid="111",
        candidate_resolved_taxid="112", species_taxid="100", frag="0",
        exact_full_sequence="1", discovery_channel="FULL_V2_EXACT",
    )
    _write(paths["full_exact_pairs"], FULL_SOURCE_REQUIRED, [full])
    rescue = _scanner("104", "A104", exact="0")
    _write(paths["rescue_pairs"], RESCUE_REQUIRED, [rescue])
    return paths


def _long(
    rid: str,
    accession: str,
    *,
    frag: str = "0",
    role: str = "ACCESSION_PRIMARY",
    value: str = "KEY",
) -> dict[str, str]:
    row = {name: "" for name in LONG_FIELDS}
    row.update(
        release="R1",
        target_id="T",
        target_primary_bucket="T:111",
        record_number=rid,
        candidate_primary_accession=accession,
        entry=accession,
        rid=rid,
        raw_ox="111",
        resolved_taxid="111",
        species_taxid="100",
        frag=frag,
        sequence_length="15",
        pe="1",
        sv="1",
        sec="",
        gene="",
        key_type="ACCESSION",
        key_value=value,
        record_match_role=role,
        source_roles="TARGET_ACCESSION,START_ANCHOR",
        from_target_accession="1",
        from_start_anchor="1",
        from_end_anchor="0",
        key_collision="0",
        key_target_multiplicity="1",
    )
    return row


def _synthetic_04e(long_form: Path, collapsed: Path) -> None:
    repo = Path(os.environ.get("PROBE_B2B_REPO", Path(__file__).parents[1]))
    script = repo / "scripts/pipeline/04E_collapse_lineage_evidence.py"
    if not script.is_file():
        raise FileNotFoundError(script)
    subprocess.run(
        [
            sys.executable,
            str(script),
            "--input", str(long_form),
            "--output", str(collapsed),
            "--summary", str(collapsed.with_suffix(".json")),
        ],
        check=True,
        capture_output=True,
        text=True,
    )


def _fixture(tmp_path: Path) -> dict[str, Path]:
    paths = _fixtures(tmp_path)
    long_form = tmp_path / "04D2.tsv"
    _write(
        long_form,
        frozenset(LONG_FIELDS),
        [
            _long("101", "A101"),
            _long("101", "A101", role="ORDERED_LOCUS", value="LOCUS1"),
            _long("103", "A103", frag="1", value="A103"),
        ],
    )
    # 04E is produced only for this synthetic fixture, not for frozen data.
    collapsed = tmp_path / "04E.tsv"
    _synthetic_04e(long_form, collapsed)
    paths["lineage_pairs"].write_bytes(collapsed.read_bytes())
    # T has both exact and documentary records. Exact is sequence-level only.
    assemble_candidate_provenance(
        release="R1", output_dir=tmp_path / "published", **paths
    )
    paths["04D2"] = long_form
    paths["04E"] = collapsed
    paths["ledger"] = tmp_path / "published" / "candidate_provenance.tsv"
    return paths


def _alter_tsv(path: Path, column: str, value: str) -> None:
    with path.open(encoding="utf-8", newline="") as stream:
        reader = csv.DictReader(stream, delimiter="\t")
        columns = list(reader.fieldnames or [])
        rows = list(reader)
    rows[0][column] = value
    _write(path, frozenset(columns), rows)


def _attest(tmp_path: Path, paths: dict[str, Path]) -> dict:
    return attest_lineage_conservation(
        release="R1",
        long_form=paths["04D2"],
        collapsed=paths["04E"],
        candidate_ledger=paths["ledger"],
        scratch_dir=tmp_path,
    )


def test_exact_does_not_suppress_separate_documentary_candidate(
    tmp_path: Path,
) -> None:
    paths = _fixture(tmp_path)
    result = _attest(tmp_path, paths)
    assert result["status"] == "PASS"
    assert result["counts"]["long_form_rows"] == 3
    assert result["counts"]["long_form_pairs"] == 2
    assert result["counts"]["ledger_lineage_pairs"] == 2
    assert result["counts"]["lineage_pairs_with_exact"] == 1
    rows = _rows(tmp_path)
    assert rows["101"]["go_positive_exact"] == "1"
    assert rows["103"]["lineage_discovered"] == "1"
    assert rows["103"]["go_positive_species_wide"] == "0"
    assert rows["103"]["full_exact_species_wide"] == "0"
    assert rows["102"]["full_exact_species_wide"] == "1"
    assert result["no_donor_or_dp_decisions"] is True


def test_missing_lineage_in_ledger_is_rejected(tmp_path: Path) -> None:
    paths = _fixture(tmp_path)
    _alter_tsv(paths["ledger"], "lineage_discovered", "0")
    with pytest.raises(ValueError, match="lineage flag lost"):
        _attest(tmp_path, paths)


def test_missing_04e_pair_is_rejected(tmp_path: Path) -> None:
    paths = _fixture(tmp_path)
    with paths["04E"].open(encoding="utf-8", newline="") as stream:
        reader = csv.DictReader(stream, delimiter="\t")
        rows = list(reader)
    _write(paths["04E"], LINEAGE_REQUIRED, rows[:-1])
    with pytest.raises(ValueError, match="absent from 04E"):
        _attest(tmp_path, paths)


def test_corrupted_04e_typed_provenance_is_rejected(tmp_path: Path) -> None:
    paths = _fixture(tmp_path)
    _alter_tsv(paths["04E"], "matched_typed_keys", "BAD:UNTRUSTED")
    with pytest.raises(ValueError, match="04D2/04E mismatch"):
        _attest(tmp_path, paths)


def test_corrupted_ledger_provenance_is_rejected(tmp_path: Path) -> None:
    paths = _fixture(tmp_path)
    _alter_tsv(paths["ledger"], "lineage_source_json", "{}")
    with pytest.raises(ValueError, match="details changed"):
        _attest(tmp_path, paths)


def test_repeated_collapsed_pair_is_rejected(tmp_path: Path) -> None:
    paths = _fixture(tmp_path)
    with paths["04E"].open(encoding="utf-8", newline="") as stream:
        reader = csv.DictReader(stream, delimiter="\t")
        rows = list(reader)
    _write(paths["04E"], LINEAGE_REQUIRED, [*rows, rows[0]])
    with pytest.raises(ValueError, match="Duplicate collapsed"):
        _attest(tmp_path, paths)


def test_inconsistent_long_form_facts_rejected(tmp_path: Path) -> None:
    paths = _fixture(tmp_path)
    with paths["04D2"].open(encoding="utf-8", newline="") as stream:
        rows = list(csv.DictReader(stream, delimiter="\t"))
    rows[1]["candidate_primary_accession"] = "WRONG"
    _write(paths["04D2"], frozenset(FACT_FIELDS) | LONG_FIELDS, rows)
    with pytest.raises(ValueError, match="Conflicting 04D2 facts"):
        _attest(tmp_path, paths)


def test_unverified_cross_taxid_never_authorizes_dp(tmp_path: Path) -> None:
    paths = _fixture(tmp_path)
    result = _attest(tmp_path, paths)
    assert result["no_donor_or_dp_decisions"]
    rows = _rows(tmp_path)
    assert rows["102"]["candidate_resolved_taxid"] == "112"
    assert rows["102"]["target_id"] == "T"
    assert rows["102"]["lineage_discovered"] == "0"
