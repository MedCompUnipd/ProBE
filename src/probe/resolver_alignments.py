"""Read-only Stage 07 joins to frozen 06A and canonical 06C1 alignments.

This module never runs alignment, searches for candidates, or converts a
sequence match into documentary evidence. All joined geometry is descriptive
until the separate resolver decision function evaluates validated evidence.
"""

from __future__ import annotations

import csv
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from pathlib import Path

from probe.resolver_candidates import CandidateKey, CandidateRecord

_BASE = (
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
_JOB_REQUIRED = frozenset(
    (
        *_BASE,
        "sequence_discovered",
        "lineage_discovered",
        "frag",
        "audit_class",
        "audit_key_source_reason",
        "exact_sequence",
    )
)
_DIRECTION_FIELDS = (
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
_ALIGNMENT_REQUIRED = frozenset(
    (
        *_BASE,
        *(
            f"{side}_{field}"
            for side in ("target_global", "candidate_global")
            for field in _DIRECTION_FIELDS
        ),
    )
)


def _rows(path: Path, required: frozenset[str]) -> Iterator[tuple[int, dict]]:
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle, delimiter="\t")
        names = reader.fieldnames
        if not names or len(names) != len(set(names)):
            raise ValueError(f"{path}: missing or duplicate TSV column names")
        missing = required - set(names)
        if missing:
            raise ValueError(f"{path}: missing columns {sorted(missing)}")
        for line, row in enumerate(reader, start=2):
            if None in row or any(row[field] is None for field in required):
                raise ValueError(f"{path}:{line}: malformed TSV row")
            yield line, row


def _positive(value: str, context: str) -> int:
    if not value.isascii() or not value.isdecimal():
        raise ValueError(f"{context}: invalid unsigned integer {value!r}")
    number = int(value)
    if number < 1:
        raise ValueError(f"{context}: nonpositive integer")
    return number


def _nonnegative(value: str, context: str) -> int:
    if not value.isascii() or not value.isdecimal():
        raise ValueError(f"{context}: invalid unsigned integer {value!r}")
    return int(value)


def _flag(value: str, context: str) -> bool:
    if value not in {"0", "1"}:
        raise ValueError(f"{context}: expected 0 or 1, got {value!r}")
    return value == "1"


def _key(row: dict, context: str) -> CandidateKey:
    key = (row["release"], row["target_id"], row["rid"])
    if not all(key):
        raise ValueError(f"{context}: empty candidate key")
    _positive(row["rid"], context)
    return key


def _order(key: CandidateKey) -> tuple[str, str, int]:
    return (key[0], key[1], int(key[2]))


def _check_job(row: dict, ref: CandidateRecord, context: str) -> bool:
    key = ref.key
    if _key(row, context) != key:
        raise ValueError(f"{context}: 06A candidate key mismatch")
    if row["job_scope"] != ref.scope:
        raise ValueError(f"{context}: 06A source scope mismatch")
    if row["candidate_primary_accession"] != ref.candidate_accession:
        raise ValueError(f"{context}: 06A candidate accession mismatch")
    if row["target_optional_accession"] != ref.target_accession:
        raise ValueError(f"{context}: 06A target accession mismatch")
    if _flag(row["frag"], context) != ref.fragment_record:
        raise ValueError(f"{context}: 06A fragment flag mismatch")
    _positive(row["target_length"], context)
    _positive(row["candidate_length"], context)
    if ref.scope == "PRIMARY":
        if row["discovery_channel"] != ref.discovery_channel:
            raise ValueError(f"{context}: 06A discovery channel mismatch")
        if _flag(row["sequence_discovered"], context) != ref.sequence_discovered:
            raise ValueError(f"{context}: 06A sequence provenance mismatch")
        if _flag(row["lineage_discovered"], context) != ref.lineage_discovered:
            raise ValueError(f"{context}: 06A lineage provenance mismatch")
        overlap = ref.overlap_audit
        if overlap is not None and overlap.alignment_allowed:
            if (
                row["audit_class"] != overlap.audit_class
                or row["audit_key_source_reason"] != overlap.reason
            ):
                raise ValueError(f"{context}: 06A overlap audit mismatch")
        else:
            if row["audit_class"] not in {"", "."}:
                raise ValueError(f"{context}: unexpected audit class")
            if row["audit_key_source_reason"] not in {"", "."}:
                raise ValueError(f"{context}: unexpected audit reason")
    else:
        if ref.audit is None:
            raise ValueError(f"{context}: missing audit provenance")
        if row["audit_class"] != ref.audit.audit_class:
            raise ValueError(f"{context}: 06A audit class mismatch")
        if row["audit_key_source_reason"] != ref.audit.reason:
            raise ValueError(f"{context}: 06A audit reason mismatch")
    exact = _flag(row["exact_sequence"], context)
    if exact and row["target_length"] != row["candidate_length"]:
        raise ValueError(f"{context}: exact sequences with different lengths")
    return exact


@dataclass(frozen=True, slots=True)
class AlignmentDirection:
    """Validated interval/count facts, with original orientation preserved."""

    status: str
    target_start: int
    target_end: int
    candidate_start: int
    candidate_end: int
    target_span: int
    candidate_span: int
    matches: int
    mismatches: int
    gap_in_target: int
    gap_in_candidate: int
    paired: int
    columns: int
    cells: int


def _direction(
    row: dict,
    side: str,
    *,
    target_length: int,
    candidate_length: int,
    exact: bool,
    context: str,
    expected_cells: int | None = None,
) -> AlignmentDirection:
    prefix = f"{side}_"
    status = row[prefix + "status"]
    expected_status = "OK_EXACT" if exact else "OK"
    if status != expected_status:
        raise ValueError(f"{context}: {side} unexpected status {status!r}")
    data = {}
    columns = {
        "target_start": "target_start0",
        "target_end": "target_end0_exclusive",
        "candidate_start": "candidate_start0",
        "candidate_end": "candidate_end0_exclusive",
        "target_span": "target_aligned_span",
        "candidate_span": "candidate_aligned_span",
        "matches": "matches",
        "mismatches": "mismatches",
        "gap_in_target": "gap_in_target_residues",
        "gap_in_candidate": "gap_in_candidate_residues",
        "paired": "aligned_residue_pairs",
        "columns": "alignment_columns",
        "cells": "cells_evaluated",
    }
    for name, field in columns.items():
        data[name] = _nonnegative(row[prefix + field], context)
    result = AlignmentDirection(status=status, **data)
    if not (0 <= result.target_start <= result.target_end <= target_length):
        raise ValueError(f"{context}: {side} invalid target interval")
    if not (0 <= result.candidate_start <= result.candidate_end <= candidate_length):
        raise ValueError(f"{context}: {side} invalid candidate interval")
    if result.target_span != result.target_end - result.target_start:
        raise ValueError(f"{context}: {side} target-span mismatch")
    if result.candidate_span != result.candidate_end - result.candidate_start:
        raise ValueError(f"{context}: {side} candidate-span mismatch")
    if result.paired != result.matches + result.mismatches:
        raise ValueError(f"{context}: {side} residue-pair mismatch")
    if result.paired + result.gap_in_candidate != result.target_span:
        raise ValueError(f"{context}: {side} target residue accounting mismatch")
    if result.paired + result.gap_in_target != result.candidate_span:
        raise ValueError(f"{context}: {side} candidate residue accounting mismatch")
    if result.columns != (
        result.paired + result.gap_in_candidate + result.gap_in_target
    ):
        raise ValueError(f"{context}: {side} alignment column mismatch")
    if side == "target_global":
        if result.target_start or result.target_end != target_length:
            raise ValueError(f"{context}: target-global incomplete target")
    elif result.candidate_start or result.candidate_end != candidate_length:
        raise ValueError(f"{context}: candidate-global incomplete candidate")
    full_cells = (target_length + 1) * (candidate_length + 1)
    required_cells = 0 if exact else full_cells
    if expected_cells is not None:
        if (
            type(expected_cells) is not int
            or exact
            and expected_cells != 0
            or not exact
            and not 1 <= expected_cells <= full_cells
        ):
            raise ValueError(f"{context}: invalid certified DP-cell expectation")
        required_cells = expected_cells
    if result.cells != required_cells:
        raise ValueError(f"{context}: {side} DP-cell mismatch")
    if exact and (
        result.target_start
        or result.candidate_start
        or result.target_end != target_length
        or result.candidate_end != candidate_length
        or result.paired != target_length
        or result.matches != target_length
        or result.mismatches
        or result.gap_in_target
        or result.gap_in_candidate
    ):
        raise ValueError(f"{context}: inconsistent exact geometry")
    return result


@dataclass(frozen=True, slots=True)
class JoinedAlignment:
    """One permitted pair, with 06A exactness and checked 06C1 geometry."""

    aligned: bool
    exact_sequence: bool | None
    target_length: int | None
    candidate_length: int | None
    exact_subsequence: bool
    equal_length_variant: bool
    target_global: AlignmentDirection | None
    candidate_global: AlignmentDirection | None
    policy_skipped: bool = False


def _make_alignment(
    job: dict,
    row: dict,
    *,
    exact: bool,
    context: str,
    expected_cells: tuple[int, int] | None = None,
) -> JoinedAlignment:
    for field in _BASE:
        if row[field] != job[field]:
            raise ValueError(f"{context}: 06A/06C1 mismatch in {field}")
    n = _positive(job["target_length"], context)
    m = _positive(job["candidate_length"], context)
    target = _direction(
        row,
        "target_global",
        target_length=n,
        candidate_length=m,
        exact=exact,
        context=context,
        expected_cells=None if expected_cells is None else expected_cells[0],
    )
    candidate = _direction(
        row,
        "candidate_global",
        target_length=n,
        candidate_length=m,
        exact=exact,
        context=context,
        expected_cells=None if expected_cells is None else expected_cells[1],
    )
    exact_subsequence = (
        not exact
        and m < n
        and candidate.target_span == m
        and candidate.candidate_span == m
        and candidate.matches == m
        and candidate.mismatches == 0
        and candidate.gap_in_target == 0
        and candidate.gap_in_candidate == 0
    )
    variant = (
        not exact
        and n == m
        and target.target_span == n
        and target.candidate_span == m
        and target.paired == n
        and target.mismatches > 0
        and target.gap_in_target == 0
        and target.gap_in_candidate == 0
    )
    return JoinedAlignment(
        aligned=True,
        exact_sequence=exact,
        target_length=n,
        candidate_length=m,
        exact_subsequence=exact_subsequence,
        equal_length_variant=variant,
        target_global=target,
        candidate_global=candidate,
    )


def _not_aligned(*, policy_skipped: bool = False) -> JoinedAlignment:
    return JoinedAlignment(
        aligned=False,
        exact_sequence=None,
        target_length=None,
        candidate_length=None,
        exact_subsequence=False,
        equal_length_variant=False,
        target_global=None,
        candidate_global=None,
        policy_skipped=policy_skipped,
    )


def collect_alignments(
    *,
    candidates: Mapping[CandidateKey, CandidateRecord],
    jobs_path: Path,
    alignments_path: Path,
    policy_skipped_keys: frozenset[CandidateKey] = frozenset(),
    expected_cells: Mapping[CandidateKey, tuple[int, int]] | None = None,
) -> dict[CandidateKey, JoinedAlignment]:
    """Require exactly the 06A/06C1 keys permitted by frozen taxonomy.

    06A and 06C1 are strictly sorted by release, target and numeric rid.
    Streaming their pairwise rows avoids retaining a second million-row TSV
    mapping. Only already-validated 06C1 measurements are interpreted.
    Certified counts must come from an independently pinned native DP plan;
    callers without that plan retain the exact full-rectangle requirement.
    """
    expected = {
        key
        for key, ref in candidates.items()
        if ref.scope == "PRIMARY"
        or (ref.audit is not None and ref.audit.alignment_allowed)
    }
    result = {
        key: _not_aligned()
        for key, ref in candidates.items()
        if ref.scope == "TAXONOMY_AUDIT"
        and ref.audit is not None
        and not ref.audit.alignment_allowed
    }
    if not policy_skipped_keys.issubset(expected):
        raise ValueError("Policy-skipped keys outside alignment jobs")
    aligned = _rows(alignments_path, _ALIGNMENT_REQUIRED)
    next_aligned = next(aligned, None)
    previous = None
    measured_nonexact = set()
    for line, job in _rows(jobs_path, _JOB_REQUIRED):
        context = f"{jobs_path}:{line}"
        key = _key(job, context)
        ordered = _order(key)
        if previous is not None and ordered <= previous:
            raise ValueError(f"{context}: unsorted or duplicate 06A job key")
        previous = ordered
        if key not in expected:
            raise ValueError(f"{context}: unexpected 06A alignment job {key}")
        if key in policy_skipped_keys:
            exact = _check_job(job, candidates[key], context)
            if exact:
                raise ValueError(f"{context}: cannot skip an EXACT pair")
            result[key] = _not_aligned(policy_skipped=True)
            continue
        if next_aligned is None:
            raise ValueError(f"{context}: 06C1 missing alignment {key}")
        aln_line, row = next_aligned
        aln_context = f"{alignments_path}:{aln_line}"
        if _key(row, aln_context) != key:
            raise ValueError(f"{aln_context}: 06C1/06A pair key mismatch")
        exact = _check_job(job, candidates[key], context)
        pair_cells = None
        if not exact:
            measured_nonexact.add(key)
            if expected_cells is not None:
                if key not in expected_cells or len(expected_cells[key]) != 2:
                    raise ValueError(f"{context}: missing certified DP-cell plan")
                pair_cells = expected_cells[key]
        result[key] = _make_alignment(
            job,
            row,
            exact=exact,
            context=aln_context,
            expected_cells=pair_cells,
        )
        next_aligned = next(aligned, None)
    if next_aligned is not None:
        line, row = next_aligned
        raise ValueError(
            f"{alignments_path}:{line}: extra 06C1 alignment for "
            f"{_key(row, alignments_path.as_posix())}"
        )
    if expected_cells is not None and set(expected_cells) != measured_nonexact:
        raise ValueError("Certified DP-cell plan has missing or extra pairs")
    actual = set(result)
    if actual != set(candidates):
        missing = sorted(set(candidates) - actual)[:3]
        extra = sorted(actual - set(candidates))[:3]
        raise ValueError(f"06A/06C1 coverage mismatch: {missing}, {extra}")
    return result
