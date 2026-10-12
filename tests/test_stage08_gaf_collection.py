"""Synthetic end-to-end tests for the one-release normalized GAF collector."""

import hashlib
import json
from dataclasses import replace
from pathlib import Path

import pytest

from probe.parsing.gaf import GafParser
from probe.stage08_annotations import NormalizedGafSource
from probe.stage08_collection_inputs import GafCollectionInput
from probe.stage08_donors import DonorAssociation, DonorIndex
from probe.stage08_gaf_collection import collect_one_normalized_gaf
from probe.workflow.artifacts import Artifact


def accepted(release="S", target="T1", rid="1", accession="A", donor="YES", count="1"):
    return DonorAssociation(
        release,
        target,
        rid,
        ".",
        accession,
        donor,
        "DIRECT_PRIMARY_ACCESSION",
        "EXACT_SEQUENCE",
        "1",
        "PRIMARY",
        "PRIMARY",
        ".",
        count,
    )


def donors(*rows):
    return DonorIndex(
        rows,
        start_release_id="S",
        end_release_id="E",
        source_database="UniProtKB",
    )


def gaf_row(
    accession="A",
    code="IDA",
    qualifier="enables",
    reference="PMID:1",
    database="UniProtKB",
    go="GO:0000001",
    form="",
    aspect="F",
):
    fields = (
        database,
        accession,
        "sym",
        qualifier,
        go,
        reference,
        code,
        "WITH:1|WITH:2",
        aspect,
        'A "quoted" name',
        "alias1|alias2",
        "protein",
        "taxon:1",
        "20230101",
        "source",
        "ext(X)",
        form,
    )
    return "\t".join(fields) + "\n"


def fixture(tmp_path, *, rows=(), release="S", role="START", version=True):
    path = tmp_path / "goa.normalized.gaf"
    text = ("!gaf-version: 2.2\n" if version else "") + "".join(rows)
    path.write_bytes(text.encode("utf-8"))
    data = path.read_bytes()
    checksum = hashlib.sha256(data).hexdigest()
    source = NormalizedGafSource(release, str(path), checksum)
    gaf = GafCollectionInput(
        role,
        source,
        len(data),
        Artifact("manifest.json", 1, "f" * 64),
        "policy/manifest.json",
    )
    staging = tmp_path / "stage"
    staging.mkdir()
    return gaf, staging


def table(path: Path):
    return [line.split("\t") for line in path.read_text().splitlines()]


def test_multiple_donors_shared_source_and_polarity(tmp_path):
    index = donors(
        accepted(count="2"),
        accepted(rid="2", accession="B", count="2"),
        accepted(target="T2"),
        accepted(target="T3", rid="4", accession="Z", donor="NO"),
        accepted(target="T4", rid="5", accession="M"),
    )
    gaf, staging = fixture(
        tmp_path,
        rows=[
            gaf_row("A", "IDA"),
            gaf_row("B", "IMP", "NOT|enables"),
            gaf_row("C", "FUTURE_CODE_X"),
            gaf_row("A", "IDA", reference="PMID:2"),
            gaf_row("B", "new-x", reference="PMID:3"),
        ],
    )
    result = collect_one_normalized_gaf(gaf=gaf, donors=index, staging_dir=staging)
    assert result.physical_lines == 6
    assert result.data_lines == 5
    assert result.retrieved_source_rows == 4
    assert result.annotation_links == 6
    assert result.donor_yes_associations == 4
    assert result.donor_yes_without_rows == 1
    assert result.coverage_certified
    sources = table(staging / "08_start_source_assertions.tsv")
    links = table(staging / "08_start_annotation_links.tsv")
    assert len(sources) == 5
    assert len(links) == 7
    assert {row[2] for row in sources[1:]} == {"2", "3", "5", "6"}
    assert sources[1][12] == 'A "quoted" name'
    assert {row[1] for row in links[1:]} == {"T1", "T2"}
    assert len({row[3] for row in links[1:]}) == 4
    coverage = table(staging / "08_start_donor_coverage.tsv")
    by_target_rid = {(row[1], row[2]): row for row in coverage[1:]}
    assert by_target_rid[("T1", "1")][5:8] == ["2", "0", "ROWS_RETRIEVED"]
    assert by_target_rid[("T1", "2")][5:8] == ["1", "1", "ROWS_RETRIEVED"]
    assert by_target_rid[("T2", "1")][5:8] == ["2", "0", "ROWS_RETRIEVED"]
    assert by_target_rid[("T3", "4")][7] == "DONOR_DISABLED"
    assert by_target_rid[("T4", "5")][7] == "NO_RETAINED_GOA_ROWS"
    inventory = table(staging / "08_start_evidence_inventory.tsv")
    assert {row[1] for row in inventory[1:]} == {"IDA", "IMP", "new-x"}
    assert [row[4:6] for row in inventory if row[1] == "IDA"] == [["2", "4"]]
    summary = json.loads((staging / "08_start_scan_summary.json").read_text())
    assert summary["coverage_certified"] is True
    assert summary["filtering_policy"] == "none"


def test_only_not_row_counts_as_retrieved(tmp_path):
    gaf, staging = fixture(tmp_path, rows=[gaf_row(qualifier="NOT|enables")])
    collect_one_normalized_gaf(gaf=gaf, donors=donors(accepted()), staging_dir=staging)
    row = table(staging / "08_start_donor_coverage.tsv")[1]
    assert row[5:8] == ["0", "1", "ROWS_RETRIEVED"]
    assert table(staging / "08_start_evidence_inventory.tsv")[1][3] == "NOT"


def test_start_end_authorizations_are_independent(tmp_path):
    index = donors(accepted(), accepted(release="E", accession="B"))
    start, staging = fixture(tmp_path, rows=[gaf_row("A"), gaf_row("B")])
    result = collect_one_normalized_gaf(gaf=start, donors=index, staging_dir=staging)
    assert result.retrieved_source_rows == 1
    assert result.donor_yes_associations == 1
    rows = table(staging / "08_start_source_assertions.tsv")
    assert {row[1] for row in rows[1:]} == {"S"}


def test_parity_with_existing_gaf_parser(tmp_path):
    rows = [
        gaf_row(qualifier="NOT|enables", reference="PMID:1|GO_REF:2", form="iso-1"),
        gaf_row(code="FUTURE_CODE_X", qualifier="involved_in", aspect="P"),
    ]
    gaf, staging = fixture(tmp_path, rows=rows)
    parsed = list(GafParser().iter_records(gaf.source.path))
    assert len(parsed) == 2
    collect_one_normalized_gaf(gaf=gaf, donors=donors(accepted()), staging_dir=staging)
    recovered = table(staging / "08_start_source_assertions.tsv")
    assert [row[4] for row in recovered[1:]] == [item.subject_id for item in parsed]
    assert [row[9] for row in recovered[1:]] == [item.evidence for item in parsed]
    assert ["NOT" in row[6] for row in recovered[1:]] == [
        item.negated for item in parsed
    ]


def test_same_size_sha_mutation_fails_without_coverage(tmp_path):
    gaf, staging = fixture(tmp_path, rows=[gaf_row()])
    path = Path(gaf.source.path)
    path.write_bytes(path.read_bytes().replace(b"IDA", b"IMP"))
    with pytest.raises(ValueError, match="SHA256"):
        collect_one_normalized_gaf(
            gaf=gaf, donors=donors(accepted()), staging_dir=staging
        )
    assert not (staging / "08_start_scan_summary.json").exists()
    assert not (staging / "08_start_donor_coverage.tsv").exists()


def test_wrong_size_fails(tmp_path):
    gaf, staging = fixture(tmp_path, rows=[gaf_row()])
    gaf = replace(gaf, size_bytes=gaf.size_bytes + 1)
    with pytest.raises(ValueError, match="size differs"):
        collect_one_normalized_gaf(
            gaf=gaf, donors=donors(accepted()), staging_dir=staging
        )
    assert not (staging / "08_start_scan_summary.json").exists()


@pytest.mark.parametrize("version", [False, True])
def test_missing_or_unsupported_header(tmp_path, version):
    gaf, staging = fixture(tmp_path, rows=[gaf_row()], version=version)
    if version:
        path = Path(gaf.source.path)
        path.write_text(path.read_text().replace("2.2", "1.0"))
        gaf = replace(
            gaf,
            source=replace(
                gaf.source,
                sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
            ),
        )
    with pytest.raises(ValueError, match="version|header"):
        collect_one_normalized_gaf(
            gaf=gaf, donors=donors(accepted()), staging_dir=staging
        )
    assert not (staging / "08_start_donor_coverage.tsv").exists()


def test_malformed_matching_row_fails(tmp_path):
    gaf, staging = fixture(tmp_path, rows=["UniProtKB\tA\tbad\n"])
    with pytest.raises(ValueError, match="column count"):
        collect_one_normalized_gaf(
            gaf=gaf, donors=donors(accepted()), staging_dir=staging
        )
    assert not (staging / "08_start_scan_summary.json").exists()


def test_staging_must_be_empty(tmp_path):
    gaf, staging = fixture(tmp_path, rows=[gaf_row()])
    (staging / "old").write_text("keep")
    with pytest.raises(ValueError, match="empty"):
        collect_one_normalized_gaf(
            gaf=gaf, donors=donors(accepted()), staging_dir=staging
        )
    assert (staging / "old").read_text() == "keep"


def test_role_mismatch_fails_before_scan(tmp_path):
    gaf, staging = fixture(tmp_path, rows=[gaf_row()], role="END")
    with pytest.raises(ValueError, match="role/release"):
        collect_one_normalized_gaf(
            gaf=gaf, donors=donors(accepted()), staging_dir=staging
        )


def test_no_donors_still_certifies_empty_coverage(tmp_path):
    gaf, staging = fixture(tmp_path, rows=[gaf_row()])
    result = collect_one_normalized_gaf(gaf=gaf, donors=donors(), staging_dir=staging)
    assert result.retrieved_source_rows == 0
    assert result.donor_yes_without_rows == 0
    assert result.coverage_certified
    assert len(table(staging / "08_start_donor_coverage.tsv")) == 1


def test_physical_line_ids_preserved(tmp_path):
    gaf, staging = fixture(
        tmp_path,
        rows=["!comment\n", "\n", gaf_row(), "!another comment\n", gaf_row()],
    )
    collect_one_normalized_gaf(gaf=gaf, donors=donors(accepted()), staging_dir=staging)
    rows = table(staging / "08_start_source_assertions.tsv")
    assert [row[2] for row in rows[1:]] == ["4", "6"]
    assert rows[1][0] != rows[2][0]


def test_no_overwrite_of_completed_outputs(tmp_path):
    gaf, staging = fixture(tmp_path, rows=[gaf_row()])
    index = donors(accepted())
    collect_one_normalized_gaf(gaf=gaf, donors=index, staging_dir=staging)
    with pytest.raises(ValueError, match="empty"):
        collect_one_normalized_gaf(gaf=gaf, donors=index, staging_dir=staging)


def test_same_accession_yes_and_no_associations(tmp_path):
    index = donors(
        accepted(target="T1", donor="YES"),
        accepted(target="T2", donor="NO"),
    )
    gaf, staging = fixture(tmp_path, rows=[gaf_row()])
    result = collect_one_normalized_gaf(gaf=gaf, donors=index, staging_dir=staging)
    assert result.annotation_links == 1
    assert table(staging / "08_start_annotation_links.tsv")[1][1] == "T1"
    coverage = table(staging / "08_start_donor_coverage.tsv")
    assert coverage[2][7] == "DONOR_DISABLED"


def test_corrupt_utf8_matched_row_does_not_certify(tmp_path):
    good = gaf_row().encode("utf-8")
    broken = good.replace(b"quoted", b"\xff\xff\xff\xff\xff\xff")
    path = tmp_path / "goa.normalized.gaf"
    payload = b"!gaf-version: 2.2\n" + broken
    path.write_bytes(payload)
    digest = hashlib.sha256(payload).hexdigest()
    gaf = GafCollectionInput(
        "START",
        NormalizedGafSource("S", str(path), digest),
        len(payload),
        Artifact("manifest.json", 1, "f" * 64),
        "policy/manifest.json",
    )
    staging = tmp_path / "stage"
    staging.mkdir()
    with pytest.raises(UnicodeDecodeError):
        collect_one_normalized_gaf(
            gaf=gaf, donors=donors(accepted()), staging_dir=staging
        )
    assert not (staging / "08_start_scan_summary.json").exists()


def test_missing_final_newline_is_not_lost(tmp_path):
    gaf, staging = fixture(tmp_path, rows=[gaf_row().rstrip("\n")])
    result = collect_one_normalized_gaf(
        gaf=gaf, donors=donors(accepted()), staging_dir=staging
    )
    assert result.retrieved_source_rows == 1
    assert result.physical_lines == 2
