"""Immutable, opt-in resolver experiment specifications (Stage 07 onward).

The canonical published pipeline is never an output of this module.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import uuid
from datetime import UTC, datetime
from pathlib import Path

import yaml

from probe.workflow.config import PipelineConfig

SCHEMA = "probe-stage07-resolver-experiment-v1"
BASELINE = "baseline-evidence-e2080d08aa73"
OUTPUT_ROOTS = {
    "stage07": "07_resolver_variants",
    "stage08": "08_resolver_variants",
    "m10c": "08_compact_resolver_variants",
    "benchmark": "08_benchmark_resolver_variants",
    "diagnostics": "08_diagnostics_resolver_variants",
}
SOURCE_MANIFESTS = {
    "targets": ("work", "common/01"),
    "primary": ("results", "common/05A2"),
    "audit": ("results", "common/05C2"),
    "start_taxonomy": ("work", "START/02B"),
    "end_taxonomy": ("work", "END/02B"),
    "start_anchors": ("work", "START/04B"),
    "end_anchors": ("work", "END/04B"),
    "lineage_index": ("work", "common/04D1"),
    "start_lineage": ("work", "START/04D2"),
    "end_lineage": ("work", "END/04D2"),
    "jobs": ("work", "common/06A"),
    "alignments": ("results", "common/06C1"),
    "start_goa": ("work", "START/01B1"),
    "end_goa": ("work", "END/01B1"),
    "go_policy": ("work", "common/01B0"),
    "canonical_stage07": ("results", "common/07"),
}
CODE_GROUPS = {
    "stage07": (
        "workflow/scripts/run_stage_07.py",
        "scripts/pipeline/07_resolve_candidate_associations.py",
        "src/probe/resolver.py",
        "src/probe/resolver_alignments.py",
        "src/probe/resolver_anchors.py",
        "src/probe/resolver_assembly.py",
        "src/probe/resolver_candidates.py",
        "src/probe/resolver_lineage.py",
        "src/probe/resolver_metadata.py",
        "src/probe/stage07_variants.py",
    ),
    "stage08": (
        "workflow/scripts/run_stage_08_resolver_variant.py",
        "scripts/pipeline/08_collect_annotations.py",
        "src/probe/stage08_annotations.py",
        "src/probe/stage08_collection_publish.py",
        "src/probe/stage08_collection_inputs.py",
        "src/probe/stage08_gaf_collection.py",
        "src/probe/stage08_donors.py",
    ),
    "m10c": (
        "workflow/scripts/run_stage_08_compact_resolver_variant.py",
        "scripts/pipeline/08_interpret_compact.py",
        "src/probe/stage08_compact_interpretation.py",
        "src/probe/stage08_m8_adapter.py",
        "src/probe/stage08_go_supports.py",
        "src/probe/stage08_go_closure.py",
    ),
    "benchmark": (
        "workflow/scripts/run_stage_08_benchmark_resolver_variant.py",
        "scripts/pipeline/08_build_benchmark_v2.py",
        "src/probe/stage08_temporal_v2.py",
        "src/probe/stage08_temporal_benchmark.py",
        "src/probe/stage08_evidence_registry.py",
    ),
    "diagnostics": (
        "workflow/scripts/run_stage_08_diagnostics_resolver_variant.py",
        "scripts/pipeline/08_profile_benchmark.py",
    ),
}


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(1024 * 1024):
            h.update(block)
    return h.hexdigest()


def fingerprint(obj: object) -> str:
    data = json.dumps(obj, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(data.encode("utf-8")).hexdigest()


def safe_file(path: Path) -> Path:
    """Require a physical file for repository code and experiment metadata."""
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"Not a physical regular file: {path}")
    return path.resolve(strict=True)


def safe_input_file(path: Path) -> Path:
    """Resolve input symlinks, including chains, to an existing regular file."""
    try:
        real = path.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise ValueError(f"Missing or invalid input file: {path}") from exc
    if not real.is_file():
        raise ValueError(f"Not a regular input file: {path}")
    return real


def verify_input_file(path: Path, realpath: str, checksum: str) -> None:
    """Fail closed if an input link changes target or payload."""
    resolved = safe_input_file(path)
    if str(resolved) != realpath:
        raise ValueError(f"Input link destination changed: {path}")
    if sha256(resolved) != checksum:
        raise ValueError(f"Input content changed: {path}")


def _json(path: Path) -> dict:
    obj = json.loads(safe_file(path).read_text(encoding="utf-8"))
    if not isinstance(obj, dict):
        raise ValueError(f"Expected JSON mapping: {path}")
    return obj


def _sources(run: Path) -> dict[str, str]:
    roots = {
        "work": run / "work/pipeline-v2",
        "results": run / "results/pipeline-v2",
    }
    found = {}
    for name, (root, relative) in SOURCE_MANIFESTS.items():
        path = roots[root] / relative / "manifest.json"
        safe_input_file(path)
        found[name] = str(path)
    return found


def _code(repo: Path) -> dict[str, str]:
    return {
        path: sha256(safe_file(repo / path))
        for files in CODE_GROUPS.values()
        for path in files
    }


def _hash_code(code: dict, group: str) -> list[str]:
    return [code[path] for path in CODE_GROUPS[group]]


def _keys(policy: dict, baseline: dict) -> dict[str, str]:
    stage07 = fingerprint(
        {
            "science": "stage07-resolver-v1",
            "policy": policy,
            "upstream": {
                k: v
                for k, v in baseline["source_hashes"].items()
                if k not in {"canonical_stage07", "start_goa", "end_goa", "go_policy"}
            },
            "code": _hash_code(baseline["code_hashes"], "stage07"),
            "ontology": baseline["ontology_sha256"],
        }
    )
    stage08 = fingerprint(
        {
            "stage07": stage07,
            "goa": {
                k: baseline["source_hashes"][k]
                for k in ("start_goa", "end_goa", "go_policy")
            },
            "code": _hash_code(baseline["code_hashes"], "stage08"),
        }
    )
    m10c = fingerprint(
        {
            "stage08": stage08,
            "ontology": baseline["ontology_sha256"],
            "code": _hash_code(baseline["code_hashes"], "m10c"),
        }
    )
    bench = fingerprint(
        {
            "m10c": m10c,
            "registry": baseline["registry_sha256"],
            "evidence_policy": baseline["evidence_policy_sha256"],
            "code": _hash_code(baseline["code_hashes"], "benchmark"),
        }
    )
    diag = fingerprint(
        {
            "benchmark": bench,
            "code": _hash_code(baseline["code_hashes"], "diagnostics"),
        }
    )
    return dict(
        stage07=stage07, stage08=stage08, m10c=m10c, benchmark=bench, diagnostics=diag
    )


def _baseline(run: Path) -> tuple[Path, Path, str, str]:
    from probe.stage08_variants import read_spec

    old = read_spec(
        run / "experiments" / BASELINE / "config.resolved.json",
        check_sources=True,
    )
    return (
        safe_file(Path(old["policy"])),
        safe_file(Path(old["registry"])),
        old["policy_sha256"],
        old["registry_sha256"],
    )


def create(repo: Path, run: Path, name: str, policy: dict) -> dict:
    repo = repo.resolve(strict=True)
    run = run.resolve(strict=True)
    if not re.fullmatch(r"[a-z][a-z0-9-]{1,38}[a-z0-9]", name):
        raise ValueError("Name requires 3..40 lowercase letters/digits/hyphens")
    if set(policy) != {"resolver", "annotation_donor"}:
        raise ValueError("Resolver policy requires exactly two groups")
    config_path = run / "pipeline.stage08.collection.yaml"
    base = yaml.safe_load((repo / "config/pipeline.yaml").read_text())
    override = yaml.safe_load(safe_input_file(config_path).read_text())
    from probe.stage08_variants import _merge

    resolved = _merge(base, override)
    resolved.update(policy)
    validated = PipelineConfig.from_mapping(resolved)
    policy = validated.stage07_parameters()
    original = PipelineConfig.from_mapping(_merge(base, override))
    if policy == original.stage07_parameters():
        raise ValueError("Resolver policy matches canonical baseline")
    if (
        subprocess.check_output(
            ["git", "branch", "--show-current"], cwd=repo, text=True
        ).strip()
        != "chatgpt/pre-resolver-v2"
    ):
        raise ValueError("Wrong development branch")
    status = subprocess.check_output(
        ["git", "status", "--porcelain"], cwd=repo, text=True
    )
    if status.strip():
        raise ValueError("Commit reviewed changes before creating experiments")
    git_sha = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=repo, text=True
    ).strip()
    sources = _sources(run)
    hashes = {key: sha256(Path(path)) for key, path in sources.items()}
    canonical = _json(safe_input_file(Path(sources["canonical_stage07"])))
    if canonical.get("parameters", {}).get("policies") != (
        original.stage07_parameters()
    ):
        raise ValueError("Canonical Stage 07 differs from baseline workflow policy")
    ontology_path = Path(resolved["ontology"]["path"])
    if not ontology_path.is_absolute():
        ontology_path = run / ontology_path
    ontology = safe_input_file(ontology_path)
    go_sha = sha256(ontology)
    if go_sha != canonical.get("ontology", {}).get("sha256"):
        raise ValueError("GO differs from published Stage 07")
    if go_sha != "42d7869b3a47cd4a9751d7f2f373acf737b64a1435e4b02b169d134b240dc666":
        raise ValueError("Unexpected frozen ontology")
    evidence, registry, evidence_sha, registry_sha = _baseline(run)
    source_targets_path = run / "inputs/targets.tsv"
    stage01_targets_path = run / "work/pipeline-v2/common/01/01_targets.tsv"
    source_targets = safe_input_file(source_targets_path)
    stage01_targets = safe_input_file(stage01_targets_path)
    code_hashes = _code(repo)
    spec = {
        "schema_version": SCHEMA,
        "git_sha": git_sha,
        "repo": str(repo),
        "run": str(run),
        "policy": policy,
        "pipeline_config": resolved,
        "source_manifests": sources,
        "source_manifest_realpaths": {
            key: str(safe_input_file(Path(path))) for key, path in sources.items()
        },
        "source_hashes": hashes,
        "code_hashes": code_hashes,
        "ontology": str(ontology_path),
        "ontology_realpath": str(ontology),
        "ontology_sha256": go_sha,
        "target_file": str(source_targets_path),
        "target_realpath": str(source_targets),
        "stage01_targets": str(stage01_targets_path),
        "stage01_realpath": str(stage01_targets),
        "targets_sha256": sha256(source_targets),
        "stage01_sha256": sha256(stage01_targets),
        "evidence_policy": "",  # Set to experiment-owned snapshot below.
        "registry": "",
        "evidence_policy_sha256": evidence_sha,
        "registry_sha256": registry_sha,
        "releases": {
            role: validated.release(role)["release_id"] for role in ("START", "END")
        },
        "scientific_version": "stage07-resolver-v1",
        "created_at_utc": datetime.now(UTC).isoformat(),
        "state": "CREATED_NOT_EXECUTED",
    }
    keys = _keys(policy, spec)
    eid = f"{name}-{keys['stage07'][:12]}"
    exp = run / "experiments" / eid
    common = run / "results/pipeline-v2/common"
    spec["experiment_id"] = eid
    spec["evidence_policy"] = str(exp / "evidence_policy.json")
    spec["registry"] = str(exp / "registry.json")
    spec["keys"] = keys
    spec["paths"] = {
        key: str(common / OUTPUT_ROOTS[key] / value) for key, value in keys.items()
    }
    if exp.exists() or exp.is_symlink():
        raise FileExistsError(f"Experiment already exists: {exp}")
    exp.parent.mkdir(exist_ok=True)
    staging = exp.parent / f".new-{uuid.uuid4().hex}"
    staging.mkdir()
    try:
        shutil.copyfile(evidence, staging / "evidence_policy.json")
        shutil.copyfile(registry, staging / "registry.json")
        if sha256(staging / "evidence_policy.json") != evidence_sha:
            raise ValueError("Evidence snapshot changed during copying")
        if sha256(staging / "registry.json") != registry_sha:
            raise ValueError("Registry snapshot changed during copying")
        (staging / "config.resolved.json").write_text(
            json.dumps(spec, sort_keys=True, indent=2) + "\n",
            encoding="utf-8",
        )
        (staging / "experiment.yaml").write_text(
            yaml.safe_dump(
                {"resolver_variant_spec": str(exp / "config.resolved.json")}
            ),
            encoding="utf-8",
        )
        if exp.exists() or exp.is_symlink():
            raise FileExistsError("Concurrent experiment creation")
        os.rename(staging, exp)
    finally:
        if staging.exists():
            for item in staging.iterdir():
                if item.is_symlink() or not item.is_file():
                    raise RuntimeError("Unexpected temporary experiment entry")
                item.unlink()
            staging.rmdir()
    return spec


def read_spec(path: Path, *, check_sources: bool = True) -> dict:
    path = safe_file(path)
    spec = _json(path)
    if spec.get("schema_version") != SCHEMA:
        raise ValueError("Unknown resolver experiment schema")
    run = Path(spec["run"]).resolve(strict=True)
    exp = run / "experiments" / spec["experiment_id"]
    if path != exp / "config.resolved.json":
        raise ValueError("Resolver experiment outside its expected directory")
    if spec["keys"] != _keys(spec["policy"], spec):
        raise ValueError("Resolver fingerprint mismatch")
    common = run / "results/pipeline-v2/common"
    for name, key in spec["keys"].items():
        expected = common / OUTPUT_ROOTS[name] / key
        if Path(spec["paths"][name]) != expected:
            raise ValueError(f"Illegal resolver output destination: {name}")
    if check_sources:
        for name, path_str in spec["source_manifests"].items():
            verify_input_file(
                Path(path_str),
                spec["source_manifest_realpaths"][name],
                spec["source_hashes"][name],
            )
        repo = Path(spec["repo"])
        for rel, expected in spec["code_hashes"].items():
            if sha256(safe_file(repo / rel)) != expected:
                raise ValueError(f"Scientific implementation changed: {rel}")
        if spec["evidence_policy"] != str(exp / "evidence_policy.json"):
            raise ValueError("Evidence-policy snapshot location mismatch")
        if spec["registry"] != str(exp / "registry.json"):
            raise ValueError("Registry snapshot location mismatch")
        for path_str, realpath, expected in (
            (
                spec["ontology"],
                spec["ontology_realpath"],
                spec["ontology_sha256"],
            ),
            (
                spec["target_file"],
                spec["target_realpath"],
                spec["targets_sha256"],
            ),
            (
                spec["stage01_targets"],
                spec["stage01_realpath"],
                spec["stage01_sha256"],
            ),
        ):
            verify_input_file(Path(path_str), realpath, expected)
        for path_str, expected in (
            (spec["evidence_policy"], spec["evidence_policy_sha256"]),
            (spec["registry"], spec["registry_sha256"]),
        ):
            if sha256(safe_file(Path(path_str))) != expected:
                raise ValueError(f"Experiment snapshot changed: {path_str}")
    return spec


def list_variants(run: Path) -> list[dict]:
    root = run / "experiments"
    if not root.is_dir():
        return []
    result = []
    for item in sorted(root.iterdir()):
        if item.is_symlink() or not item.is_dir():
            continue
        config = item / "config.resolved.json"
        if not config.is_file() or config.is_symlink():
            continue
        spec = _json(config)
        if spec.get("schema_version") != SCHEMA:
            continue
        spec = read_spec(config, check_sources=False)
        result.append(
            {
                "experiment_id": spec["experiment_id"],
                "paths": spec["paths"],
                "published": {
                    key: (
                        Path(path)
                        / (
                            "publication_manifest.json"
                            if key == "diagnostics"
                            else "manifest.json"
                        )
                    ).is_file()
                    for key, path in spec["paths"].items()
                },
            }
        )
    return result


def verify_publications(spec: dict) -> dict[str, bool]:
    """Check existing manifests without hashing multi-gigabyte payloads.

    This is a fast provenance check, not an independent deep checksum audit.
    """
    from probe.workflow.artifacts import validate_bundle

    found = {}
    for kind, root in spec["paths"].items():
        path = Path(root)
        name = "publication_manifest.json" if kind == "diagnostics" else "manifest.json"
        manifest_file = path / name
        if path.is_symlink() or manifest_file.is_symlink():
            raise ValueError(f"Symlink resolver publication: {kind}")
        found[kind] = manifest_file.is_file()
        if path.exists() and not path.is_dir():
            raise ValueError(f"Non-directory resolver destination: {kind}")
    order = ("stage07", "stage08", "m10c", "benchmark", "diagnostics")
    for i, key in enumerate(order[1:], start=1):
        if found[key] and not found[order[i - 1]]:
            raise ValueError(f"Published {key} is missing its predecessor")

    if found["stage07"]:
        root = Path(spec["paths"]["stage07"])
        manifest = validate_bundle(root, deep=False)
        if (
            manifest.stage != "07"
            or manifest.parameters.get("policies") != spec["policy"]
            or manifest.parameters.get("start_release_id") != spec["releases"]["START"]
            or manifest.parameters.get("end_release_id") != spec["releases"]["END"]
            or manifest.ontology.get("sha256") != spec["ontology_sha256"]
        ):
            raise ValueError("Published Stage 07 resolver variant is incompatible")
    if found["stage08"]:
        from probe.stage08_collection_inputs import prepare_collection_inputs

        root = Path(spec["paths"]["stage08"])
        m8 = validate_bundle(root, deep=False)
        work = Path(spec["run"]) / "work/pipeline-v2"
        inputs = prepare_collection_inputs(
            stage07_bundle=Path(spec["paths"]["stage07"]),
            start_goa_bundle=work / "START/01B1",
            end_goa_bundle=work / "END/01B1",
            go_policy_bundle=work / "common/01B0",
        )
        if (
            m8.stage != "08"
            or m8.parameters.get("input_fingerprint") != inputs.fingerprint
        ):
            raise ValueError("Published variant M8 has incompatible donors")
    if found["m10c"]:
        m8_manifest = _json(Path(spec["paths"]["stage08"]) / "manifest.json")
        m8_outputs = {row["path"]: row["sha256"] for row in m8_manifest["outputs"]}
        m10c = _json(Path(spec["paths"]["m10c"]) / "manifest.json")
        if (
            m10c.get("schema_version") != "stage08-m10c-compact-interpretation-v1"
            or m10c.get("ontology_sha256") != spec["ontology_sha256"]
        ):
            raise ValueError("Published M10c variant has incompatible GO")
        for role in ("START", "END"):
            evidence = m10c["source_payloads"][role]
            prefix = f"08_{role.lower()}_"
            if (
                evidence["links_sha256"] != m8_outputs[f"{prefix}annotation_links.tsv"]
                or evidence["source_sha256"]
                != m8_outputs[f"{prefix}source_assertions.tsv"]
            ):
                raise ValueError("Published M10c does not match variant M8")
    if found["benchmark"]:
        m12 = _json(Path(spec["paths"]["benchmark"]) / "manifest.json")
        m10c_file = Path(spec["paths"]["m10c"]) / "manifest.json"
        m8_file = Path(spec["paths"]["stage08"]) / "08_collection_summary.json"
        if (
            m12.get("m10c_manifest_sha256") != sha256(m10c_file)
            or m12.get("m8_summary_sha256") != sha256(m8_file)
            or m12.get("ontology_sha256") != spec["ontology_sha256"]
            or m12.get("policy_sha256") != spec["evidence_policy_sha256"]
            or m12.get("start_release") != spec["releases"]["START"]
            or m12.get("end_release") != spec["releases"]["END"]
        ):
            raise ValueError("Published M12 resolver benchmark is incompatible")
    if found["diagnostics"]:
        diag_file = Path(spec["paths"]["diagnostics"])
        diagnostics = _json(diag_file / "publication_manifest.json")
        bench = Path(spec["paths"]["benchmark"]) / "manifest.json"
        if diagnostics.get("benchmark_manifest_sha256") != sha256(bench):
            raise ValueError("Published resolver diagnostics mismatch M12")
    return found
