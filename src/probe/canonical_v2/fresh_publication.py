"""Integrity receipts and restart for fresh canonical scientific publications.

Receipts are outside immutable bundles. A killed producer releases the advisory
lock; a published bundle without a receipt is recovered only after complete
payload and input verification. This module never removes a published bundle.
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
import stat
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from probe.canonical_v2.dag_contract import destination, validate_scientific_contracts
from probe.workflow.artifacts import sha256_file, validate_bundle

RECEIPT_SCHEMA = "probe-canonical-v2-fresh-receipt-v1"
ANCHORS = {
    "exact": "exact_documentary_keys.summary.json",
    "ledger": "candidate_provenance.summary.json",
    "projection": "primary_candidate_facts.summary.json",
    "lookup": "documentary_nonexact_matches.summary.json",
    "handoff": "documentary_handoff.summary.json",
    "b3": "canonical_stage05_06a.summary.json",
    "workload": "2C_alignment_preview.summary.json",
    "alignment": "canonical_alignment_execution.summary.json",
    "stage07": "manifest.json",
    "m8": "manifest.json",
    "m10c": "manifest.json",
    "temporal": "manifest.json",
}
SUMMARY_SCHEMAS = {
    "exact": "probe-canonical-v2-exact-documentary-register-v2",
    "ledger": "probe-canonical-v2-candidate-ledger-v1",
    "projection": "probe-canonical-v2-primary-facts-v1",
    "lookup": "probe-canonical-v2-documentary-lookup-v3",
    "handoff": "probe-canonical-v2-documentary-handoff-v1",
    "b3": "probe-canonical-v2-stage05-06a-v1",
    "workload": "stage2c-alignment-preview-v1",
    "alignment": "probe-canonical-v2-alignment-execution-v3",
}
_DIGEST = re.compile(r"[0-9a-f]{64}")
REQUIRED_OUTPUTS = {
    "stage07": {
        "07_accepted_associations.tsv",
        "07_selection_metadata.json",
        "07_complete_decisions.tsv",
        "07_canonical_scientific_summary.json",
    },
    "m8": {
        "08_collection_summary.json",
        "stage_outcome.json",
        *{
            f"08_{role}_{suffix}"
            for role in ("start", "end")
            for suffix in (
                "source_assertions.tsv",
                "annotation_links.tsv",
                "donor_coverage.tsv",
                "evidence_inventory.tsv",
                "scan_summary.json",
            )
        },
    },
    "m10c": {"direct_provenance.sqlite3", "propagated_terms.tsv"},
    "temporal": {
        "benchmark_terms.tsv",
        "target_aspects.tsv",
        "term_audit.tsv",
        "negative_terms.tsv",
        "temporal_events.tsv",
        "duplicate_targets.tsv",
        "audit_reasons.json",
    },
}


def receipt_path(bundle: Path | str) -> Path:
    """Return an external workflow receipt; never write inside a bundle."""
    return Path(f"{bundle}.snakemake.done")


def _json(path: Path) -> dict:
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"Missing or unsafe JSON: {path}")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"Expected JSON object: {path}")
    return value


def _canonical_digest(value: dict) -> str:
    encoded = json.dumps(
        value, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _regular_files(bundle: Path) -> dict[str, Path]:
    if bundle.is_symlink() or not bundle.is_dir():
        raise ValueError(f"Missing or unsafe scientific bundle: {bundle}")
    result = {}
    for path in sorted(bundle.rglob("*")):
        mode = path.lstat().st_mode
        if stat.S_ISLNK(mode) or not (stat.S_ISREG(mode) or stat.S_ISDIR(mode)):
            raise ValueError(f"Non-regular scientific payload: {path}")
        if stat.S_ISREG(mode):
            result[path.relative_to(bundle).as_posix()] = path
    return result


def _valid_hashes(value: object) -> dict[str, str]:
    if not isinstance(value, dict) or not value:
        raise ValueError("A complete publication must declare payload hashes")
    for name, digest in value.items():
        if not isinstance(name, str):
            raise ValueError("Scientific payload names must be strings")
        path = Path(name)
        if (
            not isinstance(name, str)
            or not name
            or path.is_absolute()
            or ".." in path.parts
            or path == Path(".")
            or path.as_posix() != name
            or not isinstance(digest, str)
            or not _DIGEST.fullmatch(digest)
        ):
            raise ValueError("Invalid declared scientific payload hash")
    return value


def _custom_payload(bundle: Path, summary: dict, actual: dict) -> None:
    entries = summary.get("outputs")
    if not isinstance(entries, dict) or not entries:
        raise ValueError("Missing custom manifest output inventory")
    hashes = _valid_hashes(
        {
            name: value.get("sha256") if isinstance(value, dict) else None
            for name, value in entries.items()
        }
    )
    if set(actual) != {*hashes, "manifest.json"}:
        raise ValueError("Custom manifest omits or adds payload files")
    for name, digest in hashes.items():
        info = entries[name]
        if (
            type(info.get("bytes")) is not int
            or info["bytes"] != actual[name].stat().st_size
            or sha256_file(actual[name]) != digest
        ):
            raise ValueError(f"Custom manifest payload differs: {bundle / name}")


def _verify_custom_sources(spec: dict, stage: str, summary: dict) -> None:
    if summary.get("ontology_sha256") != sha256_file(Path(spec["inputs"]["ontology"])):
        raise ValueError("GO interpretation uses a different ontology")
    m8_root = destination(spec, "m8")
    m8 = _json(m8_root / "08_collection_summary.json")
    if m8.get("annotation_coverage_certified") is not True:
        raise ValueError("GO collection has not certified annotation coverage")
    if stage == "m10c":
        if summary.get("schema_version") != ("stage08-m10c-compact-interpretation-v1"):
            raise ValueError("Unsupported M10c publication schema")
        expected = {}
        for role in ("START", "END"):
            source = m8["by_role"][role]
            if source["release"] != spec["releases"][role]:
                raise ValueError("M8 release-local provenance mismatch")
            prefix = f"08_{role.lower()}_"
            expected[role] = {
                "source_sha256": sha256_file(
                    m8_root / f"{prefix}source_assertions.tsv"
                ),
                "links_sha256": sha256_file(m8_root / f"{prefix}annotation_links.tsv"),
                "gaf_source_sha256": source["normalized_gaf_sha256"],
            }
        if summary.get("source_payloads") != expected:
            raise ValueError("M10c source GO assertions differ from fresh M8")
    else:
        m10c_path = destination(spec, "m10c") / "manifest.json"
        if (
            summary.get("schema_version") != "stage08-temporal-eval-skip-neutral-v3"
            or summary.get("mode") != "EXECUTE"
            or summary.get("output_published") is not True
            or summary.get("start_release") != spec["releases"]["START"]
            or summary.get("end_release") != spec["releases"]["END"]
            or summary.get("m10c_manifest_sha256") != sha256_file(m10c_path)
            or summary.get("m8_summary_sha256")
            != sha256_file(m8_root / "08_collection_summary.json")
            or summary.get("policy_sha256")
            != sha256_file(Path(spec["inputs"]["temporal_policy"]))
            or summary.get("source_m10c_outputs") != _json(m10c_path)["outputs"]
        ):
            raise ValueError("Temporal benchmark provenance is incomplete or changed")
        code = summary.get("implementation_sha256")
        if not isinstance(code, dict) or not code:
            raise ValueError("Temporal benchmark has no implementation hashes")
        for name, digest in code.items():
            path = Path(name)
            if (
                not path.is_absolute()
                or path.is_symlink()
                or not path.is_file()
                or sha256_file(path) != digest
            ):
                raise ValueError("Temporal benchmark implementation changed")


def expected_manifest_inputs(spec: dict, stage: str) -> set[str]:
    """Return the concrete input paths emitted by the two native producers."""
    common = spec["inputs"]
    per = spec["per_release"]
    if stage == "stage07":
        workload = destination(spec, "workload")
        completion = destination(spec, "alignment")
        paths = {
            common["targets"],
            common["lineage_index"],
            *{
                str(workload / name)
                for name in (
                    "2C_06A_alignment_jobs.tsv",
                    "2C_05D_START_variant.tsv",
                    "2C_05D_END_variant.tsv",
                    "2C_05A2_variant.tsv",
                    "2C_05C2_variant.tsv",
                    "documentary_evidence_preserved.tsv",
                )
            },
            *{
                str(completion / name)
                for name in (
                    "2C_06C1_canonical_directional_glocal.tsv",
                    "2C_policy_skipped_nonexact.tsv",
                    "2C_06C0_certified_dp_plan.tsv",
                    ANCHORS["alignment"],
                )
            },
            *{
                per[role][name]
                for role in ("START", "END")
                for name in ("taxonomy", "anchors", "lineage")
            },
        }
    elif stage == "m8":
        stage07 = destination(spec, "stage07")
        paths = {
            str(stage07 / name)
            for name in (
                "manifest.json",
                "07_accepted_associations.tsv",
                "07_selection_metadata.json",
            )
        }
        paths.add(str(Path(common["go_policy_bundle"]) / "manifest.json"))
        for role in ("START", "END"):
            bundle = Path(per[role]["goa_bundle"])
            paths.update(
                str(bundle / name) for name in ("manifest.json", "goa.normalized.gaf")
            )
    else:
        raise ValueError("No native manifest input adapter")
    return paths


def _manifest_fasta_hash(path: Path) -> str:
    info = _json(path)
    candidates = [
        item
        for item in info.get("outputs", [])
        if item.get("path") == "01A_full_v2.fasta"
    ]
    if len(candidates) != 1 or not _DIGEST.fullmatch(
        str(candidates[0].get("sha256", ""))
    ):
        raise ValueError("FULL release manifest has no unique sequence hash")
    return candidates[0]["sha256"]


def expected_summary_inputs(spec: dict, stage: str, role: str | None) -> dict:
    """Bind the producer's actual input labels to this fresh DAG's sources."""
    common = spec["inputs"]
    releases = spec["releases"]
    per = spec["per_release"]
    tools = spec["tools"]

    def src(name: str, which: str | None = None) -> Path:
        return Path(common[name] if which is None else per[which][name])

    def anchor(name: str, which: str | None = None) -> Path:
        return destination(spec, name, which) / ANCHORS[name]

    paths: dict[str, Path] = {}
    literal: dict[str, str | None] = {}
    if stage == "exact":
        paths = {
            "discovery": src("unified_discovery", role),
            "targets": src("targets"),
            "full_manifest": src("full_manifest", role),
        }
        literal["full_fasta"] = _manifest_fasta_hash(paths["full_manifest"])
    elif stage == "ledger":
        paths = {
            name: src("unified_discovery" if name == "discovery_union" else name, role)
            for name in (
                "discovery_union",
                "go_positive_pairs",
                "full_exact_pairs",
                "lineage_pairs",
                "rescue_pairs",
            )
        }
    elif stage == "projection":
        paths = {
            name: destination(spec, "ledger", role) / name
            for name in ("candidate_provenance.tsv", ANCHORS["ledger"])
        }
    elif stage == "lookup":
        other = "END" if role == "START" else "START"
        paths = {
            "targets": src("targets"),
            "target_taxonomy": src("taxonomy", role),
            "independent_index": src("lineage_index"),
            "ox_taxonomy": src("ox_taxonomy", role),
            "full_manifest": src("full_manifest", role),
            "exact_register_summary": anchor("exact", role),
            "other_exact_register_summary": anchor("exact", other),
        }
        literal["full_fasta"] = _manifest_fasta_hash(paths["full_manifest"])
    elif stage == "handoff":
        paths = {
            "start_lookup_summary": anchor("lookup", "START"),
            "end_lookup_summary": anchor("lookup", "END"),
        }
    elif stage == "b3":
        paths = {
            "01_targets": src("targets"),
            "05c2_cli": Path(tools["stage05c2_cli"]),
            "06a_cli": Path(tools["stage06a_cli"]),
        }
        for which in ("START", "END"):
            paths.update(
                {
                    f"{which}_b2c": anchor("projection", which),
                    f"{which}_02b": src("taxonomy", which),
                    f"{which}_04b": src("anchors", which),
                    f"{which}_01a_manifest": src("full_manifest", which),
                }
            )
    elif stage == "workload":
        from probe.stage2c_alignment_preview import UNION_OUT

        primary = destination(spec, "workload") / UNION_OUT
        paths = {
            str(primary): primary,
            "b3_summary": anchor("b3"),
            "handoff_summary": anchor("handoff"),
            "targets": src("targets"),
        }
    elif stage == "alignment":
        from probe.canonical_v2.release_workload import EVIDENCE
        from probe.stage2c_alignment_preview import (
            AUDIT_OUT,
            END_STORE,
            JOBS_OUT,
            START_STORE,
            UNION_OUT,
        )

        workload = destination(spec, "workload")
        paths = {
            "workload_summary": anchor("workload"),
            "targets": src("targets"),
            "full_dp_binary": Path(tools["full_dp_binary"]),
            "nonexact_script": Path(tools["nonexact_script"]),
            "finalize_script": Path(tools["finalize_script"]),
            "START": src("taxonomy", "START"),
            "END": src("taxonomy", "END"),
            **{
                f"workload_{name}": workload / name
                for name in (JOBS_OUT, START_STORE, END_STORE, UNION_OUT, AUDIT_OUT)
            },
            "documentary_evidence": workload / EVIDENCE,
            "lineage_index": src("lineage_index"),
            **{
                f"lineage_{releases[which]}": src("lineage", which)
                for which in ("START", "END")
            },
        }
        if "large_dp" in spec:
            paths["linear_dp_binary"] = Path(spec["large_dp"]["binary"])
    else:
        raise ValueError(f"No summary input adapter for stage {stage}")
    for path in paths.values():
        if path.is_symlink() or not path.is_file():
            raise ValueError(f"Missing or unsafe scientific input: {path}")
    return {**{name: sha256_file(path) for name, path in paths.items()}, **literal}


def validate_publication(*, spec: dict, stage: str, role: str | None) -> dict:
    """Check exact payload membership, every byte hash, release and inputs."""
    validate_scientific_contracts(spec)
    bundle = destination(spec, stage, role)
    anchor_name = ANCHORS[stage]
    actual = _regular_files(bundle)
    summary = _json(bundle / anchor_name)
    if stage in REQUIRED_OUTPUTS and set(actual) != {
        *REQUIRED_OUTPUTS[stage],
        anchor_name,
    }:
        raise ValueError(f"Native scientific output set is incomplete: {stage}")
    if stage in {"m10c", "temporal"}:
        _custom_payload(bundle, summary, actual)
        _verify_custom_sources(spec, stage, summary)
    elif anchor_name == "manifest.json":
        manifest = validate_bundle(bundle, deep=True)
        if stage == "stage07":
            if (
                manifest.stage != "07"
                or manifest.parameters.get("start_release_id")
                != spec["releases"]["START"]
                or manifest.parameters.get("end_release_id") != spec["releases"]["END"]
                or manifest.parameters.get("canonical_v2") is not True
                or manifest.parameters.get("policy_version")
                != ("probe-canonical-v2-stage07-science-v2-literal-fragment")
                or manifest.parameters.get("policies")
                != {
                    "resolver": {"strain_policy": spec["policy"]},
                    "annotation_donor": {
                        name: spec["limits"][name]
                        for name in ("partial_sequence", "documentary_fragment")
                    },
                }
            ):
                raise ValueError("Stage07 publication has foreign provenance")
        elif (
            manifest.stage != "08"
            or manifest.parameters.get("release_ids") != spec["releases"]
            or manifest.parameters.get("schema_version")
            != ("stage08-raw-collection-bundle-v1")
        ):
            raise ValueError("M8 publication has foreign release provenance")
        if stage == "m8":
            collection = _json(bundle / "08_collection_summary.json")
            if (
                collection.get("schema_version") != "stage08-raw-collection-bundle-v1"
                or collection.get("annotation_coverage_certified") is not True
                or {
                    role: info.get("release")
                    for role, info in collection.get("by_role", {}).items()
                }
                != spec["releases"]
            ):
                raise ValueError(
                    "M8 annotation coverage or release identity unverified"
                )
        paths = [item.path for item in manifest.inputs]
        if len(paths) != len(set(paths)) or set(paths) != expected_manifest_inputs(
            spec, stage
        ):
            raise ValueError("Manifest input paths differ from the native DAG boundary")
        for artifact in manifest.inputs:
            path = Path(artifact.path)
            if (
                not path.is_absolute()
                or path.is_symlink()
                or not path.is_file()
                or path.stat().st_size != artifact.size_bytes
                or artifact.sha256 is None
                or sha256_file(path) != artifact.sha256
            ):
                raise ValueError(f"Manifest input has changed: {path}")
    else:
        if summary.get("schema") != SUMMARY_SCHEMAS[stage]:
            raise ValueError(f"Unexpected scientific schema for {stage}")
        if role is not None:
            if summary.get("release") != spec["releases"][role]:
                raise ValueError("Publication release differs from fresh recipe")
        elif summary.get("releases") != spec["releases"]:
            raise ValueError("Publication START/END identity differs")
        if stage == "workload" and summary.get("canonical_contract") != (
            "probe-canonical-v2-complete-workload-v1"
        ):
            raise ValueError("Publication is not the complete canonical workload")
        if stage == "alignment" and (
            summary.get("dp_backend") != "certified-geometric-bounds-v2"
            or summary.get("dp_gate_contract")
            != "same-resolved-taxid-documentary-literal-v1"
        ):
            raise ValueError("Unexpected DP backend or scientific admission gate")
        key = "outputs_sha256" if stage == "b3" else "output_sha256"
        hashes = _valid_hashes(summary.get(key))
        if set(actual) != {*hashes, anchor_name}:
            raise ValueError(f"Incomplete or extra publication payload in {bundle}")
        for name, digest in hashes.items():
            if sha256_file(actual[name]) != digest:
                raise ValueError(f"Publication payload SHA256 differs: {name}")
        input_key = (
            "inputs_sha256" if stage in {"b3", "alignment"} else ("input_sha256")
        )
        if summary.get(input_key) != expected_summary_inputs(spec, stage, role):
            raise ValueError(f"Publication inputs differ from fresh DAG: {stage}")
    return {
        name: {"sha256": sha256_file(path), "size_bytes": path.stat().st_size}
        for name, path in actual.items()
    }


def _receipt(
    *, spec: dict, stage: str, role: str | None, recipe_sha256: str, payload: dict
) -> dict:
    if not isinstance(recipe_sha256, str) or not _DIGEST.fullmatch(recipe_sha256):
        raise ValueError("Missing fresh recipe content SHA256")
    return {
        "schema": RECEIPT_SCHEMA,
        "recipe_sha256": recipe_sha256,
        "spec_sha256": _canonical_digest(spec),
        "stage": stage,
        "role": role,
        "releases": spec["releases"],
        "bundle": str(destination(spec, stage, role)),
        "payload": payload,
        "status": "PAYLOAD_AND_INPUT_INTEGRITY_VERIFIED",
    }


def _intent(*, spec: dict, stage: str, role: str | None, recipe_sha256: str) -> dict:
    """Record concrete source identities before any scientific producer runs."""
    value = _receipt(
        spec=spec, stage=stage, role=role, recipe_sha256=recipe_sha256, payload={}
    )
    value["schema"] = "probe-canonical-v2-fresh-intent-v1"
    value["status"] = "INPUTS_PINNED_BEFORE_EXECUTION"
    paths = {
        Path(path)
        for group in (spec["inputs"], spec["tools"], *spec["per_release"].values())
        for path in group.values()
    }
    source_hashes = {}
    for path in sorted(paths):
        if path.is_symlink():
            raise ValueError(f"Unsafe fresh source path: {path}")
        if path.is_dir():
            # Large upstream artifacts are certified through their immutable
            # producer manifest; the fresh runner verifies each complete bundle.
            path = path / "manifest.json"
        if not path.is_file() or path.is_symlink():
            raise ValueError(f"Missing source for fresh execution: {path}")
        source_hashes[str(path)] = sha256_file(path)
    value["source_sha256"] = source_hashes
    return value


def validate_node_receipt(
    *, spec: dict, stage: str, role: str | None = None, recipe_sha256: str
) -> dict:
    """Validate existing evidence, including non-anchor payload corruption."""
    payload = validate_publication(spec=spec, stage=stage, role=role)
    wanted = _receipt(
        spec=spec, stage=stage, role=role, recipe_sha256=recipe_sha256, payload=payload
    )
    marker = receipt_path(destination(spec, stage, role))
    intent = _intent(spec=spec, stage=stage, role=role, recipe_sha256=recipe_sha256)
    if _json(Path(f"{destination(spec, stage, role)}.fresh.intent.json")) != intent:
        raise ValueError("Fresh execution intent differs from current inputs")
    if _json(marker) != wanted:
        raise ValueError(f"Fresh receipt content or provenance differs: {marker}")
    return wanted


def _fsync_directory(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _write_receipt(marker: Path, value: dict) -> None:
    encoded = json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n"
    fd, temporary = tempfile.mkstemp(prefix=f".{marker.name}.", dir=marker.parent)
    tmp = Path(temporary)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
        # link() is atomic and fails if any receipt already occupies the path.
        os.link(tmp, marker)
        _fsync_directory(marker.parent)
    finally:
        tmp.unlink(missing_ok=True)


@contextmanager
def _node_lock(bundle: Path) -> Iterator[None]:
    path = Path(f"{bundle}.fresh.lock")
    flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(path, flags, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield
    finally:
        os.close(fd)


def execute_node(
    *, spec: dict, stage: str, role: str | None = None, recipe_sha256: str
) -> dict:
    """Run or recover one immutable canonical node under an advisory lock."""
    from probe.canonical_v2.dag_execution import dispatch

    validate_scientific_contracts(spec)
    bundle = destination(spec, stage, role)
    bundle.parent.mkdir(parents=True, exist_ok=True)
    marker = receipt_path(bundle)
    intent_path = Path(f"{bundle}.fresh.intent.json")
    with _node_lock(bundle):
        if marker.exists() or marker.is_symlink():
            return validate_node_receipt(
                spec=spec, stage=stage, role=role, recipe_sha256=recipe_sha256
            )
        intent = _intent(spec=spec, stage=stage, role=role, recipe_sha256=recipe_sha256)
        if intent_path.exists() or intent_path.is_symlink():
            if _json(intent_path) != intent:
                raise ValueError("Fresh node inputs changed after intent publication")
        elif bundle.exists() or bundle.is_symlink():
            raise ValueError("Cannot recover an orphan without its execution intent")
        else:
            _write_receipt(intent_path, intent)
        if not bundle.exists() and not bundle.is_symlink():
            dispatch(spec=spec, stage=stage, role=role, target=bundle)
        if (
            _intent(spec=spec, stage=stage, role=role, recipe_sha256=recipe_sha256)
            != intent
        ):
            raise ValueError("Fresh node inputs changed during scientific execution")
        payload = validate_publication(spec=spec, stage=stage, role=role)
        result = _receipt(
            spec=spec,
            stage=stage,
            role=role,
            recipe_sha256=recipe_sha256,
            payload=payload,
        )
        # Existing kernels publish by rename. Make their payload durable before
        # certifying the publication, including a recovered orphan bundle.
        for path in _regular_files(bundle).values():
            with path.open("rb") as handle:
                os.fsync(handle.fileno())
        for directory in sorted(
            (item for item in bundle.rglob("*") if item.is_dir()), reverse=True
        ):
            _fsync_directory(directory)
        _fsync_directory(bundle)
        _fsync_directory(bundle.parent)
        _write_receipt(marker, result)
        return result
