#!/usr/bin/env python3
"""Run the bounded real-pair and isolated fresh128 large-DP validation gates.

The installer runs the test suite before this script. This gate does not repeat
unit tests, modify the baseline, copy completed stages into the new run, or
change admission/discovery policy. All temporary state has an explicit parent.
Run with the checkout's Pixi Python; project imports are deferred until --repo
has been checked. A repeated invocation verifies and reuses completed work.
"""

from __future__ import annotations

import argparse
import copy
import csv
import fcntl
import gzip
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import time
import traceback
from pathlib import Path

BASELINE_HEAD = "086e7960ef955dac515c1e2d6a8f715ebf5fc2e8"
BRANCH = "chatgpt/probe-v2-validated"
RELEASES = {"START": "2023_01", "END": "2026_03"}
SCHEMA = "probe-canonical-v2-large-dp-server-gate-v1"
TARGET_COUNT = 128
MEMORY_BYTES = 536_870_912
CHECKPOINT_ROWS = 256
DIGEST = re.compile(r"[0-9a-f]{64}")
DISCOVERY_BINARIES = (
    "probe-primary-candidate-scan-v2/probe-primary-candidate-scan-v2",
    "probe-full-exact-cross-strain/probe-full-exact-cross-strain-v1",
)


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def regular(path: Path) -> Path:
    require(path.is_file() and not path.is_symlink(), f"Unsafe/missing file: {path}")
    return path


def physical(path: Path, *, create: bool = False) -> Path:
    require(path.is_absolute() and ".." not in path.parts, f"Unsafe path: {path}")
    require(path != Path("/") and path.resolve() == path, f"Nonphysical root: {path}")
    if create:
        path.mkdir(parents=True, exist_ok=True)
    require(path.is_dir() and not path.is_symlink(), f"Missing directory: {path}")
    return path


def overlap(first: Path, second: Path) -> bool:
    return first.is_relative_to(second) or second.is_relative_to(first)


def sha256(path: Path) -> str:
    result = hashlib.sha256()
    with regular(path).open("rb") as stream:
        for block in iter(lambda: stream.read(4 * 1024 * 1024), b""):
            result.update(block)
    return result.hexdigest()


def json_bytes(value: object) -> bytes:
    encoded = json.dumps(value, sort_keys=True, indent=2, allow_nan=False)
    return (encoded + "\n").encode()


def identity(value: object) -> str:
    return hashlib.sha256(json_bytes(value)).hexdigest()


def json_document(path: Path) -> tuple[dict, str]:
    content = regular(path).read_bytes()
    result = json.loads(content)
    require(isinstance(result, dict), f"Expected JSON object: {path}")
    return result, hashlib.sha256(content).hexdigest()


def read_json(path: Path) -> dict:
    return json_document(path)[0]


def write(path: Path, content: bytes, *, immutable: bool = True) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() or path.is_symlink():
        regular(path)
        if path.read_bytes() == content:
            return
        require(not immutable, f"Existing publication differs: {path}")
    descriptor, name = tempfile.mkstemp(prefix=".gate-", dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        descriptor = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    finally:
        temporary.unlink(missing_ok=True)


def table(path: Path, *, limit: int | None = None) -> tuple[list[str], list[dict]]:
    with regular(path).open(encoding="utf-8", newline="") as stream:
        reader = csv.reader(stream, delimiter="\t")
        fields = next(reader, [])
        require(bool(fields) and len(set(fields)) == len(fields), f"Bad TSV: {path}")
        rows = []
        for values in reader:
            require(len(values) == len(fields), f"Malformed TSV row: {path}")
            rows.append(dict(zip(fields, values, strict=True)))
            require(limit is None or len(rows) <= limit, f"TSV scope exceeded: {path}")
    return fields, rows


def pair_key(row: dict) -> tuple[str, str, str]:
    return row["release"], row["target_id"], row["rid"]


def subset_bytes(path: Path, selected: list[tuple[str, str, str]]) -> bytes:
    """Preserve the baseline's literal field representations and row order."""
    lines = regular(path).read_bytes().splitlines(keepends=True)
    fields, rows = table(path)
    require(len(lines) == len(rows) + 1, "Multiline fields are not a native DP TSV")
    require(len(selected) == len(set(selected)), "Duplicate selected pair")
    wanted, found, result = set(selected), [], [lines[0]]
    for line, row in zip(lines[1:], rows, strict=True):
        if pair_key(row) in wanted:
            found.append(pair_key(row))
            result.append(line)
    require(found == selected, f"Selected pair order/coverage differs: {path}")
    require(all(name in fields for name in ("release", "target_id", "rid")), "Bad key")
    return b"".join(result)


def compare_bytes(first: Path, second: Path) -> dict:
    """Compare actual bytes, counting rows in the same streaming pass."""
    digest = hashlib.sha256()
    size = newlines = 0
    with regular(first).open("rb") as left, regular(second).open("rb") as right:
        while True:
            a, b = left.read(4 * 1024 * 1024), right.read(4 * 1024 * 1024)
            if a != b:
                return {"status": "FAIL", "baseline": str(first), "new": str(second)}
            if not a:
                break
            digest.update(a)
            size += len(a)
            newlines += a.count(b"\n")
    return {
        "status": "PASS_BYTE_IDENTICAL",
        "sha256": digest.hexdigest(),
        "bytes": size,
        "data_rows": max(0, newlines - 1),
    }


def payload_inventory(spec: dict, api: dict, *, verify: bool) -> dict:
    """Read anchored output inventories without consulting old source paths."""
    result = {}
    nodes = [(stage, role) for stage in api["ROLE_NODES"] for role in RELEASES]
    nodes += [(stage, None) for stage in api["COMMON_NODES"]]
    require(len(nodes) == 16, "Unexpected canonical publication scope")
    for stage, role in nodes:
        root = api["destination"](spec, stage, role)
        physical(root)
        anchor_name = api["ANCHORS"][stage]
        anchor, anchor_sha256 = json_document(root / anchor_name)
        outputs = {}
        if stage in {"m10c", "temporal"}:
            for name, value in anchor["outputs"].items():
                outputs[name] = {"sha256": value["sha256"], "bytes": value["bytes"]}
        elif stage in {"stage07", "m8"}:
            for item in anchor["outputs"]:
                require(item["path"] not in outputs, "Duplicate manifest output")
                outputs[item["path"]] = {
                    "sha256": item["sha256"],
                    "bytes": item["size_bytes"],
                }
        else:
            key = "outputs_sha256" if stage == "b3" else "output_sha256"
            outputs = {name: {"sha256": digest} for name, digest in anchor[key].items()}
        require(bool(outputs), f"Empty publication inventory: {root}")
        for name, info in outputs.items():
            relative = Path(name)
            require(
                not relative.is_absolute()
                and ".." not in relative.parts
                and relative.as_posix() == name
                and name != ".",
                f"Unsafe manifest output: {name}",
            )
            require(bool(DIGEST.fullmatch(info["sha256"])), "Invalid output SHA256")
            output = regular(root / name)
            require(
                info.get("bytes", output.stat().st_size) == output.stat().st_size,
                f"Publication output size changed: {output}",
            )
            if verify:
                require(sha256(output) == info["sha256"], f"Payload changed: {output}")
            info["bytes"] = output.stat().st_size
        actual = set()
        for path in root.rglob("*"):
            require(not path.is_symlink(), f"Symlink in publication: {path}")
            if path.is_file():
                actual.add(path.relative_to(root).as_posix())
            else:
                require(path.is_dir(), f"Special file in publication: {path}")
        require(actual == {*outputs, anchor_name}, f"Unanchored payload files: {root}")
        result[f"{stage}:{role or 'COMMON'}"] = {
            "root": str(root),
            "anchor": anchor_name,
            "anchor_sha256": anchor_sha256,
            "outputs": outputs,
        }
    return result


def source_inventory(repo: Path, bounded: Path, linear: Path) -> dict:
    paths = {Path(__file__).resolve()}
    for name in ("src/probe", "scripts/pipeline", "workflow", "cpp"):
        root = repo / name
        for path in root.rglob("*"):
            if path.is_file() and (
                path.suffix in {".py", ".smk", ".cpp", ".hpp", ".h"}
                or path.name in {"Snakefile", "CMakeLists.txt"}
            ):
                paths.add(path)
    paths.update((bounded, linear))
    paths.update(repo / ".pixi/build" / name for name in DISCOVERY_BINARIES)
    return {str(path.relative_to(repo)): sha256(path) for path in sorted(paths)}


def target_guard(recipe: dict, pipeline: dict, spec: dict, api: dict) -> dict:
    metadata = Path(recipe["targets_input"])
    fields, rows = table(metadata, limit=TARGET_COUNT)
    require(fields == ["target_id", "ncbi_taxid", "uniprot_accession"], "Target schema")
    require(
        len(rows) == TARGET_COUNT,
        "This gate requires exactly the original 128 targets",
    )
    mapping = api["load_target_ids"](metadata, allow_identical_duplicates=True)
    targets = Path(spec["inputs"]["targets"])
    api["crosscheck_stage01_mapping"](targets, mapping)
    _, normalized = table(targets, limit=TARGET_COUNT)
    require(
        len(normalized) == TARGET_COUNT
        and {row["target_id"] for row in normalized} == set(mapping),
        "Baseline Stage 01 target identities differ",
    )
    other = Path(pipeline["targets"]["metadata"])
    require(
        table(other, limit=TARGET_COUNT) == (fields, rows),
        "Raw target metadata differs",
    )
    fasta = regular(Path(pipeline["targets"]["fasta"]))
    opener = gzip.open if fasta.suffix == ".gz" else open
    identifiers = []
    with opener(fasta, "rt", encoding="utf-8") as stream:
        for line in stream:
            if line.startswith(">"):
                identifiers.append(line[1:].split()[0])
                require(
                    len(identifiers) <= TARGET_COUNT,
                    "Raw FASTA exceeds fresh128 scope",
                )
    require(
        len(identifiers) == TARGET_COUNT and set(identifiers) == set(mapping),
        "Raw FASTA does not contain the baseline's 128 target IDs",
    )
    require(spec["releases"] == RELEASES, "Baseline canonical releases differ")
    require(
        {role: pipeline["releases"][role]["release_id"] for role in RELEASES}
        == RELEASES,
        "Raw pipeline releases differ",
    )
    return {
        "target_count": TARGET_COUNT,
        "target_ids": list(mapping),
        "original_accessions": mapping,
        "releases": RELEASES,
        "targets_input_sha256": sha256(metadata),
        "raw_fasta_sha256": sha256(fasta),
        "stage01_sha256": sha256(targets),
    }


def select_pairs(rows: list[dict]) -> tuple[list[tuple[str, str, str]], int]:
    costs = {
        pair_key(row): int(row["target_global_cells_evaluated"])
        + int(row["candidate_global_cells_evaluated"])
        for row in rows
    }
    require(
        len(costs) == len(rows) and len(costs) >= 2,
        "Insufficient distinct real pairs",
    )
    order = {pair_key(row): number for number, row in enumerate(rows)}
    ascending = sorted(costs, key=lambda key: (costs[key], order[key]))
    small = ascending[0]
    costly = [key for key in reversed(ascending) if costs[key] > costs[small]][:7]
    require(bool(costly), "Real pair costs cannot exercise both exact backends")
    chosen = {small, *costly}
    selected = [pair_key(row) for row in rows if pair_key(row) in chosen]
    threshold = (costs[small] + min(costs[key] for key in costly) - 1) // 2
    require(
        0 < threshold < min(costs[key] for key in costly),
        "Invalid routing threshold",
    )
    return selected, threshold


def controlled_pairs(
    args: argparse.Namespace, spec: dict, api: dict, token: str
) -> dict:
    from probe.canonical_v2 import bounded_dp, large_dp
    from probe.stage2c_alignment_completion import DP_OUTPUT, NONEXACT
    from probe.stage2c_alignment_preview import END_STORE, START_STORE

    alignment = api["destination"](spec, "alignment")
    workload = api["destination"](spec, "workload")
    fields, rows = table(alignment / DP_OUTPUT)
    require(
        fields == list(large_dp.OUTPUT_FIELDS),
        "Baseline DP is not the 49-field contract",
    )
    selected, threshold = select_pairs(rows)
    require(
        [pair_key(row) for row in table(alignment / NONEXACT)[1]]
        == [pair_key(row) for row in rows],
        "Baseline NONEXACT admission and aligned pair identities differ",
    )
    root = args.result_root / "controlled-real-pairs"
    physical(root, create=True)
    jobs = root / "selected.NONEXACT.tsv"
    expected = root / "baseline.directional_glocal.tsv"
    write(jobs, subset_bytes(alignment / NONEXACT, selected))
    write(expected, subset_bytes(alignment / DP_OUTPUT, selected))
    limits = spec["limits"]
    descriptor = {
        "package_identity_sha256": token,
        "pairs": [list(key) for key in selected],
        "jobs_sha256": sha256(jobs),
        "expected_sha256": sha256(expected),
        "max_pair_cells": threshold,
        "max_total_cells": limits["max_total_cells"],
        "max_chunk_cells": limits["max_chunk_cells"],
        "threads": 1,
        "max_dp_memory_bytes": MEMORY_BYTES,
        "checkpoint_rows": CHECKPOINT_ROWS,
    }
    write(root / "identity.json", json_bytes(descriptor))
    publication = root / "complete"
    if publication.exists() or publication.is_symlink():
        physical(publication)
        marker = read_json(publication / "result.json")
        require(
            marker["identity"] == descriptor,
            "Controlled completion identity changed",
        )
        require(
            set(path.name for path in publication.iterdir())
            == {*marker["output_sha256"], "result.json"},
            "Controlled completion has untracked files",
        )
        for name, digest in marker["output_sha256"].items():
            require(sha256(publication / name) == digest, "Controlled output changed")
        comparison = compare_bytes(expected, publication / "alignments.tsv")
        require(
            comparison["status"] == "PASS_BYTE_IDENTICAL", "Controlled result differs"
        )
        return {**marker["result"], "reused_completed_gate": True}
    parameters = {
        "binary": args.bounded_binary,
        "linear_binary": args.linear_binary,
        "jobs": jobs,
        "targets": Path(spec["inputs"]["targets"]),
        "start_store": workload / START_STORE,
        "end_store": workload / END_STORE,
        "releases": RELEASES,
        "max_pair_cells": threshold,
        "max_total_cells": limits["max_total_cells"],
        "max_chunk_cells": limits["max_chunk_cells"],
        "threads": 1,
        "max_dp_memory_bytes": MEMORY_BYTES,
        "checkpoint_rows": CHECKPOINT_ROWS,
        "scratch_root": args.scratch_root,
    }
    checkpoint = args.scratch_root / f"large-dp-controlled-{token}"
    with tempfile.TemporaryDirectory(
        prefix="large-dp-real-", dir=args.scratch_root
    ) as name:
        temporary = Path(name)
        parameters.update(
            plan=temporary / bounded_dp.PLAN, routing=temporary / large_dp.ROUTING
        )
        plan = large_dp.plan_large_dp(**parameters)
        require(
            set(plan["backend_pair_counts"])
            == {bounded_dp.BACKEND, large_dp.LINEAR_BACKEND},
            "Controlled real selection did not exercise both backends",
        )
        execution = large_dp.execute_large_dp(
            **parameters,
            output=temporary / "alignments.tsv",
            summary=temporary / "metrics.tsv",
            checkpoint_root=checkpoint,
            expected_input_sha256=plan["input_sha256"],
        )
        comparison = compare_bytes(expected, temporary / "alignments.tsv")
        require(
            comparison["status"] == "PASS_BYTE_IDENTICAL", "Real DP equivalence failed"
        )
        result = {
            "status": "PASS_49_FIELD_BYTE_EQUIVALENCE",
            "pairs": descriptor["pairs"],
            "pair_count": len(selected),
            "selection": "up to 7 largest + 1 smallest",
            "forced_pair_cell_threshold": threshold,
            "scientific_comparison": comparison,
            "planning": plan,
            "execution": execution,
            "checkpoint_root": str(checkpoint),
            "reused_completed_gate": False,
        }
        staged = Path(tempfile.mkdtemp(prefix=".controlled-", dir=root))
        # An incomplete staging directory is never a completed publication.
        for filename in (
            bounded_dp.PLAN,
            large_dp.ROUTING,
            "alignments.tsv",
            "metrics.tsv",
        ):
            write(staged / filename, (temporary / filename).read_bytes())
        marker = {
            "identity": descriptor,
            "result": result,
            "output_sha256": {path.name: sha256(path) for path in staged.iterdir()},
        }
        write(staged / "result.json", json_bytes(marker))
        os.rename(staged, publication)
        descriptor_fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(descriptor_fd)
        finally:
            os.close(descriptor_fd)
    return result


def new_recipe(
    args: argparse.Namespace, baseline: dict, pipeline: dict, token: str, api: dict
):
    import yaml

    old_repo = Path(baseline["repo_root"])
    data = copy.deepcopy(baseline)
    data.update(
        repo_root=str(args.repo),
        run_root=str(args.new_run_root),
        identity_sha256=token,
        pipeline_config=str(args.new_run_root / "pipeline.stage08.collection.yaml"),
    )
    data["tools"] = {
        key: str(args.repo / Path(value).relative_to(old_repo))
        for key, value in baseline["tools"].items()
    }
    data["tools"]["full_dp_binary"] = str(args.bounded_binary)
    data["large_dp"] = {
        "binary": str(args.linear_binary),
        "max_dp_memory_bytes": MEMORY_BYTES,
        "checkpoint_rows": CHECKPOINT_ROWS,
        "scratch_root": str(args.scratch_root),
    }
    fresh_pipeline = copy.deepcopy(pipeline)
    fresh_pipeline["locations"] = {
        key: str(args.new_run_root / prefix / "pipeline-v2")
        for key, prefix in (("work", "work"), ("output", "results"), ("logs", "logs"))
    }
    require(data["limits"] == baseline["limits"], "Resource/eligibility limits changed")
    for key in baseline:
        if key not in {
            "repo_root",
            "run_root",
            "pipeline_config",
            "identity_sha256",
            "tools",
            "large_dp",
        }:
            require(data[key] == baseline[key], f"Unintended recipe change: {key}")
    require(
        {key: value for key, value in fresh_pipeline.items() if key != "locations"}
        == {key: value for key, value in pipeline.items() if key != "locations"},
        "Pipeline scientific configuration changed",
    )
    write(
        Path(data["pipeline_config"]),
        yaml.safe_dump(fresh_pipeline, sort_keys=False).encode(),
    )
    recipe_path = args.new_run_root / "fresh_recipe.json"
    write(recipe_path, json_bytes(data))
    return api["read_recipe"](recipe_path, expected_repo=args.repo)


def process_record(pid: int) -> dict | None:
    try:
        value = (Path("/proc") / str(pid) / "stat").read_text()
        fields = value.rsplit(")", 1)[1].split()
        return {
            "group": int(fields[2]),
            "start_ticks": int(fields[19]),
            "state": fields[0],
        }
    except (OSError, ValueError, IndexError):
        return None


def live_process_group(group: int) -> bool:
    """Ignore zombies; never terminate an existing worker during recovery."""
    for path in Path("/proc").glob("[0-9]*/stat"):
        try:
            fields = path.read_text().rsplit(")", 1)[1].split()
            if int(fields[2]) == group and fields[0] != "Z":
                return True
        except (OSError, ValueError, IndexError):
            continue
    return False


def snakemake(args: argparse.Namespace, recipe, token: str) -> dict:
    require(
        int(Path("/proc/self/stat").read_text().split("(", 1)[0]) == os.getpid(),
        "Safe Snakemake crash recovery requires /proc in the process PID namespace",
    )
    common = [
        sys.executable,
        "-m",
        "snakemake",
        "--snakefile",
        str(args.repo / "workflow/canonical_v2_fresh.smk"),
        "--directory",
        str(args.new_run_root),
        "--cores",
        str(recipe.data["limits"]["threads"]),
        "--resources",
        "dataset_io=1",
        "--config",
        f"fresh_recipe={recipe.path}",
    ]
    environment = {
        **os.environ,
        **{key: str(args.scratch_root) for key in ("TMPDIR", "TMP", "TEMP")},
    }
    environment["PYTHONPATH"] = str(args.repo / "src") + (
        os.pathsep + environment["PYTHONPATH"] if environment.get("PYTHONPATH") else ""
    )
    state_path = args.new_run_root / "large-dp-snakemake-process.json"
    logs = args.result_root / "logs"
    physical(logs, create=True)
    boot_id = Path("/proc/sys/kernel/random/boot_id").read_text().strip()
    if state_path.exists():
        prior = read_json(state_path)
        require(
            prior["identity_sha256"] == token, "Snakemake invocation identity changed"
        )
        if prior["status"] != "COMPLETE":
            if prior["boot_id"] == boot_id:
                while True:
                    owner = process_record(prior["process_group"])
                    require(
                        owner is None
                        or owner["start_ticks"] == prior["process_start_ticks"],
                        "Recorded Snakemake PID reused; automatic unlock refused",
                    )
                    if not live_process_group(prior["process_group"]):
                        break
                    print(
                        "Waiting for existing Snakemake process group "
                        f"{prior['process_group']}",
                        flush=True,
                    )
                    time.sleep(15)
            # Only a recorded, now-dead process group authorizes stale-lock recovery.
            # A changed boot ID proves all processes from the former boot are gone.
            with (logs / "fresh128.unlock.log").open("ab") as log:
                subprocess.run(
                    [*common, "--unlock"],
                    cwd=args.repo,
                    env=environment,
                    stdout=log,
                    stderr=subprocess.STDOUT,
                    check=True,
                )
    result = {}
    for phase, flags in (("dry-run", ["--dry-run"]), ("execute", [])):
        command = [*common, "--rerun-incomplete", *flags]
        log_path = logs / f"fresh128.{phase}.log"
        print(f"Fresh128 {phase}: {log_path}", flush=True)
        started = time.monotonic()
        with log_path.open("ab") as log:
            log.write(json_bytes({"command": command, "started_unix": time.time()}))
            log.flush()
            process = subprocess.Popen(
                command,
                cwd=args.repo,
                env=environment,
                stdout=log,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
            owner = process_record(process.pid)
            state = {
                "identity_sha256": token,
                "phase": phase,
                "process_group": process.pid,
                "process_start_ticks": owner["start_ticks"] if owner else None,
                "boot_id": boot_id,
                "status": "RUNNING",
                "log": str(log_path),
            }
            write(state_path, json_bytes(state), immutable=False)
            status = process.wait()
            state.update(
                status="COMPLETE" if status == 0 else "FAILED", returncode=status
            )
            write(state_path, json_bytes(state), immutable=False)
        result[phase] = {
            "returncode": status,
            "seconds": time.monotonic() - started,
            "log": str(log_path),
        }
        require(status == 0, f"Fresh128 {phase} failed; inspect {log_path}")
    return result


def normalized_summary(value: dict, *, stage: str, repo: Path) -> tuple[dict, dict]:
    result = copy.deepcopy(value)
    ignored = {}
    fields = {
        "m8": ("input_fingerprint",),
        "temporal": (
            "m10c_manifest_sha256",
            "m8_summary_sha256",
            "source_m10c_outputs",
        ),
    }.get(stage, ())
    for key in fields:
        require(key in result, f"Missing declared provenance field: {stage}.{key}")
        ignored[key] = result.pop(key)
    if stage == "temporal":
        result["implementation_sha256"] = {
            str(Path(path).relative_to(repo)): digest
            for path, digest in result["implementation_sha256"].items()
        }
    return result, ignored


def compare_science(old: dict, new: dict, old_repo: Path, new_repo: Path) -> dict:
    from probe.canonical_v2.large_dp import ROUTING
    from probe.stage2c_alignment_completion import DP_SUMMARY, FINAL_SUMMARY

    require(set(old) == set(new), "Canonical publication scope changed")
    tables = []
    exclusions = []
    for node in old:
        before, after = old[node], new[node]
        for entry in (before, after):
            require(
                sha256(Path(entry["root"]) / entry["anchor"]) == entry["anchor_sha256"],
                f"Publication anchor changed after verification: {entry['root']}",
            )
        baseline_names, new_names = set(before["outputs"]), set(after["outputs"])
        extra = {ROUTING} if node == "alignment:COMMON" else set()
        require(
            baseline_names - extra == new_names - extra,
            f"Output contract changed: {node}",
        )
        for name in sorted(baseline_names | new_names):
            if not name.endswith(".tsv"):
                continue
            if node == "alignment:COMMON" and name in {ROUTING, DP_SUMMARY}:
                for entry in (before, after):
                    if name in entry["outputs"]:
                        require(
                            sha256(Path(entry["root"]) / name)
                            == entry["outputs"][name]["sha256"],
                            f"Operational output changed after verification: {name}",
                        )
                exclusions.append(
                    {
                        "node": node,
                        "file": name,
                        "reason": "execution routing/resources",
                    }
                )
                continue
            comparison = compare_bytes(
                Path(before["root"]) / name, Path(after["root"]) / name
            )
            if comparison["status"] == "PASS_BYTE_IDENTICAL" and (
                comparison["sha256"] != before["outputs"][name]["sha256"]
                or comparison["sha256"] != after["outputs"][name]["sha256"]
            ):
                comparison["status"] = "FAIL_PAYLOAD_CHANGED_AFTER_VERIFICATION"
            tables.append({"node": node, "file": name, **comparison})
    require(bool(tables), "No scientific tables were compared")
    summaries = []
    for stage, name in (
        ("alignment", FINAL_SUMMARY),
        ("stage07", "07_canonical_scientific_summary.json"),
        ("m8", "08_collection_summary.json"),
        ("temporal", "manifest.json"),
    ):
        node = f"{stage}:COMMON"
        values = []
        for entry in (old[node], new[node]):
            pinned = (
                entry["anchor_sha256"]
                if name == entry["anchor"]
                else entry["outputs"][name]["sha256"]
            )
            value, digest = json_document(Path(entry["root"]) / name)
            require(
                digest == pinned,
                f"Scientific summary changed after verification: {name}",
            )
            values.append(value)
        before, after = values
        left, old_provenance = normalized_summary(before, stage=stage, repo=old_repo)
        right, new_provenance = normalized_summary(after, stage=stage, repo=new_repo)
        summaries.append(
            {
                "node": node,
                "file": name,
                "status": "PASS" if left == right else "FAIL",
                "scientific_sha256": identity(left),
                "differing_top_level_fields": sorted(
                    key
                    for key in set(left) | set(right)
                    if key not in left or key not in right or left[key] != right[key]
                ),
                "baseline_chain_provenance": old_provenance,
                "new_chain_provenance": new_provenance,
            }
        )
    for entry in (*old.values(), *new.values()):
        require(
            sha256(Path(entry["root"]) / entry["anchor"]) == entry["anchor_sha256"],
            f"Publication anchor changed during comparison: {entry['root']}",
        )
    passed = all(row["status"].startswith("PASS") for row in [*tables, *summaries])
    return {
        "status": "PASS_ROW_LEVEL_SCIENTIFIC_EQUIVALENCE" if passed else "FAIL",
        "scientific_table_count": len(tables),
        "scientific_summary_count": len(summaries),
        "comparison": (
            "Literal bytes for every anchored scientific TSV; "
            "all scientific JSON fields"
        ),
        "tables": tables,
        "summaries": summaries,
        "operational_table_exclusions": exclusions,
        "summary_normalization": {
            "m8": "Only input_fingerprint is chain provenance",
            "temporal": (
                "Three explicit upstream-chain fields are validated by final "
                "receipts; implementation paths are relative to each source "
                "checkout, with hashes retained"
            ),
        },
    }


def project_api(repo: Path) -> dict:
    sys.path.insert(0, str(repo / "src"))
    from probe.canonical_v2.dag_contract import destination, read_dag_spec
    from probe.canonical_v2.fresh_dag import (
        COMMON_NODES,
        ROLE_NODES,
        read_recipe,
        validate_completed_workflow,
    )
    from probe.canonical_v2.fresh_publication import ANCHORS
    from probe.stage08_temporal_benchmark import (
        crosscheck_stage01_mapping,
        load_target_ids,
    )

    return {
        "destination": destination,
        "read_dag_spec": read_dag_spec,
        "COMMON_NODES": COMMON_NODES,
        "ROLE_NODES": ROLE_NODES,
        "read_recipe": read_recipe,
        "validate_completed_workflow": validate_completed_workflow,
        "ANCHORS": ANCHORS,
        "crosscheck_stage01_mapping": crosscheck_stage01_mapping,
        "load_target_ids": load_target_ids,
    }


def run(args: argparse.Namespace, report: dict) -> None:
    import yaml

    api = project_api(args.repo)
    recipe_path = args.baseline_run / "fresh_recipe.json"
    spec_path = args.baseline_run / "plans/canonical_spec.json"
    baseline = read_json(recipe_path)
    require(
        Path(baseline["run_root"]) == args.baseline_run, "Foreign baseline run_root"
    )
    pipeline_path = args.baseline_run / "pipeline.stage08.collection.yaml"
    require(
        Path(baseline["pipeline_config"]) == pipeline_path, "Foreign baseline pipeline"
    )
    pipeline = yaml.safe_load(regular(pipeline_path).read_text())
    spec = api["read_dag_spec"](spec_path)
    require(
        Path(spec["output_root"]) == args.baseline_run / "canonical",
        "Foreign baseline output root",
    )
    require(
        baseline["policy"] == spec["policy"] and baseline["limits"] == spec["limits"],
        "Baseline recipe and executed canonical policy/resources differ",
    )
    report["target_scope"] = target_guard(baseline, pipeline, spec, api)
    before = payload_inventory(spec, api, verify=True)
    sources = source_inventory(args.repo, args.bounded_binary, args.linear_binary)
    descriptor = {
        "schema": SCHEMA,
        "baseline_head": BASELINE_HEAD,
        "baseline_recipe_sha256": sha256(recipe_path),
        "baseline_spec_sha256": sha256(spec_path),
        "baseline_pipeline_sha256": sha256(pipeline_path),
        "baseline_anchor_sha256": {
            node: entry["anchor_sha256"] for node, entry in before.items()
        },
        "target_scope": report["target_scope"],
        "source_sha256": sources,
        "large_dp": {"memory_bytes": MEMORY_BYTES, "checkpoint_rows": CHECKPOINT_ROWS},
    }
    token = identity(descriptor)
    if args.new_run_root is None:
        args.new_run_root = (
            args.baseline_run.parent / f"canonical-v2-large-dp-fresh128-{token[:20]}"
        )
    for other in (args.repo, args.baseline_run, args.scratch_root):
        require(
            not overlap(args.new_run_root, other),
            f"New run overlaps protected root: {other}",
        )
    physical(args.new_run_root, create=True)
    report.update(
        identity_sha256=token,
        identity=descriptor,
        new_run_root=str(args.new_run_root),
        baseline_publications=before,
    )
    lock_path = args.new_run_root / "large-dp-validation.lock"
    require(not lock_path.is_symlink(), "Unsafe validation lock")
    with lock_path.open("a+b") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        write(
            args.new_run_root / "large-dp-validation-identity.json",
            json_bytes(descriptor),
        )
        recipe = new_recipe(args, baseline, pipeline, token, api)
        # The fresh workflow already requires these discovery executables.
        for name in DISCOVERY_BINARIES:
            require(
                os.access(regular(args.repo / ".pixi/build" / name), os.X_OK),
                "Missing discovery executable",
            )
        print("Controlled real-pair equivalence gate", flush=True)
        report["controlled_real_pairs"] = controlled_pairs(args, spec, api, token)
        write(args.result_root / "validation.json", json_bytes(report), immutable=False)
        report["fresh128_execution"] = snakemake(args, recipe, token)
        new_spec = api["read_dag_spec"](recipe.plan_path("canonical_spec"))
        print("Verifying completed fresh128 stage receipts", flush=True)
        report["fresh128_completion"] = api["validate_completed_workflow"](
            recipe, new_spec
        )
        report["new_target_scope"] = target_guard(
            recipe.data, recipe.pipeline, new_spec, api
        )
        require(
            report["new_target_scope"] == report["target_scope"],
            "Original target identities changed",
        )
        after = payload_inventory(new_spec, api, verify=False)
        report["scientific_equivalence"] = compare_science(
            before, after, Path(baseline["repo_root"]), args.repo
        )
        require(
            report["scientific_equivalence"]["status"]
            == "PASS_ROW_LEVEL_SCIENTIFIC_EQUIVALENCE",
            "Fresh128 scientific equivalence failed; inspect validation.json",
        )
        require(
            source_inventory(args.repo, args.bounded_binary, args.linear_binary)
            == sources,
            "Source changed during validation",
        )
        for path, digest in (
            (recipe_path, descriptor["baseline_recipe_sha256"]),
            (spec_path, descriptor["baseline_spec_sha256"]),
            (pipeline_path, descriptor["baseline_pipeline_sha256"]),
        ):
            require(
                sha256(path) == digest, "Baseline control changed during validation"
            )
        report["status"] = "PASS"


def arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--baseline-run", type=Path, required=True)
    parser.add_argument("--result-root", type=Path, required=True)
    parser.add_argument(
        "--scratch-root",
        type=Path,
        default=Path("/data/PROGENY/tmp/probe-v2-scratch"),
    )
    parser.add_argument("--bounded-binary", type=Path, required=True)
    parser.add_argument("--linear-binary", type=Path, required=True)
    parser.add_argument("--new-run-root", type=Path)
    return parser.parse_args()


def main() -> int:
    args = arguments()
    report = {"schema": SCHEMA, "status": "RUNNING", "started_unix": time.time()}
    report_ready = False
    try:
        physical(args.repo)
        physical(args.baseline_run)
        for path in (args.result_root, args.scratch_root):
            require(
                not overlap(path, args.repo) and not overlap(path, args.baseline_run),
                "Validation output overlaps a protected source",
            )
            require(
                not path.is_relative_to(Path("/tmp")),
                "Use the explicit ProBE scratch root",
            )
            physical(path, create=True)
            if path == args.result_root:
                report_ready = True
        for path in (args.bounded_binary, args.linear_binary):
            require(
                path.is_absolute() and path.is_relative_to(args.repo),
                "DP binary must be in the checked-out repository",
            )
            require(os.access(regular(path), os.X_OK), "DP binary is not executable")
        head = subprocess.check_output(
            ["git", "-C", str(args.repo), "rev-parse", "HEAD"], text=True
        ).strip()
        branch = subprocess.check_output(
            ["git", "-C", str(args.repo), "branch", "--show-current"], text=True
        ).strip()
        require(
            head == BASELINE_HEAD and branch == BRANCH,
            "Repository is not the authorized baseline branch/HEAD",
        )
        os.environ.update(
            {key: str(args.scratch_root) for key in ("TMPDIR", "TMP", "TEMP")}
        )
        report.update(
            repo=str(args.repo),
            baseline_run=str(args.baseline_run),
            baseline_head=head,
            branch=branch,
        )
        run(args, report)
    except BaseException as error:
        report.update(
            status="FAIL",
            exception=type(error).__name__,
            message=str(error),
            traceback=traceback.format_exc(),
        )
        print(report["traceback"], file=sys.stderr, flush=True)
    finally:
        report["finished_unix"] = time.time()
        if report_ready:
            write(
                args.result_root / "validation.json",
                json_bytes(report),
                immutable=False,
            )
            print(
                f"{report['status']}: {args.result_root / 'validation.json'}",
                flush=True,
            )
    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
