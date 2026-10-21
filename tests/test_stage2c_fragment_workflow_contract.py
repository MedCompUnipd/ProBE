"""Actual pinned FULL/union wrappers retain only literal EXACT fragments."""

from __future__ import annotations

import csv
import json
import runpy
import shutil
import subprocess
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from probe import stage2c_full_exact as full_plan
from probe import stage2c_union_plan as union_plan
from probe.stage2c_candidate_union import merge_discovery
from probe.workflow.artifacts import (
    Artifact,
    StageManifest,
    sha256_file,
    validate_bundle,
)

ROOT = Path(__file__).resolve().parents[1]
GO_HEADER = (
    "target_id\trid\taccession\traw_ox\tresolved_taxid\t"
    "species_taxid\tfrag\texact_sequence\trecord_number\n"
)
FULL_PAIRS = "03_full_exact_cross_strain.pairs.tsv"
FULL_SUMMARY = "03_full_exact_cross_strain.summary.tsv"


def _rows(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as stream:
        return list(csv.DictReader(stream, delimiter="\t"))


def _write_rows(path: Path, rows: list[dict[str, str]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(
            stream, fieldnames=tuple(rows[0]), delimiter="\t", lineterminator="\n"
        )
        writer.writeheader()
        writer.writerows(rows)


class FragmentWorkflowContractTests(unittest.TestCase):
    """stdlib tests are collected by pytest in the project environment."""

    @classmethod
    def setUpClass(cls) -> None:
        compiler = shutil.which("g++")
        if compiler is None:
            raise AssertionError("Fragment workflow validation requires g++")
        cls.compiler_directory = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.compiler_directory.cleanup)
        cls.compiled = Path(cls.compiler_directory.name) / "full-exact"
        subprocess.run(
            [
                compiler,
                "-std=c++20",
                "-O1",
                str(ROOT / "cpp/src/probe_full_exact_cross_strain_v1.cpp"),
                "-o",
                str(cls.compiled),
            ],
            check=True,
        )

    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name)
        self.repo = self.base / "repo"
        self.run = self.base / "run"
        self.repo.mkdir()
        self.run.mkdir()
        subprocess.run(["git", "init", "-q", str(self.repo)], check=True)
        subprocess.run(
            [
                "git",
                "-C",
                str(self.repo),
                "-c",
                "user.name=Test",
                "-c",
                "user.email=test@example.org",
                "commit",
                "--allow-empty",
                "-qm",
                "synthetic fixture",
            ],
            check=True,
        )
        for name in set(full_plan.CODE_FILES + union_plan.CODE_FILES):
            target = self.repo / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT / name, target)
        self.binary = self.repo / full_plan.BINARY
        self.binary.parent.mkdir(parents=True)
        shutil.copyfile(self.compiled, self.binary)
        self.binary.chmod(0o700)
        self.stages = self.run / "work/pipeline-v2"
        self._publish(
            self.stages / "common/01",
            "01",
            None,
            "01_targets.tsv",
            "target_id\tnormalized_sequence\nT1\tACXDE\n",
        )
        fasta = "".join(
            f">sp|P{rid:05d}|PROT OX={ox} FRAG={frag} RID={rid} SRC=SP REC=R\n"
            f"{sequence}\n"
            for rid, ox, frag, sequence in (
                (1, 111, 1, "ACXDE"),
                (2, 112, 1, "ACXDE"),
                (3, 111, 1, "ACYDE"),
                (4, 211, 1, "ACXDE"),
                (5, 111, 0, "ACXDE"),
            )
        )
        for role in ("START", "END"):
            for stage, name, content in (
                ("01A_FULL", "01A_full_v2.fasta", fasta),
                (
                    "02B",
                    "02B_target_taxonomy.tsv",
                    "target_id\tresolved_taxid\tspecies_taxid\tprimary_bucket\n"
                    "T1\t111\t100\tT:111\n",
                ),
                (
                    "04D0b",
                    "full_ox_taxonomy.tsv",
                    "raw_ox\tresolved_taxid\tspecies_taxid\tspecies_bucket\n"
                    "111\t111\t100\tS:100\n"
                    "112\t112\t100\tS:100\n"
                    "211\t211\t200\tS:200\n",
                ),
            ):
                self._publish(self.stages / role / stage, stage, role, name, content)
        self.plan_path = full_plan.create_plan(self.repo, self.run)
        self.full = full_plan.read_plan(self.plan_path)

    def _publish(
        self,
        directory: Path,
        stage: str,
        role: str | None,
        name: str,
        content: str,
        fingerprint: str | None = None,
    ) -> Path:
        directory.mkdir(parents=True)
        path = directory / name
        path.write_text(content, encoding="utf-8")
        implementation = {"name": "fixture", "version": "1"}
        if fingerprint is not None:
            implementation["discovery_fingerprint"] = fingerprint
        release = {"START": "2023_01", "END": "2026_03"}.get(role)
        StageManifest(
            stage=stage,
            role=role,
            release_id=release,
            parameters={},
            ontology=None,
            implementation=implementation,
            inputs=(),
            outputs=(Artifact(name, path.stat().st_size, sha256_file(path)),),
        ).write(directory / "manifest.json")
        return directory

    def _run_full(self, role: str) -> Path:
        destination = Path(self.full["paths"][role])
        context = SimpleNamespace(
            params=SimpleNamespace(role=role, plan_path=str(self.plan_path)),
            input=SimpleNamespace(
                target_manifest=str(self.stages / "common/01/manifest.json"),
                full_manifest=str(self.stages / role / "01A_FULL/manifest.json"),
                taxonomy_manifest=str(self.stages / role / "02B/manifest.json"),
                ox_manifest=str(self.stages / role / "04D0b/manifest.json"),
                binary=str(self.binary),
            ),
            output=[str(destination / "manifest.json")],
            log=[str(self.run / f"logs/full-{role}.log")],
        )
        runpy.run_path(
            str(ROOT / "workflow/scripts/run_stage_03_full_exact.py"),
            init_globals={"snakemake": context},
        )
        return destination

    def _union_spec(self) -> dict:
        species_path = self.run / "species.json"
        species_path.write_text("{}\n", encoding="utf-8")
        species = {
            "repo": str(self.repo),
            "run": str(self.run),
            "key": "a" * 64,
            "paths": {},
            "sources": {},
        }
        for role in ("START", "END"):
            species["paths"][role] = str(self.run / "go" / role)
            species["sources"][role] = {
                "taxonomy": self.full["sources"][role]["taxonomy"]
            }
        with patch.object(union_plan, "read_species_plan", return_value=species):
            return union_plan.build_plan(self.repo, species_path, self.plan_path)

    def test_native_wrapper_retains_literal_fragments_in_both_releases(self) -> None:
        for role, release in (("START", "2023_01"), ("END", "2026_03")):
            with self.subTest(role=role):
                destination = self._run_full(role)
                manifest = validate_bundle(destination, deep=True)
                self.assertEqual(manifest.parameters["fragment"], "include")
                command = manifest.parameters["command"]
                self.assertEqual(
                    command[command.index("--fragment-policy") + 1], "include"
                )
                rows = _rows(destination / FULL_PAIRS)
                self.assertEqual([row["rid"] for row in rows], ["1", "2", "5"])
                self.assertEqual({row["release"] for row in rows}, {release})
                self.assertEqual([row["frag"] for row in rows], ["1", "1", "0"])
                metrics = {
                    row["metric"]: row["value"]
                    for row in _rows(destination / FULL_SUMMARY)
                }
                self.assertEqual(metrics["fragment_policy"], "include")
                self.assertEqual(metrics["fragments_observed"], "4")
                self.assertEqual(metrics["fragments_skipped"], "0")
                self.assertEqual(metrics["same_taxid_exact_pairs"], "2")
                self.assertEqual(metrics["cross_strain_exact_pairs"], "1")
                with self.assertRaises(FileExistsError):
                    self._run_full(role)

    def test_union_wrapper_retains_full_fragments_with_original_gates(self) -> None:
        spec = self._union_spec()
        for role in ("START", "END"):
            with self.subTest(role=role):
                full = self._run_full(role)
                go = self._publish(
                    Path(spec["species_paths"][role]),
                    "03",
                    role,
                    "03_primary_candidates.pairs.tsv",
                    GO_HEADER,
                    spec["species_key"],
                )
                destination = Path(spec["paths"][role])
                context = SimpleNamespace(
                    params=SimpleNamespace(role=role, plan_path="synthetic-union"),
                    input=SimpleNamespace(
                        go_manifest=str(go / "manifest.json"),
                        full_manifest=str(full / "manifest.json"),
                        taxonomy_manifest=str(self.stages / role / "02B/manifest.json"),
                    ),
                    output=SimpleNamespace(
                        union_manifest=str(destination / "manifest.json")
                    ),
                    log=[str(self.run / f"logs/union-{role}.log")],
                )
                with patch.object(union_plan, "read_plan", return_value=spec):
                    runpy.run_path(
                        str(ROOT / "workflow/scripts/run_stage_03_discovery_union.py"),
                        init_globals={"snakemake": context},
                    )
                manifest = validate_bundle(destination, deep=True)
                self.assertIs(manifest.parameters["allow_literal_fragments"], True)
                rows = {
                    row["rid"]: row for row in _rows(destination / union_plan.PAIRS)
                }
                self.assertEqual(set(rows), {"1", "2", "5"})
                self.assertEqual(rows["1"]["strict_gate"], "DEFER_TO_DOCUMENTARY")
                self.assertEqual(rows["2"]["strict_gate"], "DENIED")
                self.assertEqual(rows["2"]["same_species_exact_gate"], "ELIGIBLE")

    def test_reported_fragment_exclusion_prevents_publication(self) -> None:
        native_run = subprocess.run

        def exclude_in_native(command: list[str], **kwargs):
            altered = list(command)
            if altered[0] == str(self.binary):
                altered[altered.index("--fragment-policy") + 1] = "exclude"
            return native_run(altered, **kwargs)

        with (
            patch.object(subprocess, "run", side_effect=exclude_in_native),
            self.assertRaisesRegex(ValueError, "fragment policy mismatch"),
        ):
            self._run_full("START")
        self.assertFalse(Path(self.full["paths"]["START"]).exists())

    def test_old_plan_contracts_cannot_be_reused(self) -> None:
        old = deepcopy(self.full)
        old["schema"] = "probe-stage2c-full-exact-plan-v3"
        old["science"] = "same-species-all-taxids-literal-iupac26-exact-v3"
        old["parameters"]["fragment"] = "exclude"
        with self.assertRaisesRegex(ValueError, "Unsupported FULL exact plan"):
            full_plan.verify_plan(old)
        identity_keys = (
            "schema",
            "science",
            "sources",
            "code",
            "binary_sha256",
            "parameters",
        )
        self.assertNotEqual(
            full_plan._key({key: old[key] for key in identity_keys}), self.full["key"]
        )
        old_union = self._union_spec()
        old_union["schema"] = "probe-stage2c-union-plan-v2"
        with self.assertRaisesRegex(ValueError, "Unknown Stage 2C union plan"):
            union_plan.verify_plan(old_union)

    def test_union_plan_pins_fragment_permission(self) -> None:
        spec = self._union_spec()
        self.assertEqual(self.full["schema"], "probe-stage2c-full-exact-plan-v4")
        self.assertEqual(self.full["parameters"]["fragment"], "include")
        self.assertEqual(spec["schema"], "probe-stage2c-union-plan-v3")
        self.assertEqual(spec["parameters"], {"allow_literal_fragments": True})
        self.assertNotIn("subtaxon_policy", json.dumps(spec))
        identity_keys = (
            "schema",
            "science",
            "species_key",
            "full_key",
            "taxonomy",
            "releases",
            "code",
            "parameters",
        )
        disabled = {key: deepcopy(spec[key]) for key in identity_keys}
        disabled["parameters"]["allow_literal_fragments"] = False
        self.assertNotEqual(union_plan._key(disabled), spec["key"])

    def _merge_inputs(self) -> dict:
        full = self._run_full("START") / FULL_PAIRS
        go = self.run / "go.tsv"
        go.write_text(GO_HEADER, encoding="utf-8")
        return {
            "release": "2023_01",
            "target_taxonomy": self.stages / "START/02B/02B_target_taxonomy.tsv",
            "go_positive_pairs": go,
            "full_exact_pairs": full,
            "output": self.run / "merged.tsv",
            "summary": self.run / "merged.json",
            "allow_literal_fragments": True,
        }

    def test_go_positive_fragments_still_rejected(self) -> None:
        args = self._merge_inputs()
        args["go_positive_pairs"].write_text(
            GO_HEADER + "T1\t3\tP00003\t111\t111\t100\t1\t0\t3\n",
            encoding="utf-8",
        )
        with self.assertRaisesRegex(ValueError, "FRAG=1"):
            merge_discovery(**args)
        self.assertFalse(args["output"].exists())

    def test_full_fragment_nonexact_and_foreign_species_still_rejected(self) -> None:
        args = self._merge_inputs()
        original = _rows(args["full_exact_pairs"])
        for field, value, message in (
            ("exact_full_sequence", "0", "Invalid FULL exact evidence"),
            ("species_taxid", "200", "Cross-species"),
        ):
            with self.subTest(field=field):
                rows = deepcopy(original)
                rows[0][field] = value
                altered = self.run / f"{field}.tsv"
                _write_rows(altered, rows)
                with self.assertRaisesRegex(ValueError, message):
                    merge_discovery(**dict(args, full_exact_pairs=altered))
                self.assertFalse(args["output"].exists())


if __name__ == "__main__":
    unittest.main()
