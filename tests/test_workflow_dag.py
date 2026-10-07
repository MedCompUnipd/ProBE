"""Exercise Snakemake itself on tiny source placeholders, never scientific jobs."""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
SNAKEFILE = ROOT / "workflow/Snakefile"


def workflow_config(tmp_path: Path, *, adapter: bool = False) -> Path:
    data = yaml.safe_load((ROOT / "config/pipeline.yaml").read_text())
    sources = [data["ontology"]["path"]]
    data["releases"]["START"]["release_id"] = "arbitrary-A"
    data["releases"]["END"]["release_id"] = "unrelated-B"
    for release in data["releases"].values():
        sources += release["uniprot_sources"] + [release["goa"]]
        sources += [v for k, v in release["taxonomy"].items() if k != "snapshot_id"]
    if adapter:
        data["targets"]["mode"] = "adapter"
        data["targets"]["adapter"] = {
            "name": "fixture-adapter",
            "version": "1",
            "implementation": "fixture_adapter.py",
            "inputs": ["raw.txt"],
            "taxid_contract": "explicit field",
            "id_policy": "source ordinal",
        }
        sources += ["fixture_adapter.py", "raw.txt"]
    else:
        sources += [data["targets"]["fasta"], data["targets"]["metadata"]]
    for source in sources:
        path = tmp_path / source
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("syntax-check placeholder; no scientific execution\n")
    config = tmp_path / "pipeline.yaml"
    config.write_text(yaml.safe_dump(data))
    return config


def invoke(tmp_path: Path, config: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "snakemake",
            "--snakefile",
            str(SNAKEFILE),
            "--directory",
            str(tmp_path),
            "--configfile",
            str(config),
            "--cores",
            "1",
            *args,
        ],
        cwd=tmp_path,
        env={**os.environ, "XDG_CACHE_HOME": str(tmp_path / "cache")},
        capture_output=True,
        text=True,
        timeout=60,
    )


@pytest.mark.parametrize("adapter", [False, True])
def test_real_snakemake_dag_structure(tmp_path, adapter):
    config = workflow_config(tmp_path, adapter=adapter)
    result = invoke(tmp_path, config, "--dag", "dot")
    assert result.returncode == 0, result.stdout + result.stderr
    nodes = dict(re.findall(r'(\d+)\[label = "([^"\\]+)', result.stdout))
    edges = {
        (nodes[a], nodes[b]) for a, b in re.findall(r"(\d+) -> (\d+)", result.stdout)
    }
    assert ("stage_00" in nodes.values()) == adapter
    chain = [
        "05A",
        "05B",
        "05R0",
        "05R1",
        "05A2",
        "05C2",
        "05D",
        "06A",
        "NONEXACT",
        "06C0",
        "06C1",
    ]
    for upstream, downstream in zip(chain, chain[1:], strict=False):
        assert (f"stage_{upstream}", f"stage_{downstream}") in edges
    assert ("stage_04B", "stage_04C") in edges
    assert ("stage_05A2", "stage_05R_POST") in edges
    assert ("stage_05C2", "stage_05R_POST") in edges
    assert ("stage_06A", "stage_06C1") in edges
    assert not any(
        a in {"stage_03", "stage_05R1"} and b.startswith("stage_04") for a, b in edges
    )
    assert not any(
        name.startswith(("stage_07", "stage_05R2", "stage_06B"))
        for name in nodes.values()
    )
    assert not (tmp_path / "results").exists()
    assert not (tmp_path / "work").exists()
    # The input-dependent rescue branch is statically declared, including empty cases.
    assert list(nodes.values()).count("stage_05R1") == 2


def test_dry_run_and_unimplemented_stage_fails_closed(tmp_path):
    config = workflow_config(tmp_path, adapter=True)
    result = invoke(tmp_path, config, "--dry-run")
    assert result.returncode == 0, result.stdout + result.stderr
    result = invoke(tmp_path, config, "stage_00")
    assert result.returncode != 0
    assert "not implemented in Block 1" in result.stdout + result.stderr
    assert not (tmp_path / "work/pipeline-v2/common/00").exists()
    log = tmp_path / "logs/pipeline-v2/common/00.log"
    assert "No scientific outputs" in log.read_text()


def test_production_snakefile_invariants():
    text = SNAKEFILE.read_text(encoding="utf-8")

    assert "directory(" not in text
    assert "TESTING" not in text

    assert re.search(r"^rule stage_05C2:$", text, re.MULTILINE)
    assert not re.search(r"^rule stage_05C:$", text, re.MULTILINE)

    assert not re.search(r"^rule stage_05R2:$", text, re.MULTILINE)
    assert not re.search(r"^rule stage_06B:$", text, re.MULTILINE)
    assert not re.search(r"^rule stage_07", text, re.MULTILINE)

    assert '"manifest.json"' in text


def test_final_taxonomy_audit_replaces_baseline_05c(tmp_path):
    config = workflow_config(tmp_path)
    result = invoke(tmp_path, config, "--dag", "dot")

    assert result.returncode == 0, result.stdout + result.stderr

    labels = re.findall(r'\d+\[label = "([^"\\]+)', result.stdout)

    assert "stage_05C2" in labels
    assert "stage_05C" not in labels


def test_stage03_declares_only_its_consumed_parameters():
    text = SNAKEFILE.read_text(encoding="utf-8")

    start = text.index("rule stage_03:")
    end = text.index("\nrule stage_04A:", start)
    block = text[start:end]

    assert "candidate_policy=cfg.stage03_parameters()" in block
    assert 'cfg.data["scientific"]' not in block
    assert '"scripts/run_stage_03.py"' in block
    assert "binary=PRIMARY_CANDIDATE_SCAN_BINARY" in block


def test_stage03_native_build_rule_is_declared():
    text = SNAKEFILE.read_text(encoding="utf-8")

    assert "rule build_primary_candidate_scan:" in text
    assert "pixi run build-primary-candidate-scan" in text


def test_stage_04a_is_production_wired():
    text = SNAKEFILE.read_text(encoding="utf-8")

    start = text.index("rule stage_04A:")
    end = text.index("rule stage_04B:", start)
    block = text[start:end]

    assert 'target_manifest=bundle("01")' in block
    assert 'full_manifest=bundle("01A_FULL", "{role}")' in block
    assert "binary=DIRECT_ACCESSION_ANCHORS_BINARY" in block
    assert 'stage="04A"' in block
    assert 'script:\n        "scripts/run_stage_04A.py"' in block

    assert 'cfg.data["scientific"]' not in block
    assert 'cfg.data["ontology"]' not in block
    assert "release_config" not in block
    assert "target_contract" not in block


def test_direct_accession_anchor_build_is_declared():
    text = SNAKEFILE.read_text(encoding="utf-8")

    assert "DIRECT_ACCESSION_ANCHORS_BINARY" in text
    assert "DIRECT_ACCESSION_ANCHORS_BUILD_INPUTS" in text
    assert "rule build_direct_accession_anchors:" in text
    assert "pixi run build-direct-accession-anchors" in text
    assert '"probe_direct_accession_anchors_v1.cpp"' in text


def test_stage_04a_declares_no_ontology_provenance():
    runner = (
        ROOT / "workflow" / "scripts" / "run_stage_04A.py"
    ).read_text(encoding="utf-8")

    assert "ontology=None" in runner
    assert "ontology={}" not in runner


def test_stage_04b_is_production_wired():
    text = SNAKEFILE.read_text(encoding="utf-8")

    start = text.index("rule stage_04B:")
    end = text.index("rule stage_04C:", start)
    block = text[start:end]

    assert 'anchor_manifest=bundle("04A", "{role}")' in block
    assert 'taxonomy_manifest=bundle("02B", "{role}")' in block
    assert "implementation_script=DIRECT_ANCHOR_CLASSIFICATION_SCRIPT" in block
    assert "taxonomy_module=TAXONOMY_MODULE" in block

    assert 'stage="04B"' in block
    assert "classification_policy=cfg.stage04b_parameters()" in block
    assert '"scripts/run_stage_04B.py"' in block

    assert 'scientific=cfg.data["scientific"]' not in block
    assert 'ontology=cfg.data["ontology"]' not in block
    assert "release_config=" not in block
    assert "target_contract=" not in block


def test_stage_04b_implementation_inputs_are_declared():
    text = SNAKEFILE.read_text(encoding="utf-8")

    assert "DIRECT_ANCHOR_CLASSIFICATION_SCRIPT" in text
    assert '"04_classify_direct_anchors.py"' in text
    assert "TAXONOMY_MODULE" in text


def test_stage_04c_is_production_wired():
    text = SNAKEFILE.read_text(encoding="utf-8")

    start = text.index("rule stage_04C:")
    end = text.index("rule stage_04D0a:", start)
    block = text[start:end]

    assert 'target_manifest=bundle("01")' in block
    assert 'start_anchor_manifest=bundle("04B", "START")' in block
    assert 'end_anchor_manifest=bundle("04B", "END")' in block
    assert "implementation_script=DIRECT_LINEAGE_KEYS_SCRIPT" in block

    assert 'stage="04C"' in block
    assert '"scripts/run_stage_04C.py"' in block

    assert 'bundle("03"' not in block
    assert 'scientific=cfg.data["scientific"]' not in block
    assert 'ontology=cfg.data["ontology"]' not in block
    assert "release_config=" not in block
    assert "target_contract=" not in block


def test_stage_04c_implementation_input_is_declared():
    text = SNAKEFILE.read_text(encoding="utf-8")

    assert "DIRECT_LINEAGE_KEYS_SCRIPT" in text
    assert '"04_compile_direct_lineage_keys.py"' in text


def test_stage_04d0a_reuses_production_raw_ox_extractor():
    text = SNAKEFILE.read_text(encoding="utf-8")

    start = text.index("rule stage_04D0a:")
    end = text.index("rule stage_04D0b:", start)
    block = text[start:end]

    assert 'source_manifest=bundle("01A_FULL", "{role}")' in block
    assert "binary=RAW_OX_EXTRACT_BINARY" in block
    assert 'stage="04D0a"' in block
    assert '"scripts/run_stage_04D0a.py"' in block

    assert 'bundle("01C"' not in block
    assert 'bundle("02A"' not in block
    assert 'bundle("03"' not in block

    assert 'scientific=cfg.data["scientific"]' not in block
    assert 'ontology=cfg.data["ontology"]' not in block
    assert "release_config=" not in block
    assert "target_contract=" not in block


def test_stage_04d0b_resolves_full_ox_with_release_taxonomy_index():
    text = SNAKEFILE.read_text(encoding="utf-8")

    start = text.index("rule stage_04D0b:")
    end = text.index("rule stage_04D1:", start)
    block = text[start:end]

    assert 'ox_manifest=bundle("04D0a", "{role}")' in block
    assert 'taxonomy_manifest=bundle("02B", "{role}")' in block
    assert "implementation_script=FULL_OX_TAXONOMY_SCRIPT" in block
    assert "taxonomy_module=TAXONOMY_MODULE" in block
    assert 'stage="04D0b"' in block
    assert '"scripts/run_stage_04D0b.py"' in block

    assert 'bundle("01C"' not in block
    assert 'bundle("02A"' not in block
    assert 'bundle("03"' not in block

    assert "nodes=" not in block
    assert "names=" not in block
    assert "merged=" not in block
    assert "delnodes=" not in block

    assert 'scientific=cfg.data["scientific"]' not in block
    assert 'ontology=cfg.data["ontology"]' not in block
    assert "release_config=" not in block
    assert "target_contract=" not in block


def test_stage_04d1_builds_common_frozen_lineage_scan_index():
    text = SNAKEFILE.read_text(encoding="utf-8")

    start = text.index("rule stage_04D1:")
    end = text.index("rule stage_04D2:", start)
    block = text[start:end]

    assert 'target_manifest=bundle("01")' in block
    assert 'keys_manifest=bundle("04C")' in block
    assert 'start_anchor_manifest=bundle("04B", "START")' in block
    assert 'end_anchor_manifest=bundle("04B", "END")' in block
    assert 'start_taxonomy_manifest=bundle("02B", "START")' in block
    assert 'end_taxonomy_manifest=bundle("02B", "END")' in block
    assert "implementation_script=LINEAGE_SCAN_INDEX_SCRIPT" in block
    assert 'stage="04D1"' in block
    assert '"scripts/run_stage_04D1.py"' in block

    assert 'bundle("03"' not in block
    assert 'bundle("04D0a"' not in block
    assert 'bundle("04D0b"' not in block

    assert 'scientific=cfg.data["scientific"]' not in block
    assert 'ontology=cfg.data["ontology"]' not in block
    assert "release_config=" not in block
    assert "target_contract=" not in block


def test_stage_04d2_uses_production_onehop_lineage_scanner():
    text = SNAKEFILE.read_text(encoding="utf-8")

    build_start = text.index("rule build_onehop_lineage_scan:")
    stage_start = text.index("rule stage_04D2:", build_start)
    build_block = text[build_start:stage_start]

    assert "ONEHOP_LINEAGE_SCAN_BUILD_INPUTS" in build_block
    assert "ONEHOP_LINEAGE_SCAN_BINARY" in build_block
    assert "pixi run build-onehop-lineage-scan" in build_block

    stage_end = text.index("rule stage_04E:", stage_start)
    block = text[stage_start:stage_end]

    assert 'index_manifest=bundle("04D1")' in block
    assert 'taxonomy_manifest=bundle("04D0b", "{role}")' in block
    assert 'full_manifest=bundle("01A_FULL", "{role}")' in block
    assert "binary=ONEHOP_LINEAGE_SCAN_BINARY" in block
    assert 'stage="04D2"' in block
    assert '"scripts/run_stage_04D2.py"' in block

    assert 'bundle("03"' not in block
    assert 'bundle("04C"' not in block
    assert 'bundle("04B"' not in block

    assert 'scientific=cfg.data["scientific"]' not in block
    assert 'ontology=cfg.data["ontology"]' not in block
    assert "release_config=" not in block
    assert "target_contract=" not in block


def test_stage_04e_collapses_onehop_lineage_evidence_without_filtering():
    text = SNAKEFILE.read_text(encoding="utf-8")

    start = text.index("rule stage_04E:")
    rest = text[start:]
    next_rule = rest.index("\nrule ", len("rule stage_04E:"))
    block = rest[:next_rule]

    assert 'evidence_manifest=bundle("04D2", "{role}")' in block
    assert "implementation_script=LINEAGE_EVIDENCE_COLLAPSE_SCRIPT" in block
    assert 'stage="04E"' in block
    assert '"scripts/run_stage_04E.py"' in block

    assert 'bundle("03"' not in block
    assert 'bundle("04D1"' not in block
    assert 'bundle("04D0b"' not in block
    assert 'bundle("04C"' not in block

    assert 'scientific=cfg.data["scientific"]' not in block
    assert 'ontology=cfg.data["ontology"]' not in block
    assert "release_config=" not in block
    assert "target_contract=" not in block


def test_stage_05a_unions_sequence_and_lineage_channels_without_filtering():
    text = SNAKEFILE.read_text(encoding="utf-8")

    start = text.index("rule stage_05A:")
    rest = text[start:]
    next_rule = rest.index("\nrule ", len("rule stage_05A:"))
    block = rest[:next_rule]

    assert 'start_sequence_manifest=bundle("03", "START")' in block
    assert 'end_sequence_manifest=bundle("03", "END")' in block
    assert 'start_lineage_manifest=bundle("04E", "START")' in block
    assert 'end_lineage_manifest=bundle("04E", "END")' in block

    assert "implementation_script=CANDIDATE_CHANNEL_UNION_SCRIPT" in block
    assert 'bundle("05A")' in block
    assert 'stage="05A"' in block
    assert '"scripts/run_stage_05A.py"' in block

    assert 'scientific=cfg.data["scientific"]' not in block
    assert 'ontology=cfg.data["ontology"]' not in block
    assert "release_config=" not in block
    assert "target_contract=" not in block


def test_stage_05a_keeps_tunable_k_generic():
    repo_root = SNAKEFILE.parent.parent

    pipeline_script = (
        repo_root
        / "scripts"
        / "pipeline"
        / "05A_union_candidate_channels.py"
    ).read_text(encoding="utf-8")

    runner = (
        repo_root
        / "workflow"
        / "scripts"
        / "run_stage_05A.py"
    ).read_text(encoding="utf-8")

    assert "target_distinct_kmers" in pipeline_script
    assert "shared_distinct_kmers" in pipeline_script
    assert "shared_unique_positional_kmers" in pipeline_script

    assert "target_distinct_7mers" not in pipeline_script
    assert "shared_distinct_7mers" not in pipeline_script
    assert "shared_unique_positional_7mers" not in pipeline_script

    assert "k != 7" not in runner
    assert "k == 7" not in runner
    assert 'cfg.data["scientific"]' not in runner


def test_stage_05b_audits_uncovered_targets_without_modifying_candidates():
    text = SNAKEFILE.read_text(encoding="utf-8")

    start = text.index("rule stage_05B:")
    rest = text[start:]
    next_rule = rest.index("\nrule ", len("rule stage_05B:"))
    block = rest[:next_rule]

    assert 'targets_manifest=bundle("01")' in block
    assert 'candidate_union_manifest=bundle("05A")' in block
    assert 'start_anchor_manifest=bundle("04B", "START")' in block
    assert 'end_anchor_manifest=bundle("04B", "END")' in block
    assert "implementation_script=UNCOVERED_TARGET_AUDIT_SCRIPT" in block

    assert 'bundle("05B")' in block
    assert 'stage="05B"' in block
    assert "policy=cfg.stage05b_parameters()" in block
    assert '"scripts/run_stage_05B.py"' in block

    assert 'scientific=cfg.data["scientific"]' not in block
    assert 'ontology=cfg.data["ontology"]' not in block
    assert "release_config=" not in block
    assert "target_contract=" not in block


def test_stage_05b_uses_configured_k_without_frozen_k7_schema():
    repo_root = SNAKEFILE.parent.parent

    pipeline_script = (
        repo_root
        / "scripts"
        / "pipeline"
        / "05B_audit_uncovered_targets.py"
    ).read_text(encoding="utf-8")

    runner = (
        repo_root
        / "workflow"
        / "scripts"
        / "run_stage_05B.py"
    ).read_text(encoding="utf-8")

    assert "--k" in pipeline_script
    assert "kmer_sequence_searchable" in pipeline_script
    assert "TARGET_LEN_LT_K_AND_NO_DIRECT_ANCHOR" in pipeline_script
    assert "TARGET_LEN_LT_K_AND_DIRECT_ANCHOR_INELIGIBLE" in pipeline_script

    assert "k7_sequence_searchable" not in pipeline_script
    assert "TARGET_LEN_LT_7" not in pipeline_script
    assert "START-2023-01" not in pipeline_script
    assert "END-2026-03" not in pipeline_script

    assert 'policy["k"]' in runner
    assert 'cfg.data["scientific"]' not in runner


def test_stage_05r0_prepares_lossless_rescue_subset():
    text = SNAKEFILE.read_text(encoding="utf-8")

    start = text.index("rule stage_05R0:")
    rest = text[start:]
    next_rule = rest.index("\nrule ", len("rule stage_05R0:"))
    block = rest[:next_rule]

    assert 'uncovered_manifest=bundle("05B")' in block
    assert 'targets_manifest=bundle("01")' in block
    assert 'target_taxonomy_manifest=bundle("02B", "{role}")' in block
    assert "implementation_script=RESCUE_TARGET_PREPARATION_SCRIPT" in block

    assert 'bundle("05R0", "{role}")' in block
    assert 'stage="05R0"' in block
    assert '"scripts/run_stage_05R0.py"' in block

    assert 'scientific=cfg.data["scientific"]' not in block
    assert 'ontology=cfg.data["ontology"]' not in block
    assert "release_config=" not in block
    assert "target_contract=" not in block


def test_stage_05r0_consumes_searchability_fact_without_rereading_k():
    repo_root = SNAKEFILE.parent.parent

    pipeline_script = (
        repo_root
        / "scripts"
        / "pipeline"
        / "05R0_prepare_rescue_targets.py"
    ).read_text(encoding="utf-8")

    runner = (
        repo_root
        / "workflow"
        / "scripts"
        / "run_stage_05R0.py"
    ).read_text(encoding="utf-8")

    assert "kmer_sequence_searchable" in pipeline_script
    assert "STAGE05B_UNCOVERED_AND_K_SEARCHABLE" in pipeline_script

    assert "k7_sequence_searchable" not in pipeline_script
    assert "STAGE05B_UNCOVERED_AND_K7_SEARCHABLE" not in pipeline_script
    assert "--k" not in pipeline_script

    assert 'cfg.data["scientific"]' not in runner
    assert 'policy["k"]' not in runner
