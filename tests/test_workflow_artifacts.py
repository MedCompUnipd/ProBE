import json
from dataclasses import replace

import pytest

from probe.workflow import artifacts
from probe.workflow.artifacts import Artifact, StageManifest, atomic_stage_bundle


@pytest.fixture
def metadata():
    return dict(
        stage="fixture",
        role="START",
        release_id="arbitrary-start",
        parameters={"policy_version": "fixture-v1"},
        ontology={
            "sha256": "0" * 64,
            "original_url": None,
            "retrieval_date": None,
            "version_iri": None,
        },
        implementation={"name": "test-producer", "version": "1"},
    )


def test_publication_roundtrip_and_explicit_deep_verification(
    tmp_path, metadata, monkeypatch
):
    destination = tmp_path / "bundle"
    with atomic_stage_bundle(destination, **metadata) as staging:
        (staging / "facts.tsv").write_text("original")
        assert not destination.exists()
    result = artifacts.validate_bundle(destination, deep=True)
    assert result.release_id == "arbitrary-start"
    assert result.ontology["retrieval_date"] is None
    # A same-size mutation is intentionally caught only in deep mode.
    (destination / "facts.tsv").write_text("modified")
    with monkeypatch.context() as context:
        context.setattr(
            artifacts, "sha256_file", lambda _: pytest.fail("unexpected hash")
        )
        artifacts.validate_bundle(destination)
    with pytest.raises(ValueError, match="SHA256"):
        artifacts.validate_bundle(destination, deep=True)


def test_failed_or_concurrent_publication_never_replaces_bundle(tmp_path, metadata):
    destination = tmp_path / "bundle"
    with (
        pytest.raises(RuntimeError),
        atomic_stage_bundle(destination, **metadata) as staging,
    ):
        (staging / "partial").write_text("incomplete")
        raise RuntimeError("interrupted producer")
    assert not destination.exists()
    assert not list(tmp_path.iterdir())
    with atomic_stage_bundle(destination, **metadata) as staging:
        with (
            pytest.raises(FileExistsError),
            atomic_stage_bundle(destination, **metadata),
        ):
            pytest.fail("concurrent writer admitted")
        (staging / "facts").write_text("complete")
    with pytest.raises(FileExistsError), atomic_stage_bundle(destination, **metadata):
        pytest.fail("existing bundle replaced")
    assert (destination / "facts").read_text() == "complete"


def test_invalid_manifest_and_symlink_publication(tmp_path, metadata):
    manifest = StageManifest(
        **metadata, inputs=(), outputs=(Artifact("facts", 0, None),)
    )
    manifest.validate()
    for invalid in (
        replace(manifest, schema_version=2),
        replace(manifest, role="release-start"),
        replace(manifest, release_id=None),
        replace(manifest, outputs=(Artifact("../escape", 0, None),)),
        replace(manifest, outputs=(Artifact("facts", -1, None),)),
        replace(manifest, outputs=(Artifact("facts", 0, "wrong"),)),
        replace(manifest, outputs=()),
    ):
        with pytest.raises(ValueError):
            invalid.validate()
    with (
        pytest.raises(ValueError, match="symlink"),
        atomic_stage_bundle(tmp_path / "bundle", **metadata) as staging,
    ):
        (staging / "link").symlink_to(tmp_path / "outside")
    assert not (tmp_path / "bundle").exists()


def test_missing_output_and_malformed_manifest(tmp_path, metadata):
    destination = tmp_path / "bundle"
    with atomic_stage_bundle(destination, **metadata) as staging:
        (staging / "facts").write_text("complete")
    (destination / "facts").unlink()
    with pytest.raises(ValueError, match="missing"):
        artifacts.validate_bundle(destination)
    path = destination / "manifest.json"
    data = json.loads(path.read_text())
    data["unexpected"] = True
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError, match="schema"):
        StageManifest.read(path)


def test_manifest_records_environment_and_utc_timestamp(tmp_path, metadata):
    destination = tmp_path / "bundle"

    with atomic_stage_bundle(
        destination,
        **metadata,
        environment={
            "python": "fixture-python",
            "platform": "fixture-platform",
        },
    ) as staging:
        (staging / "facts.tsv").write_text("complete")

    manifest = StageManifest.read(destination / "manifest.json")

    assert manifest.environment == {
        "python": "fixture-python",
        "platform": "fixture-platform",
    }
    assert manifest.created_at_utc.endswith("Z")
    manifest.validate()


def test_bundle_rejects_undeclared_payload(tmp_path, metadata):
    destination = tmp_path / "bundle"

    with atomic_stage_bundle(destination, **metadata) as staging:
        (staging / "facts.tsv").write_text("complete")

    (destination / "rogue.tsv").write_text("undeclared")

    with pytest.raises(ValueError, match="payload differs"):
        artifacts.validate_bundle(destination)


def test_optional_output_hashing_is_not_used_for_normal_validation(
    tmp_path, metadata
):
    destination = tmp_path / "bundle"

    with atomic_stage_bundle(
        destination,
        **metadata,
        hash_outputs=False,
    ) as staging:
        (staging / "facts.tsv").write_text("complete")

    manifest = artifacts.validate_bundle(destination)
    assert manifest.outputs[0].sha256 is None

    with pytest.raises(ValueError, match="missing SHA256"):
        artifacts.validate_bundle(destination, deep=True)


def test_atomic_publication_fsyncs_stage_and_parent(
    tmp_path, metadata, monkeypatch
):
    calls = []

    monkeypatch.setattr(
        artifacts,
        "_fsync_directory",
        lambda path: calls.append(path),
    )

    destination = tmp_path / "bundle"

    with atomic_stage_bundle(destination, **metadata) as staging:
        (staging / "facts.tsv").write_text("complete")

    assert len(calls) == 2
    assert calls[0].parent == tmp_path
    assert calls[1] == tmp_path


def test_manifest_allows_no_ontology_for_pre_ontology_stage(
    tmp_path, metadata
):
    metadata = dict(metadata)
    metadata["ontology"] = None
    metadata["stage"] = "01A_FULL"

    destination = tmp_path / "bundle"

    with atomic_stage_bundle(destination, **metadata) as staging:
        (staging / "artifact.txt").write_text(
            "ontology-independent\n",
            encoding="utf-8",
        )

    manifest = artifacts.validate_bundle(destination, deep=True)

    assert manifest.stage == "01A_FULL"
    assert manifest.ontology is None


def test_empty_precreated_destination_can_be_published_atomically(
    tmp_path, metadata
):
    destination = tmp_path / "bundle"
    destination.mkdir()

    with atomic_stage_bundle(destination, **metadata) as staging:
        assert not destination.exists()
        (staging / "facts.tsv").write_text(
            "complete\n",
            encoding="utf-8",
        )

    manifest = artifacts.validate_bundle(destination, deep=True)

    assert manifest.status == "complete"
    assert (destination / "facts.tsv").read_text(
        encoding="utf-8"
    ) == "complete\n"




def test_invalidated_bundle_can_be_recomputed(tmp_path, metadata):
    destination = tmp_path / "bundle"

    with atomic_stage_bundle(destination, **metadata) as staging:
        (staging / "facts.tsv").write_text("old\n", encoding="utf-8")

    # Snakemake invalidates the declared completion marker before rerunning.
    (destination / artifacts.MANIFEST_NAME).unlink()

    try:
        with atomic_stage_bundle(
            destination,
            allow_invalidated_destination=True,
            **metadata,
        ) as staging:
            assert not destination.exists()
            (staging / "facts.tsv").write_text(
                "failed-new\n",
                encoding="utf-8",
            )
            raise RuntimeError("synthetic failure")
    except RuntimeError:
        pass

    assert not destination.exists()

    with atomic_stage_bundle(
        destination,
        allow_invalidated_destination=True,
        **metadata,
    ) as staging:
        (staging / "facts.tsv").write_text("new\n", encoding="utf-8")

    artifacts.validate_bundle(destination, deep=True)
    assert (destination / "facts.tsv").read_text(
        encoding="utf-8"
    ) == "new\n"


def test_complete_bundle_is_never_overwritten(tmp_path, metadata):
    destination = tmp_path / "bundle"

    with atomic_stage_bundle(destination, **metadata) as staging:
        (staging / "facts.tsv").write_text("old\n", encoding="utf-8")

    with pytest.raises(FileExistsError), atomic_stage_bundle(
        destination,
        allow_invalidated_destination=True,
        **metadata,
    ):
        pass
