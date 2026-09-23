from __future__ import annotations

import gzip
import hashlib
import io
import json
import tarfile
import zipfile
from pathlib import Path

import pytest

from probe.data_acquisition import (
    AcquisitionError,
    acquire_configured_snapshots,
    disk_space_preflight,
    load_acquisition_config,
    main,
    validate_snapshot,
)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _taxonomy_zip(
    path: Path,
    *,
    missing: str | None = None,
    unsafe_name: str | None = None,
) -> None:
    with zipfile.ZipFile(path, "w") as archive:
        for name in ("nodes.dmp", "names.dmp", "merged.dmp", "delnodes.dmp"):
            if name != missing:
                archive.writestr(name, f"synthetic {name}\n")
        archive.writestr("rankedlineage.dmp", "synthetic lineage\n")
        if unsafe_name:
            archive.writestr(unsafe_name, "unsafe\n")


def _uniprot_archive(
    path: Path,
    *,
    extra_swiss: bool = False,
    omit_trembl: bool = False,
    unsafe_name: str | None = None,
) -> None:
    with tarfile.open(path, "w:gz") as archive:
        members = {
            "release/knowledgebase/uniprot_sprot.dat.gz": gzip.compress(b"ID SP\n"),
        }
        if not omit_trembl:
            members["release/knowledgebase/uniprot_trembl.dat.gz"] = gzip.compress(
                b"ID TR\n"
            )
        if extra_swiss:
            members["other/uniprot_sprot_copy.dat.gz"] = gzip.compress(b"ID COPY\n")
        if unsafe_name:
            members[unsafe_name] = b"unsafe\n"
        for name, content in members.items():
            info = tarfile.TarInfo(name)
            info.size = len(content)
            archive.addfile(info, io.BytesIO(content))


def _sources(tmp_path: Path) -> dict[str, Path]:
    source = tmp_path / "sources"
    source.mkdir()
    files = {
        "swiss_start": source / "swiss_start.dat.gz",
        "trembl_start": source / "trembl_start.dat.gz",
        "swiss_end": source / "swiss_end.dat.gz",
        "trembl_end": source / "trembl_end.dat.gz",
        "goa_start": source / "goa_start.gaf.gz",
        "goa_end": source / "goa_end.gaf.gz",
        "taxonomy_start": source / "taxonomy_start.zip",
        "taxonomy_end": source / "taxonomy_end.zip",
    }
    for key in ("swiss_start", "trembl_start", "swiss_end", "trembl_end"):
        files[key].write_bytes(gzip.compress(f"{key}\n".encode()))
    for key in ("goa_start", "goa_end"):
        files[key].write_bytes(gzip.compress(f"!{key}\n".encode()))
    _taxonomy_zip(files["taxonomy_start"])
    _taxonomy_zip(files["taxonomy_end"])
    return files


def _config_text(data_root: Path, files: dict[str, Path]) -> str:
    return f'''data_root = "{data_root}"
minimum_free_bytes = 0

[snapshots.start.uniprot]
release = "alpha-release"
mode = "direct"
[snapshots.start.uniprot.swissprot]
url = "{files['swiss_start'].as_uri()}"
[snapshots.start.uniprot.trembl]
url = "{files['trembl_start'].as_uri()}"
[snapshots.start.goa]
release = "alpha-goa"
url = "{files['goa_start'].as_uri()}"
[snapshots.start.taxonomy]
snapshot_date = "alpha-taxonomy"
url = "{files['taxonomy_start'].as_uri()}"

[snapshots.end.uniprot]
release = "omega-release"
mode = "direct"
[snapshots.end.uniprot.swissprot]
url = "{files['swiss_end'].as_uri()}"
[snapshots.end.uniprot.trembl]
url = "{files['trembl_end'].as_uri()}"
[snapshots.end.goa]
release = "omega-goa"
url = "{files['goa_end'].as_uri()}"
[snapshots.end.taxonomy]
snapshot_date = "omega-taxonomy"
url = "{files['taxonomy_end'].as_uri()}"
'''


def _write_direct_config(tmp_path: Path) -> tuple[Path, Path, dict[str, Path]]:
    files = _sources(tmp_path)
    data_root = tmp_path / "external-data"
    config_path = tmp_path / "acquisition.toml"
    config_path.write_text(_config_text(data_root, files), encoding="utf-8")
    return config_path, data_root, files


def _archive_config_text(
    data_root: Path,
    archive: Path,
    taxonomy: Path,
    goa: Path,
) -> str:
    sections = []
    for role, label in (("start", "alpha"), ("end", "omega")):
        sections.append(
            f'''
[snapshots.{role}.uniprot]
release = "{label}-release"
mode = "archive"
[snapshots.{role}.uniprot.archive]
url = "{archive.as_uri()}"
[snapshots.{role}.goa]
release = "{label}-goa"
url = "{goa.as_uri()}"
[snapshots.{role}.taxonomy]
snapshot_date = "{label}-taxonomy"
url = "{taxonomy.as_uri()}"
'''
        )
    return f'data_root = "{data_root}"\nminimum_free_bytes = 0\n' + "".join(
        sections
    )


def test_direct_acquisition_layout_manifest_reuse_part_and_snapshot_selection(
    tmp_path,
):
    config_path, data_root, files = _write_direct_config(tmp_path)
    config = load_acquisition_config(config_path)
    part = data_root / "start/uniprot/raw/swiss_start.dat.gz.part"
    part.parent.mkdir(parents=True)
    part.write_bytes(b"partial")

    results = acquire_configured_snapshots(config, roles=("start",))

    assert [result.role for result in results] == ["start"]
    assert not (data_root / "end").exists()
    expected_directories = (
        "start/uniprot/raw",
        "start/uniprot/extracted",
        "start/uniprot/derived",
        "start/goa/raw",
        "start/taxonomy/raw",
        "start/taxonomy/extracted",
        "start/ontology/raw",
        "start/indexes",
        "manifests",
    )
    assert all((data_root / name).is_dir() for name in expected_directories)
    assert not part.exists()
    goa = data_root / "start/goa/raw/goa_start.gaf.gz"
    assert goa.read_bytes() == files["goa_start"].read_bytes()
    assert goa.suffix == ".gz"

    manifest_path = data_root / "manifests/start.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["snapshot_role"] == "start"
    assert manifest["requested"] == {
        "goa_release": "alpha-goa",
        "taxonomy_snapshot_date": "alpha-taxonomy",
        "uniprot_mode": "direct",
        "uniprot_release": "alpha-release",
    }
    swiss = manifest["artifacts"]["uniprot_swissprot"]
    assert swiss["sha256"] == _sha256(files["swiss_start"])
    assert not Path(swiss["local_path"]).is_absolute()
    assert manifest["derived"] == []
    assert manifest["warnings"]
    assert {
        Path(record["archive_member"]).name
        for record in manifest["taxonomy_extracted"]
    } >= {"nodes.dmp", "names.dmp", "merged.dmp", "delnodes.dmp"}

    first_manifest = manifest_path.read_bytes()
    acquire_configured_snapshots(config, roles=("start",))
    assert manifest_path.read_bytes() == first_manifest
    assert validate_snapshot(config, "start")


def test_existing_mismatched_raw_artifact_requires_repair(tmp_path):
    config_path, data_root, _files = _write_direct_config(tmp_path)
    destination = data_root / "start/uniprot/raw/swiss_start.dat.gz"
    destination.parent.mkdir(parents=True)
    destination.write_bytes(b"not the configured source")
    config = load_acquisition_config(config_path)

    with pytest.raises(AcquisitionError, match="--repair"):
        acquire_configured_snapshots(config, roles=("start",))

    acquire_configured_snapshots(config, roles=("start",), repair=True)
    assert list(destination.parent.glob("swiss_start.dat.gz.conflict-*"))
    assert validate_snapshot(config, "start")


def test_full_acquisition_creates_independent_start_and_end_manifests(tmp_path):
    config_path, data_root, _files = _write_direct_config(tmp_path)

    results = acquire_configured_snapshots(load_acquisition_config(config_path))

    assert [result.role for result in results] == ["start", "end"]
    assert (data_root / "start/uniprot/raw").is_dir()
    assert (data_root / "end/uniprot/raw").is_dir()
    assert (data_root / "manifests/start.json").is_file()
    assert (data_root / "manifests/end.json").is_file()


def test_historical_uniprot_archive_extracts_only_required_dat_files(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    archive = source / "knowledgebase-release.tar.gz"
    taxonomy = source / "new-taxdump.zip"
    goa = source / "goa-release.gaf.gz"
    _uniprot_archive(archive)
    _taxonomy_zip(taxonomy)
    goa.write_bytes(gzip.compress(b"!gaf-version: 2.2\n"))
    data_root = tmp_path / "external"
    config_path = tmp_path / "archive.toml"
    config_path.write_text(
        _archive_config_text(data_root, archive, taxonomy, goa), encoding="utf-8"
    )

    result = acquire_configured_snapshots(
        load_acquisition_config(config_path), roles=("start",)
    )[0]

    records = result.manifest["uniprot_extracted"]
    assert [record["artifact"] for record in records] == [
        "uniprot_swissprot",
        "uniprot_trembl",
    ]
    assert all(record["archive_member"].endswith(".dat.gz") for record in records)
    assert archive.read_bytes() == (
        data_root / result.manifest["artifacts"]["uniprot_archive"]["local_path"]
    ).read_bytes()


@pytest.mark.parametrize(
    ("extra_swiss", "omit_trembl"),
    [(True, False), (False, True)],
)
def test_uniprot_archive_rejects_ambiguous_or_missing_members(
    tmp_path, extra_swiss, omit_trembl
):
    source = tmp_path / "source"
    source.mkdir()
    archive = source / "knowledgebase.tar.gz"
    taxonomy = source / "taxonomy.zip"
    goa = source / "goa.gaf.gz"
    _uniprot_archive(
        archive, extra_swiss=extra_swiss, omit_trembl=omit_trembl
    )
    _taxonomy_zip(taxonomy)
    goa.write_bytes(gzip.compress(b"!gaf\n"))
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        _archive_config_text(tmp_path / "external", archive, taxonomy, goa),
        encoding="utf-8",
    )

    with pytest.raises(AcquisitionError, match="exactly one"):
        acquire_configured_snapshots(
            load_acquisition_config(config_path), roles=("start",)
        )


def test_tar_path_traversal_is_rejected(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    archive = source / "knowledgebase.tar.gz"
    taxonomy = source / "taxonomy.zip"
    goa = source / "goa.gaf.gz"
    _uniprot_archive(archive, unsafe_name="../../escape")
    _taxonomy_zip(taxonomy)
    goa.write_bytes(gzip.compress(b"!gaf\n"))
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        _archive_config_text(tmp_path / "external", archive, taxonomy, goa),
        encoding="utf-8",
    )

    with pytest.raises(AcquisitionError, match="unsafe archive path"):
        acquire_configured_snapshots(
            load_acquisition_config(config_path), roles=("start",)
        )
    assert not (tmp_path / "escape").exists()


@pytest.mark.parametrize(
    ("missing", "unsafe", "message"),
    [
        ("merged.dmp", None, "missing required members"),
        (None, "../escape", "unsafe archive path"),
    ],
)
def test_taxonomy_archive_validation_and_path_safety(
    tmp_path, missing, unsafe, message
):
    config_path, _data_root, files = _write_direct_config(tmp_path)
    _taxonomy_zip(files["taxonomy_start"], missing=missing, unsafe_name=unsafe)

    with pytest.raises(AcquisitionError, match=message):
        acquire_configured_snapshots(
            load_acquisition_config(config_path), roles=("start",)
        )
    assert not (tmp_path / "escape").exists()


def test_disk_space_preflight_aborts_below_configured_threshold(tmp_path, monkeypatch):
    usage = type("Usage", (), {"total": 1000, "used": 900, "free": 100})()
    monkeypatch.setattr("probe.data_acquisition.shutil.disk_usage", lambda _path: usage)

    report = disk_space_preflight(tmp_path / "not-created", 100)
    assert report.free_bytes == 100
    with pytest.raises(AcquisitionError, match="insufficient free space"):
        disk_space_preflight(tmp_path / "not-created", 101)


def test_check_mode_does_not_download_and_snapshot_cli_selects_one(tmp_path):
    config_path, data_root, _files = _write_direct_config(tmp_path)

    assert main(["--config", str(config_path), "--snapshot", "start", "--check"]) == 2
    assert not data_root.exists()
    assert main(["--config", str(config_path), "--snapshot", "end"]) == 0
    assert (data_root / "end").is_dir()
    assert not (data_root / "start").exists()
    assert main(["--config", str(config_path), "--snapshot", "end", "--check"]) == 0


def test_configuration_rejects_shared_mutable_sources(tmp_path):
    config_path, data_root, files = _write_direct_config(tmp_path)
    mutable = "https://example.invalid/current/new_taxdump.zip"
    text = _config_text(data_root, files).replace(
        files["taxonomy_start"].as_uri(), mutable
    ).replace(files["taxonomy_end"].as_uri(), mutable)
    config_path.write_text(text, encoding="utf-8")

    with pytest.raises(AcquisitionError, match="same mutable URL"):
        load_acquisition_config(config_path)


def test_configuration_requires_generic_start_and_end_roles(tmp_path):
    config_path, _data_root, _files = _write_direct_config(tmp_path)
    config = load_acquisition_config(config_path)

    assert set(config.snapshots) == {"start", "end"}
    assert config.snapshots["start"].uniprot.release == "alpha-release"
    assert config.snapshots["end"].uniprot.release == "omega-release"
