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


def test_reject_duplicate_release_ids_unknown_keys_and_frozen_policy_changes(config):
    bad = deepcopy(config)
    bad["releases"]["END"]["release_id"] = bad["releases"]["START"]["release_id"]
    with pytest.raises(ValueError, match="distinct"):
        PipelineConfig.from_mapping(bad)
    bad = deepcopy(config)
    bad["scientific"]["sequence"]["primary_universe"] = "FULL_V2"
    with pytest.raises(ValueError, match="non-tunable"):
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



@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("k", 4),
        ("k", 8),
        ("max_required_shared", 1),
        ("max_required_shared", 65535),
        ("min_target_kmer_fraction", 0.20),
        ("min_target_kmer_fraction", 1.00),
        ("fragment_policy", "include"),
        ("taxonomy_policy", "strict_taxon"),
    ],
)
def test_stage03_scientific_parameters_are_configurable(
    config,
    key,
    value,
):
    config["scientific"]["sequence"][key] = value

    parsed = PipelineConfig.from_mapping(config)

    assert parsed.stage03_parameters()[key] == value


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("k", 3),
        ("k", 9),
        ("k", 4.0),
        ("k", True),
        ("max_required_shared", 0),
        ("max_required_shared", 65536),
        ("max_required_shared", 10.0),
        ("min_target_kmer_fraction", 0.19),
        ("min_target_kmer_fraction", 1.01),
        ("min_target_kmer_fraction", 1),
        ("min_target_kmer_fraction", True),
        ("min_target_kmer_fraction", float("nan")),
        ("fragment_policy", "sometimes"),
        ("fragment_policy", True),
        ("taxonomy_policy", "permissive"),
        ("taxonomy_policy", True),
    ],
)
def test_stage03_scientific_parameter_validation(
    config,
    key,
    value,
):
    config["scientific"]["sequence"][key] = value

    with pytest.raises(ValueError):
        PipelineConfig.from_mapping(config)


def test_stage03_parameter_projection_contains_only_consumed_values(config):
    parsed = PipelineConfig.from_mapping(config)

    assert parsed.stage03_parameters() == {
        "k": 7,
        "max_required_shared": 10,
        "min_target_kmer_fraction": 0.25,
        "fragment_policy": "exclude",
        "taxonomy_policy": "rank_aware",
    }


def test_legacy_stage03_policy_keys_are_rejected(config):
    sequence = config["scientific"]["sequence"]
    sequence["exclude_fragments"] = True

    with pytest.raises(ValueError, match="scientific.sequence"):
        PipelineConfig.from_mapping(config)
