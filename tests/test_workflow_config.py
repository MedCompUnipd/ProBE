from copy import deepcopy
from pathlib import Path

import pytest
import yaml

from probe.workflow.config import PipelineConfig

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def config():
    return yaml.safe_load((ROOT / "config/pipeline.yaml").read_text())


def test_generic_roles_and_unknown_go_metadata(config):
    config["releases"]["START"]["release_id"] = "arbitrary-release-A"
    parsed = PipelineConfig.from_mapping(config)
    assert parsed.release("START")["release_id"] == "arbitrary-release-A"
    assert parsed.data["ontology"]["retrieval_date"] is None
    assert parsed.target_inputs() == ["inputs/targets.fasta", "inputs/targets.tsv"]
    assert not parsed.uses_adapter
    with pytest.raises(ValueError, match="role"):
        parsed.release("arbitrary-release-A")


def test_adapter_selection_requires_explicit_contract(config):
    config["targets"]["mode"] = "adapter"
    with pytest.raises(ValueError, match="adapter"):
        PipelineConfig.from_mapping(config)
    config["targets"]["adapter"] = {
        "name": "generic-records",
        "version": "1",
        "implementation": "adapters/generic.py",
        "inputs": ["inputs/raw.tsv"],
        "taxid_contract": "explicit ncbi_taxid column",
        "id_policy": "deterministic input ordinal with source-ID provenance",
    }
    parsed = PipelineConfig.from_mapping(config)
    assert parsed.uses_adapter
    assert parsed.target_inputs() == ["inputs/raw.tsv", "adapters/generic.py"]


@pytest.mark.parametrize(
    ("section", "key", "value"),
    [
        ("targets", "mode", "cafa"),
        ("ontology", "expected_sha256", "bad"),
        ("validation", "deep_checksums", "false"),
        ("locations", "work", "TESTING/output"),
        ("locations", "output", "src/probe"),
    ],
)
def test_invalid_config(config, section, key, value):
    config[section][key] = value
    with pytest.raises(ValueError):
        PipelineConfig.from_mapping(config)


def test_reject_duplicate_release_ids_unknown_keys_and_policy_changes(config):
    bad = deepcopy(config)
    bad["releases"]["END"]["release_id"] = bad["releases"]["START"]["release_id"]
    with pytest.raises(ValueError, match="distinct"):
        PipelineConfig.from_mapping(bad)
    bad = deepcopy(config)
    bad["scientific"]["sequence"]["min_target_kmer_fraction"] = 0.10
    with pytest.raises(ValueError, match="frozen"):
        PipelineConfig.from_mapping(bad)
    bad = deepcopy(config)
    bad["resources"]["default"]["threads"] = 0
    with pytest.raises(ValueError, match="positive"):
        PipelineConfig.from_mapping(bad)
    config["unexpected"] = True
    with pytest.raises(ValueError, match="keys"):
        PipelineConfig.from_mapping(config)


def test_locations_must_not_overlap_inputs_or_each_other(config):
    config["locations"]["work"] = "inputs"
    with pytest.raises(ValueError, match="overlap"):
        PipelineConfig.from_mapping(config)
    config["locations"]["work"] = config["locations"]["output"]
    with pytest.raises(ValueError, match="overlap"):
        PipelineConfig.from_mapping(config)



def test_ontology_snapshot_is_user_configurable(config):
    assert config["ontology"]["expected_sha256"] is None

    parsed = PipelineConfig.from_mapping(config)
    assert parsed.data["ontology"]["expected_sha256"] is None

    user_selected_sha256 = "a" * 64
    config["ontology"]["expected_sha256"] = user_selected_sha256
    config["ontology"]["canonical_url"] = "https://example.org/custom-go.owl"

    parsed = PipelineConfig.from_mapping(config)

    assert (
        parsed.data["ontology"]["expected_sha256"]
        == user_selected_sha256
    )
    assert (
        parsed.data["ontology"]["canonical_url"]
        == "https://example.org/custom-go.owl"
    )
