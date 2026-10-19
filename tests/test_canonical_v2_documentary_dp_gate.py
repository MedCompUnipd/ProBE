"""NONEXACT DP requires a release-bound literal documentary witness."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from dataclasses import replace
from pathlib import Path

import pytest
from test_canonical_v2_alignment_v2 import (
    FIELDS,
    PRIMARY,
    _fixture,
    _read,
    _write,
)
from test_stage2c_resolver_variant import RELEASES, fixture

from probe.canonical_v2.alignment_v2 import select_nonexact_dp
from probe.canonical_v2.documentary_handoff import EVIDENCE_FIELDS
from probe.canonical_v2.resolver_runner import _resolve_documentary_gated
from probe.resolver_alignments import _not_aligned
from probe.resolver_lineage import _D1_REQUIRED, _D2_REQUIRED, JoinedLineage
from probe.stage2c_alignment_completion import _write_empty_dp


def _change_evidence(args: dict, **changes: str) -> None:
    rows = _read(args["documentary_evidence"])
    rows[0].update(changes)
    _write(args["documentary_evidence"], EVIDENCE_FIELDS, rows)


def _accepted(args: dict) -> set[tuple[str, str]]:
    select_nonexact_dp(**args)
    return {(row["release"], row["rid"]) for row in _read(args["selected"])}


def test_discovery_channel_and_accession_are_not_witnesses(tmp_path: Path) -> None:
    args = _fixture(tmp_path)
    _write(args["documentary_evidence"], EVIDENCE_FIELDS, [])
    assert _accepted(args) == set()
    reasons = [row["reason"] for row in _read(args["skipped"])]
    assert reasons == [
        "NO_DOCUMENTARY_WITNESS",
        "CROSS_TAXID_NONEXACT",
        "NO_DOCUMENTARY_WITNESS",
    ]


def test_retrospective_exact_document_is_valid_dp_search_witness(
    tmp_path: Path,
) -> None:
    args = _fixture(tmp_path)
    assert _read(args["documentary_evidence"])[0]["exact_origin_release"] == "R2"
    assert _accepted(args) == {("R1", "1")}


@pytest.mark.parametrize(
    "changes",
    [
        {
            "match_class": "NORMALIZED_HINT",
            "source_key_value": "LOC-1",
            "potential_unique_locus": "0",
        },
        {
            "source_key_type": "GENE_NAME",
            "candidate_key_type": "GENE_SYNONYM",
            "potential_unique_locus": "0",
        },
        {
            "source_key_type": "ORF",
            "candidate_key_type": "ORF",
            "source_key_value": "SO:0001217",
            "candidate_key_value": "SO:0001217",
            "potential_unique_locus": "0",
        },
    ],
)
def test_weak_or_nonidentifying_observation_does_not_authorize_dp(
    tmp_path: Path,
    changes: dict[str, str],
) -> None:
    args = _fixture(tmp_path)
    _change_evidence(args, **changes)
    assert _accepted(args) == set()


def test_documentary_match_is_not_a_donor_uniqueness_gate(tmp_path: Path) -> None:
    args = _fixture(tmp_path)
    _change_evidence(
        args,
        target_key_multiplicity="2",
        potential_unique_locus="0",
    )
    assert _accepted(args) == {("R1", "1")}


@pytest.mark.parametrize(
    "changes,error",
    [
        ({"candidate_accession": "FAKE"}, "identity mismatch"),
        ({"candidate_raw_ox": "999"}, "identity mismatch"),
        ({"candidate_frag": "1"}, "identity mismatch"),
        ({"candidate_resolved_taxid": "112"}, "Cross-TaxID"),
        (
            {"source_key_value": "OTHER", "potential_unique_locus": "0"},
            "Literal matched key value",
        ),
        ({"potential_unique_locus": "0"}, "potential locus support"),
        ({"exact_origin_release": "R3"}, "Unknown documentary source"),
        ({"candidate_gene": ""}, "absent from candidate metadata"),
        ({"candidate_key_origin": "GENE_O"}, "absent from candidate metadata"),
        ({"source_key_origin": "GENE_N"}, "Unverified typed EXACT"),
        ({"source_roles": "UNATTESTED"}, "Unverified typed EXACT"),
    ],
)
def test_forged_or_conflicting_document_fails_closed(
    tmp_path: Path,
    changes: dict[str, str],
    error: str,
) -> None:
    args = _fixture(tmp_path)
    _change_evidence(args, **changes)
    with pytest.raises(ValueError, match=error):
        select_nonexact_dp(**args)
    assert not args["selected"].exists()


def _lineage(args: dict, *, mismatch: bool = False) -> None:
    shared = {
        "release": "R2",
        "target_id": "T",
        "key_type": "ACCESSION",
        "key_value": "Q1",
        "key_target_multiplicity": "1",
        "key_collision": "0",
        "from_target_accession": "1",
        "from_start_anchor": "0",
        "from_end_anchor": "0",
        "source_roles": "TARGET_ACCESSION",
    }
    index = {**shared, "primary_bucket": "111"}
    record = {
        **shared,
        "target_primary_bucket": "111",
        "rid": "1",
        "candidate_primary_accession": "Q1",
    }
    if mismatch:
        record["key_value"] = "UNATTESTED"
    _write(args["lineage_index"], tuple(sorted(_D1_REQUIRED)), [index])
    _write(args["lineage_paths"]["R2"], tuple(sorted(_D2_REQUIRED)), [record])


def test_independent_accession_requires_attested_04d1_04d2_join(
    tmp_path: Path,
) -> None:
    args = _fixture(tmp_path)
    _lineage(args)
    assert _accepted(args) == {("R1", "1"), ("R2", "1")}


@pytest.mark.parametrize("attested", [False, True])
def test_independent_lookup_requires_original_04d1_key(
    tmp_path: Path,
    attested: bool,
) -> None:
    args = _fixture(tmp_path)
    _change_evidence(
        args,
        source_kind="INDEPENDENT_04D1",
        exact_source_rid="",
        exact_source_tax_scope="INDEPENDENT",
        exact_origin_release="R1",
        source_key_origin="ORIGINAL_DOCUMENTARY_KEY",
        source_roles="START:ORDERED_LOCUS",
    )
    if attested:
        _write(
            args["lineage_index"],
            tuple(sorted(_D1_REQUIRED)),
            [
                {
                    "release": "R1",
                    "target_id": "T",
                    "primary_bucket": "111",
                    "key_type": "ORDERED_LOCUS",
                    "key_value": "LOC_1",
                    "key_target_multiplicity": "1",
                    "key_collision": "0",
                    "from_target_accession": "0",
                    "from_start_anchor": "1",
                    "from_end_anchor": "0",
                    "source_roles": "START:ORDERED_LOCUS",
                }
            ],
        )
        assert _accepted(args) == {("R1", "1")}
    else:
        with pytest.raises(ValueError, match="authoritative 04D1 provenance"):
            select_nonexact_dp(**args)


def test_unattested_independent_key_fails_closed(tmp_path: Path) -> None:
    args = _fixture(tmp_path)
    _lineage(args, mismatch=True)
    with pytest.raises(ValueError, match="missing authoritative 04D1"):
        select_nonexact_dp(**args)


def test_missing_prior_sources_cannot_be_treated_as_no_matches(
    tmp_path: Path,
) -> None:
    args = _fixture(tmp_path)
    del args["lineage_index"]
    with pytest.raises(ValueError, match="Missing prior documentary sources"):
        select_nonexact_dp(**args)


def test_documentary_selector_does_not_exclude_35000_residue_sequences(
    tmp_path: Path,
) -> None:
    args = _fixture(tmp_path)
    jobs = _read(args["jobs"])
    jobs[0]["candidate_length"] = "35000"
    for row in jobs:
        row["target_length"] = "35000"
    primary = _read(args["primary"])
    primary[0]["sequence_length"] = "35000"
    _write(args["jobs"], (*FIELDS, "target_length"), jobs)
    _write(args["primary"], PRIMARY, primary)
    assert _accepted(args) == {("R1", "1")}
    assert _read(args["selected"])[0]["candidate_length"] == "35000"


def _resolver_case():
    provenance, alignments, taxonomy, sequences = fixture(
        candidate_taxid="100",
        rid="1",
    )
    deferred, _, _, _ = fixture(candidate_taxid="100", exact=False, rid="2")
    key = next(iter(deferred))
    return {
        "provenance": {**provenance, **deferred},
        "alignments": {**alignments, key: _not_aligned(policy_skipped=True)},
        "taxonomy": taxonomy,
        "sequences": sequences,
        "skip_reasons": {key: "NO_DOCUMENTARY_WITNESS"},
        "release_order": RELEASES,
        "strain_policy": "same_species_exact",
        "partial_sequence": "allow",
        "documentary_fragment": "allow",
    }, key


def test_no_documentary_skip_retains_candidate_and_accepted_multiplicity() -> None:
    args, key = _resolver_case()
    result = _resolve_documentary_gated(**args)
    assert set(result.rows) == set(args["provenance"])
    excluded = result.rows[key]
    assert excluded.candidate == args["provenance"][key].candidate
    assert excluded.decision.decision == "UNRESOLVED"
    assert excluded.decision.decision_basis == "NO_DOCUMENTARY_WITNESS"
    assert excluded.decision.annotation_donor is False
    assert excluded.decision.accepted_candidates_for_target_release == 1
    assert excluded.exact_sequence is None
    assert excluded.sequence_relation == "NOT_ALIGNED_BY_POLICY"
    assert result.cross_strain_total == 0


def test_no_documentary_skip_cannot_hide_a_verified_literal_match() -> None:
    args, key = _resolver_case()
    facts = args["provenance"][key]
    lineage = JoinedLineage(
        accession_key_present=True,
        collision_present=False,
        matched_locus_keys=(),
        matched_typed_keys=(("ACCESSION", "Q1"),),
    )
    args["provenance"][key] = replace(
        facts,
        lineage=lineage,
        candidate=replace(
            facts.candidate,
            lineage_discovered=True,
            lineage_key_count=1,
        ),
    )
    with pytest.raises(ValueError, match="Invalid NO_DOCUMENTARY_WITNESS"):
        _resolve_documentary_gated(**args)


def test_finalizer_preserves_new_policy_skip_without_fabricated_alignment(
    tmp_path: Path,
) -> None:
    args = _fixture(tmp_path)
    _write(args["documentary_evidence"], EVIDENCE_FIELDS, [])
    select_nonexact_dp(**args)
    jobs = _read(args["jobs"])
    for row in jobs:
        row.update(target_length="5", target_optional_accession="")
    _write(args["jobs"], tuple(jobs[0]), jobs)
    dp = tmp_path / "dp-empty.tsv"
    _write_empty_dp(dp, tmp_path / "dp-empty.summary.tsv")
    repo = Path(
        os.environ.get(
            "PROBE_SOURCE_REPO",
            Path(__file__).resolve().parents[1],
        )
    )
    output = tmp_path / "final.tsv"
    summary = tmp_path / "final.json"
    subprocess.run(
        [
            sys.executable,
            str(repo / "scripts/pipeline/06C1_finalize_directional_glocal.py"),
            "--alignment-jobs",
            str(args["jobs"]),
            "--nonexact-alignments",
            str(dp),
            "--policy-skipped",
            str(args["skipped"]),
            "--start-release-id",
            "R1",
            "--end-release-id",
            "R2",
            "--output",
            str(output),
            "--summary",
            str(summary),
        ],
        check=True,
    )
    assert _read(output) == []
    report = json.loads(summary.read_text(encoding="utf-8"))
    assert report["policy_skipped_nonexact_count"] == 3
    assert report["aligned_nonexact_pair_count"] == 0
