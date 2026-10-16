"""Read-only Stage 07 candidate-universe adapter (increment 4).

Materialize every PRIMARY 05A2 pair and every independent 05C2 audit pair.
The original target accession comes from canonical Stage 01, not from a
candidate accession or a lineage string. No filtering, ranking or decisions.
"""

from __future__ import annotations

import csv
from collections.abc import Iterator, Mapping
from dataclasses import dataclass, replace
from pathlib import Path

from probe.resolver_anchors import (
    AuditDirectReference,
    CandidateAnchorReference,
    JoinedDirect,
    collect_direct_anchors,
)
from probe.resolver_lineage import (
    CandidateReference,
    JoinedLineage,
    collect_lineage,
)

CandidateKey = tuple[str, str, str]

_TARGET_COLS = frozenset({"target_id", "raw_taxid", "optional_accession"})
_COMMON_COLS = frozenset({"release", "target_id", "rid", "frag"})
_PRIMARY_COLS = _COMMON_COLS | frozenset(
    {
        "discovery_channel",
        "sequence_discovered",
        "go_positive_sequence_discovered",
        "full_v2_rescue_sequence_discovered",
        "lineage_discovered",
        "target_primary_bucket",
        "candidate_primary_accession",
        "raw_ox",
        "resolved_taxid",
        "species_taxid",
        "lin_distinct_lineage_key_count",
        "lin_key_collision_present",
    }
)
_AUDIT_COLS = _COMMON_COLS | frozenset(
    {
        "target_optional_accession",
        "target_raw_taxid",
        "target_resolved_taxid",
        "target_primary_bucket",
        "record_already_in_primary_union_for_target",
        "audit_class",
        "audit_alignment_allowed",
        "key_source_reason",
        "taxonomy_relation",
        "direct_match_type",
        "direct_match_accession",
        "candidate_primary_accession",
        "raw_ox",
        "anchor_resolved_taxid",
        "anchor_species_taxid",
        "sec",
    }
)


def _rows(path: Path, required: frozenset[str]) -> Iterator[tuple[int, dict]]:
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle, delimiter="\t")
        headers = reader.fieldnames
        if headers is None or len(headers) != len(set(headers)):
            raise ValueError(f"{path}: missing or duplicate TSV column names")
        missing = required - set(headers)
        if missing:
            raise ValueError(f"{path}: missing columns {sorted(missing)}")
        for line, row in enumerate(reader, start=2):
            if None in row or any(row[k] is None for k in required):
                raise ValueError(f"{path}:{line}: malformed TSV row")
            yield line, row


def _flag(value: str, context: str) -> bool:
    if value not in {"0", "1"}:
        raise ValueError(f"{context}: invalid binary flag {value!r}")
    return value == "1"


def _optional(value: str) -> str | None:
    return None if value in {"", "."} else value


def _identity(row: dict, context: str) -> CandidateKey:
    key = (row["release"], row["target_id"], row["rid"])
    if not all(key):
        raise ValueError(f"{context}: empty candidate identity")
    return key


@dataclass(frozen=True)
class CandidateRecord:
    """One and only one frozen-union identity, retaining its source scope."""

    release: str
    target_id: str
    rid: str
    scope: str
    target_accession: str
    candidate_accession: str
    candidate_raw_ox: str
    candidate_resolved_taxid: str | None
    candidate_species_taxid: str | None
    primary_bucket: str
    fragment_record: bool
    discovery_channel: str | None
    sequence_discovered: bool
    go_positive_sequence_discovered: bool
    full_v2_rescue_sequence_discovered: bool
    lineage_discovered: bool
    lineage_key_count: int
    lineage_collision_present: bool
    audit: AuditDirectReference | None
    overlap_audit: AuditDirectReference | None = None

    @property
    def key(self) -> CandidateKey:
        return (self.release, self.target_id, self.rid)

    def anchor_reference(self) -> CandidateAnchorReference:
        return CandidateAnchorReference(
            scope=self.scope,
            target_accession=self.target_accession,
            candidate_accession=self.candidate_accession,
            candidate_raw_ox=self.candidate_raw_ox,
            candidate_resolved_taxid=self.candidate_resolved_taxid,
            candidate_species_taxid=self.candidate_species_taxid,
            primary_bucket=self.primary_bucket,
            audit=(
                self.audit or (
                    self.overlap_audit
                    if self.overlap_audit is not None
                    and self.overlap_audit.alignment_allowed
                    else None
                )
            ),
            primary_audit_overlap=(
                self.scope == "PRIMARY"
                and self.overlap_audit is not None
                and self.overlap_audit.alignment_allowed
            ),
        )

    def lineage_reference(self) -> CandidateReference:
        return CandidateReference(
            primary_bucket=self.primary_bucket,
            candidate_accession=self.candidate_accession,
        )


def _targets(path: Path) -> dict[str, tuple[str, str]]:
    targets: dict[str, tuple[str, str]] = {}
    for line, row in _rows(path, _TARGET_COLS):
        context = f"{path}:{line}"
        target = row["target_id"]
        if not target or not row["raw_taxid"]:
            raise ValueError(f"{context}: empty target ID or raw TaxID")
        if target in targets:
            raise ValueError(f"{context}: duplicate Stage 01 target {target}")
        targets[target] = (row["raw_taxid"], row["optional_accession"])
    return targets


def _positive_integer(value: str, context: str) -> int:
    if not value.isascii() or not value.isdecimal():
        raise ValueError(f"{context}: invalid positive integer {value!r}")
    count = int(value)
    if count < 1:
        raise ValueError(f"{context}: nonpositive count")
    return count


def _primary(
    row: dict,
    context: str,
    target_accession: str,
) -> CandidateRecord:
    go = _flag(row["go_positive_sequence_discovered"], context)
    rescue = _flag(row["full_v2_rescue_sequence_discovered"], context)
    sequence = _flag(row["sequence_discovered"], context)
    lineage = _flag(row["lineage_discovered"], context)
    # Stage 2C FULL exact discovery is neither a GO-positive scanner hit
    # nor legacy FULL_V2_RESCUE. Preserve both historical flags as false.
    full_exact = row["discovery_channel"] == "STAGE2C_FULL_EXACT"
    if (
        (full_exact and (go or rescue))
        or sequence != (go or rescue or full_exact)
        or not (sequence or lineage)
    ):
        raise ValueError(f"{context}: inconsistent discovery provenance")
    if not row["discovery_channel"]:
        raise ValueError(f"{context}: missing discovery channel")
    raw_count = row["lin_distinct_lineage_key_count"]
    if lineage:
        count = _positive_integer(raw_count, context)
    elif raw_count in {"", ".", "0"}:
        count = 0
    else:
        raise ValueError(f"{context}: unexpected lineage count")
    raw_collision = row["lin_key_collision_present"]
    collision = False
    if lineage:
        collision = _flag(raw_collision, context)
    elif raw_collision not in {"", ".", "0"}:
        raise ValueError(f"{context}: unexpected lineage collision")
    key = _identity(row, context)
    if not all(
        (
            row["target_primary_bucket"],
            row["candidate_primary_accession"],
            row["raw_ox"],
        )
    ):
        raise ValueError(f"{context}: missing candidate metadata")
    return CandidateRecord(
        release=key[0],
        target_id=key[1],
        rid=key[2],
        scope="PRIMARY",
        target_accession=target_accession,
        candidate_accession=row["candidate_primary_accession"],
        candidate_raw_ox=row["raw_ox"],
        candidate_resolved_taxid=_optional(row["resolved_taxid"]),
        candidate_species_taxid=_optional(row["species_taxid"]),
        primary_bucket=row["target_primary_bucket"],
        fragment_record=_flag(row["frag"], context),
        discovery_channel=row["discovery_channel"],
        sequence_discovered=sequence,
        go_positive_sequence_discovered=go,
        full_v2_rescue_sequence_discovered=rescue,
        lineage_discovered=lineage,
        lineage_key_count=count,
        lineage_collision_present=collision,
        audit=None,
    )


def _audit(
    row: dict,
    context: str,
    target_info: tuple[str, str],
    *,
    allow_overlap: bool = False,
) -> CandidateRecord:
    raw_taxid, accession = target_info
    if row["target_raw_taxid"] != raw_taxid:
        raise ValueError(f"{context}: Stage 01/05C2 target TaxID mismatch")
    if row["target_optional_accession"] != accession:
        raise ValueError(f"{context}: Stage 01/05C2 target accession mismatch")
    overlap = _flag(row["record_already_in_primary_union_for_target"], context)
    if overlap != allow_overlap:
        if overlap:
            raise ValueError(
                f"{context}: marked already in PRIMARY but no PRIMARY row"
            )
        raise ValueError(f"{context}: inconsistent PRIMARY/audit overlap")
    key = _identity(row, context)
    if not all(
        (
            row["target_primary_bucket"],
            row["candidate_primary_accession"],
            row["raw_ox"],
        )
    ):
        raise ValueError(f"{context}: missing audited candidate metadata")
    direct_type = row["direct_match_type"]
    direct_accession = row["direct_match_accession"]
    if direct_type == "PRIMARY":
        if direct_accession != row["candidate_primary_accession"]:
            raise ValueError(f"{context}: audited PRIMARY accession mismatch")
    elif direct_type == "SECONDARY":
        if direct_accession not in row["sec"].split(","):
            raise ValueError(f"{context}: audited SECONDARY accession mismatch")
    else:
        raise ValueError(f"{context}: unknown audited direct match type")
    if direct_accession != accession:
        raise ValueError(f"{context}: audited target accession mismatch")
    audit = AuditDirectReference(
        audit_class=row["audit_class"],
        reason=row["key_source_reason"],
        alignment_allowed=_flag(row["audit_alignment_allowed"], context),
        direct_match_type=direct_type,
        direct_match_accession=direct_accession,
        taxonomy_relation=row["taxonomy_relation"],
        target_resolved_taxid=_optional(row["target_resolved_taxid"]),
        anchor_resolved_taxid=_optional(row["anchor_resolved_taxid"]),
    )
    return CandidateRecord(
        release=key[0],
        target_id=key[1],
        rid=key[2],
        scope="TAXONOMY_AUDIT",
        target_accession=accession,
        candidate_accession=row["candidate_primary_accession"],
        candidate_raw_ox=row["raw_ox"],
        candidate_resolved_taxid=_optional(row["anchor_resolved_taxid"]),
        candidate_species_taxid=_optional(row["anchor_species_taxid"]),
        primary_bucket=row["target_primary_bucket"],
        fragment_record=_flag(row["frag"], context),
        discovery_channel=None,
        sequence_discovered=False,
        go_positive_sequence_discovered=False,
        full_v2_rescue_sequence_discovered=False,
        lineage_discovered=False,
        lineage_key_count=0,
        lineage_collision_present=False,
        audit=audit,
    )


def load_candidate_universe(
    *,
    targets_path: Path,
    primary_path: Path,
    audit_path: Path,
    release_ids: frozenset[str],
) -> dict[CandidateKey, CandidateRecord]:
    """Preserve the complete 05A2+05C2 identity universe, no exclusions.

    `release_ids` is supplied from the already-validated START/END config.
    The taxonomy and 04B direct evidence still require independent joins.
    """
    if len(release_ids) != 2 or not all(release_ids):
        raise ValueError("Expected two distinct nonempty release IDs")
    targets = _targets(targets_path)
    result: dict[CandidateKey, CandidateRecord] = {}
    for scope, path, required in (
        ("PRIMARY", primary_path, _PRIMARY_COLS),
        ("TAXONOMY_AUDIT", audit_path, _AUDIT_COLS),
    ):
        for line, row in _rows(path, required):
            context = f"{path}:{line}"
            key = _identity(row, context)
            if key[0] not in release_ids:
                raise ValueError(f"{context}: unexpected release {key[0]}")
            target_info = targets.get(key[1])
            if target_info is None:
                raise ValueError(f"{context}: unknown Stage 01 target {key[1]}")
            if key in result:
                if (
                    scope != "TAXONOMY_AUDIT"
                    or result[key].scope != "PRIMARY"
                ):
                    raise ValueError(f"{context}: duplicate candidate key {key}")
                audit_record = _audit(
                    row, context, target_info, allow_overlap=True
                )
                original = result[key]
                if any((
                    audit_record.candidate_accession != original.candidate_accession,
                    audit_record.candidate_raw_ox != original.candidate_raw_ox,
                    audit_record.candidate_resolved_taxid
                    != original.candidate_resolved_taxid,
                    audit_record.candidate_species_taxid
                    != original.candidate_species_taxid,
                    audit_record.primary_bucket != original.primary_bucket,
                    audit_record.fragment_record != original.fragment_record,
                )):
                    raise ValueError(f"{context}: overlap audit identity mismatch")
                result[key] = replace(
                    original, overlap_audit=audit_record.audit
                )
                continue
            if scope == "PRIMARY":
                record = _primary(row, context, target_info[1])
            else:
                record = _audit(row, context, target_info)
            result[key] = record
    return result


def anchor_references(
    candidates: Mapping[CandidateKey, CandidateRecord],
) -> dict[CandidateKey, CandidateAnchorReference]:
    return {key: record.anchor_reference() for key, record in candidates.items()}


def lineage_references(
    candidates: Mapping[CandidateKey, CandidateRecord],
) -> dict[CandidateKey, CandidateReference]:
    """Include every PRIMARY row to detect unexpected 04D2 matches."""
    return {
        key: record.lineage_reference()
        for key, record in candidates.items()
        if record.scope == "PRIMARY"
    }


def validate_lineage_coverage(
    candidates: Mapping[CandidateKey, CandidateRecord],
    joined: Mapping[CandidateKey, JoinedLineage],
) -> None:
    """Check exact 05A2 vs independently reconstructed 04D2 coverage."""
    expected = {
        key
        for key, record in candidates.items()
        if record.scope == "PRIMARY" and record.lineage_discovered
    }
    actual = set(joined)
    if expected != actual:
        missing = sorted(expected - actual)[:3]
        extra = sorted(actual - expected)[:3]
        raise ValueError(f"04D2 lineage coverage mismatch: {missing}, {extra}")
    for key, fact in joined.items():
        ref = candidates[key]
        if len(fact.matched_typed_keys) != ref.lineage_key_count:
            raise ValueError(f"{key}: 04D2/05A2 typed-key count mismatch")
        if fact.collision_present != ref.lineage_collision_present:
            raise ValueError(f"{key}: 04D2/05A2 collision disagreement")


@dataclass(frozen=True)
class CandidateProvenance:
    """Single candidate plus independently checked documentary contexts."""

    candidate: CandidateRecord
    direct: JoinedDirect
    lineage: JoinedLineage | None


def collect_candidate_provenance(
    *,
    candidates: Mapping[CandidateKey, CandidateRecord],
    target_taxonomy_paths: Mapping[str, Path],
    classified_anchor_paths: Mapping[str, Path],
    index_path: Path,
    evidence_paths: Mapping[str, Path],
) -> dict[CandidateKey, CandidateProvenance]:
    """Join frozen 05A2/05C2 identity to independently verified 02B/04B/D.

    Does not read 06A/06C1 or emit a resolver decision. A missing direct
    anchor is a valid PRIMARY state; a missing independently verified 04B
    anchor for an audited pair is an error. No candidate can be invented.
    """
    direct = collect_direct_anchors(
        target_taxonomy_paths=target_taxonomy_paths,
        classified_anchor_paths=classified_anchor_paths,
        candidates=anchor_references(candidates),
    )
    if set(direct) != set(candidates):
        raise ValueError("02B/04B candidate coverage mismatch")
    lineage = collect_lineage(
        index_path=index_path,
        evidence_paths=evidence_paths,
        candidates=lineage_references(candidates),
    )
    validate_lineage_coverage(candidates, lineage)
    return {
        key: CandidateProvenance(record, direct[key], lineage.get(key))
        for key, record in candidates.items()
    }
