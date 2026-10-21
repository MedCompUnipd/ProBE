"""Runtime adapters for the raw-input canonical V2 Snakemake DAG.

Reading a recipe never reads an upstream publication. Discovery plans and the
canonical specification are resolved only after their real producers finish.
The existing scientific producers, planner schemas, and dispatcher are reused.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path

import yaml

from probe.canonical_v2.dag_contract import (
    ROLES,
    SCIENTIFIC_CONTRACTS,
    TOOLS,
    VERSION,
    destination,
    read_dag_spec,
)
from probe.workflow.artifacts import StageManifest, sha256_file, validate_bundle
from probe.workflow.config import PipelineConfig

SCHEMA = "probe-canonical-v2-fresh-recipe-v1"
ROLE_NODES = ("exact", "ledger", "projection", "lookup")
COMMON_NODES = (
    "handoff",
    "b3",
    "workload",
    "alignment",
    "stage07",
    "m8",
    "m10c",
    "temporal",
)
RAW_COMMON_STAGES = ("01", "01B0", "04C", "04D1", "05A", "05B")
RAW_ROLE_STAGES = (
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

# Every published member is tracked, not just the manifest or a directory mtime.
# Output contracts are those of the imported production Stage 01--05R1 wrappers.
RAW_MEMBERS = {
    "01": (
        "01_targets.tsv",
        "01_rejected.tsv",
        "01_summary.json",
        "stage_outcome.json",
    ),
    "01A_FULL": (
        "01A_full_v2.fasta",
        "01A_full_v2.issues.tsv",
        "01A_full_v2.summary.json",
    ),
    "01B0": (
        "01B0_go_policy.tsv",
        "01B0_go_policy.stats.json",
        "01B0_go_policy.manifest.json",
    ),
    "01B1": (
        "goa.normalized.gaf",
        "goa.positive_accessions.txt",
        "goa.only_not_accessions.txt",
        "goa.missing_accessions.txt",
        "goa.discard_reason_counts.tsv",
        "goa.stats.json",
        "stage_outcome.json",
    ),
    "01C": (
        "01C_go_positive_v2.fasta",
        "01C_go_positive_v2.stats.tsv",
        "stage_outcome.json",
    ),
    "02A": ("02A_raw_ox.txt", "02A_raw_ox.stats.tsv"),
    "02B": (
        "02B_taxonomy.sqlite",
        "02B_target_taxonomy.tsv",
        "02B_ox_taxonomy.tsv",
        "02B.stats.json",
    ),
    "03": (
        "03_primary_candidates.pairs.tsv",
        "03_primary_candidates.unsearchable_targets.tsv",
        "03_primary_candidates.summary.tsv",
        "stage_outcome.json",
    ),
    "04A": (
        "04A_direct_accession_anchors.tsv",
        "04A_direct_accession_anchors.summary.tsv",
    ),
    "04B": ("04B_classified_direct_anchors.tsv", "04B_summary.json"),
    "04C": ("04C_lineage_keys.tsv", "04C_summary.json"),
    "04D0a": ("04D0a_full_raw_ox.txt", "04D0a.stats.tsv"),
    "04D0b": ("full_ox_taxonomy.tsv", "04D0b_summary.json"),
    "04D1": ("04D1_lineage_scan_index.tsv", "04D1_summary.json"),
    "04D2": ("04D2_lineage_evidence.tsv", "04D2_summary.tsv", "stage_outcome.json"),
    "04E": ("04E_lineage_candidates.tsv", "04E_summary.json"),
    "05A": ("05A_candidate_union.tsv", "05A_summary.json"),
    "05B": ("05B_uncovered_targets.tsv", "05B_summary.json"),
    "05R0": (
        "rescue_ids.txt",
        "rescue_targets.tsv",
        "rescue_target_taxonomy.tsv",
        "05R0_summary.json",
    ),
    "05R1": (
        "05R1_full_sequence_rescue.pairs.tsv",
        "05R1_full_sequence_rescue.unsearchable_targets.tsv",
        "05R1_full_sequence_rescue.summary.tsv",
        "stage_outcome.json",
    ),
}


def _absolute(value: object, label: str) -> Path:
    if not isinstance(value, str) or not value:
        raise ValueError(f"Missing fresh path: {label}")
    path = Path(value)
    if not path.is_absolute() or ".." in path.parts:
        raise ValueError(f"Unsafe fresh path: {label}")
    return path


def _regular(path: Path) -> None:
    if not path.is_file() or path.is_symlink():
        raise ValueError(f"Missing or symlinked fresh control file: {path}")


@dataclass(frozen=True)
class FreshRecipe:
    data: dict
    pipeline: dict
    path: Path
    sha256: str

    @property
    def run(self) -> Path:
        return Path(self.data["run_root"])

    @property
    def repo(self) -> Path:
        return Path(self.data["repo_root"])

    def bundle(self, stage: str, role: str | None = None) -> Path:
        if stage not in RAW_MEMBERS:
            raise ValueError(f"Unknown raw stage: {stage}")
        if role is not None and role not in (*ROLES, "{role}"):
            raise ValueError("Foreign raw release role")
        if (stage in RAW_COMMON_STAGES) != (role is None):
            raise ValueError("Raw stage role mismatch")
        return self.run / "work/pipeline-v2" / (role or "common") / stage

    def manifest(self, stage: str, role: str | None = None) -> str:
        return str(self.bundle(stage, role) / "manifest.json")

    def outputs(self, stage: str, role: str | None = None) -> list[str]:
        root = self.bundle(stage, role)
        return [str(root / name) for name in ("manifest.json", *RAW_MEMBERS[stage])]

    def plan_path(self, kind: str) -> Path:
        if kind not in {"species", "full_exact", "union", "canonical_spec"}:
            raise ValueError("Unknown fresh plan")
        return self.run / "plans" / f"{kind}.json"


def read_recipe(path: Path, *, expected_repo: Path | None = None) -> FreshRecipe:
    """Validate only control files and paths, allowing a genuinely cold DAG."""
    path = _absolute(str(path), "recipe")
    _regular(path)
    data = json.loads(path.read_text(encoding="utf-8"))
    keys = {
        "schema",
        "repo_root",
        "run_root",
        "pipeline_config",
        "identity_sha256",
        "policy",
        "limits",
        "tools",
        "targets_input",
        "evidence_registry",
        "temporal_policy",
    }
    if not isinstance(data, dict) or set(data) != keys or data["schema"] != SCHEMA:
        raise ValueError("Unsupported or incomplete fresh recipe")
    if (
        not isinstance(data["identity_sha256"], str)
        or re.fullmatch(r"[a-f0-9]{64}", data["identity_sha256"]) is None
    ):
        raise ValueError("Fresh recipe requires a complete content identity lock")
    repo = _absolute(data["repo_root"], "repo_root")
    run = _absolute(data["run_root"], "run_root")
    if run == Path("/") or run == repo or repo in run.parents:
        raise ValueError("Fresh scientific outputs must be outside the source tree")
    if repo.resolve(strict=True) != repo or run.resolve(strict=True) != run:
        raise ValueError("Fresh source/run root must be physical absolute directories")
    if expected_repo is not None and repo != expected_repo.resolve(strict=True):
        raise ValueError("Fresh recipe selects a different source checkout")
    config_path = _absolute(data["pipeline_config"], "pipeline_config")
    if config_path != run / "pipeline.stage08.collection.yaml":
        raise ValueError(
            "Discovery planners require the run-local collection configuration"
        )
    _regular(config_path)
    raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    # This rejects old variant options; no planner is called at parse time.
    pipeline = PipelineConfig.from_mapping(raw).data
    if pipeline["targets"]["mode"] != "canonical":
        raise ValueError(
            "Fresh Stage 01 requires the canonical FASTA/metadata boundary"
        )
    expected_locations = {
        "work": str(run / "work/pipeline-v2"),
        "output": str(run / "results/pipeline-v2"),
        "logs": str(run / "logs/pipeline-v2"),
    }
    if pipeline["locations"] != expected_locations:
        raise ValueError("Fresh upstream destinations are not isolated under run_root")
    source_paths = [
        pipeline["targets"]["fasta"],
        pipeline["targets"]["metadata"],
        pipeline["ontology"]["path"],
        data["targets_input"],
        data["evidence_registry"],
        data["temporal_policy"],
    ]
    for role in ROLES:
        release = pipeline["releases"][role]
        source_paths.extend([*release["uniprot_sources"], release["goa"]])
        source_paths.extend(
            value for key, value in release["taxonomy"].items() if key != "snapshot_id"
        )
    if not isinstance(data["tools"], dict) or set(data["tools"]) != TOOLS:
        raise ValueError("Incomplete fresh canonical tool mapping")
    for label, value in data["tools"].items():
        tool = _absolute(value, label)
        if not tool.is_relative_to(repo):
            raise ValueError(
                "Fresh scientific tools must belong to the source snapshot"
            )
    for value in (*source_paths, *data["tools"].values()):
        source = _absolute(value, "input/tool")
        for root in (
            *expected_locations.values(),
            str(run / "canonical"),
            str(run / "plans"),
        ):
            if source == Path(root) or Path(root) in source.parents:
                raise ValueError("Fresh raw inputs cannot reuse a scientific output")
    if data["policy"] not in {"strict", "same_species_exact"}:
        raise ValueError("Invalid canonical EXACT policy")
    limits = data["limits"]
    if not isinstance(limits, dict) or set(limits) != {
        "threads",
        "max_pair_cells",
        "max_total_cells",
        "max_chunk_cells",
        "partial_sequence",
        "documentary_fragment",
    }:
        raise ValueError("Incomplete fresh execution limits")
    for name in ("threads", "max_pair_cells", "max_total_cells", "max_chunk_cells"):
        if name == "max_chunk_cells" and limits[name] is None:
            continue
        if type(limits[name]) is not int or limits[name] < 1:
            raise ValueError(f"Invalid fresh execution limit: {name}")
    for name in ("partial_sequence", "documentary_fragment"):
        if limits[name] not in {"allow", "report_only"}:
            raise ValueError(f"Invalid fresh eligibility policy: {name}")
    return FreshRecipe(data, pipeline, path, sha256_file(path))


def publish_json(path: Path, value: dict) -> None:
    """Publish a complete control file without replacing existing content."""
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = (
        json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n"
    ).encode()
    if path.exists() or path.is_symlink():
        _regular(path)
        if path.read_bytes() != encoded:
            raise FileExistsError(f"Immutable fresh control file differs: {path}")
        return
    descriptor, name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
        os.link(temporary, path)
        directory_fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        temporary.unlink(missing_ok=True)


def plan_code_inputs(recipe: FreshRecipe, kind: str) -> list[str]:
    """List planner implementation inputs without materializing a plan."""
    if kind == "species":
        from probe.stage2c_discovery import CODE_FILES
    elif kind == "full_exact":
        from probe.stage2c_full_exact import CODE_FILES
    elif kind == "union":
        from probe.stage2c_union_plan import CODE_FILES
    else:
        raise ValueError("Unknown discovery plan kind")
    return [str(recipe.repo / name) for name in CODE_FILES]


def make_plan(recipe: FreshRecipe, kind: str, output: Path) -> None:
    """Invoke an unchanged scientific planner only inside a checkpoint job."""
    if output != recipe.plan_path(kind):
        raise ValueError("Unexpected fresh plan destination")
    if kind == "species":
        from probe.stage2c_discovery import build_plan

        plan = build_plan(recipe.repo, recipe.run)
    elif kind == "full_exact":
        from probe.stage2c_full_exact import build_plan

        plan = build_plan(recipe.repo, recipe.run)
        if plan["parameters"].get("fragment") != "include":
            raise ValueError(
                "Fresh FULL EXACT must admit literal FRAG=1 during discovery"
            )
    elif kind == "union":
        from probe.stage2c_union_plan import build_plan

        plan = build_plan(
            recipe.repo, recipe.plan_path("species"), recipe.plan_path("full_exact")
        )
        if plan.get("parameters", {}).get("allow_literal_fragments") is not True:
            raise ValueError("Fresh discovery union does not enable literal fragments")
    else:
        raise ValueError("Unknown discovery plan kind")
    publish_json(output, plan)


def load_plan(path: Path, kind: str) -> dict:
    """Verify all scientific upstreams at execution time, never in input functions."""
    if kind == "species":
        from probe.stage2c_discovery import read_plan
    elif kind == "full_exact":
        from probe.stage2c_full_exact import read_plan
    elif kind == "union":
        from probe.stage2c_union_plan import read_plan
    else:
        raise ValueError("Unknown discovery plan kind")
    return read_plan(path)


def plan_locations(recipe: FreshRecipe, kind: str, path: Path) -> dict:
    """Read safe scheduling metadata while upstream jobs may still need repair.

    The production read_plan validators inspect biological publications. Calling
    them while constructing the DAG would prevent a missing upstream payload from
    being rebuilt. Their complete verification remains in the production scripts
    and in resolve_spec, after those prerequisites have finished.
    """
    if path != recipe.plan_path(kind):
        raise ValueError("Foreign fresh scheduling plan")
    _regular(path)
    plan = json.loads(path.read_text(encoding="utf-8"))
    if (
        not isinstance(plan, dict)
        or plan.get("repo") != str(recipe.repo)
        or plan.get("run") != str(recipe.run)
        or not isinstance(plan.get("key"), str)
        or re.fullmatch(r"[a-f0-9]{64}", plan["key"]) is None
    ):
        raise ValueError("Invalid fresh scheduling identity")
    directories = {
        "species": "03_species_wide_variants",
        "full_exact": "03_full_exact_variants",
        "union": "03_discovery_union_variants",
    }

    def validate_paths(value: object, dirname: str, digest: object) -> None:
        if not isinstance(value, dict) or set(value) != set(ROLES):
            raise ValueError("Missing release-local discovery locations")
        if not isinstance(digest, str) or re.fullmatch(r"[a-f0-9]{64}", digest) is None:
            raise ValueError("Invalid discovery location fingerprint")
        for role in ROLES:
            expected = recipe.run / "work/pipeline-v2" / role / dirname / digest
            if value[role] != str(expected):
                raise ValueError("Discovery location escapes its fresh namespace")

    validate_paths(plan.get("paths"), directories[kind], plan["key"])
    if kind == "union":
        validate_paths(
            plan.get("species_paths"), directories["species"], plan.get("species_key")
        )
        validate_paths(
            plan.get("full_paths"), directories["full_exact"], plan.get("full_key")
        )
    return plan


def canonical_sources(recipe: FreshRecipe, plans: dict[str, dict]) -> dict:
    """Map actual published Stage 01--05R1 members to the canonical contract."""
    run = recipe.run
    raw = run / "work/pipeline-v2"
    common = raw / "common"
    data = recipe.data
    per_release = {}
    for role in ROLES:
        root = raw / role
        per_release[role] = {
            "unified_discovery": str(
                Path(plans["union"]["paths"][role]) / "03_unified_discovery.tsv"
            ),
            "go_positive_pairs": str(
                Path(plans["species"]["paths"][role])
                / "03_primary_candidates.pairs.tsv"
            ),
            "full_exact_pairs": str(
                Path(plans["full_exact"]["paths"][role])
                / "03_full_exact_cross_strain.pairs.tsv"
            ),
            "lineage_pairs": str(root / "04E/04E_lineage_candidates.tsv"),
            "rescue_pairs": str(root / "05R1/05R1_full_sequence_rescue.pairs.tsv"),
            "taxonomy": str(root / "02B/02B_target_taxonomy.tsv"),
            "anchors": str(root / "04B/04B_classified_direct_anchors.tsv"),
            "lineage": str(root / "04D2/04D2_lineage_evidence.tsv"),
            # Documentary lookup requires the TaxIDs from the FULL reference.
            "ox_taxonomy": str(root / "04D0b/full_ox_taxonomy.tsv"),
            "full_manifest": str(root / "01A_FULL/manifest.json"),
            "goa_bundle": str(root / "01B1"),
        }
    return {
        "schema": VERSION,
        "mode": "execute",
        "policy": data["policy"],
        "scientific_contracts": dict(SCIENTIFIC_CONTRACTS),
        "releases": {
            role: recipe.pipeline["releases"][role]["release_id"] for role in ROLES
        },
        "output_root": str(run / "canonical"),
        "inputs": {
            "targets": str(common / "01/01_targets.tsv"),
            "targets_input": data["targets_input"],
            "ontology": recipe.pipeline["ontology"]["path"],
            "evidence_registry": data["evidence_registry"],
            "temporal_policy": data["temporal_policy"],
            "lineage_index": str(common / "04D1/04D1_lineage_scan_index.tsv"),
            "go_policy_bundle": str(common / "01B0"),
        },
        "per_release": per_release,
        "tools": dict(data["tools"]),
        "limits": dict(data["limits"]),
    }


def all_raw_manifests(recipe: FreshRecipe) -> list[str]:
    return [recipe.manifest(stage) for stage in RAW_COMMON_STAGES] + [
        recipe.manifest(stage, role) for role in ROLES for stage in RAW_ROLE_STAGES
    ]


def all_raw_outputs(recipe: FreshRecipe) -> list[str]:
    """Track every upstream manifest and payload as a DAG dependency."""
    paths = [path for stage in RAW_COMMON_STAGES for path in recipe.outputs(stage)]
    paths.extend(
        path
        for role in ROLES
        for stage in RAW_ROLE_STAGES
        for path in recipe.outputs(stage, role)
    )
    if len(paths) != len(set(paths)):
        raise ValueError("Duplicate raw output in fresh DAG")
    return paths


def content_identity(
    recipe: FreshRecipe, manifests: dict[str, str]
) -> tuple[str, dict]:
    """Bind actual external bytes and produced manifests to the pinned recipe."""
    inputs = {
        "ontology": recipe.pipeline["ontology"]["path"],
        "targets_input": recipe.data["targets_input"],
        "evidence_registry": recipe.data["evidence_registry"],
        "temporal_policy": recipe.data["temporal_policy"],
        "pipeline_config": recipe.data["pipeline_config"],
        **{f"tool:{name}": path for name, path in recipe.data["tools"].items()},
    }
    observed = {}
    for label, name in inputs.items():
        path = Path(name)
        _regular(path)
        observed[label] = {"path": name, "sha256": sha256_file(path)}
    evidence = {
        "schema": "probe-canonical-v2-fresh-content-identity-v1",
        "recipe_identity_sha256": recipe.data["identity_sha256"],
        "recipe_sha256": recipe.sha256,
        "upstream_manifests_sha256": dict(manifests),
        "direct_inputs_sha256": observed,
    }
    encoded = json.dumps(
        evidence, sort_keys=True, separators=(",", ":"), allow_nan=False
    )
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest(), evidence


def validate_existing_upstreams(recipe: FreshRecipe, *, deep: bool = True) -> dict:
    """Reject incomplete publications before scheduling, then certify hashes.

    ``deep=False`` checks declared member existence at Snakefile parse time,
    BEFORE Snakemake can plan a producer for a missing protected output.
    ``deep=True`` verifies complete SHA256 content at the onstart gate.
    A damaged immutable bundle must not be silently rebuilt in place.
    """
    paths = [Path(name) for name in all_raw_manifests(recipe)]
    for kind in ("species", "full_exact", "union"):
        plan_path = recipe.plan_path(kind)
        if plan_path.exists():
            plan = plan_locations(recipe, kind, plan_path)
            paths.extend(Path(plan["paths"][role]) / "manifest.json" for role in ROLES)
    complete = {}
    present = []
    for path in paths:
        parent = path.parent
        while parent != recipe.run:
            if parent.is_symlink():
                raise ValueError(f"Symlinked fresh upstream publication: {parent}")
            parent = parent.parent
        if not path.exists():
            if path.is_symlink():
                raise ValueError(f"Broken symlink in fresh publication: {path}")
            if path.parent.is_dir() and any(path.parent.iterdir()):
                raise ValueError(
                    "Fresh publication is missing its manifest while the "
                    f"bundle still contains files: {path.parent}"
                )
            continue
        _regular(path)
        manifest = StageManifest.read(path)
        if (
            manifest.role is not None
            and manifest.release_id
            != recipe.pipeline["releases"][manifest.role]["release_id"]
        ):
            raise ValueError(f"Fresh upstream has a foreign release: {path}")
        missing = []
        for artifact in manifest.outputs:
            member = path.parent / artifact.path
            if member.is_symlink() or (member.exists() and not member.is_file()):
                raise ValueError(f"Unsafe fresh published payload: {member}")
            if not member.exists():
                missing.append(artifact.path)
        if missing:
            raise ValueError(
                "Incomplete immutable fresh publication; use a new fresh "
                f"namespace for recomputation: {path}; missing={missing}"
            )
        if deep:
            print(
                "Deep-certifying existing upstream "
                f"{manifest.stage} {manifest.role or 'COMMON'}",
                flush=True,
            )
            validate_bundle(path.parent, deep=True)
            complete[str(path)] = sha256_file(path)
        else:
            present.append(str(path))
    return {
        "complete_verified": complete,
        "present_checked": present,
        "incomplete_published": [],
    }


def validate_completion_identity(recipe: FreshRecipe, spec: dict) -> dict:
    """Reject stale namespaces before certifying an otherwise complete graph."""
    if sha256_file(recipe.path) != recipe.sha256:
        raise ValueError("Fresh recipe changed during execution")
    path = recipe.run / "plans/canonical_spec.provenance.json"
    _regular(path)
    provenance = json.loads(path.read_text(encoding="utf-8"))
    if (
        provenance.get("schema") != "probe-canonical-v2-fresh-resolved-v1"
        or provenance.get("recipe_sha256") != recipe.sha256
        or provenance.get("identity_sha256") != recipe.data["identity_sha256"]
        or provenance.get("releases") != spec["releases"]
        or provenance.get("spec_sha256")
        != sha256_file(recipe.plan_path("canonical_spec"))
    ):
        raise ValueError("Fresh completion provenance disagrees with its specification")
    inventory = provenance.get("upstream_manifests_sha256")
    expected = set(all_raw_manifests(recipe))
    for kind in ("species", "full_exact", "union"):
        plan = plan_locations(recipe, kind, recipe.plan_path(kind))
        expected.update(
            str(Path(plan["paths"][role]) / "manifest.json") for role in ROLES
        )
    if not isinstance(inventory, dict) or set(inventory) != expected:
        raise ValueError(
            "Fresh completion lacks the complete upstream manifest inventory"
        )
    current = {}
    for name in sorted(expected):
        source = Path(name)
        _regular(source)
        validate_bundle(source.parent, deep=True)
        current[name] = sha256_file(source)
    digest, evidence = content_identity(recipe, current)
    if (
        digest != spec.get("content_identity_sha256")
        or current != inventory
        or evidence != provenance.get("content_identity")
    ):
        raise ValueError("Fresh completion has stale source, tool, or policy identity")
    return evidence


def validate_completed_workflow(recipe: FreshRecipe, spec: dict) -> dict:
    """Final always-run Snakemake node; completion requires every native receipt."""
    from probe.canonical_v2.fresh_publication import receipt_path, validate_node_receipt

    if "content_identity_sha256" not in spec:
        raise ValueError("Fresh completion lacks a scientific content identity")
    validate_completion_identity(recipe, spec)
    nodes = [(stage, role) for stage in ROLE_NODES for role in ROLES]
    nodes.extend((stage, None) for stage in COMMON_NODES)
    receipts = {}
    for stage, role in nodes:
        validate_node_receipt(
            spec=spec, stage=stage, role=role, recipe_sha256=recipe.sha256
        )
        marker = receipt_path(destination(spec, stage, role))
        receipts[f"{stage}:{role or 'COMMON'}"] = {
            "path": str(marker),
            "sha256": sha256_file(marker),
        }
    result = {
        "schema": "probe-canonical-v2-fresh-completion-v1",
        "status": "PASS",
        "recipe_sha256": recipe.sha256,
        "content_identity_sha256": spec["content_identity_sha256"],
        "releases": spec["releases"],
        "canonical_publications": len(receipts),
        "receipts": receipts,
    }
    publish_json(
        recipe.run / "verification" / (spec["content_identity_sha256"] + ".json"),
        result,
    )
    return result


def resolve_spec(recipe: FreshRecipe, output: Path, provenance: Path) -> dict:
    """Validate finished sources and publish the canonical execution contract."""
    if output != recipe.plan_path("canonical_spec"):
        raise ValueError("Unexpected canonical specification path")
    plans = {
        kind: load_plan(recipe.plan_path(kind), kind)
        for kind in ("species", "full_exact", "union")
    }
    spec = canonical_sources(recipe, plans)
    manifests = [Path(path) for path in all_raw_manifests(recipe)]
    manifests.extend(
        Path(plan["paths"][role]) / "manifest.json"
        for plan in plans.values()
        for role in ROLES
    )
    inventory = {}
    for path in manifests:
        manifest = validate_bundle(path.parent, deep=True)
        if (
            manifest.role is not None
            and manifest.release_id != spec["releases"][manifest.role]
        ):
            raise ValueError(f"Fresh publication release mismatch: {path}")
        inventory[str(path)] = sha256_file(path)
    for group in (spec["inputs"], *spec["per_release"].values()):
        for value in group.values():
            path = Path(value)
            if path.is_symlink() or not path.exists():
                raise ValueError(f"Unpublished canonical source: {path}")
            if path.is_file() and path.parent.joinpath("manifest.json").is_file():
                declared = StageManifest.read(path.parent / "manifest.json")
                if path.name != "manifest.json" and path.name not in {
                    item.path for item in declared.outputs
                }:
                    raise ValueError(
                        f"Canonical source is not declared by its producer: {path}"
                    )
    spec["content_identity_sha256"], identity_evidence = content_identity(
        recipe, inventory
    )
    publish_json(output, spec)
    read_dag_spec(output)
    result = {
        "schema": "probe-canonical-v2-fresh-resolved-v1",
        "recipe_sha256": recipe.sha256,
        "identity_sha256": recipe.data["identity_sha256"],
        "spec_sha256": sha256_file(output),
        "upstream_manifests_sha256": inventory,
        "discovery_plan_keys": {kind: plan["key"] for kind, plan in plans.items()},
        "content_identity": identity_evidence,
        "releases": spec["releases"],
        "status": "UPSTREAM_PUBLISHED_CANONICAL_PENDING",
    }
    publish_json(provenance, result)
    return spec


def node_dependencies(spec: dict, stage: str, role: str | None = None) -> list[str]:
    """One explicit edge table matching dispatch; no filesystem discovery."""
    from probe.canonical_v2.fresh_publication import receipt_path

    destination(spec, stage, role)  # Validate node/role before looking up sources.
    common = spec["inputs"]
    sources = spec["per_release"]
    tools = spec["tools"]

    def marker(node: str, release_role: str | None = None) -> str:
        return str(receipt_path(destination(spec, node, release_role)))

    def both(name: str) -> list[str]:
        return [sources[value][name] for value in ROLES]

    def both_markers(node: str) -> list[str]:
        return [marker(node, value) for value in ROLES]

    if stage == "exact":
        deps = [
            sources[role]["unified_discovery"],
            common["targets"],
            sources[role]["full_manifest"],
        ]
    elif stage == "ledger":
        deps = [
            sources[role][name]
            for name in (
                "unified_discovery",
                "go_positive_pairs",
                "full_exact_pairs",
                "lineage_pairs",
                "rescue_pairs",
            )
        ]
    elif stage == "projection":
        deps = [marker("ledger", role)]
    elif stage == "lookup":
        deps = [
            *both_markers("exact"),
            common["targets"],
            common["lineage_index"],
            *[
                sources[role][name]
                for name in ("taxonomy", "ox_taxonomy", "full_manifest")
            ],
        ]
    elif stage == "handoff":
        deps = both_markers("lookup")
    elif stage == "b3":
        deps = [
            *both_markers("projection"),
            common["targets"],
            *both("taxonomy"),
            *both("anchors"),
            *both("full_manifest"),
            tools["stage05c2_cli"],
            tools["stage06a_cli"],
        ]
    elif stage == "workload":
        deps = [
            marker("b3"),
            marker("handoff"),
            common["targets"],
            *both("full_manifest"),
            tools["stage06a_cli"],
        ]
    elif stage == "alignment":
        deps = [
            marker("workload"),
            common["targets"],
            *both("taxonomy"),
            common["lineage_index"],
            *both("lineage"),
            tools["nonexact_script"],
            tools["full_dp_binary"],
            tools["finalize_script"],
        ]
    elif stage == "stage07":
        deps = [
            marker("workload"),
            marker("alignment"),
            common["targets"],
            common["lineage_index"],
            *both("taxonomy"),
            *both("anchors"),
            *both("lineage"),
        ]
    elif stage in {"m8", "m10c"}:
        deps = [
            marker("stage07"),
            *[str(Path(path) / "manifest.json") for path in both("goa_bundle")],
            str(Path(common["go_policy_bundle"]) / "manifest.json"),
            tools["collection_cli" if stage == "m8" else "m10c_cli"],
        ]
        if stage == "m10c":
            deps += [marker("m8"), common["ontology"]]
    elif stage == "temporal":
        deps = [
            marker("m10c"),
            marker("m8"),
            *[
                common[name]
                for name in (
                    "targets",
                    "targets_input",
                    "evidence_registry",
                    "temporal_policy",
                    "ontology",
                )
            ],
            tools["temporal_cli"],
        ]
    else:
        raise ValueError("Unknown canonical fresh node")
    return list(dict.fromkeys(deps))


def validate_requested_node(
    spec: dict, requested: str, stage: str, role: str | None
) -> None:
    from probe.canonical_v2.fresh_publication import receipt_path

    expected = str(receipt_path(destination(spec, stage, role)))
    if requested != expected:
        raise ValueError(
            "Requested canonical output fingerprint does not match resolved sources"
        )
