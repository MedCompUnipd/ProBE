"""Real scanner -> canonical ledger: verified literal FRAG, no similarity route."""

from __future__ import annotations

import csv
import hashlib
import shutil
import subprocess
from pathlib import Path

import pytest

from probe.canonical_v2.candidate_assembly import assemble_candidate_provenance
from probe.canonical_v2.contracts import (
    GO_SOURCE_REQUIRED,
    LINEAGE_REQUIRED,
    RESCUE_REQUIRED,
)
from probe.canonical_v2.exact_documentary_registry import (
    build_exact_documentary_register,
)
from probe.canonical_v2.primary_projection import (
    OUTPUT_NAME as PROJECTED,
)
from probe.canonical_v2.primary_projection import (
    project_primary_candidate_facts,
)
from probe.stage2c_candidate_union import merge_discovery
from probe.workflow.artifacts import Artifact, StageManifest

SEQ = "ACDBJXZEFGHIKLMNPQRS"


def write_table(path: Path, fields, records=()):
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(
            stream,
            fieldnames=list(fields),
            delimiter="\t",
            lineterminator="\n",
        )
        writer.writeheader()
        writer.writerows(records)


def read_table(path: Path):
    with path.open(encoding="utf-8", newline="") as stream:
        return list(csv.DictReader(stream, delimiter="\t"))


@pytest.fixture(scope="module")
def binary(tmp_path_factory):
    compiler = shutil.which("g++")
    if compiler is None:
        pytest.skip("C++20 compiler unavailable")
    root = Path(__file__).resolve().parents[1]
    executable = tmp_path_factory.mktemp("frag-full-exact") / "scanner"
    subprocess.run(
        [
            compiler,
            "-std=c++20",
            "-O1",
            str(root / "cpp/src/probe_full_exact_cross_strain_v1.cpp"),
            "-o",
            str(executable),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    return executable


def make_inputs(tmp: Path):
    target = tmp / "targets.tsv"
    write_table(
        target,
        ["target_id", "normalized_sequence"],
        [
            {"target_id": "T", "normalized_sequence": SEQ},
        ],
    )
    tax = tmp / "taxonomy.tsv"
    write_table(
        tax,
        [
            "target_id",
            "resolved_taxid",
            "species_taxid",
            "primary_bucket",
        ],
        [
            {
                "target_id": "T",
                "resolved_taxid": "111",
                "species_taxid": "100",
                "primary_bucket": "S:100",
            },
        ],
    )
    ox = tmp / "ox.tsv"
    write_table(
        ox,
        ["raw_ox", "resolved_taxid", "species_taxid", "species_bucket"],
        [
            {
                "raw_ox": "111",
                "resolved_taxid": "111",
                "species_taxid": "100",
                "species_bucket": "S:100",
            },
            {
                "raw_ox": "222",
                "resolved_taxid": "222",
                "species_taxid": "100",
                "species_bucket": "S:100",
            },
            {
                "raw_ox": "333",
                "resolved_taxid": "333",
                "species_taxid": "333",
                "species_bucket": "S:333",
            },
        ],
    )
    fasta_dir = tmp / "full"
    fasta_dir.mkdir()
    fasta = fasta_dir / "01A_full_v2.fasta"
    contents = []
    for rid, accession, ox_id, frag, seq in (
        (1, "A1", "111", "1", SEQ),
        (2, "A2", "111", "1", SEQ[:-1] + "A"),
        (3, "A3", "222", "1", SEQ),
        (4, "A4", "111", "0", SEQ),
        (5, "A5", "333", "1", SEQ),
    ):
        contents.append(
            f">sp|{accession}|ENTRY{rid} RID={rid} OX={ox_id} "
            f"FRAG={frag} LEN={len(seq)} PE=1 SV=1 GENE=L:LOC{rid}\n"
            f"{seq}\n"
        )
    fasta.write_text("".join(contents), encoding="ascii")
    digest = hashlib.sha256(fasta.read_bytes()).hexdigest()
    StageManifest(
        stage="01A_FULL",
        role="START",
        release_id="R1",
        parameters={},
        ontology=None,
        implementation={"name": "test", "version": "1"},
        inputs=(),
        outputs=(Artifact(fasta.name, fasta.stat().st_size, digest),),
    ).write(fasta_dir / "manifest.json")
    return target, tax, ox, fasta, fasta_dir / "manifest.json"


def scanner_run(binary: Path, base: Path, fragment_policy: str):
    base.mkdir()
    target, tax, ox, fasta, manifest = make_inputs(base)
    output = base / "pairs.tsv"
    summary = base / "summary.tsv"
    subprocess.run(
        [
            str(binary),
            "--targets",
            str(target),
            "--target-taxonomy",
            str(tax),
            "--ox-taxonomy",
            str(ox),
            "--fasta",
            str(fasta),
            "--output",
            str(output),
            "--summary",
            str(summary),
            "--release-id",
            "R1",
            "--fragment-policy",
            fragment_policy,
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    return target, tax, output, summary, manifest


def test_user_explicit_fragment_selection(binary: Path, tmp_path: Path):
    *_, pairs, summary, _ = scanner_run(binary, tmp_path / "no", "exclude")
    assert [r["accession"] for r in read_table(pairs)] == ["A4"]
    assert dict(csv.reader(summary.open(), delimiter="\t"))["fragments_skipped"] == "4"
    *_, pairs, summary, _ = scanner_run(binary, tmp_path / "yes", "include")
    rows = read_table(pairs)
    assert {r["accession"] for r in rows} == {"A1", "A3", "A4"}
    assert {r["frag"] for r in rows if r["accession"] != "A4"} == {"1"}
    assert all(r["exact_full_sequence"] == "1" for r in rows)
    assert dict(csv.reader(summary.open(), delimiter="\t"))["fragments_skipped"] == "0"


def test_literal_fragment_reaches_primary_and_exact_register(binary, tmp_path):
    target, tax, pairs, _, manifest = scanner_run(binary, tmp_path / "case", "include")
    base = tmp_path / "case"
    go, lineage, rescue = (
        base / name for name in ("go.tsv", "lineage.tsv", "rescue.tsv")
    )
    write_table(go, sorted(GO_SOURCE_REQUIRED))
    write_table(lineage, sorted(LINEAGE_REQUIRED))
    write_table(rescue, sorted(RESCUE_REQUIRED))
    merged = base / "merged.tsv"
    merge_discovery(
        release="R1",
        target_taxonomy=tax,
        go_positive_pairs=go,
        full_exact_pairs=pairs,
        output=merged,
        summary=base / "merged.json",
        allow_literal_fragments=True,
    )
    assert len(read_table(merged)) == 3
    with pytest.raises(ValueError, match="FRAG=1"):
        merge_discovery(
            release="R1",
            target_taxonomy=tax,
            go_positive_pairs=go,
            full_exact_pairs=pairs,
            output=base / "denied.tsv",
            summary=base / "denied.json",
        )
    ledger_dir = base / "ledger"
    assemble_candidate_provenance(
        release="R1",
        discovery_union=merged,
        go_positive_pairs=go,
        full_exact_pairs=pairs,
        lineage_pairs=lineage,
        rescue_pairs=rescue,
        output_dir=ledger_dir,
    )
    projected = base / "projection"
    project_primary_candidate_facts(
        release="R1",
        ledger_dir=ledger_dir,
        output_dir=projected,
    )
    data = read_table(projected / PROJECTED)
    assert len(data) == 3
    assert {r["rid"] for r in data if r["frag"] == "1"} == {"1", "3"}
    assert all(r["seq_exact_sequence"] == "1" for r in data)
    output_dir = base / "exact-register"
    summary = build_exact_documentary_register(
        release="R1",
        discovery=merged,
        targets=target,
        full_manifest=manifest,
        output_dir=output_dir,
    )
    assert summary["counts"]["literal_verified_fragment_exact"] == 2
    assert summary["counts"]["literal_verified_exact"] == 3
