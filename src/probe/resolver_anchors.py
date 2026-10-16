"""Stage 07 read-only 02B/04B evidence adapter (increment 3).

Proves direct accession CONTINUITY from 04B, never from matching strings in
05A2/05C2. Keeps the strict upstream key-donation eligibility separate from
an explicitly audited same-species/subtaxon Stage 07 override. No discovery,
no taxonomy reclassification and no edits to frozen producer outputs.
"""

from __future__ import annotations

import csv
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

CandidateKey = tuple[str, str, str]  # release, target_id, rid
TargetKey = tuple[str, str]

_TARGET_COLS = frozenset(
    {
        "target_id",
        "raw_taxid",
        "resolution_status",
        "resolved_taxid",
        "resolved_rank",
        "species_taxid",
        "primary_bucket",
    }
)
_ANCHOR_COLS = frozenset(
    {
        "release",
        "target_id",
        "rid",
        "target_optional_accession",
        "direct_match_type",
        "direct_match_accession",
        "primary_accession",
        "raw_ox",
        "sec",
        "target_raw_taxid",
        "target_resolved_taxid",
        "target_resolved_rank",
        "target_species_taxid",
        "target_primary_bucket",
        "anchor_resolution_status",
        "anchor_resolved_taxid",
        "anchor_species_taxid",
        "taxonomy_relation",
        "key_source_eligible",
        "key_source_reason",
    }
)
_RESOLVED_STATES = frozenset({"CURRENT", "MERGED"})


def _taxid(value: str | None, context: str) -> str | None:
    if value is None or value in {"", "."}:
        return None
    if not value.isascii() or not value.isdecimal() or int(value) < 1:
        raise ValueError(f"{context}: invalid resolved TaxID {value!r}")
    return value


def _flag(value: str, context: str) -> bool:
    if value not in {"0", "1"}:
        raise ValueError(f"{context}: expected 0/1, received {value!r}")
    return value == "1"


def _rows(path: Path, columns: frozenset[str]):
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle, delimiter="\t")
        if not columns.issubset(reader.fieldnames or ()):
            raise ValueError(
                f"{path}: missing columns "
                f"{sorted(columns - set(reader.fieldnames or ()))}"
            )
        for line, row in enumerate(reader, start=2):
            if None in row or any(row.get(k) is None for k in columns):
                raise ValueError(f"{path}:{line}: malformed TSV row")
            yield line, row


@dataclass(frozen=True)
class AuditDirectReference:
    """Facts from one independent 05C2 audit row, NOT from 04B."""

    audit_class: str
    reason: str
    alignment_allowed: bool
    direct_match_type: str
    direct_match_accession: str
    taxonomy_relation: str
    target_resolved_taxid: str | None
    anchor_resolved_taxid: str | None

    def __post_init__(self) -> None:
        expected = {
            "SAME_SPECIES_WRONG_SUBTAXON": ("TAXONOMY_RESCUE_AUDIT", True),
            "CROSS_SPECIES": ("CROSS_SPECIES_AUDIT", False),
        }
        if self.reason not in expected:
            raise ValueError("Unknown 05C2 audit reason")
        if (self.audit_class, self.alignment_allowed) != expected[self.reason]:
            raise ValueError("Contradictory 05C2 audit class/alignment permission")
        if self.direct_match_type not in {"PRIMARY", "SECONDARY"}:
            raise ValueError("05C2 audit requires direct accession provenance")
        if not self.direct_match_accession or not self.taxonomy_relation:
            raise ValueError("Missing 05C2 documentary or taxonomy field")
        _taxid(self.target_resolved_taxid, "05C2 target")
        _taxid(self.anchor_resolved_taxid, "05C2 anchor")


@dataclass(frozen=True)
class CandidateAnchorReference:
    """Verified 05A2 or 05C2 candidate identity supplied by a later union reader.

    ``audit`` is mandatory for original TAXONOMY_AUDIT rows. It cannot be
    inferred from 04B, which must retain its strict original classification.
    Empty target_accession is permitted only when no direct match exists.
    """

    scope: str
    target_accession: str
    candidate_accession: str
    candidate_raw_ox: str
    candidate_resolved_taxid: str | None
    candidate_species_taxid: str | None
    primary_bucket: str
    audit: AuditDirectReference | None = None
    primary_audit_overlap: bool = False

    def __post_init__(self) -> None:
        if self.scope not in {"PRIMARY", "TAXONOMY_AUDIT"}:
            raise ValueError("Unknown 05A2/05C2 source scope")
        if self.scope == "TAXONOMY_AUDIT" and self.audit is None:
            raise ValueError("Audited source must retain its 05C2 facts")
        if self.scope == "PRIMARY":
            if self.primary_audit_overlap:
                if self.audit is None or not self.audit.alignment_allowed:
                    raise ValueError("Unverified PRIMARY/audit overlap")
            elif self.audit is not None:
                raise ValueError("PRIMARY cannot invent audit provenance")
        elif self.primary_audit_overlap:
            raise ValueError("PRIMARY/audit overlap on wrong source scope")
        if (
            not self.candidate_accession
            or not self.candidate_raw_ox
            or not self.primary_bucket
        ):
            raise ValueError("Missing candidate accession, raw taxon, or bucket")
        _taxid(self.candidate_resolved_taxid, "candidate")
        _taxid(self.candidate_species_taxid, "candidate species")


@dataclass(frozen=True)
class JoinedDirect:
    """Validated facts for constructing ResolverEvidence, not a decision."""

    direct_match_type: str | None
    direct_taxonomy_pass: bool
    audited_direct_verified: bool
    exact_resolved_taxid: bool
    target_resolved_taxid: str | None
    candidate_resolved_taxid: str | None
    original_04b_key_source_eligible: bool | None
    taxonomy_relation: str | None


@dataclass(frozen=True)
class _TargetFact:
    raw_taxid: str
    resolved_taxid: str | None
    resolved_rank: str
    species_taxid: str | None
    primary_bucket: str


def _load_target_taxonomy(
    paths: Mapping[str, Path], wanted: set[TargetKey]
) -> dict[TargetKey, _TargetFact]:
    result: dict[TargetKey, _TargetFact] = {}
    for release, path in paths.items():
        for line, row in _rows(path, _TARGET_COLS):
            key = (release, row["target_id"])
            if key not in wanted:
                continue
            context = f"{path}:{line}"
            if key in result:
                raise ValueError(f"{context}: duplicate 02B target taxonomy {key}")
            if not row["raw_taxid"] or not row["primary_bucket"]:
                raise ValueError(f"{context}: incomplete target taxonomy identity")
            taxid = _taxid(row["resolved_taxid"], context)
            if taxid is not None and row["resolution_status"] not in _RESOLVED_STATES:
                raise ValueError(f"{context}: resolved TaxID lacks verified resolution")
            result[key] = _TargetFact(
                raw_taxid=row["raw_taxid"],
                resolved_taxid=taxid,
                resolved_rank=row["resolved_rank"],
                species_taxid=_taxid(row["species_taxid"], context),
                primary_bucket=row["primary_bucket"],
            )
    missing = wanted - result.keys()
    if missing:
        raise ValueError(
            f"Missing 02B target taxonomy for {len(missing)} "
            f"candidate contexts; sample={sorted(missing)[:3]}"
        )
    return result


def _same_taxid(left: str | None, right: str | None) -> bool:
    return left is not None and right is not None and left == right


def _checked_anchor(
    row: dict[str, str],
    ref: CandidateAnchorReference,
    target: _TargetFact,
    context: str,
) -> JoinedDirect:
    direct_type = row["direct_match_type"]
    if direct_type not in {"PRIMARY", "SECONDARY"}:
        raise ValueError(f"{context}: unknown 04B direct match type")
    accession = row["direct_match_accession"]
    if not ref.target_accession or accession != ref.target_accession:
        raise ValueError(f"{context}: direct accession does not match original target")
    if row["target_optional_accession"] != ref.target_accession:
        raise ValueError(f"{context}: 04B target accession disagreement")
    if row["primary_accession"] != ref.candidate_accession:
        raise ValueError(f"{context}: 04B candidate accession mismatch")
    if row["raw_ox"] != ref.candidate_raw_ox:
        raise ValueError(f"{context}: 04B candidate raw taxonomy mismatch")
    if direct_type == "PRIMARY" and accession != ref.candidate_accession:
        raise ValueError(f"{context}: PRIMARY direct accession identity mismatch")
    if direct_type == "SECONDARY" and accession not in row["sec"].split(","):
        raise ValueError(
            f"{context}: SECONDARY direct accession absent from 04B secondary IDs"
        )
    checks = {
        "target_raw_taxid": target.raw_taxid,
        "target_resolved_taxid": target.resolved_taxid,
        "target_resolved_rank": target.resolved_rank,
        "target_species_taxid": target.species_taxid,
        "target_primary_bucket": target.primary_bucket,
    }
    for field, expected in checks.items():
        actual = row[field]
        if "taxid" in field:
            actual = _taxid(actual, context)
        if actual != expected:
            raise ValueError(f"{context}: 04B/02B disagreement in {field}")
    candidate_taxid = _taxid(ref.candidate_resolved_taxid, context)
    anchor_taxid = _taxid(row["anchor_resolved_taxid"], context)
    anchor_species = _taxid(row["anchor_species_taxid"], context)
    if candidate_taxid != anchor_taxid:
        raise ValueError(f"{context}: 04B candidate resolved TaxID mismatch")
    if _taxid(ref.candidate_species_taxid, context) != anchor_species:
        raise ValueError(f"{context}: 04B candidate species TaxID mismatch")
    if (
        anchor_taxid is not None
        and row["anchor_resolution_status"] not in _RESOLVED_STATES
    ):
        raise ValueError(f"{context}: anchor TaxID resolution status invalid")
    eligible = _flag(row["key_source_eligible"], context)
    audit_verified = False
    if ref.audit is not None:
        audit = ref.audit
        if (
            audit.direct_match_type != direct_type
            or audit.direct_match_accession != accession
            or audit.taxonomy_relation != row["taxonomy_relation"]
            or _taxid(audit.target_resolved_taxid, context) != target.resolved_taxid
            or _taxid(audit.anchor_resolved_taxid, context) != anchor_taxid
            or audit.reason != row["key_source_reason"]
        ):
            raise ValueError(f"{context}: 05C2/04B audit provenance disagreement")
        if eligible:
            raise ValueError(
                f"{context}: an audited exclusion cannot be 04B key eligible"
            )
        if audit.reason == "SAME_SPECIES_WRONG_SUBTAXON":
            audit_verified = (
                audit.alignment_allowed
                and row["taxonomy_relation"] == "SAME_SPECIES_DIFFERENT_SUBTAXON"
                and _same_taxid(target.species_taxid, anchor_species)
                and not _same_taxid(target.resolved_taxid, anchor_taxid)
            )
            if not audit_verified:
                raise ValueError(
                    f"{context}: inconsistent audited subtaxon override evidence"
                )
        elif row["taxonomy_relation"] != "CROSS_SPECIES":
            raise ValueError(f"{context}: inconsistent cross-species audit relation")
    return JoinedDirect(
        direct_match_type=direct_type,
        direct_taxonomy_pass=eligible and ref.scope == "PRIMARY",
        audited_direct_verified=audit_verified,
        exact_resolved_taxid=_same_taxid(target.resolved_taxid, candidate_taxid),
        target_resolved_taxid=target.resolved_taxid,
        candidate_resolved_taxid=candidate_taxid,
        original_04b_key_source_eligible=eligible,
        taxonomy_relation=row["taxonomy_relation"],
    )


def collect_direct_anchors(
    *,
    target_taxonomy_paths: Mapping[str, Path],
    classified_anchor_paths: Mapping[str, Path],
    candidates: Mapping[CandidateKey, CandidateAnchorReference],
) -> dict[CandidateKey, JoinedDirect]:
    """Return a fact record for EVERY requested pair, including non-direct pairs.

    The release-scoped 02B target context must exist for every selected pair.
    A direct 04B anchor is checked against its 05A2/05C2 candidate reference;
    no new candidate is introduced by scanning 04B. The 05C2 audit is never
    silently promoted: ``audited_direct_verified`` is only evidence for the
    ResolverPolicy-controlled decision kernel to consume later.
    """
    releases = {key[0] for key in candidates}
    if not releases.issubset(target_taxonomy_paths) or not releases.issubset(
        classified_anchor_paths
    ):
        raise ValueError("Missing 02B or 04B release path for selected candidates")
    for key, ref in candidates.items():
        if (
            len(key) != 3
            or not all(key)
            or not isinstance(ref, CandidateAnchorReference)
        ):
            raise ValueError("Malformed candidate key/reference")
    targets = _load_target_taxonomy(
        target_taxonomy_paths,
        {(release, target) for release, target, _ in candidates},
    )
    result = {
        key: JoinedDirect(
            direct_match_type=None,
            direct_taxonomy_pass=False,
            audited_direct_verified=False,
            exact_resolved_taxid=_same_taxid(
                targets[key[:2]].resolved_taxid,
                _taxid(ref.candidate_resolved_taxid, str(key)),
            ),
            target_resolved_taxid=targets[key[:2]].resolved_taxid,
            candidate_resolved_taxid=_taxid(ref.candidate_resolved_taxid, str(key)),
            original_04b_key_source_eligible=None,
            taxonomy_relation=None,
        )
        for key, ref in candidates.items()
    }
    seen: set[CandidateKey] = set()
    for release, path in classified_anchor_paths.items():
        for line, row in _rows(path, _ANCHOR_COLS):
            context = f"{path}:{line}"
            if row["release"] != release:
                raise ValueError(f"{context}: release contradicts 04B path mapping")
            key = (release, row["target_id"], row["rid"])
            ref = candidates.get(key)
            if ref is None:
                continue
            if key in seen:
                raise ValueError(
                    f"{context}: multiple 04B anchors for same candidate key"
                )
            seen.add(key)
            target = targets[(release, row["target_id"])]
            if target.primary_bucket != ref.primary_bucket:
                raise ValueError(f"{context}: candidate/02B primary bucket mismatch")
            result[key] = _checked_anchor(row, ref, target, context)
    for key, ref in candidates.items():
        if ref.scope == "TAXONOMY_AUDIT" and key not in seen:
            raise ValueError(
                f"{key}: 05C2 audit lacks independently verified 04B direct anchor"
            )
    return result
