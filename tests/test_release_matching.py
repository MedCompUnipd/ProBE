from __future__ import annotations

import csv
import hashlib
from dataclasses import replace

from probe.parsing.uniprot import UniProtDatParser
from probe.records import UniProtSection
from probe.release_matching import match_prepared_targets
from probe.taxonomy import NcbiTaxonomyIndex


def _record(
    accession: str,
    sequence: str,
    taxid: str,
    *,
    fragment: bool = False,
    secondary_accessions: tuple[str, ...] = (),
) -> str:
    flag = "DE   Flags: Fragment;\n" if fragment else ""
    return (
        f"ID   {accession}_ENTRY {'Reviewed' if not fragment else 'Unreviewed'};"
        f"{' ' * 10}{len(sequence)} AA.\n"
        f"AC   {'; '.join((accession, *secondary_accessions))};\n"
        f"{flag}"
        f"OX   NCBI_TaxID={taxid};\n"
        f"SQ   SEQUENCE   {len(sequence)} AA;  100 MW;  ABCDEF CRC64;\n"
        f"     {sequence}\n//\n"
    )


def _taxonomy(tmp_path) -> NcbiTaxonomyIndex:
    nodes = tmp_path / "nodes.dmp"
    names = tmp_path / "names.dmp"
    merged = tmp_path / "merged.dmp"
    delnodes = tmp_path / "delnodes.dmp"
    nodes.write_text(
        "1\t|\t1\t|\tno rank\t|\n"
        "9605\t|\t1\t|\tgenus\t|\n"
        "9606\t|\t9605\t|\tspecies\t|\n"
        "123\t|\t9606\t|\tstrain\t|\n"
        "10088\t|\t1\t|\tgenus\t|\n"
        "10090\t|\t10088\t|\tspecies\t|\n",
        encoding="utf-8",
    )
    names.write_text(
        "1\t|\troot\t|\t\t|\tscientific name\t|\n"
        "9606\t|\tHomo sapiens\t|\t\t|\tscientific name\t|\n"
        "10090\t|\tMus musculus\t|\t\t|\tscientific name\t|\n",
        encoding="utf-8",
    )
    merged.write_text("999\t|\t9606\t|\n", encoding="utf-8")
    delnodes.write_text("666\t|\n", encoding="utf-8")
    return NcbiTaxonomyIndex.build(
        tmp_path / "taxonomy.sqlite3",
        nodes=nodes,
        names=names,
        merged=merged,
        delnodes=delnodes,
    )


def _targets(
    tmp_path,
    rows: list[tuple[str, str, str]],
    *,
    optional_accessions: dict[str, str] | None = None,
) -> object:
    path = tmp_path / "01_targets.tsv"
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        writer = csv.writer(handle, delimiter="\t", lineterminator="\n")
        writer.writerow(
            (
                "target_id",
                "raw_taxid",
                "optional_accession",
                "normalized_sequence",
                "sequence_length",
                "sequence_sha256",
                "normalization_policy_version",
            )
        )
        for target_id, taxid, sequence in rows:
            writer.writerow(
                (
                    target_id,
                    taxid,
                    (optional_accessions or {}).get(target_id, ""),
                    sequence,
                    len(sequence),
                    hashlib.sha256(sequence.encode()).hexdigest(),
                    "1",
                )
            )
    return path


def _read(path):
    with path.open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle, delimiter="\t"))


def test_start_matching_is_single_pass_and_keeps_all_strict_records(
    tmp_path, monkeypatch
):
    targets = _targets(
        tmp_path,
        [
            ("same", "9606", "AAA"),
            ("subtaxon", "123", "BBB"),
            ("cross", "9606", "CCC"),
            ("merged", "999", "DDD"),
            ("deleted", "666", "EEE"),
            ("unresolved", "404", "FFF"),
            ("fragment", "9606", "GGG"),
            ("none", "9606", "HHH"),
            ("record-unresolved", "9606", "III"),
        ],
        optional_accessions={"same": "P1"},
    )
    swiss = tmp_path / "sprot.dat"
    trembl = tmp_path / "trembl.dat"
    swiss.write_text(
        _record("P1", "AAA", "9606")
        + _record("P2", "AAA", "9606")
        + _record("P3", "BBB", "9606")
        + _record("P4", "CCC", "10090")
        + _record("P5", "DDD", "9606")
        + _record("P8", "AAA", "999")
        + _record("P6", "GG", "9606", fragment=True)
        + _record("P7", "III", "404"),
        encoding="utf-8",
    )
    trembl.write_text(_record("A0A1", "AAA", "9606"), encoding="utf-8")
    calls: list[UniProtSection] = []
    original = UniProtDatParser.iter_records

    def tracked(self, *args, **kwargs):
        calls.append(self.section)
        yield from original(self, *args, **kwargs)

    monkeypatch.setattr(UniProtDatParser, "iter_records", tracked)
    with _taxonomy(tmp_path) as taxonomy:
        result = match_prepared_targets(
            prepared_targets_tsv=targets,
            sources={UniProtSection.SWISS_PROT: swiss, UniProtSection.TREMBL: trembl},
            taxonomy=taxonomy,
            output_directory=tmp_path / "output",
        )

    assert calls == [UniProtSection.SWISS_PROT, UniProtSection.TREMBL]
    matches = _read(result.matches_path)
    assert [
        (row["target_id"], row["uniprot_primary_accession"]) for row in matches
    ] == [
        ("same", "P1"),
        ("same", "P2"),
        ("merged", "P5"),
        ("same", "P8"),
        ("same", "A0A1"),
    ]
    assert (
        matches[2]["target_resolved_taxid"]
        == matches[2]["uniprot_resolved_taxid"]
        == "9606"
    )
    assert {row["status"] for row in _read(result.issues_path)} >= {
        "TARGET_TAXID_DELETED",
        "TARGET_TAXID_UNRESOLVED",
        "NON_STRICT_TAXONOMIC_RELATIONSHIP",
        "NO_STRICT_START_MATCH",
    }
    summary = __import__("json").loads(result.summary_path.read_text(encoding="utf-8"))
    assert summary["same_taxon_admitted_matches"] == 5
    assert summary["same_species_different_subtaxon_candidates"] == 1
    assert summary["cross_species_candidates"] == 1
    assert summary["unresolved_record_taxonomy_candidates"] == 1
    assert summary["fragment_records_skipped"] == 1
    assert summary["targets_without_strict_match"] == 5
    assert matches[0]["optional_accession_match"] == "PRIMARY"
    assert matches[1]["optional_accession_match"] == "MISMATCH"


def test_start_matching_requires_literal_sequence_after_hash_candidate(
    tmp_path, monkeypatch
):
    targets = _targets(tmp_path, [("target", "9606", "AAA")])
    swiss = tmp_path / "sprot.dat"
    trembl = tmp_path / "trembl.dat"
    swiss.write_text(_record("P1", "AAA", "9606"), encoding="utf-8")
    trembl.write_text(_record("A0A1", "AAA", "9606"), encoding="utf-8")
    original = UniProtDatParser.iter_records

    def collision(self, *args, **kwargs):
        for record in original(self, *args, **kwargs):
            yield replace(record, sequence="BBB")

    monkeypatch.setattr(UniProtDatParser, "iter_records", collision)
    with _taxonomy(tmp_path) as taxonomy:
        result = match_prepared_targets(
            prepared_targets_tsv=targets,
            sources={UniProtSection.SWISS_PROT: swiss, UniProtSection.TREMBL: trembl},
            taxonomy=taxonomy,
            output_directory=tmp_path / "output",
        )

    assert _read(result.matches_path) == []
    assert [row["status"] for row in _read(result.issues_path)] == [
        "LITERAL_SEQUENCE_MISMATCH",
        "LITERAL_SEQUENCE_MISMATCH",
        "NO_STRICT_START_MATCH",
    ]


def test_fragment_hook_receives_different_length_fragment_in_the_single_pass(tmp_path):
    targets = _targets(tmp_path, [("target", "9606", "ABCDEFGHIJ")])
    swiss = tmp_path / "sprot.dat"
    trembl = tmp_path / "trembl.dat"
    swiss.write_text(_record("P1", "ABCDE", "9606", fragment=True), encoding="utf-8")
    trembl.write_text("", encoding="utf-8")

    class TrackingFragmentMatcher:
        def __init__(self) -> None:
            self.records: list[tuple[str, int]] = []

        def examine(self, record) -> None:
            self.records.append((record.primary_accession, record.sequence_length))

    fragment_matcher = TrackingFragmentMatcher()
    with _taxonomy(tmp_path) as taxonomy:
        result = match_prepared_targets(
            prepared_targets_tsv=targets,
            sources={UniProtSection.SWISS_PROT: swiss, UniProtSection.TREMBL: trembl},
            taxonomy=taxonomy,
            output_directory=tmp_path / "output",
            fragment_matcher=fragment_matcher,
        )

    assert fragment_matcher.records == [("P1", 5)]
    assert _read(result.matches_path) == []
    assert [row["status"] for row in _read(result.issues_path)] == [
        "NO_STRICT_START_MATCH"
    ]


def test_optional_secondary_accession_is_audit_only(tmp_path):
    targets = _targets(
        tmp_path,
        [("target", "9606", "AAA")],
        optional_accessions={"target": "Q1"},
    )
    swiss = tmp_path / "sprot.dat"
    trembl = tmp_path / "trembl.dat"
    swiss.write_text(
        _record("P1", "AAA", "9606", secondary_accessions=("Q1",)),
        encoding="utf-8",
    )
    trembl.write_text(_record("A0A1", "AAA", "9606"), encoding="utf-8")

    with _taxonomy(tmp_path) as taxonomy:
        result = match_prepared_targets(
            prepared_targets_tsv=targets,
            sources={UniProtSection.SWISS_PROT: swiss, UniProtSection.TREMBL: trembl},
            taxonomy=taxonomy,
            output_directory=tmp_path / "output",
        )

    matches = _read(result.matches_path)
    assert [
        (row["uniprot_primary_accession"], row["optional_accession_match"])
        for row in matches
    ] == [
        ("P1", "SECONDARY"),
        ("A0A1", "MISMATCH"),
    ]
