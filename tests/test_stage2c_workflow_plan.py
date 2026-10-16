"""Static contract tests for the isolated Stage 2C Snakemake DAG."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from probe.stage2c_workflow_plan import (
    PHASES,
    POLICIES,
    ROLES,
    SCHEMA,
    SUMMARY_NAMES,
    sha,
)


def test_isolated_roles_and_policies():
    assert ROLES == ("START", "END")
    assert POLICIES == ("strict", "same_species_exact")
    assert "delta" in PHASES
    assert "go_recovery" in PHASES
    assert SCHEMA.endswith("-v1")


def test_all_output_phases_have_summary():
    assert set(SUMMARY_NAMES) == set(PHASES) | {"preview", "completion", "resolver"}


def test_runner_requires_explicit_opt_in():
    script = Path(__file__).resolve().parents[1] / (
        "workflow/scripts/run_stage_2c_variant.py"
    )
    source = script.read_text(encoding="utf-8")
    tree = ast.parse(source)
    assert tree is not None
    assert "if not ctx.params.enabled:" in source
    assert "full_fasta_scanned" not in source
    assert "directory()" not in source


def test_smk_uses_only_receipts():
    script = Path(__file__).resolve().parents[1] / ("workflow/stage2c_variants.smk")
    source = script.read_text(encoding="utf-8")
    assert "rule stage_2c_variants_both:" in source
    assert "rule stage_2c_policy_resolver:" in source
    assert "stage_2c_shared_completion" in source
    assert 's2c_receipt("resolver", policy)' in source
    assert "directory(" not in source.split("rule stage_2c_evidence_step:", 1)[1]


def test_hashing_is_deterministic(tmp_path: Path):
    source = tmp_path / "source.txt"
    source.write_text("AAA\n", encoding="utf-8")
    first = sha(source)
    assert len(first) == 64
    assert first == sha(source)
    source.write_text("BBB\n", encoding="utf-8")
    assert sha(source) != first


@pytest.mark.parametrize("name", PHASES)
def test_phases_are_independent_of_policy(name: str):
    assert name not in POLICIES
