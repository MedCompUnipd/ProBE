"""Advisory publication ownership survives interruptions without stale locks."""

import json
import os
import selectors
import signal
import subprocess
import sys
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path

from probe.workflow import artifacts

METADATA = dict(
    stage="resume-fixture",
    role=None,
    release_id=None,
    parameters={"policy_version": "fixture-v1"},
    ontology=None,
    implementation={"name": "resume-test", "version": "1"},
)

WORKER = r"""
import json
import signal
import sys
from pathlib import Path

from probe.workflow import artifacts

destination = Path(sys.argv[1])
phase = sys.argv[2]
metadata = json.loads(sys.argv[3])
original_fsync = artifacts._fsync_directory

def fsync_and_pause(path):
    original_fsync(path)
    if phase == "published" and path == destination.parent:
        print(json.dumps({"phase": "published"}), flush=True)
        signal.pause()

artifacts._fsync_directory = fsync_and_pause
with artifacts.atomic_stage_bundle(destination, **metadata) as staging:
    (staging / "facts.tsv").write_text("first-producer\n", encoding="utf-8")
    if phase == "before":
        print(json.dumps({"phase": "before", "staging": str(staging)}), flush=True)
        signal.pause()
"""


@contextmanager
def paused_publisher(destination, phase):
    environment = dict(os.environ)
    source_root = str(Path(artifacts.__file__).parents[2])
    environment["PYTHONPATH"] = os.pathsep.join(
        [source_root, environment.get("PYTHONPATH", "")]
    )
    process = subprocess.Popen(
        [sys.executable, "-c", WORKER, str(destination), phase, json.dumps(METADATA)],
        env=environment,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        with selectors.DefaultSelector() as selector:
            selector.register(process.stdout, selectors.EVENT_READ)
            if not selector.select(timeout=10):
                process.kill()
                stdout, stderr = process.communicate(timeout=5)
                raise AssertionError(
                    f"producer readiness timeout: {stdout!r} {stderr!r}"
                )
        line = process.stdout.readline()
        if not line:
            stdout, stderr = process.communicate(timeout=5)
            raise AssertionError(
                f"producer exited before readiness: {stdout!r} {stderr!r}"
            )
        yield process, json.loads(line)
    finally:
        if process.poll() is None:
            process.kill()
        process.communicate(timeout=5)


class PublicationResumeTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)

    def test_sigkill_before_publication_allows_restart_without_unlock(self):
        tmp_path = self.root
        destination = tmp_path / "bundle"
        lock = tmp_path / ".bundle.publish-lock"
        with paused_publisher(destination, "before") as (process, ready):
            incomplete = Path(ready["staging"])
            assert incomplete.is_dir()
            assert not destination.exists()
            inode = lock.stat().st_ino
            process.kill()
            assert process.wait(timeout=5) == -signal.SIGKILL

            with artifacts.atomic_stage_bundle(destination, **METADATA) as staging:
                assert staging != incomplete
                (staging / "facts.tsv").write_text("fresh-restart\n", encoding="utf-8")

        artifacts.validate_bundle(destination, deep=True)
        assert (destination / "facts.tsv").read_text() == "fresh-restart\n"
        assert lock.is_file()
        assert lock.stat().st_ino == inode
        # Unpublished leftovers are never accepted or removed as a valid bundle.
        assert (incomplete / "facts.tsv").read_text() == "first-producer\n"

    def test_live_publisher_is_rejected_across_processes(self):
        tmp_path = self.root
        destination = tmp_path / "bundle"
        with paused_publisher(destination, "before") as (process, ready):
            with (
                self.assertRaisesRegex(FileExistsError, "active writer"),
                artifacts.atomic_stage_bundle(destination, **METADATA),
            ):
                raise AssertionError("two processes acquired the publication lock")
            assert process.poll() is None
            assert not destination.exists()
            assert Path(ready["staging"]).is_dir()

    def test_sigkill_after_rename_preserves_published_bundle(self):
        tmp_path = self.root
        destination = tmp_path / "bundle"
        with paused_publisher(destination, "published") as (process, ready):
            assert ready["phase"] == "published"
            artifacts.validate_bundle(destination, deep=True)
            before = {path.name: path.read_bytes() for path in destination.iterdir()}
            process.kill()
            assert process.wait(timeout=5) == -signal.SIGKILL

            with (
                self.assertRaises(FileExistsError) as error,
                artifacts.atomic_stage_bundle(
                    destination, allow_invalidated_destination=True, **METADATA
                ),
            ):
                raise AssertionError("published bundle was overwritten after SIGKILL")
        assert "active writer" not in str(error.exception)

        artifacts.validate_bundle(destination, deep=True)
        assert before == {
            path.name: path.read_bytes() for path in destination.iterdir()
        }

    def test_legacy_directory_lock_is_preserved_as_explicit_conflict(self):
        tmp_path = self.root
        destination = tmp_path / "bundle"
        legacy = tmp_path / ".bundle.publish-lock"
        legacy.mkdir()
        marker = legacy / "owner-evidence.txt"
        marker.write_text("may belong to a live legacy producer")
        with (
            self.assertRaisesRegex(FileExistsError, "legacy publication lock"),
            artifacts.atomic_stage_bundle(destination, **METADATA),
        ):
            raise AssertionError("legacy lock was silently cleared")
        assert not destination.exists()
        assert marker.read_text() == "may belong to a live legacy producer"

    def test_lock_descriptor_is_not_inherited_by_exec(self):
        tmp_path = self.root
        path = tmp_path / ".bundle.publish-lock"
        descriptor = artifacts._acquire_publication_lock(path)
        try:
            assert not os.get_inheritable(descriptor)
        finally:
            artifacts._release_publication_lock(descriptor)

    def test_exception_releases_lock_and_same_process_competitor_is_rejected(self):
        destination = self.root / "bundle"
        lock = self.root / ".bundle.publish-lock"
        with (
            self.assertRaisesRegex(RuntimeError, "producer exception"),
            artifacts.atomic_stage_bundle(destination, **METADATA) as staging,
        ):
            with (
                self.assertRaisesRegex(FileExistsError, "active writer"),
                artifacts.atomic_stage_bundle(destination, **METADATA),
            ):
                raise AssertionError("same-process competitor admitted")
            (staging / "partial.tsv").write_text("incomplete")
            raise RuntimeError("producer exception")
        assert list(self.root.iterdir()) == [lock]
        with artifacts.atomic_stage_bundle(destination, **METADATA) as staging:
            (staging / "facts.tsv").write_text("restarted")
        artifacts.validate_bundle(destination, deep=True)


if __name__ == "__main__":
    unittest.main()
