"""Strict 04D1/04D2 documentary join for Stage 07 (increment 2).

Reads frozen evidence without changing upstream scientific decisions. A matched
ACCESSION key is only documentary evidence, NOT proof of a taxonomy-eligible
04B direct anchor. That proof belongs to a later independent join.
"""

from __future__ import annotations

import csv
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from probe.resolver import MatchedLocusKey

CandidateKey = tuple[str, str, str]  # concrete release, target_id, rid
IndexKey = tuple[str, str, str, str, str]  # release, target, bucket, type, scalar

_D1_REQUIRED = frozenset(
    {
        "release",
        "target_id",
        "primary_bucket",
        "key_type",
        "key_value",
        "key_target_multiplicity",
        "key_collision",
        "from_target_accession",
        "from_start_anchor",
        "from_end_anchor",
        "source_roles",
    }
)
_D2_REQUIRED = frozenset(
    {
        "release",
        "target_id",
        "rid",
        "target_primary_bucket",
        "key_type",
        "key_value",
        "key_target_multiplicity",
        "key_collision",
        "from_target_accession",
        "from_start_anchor",
        "from_end_anchor",
        "source_roles",
        "candidate_primary_accession",
    }
)
_FACTS = (
    "key_target_multiplicity",
    "key_collision",
    "from_target_accession",
    "from_start_anchor",
    "from_end_anchor",
    "source_roles",
)
_TYPES = frozenset({"ACCESSION", "ORDERED_LOCUS", "ORF"})


@dataclass(frozen=True)
class CandidateReference:
    """Canonical 05A2/05C2 identity, verified independently of 04D2."""

    primary_bucket: str
    candidate_accession: str

    def __post_init__(self) -> None:
        if not self.primary_bucket or not self.candidate_accession:
            raise ValueError("Missing candidate bucket or accession")


@dataclass(frozen=True)
class JoinedLineage:
    """All individually validated documentary keys of one candidate pair."""

    accession_key_present: bool
    collision_present: bool
    matched_locus_keys: tuple[MatchedLocusKey, ...]
    matched_typed_keys: tuple[tuple[str, str], ...]

    @property
    def lineage_present(self) -> bool:
        return bool(self.matched_typed_keys)


@dataclass(frozen=True)
class _IndexFact:
    multiplicity: int
    collision: bool
    from_target: bool
    from_start: bool
    from_end: bool
    source_roles: str


def _rows(path: Path, required: frozenset[str]):
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle, delimiter="\t")
        if reader.fieldnames is None or not required.issubset(reader.fieldnames):
            missing = sorted(required - set(reader.fieldnames or ()))
            raise ValueError(f"{path}: missing required columns {missing}")
        for line_number, row in enumerate(reader, start=2):
            if None in row or any(row.get(k) is None for k in required):
                raise ValueError(f"{path}:{line_number}: malformed TSV row")
            yield line_number, row


def _bool(value: str, context: str) -> bool:
    if value not in {"0", "1"}:
        raise ValueError(f"{context}: expected 0/1, got {value!r}")
    return value == "1"


def _fact(row: dict[str, str], context: str) -> _IndexFact:
    try:
        multiplicity = int(row["key_target_multiplicity"])
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{context}: invalid key multiplicity") from exc
    if multiplicity < 1:
        raise ValueError(f"{context}: nonpositive key multiplicity")
    collision = _bool(row["key_collision"], context)
    if collision != (multiplicity > 1):
        raise ValueError(f"{context}: collision/multiplicity inconsistent")
    return _IndexFact(
        multiplicity=multiplicity,
        collision=collision,
        from_target=_bool(row["from_target_accession"], context),
        from_start=_bool(row["from_start_anchor"], context),
        from_end=_bool(row["from_end_anchor"], context),
        source_roles=row["source_roles"],
    )


def _key(row: dict[str, str], bucket_col: str, context: str) -> IndexKey:
    if row["key_type"] not in _TYPES:
        raise ValueError(f"{context}: unrecognized typed key {row['key_type']!r}")
    fields = ("release", "target_id", bucket_col, "key_type", "key_value")
    if any(not row[f] for f in fields):
        raise ValueError(f"{context}: missing typed key field")
    return tuple(row[f] for f in fields)  # type: ignore[return-value]


def collect_lineage(
    *,
    index_path: Path,
    evidence_paths: Mapping[str, Path],
    candidates: Mapping[CandidateKey, CandidateReference],
) -> dict[CandidateKey, JoinedLineage]:
    """Resolve all 04D2 typed rows for selected (release,target,rid) pairs.

    ``evidence_paths`` maps concrete release IDs to their 04D2 TSV files.
    Every encountered selected 04D2 key MUST match 04D1 *literally* within
    the same release, target, bucket, type, and scalar value; all source facts
    must agree. Unselected 04D2 pairs are intentionally ignored. No candidate
    is invented from 04D2; no global fanout cutoff or key decoding is used.

    Missing entries in the returned mapping mean no 04D2 documentary match.
    The caller must compare this fact with 05A2 lineage discovery provenance.
    """
    if not evidence_paths:
        raise ValueError("04D2 release mapping cannot be empty")
    if any(k[0] not in evidence_paths for k in candidates):
        raise ValueError("Candidate release missing from 04D2 release mapping")

    wanted_contexts = {
        (release, target, ref.primary_bucket)
        for (release, target, _), ref in candidates.items()
    }
    index: dict[IndexKey, _IndexFact] = {}
    for lineno, row in _rows(index_path, _D1_REQUIRED):
        context = f"{index_path}:{lineno}"
        k = _key(row, "primary_bucket", context)
        if k[:3] not in wanted_contexts:
            continue
        if k in index:
            raise ValueError(f"{context}: duplicate 04D1 typed key {k}")
        index[k] = _fact(row, context)

    joined: dict[CandidateKey, dict[tuple[str, str], _IndexFact]] = {}
    for release, path in evidence_paths.items():
        for lineno, row in _rows(path, _D2_REQUIRED):
            context = f"{path}:{lineno}"
            if row["release"] != release:
                raise ValueError(f"{context}: release contradicts 04D2 file mapping")
            pair = (release, row["target_id"], row["rid"])
            ref = candidates.get(pair)
            if ref is None:
                continue
            if row["target_primary_bucket"] != ref.primary_bucket:
                raise ValueError(f"{context}: 04D2 candidate bucket mismatch")
            if row["candidate_primary_accession"] != ref.candidate_accession:
                raise ValueError(f"{context}: 04D2 candidate accession mismatch")
            k = _key(row, "target_primary_bucket", context)
            index_fact = index.get(k)
            if index_fact is None:
                raise ValueError(f"{context}: missing authoritative 04D1 key {k}")
            row_fact = _fact(row, context)
            if row_fact != index_fact:
                differing = [
                    name
                    for name in _FACTS
                    if row[name] != _index_field(index_fact, name)
                ]
                raise ValueError(f"{context}: 04D1/04D2 disagreement: {differing}")
            typed = (row["key_type"], row["key_value"])
            found = joined.setdefault(pair, {})
            prior = found.get(typed)
            if prior is not None and prior != index_fact:
                raise ValueError(f"{context}: contradictory duplicate documentary key")
            found[typed] = index_fact

    result: dict[CandidateKey, JoinedLineage] = {}
    for pair, facts in joined.items():
        typed_keys = tuple(sorted(facts))
        loci = tuple(
            MatchedLocusKey(
                key_type=key_type,
                key_value=key_value,
                from_start_anchor=facts[(key_type, key_value)].from_start,
                from_end_anchor=facts[(key_type, key_value)].from_end,
                key_collision=facts[(key_type, key_value)].collision,
                index_verified=True,
            )
            for key_type, key_value in typed_keys
            if key_type in {"ORDERED_LOCUS", "ORF"}
        )
        result[pair] = JoinedLineage(
            accession_key_present=any(kind == "ACCESSION" for kind, _ in typed_keys),
            collision_present=any(f.collision for f in facts.values()),
            matched_locus_keys=loci,
            matched_typed_keys=typed_keys,
        )
    return result


def _index_field(fact: _IndexFact, name: str) -> str:
    if name == "key_target_multiplicity":
        return str(fact.multiplicity)
    if name == "source_roles":
        return fact.source_roles
    lookup = {
        "key_collision": fact.collision,
        "from_target_accession": fact.from_target,
        "from_start_anchor": fact.from_start,
        "from_end_anchor": fact.from_end,
    }
    return "1" if lookup[name] else "0"
