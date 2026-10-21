# Fresh raw-input canonical V2 workflow. No prepared 03/04/05 publications.
from pathlib import Path

from probe.canonical_v2.dag_contract import destination, read_dag_spec
from probe.canonical_v2.fresh_dag import (
    all_raw_manifests,
    all_raw_outputs,
    ROLE_NODES,
    COMMON_NODES,
    ROLES,
    make_plan,
    node_dependencies,
    plan_code_inputs,
    plan_locations,
    read_recipe,
    resolve_spec,
    validate_requested_node,
)
from probe.canonical_v2.fresh_publication import execute_node, receipt_path
from probe.workflow.config import PipelineConfig

PROJECT_ROOT = Path(workflow.basedir).parent.resolve()
if set(config) != {"fresh_recipe"}:
    raise ValueError("Use --config fresh_recipe=/absolute/path/recipe.json only")
FRESH = read_recipe(Path(config["fresh_recipe"]), expected_repo=PROJECT_ROOT)
# The DAG owns dependencies; scientific audits are not parse-time gates.
FRESH_CFG = PipelineConfig.from_mapping(FRESH.pipeline)
PLAN_ROOT = FRESH.run / "plans"
CANONICAL_ROOT = FRESH.run / "canonical"
SPEC_PATH = FRESH.plan_path("canonical_spec")
PROVENANCE_PATH = PLAN_ROOT / "canonical_spec.provenance.json"
SPECIES_BINARY = str(PROJECT_ROOT / ".pixi/build/probe-primary-candidate-scan-v2/probe-primary-candidate-scan-v2")
FULL_EXACT_BINARY = str(PROJECT_ROOT / ".pixi/build/probe-full-exact-cross-strain/probe-full-exact-cross-strain-v1")


def resolved_spec():
    job = checkpoints.fresh_canonical_spec.get()
    return read_dag_spec(Path(job.output.spec))


def canonical_completion_receipts(wildcards):
    spec = resolved_spec()
    nodes = [(stage, role) for stage in ROLE_NODES for role in ROLES]
    nodes.extend((stage, None) for stage in COMMON_NODES)
    if len(nodes) != 16 or len(set(nodes)) != 16:
        raise ValueError("Incomplete or duplicate canonical completion nodes")
    return [
        str(receipt_path(destination(spec, stage, role)))
        for stage, role in nodes
    ]


rule all:
    input:
        raw=all_raw_outputs(FRESH),
        canonical=canonical_completion_receipts
    run:
        # Scientific certification is executed once by verify_fresh_run.py,
        # after Snakemake has successfully completed all declared jobs.
        if len(input.canonical) != 16:
            raise ValueError("Canonical DAG is missing completion nodes")


# Imported implementations are the production scripts. Configfile directives
# inside this module are ignored by Snakemake in favor of this explicit mapping.
# Only the required upstream rules are imported; native tools are pinned inputs.
module raw_upstream:
    snakefile:
        "Snakefile"
    config:
        FRESH.pipeline


use rule stage_01 from raw_upstream as fresh_raw_01 with:
    output:
        FRESH.outputs("01")
    resources:
        mem_mb=FRESH_CFG.resources("01")["mem_mb"],
        runtime=FRESH_CFG.resources("01")["runtime_minutes"],
        dataset_io=1

use rule stage_01A_FULL from raw_upstream as fresh_raw_01A_FULL with:
    output:
        FRESH.outputs("01A_FULL", "{role}")
    resources:
        mem_mb=FRESH_CFG.resources("01A_FULL")["mem_mb"],
        runtime=FRESH_CFG.resources("01A_FULL")["runtime_minutes"],
        dataset_io=1

use rule stage_01B0 from raw_upstream as fresh_raw_01B0 with:
    output:
        FRESH.outputs("01B0")
    resources:
        mem_mb=FRESH_CFG.resources("01B0")["mem_mb"],
        runtime=FRESH_CFG.resources("01B0")["runtime_minutes"],
        dataset_io=1

use rule stage_01B1 from raw_upstream as fresh_raw_01B1 with:
    output:
        FRESH.outputs("01B1", "{role}")
    resources:
        mem_mb=FRESH_CFG.resources("01B1")["mem_mb"],
        runtime=FRESH_CFG.resources("01B1")["runtime_minutes"],
        dataset_io=1

use rule stage_01C from raw_upstream as fresh_raw_01C with:
    output:
        FRESH.outputs("01C", "{role}")
    resources:
        mem_mb=FRESH_CFG.resources("01C")["mem_mb"],
        runtime=FRESH_CFG.resources("01C")["runtime_minutes"],
        dataset_io=1

use rule stage_02A from raw_upstream as fresh_raw_02A with:
    output:
        FRESH.outputs("02A", "{role}")
    resources:
        mem_mb=FRESH_CFG.resources("02A")["mem_mb"],
        runtime=FRESH_CFG.resources("02A")["runtime_minutes"],
        dataset_io=1

use rule stage_02B from raw_upstream as fresh_raw_02B with:
    output:
        FRESH.outputs("02B", "{role}")
    resources:
        mem_mb=FRESH_CFG.resources("02B")["mem_mb"],
        runtime=FRESH_CFG.resources("02B")["runtime_minutes"],
        dataset_io=1

use rule stage_03 from raw_upstream as fresh_raw_03 with:
    output:
        FRESH.outputs("03", "{role}")
    resources:
        mem_mb=FRESH_CFG.resources("03")["mem_mb"],
        runtime=FRESH_CFG.resources("03")["runtime_minutes"],
        dataset_io=1

use rule stage_04A from raw_upstream as fresh_raw_04A with:
    output:
        FRESH.outputs("04A", "{role}")
    resources:
        mem_mb=FRESH_CFG.resources("04A")["mem_mb"],
        runtime=FRESH_CFG.resources("04A")["runtime_minutes"],
        dataset_io=1

use rule stage_04B from raw_upstream as fresh_raw_04B with:
    output:
        FRESH.outputs("04B", "{role}")
    resources:
        mem_mb=FRESH_CFG.resources("04B")["mem_mb"],
        runtime=FRESH_CFG.resources("04B")["runtime_minutes"],
        dataset_io=1

use rule stage_04C from raw_upstream as fresh_raw_04C with:
    output:
        FRESH.outputs("04C")
    resources:
        mem_mb=FRESH_CFG.resources("04C")["mem_mb"],
        runtime=FRESH_CFG.resources("04C")["runtime_minutes"],
        dataset_io=1

use rule stage_04D0a from raw_upstream as fresh_raw_04D0a with:
    output:
        FRESH.outputs("04D0a", "{role}")
    resources:
        mem_mb=FRESH_CFG.resources("04D0a")["mem_mb"],
        runtime=FRESH_CFG.resources("04D0a")["runtime_minutes"],
        dataset_io=1

use rule stage_04D0b from raw_upstream as fresh_raw_04D0b with:
    output:
        FRESH.outputs("04D0b", "{role}")
    resources:
        mem_mb=FRESH_CFG.resources("04D0b")["mem_mb"],
        runtime=FRESH_CFG.resources("04D0b")["runtime_minutes"],
        dataset_io=1

use rule stage_04D1 from raw_upstream as fresh_raw_04D1 with:
    output:
        FRESH.outputs("04D1")
    resources:
        mem_mb=FRESH_CFG.resources("04D1")["mem_mb"],
        runtime=FRESH_CFG.resources("04D1")["runtime_minutes"],
        dataset_io=1

use rule stage_04D2 from raw_upstream as fresh_raw_04D2 with:
    output:
        FRESH.outputs("04D2", "{role}")
    resources:
        mem_mb=FRESH_CFG.resources("04D2")["mem_mb"],
        runtime=FRESH_CFG.resources("04D2")["runtime_minutes"],
        dataset_io=1

use rule stage_04E from raw_upstream as fresh_raw_04E with:
    output:
        FRESH.outputs("04E", "{role}")
    resources:
        mem_mb=FRESH_CFG.resources("04E")["mem_mb"],
        runtime=FRESH_CFG.resources("04E")["runtime_minutes"],
        dataset_io=1

use rule stage_05A from raw_upstream as fresh_raw_05A with:
    output:
        FRESH.outputs("05A")
    resources:
        mem_mb=FRESH_CFG.resources("05A")["mem_mb"],
        runtime=FRESH_CFG.resources("05A")["runtime_minutes"],
        dataset_io=1

use rule stage_05B from raw_upstream as fresh_raw_05B with:
    output:
        FRESH.outputs("05B")
    resources:
        mem_mb=FRESH_CFG.resources("05B")["mem_mb"],
        runtime=FRESH_CFG.resources("05B")["runtime_minutes"],
        dataset_io=1

use rule stage_05R0 from raw_upstream as fresh_raw_05R0 with:
    output:
        FRESH.outputs("05R0", "{role}")
    resources:
        mem_mb=FRESH_CFG.resources("05R0")["mem_mb"],
        runtime=FRESH_CFG.resources("05R0")["runtime_minutes"],
        dataset_io=1

use rule stage_05R1 from raw_upstream as fresh_raw_05R1 with:
    output:
        FRESH.outputs("05R1", "{role}")
    resources:
        mem_mb=FRESH_CFG.resources("05R1")["mem_mb"],
        runtime=FRESH_CFG.resources("05R1")["runtime_minutes"],
        dataset_io=1


wildcard_constraints:
    role="START|END",
    discovery_key="[a-f0-9]{64}",
    fingerprint="[a-f0-9]{20}",
    kind="discovery|policy"


checkpoint fresh_species_plan:
    input:
        recipe=str(FRESH.path),
        pipeline=FRESH.data["pipeline_config"],
        targets=FRESH.manifest("01"),
        positive=[FRESH.manifest("01C", role) for role in ("START", "END")],
        taxonomy=[FRESH.manifest("02B", role) for role in ("START", "END")],
        binary=SPECIES_BINARY,
        implementation=plan_code_inputs(FRESH, "species")
    output:
        str(FRESH.plan_path("species"))
    run:
        make_plan(FRESH, "species", Path(output[0]))


checkpoint fresh_full_exact_plan:
    input:
        recipe=str(FRESH.path),
        targets=FRESH.manifest("01"),
        full=[FRESH.manifest("01A_FULL", role) for role in ("START", "END")],
        taxonomy=[FRESH.manifest("02B", role) for role in ("START", "END")],
        ox=[FRESH.manifest("04D0b", role) for role in ("START", "END")],
        binary=FULL_EXACT_BINARY,
        implementation=plan_code_inputs(FRESH, "full_exact")
    output:
        str(FRESH.plan_path("full_exact"))
    run:
        make_plan(FRESH, "full_exact", Path(output[0]))


checkpoint fresh_union_plan:
    input:
        species=str(FRESH.plan_path("species")),
        full=str(FRESH.plan_path("full_exact")),
        implementation=plan_code_inputs(FRESH, "union")
    output:
        str(FRESH.plan_path("union"))
    run:
        make_plan(FRESH, "union", Path(output[0]))


def checkpoint_plan(kind):
    selected_checkpoint = {
        "species": checkpoints.fresh_species_plan,
        "full_exact": checkpoints.fresh_full_exact_plan,
        "union": checkpoints.fresh_union_plan,
    }[kind]
    job = selected_checkpoint.get()
    return plan_locations(FRESH, kind, Path(job.output[0]))


def checked_plan_path(wildcards, kind):
    plan = checkpoint_plan(kind)
    if wildcards.discovery_key != plan["key"]:
        raise ValueError("Requested discovery output has a foreign fingerprint")
    return str(FRESH.plan_path(kind))


def union_input(wildcards, channel):
    plan = checkpoint_plan("union")
    if wildcards.discovery_key != plan["key"]:
        raise ValueError("Requested discovery union has a foreign fingerprint")
    return str(Path(plan[channel + "_paths"][wildcards.role]) / "manifest.json")


rule fresh_species_scan:
    input:
        plan=lambda w: checked_plan_path(w, "species"),
        target_manifest=FRESH.manifest("01"),
        go_positive_manifest=FRESH.manifest("01C", "{role}"),
        taxonomy_manifest=FRESH.manifest("02B", "{role}"),
        binary=SPECIES_BINARY
    output:
        manifest=str(FRESH.run / "work/pipeline-v2/{role}/03_species_wide_variants/{discovery_key}/manifest.json"),
        pairs=str(FRESH.run / "work/pipeline-v2/{role}/03_species_wide_variants/{discovery_key}/03_primary_candidates.pairs.tsv"),
        unsearchable=str(FRESH.run / "work/pipeline-v2/{role}/03_species_wide_variants/{discovery_key}/03_primary_candidates.unsearchable_targets.tsv"),
        summary=str(FRESH.run / "work/pipeline-v2/{role}/03_species_wide_variants/{discovery_key}/03_primary_candidates.summary.tsv"),
        outcome=str(FRESH.run / "work/pipeline-v2/{role}/03_species_wide_variants/{discovery_key}/stage_outcome.json")
    log:
        str(FRESH.run / "logs/pipeline-v2/{role}/03_species_wide/{discovery_key}.log")
    params:
        role=lambda w: w.role,
        plan_path=str(FRESH.plan_path("species"))
    threads:
        FRESH_CFG.resources("03")["threads"]
    resources:
        mem_mb=FRESH_CFG.resources("03")["mem_mb"],
        dataset_io=1
    script:
        "scripts/run_stage_03_species_wide.py"


rule fresh_full_exact_scan:
    input:
        plan=lambda w: checked_plan_path(w, "full_exact"),
        target_manifest=FRESH.manifest("01"),
        full_manifest=FRESH.manifest("01A_FULL", "{role}"),
        taxonomy_manifest=FRESH.manifest("02B", "{role}"),
        ox_manifest=FRESH.manifest("04D0b", "{role}"),
        binary=FULL_EXACT_BINARY
    output:
        manifest=str(FRESH.run / "work/pipeline-v2/{role}/03_full_exact_variants/{discovery_key}/manifest.json"),
        pairs=str(FRESH.run / "work/pipeline-v2/{role}/03_full_exact_variants/{discovery_key}/03_full_exact_cross_strain.pairs.tsv"),
        summary=str(FRESH.run / "work/pipeline-v2/{role}/03_full_exact_variants/{discovery_key}/03_full_exact_cross_strain.summary.tsv")
    log:
        str(FRESH.run / "logs/pipeline-v2/{role}/03_full_exact/{discovery_key}.log")
    params:
        role=lambda w: w.role,
        plan_path=str(FRESH.plan_path("full_exact"))
    threads:
        FRESH_CFG.resources("03")["threads"]
    resources:
        mem_mb=FRESH_CFG.resources("03")["mem_mb"],
        dataset_io=1
    script:
        "scripts/run_stage_03_full_exact.py"


rule fresh_discovery_union:
    input:
        plan=lambda w: checked_plan_path(w, "union"),
        go_manifest=lambda w: union_input(w, "species"),
        full_manifest=lambda w: union_input(w, "full"),
        taxonomy_manifest=FRESH.manifest("02B", "{role}")
    output:
        union_manifest=str(FRESH.run / "work/pipeline-v2/{role}/03_discovery_union_variants/{discovery_key}/manifest.json"),
        pairs=str(FRESH.run / "work/pipeline-v2/{role}/03_discovery_union_variants/{discovery_key}/03_unified_discovery.tsv"),
        summary=str(FRESH.run / "work/pipeline-v2/{role}/03_discovery_union_variants/{discovery_key}/03_unified_discovery.summary.json")
    log:
        str(FRESH.run / "logs/pipeline-v2/{role}/03_discovery_union/{discovery_key}.log")
    params:
        role=lambda w: w.role,
        plan_path=str(FRESH.plan_path("union"))
    resources:
        dataset_io=1
    script:
        "scripts/run_stage_03_discovery_union.py"


def union_manifests(wildcards):
    plan = checkpoint_plan("union")
    return [str(Path(plan["paths"][role]) / "manifest.json") for role in ("START", "END")]


checkpoint fresh_canonical_spec:
    input:
        recipe=str(FRESH.path),
        upstream=all_raw_manifests(FRESH),
        discovery=union_manifests,
        targets_input=FRESH.data["targets_input"],
        evidence_registry=FRESH.data["evidence_registry"],
        temporal_policy=FRESH.data["temporal_policy"],
        implementation=[str(PROJECT_ROOT / name) for name in ("src/probe/canonical_v2/fresh_dag.py", "src/probe/canonical_v2/dag_contract.py")]
    output:
        spec=str(SPEC_PATH),
        provenance=str(PROVENANCE_PATH)
    resources:
        dataset_io=1
    run:
        resolve_spec(FRESH, Path(output.spec), Path(output.provenance))


def canonical_dependencies(wildcards):
    spec = resolved_spec()
    role = getattr(wildcards, "role", None)
    requested = CANONICAL_ROOT / ("v2-" + wildcards.kind + "-" + wildcards.fingerprint) / wildcards.stage
    if role is not None:
        requested = requested / role
    validate_requested_node(spec, str(receipt_path(requested)), wildcards.stage, role)
    return [str(SPEC_PATH), str(PROVENANCE_PATH), str(FRESH.path), *node_dependencies(spec, wildcards.stage, role)]


rule fresh_canonical_role:
    input:
        canonical_dependencies
    output:
        str(CANONICAL_ROOT / "v2-{kind}-{fingerprint}/{stage}/{role}.snakemake.done")
    wildcard_constraints:
        stage="exact|ledger|projection|lookup"
    resources:
        dataset_io=1
    run:
        spec = read_dag_spec(SPEC_PATH)
        validate_requested_node(spec, str(output[0]), wildcards.stage, wildcards.role)
        execute_node(spec=spec, stage=wildcards.stage, role=wildcards.role, recipe_sha256=FRESH.sha256)


rule fresh_canonical_common:
    input:
        canonical_dependencies
    output:
        str(CANONICAL_ROOT / "v2-{kind}-{fingerprint}/{stage}.snakemake.done")
    wildcard_constraints:
        stage="handoff|b3|workload|alignment|stage07|m8|m10c|temporal"
    threads:
        lambda w: FRESH.data["limits"]["threads"] if w.stage == "alignment" else 1
    resources:
        dataset_io=1
    run:
        spec = read_dag_spec(SPEC_PATH)
        validate_requested_node(spec, str(output[0]), wildcards.stage, None)
        execute_node(spec=spec, stage=wildcards.stage, role=None, recipe_sha256=FRESH.sha256)

