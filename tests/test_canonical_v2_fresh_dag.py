"""Control-contract tests; real scientific execution is a separate required gate."""

from __future__ import annotations

import json
import re
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from canonical_v2_fresh_raw_fixture import create_raw_fixture

from probe.canonical_v2.dag_contract import (
    destination,
    discovery_fingerprint,
    policy_fingerprint,
    read_dag_spec,
)
from probe.canonical_v2.fresh_dag import (
    COMMON_NODES,
    RAW_COMMON_STAGES,
    RAW_MEMBERS,
    RAW_ROLE_STAGES,
    ROLE_NODES,
    all_raw_manifests,
    all_raw_outputs,
    canonical_sources,
    content_identity,
    node_dependencies,
    plan_locations,
    publish_json,
    read_recipe,
    validate_completion_identity,
    validate_existing_upstreams,
    validate_requested_node,
)
from probe.canonical_v2.fresh_publication import receipt_path
from probe.workflow.artifacts import atomic_stage_bundle, sha256_file


class FreshDagContractTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="probe-fresh-contract-")
        self.root = Path(self.temporary.name)
        self.repo = self.root / "source"
        self.repo.mkdir()
        self.path = create_raw_fixture(
            repo=self.repo,
            run=self.root / "run",
            dp_binary=self.repo / "bounded-dp",
        )
        self.recipe = read_recipe(self.path, expected_repo=self.repo)

    def tearDown(self):
        self.temporary.cleanup()

    def plans(self):
        return {
            kind: {
                "paths": {
                    role: str(
                        self.recipe.run / "work/pipeline-v2" / role / kind / ("a" * 64)
                    )
                    for role in ("START", "END")
                }
            }
            for kind in ("species", "full_exact", "union")
        }

    def spec(self):
        return canonical_sources(self.recipe, self.plans())

    def test_recipe_parsing_never_requires_or_creates_upstreams(self):
        self.assertEqual(
            self.recipe.pipeline["releases"]["END"]["release_id"], "2026_03"
        )
        for name in ("work", "results", "plans", "canonical", "logs"):
            self.assertFalse((self.recipe.run / name).exists(), name)
        self.assertEqual(len(all_raw_manifests(self.recipe)), 34)

    def test_root_dag_tracks_all_upstream_members_and_16_receipts(self):
        raw = all_raw_outputs(self.recipe)
        self.assertEqual(len(raw), len(set(raw)))
        self.assertEqual(
            len(raw),
            sum(
                len(self.recipe.outputs(stage))
                for stage in ("01", "01B0", "04C", "04D1", "05A", "05B")
            )
            + sum(
                len(self.recipe.outputs(stage, role))
                for role in ("START", "END")
                for stage in (
                    "01A_FULL",
                    "01B1",
                    "01C",
                    "02A",
                    "02B",
                    "03",
                    "04A",
                    "04B",
                    "04D0a",
                    "04D0b",
                    "04D2",
                    "04E",
                    "05R0",
                    "05R1",
                )
            ),
        )
        workflow = (
            Path(__file__).resolve().parents[1] / "workflow/canonical_v2_fresh.smk"
        ).read_text(encoding="utf-8")
        self.assertIn("raw=all_raw_outputs(FRESH)", workflow)
        self.assertIn("canonical=canonical_completion_receipts", workflow)
        self.assertEqual(workflow.count("FRESH.outputs("), 20)
        self.assertNotIn("protected(FRESH.outputs(", workflow)
        self.assertIn("len(nodes) != 16", workflow)
        self.assertNotIn("validate_existing_upstreams(FRESH, deep=False)", workflow)
        self.assertNotIn("onstart:", workflow)

    def test_complete_raw_payloads_are_declared_with_manifest_first(self):
        for stage in RAW_MEMBERS:
            role = (
                None
                if stage in {"01", "01B0", "04C", "04D1", "05A", "05B"}
                else "START"
            )
            outputs = self.recipe.outputs(stage, role)
            self.assertEqual(Path(outputs[0]).name, "manifest.json")
            self.assertEqual(len(outputs), len(set(outputs)))
            self.assertGreater(len(outputs), 1)
        self.assertIn(
            "full_ox_taxonomy.tsv",
            [Path(path).name for path in self.recipe.outputs("04D0b", "END")],
        )
        self.assertIn(
            "goa.normalized.gaf",
            [Path(path).name for path in self.recipe.outputs("01B1", "START")],
        )

    def test_resolved_spec_keeps_release_local_go_and_full_taxonomy(self):
        spec = self.spec()
        file = self.recipe.run / "test-spec.json"
        publish_json(file, spec)
        self.assertEqual(read_dag_spec(file), spec)
        self.assertNotEqual(
            spec["per_release"]["START"]["goa_bundle"],
            spec["per_release"]["END"]["goa_bundle"],
        )
        self.assertTrue(
            spec["per_release"]["START"]["ox_taxonomy"].endswith(
                "START/04D0b/full_ox_taxonomy.tsv"
            )
        )
        self.assertTrue(spec["inputs"]["targets"].endswith("common/01/01_targets.tsv"))
        for source in spec["per_release"]["END"].values():
            self.assertTrue(Path(source).is_relative_to(self.recipe.run))

    def test_canonical_dependency_table_has_all_16_nodes_and_no_cycle(self):
        spec = self.spec()
        nodes = [(stage, role) for stage in ROLE_NODES for role in ("START", "END")]
        nodes += [(stage, None) for stage in COMMON_NODES]
        markers = {
            str(receipt_path(destination(spec, stage, role))): (stage, role)
            for stage, role in nodes
        }
        graph = {
            node: [
                markers[path]
                for path in node_dependencies(spec, *node)
                if path in markers
            ]
            for node in nodes
        }
        completed = set()
        pending = set(nodes)
        while pending:
            ready = {node for node in pending if set(graph[node]) <= completed}
            self.assertTrue(ready, f"cycle: {pending}")
            pending -= ready
            completed |= ready
        self.assertEqual(len(completed), 16)
        self.assertEqual(
            set(graph[("lookup", "START")]), {("exact", "START"), ("exact", "END")}
        )
        self.assertEqual(
            set(graph[("stage07", None)]), {("workload", None), ("alignment", None)}
        )
        self.assertIn(("m10c", None), graph[("temporal", None)])

    def test_foreign_fingerprint_is_rejected_before_dispatch(self):
        spec = self.spec()
        correct = str(receipt_path(destination(spec, "alignment")))
        validate_requested_node(spec, correct, "alignment", None)
        with self.assertRaisesRegex(ValueError, "fingerprint"):
            validate_requested_node(spec, correct + "wrong", "alignment", None)
        with self.assertRaisesRegex(ValueError, "role mismatch"):
            node_dependencies(spec, "alignment", "START")

    def test_scheduling_plan_does_not_read_unbuilt_biological_sources(self):
        digest = "b" * 64
        metadata = {
            "repo": str(self.repo),
            "run": str(self.recipe.run),
            "key": digest,
            "paths": {
                role: str(
                    self.recipe.run
                    / "work/pipeline-v2"
                    / role
                    / "03_species_wide_variants"
                    / digest
                )
                for role in ("START", "END")
            },
        }
        path = self.recipe.plan_path("species")
        publish_json(path, metadata)
        self.assertEqual(plan_locations(self.recipe, "species", path), metadata)
        self.assertFalse((self.recipe.run / "work").exists())
        metadata["paths"]["START"] = "/old/published/result"
        path.write_text(json.dumps(metadata))
        with self.assertRaisesRegex(ValueError, "escapes"):
            plan_locations(self.recipe, "species", path)

    def test_unknown_config_keys_are_rejected(self):
        data = json.loads(self.path.read_text())
        data["global_targets"] = True
        self.path.write_text(json.dumps(data))
        with self.assertRaisesRegex(ValueError, "Unsupported or incomplete"):
            read_recipe(self.path)

    def test_changed_bytes_at_same_path_change_fresh_canonical_namespace(self):
        for name in self.recipe.data["tools"].values():
            path = Path(name)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("fixed scientific executable\n")
        first, _ = content_identity(self.recipe, {"upstream/manifest.json": "a" * 64})
        tool = Path(self.recipe.data["tools"]["full_dp_binary"])
        tool.write_text("changed scientific executable\n")
        second, _ = content_identity(self.recipe, {"upstream/manifest.json": "a" * 64})
        self.assertNotEqual(first, second)
        left = {**self.spec(), "content_identity_sha256": first}
        right = {**self.spec(), "content_identity_sha256": second}
        self.assertNotEqual(
            destination(left, "alignment"), destination(right, "alignment")
        )
        self.assertNotEqual(
            destination(left, "temporal"), destination(right, "temporal")
        )
        path = self.recipe.run / "content-spec.json"
        publish_json(path, left)
        self.assertEqual(read_dag_spec(path), left)
        left["content_identity_sha256"] = "invalid"
        path.write_text(json.dumps(left))
        with self.assertRaisesRegex(ValueError, "content identity"):
            read_dag_spec(path)

    def test_legacy_v2_fingerprints_are_unchanged_without_optional_identity(self):
        # These two constants were independently obtained from pristine 7250810
        # for the same path-normalized legacy spec, before adding this field.
        spec = json.loads(
            json.dumps(self.spec()).replace(str(self.root), "/probe-v2-fixed")
        )
        self.assertEqual(
            discovery_fingerprint(spec),
            "ccc76611d9be499a5633dd65a42efb7099751feae2f6ba9f4b88e1c9c18a3471",
        )
        self.assertEqual(
            policy_fingerprint(spec),
            "d11f06a0703751115dddb263e48d36101c958301429df789e8270b6fb207552f",
        )

    def test_completion_rejects_changed_upstream_or_direct_tool_identity(self):
        # These are control-only bundles: the real end-to-end gate must run
        # actual producers. Identity checks require valid immutable manifests.
        directories = {
            "species": "03_species_wide_variants",
            "full_exact": "03_full_exact_variants",
            "union": "03_discovery_union_variants",
        }
        plans = {}
        for kind, dirname in directories.items():
            plans[kind] = {
                "repo": str(self.repo),
                "run": str(self.recipe.run),
                "key": "b" * 64,
                "paths": {
                    role: str(
                        self.recipe.run
                        / "work/pipeline-v2"
                        / role
                        / dirname
                        / ("b" * 64)
                    )
                    for role in ("START", "END")
                },
            }
        plans["union"].update(
            {
                "species_key": "b" * 64,
                "full_key": "b" * 64,
                "species_paths": plans["species"]["paths"],
                "full_paths": plans["full_exact"]["paths"],
            }
        )

        def make_control_bundle(root, stage, role, members):
            release_id = (
                self.recipe.pipeline["releases"][role]["release_id"]
                if role is not None
                else None
            )
            with atomic_stage_bundle(
                root,
                stage=stage,
                role=role,
                release_id=release_id,
                parameters={"purpose": "identity-only-control-test"},
                ontology=None,
                implementation={
                    "name": "synthetic-identity-fixture",
                    "version": "1",
                },
                inputs=(),
            ) as staging:
                for member in members:
                    (staging / member).write_bytes(b"control identity bytes\n")

        for stage in RAW_COMMON_STAGES:
            make_control_bundle(
                self.recipe.bundle(stage), stage, None, RAW_MEMBERS[stage]
            )
        for role in ("START", "END"):
            for stage in RAW_ROLE_STAGES:
                make_control_bundle(
                    self.recipe.bundle(stage, role),
                    stage,
                    role,
                    RAW_MEMBERS[stage],
                )
        for kind, plan in plans.items():
            publish_json(self.recipe.plan_path(kind), plan)
            stage = {
                "species": "03",
                "full_exact": "03_FULL_EXACT",
                "union": "03_UNION",
            }[kind]
            for role in ("START", "END"):
                make_control_bundle(
                    Path(plan["paths"][role]),
                    stage,
                    role,
                    ("identity-control-only.tsv",),
                )
        names = all_raw_manifests(self.recipe)
        for plan in plans.values():
            names.extend(
                str(Path(root) / "manifest.json") for root in plan["paths"].values()
            )
        for name in self.recipe.data["tools"].values():
            path = Path(name)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"control tool identity bytes\n")

        inventory = {name: sha256_file(Path(name)) for name in names}
        identity, evidence = content_identity(self.recipe, inventory)
        spec = {
            **canonical_sources(self.recipe, plans),
            "content_identity_sha256": identity,
        }
        publish_json(self.recipe.plan_path("canonical_spec"), spec)
        publish_json(
            self.recipe.run / "plans/canonical_spec.provenance.json",
            {
                "schema": "probe-canonical-v2-fresh-resolved-v1",
                "recipe_sha256": self.recipe.sha256,
                "identity_sha256": self.recipe.data["identity_sha256"],
                "spec_sha256": sha256_file(self.recipe.plan_path("canonical_spec")),
                "releases": spec["releases"],
                "upstream_manifests_sha256": inventory,
                "content_identity": evidence,
            },
        )
        self.assertEqual(validate_completion_identity(self.recipe, spec), evidence)

        # Direct tool bytes changed, with path unchanged: stale fingerprint.
        tool = Path(self.recipe.data["tools"]["full_dp_binary"])
        original_tool = tool.read_bytes()
        try:
            tool.write_bytes(bytes([original_tool[0] ^ 1]) + original_tool[1:])
            with self.assertRaisesRegex(ValueError, "stale"):
                validate_completion_identity(self.recipe, spec)
        finally:
            tool.write_bytes(original_tool)

        # Manifest content changes but stays valid JSON and checksum-complete.
        # The manifest inventory must still reject its changed identity.
        manifest = Path(names[0])
        original_manifest = manifest.read_bytes()
        try:
            manifest.write_bytes(original_manifest + b"\n")
            with self.assertRaisesRegex(ValueError, "stale"):
                validate_completion_identity(self.recipe, spec)
        finally:
            manifest.write_bytes(original_manifest)

        # A same-size payload corruption is caught by DEEP bundle validation,
        # even when the manifest and all recorded identity hashes are intact.
        payload = self.recipe.bundle("01") / RAW_MEMBERS["01"][0]
        original_payload = payload.read_bytes()
        try:
            payload.write_bytes(bytes([original_payload[0] ^ 1]) + original_payload[1:])
            with self.assertRaisesRegex(ValueError, "mismatched SHA256"):
                validate_completion_identity(self.recipe, spec)
        finally:
            payload.write_bytes(original_payload)
        self.assertEqual(validate_completion_identity(self.recipe, spec), evidence)

    def test_resume_certifies_bytes_and_blocks_missing_published_payload(self):
        bundle = self.recipe.bundle("03", "START")
        with atomic_stage_bundle(
            bundle,
            stage="03",
            role="START",
            release_id="2023_01",
            parameters={},
            ontology=None,
            implementation={"name": "checksum-fixture", "version": "1"},
            inputs=(),
        ) as stage:
            for member in RAW_MEMBERS["03"]:
                (stage / member).write_bytes(b"verified fixture payload\n")
        self.assertEqual(
            len(validate_existing_upstreams(self.recipe)["complete_verified"]), 1
        )
        member = bundle / RAW_MEMBERS["03"][0]
        before = member.read_bytes()
        member.unlink()
        with self.assertRaisesRegex(ValueError, "Incomplete immutable"):
            validate_existing_upstreams(self.recipe)
        self.assertFalse(member.exists())
        member.write_bytes(bytes([before[0] ^ 1]) + before[1:])
        with self.assertRaises(ValueError):
            validate_existing_upstreams(self.recipe)

    def test_parse_time_guard_rejects_missing_raw_without_deep_hash(self):
        bundle = self.recipe.bundle("03", "START")
        with atomic_stage_bundle(
            bundle,
            stage="03",
            role="START",
            release_id="2023_01",
            parameters={},
            ontology=None,
            implementation={"name": "fixture", "version": "1"},
            inputs=(),
        ) as staging:
            for member in RAW_MEMBERS["03"]:
                (staging / member).write_bytes(b"immutable fixture\n")
        with (
            patch(
                "probe.canonical_v2.fresh_dag.sha256_file",
                side_effect=AssertionError("parse guard hashed a payload"),
            ),
            patch(
                "probe.canonical_v2.fresh_dag.validate_bundle",
                side_effect=AssertionError("parse guard ran deep validation"),
            ),
        ):
            result = validate_existing_upstreams(self.recipe, deep=False)
            self.assertEqual(len(result["present_checked"]), 1)
            self.assertEqual(result["complete_verified"], {})
            missing = bundle / RAW_MEMBERS["03"][1]
            saved = missing.read_bytes()
            missing.unlink()
            with self.assertRaisesRegex(
                ValueError, "Incomplete immutable fresh publication"
            ):
                validate_existing_upstreams(self.recipe, deep=False)
            self.assertFalse(missing.exists())
            self.assertTrue((bundle / "manifest.json").is_file())
            missing.write_bytes(saved)
        self.assertEqual(
            len(validate_existing_upstreams(self.recipe)["complete_verified"]),
            1,
        )

    def test_missing_manifest_with_surviving_payload_blocks_reexecution(self):
        bundle = self.recipe.bundle("03", "START")
        bundle.mkdir(parents=True)
        payload = bundle / "03_primary_candidates.pairs.tsv"
        payload.write_bytes(b"published data may not be overwritten")
        with self.assertRaisesRegex(ValueError, "missing its manifest"):
            validate_existing_upstreams(self.recipe)
        self.assertEqual(payload.read_bytes(), b"published data may not be overwritten")

    def test_tools_from_old_checkout_are_rejected(self):
        data = json.loads(self.path.read_text())
        data["tools"]["collection_cli"] = str(self.root / "old-source" / "collect.py")
        self.path.write_text(json.dumps(data))
        with self.assertRaisesRegex(ValueError, "source snapshot"):
            read_recipe(self.path)

    def test_control_publication_is_atomic_and_immutable(self):
        path = self.recipe.run / "plans/test.json"
        publish_json(path, {"value": "first"})
        before = path.stat().st_mtime_ns
        publish_json(path, {"value": "first"})
        self.assertEqual(before, path.stat().st_mtime_ns)
        with self.assertRaises(FileExistsError):
            publish_json(path, {"value": "second"})
        self.assertEqual(json.loads(path.read_text()), {"value": "first"})
        self.assertEqual([child.name for child in path.parent.iterdir()], ["test.json"])

    def test_discovery_logs_match_all_output_wildcards(self):
        source = (
            Path(__file__).resolve().parents[1] / "workflow/canonical_v2_fresh.smk"
        ).read_text(encoding="utf-8")
        for name in (
            "fresh_species_scan",
            "fresh_full_exact_scan",
            "fresh_discovery_union",
        ):
            with self.subTest(rule=name):
                definition = source.split(f"rule {name}:\n", 1)[1]
                outputs = definition.split("    output:\n", 1)[1].split(
                    "    log:\n", 1
                )[0]
                logs = definition.split("    log:\n", 1)[1].split("    params:\n", 1)[0]
                patterns = [
                    set(re.findall(r"\{([a-z_][a-z_0-9]*)\}", line))
                    for line in outputs.splitlines()
                    if "FRESH.run /" in line
                ]
                log_patterns = [
                    set(re.findall(r"\{([a-z_][a-z_0-9]*)\}", line))
                    for line in logs.splitlines()
                    if "FRESH.run /" in line
                ]
                expected = {"role", "discovery_key"}
                self.assertGreaterEqual(len(patterns), 3)
                self.assertEqual(log_patterns, [expected])
                for actual in patterns:
                    self.assertEqual(actual, expected)


if __name__ == "__main__":
    unittest.main()
