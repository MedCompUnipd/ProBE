"""Account for a certified DP region without changing alignment semantics.

The native planner supplies a feasible score lower bound and a region that
contains every optimal path, including ties. Resource limits apply to that
region. A resource failure never becomes an excluded biological candidate.
"""

from __future__ import annotations

import csv
import fcntl
import hashlib
import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

BACKEND = "certified-geometric-bounds-v2"
CHUNK_CONTRACT = "canonical-v2-certified-chunk-checkpoints-v1"
BINARY_VERSION = "probe_directional_glocal_bounded_dp_v2"
PLAN = "2C_06C0_certified_dp_plan.tsv"
DIRECTIONS = ("TARGET_GLOBAL", "CANDIDATE_GLOBAL")
PLAN_FIELDS = (
    "release",
    "target_id",
    "rid",
    "direction",
    "target_length",
    "candidate_length",
    "lower_bound_score",
    "diagonal_min",
    "diagonal_max",
    "certified_cells",
    "full_matrix_cells",
)
METRICS = (
    "job_count",
    "completed_count",
    "threads",
    "directional_alignment_count",
    "total_dp_cells_evaluated",
    "status_OK",
)
PairKey = tuple[str, str, str]


class BudgetExceeded(ValueError):
    """A complete cost inventory exists, but its execution is not authorized."""

    def __init__(self, message: str, report: dict) -> None:
        super().__init__(message)
        self.report = report


def _sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _number(raw: str, *, positive: bool = False) -> int:
    try:
        value = int(raw)
    except (TypeError, ValueError) as error:
        raise ValueError("Invalid certified DP integer") from error
    if str(value) != raw or (positive and value < 1):
        raise ValueError("Noncanonical certified DP integer")
    return value


def _rows(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    if not path.is_file() or path.is_symlink():
        raise ValueError(f"Missing or symlinked certified DP input: {path}")
    with path.open(encoding="utf-8", newline="") as stream:
        reader = csv.DictReader(stream, delimiter="\t")
        fields = reader.fieldnames
        if fields is None or len(fields) != len(set(fields)):
            raise ValueError("Invalid certified DP TSV header")
        rows = list(reader)
    if any(None in row or None in row.values() for row in rows):
        raise ValueError("Malformed certified DP TSV row")
    return fields, rows


def _diagonal_prefix(n: int, m: int, diagonal: int) -> int:
    """Count grid points (i,j) satisfying j-i <= diagonal, in O(1)."""
    first = max(0, -diagonal)
    last = min(n, m - diagonal - 1)
    count = max(0, last - first + 1)
    ramp = count * (first + last + 2 * (diagonal + 1)) // 2
    plateau = max(0, n - max(0, m - diagonal) + 1) * (m + 1)
    return ramp + plateau


def region_cells(n: int, m: int, low: int, high: int) -> int:
    return _diagonal_prefix(n, m, high) - _diagonal_prefix(n, m, low - 1)


def _plan_rows(path: Path) -> list[dict[str, str]]:
    fields, rows = _rows(path)
    if tuple(fields) != PLAN_FIELDS:
        raise ValueError("Unexpected certified DP plan schema")
    seen = set()
    for row in rows:
        key = row["release"], row["target_id"], row["rid"], row["direction"]
        if not all(key) or key in seen or key[3] not in DIRECTIONS:
            raise ValueError("Duplicate or invalid certified DP direction")
        seen.add(key)
        _number(key[2], positive=True)
        target = _number(row["target_length"], positive=True)
        candidate = _number(row["candidate_length"], positive=True)
        n, m = target, candidate
        global_length = target if key[3] == "TARGET_GLOBAL" else candidate
        lower = _number(row["lower_bound_score"])
        low = _number(row["diagonal_min"])
        high = _number(row["diagonal_max"])
        cells = _number(row["certified_cells"], positive=True)
        full = _number(row["full_matrix_cells"], positive=True)
        allowance = (2 * global_length - lower) // 3
        delta = candidate - target
        bounds = (
            (-allowance, delta + allowance)
            if key[3] == "TARGET_GLOBAL"
            else (delta - allowance, allowance)
        )
        if (
            full != (n + 1) * (m + 1)
            or not -n <= low <= high <= m
            or cells != region_cells(n, m, low, high)
            or cells > full
            or lower > 2 * global_length
            or (low, high) != bounds
        ):
            raise ValueError("Certified DP plan geometry mismatch")
    return rows


def read_plan(path: Path) -> dict[PairKey, tuple[int, int]]:
    """Return exact expected cell counts, preserving the two orientations."""
    result: dict[PairKey, dict[str, int]] = {}
    lengths = {}
    for row in _plan_rows(path):
        key = row["release"], row["target_id"], row["rid"]
        pair_lengths = row["target_length"], row["candidate_length"]
        if key in lengths and lengths[key] != pair_lengths:
            raise ValueError("Certified DP directions have different lengths")
        lengths[key] = pair_lengths
        result.setdefault(key, {})[row["direction"]] = int(row["certified_cells"])
    if any(set(value) != set(DIRECTIONS) for value in result.values()):
        raise ValueError("Certified DP plan lacks an alignment direction")
    return {
        key: (value[DIRECTIONS[0]], value[DIRECTIONS[1]])
        for key, value in result.items()
    }


def _job_plan(jobs: Path, plan: Path) -> tuple[list, list, dict]:
    fields, rows = _rows(jobs)
    required = {
        "release",
        "target_id",
        "rid",
        "target_length",
        "candidate_length",
        "exact_sequence",
    }
    if not required.issubset(fields):
        raise ValueError("Incomplete certified NONEXACT jobs")
    expected = read_plan(plan)
    planned_lengths = {
        (row["release"], row["target_id"], row["rid"]): (
            row["target_length"],
            row["candidate_length"],
        )
        for row in _plan_rows(plan)
    }
    found = set()
    for row in rows:
        key = row["release"], row["target_id"], row["rid"]
        if (
            key in found
            or key not in expected
            or row["exact_sequence"] != "0"
            or planned_lengths[key] != (row["target_length"], row["candidate_length"])
        ):
            raise ValueError("Certified DP plan differs from NONEXACT jobs")
        found.add(key)
    if found != set(expected):
        raise ValueError("Certified DP plan has missing or extra pairs")
    return fields, rows, expected


def _base_command(
    *,
    binary: Path,
    jobs: Path,
    targets: Path,
    start_store: Path,
    end_store: Path,
    releases: dict[str, str],
) -> list[str]:
    if set(releases) != {"START", "END"} or len(set(releases.values())) != 2:
        raise ValueError("Invalid certified DP releases")
    return [
        str(binary),
        "--targets",
        str(targets),
        "--alignment-jobs",
        str(jobs),
        "--start-sequence-store",
        str(start_store),
        "--end-sequence-store",
        str(end_store),
        "--start-release-id",
        releases["START"],
        "--end-release-id",
        releases["END"],
    ]


def plan_bounded_dp(
    *,
    binary: Path,
    jobs: Path,
    targets: Path,
    start_store: Path,
    end_store: Path,
    releases: dict[str, str],
    plan: Path,
    max_pair_cells: int,
    max_total_cells: int,
    max_chunk_cells: int | None = None,
) -> dict:
    """Write the complete native plan before enforcing resource budgets."""
    if plan.exists() or plan.is_symlink():
        raise FileExistsError("Certified DP plan destination occupied")
    version = subprocess.run(
        [str(binary), "--version"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    if version != BINARY_VERSION:
        raise ValueError("Unexpected certified DP implementation")
    paths = (binary, jobs, targets, start_store, end_store)
    pins = {str(path): _sha(path) for path in paths}
    command = _base_command(
        binary=binary,
        jobs=jobs,
        targets=targets,
        start_store=start_store,
        end_store=end_store,
        releases=releases,
    )
    subprocess.run(
        [*command, "--plan-only", "--plan-output", str(plan)],
        check=True,
    )
    _, rows, expected = _job_plan(jobs, plan)
    if pins != {str(path): _sha(path) for path in paths}:
        raise ValueError("Certified DP inputs changed during planning")
    costs = [sum(value) for value in expected.values()]
    total = sum(costs)
    largest = max(costs, default=0)
    full = sum(
        2 * (int(row["target_length"]) + 1) * (int(row["candidate_length"]) + 1)
        for row in rows
    )
    report = {
        "backend": BACKEND,
        "selected_pairs": len(expected),
        "directional_cells": total,
        "full_matrix_cells": full,
        "max_pair_cells_observed": largest,
        "plan_sha256": _sha(plan),
        "input_sha256": pins,
        "cell_definition": "visited cells in certified optimal-path region",
        "limits": {
            "max_pair_cells": max_pair_cells,
            "max_total_cells": max_total_cells,
            "max_chunk_cells": max_chunk_cells,
        },
    }
    for value in (max_pair_cells, max_total_cells):
        if type(value) is not int or value < 1:
            raise ValueError("Invalid certified DP cell budget")
    if max_chunk_cells is not None and (
        type(max_chunk_cells) is not int or max_chunk_cells < 1
    ):
        raise ValueError("Invalid certified DP chunk budget")
    if largest > max_pair_cells:
        raise BudgetExceeded(
            "INCOMPLETE_BUDGET: certified pair region exceeds budget",
            report,
        )
    invocation_limit = min(
        max_total_cells,
        max_chunk_cells if max_chunk_cells is not None else max_total_cells,
    )
    if largest > invocation_limit:
        raise BudgetExceeded(
            "INCOMPLETE_BUDGET: certified pair exceeds invocation budget",
            report,
        )
    report["execution_contract"] = CHUNK_CONTRACT
    report["invocation_cell_limit"] = invocation_limit
    report["planned_invocations_min"] = (
        total + invocation_limit - 1
    ) // invocation_limit
    return report


def _metrics(path: Path) -> dict[str, int]:
    fields, rows = _rows(path)
    if fields != ["metric", "value"]:
        raise ValueError("Invalid certified DP metrics header")
    result = {}
    for row in rows:
        name = row["metric"]
        if name not in METRICS or name in result:
            raise ValueError("Invalid certified DP metric")
        value = _number(row["value"])
        if value < 0:
            raise ValueError("Negative certified DP metric")
        result[name] = value
    if set(result) != set(METRICS):
        raise ValueError("Incomplete certified DP metrics")
    return result


def _chunk_metrics(
    *,
    result_path: Path,
    metric_path: Path,
    expected: dict[PairKey, tuple[int, int]],
    threads: int,
) -> dict[str, int]:
    """Validate an independently published chunk before trusting it."""
    metrics = _metrics(metric_path)
    cells = sum(map(sum, expected.values()))
    desired = {
        "job_count": len(expected),
        "completed_count": len(expected),
        "threads": threads,
        "directional_alignment_count": 2 * len(expected),
        "total_dp_cells_evaluated": cells,
        "status_OK": 2 * len(expected),
    }
    if metrics != desired:
        raise ValueError("Certified chunk metrics differ from its plan")
    fields, results = _rows(result_path)
    if not fields or len(results) != len(expected):
        raise ValueError("Incomplete certified chunk output")
    observed: dict[PairKey, tuple[int, int]] = {}
    for row in results:
        key = row["release"], row["target_id"], row["rid"]
        if key in observed:
            raise ValueError("Duplicate certified chunk result")
        observed[key] = (
            _number(row["target_global_cells_evaluated"], positive=True),
            _number(row["candidate_global_cells_evaluated"], positive=True),
        )
    if observed != expected:
        raise ValueError("Certified chunk results differ from its plan")
    return metrics


def execute_bounded_dp(
    *,
    binary: Path,
    jobs: Path,
    targets: Path,
    start_store: Path,
    end_store: Path,
    releases: dict[str, str],
    plan: Path,
    output: Path,
    summary: Path,
    threads: int,
    max_pair_cells: int,
    max_total_cells: int,
    max_chunk_cells: int | None = None,
    expected_input_sha256: dict[str, str] | None = None,
    checkpoint_root: Path | None = None,
) -> int:
    """Complete all pairs using independently verified, resumable invocations.

    max_total_cells limits a *single invocation*, not the entire dataset.
    It never excludes a candidate solely because earlier pairs consumed cells.
    No partially completed alignment bundle is published after an error.
    """
    fields, rows, expected = _job_plan(jobs, plan)
    for value in (threads, max_pair_cells, max_total_cells):
        if type(value) is not int or value < 1:
            raise ValueError("Invalid certified execution resources")
    if max_chunk_cells is not None and (
        type(max_chunk_cells) is not int or max_chunk_cells < 1
    ):
        raise ValueError("Invalid chunk budget")
    limit = min(
        max_total_cells,
        max_chunk_cells if max_chunk_cells is not None else max_total_cells,
    )
    if any(sum(value) > min(max_pair_cells, limit) for value in expected.values()):
        raise BudgetExceeded(
            "INCOMPLETE_BUDGET: pair exceeds bounded invocation",
            {"max_pair_cells": max_pair_cells, "invocation_limit": limit},
        )
    if not rows:
        raise ValueError("Use the native empty-DP writer for an empty selection")
    if output.exists() or summary.exists():
        raise FileExistsError("Certified DP destination already occupied")
    sources = (binary, jobs, targets, start_store, end_store, plan)
    pins = {str(path): _sha(path) for path in sources}
    if expected_input_sha256 is not None:
        actual = {str(path): pins[str(path)] for path in sources if path != plan}
        if actual != expected_input_sha256:
            raise ValueError("Certified inputs changed since planning")
    chunks: list[list[dict[str, str]]] = []
    pending: list[dict[str, str]] = []
    pending_cells = 0
    for row in rows:
        key = row["release"], row["target_id"], row["rid"]
        cells = sum(expected[key])
        if pending and pending_cells + cells > limit:
            chunks.append(pending)
            pending, pending_cells = [], 0
        pending.append(row)
        pending_cells += cells
    if pending:
        chunks.append(pending)
    # Root lives beside the *published bundle*, never inside its manifest.
    # A random temporary product path must not determine the checkpoint ID.
    if checkpoint_root is None:
        checkpoint_root = output.parent.parent / "canonical-dp-checkpoints"
    if checkpoint_root.is_symlink():
        raise ValueError("Unsafe checkpoint root symlink")
    checkpoint_root.mkdir(parents=True, exist_ok=True)
    descriptor = {
        "contract": CHUNK_CONTRACT,
        "source_sha256": {
            "binary": pins[str(binary)],
            "jobs": pins[str(jobs)],
            "targets": pins[str(targets)],
            "start_store": pins[str(start_store)],
            "end_store": pins[str(end_store)],
            "plan": pins[str(plan)],
        },
        "releases": releases,
        "threads": threads,
        "max_pair_cells": max_pair_cells,
        "invocation_cell_limit": limit,
        "pair_count": len(rows),
        "chunk_sizes": [len(chunk) for chunk in chunks],
    }
    descriptor_text = json.dumps(descriptor, sort_keys=True) + "\n"
    journal_meta = checkpoint_root / "checkpoint.json"
    lock = checkpoint_root / ".lock"
    aggregate = dict.fromkeys(METRICS, 0)
    aggregate["threads"] = threads
    header = None
    with lock.open("a+b") as lock_stream:
        fcntl.flock(lock_stream, fcntl.LOCK_EX)
        if journal_meta.exists():
            if journal_meta.is_symlink() or journal_meta.read_text() != descriptor_text:
                raise ValueError("Checkpoint contract or scientific inputs changed")
        else:
            # A pre-existing uncommitted checkpoint must never be trusted.
            if any(
                path.name.startswith("chunk-") for path in checkpoint_root.iterdir()
            ):
                raise ValueError("Uncommitted checkpoint directory found")
            with journal_meta.open("x", encoding="utf-8") as stream:
                stream.write(descriptor_text)
        with output.open("xb") as merged:
            try:
                for number, chunk in enumerate(chunks):
                    chunk_expected = {
                        (row["release"], row["target_id"], row["rid"]): expected[
                            (row["release"], row["target_id"], row["rid"])
                        ]
                        for row in chunk
                    }
                    folder = checkpoint_root / f"chunk-{number:06d}"
                    if folder.exists():
                        if folder.is_symlink() or not folder.is_dir():
                            raise ValueError("Unsafe DP checkpoint chunk")
                        manifest = json.loads((folder / "manifest.json").read_text())
                        expected_chunk = {
                            "number": number,
                            "pair_keys": [
                                [row["release"], row["target_id"], row["rid"]]
                                for row in chunk
                            ],
                        }
                        if any(manifest.get(k) != v for k, v in expected_chunk.items()):
                            raise ValueError("Unexpected checkpoint chunk identity")
                        for name, digest in manifest["sha256"].items():
                            if name not in {
                                "jobs.tsv",
                                "alignments.tsv",
                                "metrics.tsv",
                            }:
                                raise ValueError("Unexpected checkpoint payload")
                            if _sha(folder / name) != digest:
                                raise ValueError("Checkpoint chunk checksum mismatch")
                        if set(manifest["sha256"]) != {
                            "jobs.tsv",
                            "alignments.tsv",
                            "metrics.tsv",
                        }:
                            raise ValueError("Incomplete checkpoint files")
                    else:
                        staged = Path(
                            tempfile.mkdtemp(prefix=".active-", dir=checkpoint_root)
                        )
                        try:
                            job_path = staged / "jobs.tsv"
                            result_path = staged / "alignments.tsv"
                            metric_path = staged / "metrics.tsv"
                            with job_path.open(
                                "x", encoding="utf-8", newline=""
                            ) as stream:
                                writer = csv.DictWriter(
                                    stream,
                                    fieldnames=fields,
                                    delimiter="\t",
                                    lineterminator="\n",
                                )
                                writer.writeheader()
                                writer.writerows(chunk)
                            cells = sum(map(sum, chunk_expected.values()))
                            command = _base_command(
                                binary=binary,
                                jobs=job_path,
                                targets=targets,
                                start_store=start_store,
                                end_store=end_store,
                                releases=releases,
                            )
                            subprocess.run(
                                [
                                    *command,
                                    "--output",
                                    str(result_path),
                                    "--summary",
                                    str(metric_path),
                                    "--threads",
                                    str(threads),
                                    "--max-cells-per-pair",
                                    str(max_pair_cells),
                                    "--max-total-cells",
                                    str(cells),
                                ],
                                check=True,
                            )
                            _chunk_metrics(
                                result_path=result_path,
                                metric_path=metric_path,
                                expected=chunk_expected,
                                threads=threads,
                            )
                            manifest = {
                                "number": number,
                                "pair_keys": [
                                    [row["release"], row["target_id"], row["rid"]]
                                    for row in chunk
                                ],
                                "sha256": {
                                    name: _sha(staged / name)
                                    for name in (
                                        "jobs.tsv",
                                        "alignments.tsv",
                                        "metrics.tsv",
                                    )
                                },
                            }
                            (staged / "manifest.json").write_text(
                                json.dumps(manifest, sort_keys=True) + "\n",
                                encoding="utf-8",
                            )
                            os.rename(staged, folder)
                        finally:
                            if staged.exists():
                                shutil.rmtree(staged)
                    metrics = _chunk_metrics(
                        result_path=folder / "alignments.tsv",
                        metric_path=folder / "metrics.tsv",
                        expected=chunk_expected,
                        threads=threads,
                    )
                    with (folder / "alignments.tsv").open("rb") as stream:
                        current_header = stream.readline()
                        if header is None:
                            header = current_header
                            merged.write(header)
                        elif current_header != header:
                            raise ValueError("Inconsistent certified alignment headers")
                        for line in stream:
                            merged.write(line)
                    for metric in METRICS:
                        if metric != "threads":
                            aggregate[metric] += metrics[metric]
            except BaseException:
                # Journal stays verified and resumable; no partial output is trusted.
                merged.close()
                output.unlink(missing_ok=True)
                raise
    if pins != {str(path): _sha(path) for path in sources}:
        output.unlink(missing_ok=True)
        raise ValueError("Certified source changed during execution")
    with summary.open("x", encoding="utf-8", newline="") as stream:
        writer = csv.writer(stream, delimiter="\t", lineterminator="\n")
        writer.writerow(("metric", "value"))
        writer.writerows((name, aggregate[name]) for name in METRICS)
    return len(chunks)
