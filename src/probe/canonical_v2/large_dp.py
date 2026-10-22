"""Exact backend routing and durable mixed-engine DP checkpoints.

The frozen planner remains authoritative for the certified region. Execution
assignment is a resource decision after documentary/taxonomic admission and
never removes a pair. Scientific tables retain their existing schema.
"""

from __future__ import annotations

import csv
import fcntl
import hashlib
import io
import json
import os
import shutil
import subprocess
import tempfile
from collections import Counter
from pathlib import Path

from probe.canonical_v2 import bounded_dp as frozen

BACKEND = "auto-certified-exact-v1"
LINEAR_BACKEND = "certified-rolling-stats-v1"
LINEAR_VERSION = "probe_directional_glocal_linear_dp_v1"
ROUTING = "2C_06C0_backend_plan.tsv"
CHECKPOINT_CONTRACT = "canonical-v2-mixed-exact-checkpoints-v1"
DEFAULT_MEMORY = 536_870_912
MEMORY_MARGIN = 1_048_576
ROUTING_FIELDS = (
    "release",
    "target_id",
    "rid",
    "target_length",
    "candidate_length",
    "target_global_cells",
    "candidate_global_cells",
    "pair_cells",
    "full_matrix_cells",
    "target_global_bandwidth",
    "candidate_global_bandwidth",
    "bounded_memory_bytes",
    "rolling_memory_bytes",
    "estimated_memory_bytes",
    "chunk_index",
    "backend",
    "routing_reason",
    "invocation_cell_limit",
)
PairKey = frozen.PairKey
OUTPUT_FIELDS = (
    "release",
    "target_id",
    "rid",
    "job_scope",
    "discovery_channel",
    "target_optional_accession",
    "candidate_primary_accession",
    "target_length",
    "candidate_length",
) + tuple(
    f"{prefix}_{suffix}"
    for prefix in ("target_global", "candidate_global")
    for suffix in (
        "status",
        "score",
        "target_start0",
        "target_end0_exclusive",
        "candidate_start0",
        "candidate_end0_exclusive",
        "target_aligned_span",
        "candidate_aligned_span",
        "target_coverage",
        "candidate_coverage",
        "matches",
        "mismatches",
        "gap_in_target_residues",
        "gap_in_candidate_residues",
        "gap_openings",
        "aligned_residue_pairs",
        "alignment_columns",
        "identity_aligned_pairs",
        "identity_alignment_columns",
        "cells_evaluated",
    )
)


def _key(row: dict[str, str]) -> PairKey:
    return row["release"], row["target_id"], row["rid"]


def _positive(value: int, name: str) -> int:
    if type(value) is not int or value < 1:
        raise ValueError(f"Invalid large-DP {name}")
    return value


def _environment(scratch: Path) -> dict[str, str]:
    if not scratch.is_dir() or scratch.is_symlink():
        raise ValueError("Missing or unsafe exact-DP scratch directory")
    return {
        **os.environ,
        **{name: str(scratch.resolve()) for name in ("TMPDIR", "TMP", "TEMP")},
    }


def _limit(
    max_pair_cells: int,
    max_total_cells: int,
    max_chunk_cells: int | None,
    threads: int,
    max_dp_memory_bytes: int,
    checkpoint_rows: int,
) -> int:
    for name, value in (
        ("pair budget", max_pair_cells),
        ("invocation budget", max_total_cells),
        ("threads", threads),
        ("memory budget", max_dp_memory_bytes),
        ("checkpoint rows", checkpoint_rows),
    ):
        _positive(value, name)
    if max_chunk_cells is not None:
        _positive(max_chunk_cells, "chunk budget")
    return min(max_total_cells, max_chunk_cells or max_total_cells)


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _json_text(value: dict) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n"


def _atomic_json(path: Path, value: dict) -> None:
    fd, temporary = tempfile.mkstemp(prefix=".json-", dir=path.parent)
    staged = Path(temporary)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(_json_text(value))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(staged, path)
        _fsync_directory(path.parent)
    finally:
        staged.unlink(missing_ok=True)


def _read_json(path: Path) -> dict:
    if not path.is_file() or path.is_symlink():
        raise ValueError(f"Missing or unsafe exact-DP metadata: {path}")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, UnicodeError) as error:
        raise ValueError("Corrupt exact-DP checkpoint metadata") from error
    if not isinstance(value, dict):
        raise ValueError("Invalid exact-DP checkpoint metadata")
    return value


def _tsv_bytes(fields: tuple | list, rows: list[dict[str, str]]) -> bytes:
    stream = io.StringIO(newline="")
    writer = csv.DictWriter(
        stream, fieldnames=fields, delimiter="\t", lineterminator="\n"
    )
    writer.writeheader()
    writer.writerows(rows)
    return stream.getvalue().encode("utf-8")


def _write_bytes(path: Path, content: bytes) -> None:
    with path.open("xb") as stream:
        stream.write(content)
        stream.flush()
        os.fsync(stream.fileno())


def _assignment_rows(
    *,
    jobs: Path,
    plan: Path,
    releases: dict[str, str],
    max_pair_cells: int,
    invocation_limit: int,
    threads: int,
    max_dp_memory_bytes: int,
) -> tuple[list[str], list[dict[str, str]], list[dict[str, str]]]:
    fields, jobs_rows, expected = frozen._job_plan(jobs, plan)
    geometry = {(*_key(row), row["direction"]): row for row in frozen._plan_rows(plan)}
    ordering = {releases["START"]: 0, releases["END"]: 1}
    previous = None
    assigned = []
    chunk_index = -1
    pending_cells = 0
    previous_backend = None
    for row in jobs_rows:
        key = _key(row)
        if key[0] not in ordering:
            raise ValueError("Foreign release in exact-DP workload")
        order = ordering[key[0]], key[1], int(key[2])
        if previous is not None and order <= previous:
            raise ValueError("Unsorted exact-DP workload")
        previous = order
        n, m = int(row["target_length"]), int(row["candidate_length"])
        directional = expected[key]
        cells = sum(directional)
        widths = [
            min(
                m + 1,
                int(geometry[(*key, direction)]["diagonal_max"])
                - int(geometry[(*key, direction)]["diagonal_min"])
                + 1,
            )
            for direction in frozen.DIRECTIONS
        ]
        # The frozen engine retains one byte of trace per visited cell and
        # six int32 score rows, plus uint64 row offsets, per active worker.
        bounded_memory = (
            threads * (max(directional) + 24 * (m + 1) + 8 * (n + 2))
            + 2 * (n + m)
            + MEMORY_MARGIN
        )
        # Native static_assert: three 32-byte states/cell, two compact rows.
        rolling_memory = 192 * max(widths) + 2 * (n + m) + MEMORY_MARGIN
        reasons = []
        if cells > max_pair_cells:
            reasons.append("PAIR_CELL_LIMIT")
        if cells > invocation_limit:
            reasons.append("INVOCATION_CELL_LIMIT")
        if bounded_memory > max_dp_memory_bytes:
            reasons.append("BOUNDED_MEMORY_LIMIT")
        engine = LINEAR_BACKEND if reasons else frozen.BACKEND
        if (
            engine == LINEAR_BACKEND
            or previous_backend != engine
            or pending_cells + cells > invocation_limit
        ):
            chunk_index += 1
            pending_cells = 0
        if engine == frozen.BACKEND:
            pending_cells += cells
        previous_backend = engine
        assigned.append(
            dict(
                zip(
                    ROUTING_FIELDS,
                    map(
                        str,
                        (
                            *key,
                            n,
                            m,
                            *directional,
                            cells,
                            2 * (n + 1) * (m + 1),
                            *widths,
                            bounded_memory,
                            rolling_memory,
                            (
                                rolling_memory
                                if engine == LINEAR_BACKEND
                                else bounded_memory
                            ),
                            chunk_index,
                            engine,
                            ";".join(reasons) or "WITHIN_BOUNDED_LIMITS",
                            invocation_limit,
                        ),
                    ),
                    strict=True,
                )
            )
        )
    return fields, jobs_rows, assigned


def plan_large_dp(
    *,
    binary: Path,
    linear_binary: Path,
    jobs: Path,
    targets: Path,
    start_store: Path,
    end_store: Path,
    releases: dict[str, str],
    plan: Path,
    routing: Path,
    max_pair_cells: int,
    max_total_cells: int,
    max_chunk_cells: int | None = None,
    threads: int = 1,
    max_dp_memory_bytes: int = DEFAULT_MEMORY,
    checkpoint_rows: int = 256,
    scratch_root: Path | None = None,
) -> dict:
    """Inventory every admitted pair, then assign an exact execution engine."""
    invocation_limit = _limit(
        max_pair_cells,
        max_total_cells,
        max_chunk_cells,
        threads,
        max_dp_memory_bytes,
        checkpoint_rows,
    )
    if routing.exists() or routing.is_symlink():
        raise FileExistsError("Exact-DP routing destination occupied")
    if not linear_binary.is_file() or linear_binary.is_symlink():
        raise ValueError("Missing or unsafe rolling-DP binary")
    linear_sha = frozen._sha(linear_binary)
    version = subprocess.run(
        [str(linear_binary), "--version"],
        check=True,
        capture_output=True,
        text=True,
        env=_environment(scratch_root or routing.parent),
    ).stdout.strip()
    if version != LINEAR_VERSION:
        raise ValueError("Unexpected rolling-DP implementation")
    try:
        report = frozen.plan_bounded_dp(
            binary=binary,
            jobs=jobs,
            targets=targets,
            start_store=start_store,
            end_store=end_store,
            releases=releases,
            plan=plan,
            max_pair_cells=max_pair_cells,
            max_total_cells=max_total_cells,
            max_chunk_cells=max_chunk_cells,
        )
    except frozen.BudgetExceeded as error:
        # The frozen function raises only after writing and validating the
        # complete plan. Preserve real limits; routing resolves this budget.
        report = error.report
    _, _, assignments = _assignment_rows(
        jobs=jobs,
        plan=plan,
        releases=releases,
        max_pair_cells=max_pair_cells,
        invocation_limit=invocation_limit,
        threads=threads,
        max_dp_memory_bytes=max_dp_memory_bytes,
    )
    if linear_sha != frozen._sha(linear_binary):
        raise ValueError("Rolling-DP binary changed during planning")
    _write_bytes(routing, _tsv_bytes(ROUTING_FIELDS, assignments))
    report.update(
        {
            "execution_backend": BACKEND,
            "execution_contract": CHECKPOINT_CONTRACT,
            "invocation_cell_limit": invocation_limit,
            "routing_sha256": frozen._sha(routing),
            "max_dp_memory_bytes": max_dp_memory_bytes,
            "checkpoint_rows": checkpoint_rows,
            "threads": threads,
            "backend_pair_counts": dict(
                sorted(Counter(row["backend"] for row in assignments).items())
            ),
            "planned_chunks": (
                int(assignments[-1]["chunk_index"]) + 1 if assignments else 0
            ),
            "memory_estimate_contract": (
                "DP workspace plus sequence storage and explicit 1-MiB margin; "
                "not an operating-system process RSS limit"
            ),
            "engine_provenance": {
                frozen.BACKEND: {
                    "version": frozen.BINARY_VERSION,
                    "binary_sha256": frozen._sha(binary),
                },
                LINEAR_BACKEND: {
                    "version": LINEAR_VERSION,
                    "binary_sha256": linear_sha,
                },
            },
        }
    )
    report["input_sha256"][str(linear_binary)] = linear_sha
    if report["input_sha256"] != {
        path: frozen._sha(Path(path)) for path in report["input_sha256"]
    } or report["plan_sha256"] != frozen._sha(plan):
        raise ValueError("Exact-DP inputs changed during resource planning")
    report["planned_invocations_min"] = sum(
        (int(row["pair_cells"]) + invocation_limit - 1) // invocation_limit
        for row in assignments
        if row["backend"] == LINEAR_BACKEND
    ) + len(
        {row["chunk_index"] for row in assignments if row["backend"] == frozen.BACKEND}
    )
    if any(
        int(row["estimated_memory_bytes"]) > max_dp_memory_bytes for row in assignments
    ):
        raise frozen.BudgetExceeded(
            "INCOMPLETE_MEMORY: exact-DP workspace exceeds memory budget", report
        )
    return report


def _validate_chunk(
    *,
    folder: Path,
    expected_jobs: list[dict[str, str]],
    expected_cells: dict[PairKey, tuple[int, int]],
    threads: int,
) -> dict[str, int]:
    metrics = frozen._chunk_metrics(
        result_path=folder / "alignments.tsv",
        metric_path=folder / "metrics.tsv",
        expected=expected_cells,
        threads=threads,
    )
    fields, results = frozen._rows(folder / "alignments.tsv")
    if fields != list(OUTPUT_FIELDS):
        raise ValueError("Exact-DP output differs from frozen 49-column schema")
    if [_key(row) for row in results] != [_key(row) for row in expected_jobs]:
        raise ValueError("Exact-DP chunk keys differ from canonical order")
    for result, job in zip(results, expected_jobs, strict=True):
        for name in fields[:9]:
            if name in job and result[name] != job[name]:
                raise ValueError("Exact-DP output/job identity mismatch")
        if any(
            result[f"{prefix}_status"] != "OK"
            for prefix in ("target_global", "candidate_global")
        ):
            raise ValueError("Incomplete exact-DP direction")
    return metrics


def _run_linear(
    *,
    command: list[str],
    active: Path,
    identity: str,
    output: Path,
    summary: Path,
    threads: int,
    cells: int,
    invocation_limit: int,
    memory_limit: int,
    expected_memory_bytes: int,
    checkpoint_rows: int,
    environment: dict[str, str],
) -> dict:
    if active.is_symlink():
        raise ValueError("Unsafe rolling-DP checkpoint directory")
    active.mkdir(parents=True, exist_ok=True)
    computed = 0
    invocations = 0
    previous = None
    reused = 0
    while True:
        result = subprocess.run(
            [
                *command,
                "--output",
                str(output),
                "--summary",
                str(summary),
                "--threads",
                str(threads),
                "--checkpoint-dir",
                str(active),
                "--checkpoint-identity",
                identity,
                "--checkpoint-rows",
                str(checkpoint_rows),
                "--memory-limit-bytes",
                str(memory_limit),
                "--max-total-cells",
                str(invocation_limit),
            ],
            check=False,
            env=environment,
        )
        if result.returncode not in {0, 75}:
            raise subprocess.CalledProcessError(result.returncode, command)
        progress = _read_json(active / "progress.json")
        if (
            progress.get("schema") != "probe-rolling-progress-v1"
            or progress.get("backend") != LINEAR_BACKEND
            or progress.get("identity") != identity
            or progress.get("job_index") != 0
            or progress.get("direction") not in frozen.DIRECTIONS
            or progress.get("execution_threads") != 1
            or progress.get("reserved_threads") != threads
            or progress.get("quantum_cells") != invocation_limit
            or progress.get("checkpoint_rows") != checkpoint_rows
        ):
            raise ValueError("Rolling-DP progress identity or resource mismatch")
        delta = progress.get("invocation_computed_cells")
        resumed = progress.get("resumed_cells")
        required = progress.get("required_memory_bytes")
        if (
            type(delta) is not int
            or type(resumed) is not int
            or type(required) is not int
            or not 0 <= delta <= invocation_limit
            or not 0 <= resumed <= cells
            or not 0 < required <= memory_limit
            or required != expected_memory_bytes
            or resumed + delta > cells
            or progress.get("completed_cells") != resumed + delta
            or (previous is not None and resumed != previous)
        ):
            raise ValueError("Invalid rolling-DP checkpoint progress")
        if previous is None:
            reused = resumed
        previous = resumed + delta
        expected_status = "COMPLETE" if result.returncode == 0 else "CHECKPOINTED"
        if progress.get("status") != expected_status:
            raise ValueError("Rolling-DP status/exit-code disagreement")
        if progress.get("signal", 0):
            raise InterruptedError("Rolling-DP stopped after a checkpoint signal")
        if result.returncode == 75 and (delta == 0 or previous == cells):
            raise ValueError("Rolling-DP checkpoint made no valid forward progress")
        if result.returncode == 0 and previous != cells:
            raise ValueError("Rolling-DP completion omitted certified cells")
        # A crash can occur after the native completed checkpoint is durable
        # but before the chunk is committed. Zero-work output reconstruction
        # is valid only when the checkpoint already contains all cells.
        if delta == 0 and (result.returncode != 0 or resumed != cells):
            raise ValueError("Rolling-DP invocation made no progress")
        invocations += 1
        computed += delta
        if result.returncode == 0:
            return {
                "invocations": invocations,
                "newly_computed_cells": computed,
                "reused_checkpoint_cells": reused,
                "required_memory_bytes": required,
                "execution_threads": 1,
            }


def execute_large_dp(
    *,
    binary: Path,
    linear_binary: Path,
    jobs: Path,
    targets: Path,
    start_store: Path,
    end_store: Path,
    releases: dict[str, str],
    plan: Path,
    routing: Path,
    output: Path,
    summary: Path,
    threads: int,
    max_pair_cells: int,
    max_total_cells: int,
    checkpoint_root: Path,
    max_chunk_cells: int | None = None,
    expected_input_sha256: dict[str, str] | None = None,
    max_dp_memory_bytes: int = DEFAULT_MEMORY,
    checkpoint_rows: int = 256,
    scratch_root: Path | None = None,
) -> dict:
    """Execute the exact resource plan and reuse every verified durable chunk."""
    limit = _limit(
        max_pair_cells,
        max_total_cells,
        max_chunk_cells,
        threads,
        max_dp_memory_bytes,
        checkpoint_rows,
    )
    fields, jobs_rows, assignments = _assignment_rows(
        jobs=jobs,
        plan=plan,
        releases=releases,
        max_pair_cells=max_pair_cells,
        invocation_limit=limit,
        threads=threads,
        max_dp_memory_bytes=max_dp_memory_bytes,
    )
    if not jobs_rows:
        raise ValueError("Use the native empty-DP writer for an empty selection")
    routing_fields, actual_assignments = frozen._rows(routing)
    if tuple(routing_fields) != ROUTING_FIELDS or actual_assignments != assignments:
        raise ValueError("Exact-DP routing differs from verified resource plan")
    if any(
        int(row["estimated_memory_bytes"]) > max_dp_memory_bytes for row in assignments
    ):
        raise frozen.BudgetExceeded("INCOMPLETE_MEMORY: exact-DP workspace", {})
    for path in (output, summary):
        if path.exists() or path.is_symlink():
            raise FileExistsError("Exact-DP destination occupied")
    paths = {
        "binary": binary,
        "linear_binary": linear_binary,
        "jobs": jobs,
        "targets": targets,
        "start_store": start_store,
        "end_store": end_store,
        "plan": plan,
        "routing": routing,
    }
    for path in paths.values():
        if not path.is_file() or path.is_symlink():
            raise ValueError("Missing or unsafe exact-DP input")
    pins = {name: frozen._sha(path) for name, path in paths.items()}
    if expected_input_sha256 is not None:
        actual = {
            str(path): pins[name]
            for name, path in paths.items()
            if name not in {"plan", "routing"}
        }
        if actual != expected_input_sha256:
            raise ValueError("Exact-DP inputs changed since planning")
    expected = frozen.read_plan(plan)
    chunks: list[list[dict[str, str]]] = []
    engines = []
    workspace_sizes = []
    for job, assigned in zip(jobs_rows, assignments, strict=True):
        number = int(assigned["chunk_index"])
        if number == len(chunks):
            chunks.append([])
            engines.append(assigned["backend"])
            workspace_sizes.append(int(assigned["estimated_memory_bytes"]))
        chunks[number].append(job)
    descriptor = {
        "contract": CHECKPOINT_CONTRACT,
        "source_sha256": pins,
        "releases": releases,
        "threads": threads,
        "max_pair_cells": max_pair_cells,
        "invocation_cell_limit": limit,
        "max_dp_memory_bytes": max_dp_memory_bytes,
        "checkpoint_rows": checkpoint_rows,
        "chunk_pair_keys": [[list(_key(row)) for row in chunk] for chunk in chunks],
        "chunk_backends": engines,
    }
    identity = hashlib.sha256(_json_text(descriptor).encode()).hexdigest()
    if checkpoint_root.is_symlink():
        raise ValueError("Unsafe exact-DP checkpoint root")
    checkpoint_root.mkdir(parents=True, exist_ok=True)
    environment = _environment(scratch_root or checkpoint_root)
    lock = checkpoint_root / ".lock"
    if lock.is_symlink():
        raise ValueError("Unsafe exact-DP lock")
    run_info = {
        "chunks": len(chunks),
        "invocations": 0,
        "reused_chunks": 0,
        "newly_computed_cells": 0,
        "reused_checkpoint_cells": 0,
        "backend_pair_counts": dict(
            sorted(Counter(row["backend"] for row in assignments).items())
        ),
        "execution_backend": BACKEND,
    }
    aggregate = dict.fromkeys(frozen.METRICS, 0)
    aggregate["threads"] = threads
    with lock.open("a+b") as lock_stream:
        fcntl.flock(lock_stream, fcntl.LOCK_EX)
        journal = checkpoint_root / "checkpoint.json"
        if journal.exists() or journal.is_symlink():
            if _read_json(journal) != descriptor:
                raise ValueError("Exact-DP checkpoint inputs or contract changed")
        else:
            if any(
                path.name.startswith(("chunk-", "pair-"))
                for path in checkpoint_root.iterdir()
            ):
                raise ValueError("Uncommitted exact-DP checkpoint directory")
            _atomic_json(journal, descriptor)
        merge_fd, merge_name = tempfile.mkstemp(prefix=".merge-", dir=output.parent)
        merged_path = Path(merge_name)
        try:
            with os.fdopen(merge_fd, "wb") as merged:
                for number, (chunk, engine) in enumerate(
                    zip(chunks, engines, strict=True)
                ):
                    folder = checkpoint_root / f"chunk-{number:06d}"
                    chunk_expected = {_key(row): expected[_key(row)] for row in chunk}
                    chunk_cells = sum(map(sum, chunk_expected.values()))
                    job_bytes = _tsv_bytes(fields, chunk)
                    chunk_identity = {
                        "number": number,
                        "backend": engine,
                        "pair_keys": [list(_key(row)) for row in chunk],
                        "checkpoint_identity": identity,
                    }
                    if folder.exists() or folder.is_symlink():
                        if folder.is_symlink() or not folder.is_dir():
                            raise ValueError("Unsafe exact-DP chunk")
                        manifest = _read_json(folder / "manifest.json")
                        if any(
                            manifest.get(key) != value
                            for key, value in chunk_identity.items()
                        ):
                            raise ValueError("Exact-DP chunk identity changed")
                        hashes = manifest.get("sha256")
                        names = {"jobs.tsv", "alignments.tsv", "metrics.tsv"}
                        if not isinstance(hashes, dict) or set(hashes) != names:
                            raise ValueError("Incomplete exact-DP chunk manifest")
                        for name, digest in hashes.items():
                            path = folder / name
                            if (
                                not path.is_file()
                                or path.is_symlink()
                                or frozen._sha(path) != digest
                            ):
                                raise ValueError("Exact-DP chunk checksum mismatch")
                        if (folder / "jobs.tsv").read_bytes() != job_bytes:
                            raise ValueError("Exact-DP chunk jobs changed")
                        run_info["reused_chunks"] += 1
                        run_info["reused_checkpoint_cells"] += chunk_cells
                    else:
                        staged = Path(
                            tempfile.mkdtemp(prefix=".building-", dir=checkpoint_root)
                        )
                        try:
                            _write_bytes(staged / "jobs.tsv", job_bytes)
                            command = frozen._base_command(
                                binary=(
                                    linear_binary
                                    if engine == LINEAR_BACKEND
                                    else binary
                                ),
                                jobs=staged / "jobs.tsv",
                                targets=targets,
                                start_store=start_store,
                                end_store=end_store,
                                releases=releases,
                            )
                            if engine == LINEAR_BACKEND:
                                active = checkpoint_root / f"pair-{number:06d}"
                                pair_identity = hashlib.sha256(
                                    _json_text(chunk_identity).encode()
                                ).hexdigest()
                                telemetry = _run_linear(
                                    command=command,
                                    active=active,
                                    identity=pair_identity,
                                    output=staged / "alignments.tsv",
                                    summary=staged / "metrics.tsv",
                                    threads=threads,
                                    cells=chunk_cells,
                                    invocation_limit=limit,
                                    memory_limit=max_dp_memory_bytes,
                                    expected_memory_bytes=workspace_sizes[number],
                                    checkpoint_rows=checkpoint_rows,
                                    environment=environment,
                                )
                            else:
                                subprocess.run(
                                    [
                                        *command,
                                        "--output",
                                        str(staged / "alignments.tsv"),
                                        "--summary",
                                        str(staged / "metrics.tsv"),
                                        "--threads",
                                        str(threads),
                                        "--max-cells-per-pair",
                                        str(max_pair_cells),
                                        "--max-total-cells",
                                        str(limit),
                                    ],
                                    check=True,
                                    env=environment,
                                )
                                telemetry = {
                                    "invocations": 1,
                                    "newly_computed_cells": chunk_cells,
                                    "reused_checkpoint_cells": 0,
                                }
                            _validate_chunk(
                                folder=staged,
                                expected_jobs=chunk,
                                expected_cells=chunk_expected,
                                threads=threads,
                            )
                            hashes = {}
                            for name in ("jobs.tsv", "alignments.tsv", "metrics.tsv"):
                                path = staged / name
                                with path.open("rb") as stream:
                                    os.fsync(stream.fileno())
                                hashes[name] = frozen._sha(path)
                            _atomic_json(
                                staged / "manifest.json",
                                {
                                    **chunk_identity,
                                    "sha256": hashes,
                                    "execution": telemetry,
                                },
                            )
                            _fsync_directory(staged)
                            os.rename(staged, folder)
                            _fsync_directory(checkpoint_root)
                            for key in (
                                "invocations",
                                "newly_computed_cells",
                                "reused_checkpoint_cells",
                            ):
                                run_info[key] += telemetry[key]
                        finally:
                            if staged.exists():
                                shutil.rmtree(staged)
                    metrics = _validate_chunk(
                        folder=folder,
                        expected_jobs=chunk,
                        expected_cells=chunk_expected,
                        threads=threads,
                    )
                    with (folder / "alignments.tsv").open("rb") as stream:
                        header = stream.readline()
                        if number == 0:
                            merged.write(header)
                        shutil.copyfileobj(stream, merged)
                    for key in frozen.METRICS:
                        if key != "threads":
                            aggregate[key] += metrics[key]
                merged.flush()
                os.fsync(merged.fileno())
            if pins != {name: frozen._sha(path) for name, path in paths.items()}:
                raise ValueError("Exact-DP sources changed during execution")
            # Reconcile the complete ordered universe before any output commit.
            _, merged_rows = frozen._rows(merged_path)
            if [_key(row) for row in merged_rows] != [_key(row) for row in jobs_rows]:
                raise ValueError("Exact-DP merged universe mismatch")
            summary_fd, summary_name = tempfile.mkstemp(
                prefix=".summary-", dir=summary.parent
            )
            staged_summary = Path(summary_name)
            try:
                with os.fdopen(summary_fd, "wb") as stream:
                    stream.write(
                        _tsv_bytes(
                            ("metric", "value"),
                            [
                                {"metric": key, "value": str(aggregate[key])}
                                for key in frozen.METRICS
                            ],
                        )
                    )
                    stream.flush()
                    os.fsync(stream.fileno())
                os.link(merged_path, output)
                try:
                    os.link(staged_summary, summary)
                except BaseException:
                    output.unlink(missing_ok=True)
                    raise
            finally:
                staged_summary.unlink(missing_ok=True)
            _fsync_directory(output.parent)
            if summary.parent != output.parent:
                _fsync_directory(summary.parent)
        finally:
            merged_path.unlink(missing_ok=True)
    return run_info
