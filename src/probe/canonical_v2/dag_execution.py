"""Dispatch production-tested ProBE V2 kernels from a single DAG.

No historical Stage 05/07 publication is required. The input 03/04
scientific sources remain explicit; every bundle is immutable on creation.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from probe.canonical_v2.bounded_dp import BACKEND
from probe.canonical_v2.candidate_assembly import assemble_candidate_provenance
from probe.canonical_v2.dag_contract import (
    ROLES,
    destination,
    validate_scientific_contracts,
)
from probe.canonical_v2.documentary_handoff import prepare_documentary_handoff
from probe.canonical_v2.documentary_lookup import discover_documentary_nonexact
from probe.canonical_v2.exact_documentary_registry import (
    build_exact_documentary_register,
)
from probe.canonical_v2.primary_projection import project_primary_candidate_facts
from probe.canonical_v2.release_workload import build_release_workload
from probe.canonical_v2.resolver_runner import publish_canonical_stage07
from probe.canonical_v2.stage05_06a import build_stage05_06a


def _file(spec: dict, name: str, role: str | None = None) -> Path:
    group = spec["inputs"] if role is None else spec["per_release"][role]
    return Path(group[name])


def _tool(spec: dict, name: str) -> Path:
    return Path(spec["tools"][name])


def _roles(spec: dict, name: str) -> dict[str, Path]:
    return {role: _file(spec, name, role) for role in ROLES}


def _run_command(script: Path, arguments: list[str]) -> None:
    if not script.is_file() or script.is_symlink():
        raise ValueError("Invalid scientific stage CLI")
    subprocess.run([sys.executable, str(script), *arguments], check=True)


def dispatch(
    *,
    spec: dict,
    stage: str,
    target: Path,
    role: str | None = None,
) -> None:
    """Execute one DAG node with output-path and approval-mode guards."""
    validate_scientific_contracts(spec)
    if target != destination(spec, stage, role):
        raise ValueError("DAG node destination differs from versioned contract")
    if target.exists() or target.is_symlink():
        raise FileExistsError("Immutable canonical V2 output exists")
    if (
        stage in {"alignment", "stage07", "m8", "m10c", "temporal"}
        and spec["mode"] != "execute"
    ):
        raise ValueError("Scientific execution requires mode=execute")
    target.parent.mkdir(parents=True, exist_ok=True)
    releases = spec["releases"]
    if stage == "exact":
        assert role is not None
        build_exact_documentary_register(
            release=releases[role],
            discovery=_file(spec, "unified_discovery", role),
            targets=_file(spec, "targets"),
            full_manifest=_file(spec, "full_manifest", role),
            output_dir=target,
        )
    elif stage == "ledger":
        assert role is not None
        assemble_candidate_provenance(
            release=releases[role],
            discovery_union=_file(spec, "unified_discovery", role),
            go_positive_pairs=_file(spec, "go_positive_pairs", role),
            full_exact_pairs=_file(spec, "full_exact_pairs", role),
            lineage_pairs=_file(spec, "lineage_pairs", role),
            rescue_pairs=_file(spec, "rescue_pairs", role),
            output_dir=target,
        )
    elif stage == "projection":
        assert role is not None
        project_primary_candidate_facts(
            release=releases[role],
            ledger_dir=destination(spec, "ledger", role),
            output_dir=target,
        )
    elif stage == "lookup":
        assert role is not None
        other = "END" if role == "START" else "START"
        discover_documentary_nonexact(
            release=releases[role],
            targets=_file(spec, "targets"),
            target_taxonomy=_file(spec, "taxonomy", role),
            exact_register=destination(spec, "exact", role),
            other_exact_register=destination(spec, "exact", other),
            other_release=releases[other],
            independent_index=_file(spec, "lineage_index"),
            ox_taxonomy=_file(spec, "ox_taxonomy", role),
            full_manifest=_file(spec, "full_manifest", role),
            destination=target,
        )
    elif stage == "handoff":
        prepare_documentary_handoff(
            start_lookup=destination(spec, "lookup", "START"),
            end_lookup=destination(spec, "lookup", "END"),
            start_release=releases["START"],
            end_release=releases["END"],
            destination=target,
        )
    elif stage == "b3":
        build_stage05_06a(
            releases=releases,
            projections={role: destination(spec, "projection", role) for role in ROLES},
            taxonomies=_roles(spec, "taxonomy"),
            anchors=_roles(spec, "anchors"),
            full_manifests=_roles(spec, "full_manifest"),
            targets=_file(spec, "targets"),
            stage05c2_cli=_tool(spec, "stage05c2_cli"),
            stage06a_cli=_tool(spec, "stage06a_cli"),
            output_dir=target,
        )
    elif stage == "workload":
        build_release_workload(
            b3=destination(spec, "b3"),
            handoff=destination(spec, "handoff"),
            releases=releases,
            targets=_file(spec, "targets"),
            full_manifests=_roles(spec, "full_manifest"),
            stage06a_cli=_tool(spec, "stage06a_cli"),
            destination=target,
        )
    elif stage == "alignment":
        from probe.canonical_v2.alignment_v2 import execute_alignment_v2

        limits = spec["limits"]
        execute_alignment_v2(
            workload=destination(spec, "workload"),
            targets=_file(spec, "targets"),
            taxonomy=_roles(spec, "taxonomy"),
            lineage_index=_file(spec, "lineage_index"),
            lineage_paths={
                releases[role]: _file(spec, "lineage", role) for role in ROLES
            },
            nonexact_script=_tool(spec, "nonexact_script"),
            full_dp_binary=_tool(spec, "full_dp_binary"),
            finalize_script=_tool(spec, "finalize_script"),
            destination=target,
            threads=limits["threads"],
            max_pair_cells=limits["max_pair_cells"],
            max_total_cells=limits["max_total_cells"],
            max_chunk_cells=limits["max_chunk_cells"],
            dp_backend=BACKEND,
        )
    elif stage == "stage07":
        publish_canonical_stage07(
            workload=destination(spec, "workload"),
            completion=destination(spec, "alignment"),
            targets=_file(spec, "targets"),
            taxonomy_paths={
                releases[role]: _file(spec, "taxonomy", role) for role in ROLES
            },
            anchor_paths={
                releases[role]: _file(spec, "anchors", role) for role in ROLES
            },
            lineage_index=_file(spec, "lineage_index"),
            lineage_paths={
                releases[role]: _file(spec, "lineage", role) for role in ROLES
            },
            policy=spec["policy"],
            partial_sequence=spec["limits"]["partial_sequence"],
            documentary_fragment=spec["limits"]["documentary_fragment"],
            destination=target,
        )
    elif stage == "m8":
        _run_command(
            _tool(spec, "collection_cli"),
            [
                "--stage07-bundle",
                str(destination(spec, "stage07")),
                "--start-goa-bundle",
                str(_file(spec, "goa_bundle", "START")),
                "--end-goa-bundle",
                str(_file(spec, "goa_bundle", "END")),
                "--go-policy-bundle",
                str(_file(spec, "go_policy_bundle")),
                "--destination",
                str(target),
                "--execute",
            ],
        )
    elif stage == "m10c":
        _run_command(
            _tool(spec, "m10c_cli"),
            [
                "--stage07-bundle",
                str(destination(spec, "stage07")),
                "--start-goa-bundle",
                str(_file(spec, "goa_bundle", "START")),
                "--end-goa-bundle",
                str(_file(spec, "goa_bundle", "END")),
                "--go-policy-bundle",
                str(_file(spec, "go_policy_bundle")),
                "--m8-bundle",
                str(destination(spec, "m8")),
                "--ontology",
                str(_file(spec, "ontology")),
                "--destination",
                str(target),
                "--execute",
            ],
        )
    elif stage == "temporal":
        _run_command(
            _tool(spec, "temporal_cli"),
            [
                "--m10c-bundle",
                str(destination(spec, "m10c")),
                "--m8-summary",
                str(destination(spec, "m8") / "08_collection_summary.json"),
                "--targets-input",
                str(_file(spec, "targets_input")),
                "--stage01-targets",
                str(_file(spec, "targets")),
                "--evidence-registry",
                str(_file(spec, "evidence_registry")),
                "--policy",
                str(_file(spec, "temporal_policy")),
                "--ontology",
                str(_file(spec, "ontology")),
                "--start-release",
                releases["START"],
                "--end-release",
                releases["END"],
                "--destination",
                str(target),
                "--execute",
            ],
        )
    else:
        raise ValueError("Unknown canonical V2 DAG step")
