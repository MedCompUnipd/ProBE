"""Synthetic integration tests for isolated Stage 2C alignment previews."""

from __future__ import annotations

import csv
import hashlib
import json
from pathlib import Path

import pytest

from probe.stage2c_alignment_preview import (
    AUDIT_OUT,
    END_STORE,
    JOBS_OUT,
    JOBS_SUMMARY,
    START_STORE,
    SUMMARY_OUT,
    UNION_OUT,
    build_alignment_preview,
)
from probe.stage2c_go_sequence_recovery import (
    ANNEX_NAME as GO_ANNEX,
)
from probe.stage2c_go_sequence_recovery import (
    SUMMARY_NAME as GO_SUMMARY,
)
from probe.stage2c_metadata_reuse import STORE_FIELDS
from probe.stage2c_primary_projection import (
    ADDITIONS_NAME,
    AUDIT_NAME,
    OUTPUT_NAME,
)
from probe.stage2c_primary_projection import (
    SUMMARY_NAME as PROJECTION_SUMMARY,
)
from probe.stage2c_sequence_coverage import (
    ANNEX_FIELDS,
    ANNEX_NAME,
    COVERAGE_FIELDS,
    COVERAGE_NAME,
    PENDING_FIELDS,
    PENDING_NAME,
    SUMMARY_NAME_OUT,
)

BASE = [
    "release",
    "target_id",
    "rid",
    "seq_exact_sequence",
    "sequence_discovered",
    "lineage_discovered",
    "discovery_channel",
    "candidate_primary_accession",
    "entry",
    "raw_ox",
    "frag",
    "sequence_length",
    "pe",
    "sv",
    "sec",
    "gene",
]
AUDIT = [
    "release",
    "target_id",
    "rid",
    "audit_alignment_allowed",
    "record_already_in_primary_union_for_target",
    "target_has_primary_candidate",
    "audit_class",
    "key_source_reason",
]
RELEASES = ("2023_01", "2026_03")


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _table(path: Path, header, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(
            stream, fieldnames=header, delimiter="\t", lineterminator="\n"
        )
        writer.writeheader()
        writer.writerows(rows)


def _summary(path: Path, schema: str, release: str, files, **other):
    info = {
        "schema": schema,
        "release": release,
        "go_donor_decisions": 0,
        "counts": {},
        "output_sha256": {name: _sha(path.parent / name) for name in files},
        **other,
    }
    path.write_text(json.dumps(info), encoding="utf-8")


def _primary(release, rid, accession="A", exact="1"):
    return {
        "release": release,
        "target_id": "target-without-accession",
        "rid": rid,
        "seq_exact_sequence": exact,
        "sequence_discovered": "1",
        "lineage_discovered": "0",
        "discovery_channel": "STAGE2C_SPECIES_WIDE",
        "candidate_primary_accession": accession,
        "entry": "E",
        "raw_ox": "562",
        "frag": "0",
        "sequence_length": "3",
        "pe": "1",
        "sv": "1",
        "sec": "",
        "gene": "",
    }


def _sequence(release, rid, accession="A", sequence="ACD"):
    row = {
        "release": release,
        "full_v2_record_number": rid,
        "rid": rid,
        "primary_accession": accession,
        "entry": "E",
        "raw_ox": "562",
        "frag": "0",
        "sequence_length": str(len(sequence)),
        "pe": "1",
        "sv": "1",
        "sec": "",
        "gene": "",
        "needed_by_primary": "1",
        "needed_by_taxonomy_audit": "0",
        "primary_reference_count": "1",
        "audit_reference_count": "0",
        "sequence": sequence,
    }
    return row


def _annex(release, rid, accession="A", sequence="ACD", state="FULL"):
    row = _sequence(release, rid, accession, sequence)
    return {
        **{key: row[key] for key in ANNEX_FIELDS if key in row},
        "sequence_sha256": hashlib.sha256(sequence.encode()).hexdigest(),
        "source_state": state,
    }


def _prepared(tmp_path: Path):
    inp = tmp_path / "input"
    inp.mkdir()
    out = tmp_path / "result"
    out.mkdir()
    canon = inp / "canonical.tsv"
    rows = [_primary(release, "1") for release in RELEASES]
    _table(canon, BASE, rows)
    audit = inp / "audit.tsv"
    _table(
        audit,
        AUDIT,
        [
            {
                "release": RELEASES[0],
                "target_id": "target-without-accession",
                "rid": "2",
                "audit_alignment_allowed": "1",
                "record_already_in_primary_union_for_target": "0",
                "target_has_primary_candidate": "1",
                "audit_class": "TAXONOMY_RESCUE_AUDIT",
                "key_source_reason": "SAME_SPECIES_WRONG_SUBTAXON",
            }
        ],
    )
    targets = inp / "targets.tsv"
    _table(
        targets,
        [
            "target_id",
            "optional_accession",
            "normalized_sequence",
            "sequence_length",
        ],
        [
            {
                "target_id": "target-without-accession",
                "optional_accession": "",
                "normalized_sequence": "ACD",
                "sequence_length": "3",
            }
        ],
    )
    stores = {}
    projections = {}
    coverage = {}
    gos = {}
    for release in RELEASES:
        store = inp / f"05d-{release}.tsv"
        existing = [_sequence(release, "1")]
        if release == RELEASES[0]:
            existing.append(_sequence(release, "2", accession="B"))
        _table(store, STORE_FIELDS, existing)
        stores[release] = store
        proj = inp / f"projection-{release}"
        proj.mkdir()
        new = (
            [_primary(release, "2", "B"), _primary(release, "4", "D")]
            if release == RELEASES[0]
            else [_primary(release, "3", "C")]
        )
        _table(proj / ADDITIONS_NAME, BASE, new)
        _table(proj / OUTPUT_NAME, BASE, [*rows, *new])
        _table(
            proj / AUDIT_NAME,
            ["release", "target_id", "rid"],
            [{key: row[key] for key in ("release", "target_id", "rid")} for row in new],
        )
        _summary(
            proj / PROJECTION_SUMMARY,
            "stage2c-primary-projection-v1",
            release,
            (OUTPUT_NAME, ADDITIONS_NAME, AUDIT_NAME),
        )
        projections[release] = proj
        cov = inp / f"coverage-{release}"
        cov.mkdir()
        states = (
            [("2", "B", "CANONICAL_05D"), ("4", "D", "PENDING_GO_SEQUENCE")]
            if release == RELEASES[0]
            else [("3", "C", "FULL_RECONCILED_NEW_RID")]
        )
        cover_rows = []
        for rid, acc, state in states:
            cover_rows.append(
                {
                    "release": release,
                    "rid": rid,
                    "candidate_accession": acc,
                    "source_state": state,
                    "new_primary_reference_count": "1",
                    "go_positive_reference_count": "1",
                    "full_exact_reference_count": "0",
                    "sequence_sha256": (
                        ""
                        if state == "PENDING_GO_SEQUENCE"
                        else hashlib.sha256(b"ACD").hexdigest()
                    ),
                    "sequence_length": "3",
                    "all_sequence_flags_verified": (
                        "0" if state == "PENDING_GO_SEQUENCE" else "1"
                    ),
                }
            )
        _table(cov / COVERAGE_NAME, COVERAGE_FIELDS, cover_rows)
        annex = (
            []
            if release == RELEASES[0]
            else [_annex(release, "3", "C", state="PINNED_FULL_V2_METADATA_RECOVERED")]
        )
        _table(cov / ANNEX_NAME, ANNEX_FIELDS, annex)
        pending = (
            []
            if release != RELEASES[0]
            else [
                {
                    "release": release,
                    "rid": "4",
                    "candidate_accession": "D",
                    "raw_ox": "562",
                    "go_positive_record_number": "3",
                    "new_primary_reference_count": "1",
                    "reason": "GO_POSITIVE_SEQUENCE_NOT_MATERIALIZED",
                }
            ]
        )
        _table(cov / PENDING_NAME, PENDING_FIELDS, pending)
        _summary(
            cov / SUMMARY_NAME_OUT,
            "stage2c-sequence-coverage-v1",
            release,
            (COVERAGE_NAME, ANNEX_NAME, PENDING_NAME),
            input_sha256={
                "projection_summary": _sha(proj / PROJECTION_SUMMARY),
                "canonical_05d_store": _sha(store),
            },
        )
        coverage[release] = cov
        go = inp / f"go-{release}"
        go.mkdir()
        go_rows = (
            []
            if release != RELEASES[0]
            else [_annex(release, "4", "D", state="PINNED_GO_POSITIVE_01C_RECOVERED")]
        )
        _table(go / GO_ANNEX, ANNEX_FIELDS, go_rows)
        _summary(
            go / GO_SUMMARY,
            "stage2c-go-pending-sequence-recovery-v1",
            release,
            (GO_ANNEX,),
            counts={"requested_unique_rids": len(go_rows)},
            input_sha256={"coverage_summary": _sha(cov / SUMMARY_NAME_OUT)},
        )
        gos[release] = go
    runner = Path.cwd() / "scripts/pipeline/06A_prepare_alignment_jobs.py"
    assert runner.is_file()
    return {
        "start_release": RELEASES[0],
        "end_release": RELEASES[1],
        "canonical_05a2": canon,
        "canonical_05c2": audit,
        "canonical_start_05d": stores[RELEASES[0]],
        "canonical_end_05d": stores[RELEASES[1]],
        "projection_start": projections[RELEASES[0]],
        "projection_end": projections[RELEASES[1]],
        "coverage_start": coverage[RELEASES[0]],
        "coverage_end": coverage[RELEASES[1]],
        "recovered_go_start": gos[RELEASES[0]],
        "recovered_go_end": gos[RELEASES[1]],
        "targets": targets,
        "stage06a_script": runner,
        "output_dir": out / "variant",
    }


def test_alignment_variant_with_audit_overlap(tmp_path):
    args = _prepared(tmp_path)
    report = build_alignment_preview(**args)
    assert report["donor_decisions"] == 0
    assert report["counts"]["primary"] == {
        "canonical": 2,
        RELEASES[0]: 2,
        RELEASES[1]: 1,
    }
    assert report["counts"]["stage_06a_jobs"] == 5
    rows = list(_read_rows(args["output_dir"] / JOBS_OUT))
    assert len(rows) == 5
    overlap = [row for row in rows if row["rid"] == "2"]
    assert len(overlap) == 1
    assert overlap[0]["job_scope"] == "PRIMARY"
    assert overlap[0]["audit_class"] == "TAXONOMY_RESCUE_AUDIT"
    audit = list(_read_rows(args["output_dir"] / AUDIT_OUT))
    assert audit[0]["record_already_in_primary_union_for_target"] == "1"
    assert len(list(_read_rows(args["output_dir"] / START_STORE))) == 3
    assert len(list(_read_rows(args["output_dir"] / END_STORE))) == 2
    assert len(list(_read_rows(args["output_dir"] / UNION_OUT))) == 5
    assert (
        json.loads((args["output_dir"] / JOBS_SUMMARY).read_text())["policy"][
            "alignment_made_here"
        ]
        is False
    )
    assert (args["output_dir"] / SUMMARY_OUT).is_file()


def _read_rows(path):
    with path.open(encoding="utf-8", newline="") as handle:
        yield from csv.DictReader(handle, delimiter="\t")


def test_no_overwrite(tmp_path):
    args = _prepared(tmp_path)
    build_alignment_preview(**args)
    with pytest.raises(FileExistsError):
        build_alignment_preview(**args)


@pytest.mark.parametrize("tamper", ["coverage", "go", "projection", "store"])
def test_hash_tamper_fails_closed(tmp_path, tamper):
    args = _prepared(tmp_path)
    path = {
        "coverage": args["coverage_start"] / COVERAGE_NAME,
        "go": args["recovered_go_start"] / GO_ANNEX,
        "projection": args["projection_start"] / ADDITIONS_NAME,
        "store": args["canonical_start_05d"],
    }[tamper]
    with path.open("a", encoding="utf-8") as out:
        out.write("bogus\n")
    with pytest.raises(ValueError):
        build_alignment_preview(**args)
    assert not args["output_dir"].exists()


def test_annex_sequence_tamper(tmp_path):
    args = _prepared(tmp_path)
    path = args["coverage_end"] / ANNEX_NAME
    with path.open("a", encoding="utf-8") as out:
        out.write("bogus\n")
    with pytest.raises(ValueError):
        build_alignment_preview(**args)
    assert not args["output_dir"].exists()


def test_prevent_unknown_release(tmp_path):
    args = _prepared(tmp_path)
    args["end_release"] = args["start_release"]
    with pytest.raises(ValueError):
        build_alignment_preview(**args)
    assert not args["output_dir"].exists()


def test_sequence_conflict_rejected_even_with_valid_source_hash(tmp_path):
    args = _prepared(tmp_path)
    go = args["recovered_go_start"]
    rows = list(_read_rows(go / GO_ANNEX))
    rows[0]["sequence"] = "ACE"
    rows[0]["sequence_sha256"] = hashlib.sha256(b"ACE").hexdigest()
    _table(go / GO_ANNEX, ANNEX_FIELDS, rows)
    summary = json.loads((go / GO_SUMMARY).read_text())
    summary["output_sha256"][GO_ANNEX] = _sha(go / GO_ANNEX)
    (go / GO_SUMMARY).write_text(json.dumps(summary), encoding="utf-8")
    with pytest.raises(RuntimeError, match="Canonical Stage 06A"):
        build_alignment_preview(**args)
    assert not args["output_dir"].exists()


def test_metadata_conflict_rejected_even_with_valid_hash(tmp_path):
    args = _prepared(tmp_path)
    directory = args["coverage_end"]
    rows = list(_read_rows(directory / ANNEX_NAME))
    rows[0]["entry"] = "OTHER"
    _table(directory / ANNEX_NAME, ANNEX_FIELDS, rows)
    summary = json.loads((directory / SUMMARY_NAME_OUT).read_text())
    summary["output_sha256"][ANNEX_NAME] = _sha(directory / ANNEX_NAME)
    (directory / SUMMARY_NAME_OUT).write_text(json.dumps(summary), encoding="utf-8")
    go = args["recovered_go_end"]
    go_summary = json.loads((go / GO_SUMMARY).read_text())
    go_summary["input_sha256"]["coverage_summary"] = _sha(directory / SUMMARY_NAME_OUT)
    (go / GO_SUMMARY).write_text(json.dumps(go_summary), encoding="utf-8")
    with pytest.raises(ValueError, match="metadata contradicts 05A2"):
        build_alignment_preview(**args)
    assert not args["output_dir"].exists()


def test_full_annex_same_as_canonical_not_duplicated(tmp_path):
    args = _prepared(tmp_path)
    cov = args["coverage_start"]
    rows = list(_read_rows(cov / COVERAGE_NAME))
    rows[0]["source_state"] = "FULL_RECONCILED_NEW_RID"
    _table(cov / COVERAGE_NAME, COVERAGE_FIELDS, rows)
    _table(
        cov / ANNEX_NAME,
        ANNEX_FIELDS,
        [_annex(RELEASES[0], "2", "B", state="CANONICAL_05D_METADATA_REUSED")],
    )
    summary = json.loads((cov / SUMMARY_NAME_OUT).read_text())
    summary["output_sha256"][COVERAGE_NAME] = _sha(cov / COVERAGE_NAME)
    summary["output_sha256"][ANNEX_NAME] = _sha(cov / ANNEX_NAME)
    (cov / SUMMARY_NAME_OUT).write_text(json.dumps(summary), encoding="utf-8")
    go = args["recovered_go_start"]
    go_summary = json.loads((go / GO_SUMMARY).read_text())
    go_summary["input_sha256"]["coverage_summary"] = _sha(cov / SUMMARY_NAME_OUT)
    (go / GO_SUMMARY).write_text(json.dumps(go_summary), encoding="utf-8")
    build_alignment_preview(**args)
    rows = list(_read_rows(args["output_dir"] / START_STORE))
    assert len(rows) == 3
    assert [row["rid"] for row in rows].count("2") == 1
