"""M11/M12 EVAL/SKIP tests on a tiny, immutable synthetic M10c bundle."""

from __future__ import annotations

import csv
import hashlib
import json
import sqlite3

import pytest

from probe.stage08_go_closure import GoClosureIndex
from probe.stage08_temporal_benchmark import (
    END_SCORE,
    NOT_END,
    NOT_START,
    START_PRIOR,
    _status,
    build_temporal_benchmark,
    crosscheck_stage01_mapping,
    load_target_ids,
)


def write(path, content):
    path.write_text(content, encoding="utf-8")
    return hashlib.sha256(path.read_bytes()).hexdigest()


def table(path):
    with path.open(newline="") as handle:
        return list(csv.DictReader(handle, delimiter="\t"))


@pytest.fixture
def synthetic(tmp_path):
    bundle = tmp_path / "m10c"
    bundle.mkdir()
    db = bundle / "direct_provenance.sqlite3"
    with sqlite3.connect(db) as conn:
        conn.executescript("""
            CREATE TABLE term_code_support (
                release TEXT, target_id TEXT, aspect TEXT, go_id TEXT,
                polarity TEXT, evidence_code TEXT,
                support_count INTEGER, direct_count INTEGER,
                PRIMARY KEY (
                    release, target_id, aspect, go_id,
                    polarity, evidence_code
                )
            ) WITHOUT ROWID;
        """)
        rows = [
            ("2023_01", "T000000001", "F", "GO:0000002", "POSITIVE", "IDA", 1, 1),
            ("2023_01", "T000000001", "F", "GO:0000004", "POSITIVE", "IEA", 1, 1),
            ("2023_01", "T000000001", "F", "GO:0000006", "NOT", "IKR", 1, 1),
            ("2023_01", "T000000001", "F", "GO:0000007", "NOT", "IKR", 1, 0),
            ("2026_03", "T000000001", "F", "GO:0000001", "POSITIVE", "IDA", 1, 1),
            ("2026_03", "T000000001", "F", "GO:0000002", "POSITIVE", "IDA", 1, 0),
            ("2026_03", "T000000001", "F", "GO:0000004", "POSITIVE", "IDA", 1, 1),
            ("2026_03", "T000000001", "F", "GO:0000007", "POSITIVE", "IDA", 1, 1),
            ("2026_03", "T000000001", "F", "GO:0000008", "POSITIVE", "IDA", 1, 1),
            ("2026_03", "T000000001", "P", "GO:0000009", "POSITIVE", "IDA", 1, 1),
            ("2026_03", "T000000002", "F", "GO:0000001", "POSITIVE", "IDA", 1, 1),
        ]
        conn.executemany("INSERT INTO term_code_support VALUES (?,?,?,?,?,?,?,?)", rows)
    propagated = bundle / "propagated_terms.tsv"
    write(propagated, "release\ttarget_id\taspect\tgo_id\n")
    output_files = {}
    for path in (db, propagated):
        output_files[path.name] = {
            "bytes": path.stat().st_size,
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        }
    manifest = {
        "schema_version": "stage08-m10c-compact-interpretation-v1",
        "evaluation_policy": "not_computed",
        "not_policy": "preserved_no_veto",
        "ontology_sha256": "a" * 64,
        "distinct_propagated_terms": len({row[:4] for row in rows}),
        "outputs": output_files,
    }
    write(bundle / "manifest.json", json.dumps(manifest))
    mapping = tmp_path / "targets.tsv"
    write(
        mapping,
        (
            "target_id\tncbi_taxid\tuniprot_accession\n"
            "T000000001\t10090\tQ9CQV8\n"
            "T000000002\t10090\tP62259\n"
        ),
    )
    stage01 = tmp_path / "01_targets.tsv"
    write(
        stage01,
        (
            "target_id\traw_taxid\toptional_accession\t"
            "normalized_sequence\tsequence_length\tsequence_sha256\t"
            "normalization_policy_version\n"
            "T000000001\t10090\tQ9CQV8\tABC\t3\tfoo\tv1\n"
            "T000000002\t10090\tP62259\tDEF\t3\tbar\tv1\n"
        ),
    )
    registry = tmp_path / "registry.json"
    write(
        registry,
        json.dumps(
            {
                "schema_version": "probe-evidence-registry-v1",
                "registry_id": "test",
                "origin": "test",
                "codes": {
                    code: {"source": "test", "description": code}
                    for code in ("IDA", "IEA", "IKR")
                },
                "groups": {},
            }
        ),
    )
    selection = {
        "schema_version": "probe-evidence-selections-v1",
        "registry_id": "test",
        "profiles": {
            "score": {
                "include_groups": [],
                "include_codes": ["IDA"],
                "exclude_codes": [],
            },
            "prior": {
                "include_groups": [],
                "include_codes": ["IEA", "IDA"],
                "exclude_codes": [],
            },
            "neutral": {
                "include_groups": [],
                "include_codes": [],
                "exclude_codes": [],
            },
            "not": {
                "include_groups": [],
                "include_codes": ["IKR"],
                "exclude_codes": [],
            },
        },
        "purposes": {name: name for name in ("score", "prior", "neutral", "not")},
    }
    policy = tmp_path / "policy.json"
    write(
        policy,
        json.dumps(
            {
                "schema_version": "stage08-temporal-eval-skip-policy-v1",
                "evidence_selections": selection,
                "not_roles": ["START", "END"],
                "scoring_scope": "target_aspects_with_eval",
                "not_precedence": "not_is_fp",
                "benchmark_mode": "novel_acquisition",
            }
        ),
    )
    graph = GoClosureIndex(
        {**{f"GO:{i:07d}": "F" for i in range(1, 9)}, "GO:0000009": "P"},
        [("GO:0000001", "is_a", "GO:0000002"), ("GO:0000007", "is_a", "GO:0000006")],
    )
    return {
        "m10c_bundle": bundle,
        "targets_input": mapping,
        "stage01_targets": stage01,
        "registry_path": registry,
        "policy_path": policy,
        "graph": graph,
        "start_release": "2023_01",
        "end_release": "2026_03",
        "destination": tmp_path / "benchmark",
    }


def test_preflight_never_creates_output(synthetic):
    report = build_temporal_benchmark(**synthetic)
    assert report["mode"] == "PREFLIGHT_ONLY"
    assert report["original_target_ids"] == 2
    assert not synthetic["destination"].exists()


def test_eval_skip_not_fp_and_original_accession(synthetic):
    result = build_temporal_benchmark(**synthetic, execute=True)
    assert result["output_published"] is True
    rows = table(synthetic["destination"] / "benchmark_terms.tsv")
    labels = {(r["query_id"], r["aspect"], r["go_id"]): r["status"] for r in rows}
    assert labels[("Q9CQV8", "F", "GO:0000001")] == "EVAL"
    assert labels[("Q9CQV8", "F", "GO:0000002")] == "SKIP"
    assert labels[("Q9CQV8", "F", "GO:0000004")] == "SKIP"
    assert labels[("Q9CQV8", "F", "GO:0000008")] == "EVAL"
    assert labels[("Q9CQV8", "P", "GO:0000009")] == "EVAL"
    assert labels[("P62259", "F", "GO:0000001")] == "EVAL"
    assert ("Q9CQV8", "F", "GO:0000007") not in labels
    assert result["counts"]["not_positive_overlap"] == 1
    negative = table(synthetic["destination"] / "negative_terms.tsv")
    assert any(x["go_id"] == "GO:0000007" for x in negative)
    aspects = table(synthetic["destination"] / "target_aspects.tsv")
    assert len(aspects) == 3
    events = table(synthetic["destination"] / "temporal_events.tsv")
    types = {(r["go_id"], r["event"]) for r in events}
    assert ("GO:0000001", "DIRECT_REFINEMENT") in types
    assert ("GO:0000008", "DIRECT_BRANCH_ACQUISITION") in types
    assert ("GO:0000009", "DIRECT_NEW_ASPECT") in types
    assert ("GO:0000004", "DIRECT_CONFIRMATION") in types
    assert ("GO:0000001", "DIRECT_NEW_GLOBAL") in types
    for name, meta in result["outputs"].items():
        payload = synthetic["destination"] / name
        assert hashlib.sha256(payload.read_bytes()).hexdigest() == meta["sha256"]


def test_no_overwrite_existing_bundle(synthetic):
    build_temporal_benchmark(**synthetic, execute=True)
    with pytest.raises(FileExistsError, match="already exists"):
        build_temporal_benchmark(**synthetic, execute=True)


def test_target_mapping_duplicate_accession_fails(synthetic):
    with synthetic["targets_input"].open("a") as file:
        file.write("T000000003\t10090\tP62259\n")
    with pytest.raises(ValueError, match="Duplicate"):
        load_target_ids(synthetic["targets_input"])


def test_original_stage01_mismatch_fails(synthetic):
    source = synthetic["stage01_targets"]
    source.write_text(source.read_text().replace("Q9CQV8", "P14893"))
    with pytest.raises(ValueError, match="mismatch"):
        crosscheck_stage01_mapping(source, load_target_ids(synthetic["targets_input"]))


def test_bad_release_cannot_publish(synthetic):
    synthetic["start_release"] = "2023_01"
    synthetic["end_release"] = "2023_01"
    with pytest.raises(ValueError, match="precede"):
        build_temporal_benchmark(**synthetic, execute=True)
    assert not synthetic["destination"].exists()


def test_corrupted_m10c_digest_refuses_publish(synthetic):
    with synthetic["m10c_bundle"].joinpath("propagated_terms.tsv").open("a") as file:
        file.write("bad\n")
    with pytest.raises(ValueError, match="size mismatch"):
        build_temporal_benchmark(**synthetic, execute=True)
    assert not synthetic["destination"].exists()


def test_unknown_evidence_fails_closed(synthetic):
    db = synthetic["m10c_bundle"] / "direct_provenance.sqlite3"
    with sqlite3.connect(db) as conn:
        conn.execute("""UPDATE term_code_support SET evidence_code='XYZ'
            WHERE evidence_code='IEA'""")
    manifest_file = synthetic["m10c_bundle"] / "manifest.json"
    data = json.loads(manifest_file.read_text())
    data["outputs"]["direct_provenance.sqlite3"]["sha256"] = hashlib.sha256(
        db.read_bytes()
    ).hexdigest()
    manifest_file.write_text(json.dumps(data))
    with pytest.raises(ValueError, match="need review"):
        build_temporal_benchmark(**synthetic, execute=True)
    assert not synthetic["destination"].exists()


def test_not_masks_with_profile_disabled(synthetic):
    policy = synthetic["policy_path"]
    data = json.loads(policy.read_text())
    data["evidence_selections"]["profiles"]["not"]["include_codes"] = []
    policy.write_text(json.dumps(data))
    build_temporal_benchmark(**synthetic, execute=True)
    labels = {
        (row["go_id"], row["status"])
        for row in table(synthetic["destination"] / "benchmark_terms.tsv")
    }
    assert ("GO:0000007", "EVAL") in labels


def test_not_without_scope_does_not_mask(synthetic):
    policy = synthetic["policy_path"]
    data = json.loads(policy.read_text())
    data["not_roles"] = ["END"]
    policy.write_text(json.dumps(data))
    build_temporal_benchmark(**synthetic, execute=True)
    labels = {
        (row["go_id"], row["status"])
        for row in table(synthetic["destination"] / "benchmark_terms.tsv")
    }
    assert ("GO:0000007", "EVAL") in labels


def test_empty_score_selection_fails(synthetic):
    policy = synthetic["policy_path"]
    data = json.loads(policy.read_text())
    data["evidence_selections"]["profiles"]["score"]["include_codes"] = []
    policy.write_text(json.dumps(data))
    with pytest.raises(ValueError, match="nonempty END"):
        build_temporal_benchmark(**synthetic)


def test_not_first_and_known_elsewhere(synthetic):
    assert _status(END_SCORE | NOT_START) == ""
    assert _status(START_PRIOR | NOT_END) == ""
    assert _status(START_PRIOR | END_SCORE) == "SKIP"


def _append_identical_second_target(synthetic, *, sequence="DEF", taxon="10090"):
    """Make a second internal target for the original accession P62259."""
    accession = "P62259"
    with synthetic["targets_input"].open("a") as handle:
        handle.write(f"T000000003\t{taxon}\t{accession}\n")
    stage01 = synthetic["stage01_targets"]
    original = stage01.read_text()
    original = original.replace(
        "P62259\tDEF\t3\tbar\t",
        f"P62259\tDEF\t3\t{hashlib.sha256(b'DEF').hexdigest()}\t",
    )
    with stage01.open("w") as handle:
        handle.write(original)
        handle.write(
            f"T000000003\t{taxon}\t{accession}\t{sequence}\t"
            f"{len(sequence)}\t"
            f"{hashlib.sha256(sequence.encode()).hexdigest()}\tv1\n"
        )
    db = synthetic["m10c_bundle"] / "direct_provenance.sqlite3"
    with sqlite3.connect(db) as conn:
        conn.execute(
            "INSERT INTO term_code_support VALUES (?,?,?,?,?,?,?,?)",
            ("2026_03", "T000000003", "F", "GO:0000003", "POSITIVE", "IDA", 1, 1),
        )
    manifest_path = synthetic["m10c_bundle"] / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["distinct_propagated_terms"] += 1
    manifest["outputs"]["direct_provenance.sqlite3"] = {
        "bytes": db.stat().st_size,
        "sha256": hashlib.sha256(db.read_bytes()).hexdigest(),
    }
    write(manifest_path, json.dumps(manifest))


def test_identical_duplicate_combines_support_without_duplicate_query(synthetic):
    _append_identical_second_target(synthetic)
    preflight = build_temporal_benchmark(**synthetic)
    assert preflight["original_target_ids"] == 3
    assert preflight["distinct_original_query_ids"] == 2
    assert preflight["identical_duplicate_target_aliases"] == 1
    result = build_temporal_benchmark(**synthetic, execute=True)
    rows = table(synthetic["destination"] / "benchmark_terms.tsv")
    assert (
        len(
            [
                r
                for r in rows
                if r["query_id"] == "P62259" and r["go_id"] == "GO:0000001"
            ]
        )
        == 1
    )
    assert any(r["query_id"] == "P62259" and r["go_id"] == "GO:0000003" for r in rows)
    aspects = table(synthetic["destination"] / "target_aspects.tsv")
    assert len([r for r in aspects if r["query_id"] == "P62259"]) == 1
    aliases = table(synthetic["destination"] / "duplicate_targets.tsv")
    assert len(aliases) == 1
    assert aliases[0]["canonical_target_id"] == "T000000002"
    assert aliases[0]["duplicate_target_id"] == "T000000003"
    assert result["outputs"]["duplicate_targets.tsv"]["bytes"] > 0


@pytest.mark.parametrize(
    "sequence,taxon",
    [
        ("XYZ", "10090"),
        ("DEF", "9606"),
    ],
)
def test_nonidentical_duplicate_fails_closed(synthetic, sequence, taxon):
    _append_identical_second_target(synthetic, sequence=sequence, taxon=taxon)
    with pytest.raises(ValueError, match="Non-identical targets"):
        build_temporal_benchmark(**synthetic)
    assert not synthetic["destination"].exists()


def test_duplicate_negative_support_vetoes_canonical_positive(synthetic):
    _append_identical_second_target(synthetic)
    db = synthetic["m10c_bundle"] / "direct_provenance.sqlite3"
    with sqlite3.connect(db) as conn:
        conn.execute(
            "INSERT INTO term_code_support VALUES (?,?,?,?,?,?,?,?)",
            ("2026_03", "T000000003", "F", "GO:0000001", "NOT", "IKR", 1, 1),
        )
    manifest_path = synthetic["m10c_bundle"] / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["distinct_propagated_terms"] += 1
    manifest["outputs"]["direct_provenance.sqlite3"] = {
        "bytes": db.stat().st_size,
        "sha256": hashlib.sha256(db.read_bytes()).hexdigest(),
    }
    write(manifest_path, json.dumps(manifest))
    build_temporal_benchmark(**synthetic, execute=True)
    rows = table(synthetic["destination"] / "benchmark_terms.tsv")
    assert not any(
        r["query_id"] == "P62259" and r["go_id"] == "GO:0000001" for r in rows
    )
    assert any(r["query_id"] == "P62259" and r["go_id"] == "GO:0000003" for r in rows)
    negatives = table(synthetic["destination"] / "negative_terms.tsv")
    assert any(
        r["query_id"] == "P62259" and r["go_id"] == "GO:0000001" for r in negatives
    )
