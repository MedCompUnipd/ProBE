"""Reopenable local inputs with transparent gzip support."""

from __future__ import annotations

import gzip
import hashlib
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO, TextIO


@dataclass(frozen=True, slots=True)
class Source:
    """A local artifact that can be opened repeatedly and checksummed."""

    path: Path

    @classmethod
    def from_value(cls, value: Source | str | Path) -> Source:
        if isinstance(value, cls):
            return value
        return cls(Path(value))

    @property
    def name(self) -> str:
        return str(self.path)

    @property
    def is_gzip(self) -> bool:
        return self.path.suffix.lower() == ".gz"

    @contextmanager
    def open_binary(self) -> Iterator[BinaryIO]:
        opener = gzip.open if self.is_gzip else open
        with opener(self.path, "rb") as handle:
            yield handle

    @contextmanager
    def open_text(self) -> Iterator[TextIO]:
        opener = gzip.open if self.is_gzip else open
        with opener(self.path, "rt", encoding="utf-8", newline=None) as handle:
            yield handle

    def checksum(self, algorithm: str = "sha256") -> str:
        """Hash the stored bytes, including compression when present."""

        digest = hashlib.new(algorithm)
        with open(self.path, "rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()
