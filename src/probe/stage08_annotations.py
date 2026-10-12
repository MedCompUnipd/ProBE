"""Project already-parsed normalized-GAF assertions through Stage 07 donors.

No GO ontology, evidence registry, sequence matching, negative veto, persistent
cache, GAF scanner, or workflow is implemented here. Source provenance is
explicit and must be verified against manifests by the future collection runner.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections import Counter, defaultdict
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field

from probe.records import AnnotationRecord
from probe.stage08_donors import AssociationKey, DonorAssociation, DonorIndex

DirectGroupKey = tuple[str, str, str, str, bool]


@dataclass(frozen=True, slots=True)
class NormalizedGafSource:
    """Identity of the *normalized* source; not the pre-normalization GAF.

    sha256 identifies the stored source bytes as declared by validated upstream
    provenance. Constructing this value does not open or checksum the file.
    """

    release: str
    path: str
    sha256: str

    def __post_init__(self) -> None:
        for name in ("release", "path"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{name}: expected a nonempty string")
            if any(char in value for char in ("\t", "\r", "\n", "\x00")):
                raise ValueError(f"{name}: unexpected control character")
        if not isinstance(self.sha256, str) or not re.fullmatch(
            r"[0-9a-f]{64}", self.sha256
        ):
            raise ValueError("expected a lowercase SHA256 hex digest")


def _parts(value: str) -> tuple[str, ...]:
    return tuple(part for part in value.split("|") if part)


def _check_raw_fields(
    annotation: AnnotationRecord, raw_fields: tuple[str, ...]
) -> None:
    """Check an adapter's record against the retained raw 17 fields.

    This is a consistency check on an existing AnnotationRecord, not a second
    file parser. It mirrors the public representation of the existing parser.
    The exact qualifier and all raw fields remain available even when their
    structured representations omit empty pipe-separated components.
    """
    if not isinstance(raw_fields, tuple) or len(raw_fields) != 17:
        raise ValueError("raw_fields must be a tuple of 17 GAF fields")
    if any(
        not isinstance(value, str)
        or any(char in value for char in ("\t", "\r", "\n", "\x00"))
        for value in raw_fields
    ):
        raise ValueError("raw_fields contains a non-string or control character")
    if not raw_fields[0].strip() or not raw_fields[1].strip():
        raise ValueError("missing annotation database or subject identifier")
    if not raw_fields[6].strip():
        raise ValueError("missing evidence code")
    if raw_fields[8] not in {"F", "P", "C"}:
        raise ValueError("invalid annotation aspect")
    if not re.fullmatch(r"GO:[0-9]{7}", raw_fields[4]):
        raise ValueError("invalid GO identifier syntax")
    relation_parts = _parts(raw_fields[3])
    relations = tuple(value for value in relation_parts if value != "NOT")
    if len(relations) > 1:
        raise ValueError("multiple non-NOT relations in one GAF field")
    expected = (
        raw_fields[0],
        raw_fields[1],
        raw_fields[2],
        relations[0] if relations else "",
        raw_fields[4],
        "NOT" in relation_parts,
        _parts(raw_fields[5]),
        raw_fields[6],
        _parts(raw_fields[7]),
        raw_fields[8],
        raw_fields[9],
        _parts(raw_fields[10]),
        raw_fields[11],
        _parts(raw_fields[12]),
        raw_fields[13],
        raw_fields[14],
        _parts(raw_fields[15]),
        raw_fields[16],
    )
    observed = (
        annotation.database,
        annotation.subject_id,
        annotation.symbol,
        annotation.relation,
        annotation.term_id,
        annotation.negated,
        annotation.references,
        annotation.evidence,
        annotation.with_from,
        annotation.aspect,
        annotation.name,
        annotation.synonyms,
        annotation.object_type,
        annotation.taxa,
        annotation.date,
        annotation.assigned_by,
        annotation.extensions,
        annotation.gene_product_form_id,
    )
    if observed != expected:
        raise ValueError("AnnotationRecord disagrees with retained raw GAF fields")


@dataclass(frozen=True, slots=True)
class SourceAssertion:
    """One physical row of a complete normalized GAF, with its existing record.

    line_number counts the original physical lines, including comments/blanks.
    An adapter parsing a subset must restore source/line from an explicit mapping;
    a subset row number cannot be silently presented as the complete-file line.
    """

    source: NormalizedGafSource
    line_number: int
    annotation: AnnotationRecord
    raw_fields: tuple[str, ...]
    assertion_id: str = field(init=False)

    def __post_init__(self) -> None:
        if type(self.line_number) is not int or self.line_number < 1:
            raise ValueError("line_number must be a positive integer")
        if type(self.annotation.line) is not int:
            raise ValueError("AnnotationRecord must carry its original source line")
        if (
            self.annotation.source != self.source.path
            or self.annotation.line != self.line_number
        ):
            raise ValueError("AnnotationRecord source/line disagrees with provenance")
        _check_raw_fields(self.annotation, self.raw_fields)
        payload = json.dumps(
            [
                "stage08-normalized-gaf-assertion-v1",
                self.source.release,
                self.source.sha256,
                self.line_number,
            ],
            separators=(",", ":"),
            ensure_ascii=True,
        )
        object.__setattr__(
            self, "assertion_id", hashlib.sha256(payload.encode("utf-8")).hexdigest()
        )


@dataclass(frozen=True, slots=True)
class AnnotationLink:
    release: str
    target_id: str
    rid: str
    assertion_id: str


@dataclass(frozen=True, slots=True)
class ProjectedAssertion:
    """A source row once, plus every authorized target-record association."""

    assertion: SourceAssertion
    donors: tuple[DonorAssociation, ...]

    def links(self) -> Iterator[AnnotationLink]:
        for donor in self.donors:
            yield AnnotationLink(*donor.key, self.assertion.assertion_id)


def project_annotations(
    assertions: Iterable[SourceAssertion], *, donors: DonorIndex
) -> Iterator[ProjectedAssertion]:
    """Single-pass projection; no pooling of donor sets across releases.

    Assertions must retain source order within each release. The two releases
    may be interleaved. Skipped physical lines are allowed, so a prefiltered
    stream can preserve the original line numbers. Exhausting this iterator only
    exhausts the supplied stream; it does NOT certify a complete GAF scan.
    """
    sources: dict[str, NormalizedGafSource] = {}
    last_lines: dict[str, int] = {}
    for assertion in assertions:
        source = assertion.source
        donors.role_for(source.release)
        previous_source = sources.setdefault(source.release, source)
        if previous_source != source:
            raise ValueError("one projection cannot mix GAF sources for one release")
        if assertion.line_number <= last_lines.get(source.release, 0):
            raise ValueError("duplicate or non-increasing source line within release")
        last_lines[source.release] = assertion.line_number
        matches = donors.donors_for(
            release=source.release,
            database=assertion.annotation.database,
            accession=assertion.annotation.subject_id,
        )
        if matches:
            yield ProjectedAssertion(assertion, matches)


@dataclass(frozen=True, slots=True)
class DirectAnnotationGroup:
    release: str
    target_id: str
    aspect: str
    term_id: str
    negated: bool
    evidence_codes: frozenset[str]
    supports: tuple[AnnotationLink, ...]


@dataclass(frozen=True, slots=True)
class AssociationObservation:
    association: DonorAssociation
    positive_rows_observed: int
    not_rows_observed: int


@dataclass(frozen=True, slots=True)
class EvidenceObservation:
    release: str
    evidence_code: str
    positive_source_rows: int
    not_source_rows: int
    association_links: int
    distinct_donors: int
    distinct_targets: int
    aspects: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ProjectionSummary:
    """Summary of supplied observations, not a certificate of GAF coverage.

    Zero observed rows must not be interpreted as NO_RETAINED_GOA_ROWS until a
    later collection runner verifies completion of the required source scan.
    This optional reference reducer stores support IDs and is intended for unit
    tests/small extracted subsets, not an unprofiled full-release execution.
    """

    direct_groups: tuple[DirectAnnotationGroup, ...]
    associations: tuple[AssociationObservation, ...]
    evidence_inventory: tuple[EvidenceObservation, ...]
    source_row_count: int
    association_link_count: int
    inventory_scope: str = field(default="retrieved_donor_assertions", init=False)
    completeness: str = field(default="supplied_observations_only", init=False)


def summarize_projection(
    projected: Iterable[ProjectedAssertion], *, donors: DonorIndex
) -> ProjectionSummary:
    """Reference reducer with dynamic code inventory and preserved link IDs."""
    evidence: dict[DirectGroupKey, set[str]] = defaultdict(set)
    supports: dict[DirectGroupKey, list[AnnotationLink]] = defaultdict(list)
    positive: Counter[AssociationKey] = Counter()
    negative: Counter[AssociationKey] = Counter()
    inventory_positive: Counter[tuple[str, str]] = Counter()
    inventory_negative: Counter[tuple[str, str]] = Counter()
    inventory_links: Counter[tuple[str, str]] = Counter()
    inventory_donors: dict[tuple[str, str], set[str]] = defaultdict(set)
    inventory_targets: dict[tuple[str, str], set[str]] = defaultdict(set)
    inventory_aspects: dict[tuple[str, str], set[str]] = defaultdict(set)
    seen: set[str] = set()
    total_links = 0
    sources: dict[str, NormalizedGafSource] = {}
    for item in projected:
        assertion = item.assertion
        source = assertion.source
        annotation = assertion.annotation
        expected = donors.donors_for(
            release=source.release,
            database=annotation.database,
            accession=annotation.subject_id,
        )
        if not expected or item.donors != expected:
            raise ValueError("projected donors differ from complete authorized set")
        if sources.setdefault(source.release, source) != source:
            raise ValueError("mixed annotation sources for one release")
        if assertion.assertion_id in seen:
            raise ValueError("duplicate source assertion in projection summary")
        seen.add(assertion.assertion_id)
        inventory_key = source.release, annotation.evidence
        row_counter = inventory_negative if annotation.negated else inventory_positive
        row_counter[inventory_key] += 1
        inventory_aspects[inventory_key].add(annotation.aspect)
        for donor, link in zip(item.donors, item.links(), strict=True):
            group = (
                source.release,
                donor.target_id,
                annotation.aspect,
                annotation.term_id,
                annotation.negated,
            )
            evidence[group].add(annotation.evidence)
            supports[group].append(link)
            counter = negative if annotation.negated else positive
            counter[donor.key] += 1
            inventory_links[inventory_key] += 1
            inventory_donors[inventory_key].add(donor.rid)
            inventory_targets[inventory_key].add(donor.target_id)
            total_links += 1
    groups = tuple(
        DirectAnnotationGroup(
            *key,
            frozenset(evidence[key]),
            tuple(
                sorted(
                    supports[key],
                    key=lambda link: (link.rid, link.assertion_id),
                )
            ),
        )
        for key in sorted(evidence)
    )
    observations = tuple(
        AssociationObservation(row, positive[row.key], negative[row.key])
        for row in donors.associations
    )
    inventory = tuple(
        EvidenceObservation(
            *key,
            inventory_positive[key],
            inventory_negative[key],
            inventory_links[key],
            len(inventory_donors[key]),
            len(inventory_targets[key]),
            tuple(sorted(inventory_aspects[key])),
        )
        for key in sorted(inventory_links)
    )
    return ProjectionSummary(groups, observations, inventory, len(seen), total_links)
