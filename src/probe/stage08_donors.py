"""Read Stage 07 associations without reinterpreting resolver decisions.

This is a library boundary, not a workflow runner. The future runner must
validate upstream manifests before calling it. No FASTA or GOA is opened here.
"""

from __future__ import annotations

import csv
from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType

ACCEPTED_COLUMNS = (
    "release",
    "target_id",
    "rid",
    "target_accession",
    "candidate_accession",
    "annotation_donor",
    "decision_basis",
    "sequence_relation",
    "exact_sequence",
    "scope",
    "evaluation_scope",
    "audit_outcome",
    "accepted_candidates_for_target_release",
)

AssociationKey = tuple[str, str, str]
DonorLookupKey = tuple[str, str, str]


def _scalar(value: str, label: str, *, allow_dot: bool = False) -> None:
    if not isinstance(value, str) or not value or not value.strip():
        raise ValueError(f"{label}: expected a nonempty string")
    if any(char in value for char in ("\t", "\r", "\n", "\x00")):
        raise ValueError(f"{label}: unexpected control character")
    if value != value.strip():
        raise ValueError(f"{label}: leading/trailing whitespace is not allowed")
    if value == "." and not allow_dot:
        raise ValueError(f"{label}: missing value")


def _positive_integer(value: str, label: str) -> int:
    if not value.isascii() or not value.isdecimal() or int(value) < 1:
        raise ValueError(f"{label}: expected a positive ASCII integer")
    return int(value)


@dataclass(frozen=True, slots=True)
class DonorAssociation:
    """One unchanged 13-column ACCEPTED projection row from Stage 07.

    Strings, including numeric spellings, remain exactly as supplied. Validation
    checks the structural contract, not whether the resolver should have accepted
    a candidate. A donor NO remains in the index but never authorizes a link.
    """

    release: str
    target_id: str
    rid: str
    target_accession: str
    candidate_accession: str
    annotation_donor: str
    decision_basis: str
    sequence_relation: str
    exact_sequence: str
    scope: str
    evaluation_scope: str
    audit_outcome: str
    accepted_candidates_for_target_release: str

    def __post_init__(self) -> None:
        optional = {"target_accession", "audit_outcome"}
        for name in ACCEPTED_COLUMNS:
            _scalar(getattr(self, name), name, allow_dot=name in optional)
        _positive_integer(self.rid, "rid")
        _positive_integer(
            self.accepted_candidates_for_target_release,
            "accepted_candidates_for_target_release",
        )
        if self.annotation_donor not in {"YES", "NO"}:
            raise ValueError("annotation_donor must be YES or NO")
        if self.exact_sequence not in {"0", "1"}:
            raise ValueError("exact_sequence must be 0 or 1")
        for name in ("scope", "evaluation_scope"):
            if getattr(self, name) not in {"PRIMARY", "TAXONOMY_AUDIT"}:
                raise ValueError(f"{name}: unknown Stage 07 scope")

    @property
    def key(self) -> AssociationKey:
        return self.release, self.target_id, self.rid

    @property
    def may_donate(self) -> bool:
        return self.annotation_donor == "YES"

    @property
    def declared_multiplicity(self) -> int:
        return int(self.accepted_candidates_for_target_release)

    def tsv_values(self) -> tuple[str, ...]:
        return tuple(getattr(self, name) for name in ACCEPTED_COLUMNS)


@dataclass(frozen=True, slots=True, init=False)
class DonorIndex:
    """Release-qualified, many-to-many authorization index.

    The source database is explicit. For the current Stage 07 UniProt contract
    it is UniProtKB; aliases such as sp/tr are not inferred. Building the index
    consumes the complete accepted projection, including donor NO rows, so its
    declared group multiplicities can be checked faithfully.
    """

    associations: tuple[DonorAssociation, ...]
    start_release_id: str
    end_release_id: str
    source_database: str
    by_key: Mapping[AssociationKey, DonorAssociation] = field(repr=False)
    _lookup: Mapping[DonorLookupKey, tuple[DonorAssociation, ...]] = field(repr=False)

    def __init__(
        self,
        associations: Iterable[DonorAssociation],
        *,
        start_release_id: str,
        end_release_id: str,
        source_database: str,
    ) -> None:
        for label, value in (
            ("start_release_id", start_release_id),
            ("end_release_id", end_release_id),
            ("source_database", source_database),
        ):
            _scalar(value, label)
        if start_release_id == end_release_id:
            raise ValueError("START and END must identify distinct releases")
        releases = {start_release_id, end_release_id}
        ordered = tuple(sorted(associations, key=lambda item: item.key))
        by_key: dict[AssociationKey, DonorAssociation] = {}
        counts: Counter[tuple[str, str]] = Counter()
        declared: dict[tuple[str, str], int] = {}
        accession_by_record: dict[tuple[str, str], str] = {}
        lookup: dict[DonorLookupKey, list[DonorAssociation]] = defaultdict(list)
        for row in ordered:
            if row.release not in releases:
                raise ValueError(f"unknown release in association: {row.release!r}")
            if row.key in by_key:
                raise ValueError(f"duplicate association key: {row.key!r}")
            by_key[row.key] = row
            record_key = row.release, row.rid
            accession = accession_by_record.setdefault(
                record_key, row.candidate_accession
            )
            if accession != row.candidate_accession:
                raise ValueError(f"inconsistent accession for record: {record_key!r}")
            group = row.release, row.target_id
            previous = declared.setdefault(group, row.declared_multiplicity)
            if previous != row.declared_multiplicity:
                raise ValueError(f"inconsistent declared multiplicity: {group!r}")
            counts[group] += 1
            if row.may_donate:
                key = row.release, source_database, row.candidate_accession
                lookup[key].append(row)
        for group, count in counts.items():
            if declared[group] != count:
                raise ValueError(f"observed ACCEPTED multiplicity mismatch: {group!r}")
        object.__setattr__(self, "associations", ordered)
        object.__setattr__(self, "start_release_id", start_release_id)
        object.__setattr__(self, "end_release_id", end_release_id)
        object.__setattr__(self, "source_database", source_database)
        object.__setattr__(self, "by_key", MappingProxyType(by_key))
        object.__setattr__(
            self,
            "_lookup",
            MappingProxyType({key: tuple(rows) for key, rows in lookup.items()}),
        )

    def role_for(self, release: str) -> str:
        if release == self.start_release_id:
            return "START"
        if release == self.end_release_id:
            return "END"
        raise ValueError(f"unknown annotation release: {release!r}")

    def donors_for(
        self, *, release: str, database: str, accession: str
    ) -> tuple[DonorAssociation, ...]:
        self.role_for(release)
        return self._lookup.get((release, database, accession), ())

    def donor_accessions(self, release: str) -> frozenset[str]:
        self.role_for(release)
        return frozenset(
            accession for rel, _database, accession in self._lookup if rel == release
        )


def read_accepted_associations(
    path: str | Path,
    *,
    start_release_id: str,
    end_release_id: str,
    source_database: str,
) -> DonorIndex:
    """Read a complete Stage 07 projection using literal TSV field semantics.

    The caller supplies release IDs from validated metadata, not from directory
    names. This function does not validate a scientific bundle or its checksum.
    """
    source = Path(path)
    rows: list[DonorAssociation] = []
    with source.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.reader(handle, delimiter="\t", quoting=csv.QUOTE_NONE)
        if tuple(next(reader, ())) != ACCEPTED_COLUMNS:
            raise ValueError(f"{source}: unexpected Stage 07 accepted header")
        for values in reader:
            if len(values) != len(ACCEPTED_COLUMNS):
                raise ValueError(
                    f"{source}:{reader.line_num}: expected 13 fields, "
                    f"found {len(values)}"
                )
            try:
                rows.append(DonorAssociation(*values))
            except ValueError as error:
                raise ValueError(f"{source}:{reader.line_num}: {error}") from error
    return DonorIndex(
        rows,
        start_release_id=start_release_id,
        end_release_id=end_release_id,
        source_database=source_database,
    )
