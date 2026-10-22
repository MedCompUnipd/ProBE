# Canonical ProBE V2. Invoke explicitly with -s workflow/canonical_v2.smk.
# --config canonical_v2_spec=/absolute/path/to/pinned-spec.json
# No legacy Stage 05 publication or Stage 2C runner is consumed.
from pathlib import Path

from probe.canonical_v2.dag_contract import destination, read_dag_spec
from probe.canonical_v2.dag_execution import dispatch
from probe.workflow.artifacts import sha256_file

SPEC_PATH = Path(config.get("canonical_v2_spec", ""))
V2 = read_dag_spec(SPEC_PATH)
ROOT = destination(V2, "workload").parent
SCIROOT = destination(V2, "stage07").parent
START = "START"
END = "END"
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

wildcard_constraints:
    role="START|END"


def input_for(role, name):
    return V2["per_release"][role][name]


def fixed(name):
    return V2["inputs"][name]


def tool(name):
    return V2["tools"][name]


def node(stage, role=None):
    return str(destination(V2, stage, role))


def provenance_anchor(bundle):
    """Use each producer's real summary or manifest, not a fake manifest."""
    bundle = Path(bundle)
    kind = bundle.name if bundle.name in ANCHORS else bundle.parent.name
    if kind not in ANCHORS:
        raise ValueError(f"Unknown canonical publication: {bundle}")
    return bundle / ANCHORS[kind]


def receipt_path(bundle):
    """Snakemake metadata lives *beside*, never inside, a sealed bundle."""
    return Path(f"{bundle}.snakemake.done")


def receipt(bundle):
    """Fail closed if a recorded publication has disappeared or changed."""
    marker = receipt_path(bundle)
    if marker.exists() or marker.is_symlink():
        if marker.is_symlink() or not marker.is_file():
            raise ValueError(f"Invalid DAG receipt: {marker}")
        anchor = provenance_anchor(bundle)
        if (not anchor.is_file() or anchor.is_symlink()
                or Path(bundle).is_symlink()):
            raise ValueError(f"DAG receipt has no valid bundle: {bundle}")
        if marker.read_text(encoding="utf-8") != (
            sha256_file(anchor) + "\n"
        ):
            raise ValueError(f"DAG receipt provenance SHA256 mismatch: {bundle}")
    return str(marker)


def publish_receipt(bundle, marker):
    """Commit a tracked external receipt only after scientific publication."""
    bundle = Path(bundle)
    marker = Path(marker)
    anchor = provenance_anchor(bundle)
    if (not bundle.is_dir() or bundle.is_symlink()
            or not anchor.is_file() or anchor.is_symlink()
            or marker != receipt_path(bundle)):
        raise ValueError(f"Invalid canonical DAG publication: {bundle}")
    with marker.open("x", encoding="utf-8") as handle:
        handle.write(sha256_file(anchor) + "\n")


rule all:
    input:
        (
            receipt(node("temporal"))
            if V2["mode"] == "execute"
            else receipt(node("workload"))
        )


rule exact_registry:
    input:
        discovery=lambda wc: input_for(wc.role, "unified_discovery"),
        targets=fixed("targets"),
        fasta=lambda wc: input_for(wc.role, "full_manifest")
    output:
        receipt(str(ROOT / "exact" / "{role}"))
    run:
        dispatch(
            spec=V2, stage="exact", role=wildcards.role,
            target=Path(node("exact", wildcards.role)),
        )
        publish_receipt(
            Path(str(output[0]).removesuffix(".snakemake.done")),
            output[0],
        )


rule candidate_ledger:
    input:
        discovery=lambda wc: input_for(wc.role, "unified_discovery"),
        go=lambda wc: input_for(wc.role, "go_positive_pairs"),
        full=lambda wc: input_for(wc.role, "full_exact_pairs"),
        lineage=lambda wc: input_for(wc.role, "lineage_pairs"),
        rescue=lambda wc: input_for(wc.role, "rescue_pairs")
    output:
        receipt(str(ROOT / "ledger" / "{role}"))
    run:
        dispatch(
            spec=V2, stage="ledger", role=wildcards.role,
            target=Path(node("ledger", wildcards.role)),
        )
        publish_receipt(
            Path(str(output[0]).removesuffix(".snakemake.done")),
            output[0],
        )


rule candidate_projection:
    input:
        ledger=receipt(str(ROOT / "ledger" / "{role}"))
    output:
        receipt(str(ROOT / "projection" / "{role}"))
    run:
        dispatch(spec=V2, stage="projection", role=wildcards.role,
                 target=Path(node("projection", wildcards.role)))
        publish_receipt(
            Path(str(output[0]).removesuffix(".snakemake.done")),
            output[0],
        )


rule documentary_lookup:
    input:
        start=receipt(node("exact", START)),
        end=receipt(node("exact", END)),
        targets=fixed("targets"),
        taxonomy=lambda wc: input_for(wc.role, "taxonomy"),
        ox=lambda wc: input_for(wc.role, "ox_taxonomy"),
        full=lambda wc: input_for(wc.role, "full_manifest"),
        index=fixed("lineage_index")
    output:
        receipt(str(ROOT / "lookup" / "{role}"))
    run:
        dispatch(spec=V2, stage="lookup", role=wildcards.role,
                 target=Path(node("lookup", wildcards.role)))
        publish_receipt(
            Path(str(output[0]).removesuffix(".snakemake.done")),
            output[0],
        )


rule documentary_handoff:
    input:
        start=receipt(node("lookup", START)),
        end=receipt(node("lookup", END))
    output:
        receipt(node("handoff"))
    run:
        dispatch(spec=V2, stage="handoff", target=Path(node("handoff")))
        publish_receipt(
            Path(str(output[0]).removesuffix(".snakemake.done")),
            output[0],
        )


rule stage05_06a_seed:
    input:
        start=receipt(node("projection", START)),
        end=receipt(node("projection", END)),
        targets=fixed("targets"),
        taxonomy_start=input_for(START, "taxonomy"),
        taxonomy_end=input_for(END, "taxonomy"),
        anchors_start=input_for(START, "anchors"),
        anchors_end=input_for(END, "anchors"),
        fasta_start=input_for(START, "full_manifest"),
        fasta_end=input_for(END, "full_manifest"),
        taxonomy_cli=tool("stage05c2_cli"),
        alignment_cli=tool("stage06a_cli")
    output:
        receipt(node("b3"))
    run:
        dispatch(spec=V2, stage="b3", target=Path(node("b3")))
        publish_receipt(
            Path(str(output[0]).removesuffix(".snakemake.done")),
            output[0],
        )


rule complete_release_workload:
    input:
        b3=receipt(node("b3")),
        documentary=receipt(node("handoff")),
        targets=fixed("targets"),
        fasta_start=input_for(START, "full_manifest"),
        fasta_end=input_for(END, "full_manifest"),
        alignment_cli=tool("stage06a_cli")
    output:
        receipt(node("workload"))
    run:
        dispatch(spec=V2, stage="workload", target=Path(node("workload")))
        publish_receipt(
            Path(str(output[0]).removesuffix(".snakemake.done")),
            output[0],
        )


rule release_local_dp:
    input:
        workload=receipt(node("workload")),
        targets=fixed("targets"),
        taxonomy_start=input_for(START, "taxonomy"),
        taxonomy_end=input_for(END, "taxonomy"),
        lineage_index=fixed("lineage_index"),
        lineage_start=input_for(START, "lineage"),
        lineage_end=input_for(END, "lineage"),
        nonexact=tool("nonexact_script"),
        binary=tool("full_dp_binary"),
        linear_binary=(
            [V2["large_dp"]["binary"]] if "large_dp" in V2 else []
        ),
        finalize=tool("finalize_script")
    output:
        receipt(node("alignment"))
    threads:
        V2["limits"]["threads"]
    resources:
        mem_mb=(
            V2.get("large_dp", {}).get("max_dp_memory_bytes", 0) + 999_999
        ) // 1_000_000
    run:
        dispatch(spec=V2, stage="alignment", target=Path(node("alignment")))
        publish_receipt(
            Path(str(output[0]).removesuffix(".snakemake.done")),
            output[0],
        )


rule scientific_resolver:
    input:
        workload=receipt(node("workload")),
        dp=receipt(node("alignment")),
        targets=fixed("targets"),
        index=fixed("lineage_index"),
        taxonomy_start=input_for(START, "taxonomy"),
        taxonomy_end=input_for(END, "taxonomy"),
        anchors_start=input_for(START, "anchors"),
        anchors_end=input_for(END, "anchors"),
        lineage_start=input_for(START, "lineage"),
        lineage_end=input_for(END, "lineage")
    output:
        receipt(node("stage07"))
    run:
        dispatch(spec=V2, stage="stage07", target=Path(node("stage07")))
        publish_receipt(
            Path(str(output[0]).removesuffix(".snakemake.done")),
            output[0],
        )


rule goa_collection:
    input:
        stage07=receipt(node("stage07")),
        start=input_for(START, "goa_bundle"),
        end=input_for(END, "goa_bundle"),
        policy=fixed("go_policy_bundle"),
        cli=tool("collection_cli")
    output:
        receipt(node("m8"))
    run:
        dispatch(spec=V2, stage="m8", target=Path(node("m8")))
        publish_receipt(
            Path(str(output[0]).removesuffix(".snakemake.done")),
            output[0],
        )


rule m10c_interpretation:
    input:
        stage07=receipt(node("stage07")),
        m8=receipt(node("m8")),
        start=input_for(START, "goa_bundle"),
        end=input_for(END, "goa_bundle"),
        policy=fixed("go_policy_bundle"),
        ontology=fixed("ontology"),
        cli=tool("m10c_cli")
    output:
        receipt(node("m10c"))
    run:
        dispatch(spec=V2, stage="m10c", target=Path(node("m10c")))
        publish_receipt(
            Path(str(output[0]).removesuffix(".snakemake.done")),
            output[0],
        )


rule temporal_v2_benchmark:
    input:
        m10c=receipt(node("m10c")),
        m8=receipt(node("m8")),
        targets=fixed("targets"),
        original=fixed("targets_input"),
        registry=fixed("evidence_registry"),
        policy=fixed("temporal_policy"),
        ontology=fixed("ontology"),
        cli=tool("temporal_cli")
    output:
        receipt(node("temporal"))
    run:
        dispatch(spec=V2, stage="temporal", target=Path(node("temporal")))
        publish_receipt(
            Path(str(output[0]).removesuffix(".snakemake.done")),
            output[0],
        )
