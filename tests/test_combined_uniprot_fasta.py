from __future__ import annotations

import gzip
import hashlib
import json
import zipfile
from pathlib import Path

import pytest

from probe.data_acquisition import (
    AcquisitionError,
    acquire_configured_snapshots,
    generate_combined_uniprot_fasta,
    load_acquisition_config,
    validate_snapshot,
)
from probe.parsing.uniprot import UniProtDatParser

SWISS_PROT_RECORD = """\
ID   TEST_HUMAN              Reviewed;          6 AA.
AC   P12345; Q11111;
DE   RecName: Full=Synthetic Swiss-Prot protein;
GN   Name=GENE1;
OX   NCBI_TaxID=9606;
SQ   SEQUENCE   6 AA;  600 MW;  ABCDEF CRC64;
     ACDEFG         6
//
"""

TREMBL_RECORD = """\
ID   TEST_ECOLI              Unreviewed;        6 AA.
AC   A0A000;
DE   RecName: Full=Synthetic TrEMBL protein;
DE   Flags: Fragment;
OX   NCBI_TaxID=83333;
SQ   SEQUENCE   6 AA;  600 MW;  FEDCBA CRC64;
     MNPQRS         6
//
"""


def _write_taxonomy(path: Path) -> None:
    with zipfile.ZipFile(path, "w") as archive:
        for name in ("nodes.dmp", "names.dmp", "merged.dmp", "delnodes.dmp"):
            archive.writestr(name, f"synthetic {name}\n")


def _config(tmp_path: Path) -> tuple[object, Path]:
    source = tmp_path / "source"
    source.mkdir()
    swiss = source / "swiss.dat.gz"
    trembl = source / "trembl.dat.gz"
    goa = source / "goa.gaf.gz"
    taxonomy = source / "taxonomy.zip"
    swiss.write_bytes(
        gzip.compress(
            (
                SWISS_PROT_RECORD
                + SWISS_PROT_RECORD.replace("TEST_HUMAN", "TEST_MOUSE").replace(
                    "P12345; Q11111;", "P99999;"
                )
            ).encode()
        )
    )
    trembl.write_bytes(gzip.compress(TREMBL_RECORD.encode()))
    goa.write_bytes(gzip.compress(b"!gaf-version: 2.2\n"))
    _write_taxonomy(taxonomy)
    data_root = tmp_path / "data"
    config_path = tmp_path / "acquisition.toml"
    config_path.write_text(
        f'''data_root = "{data_root}"
minimum_free_bytes = 0

[snapshots.start.uniprot]
release = "synthetic"
mode = "direct"
[snapshots.start.uniprot.swissprot]
url = "{swiss.as_uri()}"
[snapshots.start.uniprot.trembl]
url = "{trembl.as_uri()}"
[snapshots.start.goa]
release = "synthetic"
url = "{goa.as_uri()}"
[snapshots.start.taxonomy]
snapshot_date = "synthetic"
url = "{taxonomy.as_uri()}"

[snapshots.end.uniprot]
release = "synthetic-end"
mode = "direct"
[snapshots.end.uniprot.swissprot]
url = "{swiss.as_uri()}"
[snapshots.end.uniprot.trembl]
url = "{trembl.as_uri()}"
[snapshots.end.goa]
release = "synthetic-end"
url = "{goa.as_uri()}"
[snapshots.end.taxonomy]
snapshot_date = "synthetic-end"
url = "{taxonomy.as_uri()}"
''',
        encoding="utf-8",
    )
    return load_acquisition_config(config_path), data_root


def test_combined_fasta_is_streamed_deterministic_and_recorded_in_manifest(tmp_path):
    config, data_root = _config(tmp_path)
    acquire_configured_snapshots(config, roles=("start",))

    result = generate_combined_uniprot_fasta(config, "start")
    output = result.output_path
    assert output == data_root / "start/uniprot/derived/combined.fasta.gz"
    assert result.record_count == 3
    with gzip.open(output, "rt", encoding="utf-8") as handle:
        assert handle.read() == (
            ">sp|P12345|TEST_HUMAN OX=9606\n"
            "ACDEFG\n"
            ">sp|P99999|TEST_MOUSE OX=9606\n"
            "ACDEFG\n"
            ">tr|A0A000|TEST_ECOLI OX=83333\n"
            "MNPQRS\n"
        )
    first_bytes = output.read_bytes()
    assert (
        generate_combined_uniprot_fasta(config, "start").sha256
        == hashlib.sha256(first_bytes).hexdigest()
    )
    assert output.read_bytes() == first_bytes

    manifest = json.loads((data_root / "manifests/start.json").read_text())
    (derived,) = manifest["derived"]
    assert derived["artifact"] == "uniprot_combined_fasta"
    assert derived["generation_status"] == "complete"
    assert derived["record_count"] == 3
    assert derived["sha256"] == hashlib.sha256(first_bytes).hexdigest()
    assert [source["section"] for source in derived["source_files"]] == [
        "Swiss-Prot",
        "TrEMBL",
    ]

    acquire_configured_snapshots(config, roles=("start",))
    preserved = json.loads((data_root / "manifests/start.json").read_text())
    assert preserved["derived"] == [derived]
    assert "verified start/uniprot_combined_fasta" in validate_snapshot(config, "start")


def test_failed_combined_fasta_generation_leaves_no_valid_artifact(
    tmp_path, monkeypatch
):
    config, data_root = _config(tmp_path)
    acquire_configured_snapshots(config, roles=("start",))
    original_iter_records = UniProtDatParser.iter_records

    def failing_trembl(self, *args, **kwargs):
        if self.section.value == "TrEMBL":
            raise AcquisitionError("synthetic interrupted generation")
        yield from original_iter_records(self, *args, **kwargs)

    monkeypatch.setattr(UniProtDatParser, "iter_records", failing_trembl)

    with pytest.raises(AcquisitionError, match="interrupted"):
        generate_combined_uniprot_fasta(config, "start")

    assert not (data_root / "start/uniprot/derived/combined.fasta.gz").exists()
    assert not (data_root / "start/uniprot/derived/combined.fasta.gz.part").exists()
    manifest = json.loads((data_root / "manifests/start.json").read_text())
    assert manifest["derived"] == []
