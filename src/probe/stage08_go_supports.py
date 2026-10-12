"""Donor-level GO propagation with literal evidence and source provenance.

Library-only M10b. No GAF scan, selection, scoring, masking, or publication.
Input is an M1 ProjectedAssertion iterable; the production M8 bundle adapter
and streaming sort/aggregation are separate subsequent increments.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from itertools import groupby
from typing import TYPE_CHECKING

from probe.stage08_go_closure import GoClosureIndex

if TYPE_CHECKING:
    from probe.stage08_annotations import ProjectedAssertion

ASPECT_NAMES = {
    "F": "F",
    "P": "P",
    "C": "C",
    "molecular_function": "F",
    "biological_process": "P",
    "cellular_component": "C",
}


@dataclass(frozen=True, slots=True)
class GoPropagationSupport:
    """One propagated GO for one original source-row/donor/target link.

    Every inherited GO retains the *direct* GO and its literal GAF evidence,
    relation, source assertion ID, physical line number, and donor identity.
    """

    release: str
    target_id: str
    rid: str
    donor_accession: str
    assertion_id: str
    normalized_gaf_sha256: str
    normalized_gaf_line: int
    source_database: str
    aspect: str
    source_go_id: str
    go_id: str
    polarity: str
    evidence_code: str
    gaf_relation: str
    gaf_qualifier: str
    origin: str

    @property
    def term_key(self) -> tuple[str, str, str, str]:
        return self.release, self.target_id, self.aspect, self.go_id

    @property
    def source_link_key(self) -> tuple[str, str, str, str]:
        return self.release, self.target_id, self.rid, self.assertion_id


@dataclass(frozen=True, slots=True)
class TermEvidenceSummary:
    """Descriptive per-target term status; never a scoring decision."""

    release: str
    target_id: str
    aspect: str
    go_id: str
    has_positive: bool
    has_not: bool
    positive_evidence_codes: tuple[str, ...]
    not_evidence_codes: tuple[str, ...]
    positive_supports: int
    not_supports: int

    @property
    def is_conflict(self) -> bool:
        return self.has_positive and self.has_not


def propagate_projected_assertions(
    projected: Iterable[ProjectedAssertion],
    *,
    graph: GoClosureIndex,
) -> Iterator[GoPropagationSupport]:
    """Yield direct/inherited supports for each authorized M1 donor link.

    Does not apply evidence-code selection; unknown evidence codes are retained.
    Positive travels to ancestors; NOT travels to descendants. An overlap is
    kept as separate positive and NOT supports, with no veto or truth decision.
    Results are not guaranteed to be ordered by target/GO.
    """
    for item in projected:
        assertion = item.assertion
        annotation = assertion.annotation
        release = assertion.source.release
        direct = annotation.term_id
        try:
            graph_aspect = ASPECT_NAMES[graph.aspect(direct)]
        except KeyError as error:
            raise ValueError(
                f"Unknown GO or aspect in pinned graph: {direct}"
            ) from error
        if annotation.aspect != graph_aspect:
            raise ValueError(
                f"GAF aspect {annotation.aspect} disagrees with GO "
                f"aspect {graph_aspect} for {direct}"
            )
        if not item.donors:
            raise ValueError("Projected assertion must retain donor links")
        polarity = "NOT" if annotation.negated else "POSITIVE"
        closure = (
            graph.descendants(direct) if annotation.negated else graph.ancestors(direct)
        )
        observed_donors: set[tuple[str, str, str]] = set()
        for donor in item.donors:
            if donor.key in observed_donors:
                raise ValueError("Duplicate donor link for source assertion")
            observed_donors.add(donor.key)
            if (
                not donor.may_donate
                or donor.release != release
                or donor.candidate_accession != annotation.subject_id
            ):
                raise ValueError("Projected donor is not authorized for source")
            for term in closure:
                yield GoPropagationSupport(
                    release=release,
                    target_id=donor.target_id,
                    rid=donor.rid,
                    donor_accession=donor.candidate_accession,
                    assertion_id=assertion.assertion_id,
                    normalized_gaf_sha256=assertion.source.sha256,
                    normalized_gaf_line=assertion.line_number,
                    source_database=annotation.database,
                    aspect=graph_aspect,
                    source_go_id=direct,
                    go_id=term,
                    polarity=polarity,
                    evidence_code=annotation.evidence,
                    gaf_relation=annotation.relation,
                    gaf_qualifier=assertion.raw_fields[3],
                    origin="DIRECT" if term == direct else "INHERITED",
                )


def summarize_sorted_term_supports(
    supports: Iterable[GoPropagationSupport],
) -> Iterator[TermEvidenceSummary]:
    """Summarize an already sorted support stream; do not discard detail.

    Caller must sort by `term_key` (e.g. external sort at production scale).
    Unsorted keys fail closed. Memory use is bounded by the number of evidence
    codes within one target-term group, not the whole corpus.
    """
    previous_key: tuple[str, str, str, str] | None = None
    for key, rows in groupby(supports, key=lambda item: item.term_key):
        if previous_key is not None and key <= previous_key:
            raise ValueError("Term supports must be sorted by term_key")
        previous_key = key
        positive: set[str] = set()
        negative: set[str] = set()
        pos_count = 0
        neg_count = 0
        for row in rows:
            if row.polarity == "POSITIVE":
                positive.add(row.evidence_code)
                pos_count += 1
            elif row.polarity == "NOT":
                negative.add(row.evidence_code)
                neg_count += 1
            else:
                raise ValueError(f"Invalid propagated polarity: {row.polarity}")
        yield TermEvidenceSummary(
            *key,
            bool(positive),
            bool(negative),
            tuple(sorted(positive)),
            tuple(sorted(negative)),
            pos_count,
            neg_count,
        )
