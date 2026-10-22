"""Synthetic transaction tests of the common START+END collection bundle."""

import hashlib
import json
from dataclasses import replace
from pathlib import Path

import pytest

from probe import stage08_collection_publish as publisher
from probe.stage08_collection_publish import publish_raw_annotation_collection
from probe.stage08_donors import ACCEPTED_COLUMNS, DonorAssociation
from probe.workflow.artifacts import (
    Artifact,
    atomic_stage_bundle,
    sha256_file,
    validate_bundle,
)


def _annotation(accession="A", code="IDA", qualifier="enables"):
    fields = (
        "UniProtKB",
        accession,
        "symbol",
        qualifier,
        "GO:0000001",
        "PMID:10",
        code,
        "WITH:1",
        "F",
        "Protein A",
        "",
        "protein",
        "taxon:1",
        "20230101",
        "SOURCE",
        "",
        "",
    )
    return "\t".join(fields) + "\n"


def _accepted(release, target, rid, accession, donor="YES", count="1"):
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


def _setup(tmp_path: Path, *, start_rows=None, end_rows=None):
    if start_rows is None:
        start_rows = [_annotation("A"), _annotation("B", "IEA")]
    if end_rows is None:
        end_rows = [
            _annotation("A", "IMP"),
            _annotation("B", "IDA", "NOT|enables"),
            _annotation("X", "new_code"),
        ]
    paths = {
        "stage07_bundle": tmp_path / "07",
        "go_policy_bundle": tmp_path / "01B0",
        "start_goa_bundle": tmp_path / "START-01B1",
        "end_goa_bundle": tmp_path / "END-01B1",
        "destination": tmp_path / "08",
    }
    impl = {"name": "synthetic", "version": "1"}
    ontology = {
        "sha256": hashlib.sha256(b"fixed ontology").hexdigest(),
        "original_url": None,
        "retrieval_date": None,
        "version_iri": None,
    }
    with atomic_stage_bundle(
        paths["go_policy_bundle"],
        stage="01B0",
        role=None,
        release_id=None,
        parameters={},
        ontology=ontology,
        implementation=impl,
    ) as stage:
        (stage / "01B0_go_policy.tsv").write_text("policy\n")
    policy_manifest = paths["go_policy_bundle"] / "manifest.json"

    reference = Artifact(
        str(policy_manifest.resolve()),
        policy_manifest.stat().st_size,
        sha256_file(policy_manifest),
    )
    for role, release, path_key, rows in (
        ("START", "S", "start_goa_bundle", start_rows),
        ("END", "E", "end_goa_bundle", end_rows),
    ):
        with atomic_stage_bundle(
            paths[path_key],
            stage="01B1",
            role=role,
            release_id=release,
            parameters={},
            ontology=ontology,
            implementation=impl,
            inputs=(reference,),
        ) as stage:
            (stage / "goa.normalized.gaf").write_text(
                "!gaf-version: 2.2\n" + "".join(rows)
            )
    accepted = (
        _accepted("S", "T1", "1", "A", count="2"),
        _accepted("S", "T1", "2", "B", count="2"),
        _accepted("S", "T2", "3", "A"),
        _accepted("S", "T3", "4", "M"),
        _accepted("E", "T1", "5", "B"),
        _accepted("E", "T2", "6", "A"),
        _accepted("E", "T3", "7", "X", donor="NO"),
    )
    policies = {
        "resolver": {"subtaxon_policy": "strict"},
        "annotation_donor": {
            "partial_sequence": "allow",
            "documentary_fragment": "allow",
        },
    }
    meta = {
        "schema_version": "stage07-selection-metadata-v1",
        "stage": "07",
        "policy_version": "stage07-resolver-v1",
        "releases": {"START": "S", "END": "E"},
        "effective_policies": policies,
        "stage08_contract": {
            "association_source": "07_accepted_associations.tsv",
            "go_donor_criterion": "annotation_donor=YES",
            "preserve_nondonors": True,
            "preserve_audit_origin": True,
        },
    }
    with atomic_stage_bundle(
        paths["stage07_bundle"],
        stage="07",
        role=None,
        release_id=None,
        parameters={
            "start_release_id": "S",
            "end_release_id": "E",
            "policy_version": "stage07-resolver-v1",
            "policies": policies,
        },
        ontology=None,
        implementation=impl,
    ) as stage:
        (stage / "07_accepted_associations.tsv").write_text(
            "\t".join(ACCEPTED_COLUMNS)
            + "\n"
            + "".join("\t".join(a.tsv_values()) + "\n" for a in accepted)
        )
        (stage / "07_selection_metadata.json").write_text(json.dumps(meta))
    return paths


def _run(paths, **kwargs):
    return publish_raw_annotation_collection(**paths, **kwargs)


def _rows(path):
    return [row.split("\t") for row in path.read_text().splitlines()]


def _assert_no_08_staging_leaks(tmp_path: Path) -> None:
    remaining = {
        path.name for path in tmp_path.iterdir() if path.name.startswith(".08.")
    }
    assert remaining == {".08.publish-lock"}
    lock = tmp_path / ".08.publish-lock"
    assert lock.is_file() and not lock.is_symlink()


def test_atomic_shared_bundle_provenance_and_counts(tmp_path):
    paths = _setup(tmp_path)
    manifest = _run(paths)
    root = paths["destination"]
    assert manifest.stage == "08" and manifest.role is None
    assert len(manifest.inputs) == 8
    assert len(manifest.outputs) == 12
    assert validate_bundle(root, deep=True).status == "complete"
    summary = json.loads((root / "08_collection_summary.json").read_text())
    assert summary["annotation_coverage_certified"] is True
    assert summary["retrieved_source_rows"] == 4
    assert summary["annotation_links"] == 5
    assert summary["donor_yes_without_rows"] == 1
    assert summary["by_role"]["START"]["retrieved_source_rows"] == 2
    assert summary["by_role"]["END"]["retrieved_source_rows"] == 2
    assert manifest.parameters["evidence_selection"] == "none"
    assert manifest.parameters["not_veto"] == "none"
    assert manifest.implementation["source_sha256"]
    assert manifest.ontology["sha256"]
    assert len(list(root.rglob("manifest.json"))) == 1
    _assert_no_08_staging_leaks(tmp_path)


def test_original_source_and_authorizations_are_preserved(tmp_path):
    paths = _setup(tmp_path)
    _run(paths)
    root = paths["destination"]
    start = _rows(root / "08_start_source_assertions.tsv")
    end = _rows(root / "08_end_source_assertions.tsv")
    assert [row[2] for row in start[1:]] == ["2", "3"]
    assert [row[2] for row in end[1:]] == ["2", "3"]
    assert {row[1] for row in start[1:]} == {"S"}
    assert {row[1] for row in end[1:]} == {"E"}
    starts = _rows(root / "08_start_annotation_links.tsv")
    ends = _rows(root / "08_end_annotation_links.tsv")
    assert {row[1] for row in starts[1:]} == {"T1", "T2"}
    assert {row[1] for row in ends[1:]} == {"T1", "T2"}
    end_coverage = _rows(root / "08_end_donor_coverage.tsv")
    assert any(row[7] == "DONOR_DISABLED" for row in end_coverage[1:])
    start_coverage = _rows(root / "08_start_donor_coverage.tsv")
    assert any(row[7] == "NO_RETAINED_GOA_ROWS" for row in start_coverage[1:])


def test_completed_bundle_is_never_overwritten(tmp_path):
    paths = _setup(tmp_path)
    original = _run(paths)
    with pytest.raises(FileExistsError):
        _run(paths)
    with pytest.raises(FileExistsError):
        _run(paths, allow_invalidated_destination=True)
    assert validate_bundle(paths["destination"], deep=True) == original


def test_end_scan_failure_cannot_publish_partial_start(tmp_path):
    paths = _setup(tmp_path)
    from probe.stage08_collection_inputs import GAF_NAME

    end = paths["end_goa_bundle"] / GAF_NAME
    payload = end.read_bytes().replace(b"IMP", b"IEA")
    assert len(payload) == end.stat().st_size
    end.write_bytes(payload)
    with pytest.raises(ValueError, match="SHA256"):
        _run(paths)
    assert not paths["destination"].exists()
    _assert_no_08_staging_leaks(tmp_path)


def test_mid_transaction_exception_publishes_nothing(tmp_path, monkeypatch):
    paths = _setup(tmp_path)
    real = publisher.collect_one_normalized_gaf

    def fail_second(*, gaf, **kwargs):
        if gaf.role == "END":
            raise RuntimeError("simulated END failure")
        return real(gaf=gaf, **kwargs)

    monkeypatch.setattr(publisher, "collect_one_normalized_gaf", fail_second)
    with pytest.raises(RuntimeError, match="simulated"):
        _run(paths)
    assert not paths["destination"].exists()
    _assert_no_08_staging_leaks(tmp_path)


def test_both_scans_finish_but_input_changes_before_publication(tmp_path, monkeypatch):
    paths = _setup(tmp_path)
    real = publisher.collect_one_normalized_gaf

    def mutate_after_end(*, gaf, **kwargs):
        result = real(gaf=gaf, **kwargs)
        if gaf.role == "END":
            manifest = paths["stage07_bundle"] / "manifest.json"
            manifest.write_text(manifest.read_text() + " ")
        return result

    monkeypatch.setattr(publisher, "collect_one_normalized_gaf", mutate_after_end)
    with pytest.raises(ValueError, match="changed before publication"):
        _run(paths)
    assert not paths["destination"].exists()


def test_modification_to_gaf_after_start_scan_prevents_publication(
    tmp_path, monkeypatch
):
    paths = _setup(tmp_path)
    real = publisher.collect_one_normalized_gaf

    def mutate_after_end(*, gaf, **kwargs):
        result = real(gaf=gaf, **kwargs)
        if gaf.role == "END":
            source = paths["start_goa_bundle"] / "goa.normalized.gaf"
            source.write_bytes(source.read_bytes().replace(b"IDA", b"IMP"))
        return result

    monkeypatch.setattr(publisher, "collect_one_normalized_gaf", mutate_after_end)
    with pytest.raises(ValueError, match="changed across collection"):
        _run(paths)
    assert not paths["destination"].exists()


def test_implementation_change_during_scans_prevents_publication(tmp_path, monkeypatch):
    paths = _setup(tmp_path)
    real = publisher._source_metadata
    calls = 0

    def changed(extra_sources=None):
        nonlocal calls
        calls += 1
        value = real(extra_sources)
        if calls > 1:
            value["source_sha256"]["src/probe/records.py"] = "a" * 64
        return value

    monkeypatch.setattr(publisher, "_source_metadata", changed)
    with pytest.raises(ValueError, match="implementation changed"):
        _run(paths)
    assert not paths["destination"].exists()


def test_empty_complete_scan_is_not_a_failed_stage(tmp_path):
    paths = _setup(tmp_path, start_rows=[], end_rows=[])
    _run(paths)
    outcome = json.loads((paths["destination"] / "stage_outcome.json").read_text())
    assert outcome["state"] == "EMPTY_RESULT"
    summary = json.loads(
        (paths["destination"] / "08_collection_summary.json").read_text()
    )
    assert summary["annotation_coverage_certified"] is True
    assert summary["donor_yes_without_rows"] == 6


def test_no_donor_case_retains_no_invents(tmp_path):
    paths = _setup(tmp_path)
    root = paths["stage07_bundle"]
    accepted = root / "07_accepted_associations.tsv"
    accepted.write_text("\t".join(ACCEPTED_COLUMNS) + "\n")
    manifest = root / "manifest.json"
    payload = json.loads(manifest.read_text())
    for item in payload["outputs"]:
        if item["path"] == accepted.name:
            item["size_bytes"] = accepted.stat().st_size
            item["sha256"] = sha256_file(accepted)
    manifest.write_text(json.dumps(payload))
    _run(paths)
    summary = json.loads(
        (paths["destination"] / "08_collection_summary.json").read_text()
    )
    assert summary["annotation_links"] == 0
    assert summary["donor_yes_without_rows"] == 0


def test_invalidated_destination_contract(tmp_path):
    paths = _setup(tmp_path)
    dest = paths["destination"]
    dest.mkdir()
    (dest / "obsolete").write_text("stale")
    with pytest.raises(FileExistsError):
        _run(paths)
    assert (dest / "obsolete").exists()
    _run(paths, allow_invalidated_destination=True)
    assert not (dest / "obsolete").exists()
    assert validate_bundle(dest, deep=True).stage == "08"


def test_source_metadata_is_deterministic_and_complete():
    first = publisher._source_metadata()
    assert first == publisher._source_metadata()
    assert len(first["source_sha256"]) == len(publisher._IMPLEMENTATION_FILES)
    assert all(len(value) == 64 for value in first["source_sha256"].values())


def test_corrupted_output_after_publication_is_detected(tmp_path):
    paths = _setup(tmp_path)
    _run(paths)
    source = paths["destination"] / "08_start_source_assertions.tsv"
    original = source.read_bytes()
    source.write_bytes(original.replace(b"IDA", b"IMP"))
    with pytest.raises(ValueError, match="SHA256"):
        validate_bundle(paths["destination"], deep=True)


def test_uncertified_result_is_never_published(tmp_path, monkeypatch):
    paths = _setup(tmp_path)
    original = publisher.collect_one_normalized_gaf

    def uncertified(*, gaf, **kwargs):
        result = original(gaf=gaf, **kwargs)
        if gaf.role == "END":
            return replace(result, coverage_certified=False)
        return result

    monkeypatch.setattr(publisher, "collect_one_normalized_gaf", uncertified)
    with pytest.raises(ValueError, match="uncertified"):
        _run(paths)
    assert not paths["destination"].exists()


def test_unexpected_collector_output_is_not_published(tmp_path, monkeypatch):
    paths = _setup(tmp_path)
    original = publisher.collect_one_normalized_gaf

    def append_unexpected(*, gaf, staging_dir, **kwargs):
        result = original(gaf=gaf, staging_dir=staging_dir, **kwargs)
        (staging_dir / "rogue.txt").write_text("unexpected")
        return result

    monkeypatch.setattr(publisher, "collect_one_normalized_gaf", append_unexpected)
    with pytest.raises(ValueError, match="collector output"):
        _run(paths)
    assert not paths["destination"].exists()


def test_concurrent_publisher_lock_is_respected(tmp_path):
    paths = _setup(tmp_path)
    lock = tmp_path / ".08.publish-lock"
    lock.mkdir()
    try:
        with pytest.raises(FileExistsError):
            _run(paths)
        assert not paths["destination"].exists()
    finally:
        lock.rmdir()
    assert _run(paths).stage == "08"
