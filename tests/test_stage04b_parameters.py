from copy import deepcopy
from pathlib import Path

import yaml

from probe.workflow.config import PipelineConfig

ROOT = Path(__file__).resolve().parents[1]


def load_config() -> dict:
    return yaml.safe_load(
        (ROOT / "config" / "pipeline.yaml").read_text(encoding="utf-8")
    )


def test_stage04b_parameters_expose_only_taxonomy_policy():
    data = load_config()
    parsed = PipelineConfig.from_mapping(data)

    assert parsed.stage04b_parameters() == {
        "taxonomy_policy": data["scientific"]["sequence"]["taxonomy_policy"],
    }


def test_stage04b_parameters_ignore_stage03_k():
    data = load_config()

    baseline = PipelineConfig.from_mapping(deepcopy(data)).stage04b_parameters()

    changed = deepcopy(data)
    changed["scientific"]["sequence"]["k"] = 6

    assert PipelineConfig.from_mapping(changed).stage04b_parameters() == baseline


def test_stage04b_parameters_follow_taxonomy_policy():
    data = load_config()
    changed = deepcopy(data)
    changed["scientific"]["sequence"]["taxonomy_policy"] = "strict_taxon"

    assert PipelineConfig.from_mapping(changed).stage04b_parameters() == {
        "taxonomy_policy": "strict_taxon",
    }
