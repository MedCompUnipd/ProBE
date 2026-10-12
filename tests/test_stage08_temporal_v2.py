"""Regression for configurable V2 evidence classes and neutral conflicts."""

from __future__ import annotations

import csv
import hashlib
import json
import sqlite3
from pathlib import Path

import pytest
from test_stage08_temporal_benchmark import (
    synthetic as base_synthetic_fixture,  # noqa: F401
)

from probe.stage08_temporal_v2 import (
    IKR0,
    N0N,
    N0S,
    N1N,
    N1S,
    P0N,
    P0S,
    P1N,
    P1S,
    build_temporal_benchmark_v2,
    decide,
    load_classes,
)


@pytest.fixture(name="synthetic")
def synthetic_fixture(request):
    # Re-export the imported source fixture without shadowing its name.
    return request.getfixturevalue("base_synthetic_fixture")


def table(path: Path):
    with path.open(newline="") as handle:
        return list(csv.DictReader(handle, delimiter="\t"))


def v2_setup(synthetic):
    cfg = {
        "schema_version": "stage08-evidence-classes-v1",
        "registry_id": "test",
        "scoring_scope": "target_aspects_with_eval",
        "negative_policy": "evidence_sensitive_neutral",
        "assessment_mode": "acquisition_and_confirmation",
        "classes": {
            k: {"include_groups": [], "include_codes": v, "exclude_codes": []}
            for k, v in {
                "SCORE": ["IDA"],
                "NON_SCORE": ["IEA", "IKR"],
                "EXCLUDED": [],
            }.items()
        },
    }
    path = synthetic["policy_path"]
    path.write_text(json.dumps(cfg), encoding="utf-8")
    synthetic["destination"] = path.parent / "v2_benchmark"
    del synthetic["graph"]
    return synthetic


@pytest.mark.parametrize(
    "flags,expected",
    [
        (P1S, ("EVAL", "NOVEL")),
        (P1S | N1S, ("EVAL", "NOVEL")),
        (P1S | N0S, ("EVAL", "CONFIRMATION")),
        (P1S | P0S, ("SKIP", "ALREADY_SCORE_AT_START")),
        (P1S | N0N, ("EVAL", "NOVEL")),
        (P1S | N1N, ("EVAL", "NOVEL")),
        (P1S | P1N, ("NEUTRAL", "CONFLICT_SCORE_END")),
        (
            P1S | P0N,
            (
                "NEUTRAL",
                "TEMPORAL_REVERSAL_NOT_TO_POSITIVE",
            ),
        ),
        (
            P0S | P1N,
            (
                "NEUTRAL",
                "TEMPORAL_REVERSAL_POSITIVE_TO_NOT",
            ),
        ),
        (P1S | IKR0 | N0N, ("NEUTRAL", "CONFLICT_IKR_IRD")),
        (N1S | P1N, ("", "RESOLVED_NOT_SCORE")),
        (N1S | N1N, ("NEUTRAL", "CONFLICT_NON_SCORE_END")),
        (N1S, ("SKIP", "NON_SCORE_END_ONLY")),
        (N0S, ("SKIP", "NON_SCORE_START_ONLY")),
    ],
)
def test_decision_matrix(flags, expected):
    assert decide(flags) == expected


def test_full_v2_synthetic_integrity(synthetic):
    params = v2_setup(synthetic)
    report = build_temporal_benchmark_v2(**params, execute=False)
    assert report["mode"] == "PREFLIGHT_ONLY"
    assert not params["destination"].exists()
    result = build_temporal_benchmark_v2(**params, execute=True)
    assert result["output_published"] is True
    terms = table(params["destination"] / "benchmark_terms.tsv")
    labeled = {(x["query_id"], x["go_id"]): x["status"] for x in terms}
    assert labeled["Q9CQV8", "GO:0000004"] == "EVAL"
    assert labeled["Q9CQV8", "GO:0000007"] == "NEUTRAL"
    assert labeled["Q9CQV8", "GO:0000002"] == "SKIP"
    assert all(x["status"] in ("EVAL", "SKIP", "NEUTRAL") for x in terms)
    audit = table(params["destination"] / "term_audit.tsv")
    by_go = {x["go_id"]: x for x in audit if x["query_id"] == "Q9CQV8"}
    assert by_go["GO:0000004"]["reason"] == "CONFIRMATION"
    assert by_go["GO:0000007"]["reason"] == "CONFLICT_IKR_IRD"
    assert result["counts"]["benchmark_NEUTRAL"] >= 1
    assert result["counts"]["benchmark_EVAL"] >= 1
    for name, metadata in result["outputs"].items():
        content = (params["destination"] / name).read_bytes()
        assert hashlib.sha256(content).hexdigest() == metadata["sha256"]
    with pytest.raises(FileExistsError):
        build_temporal_benchmark_v2(**params, execute=False)


def test_excluded_code_cannot_veto_score(synthetic):
    params = v2_setup(synthetic)
    p = params["policy_path"]
    cfg = json.loads(p.read_text())
    cfg["classes"]["NON_SCORE"]["include_codes"] = ["IEA"]
    cfg["classes"]["EXCLUDED"]["include_codes"] = ["IKR"]
    p.write_text(json.dumps(cfg))
    build_temporal_benchmark_v2(**params, execute=True)
    terms = table(params["destination"] / "benchmark_terms.tsv")
    labels = {r["go_id"]: r["status"] for r in terms if r["query_id"] == "Q9CQV8"}
    assert labels["GO:0000007"] == "EVAL"


def test_classes_overlap_is_rejected(synthetic):
    params = v2_setup(synthetic)
    p = params["policy_path"]
    cfg = json.loads(p.read_text())
    cfg["classes"]["NON_SCORE"]["include_codes"].append("IDA")
    p.write_text(json.dumps(cfg))
    with pytest.raises(ValueError, match="disjoint"):
        load_classes(params["registry_path"], p)


def test_classes_incomplete_is_rejected(synthetic):
    params = v2_setup(synthetic)
    p = params["policy_path"]
    cfg = json.loads(p.read_text())
    cfg["classes"]["NON_SCORE"]["include_codes"] = ["IEA"]
    p.write_text(json.dumps(cfg))
    with pytest.raises(ValueError, match="Every registered"):
        load_classes(params["registry_path"], p)


def test_dynamic_class_reassignment(synthetic):
    params = v2_setup(synthetic)
    before = load_classes(params["registry_path"], params["policy_path"])[1]
    p = params["policy_path"]
    cfg = json.loads(p.read_text())
    cfg["classes"]["SCORE"]["include_codes"].append("IEA")
    cfg["classes"]["NON_SCORE"]["include_codes"].remove("IEA")
    p.write_text(json.dumps(cfg))
    after = load_classes(params["registry_path"], p)[1]
    assert "IEA" in after.score and "IEA" not in after.non_score
    assert before.digest != after.digest
    build_temporal_benchmark_v2(**params, execute=True)
    terms = table(params["destination"] / "benchmark_terms.tsv")
    label = {(r["query_id"], r["go_id"]): r["status"] for r in terms}
    assert label["Q9CQV8", "GO:0000004"] == "SKIP"


def test_excluded_not_without_score_is_not_eval(synthetic):
    params = v2_setup(synthetic)
    db = params["m10c_bundle"] / "direct_provenance.sqlite3"
    with sqlite3.connect(db) as conn:
        conn.execute("""INSERT INTO term_code_support VALUES
            ('2026_03','T000000002','F','GO:0000011','NOT','IEA',1,1)""")
    mf = params["m10c_bundle"] / "manifest.json"
    data = json.loads(mf.read_text())
    data["distinct_propagated_terms"] += 1
    data["outputs"]["direct_provenance.sqlite3"]["sha256"] = hashlib.sha256(
        db.read_bytes()
    ).hexdigest()
    data["outputs"]["direct_provenance.sqlite3"]["bytes"] = db.stat().st_size
    mf.write_text(json.dumps(data))
    build_temporal_benchmark_v2(**params, execute=True)
    terms = table(params["destination"] / "benchmark_terms.tsv")
    assert not any(r["go_id"] == "GO:0000011" for r in terms)
    negatives = table(params["destination"] / "negative_terms.tsv")
    assert any(r["go_id"] == "GO:0000011" for r in negatives)
