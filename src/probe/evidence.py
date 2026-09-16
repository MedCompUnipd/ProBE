"""Explicit policies for interpreting GO evidence changes."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from enum import IntEnum, StrEnum


class EvidenceCategory(StrEnum):
    EXPERIMENTAL = "experimental"
    PHYLOGENETIC = "phylogenetic"
    COMPUTATIONAL = "computational"
    AUTHOR = "author_statement"
    CURATOR = "curator_statement"
    ELECTRONIC = "electronic"
    UNKNOWN = "unknown"


class EvidenceTier(IntEnum):
    """ProBE's operational evidence order for cumulative benchmark profiles."""

    EXPERIMENTAL_TRADITIONAL = 1
    EXPERIMENTAL_HIGH_THROUGHPUT = 2
    PHYLOGENETIC_CURATED = 3
    TRACEABLE_CURATED_STATEMENT = 4
    COMPUTATIONAL_CURATED = 5
    ELECTRONIC = 6


EXPERIMENTAL_TRADITIONAL_CODES = frozenset({"EXP", "IDA", "IPI", "IMP", "IGI", "IEP"})
EXPERIMENTAL_HIGH_THROUGHPUT_CODES = frozenset({"HTP", "HDA", "HMP", "HGI", "HEP"})
PHYLOGENETIC_CURATED_CODES = frozenset({"IBA", "IBD", "IKR", "IRD"})
TRACEABLE_CURATED_STATEMENT_CODES = frozenset({"IC", "TAS"})
COMPUTATIONAL_CURATED_CODES = frozenset({"ISS", "ISO", "ISA", "ISM", "IGC", "RCA"})
ELECTRONIC_CODES = frozenset({"IEA"})
UNUSABLE_EVIDENCE_CODES = frozenset({"NAS", "ND"})

EVIDENCE_TIERS: dict[str, EvidenceTier] = {
    **{
        code: EvidenceTier.EXPERIMENTAL_TRADITIONAL
        for code in EXPERIMENTAL_TRADITIONAL_CODES
    },
    **{
        code: EvidenceTier.EXPERIMENTAL_HIGH_THROUGHPUT
        for code in EXPERIMENTAL_HIGH_THROUGHPUT_CODES
    },
    **{code: EvidenceTier.PHYLOGENETIC_CURATED for code in PHYLOGENETIC_CURATED_CODES},
    **{
        code: EvidenceTier.TRACEABLE_CURATED_STATEMENT
        for code in TRACEABLE_CURATED_STATEMENT_CODES
    },
    **{
        code: EvidenceTier.COMPUTATIONAL_CURATED for code in COMPUTATIONAL_CURATED_CODES
    },
    **{code: EvidenceTier.ELECTRONIC for code in ELECTRONIC_CODES},
}


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
    """An explicit cumulative evidence profile used for event classification."""

    name: str
    accepted_categories: frozenset[EvidenceCategory] = frozenset()
    upgrades: frozenset[tuple[EvidenceCategory, EvidenceCategory]] = frozenset()
    accepted_tiers: frozenset[EvidenceTier] = frozenset()

    @classmethod
    def _profile(
        cls,
        name: str,
        *accepted_tiers: EvidenceTier,
    ) -> EvidencePolicy:
        return cls(name=name, accepted_tiers=frozenset(accepted_tiers))

    @classmethod
    def experimental(cls) -> EvidencePolicy:
        """Compatibility preset accepting all experimental evidence."""

        return cls._profile(
            "experimental",
            EvidenceTier.EXPERIMENTAL_TRADITIONAL,
            EvidenceTier.EXPERIMENTAL_HIGH_THROUGHPUT,
        )

    @classmethod
    def experimental_strict(cls) -> EvidencePolicy:
        return cls._profile(
            "experimental_strict",
            EvidenceTier.EXPERIMENTAL_TRADITIONAL,
        )

    @classmethod
    def experimental_all(cls) -> EvidencePolicy:
        return cls._profile(
            "experimental_all",
            EvidenceTier.EXPERIMENTAL_TRADITIONAL,
            EvidenceTier.EXPERIMENTAL_HIGH_THROUGHPUT,
        )

    @classmethod
    def curated_phylogenetic(cls) -> EvidencePolicy:
        return cls._profile(
            "curated_phylogenetic",
            EvidenceTier.EXPERIMENTAL_TRADITIONAL,
            EvidenceTier.EXPERIMENTAL_HIGH_THROUGHPUT,
            EvidenceTier.PHYLOGENETIC_CURATED,
        )

    @classmethod
    def curated_traceable(cls) -> EvidencePolicy:
        return cls._profile(
            "curated_traceable",
            EvidenceTier.EXPERIMENTAL_TRADITIONAL,
            EvidenceTier.EXPERIMENTAL_HIGH_THROUGHPUT,
            EvidenceTier.PHYLOGENETIC_CURATED,
            EvidenceTier.TRACEABLE_CURATED_STATEMENT,
        )

    @classmethod
    def curated_non_electronic(cls) -> EvidencePolicy:
        return cls._profile(
            "curated_non_electronic",
            EvidenceTier.EXPERIMENTAL_TRADITIONAL,
            EvidenceTier.EXPERIMENTAL_HIGH_THROUGHPUT,
            EvidenceTier.PHYLOGENETIC_CURATED,
            EvidenceTier.TRACEABLE_CURATED_STATEMENT,
            EvidenceTier.COMPUTATIONAL_CURATED,
        )

    def category(self, evidence_code: str) -> EvidenceCategory:
        return EVIDENCE_CATEGORIES.get(evidence_code, EvidenceCategory.UNKNOWN)

    def tier(self, evidence_code: str) -> EvidenceTier | None:
        """Return a usable operational tier; NAS and ND never have one."""

        if evidence_code in UNUSABLE_EVIDENCE_CODES:
            return None
        return EVIDENCE_TIERS.get(evidence_code)

    def best_tier(self, evidence_codes: Iterable[str]) -> EvidenceTier | None:
        tiers = [tier for code in evidence_codes if (tier := self.tier(code))]
        return min(tiers, default=None)

    def accepts(self, evidence_codes: Iterable[str]) -> bool:
        if self.accepted_tiers:
            return any(
                tier in self.accepted_tiers
                for code in evidence_codes
                if (tier := self.tier(code)) is not None
            )
        return any(
            code not in UNUSABLE_EVIDENCE_CODES
            and self.category(code) in self.accepted_categories
            for code in evidence_codes
        )

    def is_upgrade(
        self,
        old_codes: frozenset[str],
        new_codes: frozenset[str],
    ) -> bool:
        if not old_codes or not new_codes or self.accepts(old_codes):
            return False
        if self.accepted_tiers:
            return self.accepts(new_codes)
        return any(
            (self.category(old), self.category(new)) in self.upgrades
            for old in old_codes
            for new in new_codes
            if old not in UNUSABLE_EVIDENCE_CODES and new not in UNUSABLE_EVIDENCE_CODES
        )
