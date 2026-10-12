"""M9 registration and selection tests: no GO or GAF files are required."""

from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path

import pytest

from probe.stage08_evidence_registry import (
    EvidenceRegistry,
    compile_selections,
    read_json_object,
)


def registry_spec() -> dict:
    return {
        "schema_version": "probe-evidence-registry-v1",
        "registry_id": "example-v1",
        "origin": "test-record",
        "codes": {
            "IDA": {"source": "test", "description": "assay"},
            "IEA": {"source": "test", "description": "electronic"},
            "XNEW": {"source": "local", "description": "reviewed new code"},
        },
        "groups": {
            "experimental": ["IDA", "XNEW"],
            "selected": ["XNEW", "IEA"],
        },
    }


def policy_spec() -> dict:
    return {
        "schema_version": "probe-evidence-selections-v1",
        "registry_id": "example-v1",
        "profiles": {
            "new_assays": {
                "include_groups": ["selected", "experimental"],
                "include_codes": [],
                "exclude_codes": ["IEA"],
            },
            "prior": {
                "include_groups": [],
                "include_codes": ["IEA"],
                "exclude_codes": [],
            },
            "empty": {
                "include_groups": [],
                "include_codes": [],
                "exclude_codes": [],
            },
        },
        "purposes": {
            "score": "new_assays",
            "prior": "prior",
            "neutral": "empty",
            "not": "new_assays",
        },
    }


def test_overlap_deduplicated_and_explicit_exclusion() -> None:
    selection = compile_selections(
        EvidenceRegistry.from_mapping(registry_spec()), policy_spec()
    )
    assert selection.by_purpose["score"].allowed_codes == {"IDA", "XNEW"}
    assert selection.by_purpose["score"].accepts({"XNEW"})
    assert not selection.by_purpose["score"].accepts({"IEA"})


def test_four_purposes_independent_and_empty_does_not_mean_all() -> None:
    selection = compile_selections(
        EvidenceRegistry.from_mapping(registry_spec()), policy_spec()
    )
    assert selection.by_purpose["prior"].allowed_codes == {"IEA"}
    assert selection.by_purpose["neutral"].allowed_codes == set()
    assert selection.by_purpose["not"].allowed_codes == {"IDA", "XNEW"}
    assert not selection.by_purpose["neutral"].accepts({"IDA", "IEA"})
    assert selection.unobserved_selected({"IDA"})["prior"] == ("IEA",)


def test_order_changes_do_not_change_fingerprints() -> None:
    original = registry_spec()
    altered = deepcopy(original)
    altered["groups"]["experimental"].reverse()
    altered["groups"] = dict(reversed(list(altered["groups"].items())))
    r1 = EvidenceRegistry.from_mapping(original)
    r2 = EvidenceRegistry.from_mapping(altered)
    assert r1.fingerprint == r2.fingerprint
    one, two = policy_spec(), policy_spec()
    two["profiles"]["new_assays"]["include_groups"].reverse()
    assert compile_selections(r1, one).fingerprint == (
        compile_selections(r2, two).fingerprint
    )


def test_metadata_change_changes_fingerprint() -> None:
    original = registry_spec()
    revised = deepcopy(original)
    revised["codes"]["IDA"]["description"] = "updated documentation"
    assert EvidenceRegistry.from_mapping(original).fingerprint != (
        EvidenceRegistry.from_mapping(revised).fingerprint
    )


def test_unknown_observed_code_requires_review() -> None:
    registry = EvidenceRegistry.from_mapping(registry_spec())
    assert registry.unregistered_observed({"IEA", "NEW2027"}) == ("NEW2027",)
    with pytest.raises(ValueError, match="need review"):
        registry.require_observed_reviewed({"NEW2027"})
    registry.require_observed_reviewed({"IDA", "IEA"})


def test_unknown_profile_group_rejected() -> None:
    spec = policy_spec()
    spec["profiles"]["new_assays"]["include_groups"] = ["not_a_group"]
    with pytest.raises(ValueError, match="Unregistered groups"):
        compile_selections(EvidenceRegistry.from_mapping(registry_spec()), spec)


def test_unregistered_selected_code_rejected() -> None:
    spec = policy_spec()
    spec["profiles"]["prior"]["include_codes"] = ["NONEXISTENT"]
    with pytest.raises(ValueError, match="Unregistered codes"):
        compile_selections(EvidenceRegistry.from_mapping(registry_spec()), spec)


def test_missing_purpose_and_profile_rejected() -> None:
    spec = policy_spec()
    del spec["purposes"]["not"]
    with pytest.raises(ValueError, match="purposes"):
        compile_selections(EvidenceRegistry.from_mapping(registry_spec()), spec)
    spec = policy_spec()
    spec["purposes"]["score"] = "missing"
    with pytest.raises(ValueError, match="Missing profile"):
        compile_selections(EvidenceRegistry.from_mapping(registry_spec()), spec)


def test_registry_group_must_only_reference_registered_codes() -> None:
    spec = registry_spec()
    spec["groups"]["experimental"].append("ABSENT")
    with pytest.raises(ValueError, match="unregistered codes"):
        EvidenceRegistry.from_mapping(spec)


def test_explicit_selection_of_new_code_requires_registry_entry() -> None:
    spec = registry_spec()
    del spec["codes"]["XNEW"]
    spec["groups"] = {"experimental": ["IDA"], "selected": ["IEA"]}
    registry = EvidenceRegistry.from_mapping(spec)
    selections = policy_spec()
    selections["profiles"]["new_assays"]["include_codes"] = ["XNEW"]
    with pytest.raises(ValueError, match="Unregistered codes"):
        compile_selections(registry, selections)


def test_duplicate_json_keys_rejected(tmp_path: Path) -> None:
    source = tmp_path / "duplicated.json"
    source.write_text('{"codes": {}, "codes": {}}', encoding="utf-8")
    with pytest.raises(ValueError, match="Duplicate JSON"):
        read_json_object(source)


def test_example_registry_covers_canonical_observed_codes() -> None:
    root = Path(__file__).resolve().parents[1]
    source = root / "config/stage08_evidence_registry.example.json"
    registry = EvidenceRegistry.from_json(source)
    observed = {
        "EXP",
        "HDA",
        "HEP",
        "HGI",
        "HMP",
        "HTP",
        "IBA",
        "IC",
        "IDA",
        "IEA",
        "IEP",
        "IGC",
        "IGI",
        "IKR",
        "IMP",
        "IPI",
        "ISA",
        "ISM",
        "ISO",
        "ISS",
        "NAS",
        "RCA",
        "TAS",
    }
    registry.require_observed_reviewed(observed)
    assert "ND" in registry.definitions
    assert "ND" not in observed


def test_unknown_preset_not_silently_enabled() -> None:
    spec = policy_spec()
    spec["profiles"]["new_assays"]["preset"] = "magical"
    with pytest.raises(ValueError, match="profile new_assays"):
        compile_selections(EvidenceRegistry.from_mapping(registry_spec()), spec)


def test_registered_rare_code_can_be_selected() -> None:
    registry = EvidenceRegistry.from_mapping(registry_spec())
    spec = policy_spec()
    spec["profiles"]["new_assays"] = {
        "include_groups": [],
        "include_codes": ["XNEW"],
        "exclude_codes": [],
    }
    compiled = compile_selections(registry, spec)
    assert compiled.by_purpose["score"].allowed_codes == {"XNEW"}


def test_reject_invalid_json_root(tmp_path: Path) -> None:
    path = tmp_path / "bad.json"
    path.write_text(json.dumps(["not", "object"]), encoding="utf-8")
    with pytest.raises(ValueError, match="root"):
        read_json_object(path)
