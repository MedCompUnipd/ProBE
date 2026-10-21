#!/usr/bin/env python3
"""Mandatory real Snakemake raw-to-temporal gate before a fresh 128-target run.

All biological inputs are tiny original-format synthetic files. Every Stage
01--08 output is produced by the production DAG. Missing dependencies fail;
this runner has no skip, mock producer, prepared source bundle, or rule bypass.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import importlib.util
import json
import os
import subprocess
import sys
import time
from pathlib import Path

from canonical_v2_fresh_raw_fixture import (
    ANNOTATIONS,
    RELEASES,
    TARGETS,
    create_raw_fixture,
)

from probe.canonical_v2.dag_contract import destination, read_dag_spec
from probe.canonical_v2.fresh_dag import (
    COMMON_NODES,
    ROLE_NODES,
    all_raw_manifests,
    read_recipe,
)
from probe.canonical_v2.fresh_publication import receipt_path, validate_node_receipt
from probe.workflow.artifacts import sha256_file, validate_bundle


def _table(path: Path) -> list[dict]:
    with path.open(encoding="utf-8", newline="") as stream:
        reader = csv.DictReader(stream, delimiter="\t")
        if not reader.fieldnames or len(reader.fieldnames) != len(
            set(reader.fieldnames)
        ):
            raise AssertionError(f"Invalid TSV header: {path}")
        result = list(reader)
        if any(
            None in row or any(value is None for value in row.values())
            for row in result
        ):
            raise AssertionError(f"Malformed output TSV: {path}")
        return result


def _run(command: list[str], *, cwd: Path, log: Path) -> None:
    print(f"Running synthetic gate: {log.name}", flush=True)
    with log.open("x", encoding="utf-8") as stream:
        result = subprocess.run(
            command,
            cwd=cwd,
            stdout=stream,
            stderr=subprocess.STDOUT,
            check=False,
            timeout=1800,
        )
    if result.returncode:
        tail = "\n".join(log.read_text(errors="replace").splitlines()[-100:])
        raise RuntimeError(
            f"Synthetic Snakemake gate failed ({result.returncode}); {log}\n{tail}"
        )


def _canonical_inventory(spec: dict, recipe_sha: str) -> dict:
    nodes = [(stage, role) for stage in ROLE_NODES for role in ("START", "END")]
    nodes += [(stage, None) for stage in COMMON_NODES]
    inventory = {}
    for stage, role in nodes:
        marker = validate_node_receipt(
            spec=spec, stage=stage, role=role, recipe_sha256=recipe_sha
        )
        bundle = destination(spec, stage, role)
        inventory[f"{stage}:{role or 'COMMON'}"] = {
            "bundle": str(bundle),
            "receipt_sha256": sha256_file(receipt_path(bundle)),
            "payload": {
                str(path.relative_to(bundle)): sha256_file(path)
                for path in sorted(bundle.rglob("*"))
                if path.is_file()
            },
        }
        if not marker or any(
            path.name == ".snakemake_timestamp" for path in bundle.rglob("*")
        ):
            raise AssertionError(
                "Unverified receipt or Snakemake state inside a scientific bundle"
            )
    if len(inventory) != 16:
        raise AssertionError("Incomplete canonical publication graph")
    return inventory


def _raw_inventory(recipe) -> dict:
    inventory = {}
    for name in all_raw_manifests(recipe):
        path = Path(name)
        manifest = validate_bundle(path.parent, deep=True)
        if manifest.role is not None and manifest.release_id != RELEASES[manifest.role]:
            raise AssertionError("Foreign release in synthetic raw publication")
        inventory[str(path)] = sha256_file(path)
    return inventory


def _scientific_snapshot(spec: dict) -> dict:
    """Compare numerical and GO tables across provenance-versioned namespaces."""
    members = {
        "alignment": (
            "2C_06C0_nonexact_directional_glocal.tsv",
            "2C_06C1_canonical_directional_glocal.tsv",
        ),
        "m8": (
            "08_start_source_assertions.tsv",
            "08_end_source_assertions.tsv",
            "08_start_annotation_links.tsv",
            "08_end_annotation_links.tsv",
        ),
        "temporal": (
            "benchmark_terms.tsv",
            "target_aspects.tsv",
            "term_audit.tsv",
            "negative_terms.tsv",
            "temporal_events.tsv",
            "duplicate_targets.tsv",
        ),
    }
    return {
        f"{stage}/{name}": sha256_file(destination(spec, stage) / name)
        for stage, names in members.items()
        for name in names
    }


def _science_checks(spec: dict, run: Path) -> dict:
    raw = run / "work/pipeline-v2"
    target_rows = _table(Path(spec["inputs"]["targets"]))
    if {row["target_id"]: row["normalized_sequence"] for row in target_rows} != {
        target: values[1] for target, values in TARGETS.items()
    }:
        raise AssertionError(
            "Stage 01 changed literal input sequences or target coverage"
        )
    for role in ("START", "END"):
        rows = _table(Path(spec["per_release"][role]["full_exact_pairs"]))
        exact_fragment = [
            row
            for row in rows
            if row["target_id"] == "T2" and row["accession"] == "P20001"
        ]
        if (
            len(exact_fragment) != 1
            or exact_fragment[0]["frag"] != "1"
            or exact_fragment[0]["exact_full_sequence"] != "1"
        ):
            raise AssertionError(
                "FULL discovery did not recover literal IUPAC26 EXACT FRAG=1"
            )
        union = _table(Path(spec["per_release"][role]["unified_discovery"]))
        if not any(
            row["target_id"] == "T2" and row["candidate_accession"] == "P20001"
            for row in union
        ):
            raise AssertionError("Discovery union discarded literal EXACT fragment")
    lookup = _table(
        destination(spec, "lookup", "START") / "documentary_nonexact_matches.tsv"
    )
    merged_candidates = [
        row
        for row in lookup
        if row["target_id"] == "T3" and row["candidate_accession"] == "P30002"
    ]
    if not merged_candidates or any(
        row["candidate_raw_ox"] != "222"
        or row["candidate_resolved_taxid"] != "111"
        or row["target_resolved_taxid"] != "111"
        for row in merged_candidates
    ):
        raise AssertionError(
            "FULL documentary lookup lost a same-resolved-TaxID candidate"
        )
    # Run the identical lookup against the old GO-positive-only OX boundary.
    # The controlled counterexample establishes why FULL OX is an actual input.
    from probe.canonical_v2.documentary_lookup import discover_documentary_nonexact

    checks = run / "checks"
    checks.mkdir()
    filtered = checks / "go_positive_ox_counterexample"
    discover_documentary_nonexact(
        release=RELEASES["START"],
        targets=Path(spec["inputs"]["targets"]),
        target_taxonomy=Path(spec["per_release"]["START"]["taxonomy"]),
        exact_register=destination(spec, "exact", "START"),
        other_exact_register=destination(spec, "exact", "END"),
        other_release=RELEASES["END"],
        independent_index=Path(spec["inputs"]["lineage_index"]),
        ox_taxonomy=raw / "START/02B/02B_ox_taxonomy.tsv",
        full_manifest=Path(spec["per_release"]["START"]["full_manifest"]),
        destination=filtered,
    )
    if any(
        row["candidate_accession"] == "P30002"
        for row in _table(filtered / "documentary_nonexact_matches.tsv")
    ):
        raise AssertionError("Synthetic OX counterexample is not discriminating")
    alignment = destination(spec, "alignment")
    summary = json.loads(
        (alignment / "canonical_alignment_execution.summary.json").read_text()
    )
    selected = summary["dp_gate"]["selected"]
    if selected < 1 or summary["dp_cells"] < 1:
        raise AssertionError(
            "Integration fixture did not exercise admitted NONEXACT DP"
        )
    jobs = _table(alignment / "2C_NONEXACT_alignment_jobs.tsv")
    results = _table(alignment / "2C_06C0_nonexact_directional_glocal.tsv")

    def key(row):
        return row["release"], row["target_id"], row["rid"]

    expected_keys = {key(row) for row in jobs}
    if (
        len(expected_keys) != selected
        or len(results) != selected
        or {key(row) for row in results} != expected_keys
    ):
        raise AssertionError("DP result pair coverage or uniqueness is incomplete")
    if any(
        row["target_global_status"] != "OK" or row["candidate_global_status"] != "OK"
        for row in results
    ):
        raise AssertionError("Directional DP status is not complete")
    metrics = {
        row["metric"]: int(row["value"])
        for row in _table(alignment / "2C_06C0_summary.tsv")
    }
    if (
        metrics["directional_alignment_count"] != 2 * selected
        or metrics["completed_count"] != selected
    ):
        raise AssertionError("Directional DP counts do not match admitted pairs")
    finalized = _table(alignment / "2C_06C1_canonical_directional_glocal.tsv")
    final_keys = {key(row) for row in finalized}
    if len(final_keys) != len(finalized):
        raise AssertionError("Duplicate pairs after Stage 06C1")
    for release in RELEASES.values():
        if (release, "T2", "2") not in final_keys or (
            release,
            "T2",
            "2",
        ) in expected_keys:
            raise AssertionError(
                "Literal EXACT fragment did not meet NONEXACT at Stage 06C1"
            )
    m8 = destination(spec, "m8")
    go_counts = {}
    for role in ("START", "END"):
        source = _table(m8 / f"08_{role.lower()}_source_assertions.tsv")
        allowed = set(ANNOTATIONS[role])
        observed = {
            (row["db_object_id"], row["go_id"], row["evidence_code"]) for row in source
        }
        if not observed <= allowed or any(
            row["release"] != RELEASES[role] for row in source
        ):
            raise AssertionError(
                "GO assertions escaped their actual local release source"
            )
        expected_t1 = {row for row in allowed if row[0] == "P10001"}
        if not expected_t1 <= observed:
            raise AssertionError(
                "Known EXACT target lost actual release-local GO assertions"
            )
        go_counts[role] = len(source)
    terms = _table(destination(spec, "temporal") / "benchmark_terms.tsv")
    by_key = {(row["query_id"], row["go_id"]): row for row in terms}
    for go in ("GO:0000001", "GO:0000002"):
        if by_key.get(("P10001", go), {}).get("status") != "EVAL":
            raise AssertionError(
                "Temporal benchmark lost confirmation or novel GO acquisition"
            )
    audit = _table(destination(spec, "temporal") / "term_audit.tsv")
    reasons = {(row["query_id"], row["go_id"]): row["reason"] for row in audit}
    if (
        reasons.get(("P10001", "GO:0000001")) != "CONFIRMATION"
        or reasons.get(("P10001", "GO:0000002")) != "NOVEL"
    ):
        raise AssertionError(
            "Temporal confirmation/novel labels differ from raw GO evidence"
        )
    return {
        "accepted_targets": len(target_rows),
        "admitted_dp_pairs": selected,
        "dp_cells": summary["dp_cells"],
        "release_local_go_source_rows": go_counts,
        "full_ox_documentary_counterexample": "VERIFIED",
        "literal_fragment_both_releases": "VERIFIED",
    }


def _native_restart_smoke(python: str, run_root: Path) -> dict:
    """Verify native missing/unfinished stage handling with atomic publisher.

    This is a production publisher exercised by real Snakemake. The test does
    not alter any scientific stage or use --allowed-rules.
    """
    scratch = run_root / "native-restart-smoke"
    scratch.mkdir()
    snakefile = scratch / "Snakefile"
    snakefile.write_text(
        "from pathlib import Path\n"
        "from probe.workflow.artifacts import atomic_stage_bundle\n"
        "rule all:\n"
        "    input: 'bundle/manifest.json', 'bundle/result.tsv'\n"
        "rule publish:\n"
        "    output: 'bundle/manifest.json', 'bundle/result.tsv'\n"
        "    run:\n"
        "        with atomic_stage_bundle(Path('bundle'), "
        "stage='SMOKE', role=None, release_id=None, "
        "parameters={}, ontology=None, "
        "implementation={'name':'native-smoke','version':'1'}, "
        "allow_invalidated_destination=True) as staging:\n"
        "            (staging / 'result.tsv').write_text('key\\tvalue\\nA\\t1\\n')\n",
        encoding="utf-8",
    )
    command = [
        python,
        "-m",
        "snakemake",
        "--snakefile",
        str(snakefile),
        "--directory",
        str(scratch),
        "--cores",
        "1",
        "--rerun-incomplete",
        "all",
    ]
    _run(command, cwd=scratch, log=scratch / "01_initial.log")
    bundle = scratch / "bundle"
    from probe.workflow.artifacts import validate_bundle

    validate_bundle(bundle, deep=True)
    payload = bundle / "result.tsv"
    original = payload.read_bytes()
    payload.unlink()
    _run(command, cwd=scratch, log=scratch / "02_missing_member.log")
    if payload.read_bytes() != original:
        raise AssertionError("Native restart did not reconstruct missing member")
    validate_bundle(bundle, deep=True)
    (bundle / "manifest.json").unlink()
    payload.write_bytes(b"incomplete\n")
    _run(command, cwd=scratch, log=scratch / "03_interrupted_output.log")
    if payload.read_bytes() != original:
        raise AssertionError("Native restart kept interrupted stage output")
    validate_bundle(bundle, deep=True)
    return {
        "status": "PASS",
        "missing_member": "RECOMPUTED",
        "interrupted_stage": "RECOMPUTED",
    }


def run_gate(*, repo: Path, run_root: Path, dp_binary: Path) -> dict:
    if importlib.util.find_spec("snakemake") is None:
        raise RuntimeError(
            "Mandatory Snakemake integration dependency is unavailable; "
            "no test may be skipped"
        )
    repo = repo.resolve(strict=True)
    dp_binary = dp_binary.resolve(strict=True)
    if not dp_binary.is_relative_to(repo) or not os.access(dp_binary, os.X_OK):
        raise ValueError(
            "Synthetic gate requires the source snapshot's compiled DP binary"
        )
    os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
    os.environ["PYTHONPATH"] = os.pathsep.join((str(repo / "src"), str(repo / "tests")))
    recipe_path = create_raw_fixture(repo=repo, run=run_root, dp_binary=dp_binary)
    recipe = read_recipe(recipe_path, expected_repo=repo)
    os.environ["XDG_CACHE_HOME"] = str(run_root / "cache")
    command = [
        sys.executable,
        "-m",
        "snakemake",
        "--snakefile",
        str(repo / "workflow/canonical_v2_fresh.smk"),
        "--directory",
        str(run_root),
        "--config",
        f"fresh_recipe={recipe_path}",
        "--cores",
        "1",
        "--resources",
        "dataset_io=1",
        "--rerun-incomplete",
    ]
    start = time.monotonic()
    _run(
        [*command, "--dry-run", "all"],
        cwd=run_root,
        log=run_root / "01_cold_dry_run.log",
    )
    if any(
        (run_root / name).exists() for name in ("work", "results", "canonical", "plans")
    ):
        raise AssertionError("Cold dry-run executed or prepared a scientific producer")
    _run([*command, "all"], cwd=run_root, log=run_root / "02_raw_to_temporal.log")
    spec_path = recipe.plan_path("canonical_spec")
    spec = read_dag_spec(spec_path)
    raw_inventory = _raw_inventory(recipe)
    before = _canonical_inventory(spec, recipe.sha256)
    science = _science_checks(spec, run_root)
    science_before = _scientific_snapshot(spec)
    # Simulate interruption precisely after atomic scientific publication and
    # before external completion receipt. Resume must verify and recover it.
    lost = receipt_path(destination(spec, "alignment"))
    lost.unlink()
    _run([*command, "all"], cwd=run_root, log=run_root / "03_receipt_recovery.log")
    recovered = _canonical_inventory(spec, recipe.sha256)
    if {key: value["payload"] for key, value in recovered.items()} != {
        key: value["payload"] for key, value in before.items()
    }:
        raise AssertionError(
            "Receipt recovery rewrote an immutable scientific publication"
        )
    # Native Snakemake restart semantics are exercised in an isolated
    # producer using the same ProBE atomic stage-bundle publisher. Deliberate
    # corruption of a completed scientific output is NOT required to test
    # interrupted jobs and must not alter certified GO/DP fixtures.
    smoke = _native_restart_smoke(sys.executable, run_root)
    _run([*command, "all"], cwd=run_root, log=run_root / "05_clean_restart.log")
    spec_after = read_dag_spec(spec_path)
    _raw_inventory(recipe)
    after = _canonical_inventory(spec_after, recipe.sha256)
    if spec_after != spec or after != before:
        raise AssertionError("Clean restart altered scientific publications")
    if _scientific_snapshot(spec_after) != science_before:
        raise AssertionError("Clean restart modified release-local GO or DP")
    raw_lock = json.loads((run_root / "raw_fixture.identity.json").read_text())
    actual_inputs = {
        str(path.relative_to(run_root / "inputs")): hashlib.sha256(
            path.read_bytes()
        ).hexdigest()
        for path in sorted((run_root / "inputs").rglob("*"))
        if path.is_file()
    }
    if actual_inputs != raw_lock["inputs"]:
        raise AssertionError(
            "Original synthetic inputs were modified during the workflow"
        )
    return {
        "schema": "probe-canonical-v2-fresh-integration-result-v1",
        "status": "PASS",
        "scientific_execution": "REAL_RAW_TO_TEMPORAL_SNAKEMAKE",
        "recipe_sha256": recipe.sha256,
        "raw_producer_manifests": len(raw_inventory),
        "canonical_publications": len(after),
        "science": science,
        "receipt_interruption_recovery": "PASS",
        "native_incomplete_stage_reconstruction": smoke,
        "content_fingerprint_invalidation": "SEPARATE_RUN_ON_CHANGED_INPUT",
        "older_canonical_namespace_unchanged": True,
        "original_inputs_unchanged": True,
        "elapsed_seconds": time.monotonic() - start,
        "canonical_inventory": after,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", required=True, type=Path)
    parser.add_argument("--run-root", required=True, type=Path)
    parser.add_argument("--dp-binary", required=True, type=Path)
    args = parser.parse_args()
    try:
        report = run_gate(
            repo=args.repo, run_root=args.run_root.absolute(), dp_binary=args.dp_binary
        )
        code = 0
    except Exception as error:
        report = {
            "schema": "probe-canonical-v2-fresh-integration-result-v1",
            "status": "FAIL",
            "error_type": type(error).__name__,
            "error": str(error),
        }
        code = 1
    if args.run_root.is_dir():
        report_path = args.run_root / "fresh_integration_report.json"
        if not report_path.exists():
            with report_path.open("x", encoding="utf-8") as stream:
                json.dump(report, stream, sort_keys=True, indent=2)
                stream.write("\n")
    print(
        json.dumps(
            {
                key: value
                for key, value in report.items()
                if key != "canonical_inventory"
            },
            sort_keys=True,
            indent=2,
        ),
        flush=True,
    )
    return code


if __name__ == "__main__":
    raise SystemExit(main())
