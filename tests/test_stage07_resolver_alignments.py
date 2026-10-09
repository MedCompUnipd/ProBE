"""Synthetic 06A/06C1 geometry and relational contract tests for Stage 07."""

from __future__ import annotations

import csv
from dataclasses import replace

import pytest

from probe.resolver_alignments import collect_alignments
from probe.resolver_anchors import AuditDirectReference
from probe.resolver_candidates import CandidateRecord

KEY = ("2023_01", "T1", "100")
AUDIT_KEY = ("2026_03", "T1", "200")
CROSS_KEY = ("2026_03", "T1", "201")
BASE = (
    "release",
    "target_id",
    "rid",
    "job_scope",
    "discovery_channel",
    "target_optional_accession",
    "candidate_primary_accession",
    "target_length",
    "candidate_length",
)
JOB_FIELDS = (
    *BASE,
    "sequence_discovered",
    "lineage_discovered",
    "frag",
    "audit_class",
    "audit_key_source_reason",
    "exact_sequence",
)
DIRECTION_FIELDS = (
    "status",
    "target_start0",
    "target_end0_exclusive",
    "candidate_start0",
    "candidate_end0_exclusive",
    "target_aligned_span",
    "candidate_aligned_span",
    "matches",
    "mismatches",
    "gap_in_target_residues",
    "gap_in_candidate_residues",
    "aligned_residue_pairs",
    "alignment_columns",
    "cells_evaluated",
)
ALIGN_FIELDS = (
    *BASE,
    *(
        f"{side}_{field}"
        for side in ("target_global", "candidate_global")
        for field in DIRECTION_FIELDS
    ),
)


def record(key=KEY, *, kind="PRIMARY"):
    if kind == "PRIMARY":
        audit = None
        accession = "P1"
        channel = "SEQUENCE_ONLY"
    else:
        reason = (
            "CROSS_SPECIES"
            if kind == "CROSS_SPECIES"
            else "SAME_SPECIES_WRONG_SUBTAXON"
        )
        audit = AuditDirectReference(
            audit_class=(
                "CROSS_SPECIES_AUDIT"
                if kind == "CROSS_SPECIES"
                else "TAXONOMY_RESCUE_AUDIT"
            ),
            reason=reason,
            alignment_allowed=kind != "CROSS_SPECIES",
            direct_match_type="SECONDARY",
            direct_match_accession="P1",
            taxonomy_relation=(
                "CROSS_SPECIES"
                if kind == "CROSS_SPECIES"
                else "SAME_SPECIES_DIFFERENT_SUBTAXON"
            ),
            target_resolved_taxid="981087",
            anchor_resolved_taxid="5661",
        )
        accession = "Q2"
        channel = None
    return CandidateRecord(
        release=key[0],
        target_id=key[1],
        rid=key[2],
        scope="PRIMARY" if audit is None else "TAXONOMY_AUDIT",
        target_accession="P1",
        candidate_accession=accession,
        candidate_raw_ox="5661",
        candidate_resolved_taxid="5661",
        candidate_species_taxid="5661",
        primary_bucket="T:981087",
        fragment_record=False,
        discovery_channel=channel,
        sequence_discovered=audit is None,
        go_positive_sequence_discovered=audit is None,
        full_v2_rescue_sequence_discovered=False,
        lineage_discovered=False,
        lineage_key_count=0,
        lineage_collision_present=False,
        audit=audit,
    )


def job(ref, *, n=5, m=5, exact=True):
    return dict(
        release=ref.release,
        target_id=ref.target_id,
        rid=ref.rid,
        job_scope=ref.scope,
        discovery_channel=(ref.discovery_channel or "TAXONOMY_AUDIT"),
        target_optional_accession=ref.target_accession,
        candidate_primary_accession=ref.candidate_accession,
        target_length=str(n),
        candidate_length=str(m),
        sequence_discovered="1" if ref.sequence_discovered else "0",
        lineage_discovered="1" if ref.lineage_discovered else "0",
        frag="1" if ref.fragment_record else "0",
        audit_class="" if ref.audit is None else ref.audit.audit_class,
        audit_key_source_reason="" if ref.audit is None else ref.audit.reason,
        exact_sequence="1" if exact else "0",
    )


def direction(
    *,
    n,
    m,
    side,
    status,
    target_interval,
    candidate_interval,
    matches,
    mismatches=0,
    gap_in_target=0,
    gap_in_candidate=0,
    cells=0,
):
    ts, te = target_interval
    cs, ce = candidate_interval
    paired = matches + mismatches
    return {
        f"{side}_status": status,
        f"{side}_target_start0": str(ts),
        f"{side}_target_end0_exclusive": str(te),
        f"{side}_candidate_start0": str(cs),
        f"{side}_candidate_end0_exclusive": str(ce),
        f"{side}_target_aligned_span": str(te - ts),
        f"{side}_candidate_aligned_span": str(ce - cs),
        f"{side}_matches": str(matches),
        f"{side}_mismatches": str(mismatches),
        f"{side}_gap_in_target_residues": str(gap_in_target),
        f"{side}_gap_in_candidate_residues": str(gap_in_candidate),
        f"{side}_aligned_residue_pairs": str(paired),
        f"{side}_alignment_columns": str(paired + gap_in_target + gap_in_candidate),
        f"{side}_cells_evaluated": str(cells),
    }


def alignment(j, kind="exact"):
    n, m = int(j["target_length"]), int(j["candidate_length"])
    row = {key: j[key] for key in BASE}
    cells = 0 if kind == "exact" else (n + 1) * (m + 1)
    status = "OK_EXACT" if kind == "exact" else "OK"
    if kind == "exact":
        values = [
            ("target_global", (0, n), (0, m), n, 0, 0),
            ("candidate_global", (0, n), (0, m), n, 0, 0),
        ]
    elif kind == "subsequence":
        values = [
            ("target_global", (0, n), (0, m), m, 0, n - m),
            ("candidate_global", (3, 3 + m), (0, m), m, 0, 0),
        ]
    elif kind == "variant":
        values = [
            ("target_global", (0, n), (0, m), 0, n, 0),
            ("candidate_global", (0, n), (0, m), 0, n, 0),
        ]
    elif kind == "other":
        values = [
            ("target_global", (0, n), (0, m), m - 1, 1, n - m),
            ("candidate_global", (0, m), (0, m), m - 1, 1, 0),
        ]
    else:
        raise AssertionError(kind)
    for side, t, c, matches, mismatches, gap in values:
        row.update(
            direction(
                n=n,
                m=m,
                side=side,
                status=status,
                target_interval=t,
                candidate_interval=c,
                matches=matches,
                mismatches=mismatches,
                gap_in_candidate=gap,
                cells=cells,
            )
        )
    return row


def write(path, fields, rows):
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(
            handle, fieldnames=fields, delimiter="\t", lineterminator="\n"
        )
        writer.writeheader()
        writer.writerows(rows)
    return path


def collect(tmp_path, candidates=None, jobs=None, alignments=None):
    if candidates is None:
        p = record()
        a = record(AUDIT_KEY, kind="AUDIT")
        x = record(CROSS_KEY, kind="CROSS_SPECIES")
        candidates = {p.key: p, a.key: a, x.key: x}
    if jobs is None:
        jobs = [job(candidates[KEY]), job(candidates[AUDIT_KEY])]
    if alignments is None:
        alignments = [alignment(j) for j in jobs]
    return collect_alignments(
        candidates=candidates,
        jobs_path=write(tmp_path / "06A.tsv", JOB_FIELDS, jobs),
        alignments_path=write(tmp_path / "06C1.tsv", ALIGN_FIELDS, alignments),
    )


def test_exact_and_taxonomy_audit_and_excluded_cross_species(tmp_path):
    result = collect(tmp_path)
    assert set(result) == {KEY, AUDIT_KEY, CROSS_KEY}
    for key in (KEY, AUDIT_KEY):
        assert result[key].aligned
        assert result[key].exact_sequence
        assert result[key].target_length == 5
        assert not result[key].exact_subsequence
        assert not result[key].equal_length_variant
        assert result[key].target_global.status == "OK_EXACT"
    cross = result[CROSS_KEY]
    assert not cross.aligned
    assert cross.exact_sequence is None
    assert cross.target_global is None


@pytest.mark.parametrize(
    "kind,n,m,subseq,variant",
    [
        ("subsequence", 10, 4, True, False),
        ("variant", 5, 5, False, True),
        ("other", 5, 4, False, False),
    ],
)
def test_nonexact_geometry(tmp_path, kind, n, m, subseq, variant):
    p = record()
    j = job(p, n=n, m=m, exact=False)
    result = collect(tmp_path, {p.key: p}, [j], [alignment(j, kind)])[KEY]
    assert not result.exact_sequence
    assert result.exact_subsequence is subseq
    assert result.equal_length_variant is variant
    assert result.candidate_global.status == "OK"


def test_variant_has_no_similarity_threshold(tmp_path):
    p = record()
    j = job(p, exact=False)
    result = collect(tmp_path, {KEY: p}, [j], [alignment(j, "variant")])[KEY]
    assert result.equal_length_variant
    assert result.target_global.matches == 0
    assert result.target_global.mismatches == 5


def test_missing_alignment(tmp_path):
    with pytest.raises(ValueError, match="missing alignment"):
        collect(tmp_path, alignments=[])


def test_missing_job(tmp_path):
    with pytest.raises(ValueError, match="coverage mismatch"):
        collect(tmp_path, jobs=[], alignments=[])


def test_extra_alignment(tmp_path):
    p = record()
    j = job(p)
    with pytest.raises(ValueError, match="extra 06C1"):
        collect(
            tmp_path,
            {KEY: p},
            [j],
            [alignment(j), alignment(j)],
        )


def test_06c1_key_mismatch(tmp_path):
    p = record()
    j = job(p)
    row = alignment(j)
    row["rid"] = "101"
    with pytest.raises(ValueError, match="06C1/06A pair key mismatch"):
        collect(tmp_path, {KEY: p}, [j], [row])


def test_audit_cross_species_cannot_be_aligned(tmp_path):
    p = record()
    a = record(AUDIT_KEY, kind="AUDIT")
    x = record(CROSS_KEY, kind="CROSS_SPECIES")
    j = [job(p), job(a), job(x)]
    with pytest.raises(ValueError, match="unexpected 06A"):
        collect(
            tmp_path,
            {KEY: p, AUDIT_KEY: a, CROSS_KEY: x},
            j,
            [alignment(row) for row in j],
        )


def test_duplicate_06a_job(tmp_path):
    p = record()
    j = job(p)
    with pytest.raises(ValueError, match="unsorted or duplicate"):
        collect(tmp_path, {KEY: p}, [j, j], [alignment(j)] * 2)


def test_unsorted_06a_job(tmp_path):
    p = record()
    a = record(AUDIT_KEY, kind="AUDIT")
    j1, j2 = job(p), job(a)
    with pytest.raises(ValueError, match="unsorted or duplicate"):
        collect(
            tmp_path,
            {KEY: p, AUDIT_KEY: a},
            [j2, j1],
            [alignment(j2), alignment(j1)],
        )


@pytest.mark.parametrize(
    "column,wrong,message",
    [
        ("candidate_primary_accession", "X", "candidate accession"),
        ("target_optional_accession", "X", "target accession"),
        ("job_scope", "PRIMARY_FAKE", "source scope"),
        ("discovery_channel", "X", "discovery channel"),
        ("sequence_discovered", "0", "sequence provenance"),
        ("lineage_discovered", "1", "lineage provenance"),
        ("frag", "1", "fragment flag"),
        ("audit_class", "BAD", "unexpected audit class"),
        ("audit_key_source_reason", "BAD", "unexpected audit reason"),
        ("exact_sequence", "N/A", "expected 0 or 1"),
        ("target_length", "0", "nonpositive integer"),
    ],
)
def test_06a_disagreement(tmp_path, column, wrong, message):
    p = record()
    j = job(p)
    a = alignment(j)
    j[column] = wrong
    with pytest.raises(ValueError, match=message):
        collect(tmp_path, {KEY: p}, [j], [a])


@pytest.mark.parametrize(
    "column,wrong,message",
    [
        ("target_length", "3", "06A/06C1 mismatch"),
        ("candidate_global_status", "FAILED", "unexpected status"),
        ("target_global_target_end0_exclusive", "4", "target-span mismatch"),
        ("candidate_global_candidate_start0", "1", "candidate-span mismatch"),
        ("candidate_global_matches", "4", "residue-pair mismatch"),
        ("candidate_global_alignment_columns", "0", "alignment column"),
        ("candidate_global_cells_evaluated", "2", "DP-cell mismatch"),
        ("candidate_global_gap_in_candidate_residues", "1", "target residue"),
        ("candidate_global_target_aligned_span", "4", "target-span mismatch"),
        ("candidate_global_mismatches", "invalid", "invalid unsigned integer"),
    ],
)
def test_06c1_invalid_geometry(tmp_path, column, wrong, message):
    p = record()
    j = job(p)
    a = alignment(j)
    a[column] = wrong
    with pytest.raises(ValueError, match=message):
        collect(tmp_path, {KEY: p}, [j], [a])


def test_incorrect_exact_length(tmp_path):
    p = record()
    j = job(p, n=6, m=5)
    with pytest.raises(ValueError, match="different lengths"):
        collect(tmp_path, {KEY: p}, [j], [alignment(j)])


def test_missing_06c1_direction(tmp_path):
    p = record()
    j = job(p)
    a = alignment(j)
    del a["candidate_global_mismatches"]
    with pytest.raises(ValueError, match="invalid unsigned integer"):
        collect(tmp_path, {KEY: p}, [j], [a])


def test_audit_class_disagreement(tmp_path):
    a = record(AUDIT_KEY, kind="AUDIT")
    j = job(a)
    j["audit_key_source_reason"] = "CROSS_SPECIES"
    with pytest.raises(ValueError, match="audit reason mismatch"):
        collect(tmp_path, {AUDIT_KEY: a}, [j], [alignment(j)])


def test_sequence_only_does_not_make_geometry_a_direct_accession(tmp_path):
    p = record()
    j = job(p, n=10, m=4, exact=False)
    result = collect(tmp_path, {KEY: p}, [j], [alignment(j, "subsequence")])
    assert result[KEY].exact_subsequence
    assert p.discovery_channel == "SEQUENCE_ONLY"
    assert not p.lineage_discovered


def test_subsequence_requires_complete_candidate(tmp_path):
    p = record()
    j = job(p, n=10, m=4, exact=False)
    a = alignment(j, "subsequence")
    a["candidate_global_candidate_end0_exclusive"] = "3"
    a["candidate_global_candidate_aligned_span"] = "3"
    with pytest.raises(ValueError, match="candidate residue accounting"):
        collect(tmp_path, {KEY: p}, [j], [a])


def test_malformed_06a_field_fails_closed(tmp_path):
    p = record()
    j = job(p)
    j["rid"] = ""
    with pytest.raises(ValueError, match="empty candidate key"):
        collect(tmp_path, {KEY: p}, [j], [alignment(job(p))])


def test_fragment_metadata_stays_independent_of_alignment(tmp_path):
    p = replace(record(), fragment_record=True)
    j = job(p, exact=False)
    with pytest.raises(ValueError, match="DP-cell mismatch"):
        a = alignment(j, "variant")
        a["target_global_cells_evaluated"] = "0"
        collect(tmp_path, {KEY: p}, [j], [a])
