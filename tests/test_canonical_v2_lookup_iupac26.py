"""Literal extended-alphabet candidates and selective documentary lookup."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from test_canonical_v2_documentary_lookup import _fixture, _rows

from probe.canonical_v2.documentary_lookup import (
    OUTPUT,
    discover_documentary_nonexact,
)


def _replace_record(paths: dict, original: bytes, replacement: bytes) -> None:
    manifest = paths["full_manifest"]
    fasta = manifest.parent / "01A_full_v2.fasta"
    payload = fasta.read_bytes()
    assert payload.count(original) == 1
    fasta.write_bytes(payload.replace(original, replacement))
    data = json.loads(manifest.read_text())
    output = data["outputs"][0]
    output["sha256"] = hashlib.sha256(fasta.read_bytes()).hexdigest()
    output["size_bytes"] = fasta.stat().st_size
    manifest.write_text(json.dumps(data, sort_keys=True, indent=2) + "\n")


@pytest.mark.parametrize("symbol", "BJXZ")
def test_ambiguous_literal_documented_nonexact_is_discoverable(
    tmp_path: Path,
    symbol: str,
) -> None:
    paths = _fixture(tmp_path)
    _replace_record(paths, b"\nACDF\n", b"\nA" + symbol.encode() + b"DF\n")
    report = discover_documentary_nonexact(**paths)
    rows = _rows(paths["destination"] / OUTPUT)
    found = [row for row in rows if row["candidate_rid"] == "3"]
    assert any(row["match_class"] == "LITERAL" for row in found)
    assert report["count"]["ambiguous_candidate_records"] == 1
    assert report["ambiguous_symbols_are_wildcards"] is False
    assert report["nonexact_dp_authorized"] == 0


@pytest.mark.parametrize("symbol", [b"*", b"x", b"\xff"])
def test_invalid_symbols_do_not_become_documentary_candidates(
    tmp_path: Path,
    symbol: bytes,
) -> None:
    paths = _fixture(tmp_path)
    _replace_record(paths, b"\nACDF\n", b"\nA" + symbol + b"DF\n")
    report = discover_documentary_nonexact(**paths)
    assert report["count"]["invalid_alphabet_candidate_records"] == 1
    assert not any(
        row["candidate_rid"] == "3" for row in _rows(paths["destination"] / OUTPUT)
    )


def test_ambiguous_record_in_inactive_taxon_remains_outside_lookup(
    tmp_path: Path,
) -> None:
    paths = _fixture(tmp_path)
    _replace_record(paths, b"\nACDI\n", b"\nAXDI\n")
    report = discover_documentary_nonexact(**paths)
    assert report["count"]["ambiguous_candidate_records"] == 1
    assert report["count"]["full_records_scanned"] == 7
    assert not any(
        row["candidate_rid"] == "7" for row in _rows(paths["destination"] / OUTPUT)
    )


def test_inactive_taxon_still_participates_in_full_integrity_check(
    tmp_path: Path,
) -> None:
    paths = _fixture(tmp_path)
    fasta = paths["full_manifest"].parent / "01A_full_v2.fasta"
    fasta.write_bytes(fasta.read_bytes().replace(b"ACDI\n", b"AXDI\n"))
    with pytest.raises(ValueError, match="SHA256"):
        discover_documentary_nonexact(**paths)
    assert not paths["destination"].exists()
