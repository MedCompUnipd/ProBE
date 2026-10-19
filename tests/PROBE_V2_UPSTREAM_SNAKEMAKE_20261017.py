#!/usr/bin/env python3
"""Execute the canonical upstream and all consumers on synthetic inputs only.

This explicit runner is intentionally outside pytest's default test discovery.
It never prepares a ledger, registry, handoff, workload or downstream output.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import time
from pathlib import Path

ROLE_STAGES = ("exact", "ledger", "projection", "lookup")
GLOBAL_STAGES = (
    "handoff",
    "b3",
    "workload",
    "alignment",
    "stage07",
    "m8",
    "m10c",
    "temporal",
)
ANCHORS = {
    "exact": "exact_documentary_keys.summary.json",
    "ledger": "candidate_provenance.summary.json",
    "projection": "primary_candidate_facts.summary.json",
    "lookup": "documentary_nonexact_matches.summary.json",
    "handoff": "documentary_handoff.summary.json",
    "b3": "canonical_stage05_06a.summary.json",
    "workload": "2C_alignment_preview.summary.json",
    "alignment": "canonical_alignment_execution.summary.json",
    "stage07": "manifest.json",
    "m8": "manifest.json",
    "m10c": "manifest.json",
    "temporal": "manifest.json",
}


def _sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _write_json(path: Path, value: dict) -> None:
    with path.open("x", encoding="utf-8") as stream:
        json.dump(value, stream, indent=2, sort_keys=True)
        stream.write("\n")


def _run(command: list[str], *, directory: Path, log: Path) -> None:
    with log.open("x", encoding="utf-8") as stream:
        result = subprocess.run(
            command,
            cwd=directory,
            stdout=stream,
            stderr=subprocess.STDOUT,
            check=False,
        )
    if result.returncode:
        tail = "\n".join(log.read_text(errors="replace").splitlines()[-80:])
        raise RuntimeError(f"Command failed ({result.returncode}); log: {log}\n{tail}")


def _tool_paths(pinned: dict, source_repo: Path, original_repo: Path) -> dict:
    result = {}
    for name, value in pinned["tools"].items():
        original = Path(value)
        if name == "full_dp_binary":
            path = original
        else:
            path = source_repo / original.relative_to(original_repo)
        if not path.is_file() or path.is_symlink():
            raise ValueError(f"Missing/unsafe scientific tool: {name}")
        result[name] = str(path)
    if not os.access(result["full_dp_binary"], os.X_OK):
        raise ValueError("Pinned native C++ DP binary is not executable")
    return result


def _check_synthetic_sources(spec: dict, fixture_root: Path) -> dict:
    inventory = {}
    for group in (spec["inputs"], *spec["per_release"].values()):
        for value in group.values():
            path = Path(value)
            if (
                not path.exists()
                or path.is_symlink()
                or not path.resolve().is_relative_to(fixture_root)
            ):
                raise ValueError(f"Scientific input escapes synthetic root: {path}")
    for path in fixture_root.rglob("*"):
        if path.is_symlink():
            raise ValueError(f"Symlink in synthetic inputs: {path}")
        if path.is_file():
            if path.stat().st_size > 1024 * 1024:
                raise ValueError(f"Unexpectedly large synthetic input: {path}")
            inventory[str(path.relative_to(fixture_root))] = _sha(path)
    return inventory


def _check_native_publication(bundle: Path, stage: str) -> None:
    from probe.stage08_temporal_benchmark import inspect_m10c_bundle
    from probe.stage08_temporal_v2 import SCHEMA

    if stage == "m10c":
        _, manifest = inspect_m10c_bundle(bundle)
    else:
        manifest = json.loads((bundle / "manifest.json").read_text())
        expected = {
            "benchmark_terms.tsv",
            "target_aspects.tsv",
            "term_audit.tsv",
            "negative_terms.tsv",
            "temporal_events.tsv",
            "duplicate_targets.tsv",
            "audit_reasons.json",
        }
        if (
            manifest.get("schema_version") != SCHEMA
            or manifest.get("mode") != "EXECUTE"
            or manifest.get("output_published") is not True
            or set(manifest.get("outputs", {})) != expected
        ):
            raise AssertionError("Temporal V2 publication contract mismatch")
    outputs = manifest["outputs"]
    if {path.name for path in bundle.iterdir()} != {*outputs, "manifest.json"}:
        raise AssertionError(f"Unexpected native bundle members: {bundle}")
    for name, metadata in outputs.items():
        path = bundle / name
        if (
            not path.is_file()
            or path.is_symlink()
            or path.stat().st_size != metadata["bytes"]
            or _sha(path) != metadata["sha256"]
        ):
            raise AssertionError(f"Invalid native publication payload: {path}")


def _check_publications(spec: dict) -> dict:
    from probe.canonical_v2.dag_contract import destination
    from probe.workflow.artifacts import validate_bundle

    nodes = [(stage, role) for stage in ROLE_STAGES for role in ("START", "END")] + [
        (stage, None) for stage in GLOBAL_STAGES
    ]
    observed = {}
    for stage, role in nodes:
        bundle = destination(spec, stage, role)
        anchor = bundle / ANCHORS[stage]
        marker = Path(f"{bundle}.snakemake.done")
        if (
            not bundle.is_dir()
            or bundle.is_symlink()
            or not anchor.is_file()
            or anchor.is_symlink()
            or not marker.is_file()
            or marker.is_symlink()
            or marker.read_text(encoding="utf-8") != _sha(anchor) + "\n"
        ):
            raise AssertionError(f"Incomplete or inconsistent publication: {bundle}")
        if stage in {"stage07", "m8"}:
            validate_bundle(bundle, deep=True)
        elif stage in {"m10c", "temporal"}:
            _check_native_publication(bundle, stage)
        else:
            summary = json.loads(anchor.read_text(encoding="utf-8"))
            key = "outputs_sha256" if stage == "b3" else "output_sha256"
            outputs = summary.get(key)
            if not isinstance(outputs, dict) or not outputs:
                raise AssertionError(f"Missing producer output hashes: {anchor}")
            for relative, digest in outputs.items():
                path = bundle / relative
                if (
                    not path.is_file()
                    or path.is_symlink()
                    or not path.resolve().is_relative_to(bundle.resolve())
                    or _sha(path) != digest
                ):
                    raise AssertionError(f"Invalid producer payload: {path}")
        if any(
            path.name == ".snakemake_timestamp" or path.name.endswith(".snakemake.done")
            for path in bundle.rglob("*")
        ):
            raise AssertionError(f"Snakemake metadata inside a bundle: {bundle}")
        observed[f"{stage}:{role or 'COMMON'}"] = {
            "bundle": str(bundle),
            "anchor_sha256": _sha(anchor),
        }
    receipts = list(Path(spec["output_root"]).rglob("*.snakemake.done"))
    if len(receipts) != len(nodes):
        raise AssertionError("Unexpected number of canonical execution receipts")
    return observed


def run(
    *,
    repo: Path,
    original_repo: Path,
    pinned_path: Path,
    run_root: Path,
) -> dict:
    started = time.monotonic()
    import_paths = [str(repo / "src"), str(repo / "tests")]
    sys.path[:0] = import_paths
    inherited = os.environ.get("PYTHONPATH")
    os.environ["PYTHONPATH"] = os.pathsep.join(
        import_paths + ([inherited] if inherited else [])
    )

    from canonical_v2_upstream_fixture import (
        create_upstream_inputs,
        validate_upstream_outputs,
    )
    from canonical_v2_upstream_go_fixture import (
        create_go_inputs,
        validate_go_outputs,
    )

    import probe
    from probe.canonical_v2.dag_contract import (
        destination,
        discovery_fingerprint,
        policy_fingerprint,
        read_dag_spec,
    )

    if Path(probe.__file__).resolve().parent != repo / "src/probe":
        raise ValueError("Python imported ProBE from a different source tree")
    if run_root.exists() or run_root.is_symlink():
        raise FileExistsError("Synthetic run destination must be new")
    run_root.mkdir(parents=True)
    sources = run_root / "inputs"
    sources.mkdir()
    pinned = read_dag_spec(pinned_path)
    if pinned["policy"] != "same_species_exact":
        raise ValueError("Checkpoint default EXACT policy has changed")
    upstream = create_upstream_inputs(sources / "upstream", repo)
    go = create_go_inputs(
        sources / "go_sources",
        target_rows=upstream["target_rows"],
        targets_input=Path(upstream["inputs"]["targets_input"]),
    )
    spec = {
        "schema": pinned["schema"],
        "mode": "execute",
        "policy": pinned["policy"],
        "releases": pinned["releases"],
        "output_root": str(run_root / "outputs"),
        "inputs": {**upstream["inputs"], **go["inputs"]},
        "per_release": {
            role: {**upstream["per_release"][role], **go["per_release"][role]}
            for role in ("START", "END")
        },
        "tools": _tool_paths(pinned, repo, original_repo),
        "limits": {
            "threads": 1,
            "max_pair_cells": 100_000,
            "max_total_cells": 1_000_000,
            "max_chunk_cells": 1_000_000,
            "partial_sequence": "allow",
            "documentary_fragment": "allow",
        },
    }
    if spec["releases"] != {"START": "2023_01", "END": "2026_03"}:
        raise ValueError("Synthetic fixtures and pinned release identities differ")
    config_path = run_root / "synthetic_spec.json"
    _write_json(config_path, spec)
    spec = read_dag_spec(config_path)
    strict = {**spec, "policy": "strict"}
    if discovery_fingerprint(spec) != discovery_fingerprint(
        strict
    ) or policy_fingerprint(spec) == policy_fingerprint(strict):
        raise AssertionError("Donor policy changed discovery fingerprint")
    before = _check_synthetic_sources(spec, sources)
    if Path(spec["output_root"]).exists():
        raise AssertionError("A canonical output was prepared before Snakemake")
    workdir = run_root / "snakemake-work"
    workdir.mkdir()
    print("CANONICAL_SYNTHETIC_INPUTS_READY=YES", flush=True)
    _run(
        [
            sys.executable,
            "-m",
            "snakemake",
            "--snakefile",
            str(repo / "workflow/canonical_v2.smk"),
            "--directory",
            str(workdir),
            "--cores",
            "1",
            "--executor",
            "local",
            "--config",
            f"canonical_v2_spec={config_path}",
        ],
        directory=repo,
        log=run_root / "snakemake-execution.log",
    )
    if before != _check_synthetic_sources(spec, sources):
        raise AssertionError("A synthetic scientific input changed during execution")
    publications = _check_publications(spec)
    upstream_result = validate_upstream_outputs(spec, upstream["expected"])
    go_result = validate_go_outputs(
        go["expected"],
        m8=destination(spec, "m8"),
        m10c=destination(spec, "m10c"),
        temporal=destination(spec, "temporal"),
    )
    result = {
        "status": "PASS",
        "data_kind": "SYNTHETIC_ONLY",
        "entry_boundary": "prepared 01/02 and discovery/03/04 sources",
        "canonical_outputs_prepared_before_snakemake": 0,
        "upstream_executed_jobs": 11,
        "downstream_executed_jobs": 5,
        "receipts": len(publications),
        "publications": publications,
        "upstream": upstream_result,
        "go_temporal": go_result,
        "discovery_independent_of_donor_policy": True,
        "synthetic_spec_sha256": _sha(config_path),
        "native_dp_binary_sha256": _sha(Path(spec["tools"]["full_dp_binary"])),
        "elapsed_seconds": round(time.monotonic() - started, 3),
        "real_scientific_execution_performed": False,
        "real_data_scientific_execution_performed": False,
        "scientific_kernels_executed_on_synthetic_data": True,
        "original_01_04_producers_cold_start_certified": False,
    }
    _write_json(run_root / "result.json", result)
    print("SNAKEMAKE_SYNTHETIC_UPSTREAM=PASS JOBS=11", flush=True)
    print("SNAKEMAKE_SYNTHETIC_DOWNSTREAM_FROM_NEW_WORKLOAD=PASS JOBS=5")
    print("CANONICAL_V2_UPSTREAM_SYNTHETIC_STATUS=PASS", flush=True)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--original-repo", type=Path, required=True)
    parser.add_argument("--pinned", type=Path, required=True)
    parser.add_argument("--run-root", type=Path, required=True)
    args = parser.parse_args()
    run(
        repo=args.repo.resolve(),
        original_repo=args.original_repo.resolve(),
        pinned_path=args.pinned.resolve(),
        run_root=args.run_root.absolute(),
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
