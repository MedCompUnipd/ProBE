"""Actual mixed-engine planning, merge, and resume through native DP binaries.

Run with pytest in the project environment, or use the standalone stdlib gate:
    PYTHONPATH=src python tests/test_canonical_v2_large_dp.py

The standalone gate imports only the DP modules through a package namespace;
it deliberately does not exercise the optional public ontology API imports.
"""

from __future__ import annotations

import csv
import importlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

from test_canonical_v2_linear_dp import _inputs

ROOT = Path(__file__).resolve().parents[1]
if __name__ == "__main__":
    package = types.ModuleType("probe")
    package.__path__ = [str(ROOT / "src" / "probe")]
    sys.modules["probe"] = package

large = importlib.import_module("probe.canonical_v2.large_dp")
frozen = importlib.import_module("probe.canonical_v2.bounded_dp")


def _read(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    with path.open(encoding="utf-8", newline="") as stream:
        reader = csv.DictReader(stream, delimiter="\t")
        return reader.fieldnames, list(reader)


def _write(path: Path, fields: list[str], rows: list[dict[str, str]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(
            stream, fieldnames=fields, delimiter="\t", lineterminator="\n"
        )
        writer.writeheader()
        writer.writerows(rows)


class MixedExactDPTests(unittest.TestCase):
    """Check the complete admitted universe and persistent native checkpoints."""

    @classmethod
    def setUpClass(cls) -> None:
        scratch = os.environ.get("TMPDIR")
        if not scratch or not Path(scratch).is_dir():
            raise RuntimeError("Set TMPDIR to the existing task scratch root")
        cls.temporary = tempfile.TemporaryDirectory(
            prefix="probe-mixed-native-tests-", dir=scratch
        )
        cls.root = Path(cls.temporary.name)
        compiler = shutil.which("g++")
        if compiler is None:
            raise RuntimeError("Native DP routing tests require g++")
        cls.binaries = {}
        for backend, executable in (
            ("bounded", frozen.BINARY_VERSION),
            ("linear", large.LINEAR_VERSION),
        ):
            binary = cls.root / executable
            subprocess.run(
                [
                    compiler,
                    "-O2",
                    "-std=c++17",
                    "-fopenmp",
                    str(ROOT / "cpp" / "src" / f"{executable}.cpp"),
                    "-o",
                    str(binary),
                ],
                check=True,
                capture_output=True,
                text=True,
            )
            cls.binaries[backend] = binary

    @classmethod
    def tearDownClass(cls) -> None:
        cls.temporary.cleanup()

    def setUp(self) -> None:
        self.folder = self.root / self._testMethodName
        self.folder.mkdir()
        command = _inputs(
            self.folder / "inputs",
            [
                ("AAAAA", "AAAAB"),
                ("AAAAA", "W" * 13 + "AAAAC"),
                ("AAAAA", "AAAAD"),
            ],
        )
        options = dict(zip(command[::2], command[1::2], strict=True))
        jobs = Path(options["--alignment-jobs"])
        fields, rows = _read(jobs)
        rows.sort(key=lambda row: (row["release"], row["target_id"], int(row["rid"])))
        _write(jobs, fields, rows)
        self.arguments = {
            "binary": self.binaries["bounded"],
            "linear_binary": self.binaries["linear"],
            "jobs": jobs,
            "targets": Path(options["--targets"]),
            "start_store": Path(options["--start-sequence-store"]),
            "end_store": Path(options["--end-sequence-store"]),
            "releases": {"START": "R1", "END": "R2"},
            "plan": self.folder / "plan.tsv",
            "routing": self.folder / "routing.tsv",
            "max_pair_cells": 50,
            "max_total_cells": 64,
            "max_chunk_cells": 64,
            "threads": 1,
            "checkpoint_rows": 2,
            "max_dp_memory_bytes": 4 * 1024 * 1024,
            "scratch_root": self.folder,
        }
        self.output = self.folder / "output.tsv"
        self.summary = self.folder / "metrics.tsv"
        self.checkpoints = self.folder / "checkpoints"

    def plan(self) -> dict:
        self.report = large.plan_large_dp(**self.arguments)
        return self.report

    def execute(self) -> dict:
        return large.execute_large_dp(
            **self.arguments,
            output=self.output,
            summary=self.summary,
            checkpoint_root=self.checkpoints,
            expected_input_sha256=self.report["input_sha256"],
        )

    def clear_output(self) -> None:
        self.output.unlink(missing_ok=True)
        self.summary.unlink(missing_ok=True)

    def reference(self) -> tuple[bytes, bytes]:
        command = frozen._base_command(
            **{
                name: self.arguments[name]
                for name in (
                    "binary",
                    "jobs",
                    "targets",
                    "start_store",
                    "end_store",
                    "releases",
                )
            }
        )
        output, summary = self.folder / "reference.tsv", self.folder / "ref.tsv"
        subprocess.run(
            [
                *command,
                "--output",
                str(output),
                "--summary",
                str(summary),
                "--threads",
                str(self.arguments["threads"]),
                "--max-cells-per-pair",
                "10000",
                "--max-total-cells",
                "10000",
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        return output.read_bytes(), summary.read_bytes()

    def test_mixed_output_matches_all_49_frozen_columns(self) -> None:
        self.arguments["threads"] = 3
        report = self.plan()
        self.assertEqual(report["backend"], frozen.BACKEND)
        self.assertEqual(report["execution_backend"], large.BACKEND)
        self.assertEqual(
            report["backend_pair_counts"], {frozen.BACKEND: 2, large.LINEAR_BACKEND: 1}
        )
        info = self.execute()
        self.assertEqual(info["chunks"], 2)
        self.assertGreater(info["invocations"], 2)
        self.assertEqual(info["newly_computed_cells"], report["directional_cells"])
        reference, metrics = self.reference()
        self.assertEqual(self.output.read_bytes(), reference)
        self.assertEqual(self.summary.read_bytes(), metrics)
        self.assertEqual(len(_read(self.output)[0]), 49)

    def test_tiny_quantum_crosses_rows_and_directions_exactly(self) -> None:
        self.arguments["max_total_cells"] = 7
        self.arguments["max_chunk_cells"] = 7
        report = self.plan()
        self.assertEqual(report["backend_pair_counts"], {large.LINEAR_BACKEND: 3})
        result = self.execute()
        self.assertGreater(result["invocations"], 10)
        reference, metrics = self.reference()
        self.assertEqual(self.output.read_bytes(), reference)
        self.assertEqual(self.summary.read_bytes(), metrics)
        for path in self.checkpoints.glob("pair-*/progress.json"):
            progress = json.loads(path.read_text())
            self.assertLessEqual(progress["invocation_computed_cells"], 7)
            self.assertEqual(progress["status"], "COMPLETE")

    def test_interrupt_reuses_completed_chunk_and_internal_pair_checkpoint(self):
        self.plan()
        real_run = subprocess.run
        linear_calls = 0

        def interrupt(command, **kwargs):
            nonlocal linear_calls
            if "--checkpoint-dir" in command:
                linear_calls += 1
                if linear_calls == 2:
                    raise RuntimeError("Synthetic interruption inside large pair")
            return real_run(command, **kwargs)

        with (
            mock.patch.object(large.subprocess, "run", side_effect=interrupt),
            self.assertRaisesRegex(RuntimeError, "inside large pair"),
        ):
            self.execute()
        self.assertFalse(self.output.exists())
        self.assertTrue((self.checkpoints / "chunk-000000/manifest.json").is_file())
        progress = json.loads(
            (self.checkpoints / "pair-000001/progress.json").read_text()
        )
        self.assertEqual(progress["completed_cells"], 64)
        result = self.execute()
        self.assertEqual(result["reused_chunks"], 1)
        self.assertGreater(result["reused_checkpoint_cells"], 64)
        self.assertEqual(self.output.read_bytes(), self.reference()[0])

    def test_completed_chunks_are_reused_without_native_execution(self) -> None:
        self.plan()
        initial = self.execute()
        reference = self.output.read_bytes()
        self.clear_output()
        with mock.patch.object(large.subprocess, "run") as run:
            replay = self.execute()
            run.assert_not_called()
        self.assertEqual(replay["reused_chunks"], initial["chunks"])
        self.assertEqual(replay["invocations"], 0)
        self.assertEqual(replay["newly_computed_cells"], 0)
        self.assertEqual(self.output.read_bytes(), reference)

    def test_completed_native_state_recreates_output_without_dp_recalculation(self):
        self.plan()
        real_write = large._atomic_json

        def interrupt(path, value):
            if path.name == "manifest.json" and value.get("backend") == (
                large.LINEAR_BACKEND
            ):
                raise RuntimeError("Synthetic interruption after native completion")
            return real_write(path, value)

        with (
            mock.patch.object(large, "_atomic_json", side_effect=interrupt),
            self.assertRaisesRegex(RuntimeError, "after native completion"),
        ):
            self.execute()
        self.assertFalse(self.output.exists())
        result = self.execute()
        self.assertEqual(result["newly_computed_cells"], 0)
        self.assertEqual(
            result["reused_checkpoint_cells"], self.report["directional_cells"]
        )
        self.assertEqual(result["invocations"], 1)
        self.assertEqual(self.output.read_bytes(), self.reference()[0])

    def test_native_no_progress_cannot_loop_or_publish(self) -> None:
        self.plan()
        real_run = subprocess.run

        def corrupt_progress(command, **kwargs):
            result = real_run(command, **kwargs)
            if "--checkpoint-dir" in command and result.returncode == 75:
                active = Path(command[command.index("--checkpoint-dir") + 1])
                progress_path = active / "progress.json"
                progress = json.loads(progress_path.read_text())
                progress["invocation_computed_cells"] = 0
                progress["completed_cells"] = progress["resumed_cells"]
                progress_path.write_text(json.dumps(progress))
            return result

        with (
            mock.patch.object(large.subprocess, "run", side_effect=corrupt_progress),
            self.assertRaisesRegex(ValueError, "no valid forward progress"),
        ):
            self.execute()
        self.assertFalse(self.output.exists())
        self.assertFalse(self.summary.exists())

    def test_corrupt_chunk_is_rejected_before_merge_publication(self) -> None:
        self.plan()
        self.execute()
        self.clear_output()
        chunk = self.checkpoints / "chunk-000000/alignments.tsv"
        with chunk.open("ab") as stream:
            stream.write(b"corrupted\n")
        with self.assertRaisesRegex(ValueError, "checksum mismatch"):
            self.execute()
        self.assertFalse(self.output.exists())
        self.assertFalse(self.summary.exists())

    def test_routing_tamper_and_changed_inputs_are_rejected(self) -> None:
        self.plan()
        fields, rows = _read(self.arguments["routing"])
        rows[-1]["backend"] = frozen.BACKEND
        _write(self.arguments["routing"], fields, rows)
        with self.assertRaisesRegex(ValueError, "routing differs"):
            self.execute()
        self.arguments["routing"].unlink()
        self.arguments["plan"].unlink()
        self.plan()
        with self.arguments["targets"].open("ab") as stream:
            stream.write(b"\n")
        with self.assertRaisesRegex(ValueError, "inputs changed since planning"):
            self.execute()
        self.assertFalse(self.checkpoints.exists())

    def test_resource_failure_still_contains_every_planned_pair(self) -> None:
        self.arguments["max_dp_memory_bytes"] = 1
        with self.assertRaisesRegex(frozen.BudgetExceeded, "INCOMPLETE_MEMORY"):
            self.plan()
        self.assertEqual(len(frozen.read_plan(self.arguments["plan"])), 3)
        self.assertEqual(len(_read(self.arguments["routing"])[1]), 3)
        self.assertFalse(self.output.exists())
        self.assertFalse(self.checkpoints.exists())

    def test_interrupted_merge_reuses_all_committed_chunks(self) -> None:
        self.plan()
        real_link = os.link

        def interrupt(source, destination, **kwargs):
            if destination == self.output:
                raise RuntimeError("Synthetic interruption before merged publication")
            return real_link(source, destination, **kwargs)

        with (
            mock.patch.object(large.os, "link", side_effect=interrupt),
            self.assertRaisesRegex(RuntimeError, "before merged publication"),
        ):
            self.execute()
        self.assertFalse(self.output.exists())
        self.assertFalse(self.summary.exists())
        with mock.patch.object(large.subprocess, "run") as run:
            replay = self.execute()
            run.assert_not_called()
        self.assertEqual(replay["reused_chunks"], 2)
        self.assertEqual(self.output.read_bytes(), self.reference()[0])


if __name__ == "__main__":
    unittest.main(verbosity=2)
