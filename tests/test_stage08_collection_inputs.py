"""Synthetic, real-manifest tests of the collection-input boundary."""

import hashlib
import json
import shutil
from dataclasses import FrozenInstanceError, asdict, replace
from pathlib import Path

import pytest

from probe import stage08_collection_inputs as preflight
from probe.stage08_collection_inputs import (
    ACCEPTED_NAME,
    GAF_NAME,
    POLICY_NAME,
    SELECTION_NAME,
    prepare_collection_inputs,
)
from probe.stage08_donors import ACCEPTED_COLUMNS, DonorAssociation
from probe.workflow.artifacts import (
    Artifact,
    StageManifest,
    atomic_stage_bundle,
    sha256_file,
)

POLICIES = {
    "resolver": {"subtaxon_policy": "strict"},
    "annotation_donor": {
        "partial_sequence": "report_only",
        "documentary_fragment": "allow",
    },
}


def describe(path: Path) -> Artifact:
    return Artifact(str(path.resolve()), path.stat().st_size, sha256_file(path))


def metadata() -> dict:
    return {
        "schema_version": "stage07-selection-metadata-v1",
        "stage": "07",
        "policy_version": "stage07-resolver-v1",
        "releases": {"START": "old-release", "END": "new-release"},
        "effective_policies": POLICIES,
        "stage08_contract": {
            "association_source": ACCEPTED_NAME,
            "go_donor_criterion": "annotation_donor=YES",
            "preserve_nondonors": True,
            "preserve_audit_origin": True,
        },
    }


def accepted_text() -> str:
    rows = []
    for release, target, rid, accession, donor, count in (
        ("old-release", "T1", "1", "A", "YES", "3"),
        ("old-release", "T1", "2", "B", "YES", "3"),
        ("old-release", "T1", "3", "C", "NO", "3"),
        ("old-release", "T2", "1", "A", "YES", "1"),
        ("new-release", "T1", "7", "B", "YES", "1"),
    ):
        row = DonorAssociation(
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
        if donor == "NO":
            row = replace(
                row,
                decision_basis="STABLE_UNIQUE_LOCUS_EXACT_SUBSEQUENCE",
                sequence_relation="CANDIDATE_EXACT_CONTIGUOUS_SUBSEQUENCE",
                exact_sequence="0",
            )
        rows.append("\t".join(row.tsv_values()))
    return "\t".join(ACCEPTED_COLUMNS) + "\n" + "\n".join(rows) + "\n"


@pytest.fixture
def bundles(tmp_path: Path) -> dict[str, Path]:
    args = {
        "stage07_bundle": tmp_path / "07",
        "start_goa_bundle": tmp_path / "START-01B1",
        "end_goa_bundle": tmp_path / "END-01B1",
        "go_policy_bundle": tmp_path / "01B0",
    }
    ontology = {
        "sha256": hashlib.sha256(b"synthetic ontology").hexdigest(),
        "original_url": None,
        "retrieval_date": None,
        "version_iri": None,
    }
    implementation = {"name": "synthetic-fixture", "version": "1"}
    with atomic_stage_bundle(
        args["go_policy_bundle"],
        stage="01B0",
        role=None,
        release_id=None,
        parameters={},
        ontology=ontology,
        implementation=implementation,
    ) as staging:
        (staging / POLICY_NAME).write_text("synthetic policy, never parsed here\n")
    policy_artifact = describe(args["go_policy_bundle"] / "manifest.json")
    for role, release, key in (
        ("START", "old-release", "start_goa_bundle"),
        ("END", "new-release", "end_goa_bundle"),
    ):
        with atomic_stage_bundle(
            args[key],
            stage="01B1",
            role=role,
            release_id=release,
            parameters={},
            ontology=ontology,
            implementation=implementation,
            inputs=(policy_artifact,),
        ) as staging:
            fields = (
                "UniProtKB",
                "A",
                "symbol",
                "enables",
                "GO:0000001",
                "PMID:1",
                "IDA",
                "",
                "F",
                "name",
                "",
                "protein",
                "taxon:1",
                "20230101",
                "source",
                "",
                "",
            )
            (staging / GAF_NAME).write_text(
                "!gaf-version: 2.2\n" + "\t".join(fields) + "\n"
            )
    with atomic_stage_bundle(
        args["stage07_bundle"],
        stage="07",
        role=None,
        release_id=None,
        parameters={
            "start_release_id": "old-release",
            "end_release_id": "new-release",
            "policy_version": "stage07-resolver-v1",
            "policies": POLICIES,
        },
        ontology=None,
        implementation=implementation,
    ) as staging:
        (staging / ACCEPTED_NAME).write_text(accepted_text())
        (staging / SELECTION_NAME).write_text(json.dumps(metadata()))
    return args


def update_manifest(bundle: Path, transform) -> None:
    path = bundle / "manifest.json"
    value = json.loads(path.read_text())
    transform(value)
    path.write_text(json.dumps(value))


def rehash_output(bundle: Path, name: str) -> None:
    artifact = describe(bundle / name)

    def transform(value):
        for row in value["outputs"]:
            if row["path"] == name:
                row.update(size_bytes=artifact.size_bytes, sha256=artifact.sha256)

    update_manifest(bundle, transform)


def update_selection(bundles, transform) -> None:
    root = bundles["stage07_bundle"]
    path = root / SELECTION_NAME
    value = json.loads(path.read_text())
    transform(value)
    path.write_text(json.dumps(value))
    rehash_output(root, SELECTION_NAME)


def test_real_bundle_roundtrip_preserves_all_donors(bundles) -> None:
    result = prepare_collection_inputs(**bundles)
    report = result.report()
    assert len(result.donors.associations) == 5
    assert report["by_role"]["START"]["donor_yes_associations"] == 3
    assert report["by_role"]["START"]["donor_no_associations"] == 1
    assert report["by_role"]["START"]["unique_donor_accessions"] == 2
    assert report["by_role"]["END"]["donor_yes_associations"] == 1
    assert (
        result.donors.donors_for(
            release="new-release", database="UniProtKB", accession="A"
        )
        == ()
    )
    assert all(value == "PASS" for value in report["checks"].values())
    assert not report["annotation_coverage_certified"]
    assert not report["recursive_upstream_validation"]
    assert len(result.provenance_inputs) == 8
    assert len(result.fingerprint) == 64


def test_only_manifests_and_consumed_stage07_payloads_are_hashed(
    bundles, monkeypatch
) -> None:
    observed = []
    real_hash = preflight.sha256_file

    def track(path):
        observed.append(path)
        return real_hash(path)

    monkeypatch.setattr(preflight, "sha256_file", track)
    result = prepare_collection_inputs(**bundles)
    assert len(observed) == 6
    assert {path.name for path in observed} == {
        "manifest.json",
        ACCEPTED_NAME,
        SELECTION_NAME,
    }
    assert not result.report()["normalized_gaf_contents_read"]


def test_no_gaf_or_owl_content_opened(bundles, monkeypatch) -> None:
    original = Path.open

    def guarded(path, *args, **kwargs):
        if path.suffix in {".gaf", ".owl", ".fasta"} or path.name == POLICY_NAME:
            pytest.fail(f"unexpected biological payload read: {path}")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", guarded)
    prepare_collection_inputs(**bundles)


def test_same_size_gaf_mutation_is_explicitly_not_verified(bundles) -> None:
    path = bundles["start_goa_bundle"] / GAF_NAME
    original = path.read_bytes()
    path.write_bytes(original.replace(b"IDA", b"IMP"))
    result = prepare_collection_inputs(**bundles)
    assert result.gafs[0].source.sha256 != sha256_file(path)
    assert result.report()["by_role"]["START"]["normalized_gaf_content_check"] == (
        "NOT_PERFORMED"
    )


@pytest.mark.parametrize("name", [ACCEPTED_NAME, SELECTION_NAME])
def test_same_size_consumed_stage07_mutation_is_rejected(bundles, name) -> None:
    path = bundles["stage07_bundle"] / name
    data = path.read_bytes()
    assert b"old-release" in data
    path.write_bytes(data.replace(b"old-release", b"bad-release"))
    with pytest.raises(ValueError, match="SHA256/size mismatch"):
        prepare_collection_inputs(**bundles)


@pytest.mark.parametrize(
    ("key", "stage", "role", "release"),
    [
        ("stage07_bundle", "08", None, None),
        ("go_policy_bundle", "07", None, None),
        ("start_goa_bundle", "01B1", "END", "old-release"),
        ("end_goa_bundle", "01B1", "START", "new-release"),
        ("start_goa_bundle", "01B1", "START", "different-release"),
    ],
)
def test_wrong_bundle_roles_or_releases_fail(bundles, key, stage, role, release):
    update_manifest(
        bundles[key],
        lambda value: value.update(stage=stage, role=role, release_id=release),
    )
    with pytest.raises(ValueError, match="stage/role|release mismatch"):
        prepare_collection_inputs(**bundles)


def test_swapped_goa_bundles_fail(bundles) -> None:
    args = dict(bundles)
    args["start_goa_bundle"], args["end_goa_bundle"] = (
        args["end_goa_bundle"],
        args["start_goa_bundle"],
    )
    with pytest.raises(ValueError, match="stage/role"):
        prepare_collection_inputs(**args)


@pytest.mark.parametrize(
    ("key", "value", "message"),
    [
        ("schema_version", "unsupported", "metadata schema"),
        ("stage", "08", "metadata schema"),
        ("releases", {"START": "old-release"}, "exactly START and END"),
        ("releases", {"START": 1, "END": "new-release"}, "release identifier"),
        ("releases", {"START": "other", "END": "new-release"}, "release mismatch"),
        ("policy_version", "other", "policy version mismatch"),
        ("effective_policies", {}, "effective policies"),
        ("effective_policies", {"new": True}, "effective policy mismatch"),
        ("stage08_contract", {}, "donor-consumption"),
    ],
)
def test_invalid_selection_metadata_fail(bundles, key, value, message):
    update_selection(bundles, lambda payload: payload.update({key: value}))
    with pytest.raises(ValueError, match=message):
        prepare_collection_inputs(**bundles)


@pytest.mark.parametrize("field", ["preserve_nondonors", "preserve_audit_origin"])
def test_metadata_truthy_string_is_not_boolean_true(bundles, field):
    update_selection(
        bundles, lambda value: value["stage08_contract"].update({field: "true"})
    )
    with pytest.raises(ValueError, match="donor-consumption"):
        prepare_collection_inputs(**bundles)


def test_duplicate_metadata_keys_are_rejected(bundles) -> None:
    root = bundles["stage07_bundle"]
    path = root / SELECTION_NAME
    path.write_text('{"stage": "07",' + path.read_text()[1:])
    rehash_output(root, SELECTION_NAME)
    with pytest.raises(ValueError, match="duplicate selection-metadata key"):
        prepare_collection_inputs(**bundles)


@pytest.mark.parametrize("key", ["start_goa_bundle", "end_goa_bundle"])
def test_missing_or_incompatible_ontology_fails(bundles, key) -> None:
    update_manifest(bundles[key], lambda value: value.update(ontology=None))
    with pytest.raises(ValueError, match="missing ontology"):
        prepare_collection_inputs(**bundles)


def test_goa_ontology_digest_mismatch_fails(bundles) -> None:
    update_manifest(
        bundles["start_goa_bundle"],
        lambda value: value["ontology"].update(sha256="f" * 64),
    )
    with pytest.raises(ValueError, match="ontology mismatch"):
        prepare_collection_inputs(**bundles)


def test_expected_ontology_digest_mismatch_fails(bundles) -> None:
    update_manifest(
        bundles["start_goa_bundle"],
        lambda value: value["ontology"].update(expected_sha256="f" * 64),
    )
    with pytest.raises(ValueError, match="expected/observed"):
        prepare_collection_inputs(**bundles)


def test_stale_policy_manifest_link_fails_even_with_same_ontology(bundles) -> None:
    update_manifest(
        bundles["go_policy_bundle"],
        lambda value: value["parameters"].update(policy_version="changed"),
    )
    with pytest.raises(ValueError, match="supplied 01B0 manifest identity"):
        prepare_collection_inputs(**bundles)


def test_missing_policy_reference_fails(bundles) -> None:
    update_manifest(bundles["end_goa_bundle"], lambda value: value.update(inputs=[]))
    with pytest.raises(ValueError, match="supplied 01B0 manifest identity"):
        prepare_collection_inputs(**bundles)


@pytest.mark.parametrize(
    ("key", "name"),
    [
        ("stage07_bundle", ACCEPTED_NAME),
        ("stage07_bundle", SELECTION_NAME),
        ("start_goa_bundle", GAF_NAME),
        ("end_goa_bundle", GAF_NAME),
        ("go_policy_bundle", POLICY_NAME),
    ],
)
def test_required_payload_requires_declared_digest(bundles, key, name):
    def remove_digest(value):
        for item in value["outputs"]:
            if item["path"] == name:
                item["sha256"] = None

    update_manifest(bundles[key], remove_digest)
    with pytest.raises(ValueError, match="missing declared SHA256"):
        prepare_collection_inputs(**bundles)


def test_gaf_size_mismatch_fails_without_reading_contents(bundles) -> None:
    path = bundles["start_goa_bundle"] / GAF_NAME
    path.write_bytes(path.read_bytes() + b"x")
    with pytest.raises(ValueError, match="size-mismatched"):
        prepare_collection_inputs(**bundles)


@pytest.mark.parametrize("key", ["stage07_bundle", "start_goa_bundle"])
def test_undeclared_payload_rejected_by_existing_validator(bundles, key) -> None:
    (bundles[key] / "unlisted.txt").write_text("unexpected")
    with pytest.raises(ValueError, match="payload differs"):
        prepare_collection_inputs(**bundles)


def test_symlink_bundle_is_not_hidden_by_path_resolution(bundles, tmp_path) -> None:
    link = tmp_path / "linked_bundle"
    link.symlink_to(bundles["start_goa_bundle"], target_is_directory=True)
    args = dict(bundles, start_goa_bundle=link)
    with pytest.raises(ValueError, match="symlink"):
        prepare_collection_inputs(**args)


def test_no_inputs_are_modified(bundles, tmp_path) -> None:
    paths = sorted(path for path in tmp_path.rglob("*") if path.is_file())
    before = {path: describe(path) for path in paths}
    prepare_collection_inputs(**bundles)
    assert {path: describe(path) for path in paths} == before


def test_repeated_preflight_has_same_fingerprint(bundles) -> None:
    a = prepare_collection_inputs(**bundles)
    b = prepare_collection_inputs(**bundles)
    assert a.fingerprint == b.fingerprint
    assert a.report() == b.report()


def test_relocated_unchanged_bundles_have_same_input_identity(bundles, tmp_path):
    before = prepare_collection_inputs(**bundles)
    relocated = tmp_path / "relocated"
    relocated.mkdir()
    args = {}
    for key, path in bundles.items():
        args[key] = relocated / path.name
        shutil.copytree(path, args[key])
    after = prepare_collection_inputs(**args)
    assert before.fingerprint == after.fingerprint
    assert before.gafs[0].source.path != after.gafs[0].source.path
    assert before.gafs[0].source.sha256 == after.gafs[0].source.sha256


def test_report_mutation_cannot_change_prepared_inputs(bundles) -> None:
    result = prepare_collection_inputs(**bundles)
    report = result.report()
    report["ontology"]["sha256"] = "modified"
    assert result.report()["ontology"]["sha256"] != "modified"
    with pytest.raises(FrozenInstanceError):
        result.fingerprint = "modified"


def test_header_only_accepted_table_remains_valid(bundles) -> None:
    root = bundles["stage07_bundle"]
    (root / ACCEPTED_NAME).write_text("\t".join(ACCEPTED_COLUMNS) + "\n")
    rehash_output(root, ACCEPTED_NAME)
    result = prepare_collection_inputs(**bundles)
    assert result.donors.associations == ()
    assert result.report()["associations_loaded"] == 0
    assert not result.report()["annotation_coverage_certified"]


def test_stage07_change_changes_collection_identity(bundles) -> None:
    before = prepare_collection_inputs(**bundles)
    update_manifest(
        bundles["stage07_bundle"],
        lambda value: value["parameters"].update(new_relevant_value=1),
    )
    after = prepare_collection_inputs(**bundles)
    assert before.fingerprint != after.fingerprint


def test_nonnull_stage07_ontology_must_agree(bundles) -> None:
    policy = StageManifest.read(bundles["go_policy_bundle"] / "manifest.json")
    ontology = dict(policy.ontology, sha256="f" * 64)
    update_manifest(bundles["stage07_bundle"], lambda v: v.update(ontology=ontology))
    with pytest.raises(ValueError, match="Stage 07/01B0 ontology mismatch"):
        prepare_collection_inputs(**bundles)


def test_detects_input_modification_during_preflight(bundles, monkeypatch) -> None:
    original = preflight.read_accepted_associations

    def modify_after_read(path, **kwargs):
        result = original(path, **kwargs)
        Path(path).write_text(Path(path).read_text() + "\n")
        return result

    monkeypatch.setattr(preflight, "read_accepted_associations", modify_after_read)
    with pytest.raises(ValueError, match="input changed during preflight"):
        prepare_collection_inputs(**bundles)


def test_recorded_artifacts_match_actual_consumed_small_files(bundles) -> None:
    result = prepare_collection_inputs(**bundles)
    for artifact in result.provenance_inputs:
        assert asdict(artifact) == asdict(describe(Path(artifact.path)))
