"""A deliberately unaligned NONEXACT candidate can never become a GO donor."""

from __future__ import annotations

import pytest
from test_stage07_resolver_alignments import (
    ALIGN_FIELDS,
    JOB_FIELDS,
    KEY,
    alignment,
    job,
    record,
    write,
)
from test_stage2c_resolver_variant import RELEASES, fixture, run

from probe.resolver_alignments import (
    _not_aligned,
    collect_alignments,
)
from probe.resolver_assembly import resolve_joined_candidates


@pytest.mark.parametrize("policy", ["strict", "same_species_exact"])
@pytest.mark.parametrize("audit", [False, True])
def test_cross_taxid_nonexact_skipped_is_retained_and_not_donor(
    policy: str, audit: bool
) -> None:
    case = fixture(exact=False, documentary=True, audit=audit)
    key = next(iter(case[1]))
    case[1][key] = _not_aligned(policy_skipped=True)
    resolved = run(case, policy=policy)
    row = resolved.rows[key]
    assert row.decision.decision == ("AUDIT_ONLY" if audit else "UNRESOLVED")
    assert row.decision.annotation_donor is False
    assert row.decision.decision_basis == "CROSS_STRAIN_NONEXACT_POLICY_EXCLUDED"
    assert row.sequence_relation == "NOT_ALIGNED_BY_POLICY"
    assert row.exact_sequence is None
    assert resolved.cross_strain_total == 1
    assert resolved.cross_strain_donors == 0


def test_same_taxid_skipped_is_rejected() -> None:
    case = fixture(candidate_taxid="100", exact=False, documentary=True)
    key = next(iter(case[1]))
    case[1][key] = _not_aligned(policy_skipped=True)
    with pytest.raises(ValueError, match="verified cross-TaxID"):
        run(case)


def test_missing_taxid_skipped_is_rejected() -> None:
    case = fixture(candidate_taxid=None, exact=False)
    key = next(iter(case[1]))
    case[1][key] = _not_aligned(policy_skipped=True)
    with pytest.raises(ValueError, match="verified cross-TaxID"):
        run(case)


def test_canonical_resolver_cannot_admit_skipped_pair() -> None:
    case = fixture(exact=False, documentary=True)
    key = next(iter(case[1]))
    case[1][key] = _not_aligned(policy_skipped=True)
    with pytest.raises(ValueError, match="requires Stage 2C gate"):
        resolve_joined_candidates(
            provenance=case[0], alignments=case[1], release_order=RELEASES
        )


def test_skipped_and_accepted_counted_without_false_multiplicity() -> None:
    accepted = fixture(candidate_taxid="100", documentary=True, rid="1")
    skipped = fixture(candidate_taxid="200", exact=False, documentary=True, rid="2")
    key = next(iter(skipped[1]))
    skipped[1][key] = _not_aligned(policy_skipped=True)
    for index in (0, 1, 3):
        accepted[index].update(skipped[index])
    result = run(accepted)
    assert result.rows[(RELEASES[0], "T1", "1")].decision.annotation_donor
    assert not result.rows[key].decision.annotation_donor
    assert result.rows[key].decision.accepted_candidates_for_target_release == 1


def test_reader_skipped_requires_explicit_key_and_nonexact(tmp_path) -> None:
    ref = record()
    j = job(ref, exact=False)
    jobs = write(tmp_path / "jobs.tsv", JOB_FIELDS, [j])
    missing = write(tmp_path / "missing.tsv", ALIGN_FIELDS, [])
    with pytest.raises(ValueError, match="missing alignment"):
        collect_alignments(
            candidates={KEY: ref}, jobs_path=jobs, alignments_path=missing
        )
    result = collect_alignments(
        candidates={KEY: ref},
        jobs_path=jobs,
        alignments_path=missing,
        policy_skipped_keys=frozenset({KEY}),
    )
    assert result[KEY].policy_skipped
    assert not result[KEY].aligned
    assert result[KEY].exact_sequence is None
    j["exact_sequence"] = "1"
    write(jobs, JOB_FIELDS, [j])
    with pytest.raises(ValueError, match="cannot skip an EXACT pair"):
        collect_alignments(
            candidates={KEY: ref},
            jobs_path=jobs,
            alignments_path=missing,
            policy_skipped_keys=frozenset({KEY}),
        )


def test_reader_does_not_ignore_unexpected_alignment_for_skipped(tmp_path) -> None:
    ref = record()
    j = job(ref, exact=False)
    jobs = write(tmp_path / "jobs.tsv", JOB_FIELDS, [j])
    extra = write(tmp_path / "extra.tsv", ALIGN_FIELDS, [alignment(j, "variant")])
    with pytest.raises(ValueError, match="extra 06C1 alignment"):
        collect_alignments(
            candidates={KEY: ref},
            jobs_path=jobs,
            alignments_path=extra,
            policy_skipped_keys=frozenset({KEY}),
        )


def test_reader_does_not_accept_skip_outside_candidates(tmp_path) -> None:
    ref = record()
    j = job(ref, exact=False)
    jobs = write(tmp_path / "jobs.tsv", JOB_FIELDS, [j])
    no_align = write(tmp_path / "empty.tsv", ALIGN_FIELDS, [])
    with pytest.raises(ValueError, match="outside alignment jobs"):
        collect_alignments(
            candidates={KEY: ref},
            jobs_path=jobs,
            alignments_path=no_align,
            policy_skipped_keys=frozenset({("A", "x", "1")}),
        )


def test_policy_skip_sidecar_requires_schema_and_reason(tmp_path) -> None:
    import ast
    import csv
    from pathlib import Path

    script = (
        Path(__file__).parents[1] / "scripts/pipeline/07_resolve_stage2c_variant.py"
    )
    syntax = ast.parse(script.read_text(encoding="utf-8"))
    function = next(
        item
        for item in syntax.body
        if isinstance(item, ast.FunctionDef) and item.name == "_policy_skipped_keys"
    )
    module = ast.Module(body=[function], type_ignores=[])
    namespace = {"csv": csv, "Path": Path}
    exec(compile(module, str(script), "exec"), namespace)
    policy_skipped = namespace["_policy_skipped_keys"]
    sidecar = tmp_path / "skip.tsv"
    fields = ["release", "target_id", "rid", "reason"]
    rows = [
        {
            "release": "2023_01",
            "target_id": "T1",
            "rid": "1",
            "reason": "CROSS_TAXID_NONEXACT",
        }
    ]
    write(sidecar, fields, rows)
    expected = frozenset({("2023_01", "T1", "1")})
    assert policy_skipped(sidecar, RELEASES) == expected
    rows[0]["reason"] = "DP_BUDGET_EXCEEDED"
    write(sidecar, fields, rows)
    with pytest.raises(ValueError, match="reason"):
        policy_skipped(sidecar, RELEASES)
    rows[0]["reason"] = "CROSS_TAXID_NONEXACT"
    rows[0]["rid"] = "01"
    write(sidecar, fields, rows)
    with pytest.raises(ValueError, match="key"):
        policy_skipped(sidecar, RELEASES)
