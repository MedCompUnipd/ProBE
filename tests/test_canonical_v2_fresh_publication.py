"""Fresh receipts exercise actual files, native manifests and process failure.

Only scientific dispatch is replaced by small deterministic fixture producers.
Validation, SHA256, intent/receipt publication and advisory locking are real.
The native manifest shapes follow the producers at published HEAD 7250810.
"""

from __future__ import annotations

import hashlib
import json
import multiprocessing
import os
import signal
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from probe.canonical_v2 import fresh_publication as publication
from probe.canonical_v2.dag_contract import (
    COMMON_INPUTS,
    RELEASE_INPUTS,
    SCIENTIFIC_CONTRACTS,
    TOOLS,
    VERSION,
    destination,
    read_dag_spec,
)
from probe.workflow.artifacts import Artifact, StageManifest, sha256_file

RECIPE = hashlib.sha256(b"fresh synthetic receipt recipe").hexdigest()
OTHER_RECIPE = hashlib.sha256(b"different fresh recipe").hexdigest()
LEDGER_SCHEMA = "probe-canonical-v2-candidate-ledger-v1"
LEDGER_ANCHOR = "candidate_provenance.summary.json"
LEDGER_PAYLOAD = "candidate_provenance.tsv"
RESOLVER_VERSION = "probe-canonical-v2-stage07-science-v2-literal-fragment"
M8_SCHEMA = "stage08-raw-collection-bundle-v1"
M10C_SCHEMA = "stage08-m10c-compact-interpretation-v1"
TEMPORAL_SCHEMA = "stage08-temporal-eval-skip-neutral-v3"


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, sort_keys=True) + "\n", encoding="utf-8")


def _read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _corrupt_same_length(path: Path) -> None:
    before = path.read_bytes()
    if not before:
        raise AssertionError("Corruption fixture must contain bytes")
    after = bytes([before[0] ^ 1]) + before[1:]
    path.write_bytes(after)
    if path.stat().st_size != len(before):
        raise AssertionError("Corruption changed the file length")


def _artifact(path: Path, *, relative_to: Path | None = None) -> Artifact:
    name = (
        str(path) if relative_to is None else path.relative_to(relative_to).as_posix()
    )
    return Artifact(name, path.stat().st_size, sha256_file(path))


class ReceiptFixture:
    """Provide pinned existing inputs and the native shapes dispatch produces."""

    def __init__(self, root: Path) -> None:
        self.root = root
        sources = root / "inputs"
        sources.mkdir(parents=True)

        def source(group: str, name: str) -> str:
            path = sources / group / name
            path.parent.mkdir(parents=True, exist_ok=True)
            if name in {"goa_bundle", "go_policy_bundle"}:
                path.mkdir()
                _write_json(path / "manifest.json", {"fixture_source": group})
                if name == "goa_bundle":
                    (path / "goa.normalized.gaf").write_text(
                        f"normalized GAF {group}\n", encoding="utf-8"
                    )
            elif name == "full_manifest":
                _write_json(
                    path,
                    {
                        "outputs": [{"path": "01A_full_v2.fasta", "sha256": "a" * 64}],
                    },
                )
            else:
                path.write_text(f"pinned {group}/{name}\n", encoding="utf-8")
            return str(path)

        self.spec = {
            "schema": VERSION,
            "mode": "execute",
            "policy": "strict",
            "releases": {"START": "2023_01", "END": "2026_03"},
            "output_root": str(root / "output"),
            "inputs": {name: source("common", name) for name in COMMON_INPUTS},
            "per_release": {
                role: {name: source(role, name) for name in RELEASE_INPUTS}
                for role in ("START", "END")
            },
            "tools": {name: source("tools", name) for name in TOOLS},
            "limits": {
                "threads": 1,
                "max_pair_cells": 50_000_000,
                "max_total_cells": 500_000_000,
                "max_chunk_cells": 50_000_000,
                "partial_sequence": "report_only",
                "documentary_fragment": "report_only",
            },
            "scientific_contracts": dict(SCIENTIFIC_CONTRACTS),
        }
        spec_path = root / "spec.json"
        _write_json(spec_path, self.spec)
        if read_dag_spec(spec_path) != self.spec:
            raise AssertionError("Fixture must pass the actual DAG input contract")

    def bundle(self, stage: str = "ledger", role: str | None = "START") -> Path:
        return destination(self.spec, stage, role)

    def intent(self, stage: str = "ledger", role: str | None = "START") -> Path:
        return Path(f"{self.bundle(stage, role)}.fresh.intent.json")

    def marker(self, stage: str = "ledger", role: str | None = "START") -> Path:
        return publication.receipt_path(self.bundle(stage, role))

    def ledger(self, *, target: Path, role: str) -> None:
        target.mkdir(parents=True)
        (target / LEDGER_PAYLOAD).write_text(
            "release\ttarget_id\tRID\n2023_01\tT1\t17\n", encoding="utf-8"
        )
        labels = {
            "discovery_union": "unified_discovery",
            "go_positive_pairs": "go_positive_pairs",
            "full_exact_pairs": "full_exact_pairs",
            "lineage_pairs": "lineage_pairs",
            "rescue_pairs": "rescue_pairs",
        }
        _write_json(
            target / LEDGER_ANCHOR,
            {
                "schema": LEDGER_SCHEMA,
                "release": self.spec["releases"][role],
                "go_donor_decisions": 0,
                "alignment_decisions": 0,
                "taxonomy_audit_promotions": 0,
                "counts": {"candidate_pairs": 1},
                "input_sha256": {
                    label: sha256_file(Path(self.spec["per_release"][role][name]))
                    for label, name in labels.items()
                },
                "output_sha256": {LEDGER_PAYLOAD: sha256_file(target / LEDGER_PAYLOAD)},
            },
        )

    def native_manifest_sources(self, stage: str) -> tuple[Path, ...]:
        """Pin real resolver_runner and CollectionInputs native path contracts."""
        common = self.spec["inputs"]
        per = self.spec["per_release"]
        if stage == "stage07":
            sources = [Path(common[name]) for name in ("targets", "lineage_index")]
            sources.extend(
                self.bundle("workload", None) / name
                for name in (
                    "2C_06A_alignment_jobs.tsv",
                    "2C_05D_START_variant.tsv",
                    "2C_05D_END_variant.tsv",
                    "2C_05A2_variant.tsv",
                    "2C_05C2_variant.tsv",
                    "documentary_evidence_preserved.tsv",
                )
            )
            sources.extend(
                self.bundle("alignment", None) / name
                for name in (
                    "2C_06C1_canonical_directional_glocal.tsv",
                    "2C_policy_skipped_nonexact.tsv",
                    "2C_06C0_certified_dp_plan.tsv",
                    "canonical_alignment_execution.summary.json",
                )
            )
            sources.extend(
                Path(per[role][name])
                for role in ("START", "END")
                for name in ("taxonomy", "anchors", "lineage")
            )
            for path in sources:
                if not path.exists():
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_text(f"source {path.name}\n", encoding="utf-8")
        else:
            stage07 = self.bundle("stage07", None)
            if not stage07.exists():
                self.stage_manifest(target=stage07, stage="stage07")
            sources = [
                stage07 / name
                for name in (
                    "manifest.json",
                    "07_accepted_associations.tsv",
                    "07_selection_metadata.json",
                )
            ]
            sources.append(Path(common["go_policy_bundle"]) / "manifest.json")
            sources.extend(
                Path(per[role]["goa_bundle"]) / name
                for role in ("START", "END")
                for name in ("manifest.json", "goa.normalized.gaf")
            )
        return tuple(sources)

    def stage_manifest(self, *, target: Path, stage: str) -> None:
        target.mkdir(parents=True)
        if stage == "stage07":
            names = (
                "07_accepted_associations.tsv",
                "07_complete_decisions.tsv",
                "07_selection_metadata.json",
                "07_canonical_scientific_summary.json",
            )
            parameters = {
                "start_release_id": self.spec["releases"]["START"],
                "end_release_id": self.spec["releases"]["END"],
                "policy_version": RESOLVER_VERSION,
                "policies": {
                    "resolver": {"strain_policy": self.spec["policy"]},
                    "annotation_donor": {
                        name: self.spec["limits"][name]
                        for name in ("partial_sequence", "documentary_fragment")
                    },
                },
                "canonical_v2": True,
            }
            implementation = {
                "name": "canonical-stage07-v2",
                "version": RESOLVER_VERSION,
            }
        else:
            names = ("08_collection_summary.json", "stage_outcome.json")
            names += tuple(
                f"08_{role}_{suffix}"
                for role in ("start", "end")
                for suffix in (
                    "source_assertions.tsv",
                    "annotation_links.tsv",
                    "donor_coverage.tsv",
                    "evidence_inventory.tsv",
                    "scan_summary.json",
                )
            )
            parameters = {
                "schema_version": M8_SCHEMA,
                "input_fingerprint": "b" * 64,
                "collection": "all_retained_normalized_gaf_donor_rows",
                "evidence_selection": "none",
                "not_veto": "none",
                "donor_criterion": "annotation_donor=YES",
                "release_ids": self.spec["releases"],
            }
            implementation = {"name": "stage08-collection", "version": M8_SCHEMA}
        for name in names:
            (target / name).write_text(f"fixture {name}\n", encoding="utf-8")
        if stage == "m8":
            _write_json(
                target / "08_collection_summary.json",
                {
                    "schema_version": M8_SCHEMA,
                    "annotation_coverage_certified": True,
                    "by_role": {
                        role: {
                            "release": self.spec["releases"][role],
                            "normalized_gaf_sha256": ("1" if role == "START" else "2")
                            * 64,
                        }
                        for role in ("START", "END")
                    },
                },
            )
        manifest = StageManifest(
            stage="07" if stage == "stage07" else "08",
            role=None,
            release_id=None,
            parameters=parameters,
            ontology=None,
            implementation=implementation,
            inputs=tuple(
                _artifact(path) for path in self.native_manifest_sources(stage)
            ),
            outputs=tuple(
                _artifact(target / name, relative_to=target) for name in names
            ),
        )
        manifest.write(target / "manifest.json")

    def custom_manifest(self, *, target: Path, stage: str) -> None:
        m8_root = self.bundle("m8", None)
        if not m8_root.exists():
            self.stage_manifest(target=m8_root, stage="m8")
        m8 = _read_json(m8_root / "08_collection_summary.json")
        target.mkdir(parents=True)
        summary = {
            "schema_version": M10C_SCHEMA if stage == "m10c" else TEMPORAL_SCHEMA,
            "ontology_sha256": sha256_file(Path(self.spec["inputs"]["ontology"])),
        }
        if stage == "m10c":
            names = ("direct_provenance.sqlite3", "propagated_terms.tsv")
            summary["source_payloads"] = {
                role: {
                    "source_sha256": sha256_file(
                        m8_root / f"08_{role.lower()}_source_assertions.tsv"
                    ),
                    "links_sha256": sha256_file(
                        m8_root / f"08_{role.lower()}_annotation_links.tsv"
                    ),
                    "gaf_source_sha256": m8["by_role"][role]["normalized_gaf_sha256"],
                }
                for role in ("START", "END")
            }
        else:
            m10c_root = self.bundle("m10c", None)
            if not m10c_root.exists():
                self.custom_manifest(target=m10c_root, stage="m10c")
            names = (
                "benchmark_terms.tsv",
                "target_aspects.tsv",
                "term_audit.tsv",
                "negative_terms.tsv",
                "temporal_events.tsv",
                "duplicate_targets.tsv",
                "audit_reasons.json",
            )
            code = Path(self.spec["tools"]["temporal_cli"])
            summary.update(
                {
                    "mode": "EXECUTE",
                    "output_published": True,
                    "start_release": self.spec["releases"]["START"],
                    "end_release": self.spec["releases"]["END"],
                    "m10c_manifest_sha256": sha256_file(m10c_root / "manifest.json"),
                    "m8_summary_sha256": sha256_file(
                        m8_root / "08_collection_summary.json"
                    ),
                    "policy_sha256": sha256_file(
                        Path(self.spec["inputs"]["temporal_policy"])
                    ),
                    "source_m10c_outputs": _read_json(m10c_root / "manifest.json")[
                        "outputs"
                    ],
                    "implementation_sha256": {str(code): sha256_file(code)},
                }
            )
        for name in names:
            (target / name).write_text(f"fixture {name}\n", encoding="utf-8")
        summary["outputs"] = {
            name: {
                "sha256": sha256_file(target / name),
                "bytes": (target / name).stat().st_size,
            }
            for name in names
        }
        _write_json(target / "manifest.json", summary)

    def dispatch(
        self, *, spec: dict, stage: str, role: str | None, target: Path
    ) -> None:
        if spec != self.spec or target != self.bundle(stage, role):
            raise AssertionError("Dispatcher was passed foreign fixture arguments")
        if stage == "ledger":
            self.ledger(target=target, role=role)
        elif stage in {"stage07", "m8"}:
            self.stage_manifest(target=target, stage=stage)
        elif stage in {"m10c", "temporal"}:
            self.custom_manifest(target=target, stage=stage)
        else:
            raise AssertionError(f"Unsupported scientific fixture: {stage}")


class FreshPublicationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="probe-receipt-test-")
        self.addCleanup(self.temporary.cleanup)
        self.fixture = ReceiptFixture(Path(self.temporary.name))
        self.dispatch = Mock(side_effect=self.fixture.dispatch)
        module = types.ModuleType("probe.canonical_v2.dag_execution")
        module.dispatch = self.dispatch
        self.modules = patch.dict(sys.modules, {module.__name__: module})
        self.modules.start()
        self.addCleanup(self.modules.stop)

    def execute(
        self, stage: str = "ledger", role: str | None = "START", recipe: str = RECIPE
    ) -> dict:
        return publication.execute_node(
            spec=self.fixture.spec, stage=stage, role=role, recipe_sha256=recipe
        )

    def test_complete_receipt_checks_every_payload_byte_and_reuses(self) -> None:
        receipt = self.execute()
        bundle = self.fixture.bundle()
        expected = {
            name: {
                "sha256": sha256_file(bundle / name),
                "size_bytes": (bundle / name).stat().st_size,
            }
            for name in (LEDGER_PAYLOAD, LEDGER_ANCHOR)
        }
        self.assertEqual(receipt["payload"], expected)
        self.assertEqual(receipt["recipe_sha256"], RECIPE)
        self.assertEqual(receipt["status"], "PAYLOAD_AND_INPUT_INTEGRITY_VERIFIED")
        self.assertEqual(_read_json(self.fixture.marker()), receipt)
        self.assertTrue(self.fixture.intent().is_file())
        self.assertEqual(self.execute(), receipt)
        self.dispatch.assert_called_once()
        self.assertNotIn(self.fixture.marker().name, {p.name for p in bundle.iterdir()})

    def test_same_length_non_anchor_corruption_blocks_reuse(self) -> None:
        self.execute()
        marker_bytes = self.fixture.marker().read_bytes()
        _corrupt_same_length(self.fixture.bundle() / LEDGER_PAYLOAD)
        with self.assertRaisesRegex(ValueError, "SHA256"):
            self.execute()
        self.assertEqual(self.fixture.marker().read_bytes(), marker_bytes)
        self.dispatch.assert_called_once()

    def test_missing_payload_blocks_without_rebuilding_publication(self) -> None:
        self.execute()
        (self.fixture.bundle() / LEDGER_PAYLOAD).unlink()
        with self.assertRaisesRegex(ValueError, "payload"):
            self.execute()
        self.dispatch.assert_called_once()
        self.assertTrue(self.fixture.marker().is_file())

    def test_extra_payload_blocks_without_removing_extra_file(self) -> None:
        self.execute()
        extra = self.fixture.bundle() / "unmanifested.tsv"
        extra.write_text("unmanifested bytes\n", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "payload"):
            self.execute()
        self.assertEqual(extra.read_text(encoding="utf-8"), "unmanifested bytes\n")
        self.dispatch.assert_called_once()

    def test_foreign_recipe_blocks_without_overwriting_receipt(self) -> None:
        self.execute()
        old = self.fixture.marker().read_bytes()
        with self.assertRaisesRegex(ValueError, "intent|provenance"):
            self.execute(recipe=OTHER_RECIPE)
        self.assertEqual(self.fixture.marker().read_bytes(), old)
        self.dispatch.assert_called_once()

    def test_invalid_recipe_blocks_before_scientific_dispatch(self) -> None:
        with self.assertRaises(ValueError):
            self.execute(recipe="not-a-sha256")
        self.dispatch.assert_not_called()
        self.assertFalse(self.fixture.bundle().exists())
        self.assertFalse(self.fixture.marker().exists())

    def test_same_length_scientific_source_corruption_blocks(self) -> None:
        self.execute()
        source = Path(self.fixture.spec["per_release"]["START"]["full_exact_pairs"])
        _corrupt_same_length(source)
        with self.assertRaisesRegex(ValueError, "inputs|intent"):
            self.execute()
        self.dispatch.assert_called_once()

    def test_foreign_common_source_is_bound_even_without_native_pin(self) -> None:
        self.execute()
        _corrupt_same_length(Path(self.fixture.spec["inputs"]["targets_input"]))
        with self.assertRaisesRegex(ValueError, "intent"):
            self.execute()
        self.dispatch.assert_called_once()

    def test_source_change_during_producer_blocks_receipt(self) -> None:
        def producer(**kwargs: object) -> None:
            self.fixture.dispatch(**kwargs)
            _corrupt_same_length(Path(self.fixture.spec["inputs"]["targets_input"]))

        self.dispatch.side_effect = producer
        with self.assertRaisesRegex(ValueError, "during scientific execution"):
            self.execute()
        self.assertTrue(self.fixture.bundle().is_dir())
        self.assertTrue(self.fixture.intent().is_file())
        self.assertFalse(self.fixture.marker().exists())

    def test_unattested_orphan_is_never_adopted(self) -> None:
        self.fixture.ledger(target=self.fixture.bundle(), role="START")
        before = (self.fixture.bundle() / LEDGER_ANCHOR).read_bytes()
        with self.assertRaisesRegex(ValueError, "orphan.*intent"):
            self.execute()
        self.dispatch.assert_not_called()
        self.assertFalse(self.fixture.marker().exists())
        self.assertFalse(self.fixture.intent().exists())
        self.assertEqual((self.fixture.bundle() / LEDGER_ANCHOR).read_bytes(), before)

    def test_invalid_payload_does_not_receive_completion_receipt(self) -> None:
        def producer(**kwargs: object) -> None:
            self.fixture.dispatch(**kwargs)
            (self.fixture.bundle() / LEDGER_PAYLOAD).unlink()

        self.dispatch.side_effect = producer
        with self.assertRaises(ValueError):
            self.execute()
        self.assertTrue(self.fixture.intent().is_file())
        self.assertFalse(self.fixture.marker().exists())
        with self.assertRaises(ValueError):
            self.execute()
        self.dispatch.assert_called_once()

    def test_foreign_release_and_summary_schema_never_receive_receipt(self) -> None:
        for field, value in (("schema", "old-ledger-v0"), ("release", "2024_01")):
            with self.subTest(field=field):
                case = ReceiptFixture(Path(self.temporary.name) / field)
                dispatch = Mock(side_effect=case.dispatch)

                def producer(
                    *,
                    _case: ReceiptFixture = case,
                    _field: str = field,
                    _value: str = value,
                    **kwargs: object,
                ) -> None:
                    _case.dispatch(**kwargs)
                    path = _case.bundle() / LEDGER_ANCHOR
                    info = _read_json(path)
                    info[_field] = _value
                    _write_json(path, info)

                dispatch.side_effect = producer
                with (
                    patch.object(
                        sys.modules["probe.canonical_v2.dag_execution"],
                        "dispatch",
                        dispatch,
                    ),
                    self.assertRaises(ValueError),
                ):
                    publication.execute_node(
                        spec=case.spec,
                        stage="ledger",
                        role="START",
                        recipe_sha256=RECIPE,
                    )
                self.assertFalse(case.marker().exists())

    def test_malformed_or_foreign_receipt_is_not_replaced(self) -> None:
        self.execute()
        marker = self.fixture.marker()
        original = marker.read_bytes()
        for malformed in (b"{", b"[]\n", b'{"schema":"foreign"}\n'):
            with self.subTest(malformed=malformed):
                marker.write_bytes(malformed)
                with self.assertRaises(ValueError):
                    self.execute()
                self.assertEqual(marker.read_bytes(), malformed)
        marker.write_bytes(original)
        self.execute()
        self.dispatch.assert_called_once()

    def test_symlink_payload_is_preserved_and_rejected(self) -> None:
        self.execute()
        payload = self.fixture.bundle() / LEDGER_PAYLOAD
        outside = Path(self.temporary.name) / "outside.tsv"
        payload.rename(outside)
        payload.symlink_to(outside)
        with self.assertRaisesRegex(ValueError, "Non-regular"):
            self.execute()
        self.assertTrue(payload.is_symlink())
        self.assertTrue(outside.is_file())

    def test_symlink_receipt_is_preserved_and_rejected(self) -> None:
        self.execute()
        marker = self.fixture.marker()
        outside = Path(self.temporary.name) / "outside-receipt.json"
        marker.rename(outside)
        marker.symlink_to(outside)
        with self.assertRaisesRegex(ValueError, "unsafe JSON"):
            self.execute()
        self.assertTrue(marker.is_symlink())
        self.assertTrue(outside.is_file())

    def test_symlink_publication_and_lock_are_not_removed(self) -> None:
        bundle = self.fixture.bundle()
        bundle.parent.mkdir(parents=True)
        outside = Path(self.temporary.name) / "outside-bundle"
        outside.mkdir()
        bundle.symlink_to(outside, target_is_directory=True)
        with self.assertRaises(ValueError):
            self.execute()
        self.assertTrue(bundle.is_symlink())
        self.assertTrue(Path(f"{bundle}.fresh.lock").is_file())
        self.dispatch.assert_not_called()

    def test_native_stage07_and_m8_manifests_receive_deep_receipts(self) -> None:
        for stage, expected_count in (("stage07", 5), ("m8", 13)):
            with self.subTest(stage=stage):
                result = self.execute(stage, None)
                self.assertEqual(len(result["payload"]), expected_count)
                payload = self.fixture.bundle(stage, None) / (
                    "07_complete_decisions.tsv"
                    if stage == "stage07"
                    else "08_end_donor_coverage.tsv"
                )
                _corrupt_same_length(payload)
                with self.assertRaisesRegex(ValueError, "SHA256"):
                    self.execute(stage, None)

    def test_native_m10c_and_temporal_custom_manifests(self) -> None:
        for stage, expected_count in (("m10c", 3), ("temporal", 8)):
            with self.subTest(stage=stage):
                result = self.execute(stage, None)
                self.assertEqual(len(result["payload"]), expected_count)
                self.assertEqual(self.execute(stage, None), result)

    def test_custom_manifest_same_length_corruption_blocks(self) -> None:
        self.execute("temporal", None)
        _corrupt_same_length(self.fixture.bundle("temporal", None) / "term_audit.tsv")
        with self.assertRaisesRegex(ValueError, "payload differs"):
            self.execute("temporal", None)

    def test_foreign_custom_schema_and_release_cannot_publish_receipt(self) -> None:
        for field, value in (
            ("schema_version", "foreign-v0"),
            ("end_release", "2025_01"),
        ):
            with self.subTest(field=field):
                case = ReceiptFixture(Path(self.temporary.name) / field)

                def producer(
                    *,
                    _case: ReceiptFixture = case,
                    _field: str = field,
                    _value: str = value,
                    **kwargs: object,
                ) -> None:
                    _case.dispatch(**kwargs)
                    path = _case.bundle("temporal", None) / "manifest.json"
                    info = _read_json(path)
                    info[_field] = _value
                    _write_json(path, info)

                with (
                    patch.object(
                        sys.modules["probe.canonical_v2.dag_execution"],
                        "dispatch",
                        producer,
                    ),
                    self.assertRaises(ValueError),
                ):
                    publication.execute_node(
                        spec=case.spec,
                        stage="temporal",
                        role=None,
                        recipe_sha256=RECIPE,
                    )
                self.assertFalse(case.marker("temporal", None).exists())

    def test_self_consistent_incomplete_custom_manifest_is_rejected(self) -> None:
        for stage, missing in (
            ("m10c", "propagated_terms.tsv"),
            ("temporal", "benchmark_terms.tsv"),
        ):
            with self.subTest(stage=stage):
                case = ReceiptFixture(Path(self.temporary.name) / stage)

                def producer(
                    *,
                    _case: ReceiptFixture = case,
                    _stage: str = stage,
                    _missing: str = missing,
                    **kwargs: object,
                ) -> None:
                    _case.dispatch(**kwargs)
                    bundle = _case.bundle(_stage, None)
                    info = _read_json(bundle / "manifest.json")
                    info["outputs"].pop(_missing)
                    (bundle / _missing).unlink()
                    _write_json(bundle / "manifest.json", info)

                with (
                    patch.object(
                        sys.modules["probe.canonical_v2.dag_execution"],
                        "dispatch",
                        producer,
                    ),
                    self.assertRaises(ValueError),
                ):
                    publication.execute_node(
                        spec=case.spec,
                        stage=stage,
                        role=None,
                        recipe_sha256=RECIPE,
                    )
                self.assertFalse(case.marker(stage, None).exists())

    def test_stage07_foreign_donor_policy_is_rejected(self) -> None:
        def producer(**kwargs: object) -> None:
            self.fixture.dispatch(**kwargs)
            path = self.fixture.bundle("stage07", None) / "manifest.json"
            info = _read_json(path)
            info["parameters"]["policies"]["resolver"]["strain_policy"] = (
                "same_species_exact"
            )
            _write_json(path, info)

        self.dispatch.side_effect = producer
        with self.assertRaises(ValueError):
            self.execute("stage07", None)
        self.assertFalse(self.fixture.marker("stage07", None).exists())

    def test_m8_foreign_collection_schema_is_rejected(self) -> None:
        def producer(**kwargs: object) -> None:
            self.fixture.dispatch(**kwargs)
            path = self.fixture.bundle("m8", None) / "manifest.json"
            info = _read_json(path)
            info["parameters"]["schema_version"] = "foreign-go-stage08-v0"
            _write_json(path, info)

        self.dispatch.side_effect = producer
        with self.assertRaises(ValueError):
            self.execute("m8", None)
        self.assertFalse(self.fixture.marker("m8", None).exists())

    def test_m8_uncertified_annotation_coverage_is_rejected(self) -> None:
        def producer(**kwargs: object) -> None:
            self.fixture.dispatch(**kwargs)
            bundle = self.fixture.bundle("m8", None)
            path = bundle / "08_collection_summary.json"
            info = _read_json(path)
            info["annotation_coverage_certified"] = False
            _write_json(path, info)
            manifest = _read_json(bundle / "manifest.json")
            for artifact in manifest["outputs"]:
                if artifact["path"] == path.name:
                    artifact["sha256"] = sha256_file(path)
                    artifact["size_bytes"] = path.stat().st_size
            _write_json(bundle / "manifest.json", manifest)

        self.dispatch.side_effect = producer
        with self.assertRaises(ValueError):
            self.execute("m8", None)
        self.assertFalse(self.fixture.marker("m8", None).exists())

    def test_native_manifest_foreign_input_with_valid_hash_is_rejected(self) -> None:
        for stage in ("stage07", "m8"):
            with self.subTest(stage=stage):
                case = ReceiptFixture(Path(self.temporary.name) / stage)
                foreign = case.root / "foreign-source.tsv"
                foreign.write_text("foreign but consistent input\n", encoding="utf-8")

                def producer(
                    *,
                    _case: ReceiptFixture = case,
                    _stage: str = stage,
                    _foreign: Path = foreign,
                    **kwargs: object,
                ) -> None:
                    _case.dispatch(**kwargs)
                    path = _case.bundle(_stage, None) / "manifest.json"
                    info = _read_json(path)
                    info["inputs"][0] = {
                        "path": str(_foreign),
                        "size_bytes": _foreign.stat().st_size,
                        "sha256": sha256_file(_foreign),
                    }
                    _write_json(path, info)

                with (
                    patch.object(
                        sys.modules["probe.canonical_v2.dag_execution"],
                        "dispatch",
                        producer,
                    ),
                    self.assertRaisesRegex(ValueError, "input paths"),
                ):
                    publication.execute_node(
                        spec=case.spec,
                        stage=stage,
                        role=None,
                        recipe_sha256=RECIPE,
                    )
                self.assertFalse(case.marker(stage, None).exists())

    def _kill_during_receipt_link(self, *, after_link: bool) -> None:
        if "fork" not in multiprocessing.get_all_start_methods():
            self.skipTest("POSIX fork required for actual SIGKILL lock test")
        context = multiprocessing.get_context("fork")
        incoming, outgoing = context.Pipe(duplex=False)
        marker = self.fixture.marker()

        def child() -> None:
            incoming.close()
            real_link = os.link

            def interrupted_link(
                source: object, target: object, **kwargs: object
            ) -> None:
                if Path(target) != marker:
                    real_link(source, target, **kwargs)
                    return
                if after_link:
                    real_link(source, target, **kwargs)
                outgoing.send(
                    "receipt-ready-after" if after_link else "receipt-ready-before"
                )
                signal.pause()
                raise AssertionError("Child should have been killed")

            try:
                with patch.object(os, "link", interrupted_link):
                    self.execute()
            except BaseException as error:
                outgoing.send(f"child-error:{error!r}")
                raise

        process = context.Process(target=child)
        process.start()
        outgoing.close()
        try:
            self.assertTrue(
                incoming.poll(15), "Child failed to reach receipt publication"
            )
            message = incoming.recv()
            self.assertTrue(message.startswith("receipt-ready-"), message)
            self.assertEqual(marker.exists(), after_link)
            self.assertTrue(self.fixture.bundle().is_dir())
            self.assertTrue(self.fixture.intent().is_file())
            lock = Path(f"{self.fixture.bundle()}.fresh.lock")
            inode = lock.stat().st_ino
            with self.assertRaises(BlockingIOError):
                self.execute()
            os.kill(process.pid, signal.SIGKILL)
            process.join(10)
            self.assertEqual(process.exitcode, -signal.SIGKILL)
            result = self.execute()
            self.assertEqual(_read_json(marker), result)
            self.assertEqual(lock.stat().st_ino, inode)
            self.dispatch.assert_not_called()
            self.assertEqual(self.execute(), result)
        finally:
            if process.is_alive():
                process.kill()
                process.join(10)
            incoming.close()

    def test_sigkill_before_receipt_link_releases_lock_and_recovers(self) -> None:
        self._kill_during_receipt_link(after_link=False)

    def test_sigkill_after_receipt_link_releases_lock_and_reuses(self) -> None:
        self._kill_during_receipt_link(after_link=True)


if __name__ == "__main__":
    unittest.main()
