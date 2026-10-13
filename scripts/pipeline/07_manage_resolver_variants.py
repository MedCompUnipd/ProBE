#!/usr/bin/env python3
"""Create and plan resolver policy variants without touching canonical artifacts."""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
from pathlib import Path

from probe.stage07_variants import create, list_variants, read_spec


def snakemake_args(spec: dict, *, full: bool, execute: bool) -> list[str]:
    pixi = shutil.which("pixi")
    if not pixi:
        raise RuntimeError("Pixi is required")
    repo = Path(spec["repo"])
    exp = Path(spec["run"]) / "experiments" / spec["experiment_id"]
    rules = [
        "stage_07_resolver_variant",
        "stage_08_resolver_variant",
        "stage_08_compact_resolver_variant",
        "stage_08_benchmark_resolver_variant",
        "stage_08_diagnostics_resolver_variant",
        "stage_08_resolver_diagnostics",
    ]
    command = [
        pixi,
        "run",
        "snakemake",
        "--snakefile",
        str(repo / "workflow/Snakefile"),
        "--configfile",
        str(Path(spec["run"]) / "pipeline.stage08.collection.yaml"),
        str(exp / "experiment.yaml"),
        "--directory",
        spec["run"],
        "--cores",
        "1",
        (
            "stage_08_resolver_diagnostics"
            if full
            else str(Path(spec["paths"]["stage07"]) / "manifest.json")
        ),
        "--allowed-rules",
        *rules,
        "--rerun-triggers",
        "mtime",
    ]
    if not execute:
        command.append("--dry-run")
    return command


def _run(spec: dict, *, full: bool, execute: bool, authorized: bool) -> dict:
    if full and execute and not authorized:
        raise ValueError("Full execution requires --authorize-goa-scan")
    exp = Path(spec["run"]) / "experiments" / spec["experiment_id"]
    marker = exp / "FULL_SCAN_AUTHORIZED"
    running = exp / "RUNNING"
    if marker.exists() or marker.is_symlink() or running.exists():
        raise RuntimeError("Experiment already authorized or running")
    command = snakemake_args(spec, full=full, execute=execute)
    if execute:
        with running.open("x", encoding="utf-8") as stream:
            stream.write(spec["experiment_id"] + "\n")
    try:
        if execute and full:
            with marker.open("x", encoding="utf-8") as stream:
                stream.write(spec["experiment_id"] + "\n")
        subprocess.run(command, cwd=spec["repo"], check=True)
    finally:
        if execute and full:
            marker.unlink(missing_ok=True)
        if execute:
            running.unlink(missing_ok=True)
    return {"command": command, "mode": "EXECUTE" if execute else "DRY_RUN"}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", type=Path, required=True)
    commands = parser.add_subparsers(dest="command", required=True)
    new = commands.add_parser("create")
    new.add_argument("--name", required=True)
    new.add_argument(
        "--subtaxon-policy",
        choices=("strict", "include_audited_same_species"),
        default="strict",
    )
    new.add_argument(
        "--partial-sequence",
        choices=("allow", "report_only"),
        default="allow",
    )
    new.add_argument(
        "--documentary-fragment",
        choices=("allow", "report_only"),
        default="allow",
    )
    commands.add_parser("list")
    show = commands.add_parser("show")
    show.add_argument("experiment_id")
    for action in ("plan", "execute"):
        item = commands.add_parser(action)
        item.add_argument("experiment_id")
        item.add_argument("--full", action="store_true")
        if action == "execute":
            item.add_argument("--authorize-goa-scan", action="store_true")
    args = parser.parse_args()
    run = args.run.resolve(strict=True)
    repo = Path(__file__).resolve().parents[2]
    if args.command == "create":
        result = create(
            repo=repo,
            run=run,
            name=args.name,
            policy={
                "resolver": {"subtaxon_policy": args.subtaxon_policy},
                "annotation_donor": {
                    "partial_sequence": args.partial_sequence,
                    "documentary_fragment": args.documentary_fragment,
                },
            },
        )
    elif args.command == "list":
        result = list_variants(run)
    else:
        spec_file = run / "experiments" / args.experiment_id
        spec_file = spec_file / "config.resolved.json"
        spec = read_spec(spec_file, check_sources=args.command != "show")
        if args.command == "show":
            result = spec
        else:
            result = _run(
                spec,
                full=args.full,
                execute=args.command == "execute",
                authorized=getattr(args, "authorize_goa_scan", False),
            )
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
