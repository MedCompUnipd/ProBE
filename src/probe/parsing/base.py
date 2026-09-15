"""A deliberately small contract for streaming record parsers."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Protocol, TypeVar

from probe.source import Source
from probe.validation import ValidationReport

T = TypeVar("T", covariant=True)


class RecordParser(Protocol[T]):
    def iter_records(
        self,
        source: Source | str,
        *,
        report: ValidationReport | None = None,
        strict: bool = True,
    ) -> Iterator[T]: ...
