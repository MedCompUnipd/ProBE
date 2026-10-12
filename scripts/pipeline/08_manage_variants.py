#!/usr/bin/env python3
"""Manage immutable evidence-policy variants and explicit Snakemake runs."""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
from pathlib import Path

from probe.stage08_variants import (
    EDGES,
    create,
    list_variants,
    read_spec,
    remove_variant,
)


def snakemake_args(spec: dict, *, dry_run: bool, cores: int) -> list[str]:
    pixi = shutil.which("pixi")
    if pixi is None:
        raise RuntimeError("Pixi is required for the workflow environment")
    repo = Path(spec["repo"])
    run = Path(spec["run"])
    args = [
        pixi,
        "run",
        "snakemake",
        "--snakefile",
        str(repo / "workflow/Snakefile"),
        "--configfile",
        str(run / "pipeline.stage08.collection.yaml"),
        str(run / "experiments" / spec["experiment_id"] / "experiment.yaml"),
        "--directory",
        str(run),
        "--cores",
        str(cores),
    ]
    if dry_run:
        args.append("--dry-run")
    args.append("stage_08_variant_diagnostics")
    return args


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", type=Path, required=True)
    commands = parser.add_subparsers(dest="command", required=True)
    new = commands.add_parser("create")
    new.add_argument("--name", required=True)
    new.add_argument("--policy", type=Path, required=True)
    new.add_argument("--registry", type=Path, required=True)
    new.add_argument("--ontology", type=Path, required=True)
    new.add_argument("--workflow-config", type=Path)
    new.add_argument("--length-edges", default=EDGES)
    new.add_argument("--min-length", type=int)
    new.add_argument("--max-length", type=int)
    new.add_argument("--taxids")
    commands.add_parser("list")
    diff = commands.add_parser("compare")
    diff.add_argument("first")
    diff.add_argument("second")
    show = commands.add_parser("show")
    show.add_argument("experiment_id")
    for kind in ("plan", "execute"):
        cmd = commands.add_parser(kind)
        cmd.add_argument("experiment_id")
        cmd.add_argument("--cores", type=int, default=1)
    delete = commands.add_parser("remove")
    delete.add_argument("experiment_id")
    delete.add_argument("--delete-unreferenced", action="store_true")
    delete.add_argument("--confirm-id")
    args = parser.parse_args()
    run = args.run.resolve(strict=True)
    repo = Path(__file__).resolve().parents[2]
    if args.command == "create":
        result = create(
            repo=repo,
            run=run,
            name=args.name,
            policy=args.policy,
            registry=args.registry,
            ontology=args.ontology,
            workflow_config=(
                args.workflow_config or run / "pipeline.stage08.collection.yaml"
            ),
            length_edges=args.length_edges,
            min_length=args.min_length,
            max_length=args.max_length,
            taxids=args.taxids,
        )
    elif args.command == "list":
        result = list_variants(run)
    elif args.command == "compare":
        first = read_spec(
            run / "experiments" / args.first / "config.resolved.json",
            check_sources=False,
        )
        second = read_spec(
            run / "experiments" / args.second / "config.resolved.json",
            check_sources=False,
        )
        result = {
            "same_benchmark": first["benchmark_key"] == second["benchmark_key"],
            "same_statistics": (first["diagnostic_key"] == second["diagnostic_key"]),
            "policy_sha256": [first["policy_sha256"], second["policy_sha256"]],
            "filters": [first["filters"], second["filters"]],
            "benchmark_paths": [first["benchmark"], second["benchmark"]],
        }
    elif args.command == "remove":
        result = remove_variant(
            run,
            args.experiment_id,
            confirm=args.confirm_id,
            delete_unreferenced=args.delete_unreferenced,
        )
    else:
        spec_file = run / "experiments" / args.experiment_id
        spec_file = spec_file / "config.resolved.json"
        spec = read_spec(spec_file, check_sources=args.command != "show")
        if args.command == "show":
            result = spec
        else:
            if args.cores < 1:
                parser.error("--cores must be positive")
            dry_run = args.command == "plan"
            command = snakemake_args(spec, dry_run=dry_run, cores=args.cores)
            print("SNAKEMAKE_COMMAND=", " ".join(command), flush=True)
            if dry_run:
                subprocess.run(command, cwd=repo, check=True)
            else:
                marker = spec_file.parent / "RUNNING"
                with marker.open("x", encoding="utf-8") as handle:
                    handle.write("Run started; do not delete this experiment.\n")
                try:
                    subprocess.run(command, cwd=repo, check=True)
                finally:
                    marker.unlink()
            result = {
                "mode": "DRY_RUN" if dry_run else "EXECUTE",
                "experiment_id": args.experiment_id,
                "benchmark": spec["benchmark"],
                "statistics": spec["statistics"],
            }
    print(json.dumps(result, sort_keys=True, indent=2))


if __name__ == "__main__":
    main()
