"""Explicit policies for interpreting GO evidence changes."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class EvidenceCategory(StrEnum):
    EXPERIMENTAL = "experimental"
    PHYLOGENETIC = "phylogenetic"
    COMPUTATIONAL = "computational"
    AUTHOR = "author_statement"
    CURATOR = "curator_statement"
    ELECTRONIC = "electronic"
    UNKNOWN = "unknown"


EVIDENCE_CATEGORIES: dict[str, EvidenceCategory] = {
    **{
        code: EvidenceCategory.EXPERIMENTAL
        for code in (
            "EXP",
            "IDA",
            "IPI",
            "IMP",
            "IGI",
            "IEP",
            "HTP",
            "HDA",
            "HMP",
            "HGI",
            "HEP",
        )
    },
    **{code: EvidenceCategory.PHYLOGENETIC for code in ("IBA", "IBD", "IKR", "IRD")},
    **{
        code: EvidenceCategory.COMPUTATIONAL
        for code in ("ISS", "ISO", "ISA", "ISM", "IGC", "RCA")
    },
    "TAS": EvidenceCategory.AUTHOR,
    "NAS": EvidenceCategory.AUTHOR,
    "IC": EvidenceCategory.CURATOR,
    "ND": EvidenceCategory.CURATOR,
    "IEA": EvidenceCategory.ELECTRONIC,
}


@dataclass(frozen=True, slots=True)
class EvidencePolicy:
    """Accepted categories and category transitions considered improvements."""

    name: str
    accepted_categories: frozenset[EvidenceCategory]
    upgrades: frozenset[tuple[EvidenceCategory, EvidenceCategory]]

    @classmethod
    def experimental(cls) -> EvidencePolicy:
        target = EvidenceCategory.EXPERIMENTAL
        sources = set(EvidenceCategory) - {target}
        return cls(
            name="experimental",
            accepted_categories=frozenset({target}),
            upgrades=frozenset((source, target) for source in sources),
        )

    def category(self, evidence_code: str) -> EvidenceCategory:
        return EVIDENCE_CATEGORIES.get(evidence_code, EvidenceCategory.UNKNOWN)

    def accepts(self, evidence_codes: frozenset[str]) -> bool:
        return any(
            self.category(code) in self.accepted_categories for code in evidence_codes
        )

    def is_upgrade(
        self,
        old_codes: frozenset[str],
        new_codes: frozenset[str],
    ) -> bool:
        if not old_codes or not new_codes or self.accepts(old_codes):
            return False
        return any(
            (self.category(old), self.category(new)) in self.upgrades
            for old in old_codes
            for new in new_codes
        )
