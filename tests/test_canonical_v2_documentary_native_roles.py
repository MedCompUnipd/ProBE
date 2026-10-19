"""Preserve and interpret the typed roles emitted by the real 04D1 compiler."""

from __future__ import annotations

from pathlib import Path

import pytest
from test_canonical_v2_documentary_lookup import _fixture, _rows, _tsv
from test_canonical_v2_documentary_science import KEY, RELEASES, _source, _write

from probe.canonical_v2.documentary_lookup import (
    OUTPUT,
    discover_documentary_nonexact,
)
from probe.canonical_v2.documentary_science import (
    has_stable_documentary_locus,
    read_documentary_facts,
)


def test_lookup_preserves_native_roles_separately_from_origin(tmp_path: Path) -> None:
    paths = _fixture(tmp_path)
    records = _rows(paths["independent_index"])
    native_roles = "END:ORDERED_LOCUS,START:ORDERED_LOCUS"
    records[0]["source_roles"] = native_roles
    _tsv(paths["independent_index"], tuple(records[0]), records)
    discover_documentary_nonexact(**paths)
    observed = _rows(paths["destination"] / OUTPUT)
    independent = [row for row in observed if row["source_kind"] == "INDEPENDENT_04D1"]
    assert independent
    assert {row["source_roles"] for row in independent} == {native_roles}
    assert {row["source_key_origin"] for row in independent} == {
        "ORIGINAL_DOCUMENTARY_KEY"
    }
    exact = [row for row in observed if row["source_kind"] == "EXACT"]
    assert exact
    assert {row["source_roles"] for row in exact} == {"EXACT_VERIFIED"}
    assert all(row["source_key_origin"].startswith("GENE_") for row in exact)


def _independent(kind: str, roles: str, **changed: str) -> dict[str, str]:
    return _source(
        source_kind="INDEPENDENT_04D1",
        exact_source_rid="",
        exact_source_tax_scope="",
        exact_origin_release=KEY[0],
        source_roles=roles,
        source_key_type=kind,
        candidate_key_type=kind,
        source_key_origin="ORIGINAL_DOCUMENTARY_KEY",
        **changed,
    )


@pytest.mark.parametrize("kind", ["ORDERED_LOCUS", "ORF"])
def test_native_dual_anchor_roles_support_typed_locus(
    tmp_path: Path,
    kind: str,
) -> None:
    source = tmp_path / "evidence.tsv"
    _write(source, _independent(kind, f"END:{kind},START:{kind}"))
    observed = read_documentary_facts(path=source, releases=RELEASES)
    assert has_stable_documentary_locus(observed[KEY])
    (locus,) = observed[KEY].matched_locus_keys
    assert locus.from_start_anchor and locus.from_end_anchor
    assert locus.key_type == kind


@pytest.mark.parametrize("role", ["START", "END"])
def test_native_single_anchor_cannot_certify_stable_locus(
    tmp_path: Path,
    role: str,
) -> None:
    source = tmp_path / "evidence.tsv"
    _write(source, _independent("ORDERED_LOCUS", f"{role}:ORDERED_LOCUS"))
    observed = read_documentary_facts(path=source, releases=RELEASES)
    assert not has_stable_documentary_locus(observed[KEY])


@pytest.mark.parametrize(
    "roles",
    [
        "END:ORF,START:ORF",
        "END:ORDERED_LOCUS,START:ORF",
        "END:ORDERED_LOCUS,START:ORDERED_LOCUS:ORIGINAL_DOCUMENTARY_KEY",
        "END_ANCHOR,START_ANCHOR,START:ORF",
    ],
)
def test_native_role_must_match_the_literal_key_type(
    tmp_path: Path,
    roles: str,
) -> None:
    source = tmp_path / "evidence.tsv"
    _write(source, _independent("ORDERED_LOCUS", roles))
    with pytest.raises(ValueError, match="anchor role.*typed locus"):
        read_documentary_facts(path=source, releases=RELEASES)


@pytest.mark.parametrize(
    "collision_field",
    [
        "target_key_multiplicity",
        "candidate_key_multiplicity",
    ],
)
def test_native_dual_anchor_does_not_override_collision(
    tmp_path: Path,
    collision_field: str,
) -> None:
    source = tmp_path / "evidence.tsv"
    _write(
        source,
        _independent(
            "ORDERED_LOCUS",
            "END:ORDERED_LOCUS,START:ORDERED_LOCUS",
            **{collision_field: "2", "potential_unique_locus": "0"},
        ),
    )
    observed = read_documentary_facts(path=source, releases=RELEASES)
    assert observed[KEY].collision_present
    assert not has_stable_documentary_locus(observed[KEY])


def test_native_roles_do_not_promote_normalized_hint(tmp_path: Path) -> None:
    source = tmp_path / "evidence.tsv"
    _write(
        source,
        _independent(
            "ORDERED_LOCUS",
            "END:ORDERED_LOCUS,START:ORDERED_LOCUS",
            source_key_value="LOC-7",
            candidate_key_value="LOC7",
            match_class="NORMALIZED_HINT",
            potential_unique_locus="0",
        ),
    )
    assert read_documentary_facts(path=source, releases=RELEASES) == {}
