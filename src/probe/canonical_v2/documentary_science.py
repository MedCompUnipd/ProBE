"""Build typed V2 documentary evidence without fabricating legacy 04D records.

Inherited EXACT metadata is a search anchor, never independent proof of
NONEXACT donor eligibility. Only independently supported typed locus keys
can satisfy the frozen Stage 07 stable-locus criterion.
"""

from __future__ import annotations

import csv
from collections import defaultdict
from collections.abc import Mapping
from dataclasses import replace
from pathlib import Path

from probe.canonical_v2.documentary_handoff import (
    EVIDENCE_FIELDS,
    _locus_support,
    _verify_row,
)
from probe.resolver import MatchedLocusKey, stable_unique_locus
from probe.resolver_candidates import (
    CandidateKey,
    CandidateProvenance,
    CandidateRecord,
)
from probe.resolver_lineage import JoinedLineage

VERSION = "probe-canonical-v2-documentary-science-v1"
_ALLOWED_ORIGINS = frozenset({"EXACT", "INDEPENDENT_04D1"})


def _roles(value: str, key_type: str) -> frozenset[str]:
    """Read literal typed 04D1 roles and the existing explicit anchor labels."""
    observed = frozenset(piece.strip() for piece in value.split(",") if piece.strip())
    expected = {f"START:{key_type}", f"END:{key_type}"}
    native = {item for item in observed if item.startswith(("START:", "END:"))}
    if native - expected:
        raise ValueError("Independent anchor role contradicts typed locus")
    return frozenset(
        f"{role}_ANCHOR"
        for role in ("START", "END")
        if f"{role}:{key_type}" in observed or f"{role}_ANCHOR" in observed
    )


def _row_key(row: Mapping[str, str]) -> CandidateKey:
    return row["release"], row["target_id"], row["candidate_rid"]


def read_documentary_facts(
    *,
    path: Path,
    releases: Mapping[str, str],
) -> dict[CandidateKey, JoinedLineage]:
    """Validate and classify V2 observations, including weak lookup hints.

    This is a provenance reader, not a GO approval or a DP selector.
    An inherited EXACT key remains weak here even if its spelling matches.
    Independent dual-anchor evidence alone may become a stable typed key.
    """
    if set(releases) != {"START", "END"}:
        raise ValueError("Missing START/END release identifiers")
    if len(set(releases.values())) != 2:
        raise ValueError("Identical START/END release identifiers")
    if not path.is_file() or path.is_symlink():
        raise ValueError("Missing or symlinked documentary observations")
    by_pair: dict[CandidateKey, dict] = defaultdict(
        lambda: {"typed": set(), "loci": {}, "collision": False}
    )
    with path.open(encoding="utf-8", newline="") as source:
        reader = csv.DictReader(source, delimiter="\t")
        if tuple(reader.fieldnames or ()) != EVIDENCE_FIELDS:
            raise ValueError("Documentary observations have the wrong schema")
        for number, row in enumerate(reader, 2):
            if None in row or any(value is None for value in row.values()):
                raise ValueError(f"Malformed documentary observation: {number}")
            _verify_row(row, row["release"], frozenset(releases.values()))
            if row["release"] not in releases.values():
                raise ValueError("Documentary observation has a foreign release")
            expected = "1" if _locus_support(row) else "0"
            if row["potential_unique_locus"] != expected:
                raise ValueError("Unverified potential locus support flag")
            identity = _row_key(row)
            state = by_pair[identity]
            if row["match_class"] != "LITERAL":
                continue
            kind = row["source_key_type"]
            value = row["source_key_value"]
            if kind != row["candidate_key_type"]:
                continue
            if value != row["candidate_key_value"]:
                raise ValueError("Literal matched key value disagrees")
            state["typed"].add((kind, value))
            if (
                int(row["target_key_multiplicity"]) > 1
                or int(row["candidate_key_multiplicity"]) > 1
            ):
                state["collision"] = True
            if expected != "1":
                continue
            if row["source_kind"] not in _ALLOWED_ORIGINS:
                raise ValueError("Unknown documentary source kind")
            if row["source_kind"] == "EXACT":
                # EXACT-origin metadata may discover NONEXACT, but does not
                # constitute an independently verified target locus anchor.
                continue
            roles = _roles(row["source_roles"], kind)
            key = kind, value
            found = state["loci"].get(key)
            current = MatchedLocusKey(
                key_type=kind,
                key_value=value,
                from_start_anchor="START_ANCHOR" in roles,
                from_end_anchor="END_ANCHOR" in roles,
                key_collision=False,
                index_verified=True,
            )
            if found is not None and found != current:
                raise ValueError("Conflicting independent locus provenance")
            state["loci"][key] = current
    result = {}
    for key, state in by_pair.items():
        if not state["typed"]:
            continue
        result[key] = JoinedLineage(
            accession_key_present=any(
                kind == "ACCESSION" for kind, _ in state["typed"]
            ),
            collision_present=state["collision"],
            matched_locus_keys=tuple(
                state["loci"][item] for item in sorted(state["loci"])
            ),
            matched_typed_keys=tuple(sorted(state["typed"])),
        )
    return result


def merge_documentary_facts(
    *,
    candidates: Mapping[CandidateKey, CandidateRecord],
    independently_checked: Mapping[CandidateKey, JoinedLineage],
    new_evidence: Mapping[CandidateKey, JoinedLineage],
) -> dict[CandidateKey, JoinedLineage]:
    """Combine source identities within the SAME release, never across them."""
    if not set(independently_checked).issubset(candidates):
        raise ValueError("Independent documentary proof outside candidates")
    if not set(new_evidence).issubset(candidates):
        raise ValueError("V2 documentary proof outside candidates")
    result = {}
    for key in candidates:
        original = independently_checked.get(key)
        latest = new_evidence.get(key)
        if original is None and latest is None:
            continue
        evidence = [item for item in (original, latest) if item is not None]
        typed = tuple(
            sorted(
                {
                    typed_key
                    for item in evidence
                    for typed_key in item.matched_typed_keys
                }
            )
        )
        loci: dict[tuple[str, str], MatchedLocusKey] = {}
        for item in evidence:
            for locus in item.matched_locus_keys:
                identity = locus.key_type, locus.key_value
                existing = loci.get(identity)
                if existing is None:
                    loci[identity] = locus
                    continue
                loci[identity] = MatchedLocusKey(
                    key_type=locus.key_type,
                    key_value=locus.key_value,
                    from_start_anchor=(
                        existing.from_start_anchor or locus.from_start_anchor
                    ),
                    from_end_anchor=(existing.from_end_anchor or locus.from_end_anchor),
                    key_collision=(existing.key_collision or locus.key_collision),
                    index_verified=(existing.index_verified and locus.index_verified),
                )
        result[key] = JoinedLineage(
            accession_key_present=any(item.accession_key_present for item in evidence),
            collision_present=any(item.collision_present for item in evidence),
            matched_locus_keys=tuple(loci[item] for item in sorted(loci)),
            matched_typed_keys=typed,
        )
    return result


def bind_scientific_provenance(
    *,
    candidate_facts: Mapping[CandidateKey, CandidateProvenance],
    documentary_facts: Mapping[CandidateKey, JoinedLineage],
) -> dict[CandidateKey, CandidateProvenance]:
    """Prepare actual native Stage 07 inputs; preserve all candidate keys."""
    if not set(documentary_facts).issubset(candidate_facts):
        raise ValueError("Documentary evidence for unknown candidate")
    result = {}
    for key, original in candidate_facts.items():
        lineage = documentary_facts.get(key, original.lineage)
        count = 0 if lineage is None else len(lineage.matched_typed_keys)
        candidate = replace(
            original.candidate,
            lineage_discovered=lineage is not None,
            lineage_key_count=count,
            lineage_collision_present=(
                False if lineage is None else lineage.collision_present
            ),
        )
        result[key] = replace(original, candidate=candidate, lineage=lineage)
    return result


def has_stable_documentary_locus(record: JoinedLineage | None) -> bool:
    """Return the frozen scientific criterion, not a new lexical criterion."""
    return record is not None and stable_unique_locus(
        record.matched_locus_keys, taxonomy_pass=True
    )
