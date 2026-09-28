"""Local, immutable-index support for pinned NCBI Taxonomy snapshots."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Self

from probe.source import Source

_SCHEMA_VERSION = 1
_BATCH_SIZE = 10_000


@dataclass(frozen=True, slots=True)
class TaxonomyNode:
    """One raw NCBI taxonomy node without derived lineage interpretation."""

    taxid: str
    parent_taxid: str
    rank: str
    scientific_name: str | None


@dataclass(frozen=True, slots=True)
class TaxonomySourceProvenance:
    """Stored-byte provenance for one taxdump member used by the index."""

    member: str
    path: str
    sha256: str


@dataclass(frozen=True, slots=True)
class TaxonomySnapshotProvenance:
    """Identity and local source paths for one pinned taxonomy snapshot."""

    snapshot_id: str | None
    sources: tuple[TaxonomySourceProvenance, ...]


def _fields(line: str) -> tuple[str, ...]:
    return tuple(value.strip() for value in line.rstrip("\r\n").split("|"))[:-1]


def _iter_fields(source: Source, member: str) -> Iterator[tuple[int, tuple[str, ...]]]:
    with source.open_text() as handle:
        for line_number, line in enumerate(handle, start=1):
            fields = _fields(line)
            if not fields or not fields[0]:
                raise ValueError(f"invalid {member} row at {source.name}:{line_number}")
            yield line_number, fields


class NcbiTaxonomyIndex:
    """SQLite index for local NCBI ``new_taxdump`` members.

    This class stores raw taxonomy facts only. It does not resolve merged TaxIDs,
    calculate species anchors, or make annotation-aggregation decisions.
    """

    schema_version = _SCHEMA_VERSION

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self._connection = sqlite3.connect(self.path)
        self._connection.row_factory = sqlite3.Row
        self._validate_schema()

    @classmethod
    def build(
        cls,
        path: str | Path,
        *,
        nodes: Source | str | Path,
        names: Source | str | Path,
        merged: Source | str | Path,
        delnodes: Source | str | Path,
        snapshot_id: str | None = None,
    ) -> Self:
        """Transactionally rebuild an index from local pinned taxdump members."""

        sources = {
            "delnodes.dmp": Source.from_value(delnodes),
            "merged.dmp": Source.from_value(merged),
            "names.dmp": Source.from_value(names),
            "nodes.dmp": Source.from_value(nodes),
        }
        connection = sqlite3.connect(Path(path))
        try:
            connection.execute("PRAGMA foreign_keys = ON")
            connection.execute("BEGIN IMMEDIATE")
            cls._create_schema(connection)
            cls._insert_nodes(connection, sources["nodes.dmp"])
            cls._set_scientific_names(connection, sources["names.dmp"])
            cls._insert_merged(connection, sources["merged.dmp"])
            cls._insert_deleted(connection, sources["delnodes.dmp"])
            connection.execute(
                "INSERT INTO taxonomy_metadata (key, value) VALUES (?, ?)",
                ("schema_version", str(_SCHEMA_VERSION)),
            )
            if snapshot_id is not None:
                connection.execute(
                    "INSERT INTO taxonomy_metadata (key, value) VALUES (?, ?)",
                    ("snapshot_id", snapshot_id),
                )
            connection.executemany(
                """
                INSERT INTO taxonomy_sources (member, path, sha256)
                VALUES (?, ?, ?)
                """,
                [
                    (member, source.name, source.checksum())
                    for member, source in sorted(sources.items())
                ],
            )
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()
        return cls(path)

    @staticmethod
    def _create_schema(connection: sqlite3.Connection) -> None:
        statements = (
            "DROP TABLE IF EXISTS taxonomy_sources",
            "DROP TABLE IF EXISTS taxonomy_metadata",
            "DROP TABLE IF EXISTS taxonomy_deleted",
            "DROP TABLE IF EXISTS taxonomy_merged",
            "DROP TABLE IF EXISTS taxonomy_scientific_names",
            "DROP TABLE IF EXISTS taxonomy_nodes",
            """
            CREATE TABLE taxonomy_nodes (
                taxid TEXT PRIMARY KEY,
                parent_taxid TEXT NOT NULL,
                rank TEXT NOT NULL,
                scientific_name TEXT
            )
            """,
            """
            CREATE TABLE taxonomy_merged (
                old_taxid TEXT PRIMARY KEY,
                new_taxid TEXT NOT NULL
            )
            """,
            """
            CREATE TABLE taxonomy_scientific_names (
                taxid TEXT PRIMARY KEY REFERENCES taxonomy_nodes(taxid),
                name TEXT NOT NULL
            )
            """,
            "CREATE TABLE taxonomy_deleted (taxid TEXT PRIMARY KEY)",
            """
            CREATE TABLE taxonomy_metadata (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )
            """,
            """
            CREATE TABLE taxonomy_sources (
                member TEXT PRIMARY KEY,
                path TEXT NOT NULL,
                sha256 TEXT NOT NULL
            )
            """,
            "CREATE INDEX taxonomy_nodes_parent_idx ON taxonomy_nodes (parent_taxid)",
            "CREATE INDEX taxonomy_merged_new_idx ON taxonomy_merged (new_taxid)",
        )
        for statement in statements:
            connection.execute(statement)

    @staticmethod
    def _insert_nodes(connection: sqlite3.Connection, source: Source) -> None:
        batch: list[tuple[str, str, str]] = []
        for line_number, fields in _iter_fields(source, "nodes.dmp"):
            if len(fields) < 3:
                raise ValueError(
                    f"invalid nodes.dmp row at {source.name}:{line_number}"
                )
            batch.append((fields[0], fields[1], fields[2]))
            if len(batch) == _BATCH_SIZE:
                connection.executemany(
                    """
                    INSERT INTO taxonomy_nodes (taxid, parent_taxid, rank)
                    VALUES (?, ?, ?)
                    """,
                    batch,
                )
                batch.clear()
        if batch:
            connection.executemany(
                """
                INSERT INTO taxonomy_nodes (taxid, parent_taxid, rank)
                VALUES (?, ?, ?)
                """,
                batch,
            )

    @staticmethod
    def _set_scientific_names(connection: sqlite3.Connection, source: Source) -> None:
        batch: list[tuple[str, str]] = []
        for line_number, fields in _iter_fields(source, "names.dmp"):
            if len(fields) < 4:
                raise ValueError(
                    f"invalid names.dmp row at {source.name}:{line_number}"
                )
            if fields[3] != "scientific name":
                continue
            batch.append((fields[0], fields[1]))
            if len(batch) == _BATCH_SIZE:
                NcbiTaxonomyIndex._insert_scientific_names(connection, batch, source)
                batch.clear()
        if batch:
            NcbiTaxonomyIndex._insert_scientific_names(connection, batch, source)
        connection.execute(
            """
            UPDATE taxonomy_nodes
            SET scientific_name = (
                SELECT name FROM taxonomy_scientific_names
                WHERE taxonomy_scientific_names.taxid = taxonomy_nodes.taxid
            )
            WHERE taxid IN (SELECT taxid FROM taxonomy_scientific_names)
            """
        )

    @staticmethod
    def _insert_scientific_names(
        connection: sqlite3.Connection,
        names: list[tuple[str, str]],
        source: Source,
    ) -> None:
        try:
            connection.executemany(
                "INSERT INTO taxonomy_scientific_names (taxid, name) VALUES (?, ?)",
                names,
            )
        except sqlite3.IntegrityError as error:
            raise ValueError(
                f"duplicate or unknown scientific name record in {source.name}"
            ) from error

    @staticmethod
    def _insert_merged(connection: sqlite3.Connection, source: Source) -> None:
        batch: list[tuple[str, str]] = []
        for line_number, fields in _iter_fields(source, "merged.dmp"):
            if len(fields) < 2:
                raise ValueError(
                    f"invalid merged.dmp row at {source.name}:{line_number}"
                )
            batch.append((fields[0], fields[1]))
            if len(batch) == _BATCH_SIZE:
                connection.executemany(
                    "INSERT INTO taxonomy_merged (old_taxid, new_taxid) VALUES (?, ?)",
                    batch,
                )
                batch.clear()
        if batch:
            connection.executemany(
                "INSERT INTO taxonomy_merged (old_taxid, new_taxid) VALUES (?, ?)",
                batch,
            )

    @staticmethod
    def _insert_deleted(connection: sqlite3.Connection, source: Source) -> None:
        batch: list[tuple[str]] = []
        for line_number, fields in _iter_fields(source, "delnodes.dmp"):
            if len(fields) < 1:
                raise ValueError(
                    f"invalid delnodes.dmp row at {source.name}:{line_number}"
                )
            batch.append((fields[0],))
            if len(batch) == _BATCH_SIZE:
                connection.executemany(
                    "INSERT INTO taxonomy_deleted (taxid) VALUES (?)",
                    batch,
                )
                batch.clear()
        if batch:
            connection.executemany(
                "INSERT INTO taxonomy_deleted (taxid) VALUES (?)",
                batch,
            )

    def _validate_schema(self) -> None:
        try:
            row = self._connection.execute(
                "SELECT value FROM taxonomy_metadata WHERE key = ?",
                ("schema_version",),
            ).fetchone()
        except sqlite3.DatabaseError as error:
            self.close()
            raise ValueError(f"not a ProBE NCBI taxonomy index: {self.path}") from error
        if row is None or row["value"] != str(self.schema_version):
            self.close()
            raise ValueError(f"unsupported NCBI taxonomy index: {self.path}")

    def node(self, taxid: str) -> TaxonomyNode | None:
        row = self._connection.execute(
            """
            SELECT taxid, parent_taxid, rank, scientific_name
            FROM taxonomy_nodes WHERE taxid = ?
            """,
            (taxid,),
        ).fetchone()
        if row is None:
            return None
        return TaxonomyNode(
            taxid=row["taxid"],
            parent_taxid=row["parent_taxid"],
            rank=row["rank"],
            scientific_name=row["scientific_name"],
        )

    def merged_taxid(self, taxid: str) -> str | None:
        row = self._connection.execute(
            "SELECT new_taxid FROM taxonomy_merged WHERE old_taxid = ?",
            (taxid,),
        ).fetchone()
        return None if row is None else row["new_taxid"]

    def is_deleted(self, taxid: str) -> bool:
        return (
            self._connection.execute(
                "SELECT 1 FROM taxonomy_deleted WHERE taxid = ?", (taxid,)
            ).fetchone()
            is not None
        )

    @property
    def provenance(self) -> TaxonomySnapshotProvenance:
        snapshot_row = self._connection.execute(
            "SELECT value FROM taxonomy_metadata WHERE key = ?", ("snapshot_id",)
        ).fetchone()
        sources = tuple(
            TaxonomySourceProvenance(
                member=row["member"], path=row["path"], sha256=row["sha256"]
            )
            for row in self._connection.execute(
                "SELECT member, path, sha256 FROM taxonomy_sources ORDER BY member"
            )
        )
        return TaxonomySnapshotProvenance(
            snapshot_id=None if snapshot_row is None else snapshot_row["value"],
            sources=sources,
        )

    def close(self) -> None:
        self._connection.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()


__all__ = [
    "NcbiTaxonomyIndex",
    "TaxonomyNode",
    "TaxonomySnapshotProvenance",
    "TaxonomySourceProvenance",
]
