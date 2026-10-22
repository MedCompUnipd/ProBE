"""Differential and crash tests of the exact, resumable rolling DP engine.

This suite also runs with the Python standard library alone:
    python tests/test_canonical_v2_linear_dp.py

It compares the actual frozen C++ executables; no Python reimplementation
acts as the numerical oracle. TMPDIR must identify the task scratch root.
"""

from __future__ import annotations

import csv
import ctypes
import hashlib
import itertools
import json
import os
import random
import shutil
import signal
import string
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
IDENTITY = hashlib.sha256(b"probe-linear-dp-native-test-v1").hexdigest()
BACKENDS = {
    "bounded": "probe_directional_glocal_bounded_dp_v2",
    "full": "probe_directional_glocal_full_dp_v1",
    "linear": "probe_directional_glocal_linear_dp_v1",
}
CELL_FIELDS = {
    "target_global_cells_evaluated",
    "candidate_global_cells_evaluated",
}


def _write(path: Path, fields: tuple[str, ...], rows: list[tuple]) -> Path:
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.writer(stream, delimiter="\t", lineterminator="\n")
        writer.writerow(fields)
        writer.writerows(rows)
    return path


def _read(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as stream:
        return list(csv.DictReader(stream, delimiter="\t"))


def _inputs(root: Path, pairs: list[tuple[str, str]]) -> list[str]:
    root.mkdir(parents=True, exist_ok=True)
    target_rows, start_rows, end_rows, jobs = [], [], [], []
    for number, (target, candidate) in enumerate(pairs, 1):
        identifier = f"T{number:07d}"
        release = "R1" if number % 2 else "R2"
        rid = str((number + 1) // 2)
        target_rows.append((identifier, f"TACC{number}", target))
        store = start_rows if release == "R1" else end_rows
        store.append((release, rid, f"CACC{number}", candidate))
        jobs.append(
            (
                release,
                identifier,
                rid,
                "PRIMARY",
                "DOCUMENTARY",
                "0",
                str(len(target)),
                str(len(candidate)),
            )
        )
    targets = _write(
        root / "targets.tsv",
        ("target_id", "optional_accession", "normalized_sequence"),
        target_rows,
    )
    fields = ("release", "rid", "primary_accession", "sequence")
    start = _write(root / "start.tsv", fields, start_rows)
    end = _write(root / "end.tsv", fields, end_rows)
    job_file = _write(
        root / "jobs.tsv",
        (
            "release",
            "target_id",
            "rid",
            "job_scope",
            "discovery_channel",
            "exact_sequence",
            "target_length",
            "candidate_length",
        ),
        jobs,
    )
    return [
        "--targets",
        str(targets),
        "--alignment-jobs",
        str(job_file),
        "--start-sequence-store",
        str(start),
        "--end-sequence-store",
        str(end),
        "--start-release-id",
        "R1",
        "--end-release-id",
        "R2",
    ]


class NativeLinearDPTests(unittest.TestCase):
    """Exercise scientific equivalence and persistence through native I/O."""

    @classmethod
    def setUpClass(cls) -> None:
        scratch = os.environ.get("TMPDIR")
        if not scratch or not Path(scratch).is_dir():
            raise RuntimeError("Set TMPDIR to the existing task scratch directory")
        compiler = shutil.which("g++")
        if compiler is None:
            raise RuntimeError("Native DP equivalence tests require g++")
        cls.temporary = tempfile.TemporaryDirectory(
            prefix="probe-linear-native-tests-", dir=scratch
        )
        cls.root = Path(cls.temporary.name)
        cls.binaries = {}
        cls.measurements: dict[str, object] = {}
        source_hashes = {}
        for name, executable in BACKENDS.items():
            binary = cls.root / executable
            source = ROOT / "cpp" / "src" / f"{executable}.cpp"
            source_hashes[name] = hashlib.sha256(source.read_bytes()).hexdigest()
            subprocess.run(
                [
                    compiler,
                    "-O2",
                    "-std=c++17",
                    "-fopenmp",
                    str(source),
                    "-o",
                    str(binary),
                ],
                check=True,
                capture_output=True,
                text=True,
            )
            if hashlib.sha256(source.read_bytes()).hexdigest() != source_hashes[name]:
                raise RuntimeError("DP source changed during native test compilation")
            cls.binaries[name] = binary
        cls.measurements["source_sha256"] = source_hashes

    @classmethod
    def tearDownClass(cls) -> None:
        report = os.environ.get("PROBE_LINEAR_DP_REPORT")
        if report:
            Path(report).write_text(
                json.dumps(cls.measurements, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
        cls.temporary.cleanup()

    def setUp(self) -> None:
        self.folder = self.root / self._testMethodName
        self.folder.mkdir()

    def command(
        self,
        backend: str,
        inputs: list[str],
        label: str,
        extra: list[str] | None = None,
    ) -> tuple[list[str], Path, Path]:
        output = self.folder / f"{label}.tsv"
        metrics = self.folder / f"{label}.metrics.tsv"
        return (
            [
                str(self.binaries[backend]),
                *inputs,
                "--output",
                str(output),
                "--summary",
                str(metrics),
                *(extra or []),
            ],
            output,
            metrics,
        )

    def run_native(
        self,
        backend: str,
        inputs: list[str],
        label: str,
        extra: list[str] | None = None,
    ) -> tuple[Path, Path]:
        command, output, metrics = self.command(backend, inputs, label, extra)
        result = subprocess.run(command, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        return output, metrics

    def checkpoint_flags(self, path: Path) -> list[str]:
        return [
            "--checkpoint-dir",
            str(path),
            "--checkpoint-identity",
            IDENTITY,
        ]

    def assert_equivalent(self, pairs: list[tuple[str, str]], label: str) -> Path:
        inputs = _inputs(self.folder / label, pairs)
        bounded, bounded_metrics = self.run_native(
            "bounded", inputs, f"{label}-bounded"
        )
        linear, linear_metrics = self.run_native("linear", inputs, f"{label}-linear")
        full, _ = self.run_native("full", inputs, f"{label}-full")
        self.assertEqual(linear.read_bytes(), bounded.read_bytes())
        self.assertEqual(linear_metrics.read_bytes(), bounded_metrics.read_bytes())
        observed, reference = _read(linear), _read(full)
        self.assertEqual(len(observed), len(pairs))
        self.assertEqual(len(reference), len(pairs))
        for number, (row, full_row) in enumerate(zip(observed, reference, strict=True)):
            self.assertEqual(len(row), 49)
            self.assertEqual(
                {key: value for key, value in row.items() if key not in CELL_FIELDS},
                {
                    key: value
                    for key, value in full_row.items()
                    if key not in CELL_FIELDS
                },
                f"Differential mismatch at pair {number}: {pairs[number]}",
            )
        self.measurements[f"{label}_pairs"] = len(pairs)
        return linear

    def test_exhaustive_short_ties(self) -> None:
        sequences = [
            "".join(letters)
            for length in range(1, 5)
            for letters in itertools.product("AX", repeat=length)
        ]
        self.assert_equivalent(list(itertools.product(sequences, repeat=2)), "all")

    def test_seeded_iupac_gaps_and_asymmetry(self) -> None:
        rng = random.Random(20261021)
        pairs = [
            ("ACDE", "XXXXACDEZZZZ"),
            ("XXXXACDEZZZZ", "ACDE"),
            ("AAAAAAA", "AAABAAAA"),
            ("AAABAAAA", "AAAAAAA"),
            ("ABCDEF", "ABQQCDEF"),
            ("ABQQCDEF", "ABCDEF"),
            ("BJOXUZ", "BJOXUZ"),
            ("B", "D"),
            ("J", "L"),
            ("Z", "E"),
            ("X", "A"),
        ]
        for index in range(600):
            alphabet = string.ascii_uppercase if index % 3 else "ABX"
            target = "".join(rng.choices(alphabet, k=rng.randint(1, 90)))
            if index % 2:
                candidate = "".join(rng.choices(alphabet, k=rng.randint(1, 130)))
            else:
                candidate = list(target)
                for _ in range(rng.randint(1, 12)):
                    position = rng.randrange(len(candidate)) if candidate else 0
                    operation = rng.randrange(3)
                    if operation == 0 and candidate:
                        candidate.pop(position)
                    elif operation == 1 and candidate:
                        candidate[position] = rng.choice(alphabet)
                    else:
                        candidate.insert(position, rng.choice(alphabet))
                candidate = "".join(candidate) or "X"
            pairs.append((target, candidate))
        self.assert_equivalent(pairs, "random")

    def test_literal_bijection_and_thread_determinism(self) -> None:
        pairs = [
            ("ABCABXXJZ", "ZZABCABAXJZ"),
            ("AAAAAXAAAAA", "AAAAXAAAA"),
            ("BCD", "BBBBBCDDDDD"),
            ("ZZXXXX", "BJJJJJO"),
        ]
        table = str.maketrans(string.ascii_uppercase, string.ascii_uppercase[::-1])
        original = _inputs(self.folder / "original", pairs)
        transformed = _inputs(
            self.folder / "transformed",
            [(a.translate(table), b.translate(table)) for a, b in pairs],
        )
        one, _ = self.run_native("linear", original, "one")
        mapped, _ = self.run_native("linear", transformed, "mapped")
        many, _ = self.run_native("linear", original, "many", ["--threads", "4"])
        self.assertEqual(one.read_bytes(), mapped.read_bytes())
        self.assertEqual(one.read_bytes(), many.read_bytes())

    def test_tiny_quantum_resumes_inside_rows_and_both_directions(self) -> None:
        inputs = _inputs(self.folder / "inputs", [("ABXABXXA", "XXBAXAXBAXXAB")])
        expected, expected_metrics = self.run_native("bounded", inputs, "expected")
        checkpoint = self.folder / "checkpoint"
        command, output, metrics = self.command(
            "linear",
            inputs,
            "resumed",
            [*self.checkpoint_flags(checkpoint), "--max-total-cells", "7"],
        )
        previous = 0
        cursors = []
        for _ in range(1000):
            result = subprocess.run(command, capture_output=True, text=True)
            self.assertIn(result.returncode, (0, 75), result.stderr)
            progress = json.loads((checkpoint / "progress.json").read_text())
            physical = progress["invocation_computed_cells"]
            if result.returncode == 75:
                self.assertGreater(physical, 0)
            else:
                self.assertGreaterEqual(physical, 0)
            self.assertLessEqual(physical, 7)
            self.assertEqual(progress["resumed_cells"], previous)
            self.assertEqual(progress["completed_cells"], previous + physical)
            previous = progress["completed_cells"]
            cursors.append(
                (progress["direction"], progress["row"], progress["next_column"])
            )
            if result.returncode == 0:
                break
            self.assertFalse(output.exists())
            self.assertFalse(metrics.exists())
        else:
            self.fail("Checkpoint invocations failed to complete")
        self.assertEqual(output.read_bytes(), expected.read_bytes())
        self.assertEqual(metrics.read_bytes(), expected_metrics.read_bytes())
        self.assertEqual({cursor[0] for cursor in cursors}, set(CELL_DIRECTIONS))
        self.assertTrue(
            any(
                a[:2] == b[:2] and b[2] > a[2]
                for a, b in zip(cursors, cursors[1:], strict=False)
            )
        )
        self.measurements["tiny_quantum_invocations"] = len(cursors)

    def test_completed_first_direction_is_reused(self) -> None:
        inputs = _inputs(self.folder / "inputs", [("ABX" * 30, "BAX" * 45)])
        expected, _ = self.run_native("bounded", inputs, "expected")
        target_cells = int(_read(expected)[0]["target_global_cells_evaluated"])
        checkpoint = self.folder / "checkpoint"
        command, output, metrics = self.command(
            "linear",
            inputs,
            "resumed",
            [
                *self.checkpoint_flags(checkpoint),
                "--max-total-cells",
                str(target_cells + 11),
            ],
        )
        result = subprocess.run(command, capture_output=True, text=True)
        self.assertEqual(result.returncode, 75, result.stderr)
        progress = json.loads((checkpoint / "progress.json").read_text())
        self.assertEqual(progress["direction"], "CANDIDATE_GLOBAL")
        tg = checkpoint / "job-00000000-TARGET_GLOBAL.bin"
        completed_tg = tg.read_bytes()
        prior = progress["completed_cells"]
        for _ in range(50):
            result = subprocess.run(command, capture_output=True, text=True)
            self.assertIn(result.returncode, (0, 75), result.stderr)
            progress = json.loads((checkpoint / "progress.json").read_text())
            self.assertEqual(progress["resumed_cells"], prior)
            self.assertEqual(tg.read_bytes(), completed_tg)
            prior = progress["completed_cells"]
            if result.returncode == 0:
                break
        else:
            self.fail("Second direction did not complete")
        self.assertEqual(output.read_bytes(), expected.read_bytes())
        output.unlink()
        metrics.unlink()
        result = subprocess.run(command, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        progress = json.loads((checkpoint / "progress.json").read_text())
        self.assertEqual(progress["invocation_computed_cells"], 0)
        self.assertEqual(progress["resumed_cells"], prior)
        self.assertEqual(output.read_bytes(), expected.read_bytes())

    def partial_checkpoint(self, label: str) -> tuple[list[str], Path, Path]:
        inputs = _inputs(self.folder / label, [("ABXABXABX", "BXABXABXABXA")])
        checkpoint = self.folder / f"{label}-checkpoint"
        command, output, _ = self.command(
            "linear",
            inputs,
            label,
            [*self.checkpoint_flags(checkpoint), "--max-total-cells", "17"],
        )
        result = subprocess.run(command, capture_output=True, text=True)
        self.assertEqual(result.returncode, 75, result.stderr)
        self.assertFalse(output.exists())
        state = next(checkpoint.glob("*.bin")).read_bytes()
        self.assertEqual(hashlib.sha256(state[:-32]).digest(), state[-32:])
        return command, checkpoint, output

    def test_corrupt_checkpoint_is_rejected(self) -> None:
        command, checkpoint, output = self.partial_checkpoint("corrupt")
        state = next(checkpoint.glob("*.bin"))
        payload = bytearray(state.read_bytes())
        payload[len(payload) // 2] ^= 1
        state.write_bytes(payload)
        result = subprocess.run(command, capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertNotEqual(result.returncode, 75)
        self.assertRegex(result.stderr.lower(), "checkpoint|checksum|identity")
        self.assertFalse(output.exists())

    def test_changed_sequence_or_identity_is_rejected(self) -> None:
        for change in ("sequence", "identity"):
            with self.subTest(change=change):
                command, _, output = self.partial_checkpoint(change)
                if change == "sequence":
                    targets = Path(command[command.index("--targets") + 1])
                    targets.write_text(
                        targets.read_text().replace("ABXABXABX", "ABXABXABY"),
                        encoding="utf-8",
                    )
                else:
                    index = command.index("--checkpoint-identity") + 1
                    command[index] = "b" * 64
                result = subprocess.run(command, capture_output=True, text=True)
                self.assertNotIn(result.returncode, (0, 75), result.stderr)
                self.assertRegex(
                    result.stderr.lower(), "checkpoint|checksum|identity|input"
                )
                self.assertFalse(output.exists())

    def test_memory_limit_refuses_before_execution(self) -> None:
        inputs = _inputs(self.folder / "inputs", [("A" * 1000, "C" * 1000)])
        command, output, metrics = self.command(
            "linear", inputs, "limited", ["--memory-limit-bytes", "1024"]
        )
        result = subprocess.run(command, capture_output=True, text=True)
        self.assertNotIn(result.returncode, (0, 75), result.stderr)
        self.assertRegex(result.stderr.lower(), "memory")
        self.assertFalse(output.exists())
        self.assertFalse(metrics.exists())

    @unittest.skipUnless(sys.platform.startswith("linux"), "Linux parent-death test")
    def test_parent_sigkill_checkpoints_child_and_allows_immediate_resume(self) -> None:
        # Adopt the orphaned worker so this test can verify its real exit code
        # and reap it; otherwise an unrelated container PID1 owns the zombie.
        libc = ctypes.CDLL(None, use_errno=True)
        old_subreaper = ctypes.c_int()
        self.assertEqual(libc.prctl(37, ctypes.byref(old_subreaper), 0, 0, 0), 0)
        self.assertEqual(libc.prctl(36, 1, 0, 0, 0), 0)
        wrapper = resumed = None
        worker_pid = None
        worker_reaped = False
        inputs = _inputs(self.folder / "inputs", [("A" * 6000, "C" * 6000)])
        checkpoint = self.folder / "checkpoint"
        command, output, metrics = self.command(
            "linear",
            inputs,
            "resumed",
            [*self.checkpoint_flags(checkpoint), "--checkpoint-rows", "32"],
        )
        launcher = (
            "import subprocess,sys\n"
            "worker=subprocess.Popen(sys.argv[1:])\n"
            "print(worker.pid,flush=True)\n"
            "sys.exit(worker.wait())\n"
        )
        try:
            with (self.folder / "parent-kill.stderr").open("w") as errors:
                wrapper = subprocess.Popen(
                    [sys.executable, "-c", launcher, *command],
                    stdout=subprocess.PIPE,
                    stderr=errors,
                    text=True,
                )
                self.assertIsNotNone(wrapper.stdout)
                worker_pid = int(wrapper.stdout.readline().strip())
                wrapper.stdout.close()
                deadline = time.monotonic() + 20
                while not (checkpoint / "progress.json").exists():
                    self.assertIsNone(wrapper.poll(), "Worker completed before kill")
                    self.assertLess(time.monotonic(), deadline, "No durable state")
                    time.sleep(0.01)
                before = json.loads((checkpoint / "progress.json").read_text())
                wrapper.kill()
                self.assertEqual(wrapper.wait(timeout=10), -signal.SIGKILL)

                # Start the retry immediately. Its lock must wait if the old
                # child is still committing its parent-death checkpoint.
                resumed = subprocess.Popen(
                    command,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.PIPE,
                    text=True,
                )
                deadline = time.monotonic() + 10
                while True:
                    found, status = os.waitpid(worker_pid, os.WNOHANG)
                    if found:
                        worker_reaped = True
                        break
                    self.assertLess(time.monotonic(), deadline, "Orphan did not stop")
                    time.sleep(0.01)
                self.assertEqual(os.waitstatus_to_exitcode(status), 75)
                _, errors_text = resumed.communicate(timeout=20)
                self.assertEqual(resumed.returncode, 0, errors_text)
                progress = json.loads((checkpoint / "progress.json").read_text())
                self.assertEqual(progress["status"], "COMPLETE")
                self.assertEqual(progress["signal"], 0)
                self.assertGreaterEqual(
                    progress["resumed_cells"], before["completed_cells"]
                )
                self.assertGreater(progress["resumed_cells"], 0)
            expected, expected_metrics = self.run_native("bounded", inputs, "expected")
            self.assertEqual(output.read_bytes(), expected.read_bytes())
            self.assertEqual(metrics.read_bytes(), expected_metrics.read_bytes())
            self.measurements["parent_sigkill_resume"] = {
                "child_exit_code": 75,
                "resumed_cells": progress["resumed_cells"],
                "completed_cells": progress["completed_cells"],
            }
        finally:
            if wrapper is not None and wrapper.poll() is None:
                wrapper.kill()
                wrapper.wait(timeout=10)
            if resumed is not None and resumed.poll() is None:
                resumed.kill()
                resumed.wait(timeout=10)
            if worker_pid is not None and not worker_reaped:
                try:
                    os.kill(worker_pid, signal.SIGKILL)
                    os.waitpid(worker_pid, 0)
                except ProcessLookupError:
                    pass
            self.assertEqual(libc.prctl(36, old_subreaper.value, 0, 0, 0), 0)

    def profiled(self, command: list[str], label: str) -> dict:
        started = time.perf_counter()
        with (self.folder / f"{label}.stderr").open("w+") as errors:
            process = subprocess.Popen(
                command, stdout=subprocess.DEVNULL, stderr=errors
            )
            try:
                _, status, usage = os.wait4(process.pid, 0)
                process.returncode = os.waitstatus_to_exitcode(status)
            finally:
                if process.returncode is None:
                    process.kill()
                    process.wait()
            errors.seek(0)
            self.assertEqual(process.returncode, 0, errors.read())
        rss = usage.ru_maxrss / 1024 if sys.platform == "darwin" else usage.ru_maxrss
        return {
            "elapsed_seconds": time.perf_counter() - started,
            "cpu_seconds": usage.ru_utime + usage.ru_stime,
            "peak_rss_kib": int(rss),
        }

    def test_large_pair_and_sigkill_resume(self) -> None:
        inputs = _inputs(self.folder / "inputs", [("A" * 6000, "C" * 6000)])
        reference_command, expected, expected_metrics = self.command(
            "bounded", inputs, "expected"
        )
        reference_usage = self.profiled(reference_command, "bounded")
        row = _read(expected)[0]
        total = sum(int(row[field]) for field in CELL_FIELDS)
        self.assertGreater(total, 50_000_000)
        self.assertEqual(total, 2 * 6001 * 6001)
        checkpoint = self.folder / "checkpoint"
        command, output, metrics = self.command(
            "linear",
            inputs,
            "resumed",
            [
                *self.checkpoint_flags(checkpoint),
                "--checkpoint-rows",
                "128",
                "--memory-limit-bytes",
                str(8 * 1024 * 1024),
            ],
        )
        with (self.folder / "interrupted.stderr").open("w") as errors:
            process = subprocess.Popen(
                command, stdout=subprocess.DEVNULL, stderr=errors
            )
            try:
                deadline = time.monotonic() + 20
                while not list(checkpoint.glob("*.bin")):
                    self.assertIsNone(process.poll(), "Native run ended before kill")
                    self.assertLess(time.monotonic(), deadline, "No durable checkpoint")
                    time.sleep(0.01)
                process.send_signal(signal.SIGKILL)
                self.assertEqual(process.wait(timeout=10), -signal.SIGKILL)
            finally:
                if process.poll() is None:
                    process.kill()
                    process.wait(timeout=10)
        self.assertFalse(output.exists())
        linear_usage = self.profiled(command, "linear-resumed")
        progress = json.loads((checkpoint / "progress.json").read_text())
        self.assertGreater(progress["resumed_cells"], 0)
        self.assertEqual(progress["completed_cells"], total)
        self.assertLessEqual(progress["required_memory_bytes"], 8 * 1024 * 1024)
        self.assertEqual(output.read_bytes(), expected.read_bytes())
        self.assertEqual(metrics.read_bytes(), expected_metrics.read_bytes())
        self.measurements["large_pair"] = {
            "target_length": 6000,
            "candidate_length": 6000,
            "certified_cells": total,
            "bounded": reference_usage,
            "rolling_resumed": linear_usage,
            "progress": progress,
        }

    def test_production_quantum_and_hundred_thousand_residues(self) -> None:
        # A dense certified region exercises the actual production quantum;
        # an independent thin band exercises sequence length with modest work.
        with self.subTest(case="production_quantum"):
            length = 16_000
            total = 2 * (length + 1) ** 2
            quantum = 500_000_000
            inputs = _inputs(
                self.folder / "dense-inputs", [("A" * length, "C" * length)]
            )
            reference_command, expected, expected_metrics = self.command(
                "bounded", inputs, "dense-expected"
            )
            reference_usage = self.profiled(reference_command, "dense-bounded")
            checkpoint = self.folder / "dense-checkpoint"
            command, output, metrics = self.command(
                "linear",
                inputs,
                "dense-resumed",
                [
                    *self.checkpoint_flags(checkpoint),
                    "--max-total-cells",
                    str(quantum),
                    "--checkpoint-rows",
                    "4096",
                    "--memory-limit-bytes",
                    str(8 * 1024 * 1024),
                ],
            )
            started = time.perf_counter()
            first = subprocess.run(command, capture_output=True, text=True)
            first_elapsed = time.perf_counter() - started
            self.assertEqual(first.returncode, 75, first.stderr)
            initial = json.loads((checkpoint / "progress.json").read_text())
            self.assertEqual(initial["status"], "CHECKPOINTED")
            self.assertEqual(initial["direction"], "CANDIDATE_GLOBAL")
            self.assertEqual(initial["invocation_computed_cells"], quantum)
            self.assertEqual(initial["resumed_cells"], 0)
            self.assertEqual(initial["completed_cells"], quantum)
            self.assertFalse(output.exists())
            self.assertFalse(metrics.exists())
            second_usage = self.profiled(command, "dense-linear-second")
            complete = json.loads((checkpoint / "progress.json").read_text())
            self.assertEqual(complete["status"], "COMPLETE")
            self.assertEqual(complete["resumed_cells"], quantum)
            self.assertEqual(complete["invocation_computed_cells"], total - quantum)
            self.assertEqual(complete["completed_cells"], total)
            self.assertEqual(output.read_bytes(), expected.read_bytes())
            self.assertEqual(metrics.read_bytes(), expected_metrics.read_bytes())
            self.measurements["production_quantum"] = {
                "target_length": length,
                "candidate_length": length,
                "certified_cells": total,
                "invocations": 2,
                "bounded": reference_usage,
                "first_invocation_elapsed_seconds": first_elapsed,
                "second_invocation": second_usage,
                "first_progress": initial,
                "complete_progress": complete,
            }
        with self.subTest(case="hundred_thousand_residues"):
            length = 100_000
            inputs = _inputs(
                self.folder / "long-inputs", [("A" * length, "A" * length)]
            )
            bounded, bounded_metrics = self.run_native(
                "bounded", inputs, "long-bounded"
            )
            linear, linear_metrics = self.run_native("linear", inputs, "long-linear")
            self.assertEqual(linear.read_bytes(), bounded.read_bytes())
            self.assertEqual(linear_metrics.read_bytes(), bounded_metrics.read_bytes())
            row = _read(linear)[0]
            cells = sum(int(row[field]) for field in CELL_FIELDS)
            self.assertEqual(cells, 2 * (length + 1))
            for direction in ("target_global", "candidate_global"):
                self.assertEqual(int(row[f"{direction}_matches"]), length)
            self.measurements["hundred_thousand_residues"] = {
                "target_length": length,
                "candidate_length": length,
                "certified_cells": cells,
                "equivalent_fields": len(row),
            }


CELL_DIRECTIONS = ("TARGET_GLOBAL", "CANDIDATE_GLOBAL")


if __name__ == "__main__":
    unittest.main(verbosity=2)
