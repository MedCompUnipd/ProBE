"""Exact protein sequence identity and identifier aliasing."""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from probe.parsing.fasta import FastaParser
from probe.records import SequenceRecord
from probe.source import Source
from probe.validation import ValidationReport


def normalize_sequence(sequence: str) -> str:
    """Apply ProBE's stable normalization policy before exact comparison."""

    return "".join(sequence.split()).upper()


def sequence_digest(sequence: str) -> str:
    return hashlib.sha256(normalize_sequence(sequence).encode("ascii")).hexdigest()


@dataclass(frozen=True, slots=True, order=True)
class SequenceAlias:
    identifier: str
    source: str


@dataclass(frozen=True, slots=True)
class SequenceMatch:
    target_id: str
    sequence_id: str
    aliases: tuple[SequenceAlias, ...]


@dataclass(frozen=True, slots=True)
class IdentityMap:
    matches: tuple[SequenceMatch, ...]
    unmatched_targets: tuple[str, ...]

    @property
    def subject_ids(self) -> frozenset[str]:
        identifiers = {match.target_id for match in self.matches}
        identifiers.update(
            alias.identifier for match in self.matches for alias in match.aliases
        )
        return frozenset(identifiers)

    def aliases_for(self, sequence_id: str) -> tuple[SequenceAlias, ...]:
        aliases = {
            alias
            for match in self.matches
            if match.sequence_id == sequence_id
            for alias in match.aliases
        }
        return tuple(sorted(aliases))


@dataclass(frozen=True, slots=True)
class SequenceDataset:
    records: tuple[SequenceRecord, ...]
    validation: ValidationReport

    @classmethod
    def read(
        cls,
        source: Source | str | Path,
        *,
        strict: bool = True,
    ) -> SequenceDataset:
        report = ValidationReport()
        records = FastaParser().parse(source, report=report, strict=strict)
        return cls(records, report)

    def __len__(self) -> int:
        return len(self.records)


class SequenceIndex:
    """An in-memory exact index that verifies sequences after hash lookup."""

    def __init__(self) -> None:
        self._buckets: dict[tuple[str, int], dict[str, set[SequenceAlias]]] = {}
        self.validation = ValidationReport()

    @classmethod
    def from_fasta(
        cls,
        source: Source | str | Path,
        *,
        source_name: str | None = None,
        strict: bool = True,
    ) -> SequenceIndex:
        index = cls()
        index.add_fasta(source, source_name=source_name, strict=strict)
        return index

    @classmethod
    def from_fastas(
        cls,
        sources: Mapping[str, Source | str | Path],
        *,
        strict: bool = True,
    ) -> SequenceIndex:
        index = cls()
        for source_name, source in sources.items():
            index.add_fasta(source, source_name=source_name, strict=strict)
        return index

    def add_fasta(
        self,
        source: Source | str | Path,
        *,
        source_name: str | None = None,
        strict: bool = True,
    ) -> None:
        artifact = Source.from_value(source)
        source_name = source_name or artifact.name
        report = ValidationReport()
        for record in FastaParser().iter_records(
            artifact, report=report, strict=strict
        ):
            self.add(record.identifier, record.sequence, source=source_name)
        self.validation.extend(report)

    def add(self, identifier: str, sequence: str, *, source: str) -> None:
        normalized = normalize_sequence(sequence)
        key = (sequence_digest(normalized), len(normalized))
        by_sequence = self._buckets.setdefault(key, {})
        by_sequence.setdefault(normalized, set()).add(SequenceAlias(identifier, source))

    def match(self, targets: SequenceDataset) -> IdentityMap:
        matches: list[SequenceMatch] = []
        unmatched: list[str] = []
        for target in targets.records:
            normalized = normalize_sequence(target.sequence)
            digest = sequence_digest(normalized)
            key = (digest, len(normalized))
            aliases = self._buckets.get(key, {}).get(normalized)
            if not aliases:
                unmatched.append(target.identifier)
                continue
            matches.append(
                SequenceMatch(
                    target_id=target.identifier,
                    sequence_id=f"sha256:{digest}",
                    aliases=tuple(sorted(aliases)),
                )
            )
        return IdentityMap(tuple(matches), tuple(unmatched))

    def __len__(self) -> int:
        return sum(len(sequences) for sequences in self._buckets.values())
