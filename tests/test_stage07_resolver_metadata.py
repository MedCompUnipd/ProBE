"""Scientific metadata tests for all Stage 07 policy combinations."""

from __future__ import annotations

import json
from itertools import product

import pytest

from probe.resolver import ResolverPolicy
from probe.resolver_metadata import (
    RULE_PRECEDENCE,
    SCHEMA_VERSION,
    build_selection_metadata,
)

POLICY_COMBINATIONS = tuple(
    product(
        ("strict", "include_audited_same_species"),
        ("allow", "report_only"),
        ("allow", "report_only"),
    )
)


@pytest.mark.parametrize(
    ("subtaxon", "partial", "fragment"),
    POLICY_COMBINATIONS,
)
def test_metadata_all_policy_combinations(subtaxon, partial, fragment):
    policy = ResolverPolicy(
        subtaxon_policy=subtaxon,
        partial_sequence=partial,
        documentary_fragment=fragment,
    )

    kwargs = {
        "start_release_id": "2023_01",
        "end_release_id": "2026_03",
        "policy": policy,
        "policy_version": "stage07-resolver-v1",
    }

    first = build_selection_metadata(**kwargs)
    second = build_selection_metadata(**kwargs)

    assert first == second
    assert first["schema_version"] == SCHEMA_VERSION
    assert first["releases"] == {
        "START": "2023_01",
        "END": "2026_03",
    }

    assert first["effective_policies"] == {
        "resolver": {"subtaxon_policy": subtaxon},
        "annotation_donor": {
            "partial_sequence": partial,
            "documentary_fragment": fragment,
        },
    }

    effects = first["effective_policy_effects"]

    assert effects["audited_same_species"]["evaluated"] == (
        subtaxon == "include_audited_same_species"
    )
    assert effects["partial_sequence"]["donation_permitted"] == (partial == "allow")
    assert effects["documentary_fragment"]["donation_permitted"] == (
        fragment == "allow"
    )

    rules = first["decision_rule_precedence"]

    assert len(rules) == 15
    assert len(RULE_PRECEDENCE) == 15
    assert [rule["priority"] for rule in rules] == list(range(15))

    assert rules[0]["decision"] == "AUDIT_ONLY"
    assert rules[2]["decision_basis"] == "DIRECT_PRIMARY_ACCESSION"
    assert rules[8]["decision_basis"] == ("STABLE_UNIQUE_LOCUS_EXACT_SUBSEQUENCE")
    assert rules[14]["decision_basis"] == "SEQUENCE_ONLY"

    assert first["stage08_contract"]["go_donor_criterion"] == ("annotation_donor=YES")
    assert first["stage08_contract"]["preserve_nondonors"] is True

    serialized = json.dumps(
        first,
        sort_keys=True,
        indent=2,
        allow_nan=False,
    )
    assert serialized == json.dumps(
        second,
        sort_keys=True,
        indent=2,
        allow_nan=False,
    )


def test_metadata_rejects_identical_releases():
    policy = ResolverPolicy()

    with pytest.raises(ValueError, match="distinct"):
        build_selection_metadata(
            start_release_id="2023_01",
            end_release_id="2023_01",
            policy=policy,
            policy_version="stage07-resolver-v1",
        )


def test_metadata_rejects_invalid_policy_object():
    with pytest.raises(TypeError, match="ResolverPolicy"):
        build_selection_metadata(
            start_release_id="2023_01",
            end_release_id="2026_03",
            policy=None,
            policy_version="stage07-resolver-v1",
        )


def test_metadata_rejects_empty_policy_version():
    with pytest.raises(ValueError, match="policy_version"):
        build_selection_metadata(
            start_release_id="2023_01",
            end_release_id="2026_03",
            policy=ResolverPolicy(),
            policy_version="",
        )
