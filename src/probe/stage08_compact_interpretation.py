"""M10c2/c3: bounded, disk-backed GO interpretation of certified M8 TSVs.

Direct donor links are indexed once in SQLite. Propagation is performed on
unique direct target/GO/polarity/evidence groups, not on every donor link.
A separate term-code table counts propagated supports; a compact per-term TSV
is published only after both M8 source/link streams pass their SHA256 checks.
No evidence policy or EVAL/SKIP classification is applied here.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import sqlite3
import tempfile
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from probe.stage08_annotations import NormalizedGafSource
from probe.stage08_donors import DonorIndex
from probe.stage08_go_closure import GoClosureIndex
from probe.stage08_m8_adapter import iter_m8_projected_assertions

SCHEMA_VERSION = "stage08-m10c-compact-interpretation-v1"
TERM_COLUMNS = (
    "release",
    "target_id",
    "aspect",
    "go_id",
    "positive_supports",
    "not_supports",
    "positive_evidence_json",
    "not_evidence_json",
    "direct_positive_supports",
    "direct_not_supports",
    "conflict",
)


@dataclass(frozen=True, slots=True)
class RoleTables:
    role: str
    source_tsv: Path
    links_tsv: Path
    gaf_source: NormalizedGafSource
    expected_source_rows: int
    expected_links: int
    source_tsv_sha256: str
    links_tsv_sha256: str

    def __post_init__(self) -> None:
        if self.role not in {"START", "END"}:
            raise ValueError("Role must be START or END")
        if self.expected_links < self.expected_source_rows:
            raise ValueError("M8 must have at least one link per source row")
        if self.expected_source_rows < 0:
            raise ValueError("Source count must be nonnegative")
        for digest in (self.source_tsv_sha256, self.links_tsv_sha256):
            if not re.fullmatch(r"[a-f0-9]{64}", digest):
                raise ValueError("Invalid expected M8 payload SHA256")


def _database(db_file: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(db_file)
    # This database is built in a private, unpublished staging directory.
    # An interrupted build is discarded; no published database is modified.
    # Keep rollback journals in RAM and avoid per-commit filesystem syncs.
    # Final integrity checks, file fsyncs, and atomic rename are retained.
    mode = conn.execute("PRAGMA journal_mode=MEMORY").fetchone()[0]
    if mode != "memory":
        conn.close()
        raise RuntimeError("M10c staging requires an in-memory SQLite journal")
    conn.execute("PRAGMA synchronous=OFF")
    conn.execute("PRAGMA temp_store=FILE")
    conn.execute("PRAGMA cache_size=-131072")
    conn.executescript("""
        CREATE TABLE direct_support (
            release TEXT NOT NULL,
            target_id TEXT NOT NULL,
            aspect TEXT NOT NULL,
            source_go TEXT NOT NULL,
            polarity TEXT NOT NULL,
            evidence_code TEXT NOT NULL,
            rid TEXT NOT NULL,
            assertion_id TEXT NOT NULL,
            normalized_gaf_line INTEGER NOT NULL,
            donor_accession TEXT NOT NULL,
            gaf_relation TEXT NOT NULL,
            gaf_qualifier TEXT NOT NULL,
            UNIQUE (release, target_id, rid, assertion_id)
        );
        CREATE TABLE term_code_support (
            release TEXT NOT NULL,
            target_id TEXT NOT NULL,
            aspect TEXT NOT NULL,
            go_id TEXT NOT NULL,
            polarity TEXT NOT NULL,
            evidence_code TEXT NOT NULL,
            support_count INTEGER NOT NULL,
            direct_count INTEGER NOT NULL,
            PRIMARY KEY (
                release, target_id, aspect, go_id, polarity, evidence_code
            )
        ) WITHOUT ROWID;
    """)
    return conn


def _insert_direct(
    conn: sqlite3.Connection,
    role: RoleTables,
    donors: DonorIndex,
    graph: GoClosureIndex,
) -> tuple[int, int]:
    records = 0
    linked = 0
    batch: list[tuple[str | int, ...]] = []
    statement = """INSERT INTO direct_support VALUES
        (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"""
    incoming = iter_m8_projected_assertions(
        source_tsv=role.source_tsv,
        links_tsv=role.links_tsv,
        gaf_source=role.gaf_source,
        donors=donors,
        expected_source_rows=role.expected_source_rows,
        expected_links=role.expected_links,
        source_tsv_sha256=role.source_tsv_sha256,
        links_tsv_sha256=role.links_tsv_sha256,
    )
    try:
        for item in incoming:
            records += 1
            annotation = item.assertion.annotation
            try:
                aspect = graph.aspect(annotation.term_id)
            except KeyError as error:
                raise ValueError(
                    f"M8 GO term is absent from the pinned graph: {annotation.term_id}"
                ) from error
            if aspect not in {"F", "P", "C"}:
                aspect = {
                    "molecular_function": "F",
                    "biological_process": "P",
                    "cellular_component": "C",
                }.get(aspect, "")
            if aspect != annotation.aspect:
                raise ValueError("M8 aspect disagrees with pinned GO graph")
            polarity = "NOT" if annotation.negated else "POSITIVE"
            for donor in item.donors:
                batch.append(
                    (
                        role.gaf_source.release,
                        donor.target_id,
                        aspect,
                        annotation.term_id,
                        polarity,
                        annotation.evidence,
                        donor.rid,
                        item.assertion.assertion_id,
                        item.assertion.line_number,
                        donor.candidate_accession,
                        annotation.relation,
                        item.assertion.raw_fields[3],
                    )
                )
                linked += 1
            if len(batch) >= 8192:
                conn.executemany(statement, batch)
                batch.clear()
                conn.commit()
        if batch:
            conn.executemany(statement, batch)
            conn.commit()
    finally:
        incoming.close()
    if records != role.expected_source_rows or linked != role.expected_links:
        raise ValueError("M8 adapter did not exhaust expected source/link counts")
    return records, linked


def _propagate_unique_groups(
    conn: sqlite3.Connection,
    graph: GoClosureIndex,
) -> tuple[int, int]:
    """Count provenance supports without storing each inherited donor row."""
    conn.execute("""
        CREATE INDEX direct_by_source ON direct_support
        (release, target_id, aspect, source_go, polarity, evidence_code)
    """)
    conn.commit()
    grouped = conn.execute("""
        SELECT release, target_id, aspect, source_go, polarity,
               evidence_code, COUNT(*)
        FROM direct_support
        GROUP BY release, target_id, aspect, source_go, polarity,
                 evidence_code
        ORDER BY release, target_id, aspect, source_go, polarity,
                 evidence_code
    """)
    upsert = """INSERT INTO term_code_support VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(release, target_id, aspect, go_id, polarity, evidence_code)
        DO UPDATE SET
            support_count = support_count + excluded.support_count,
            direct_count = direct_count + excluded.direct_count"""
    batch: list[tuple[str | int, ...]] = []
    unique_groups = 0
    expanded_group_terms = 0
    for release, target, aspect, source_go, polarity, code, count in grouped:
        unique_groups += 1
        closure = (
            graph.ancestors(source_go)
            if polarity == "POSITIVE"
            else graph.descendants(source_go)
        )
        for term in closure:
            batch.append(
                (
                    release,
                    target,
                    aspect,
                    term,
                    polarity,
                    code,
                    count,
                    count if term == source_go else 0,
                )
            )
            expanded_group_terms += 1
        if len(batch) >= 8192:
            conn.executemany(upsert, batch)
            batch.clear()
            conn.commit()
    if batch:
        conn.executemany(upsert, batch)
        conn.commit()
    return unique_groups, expanded_group_terms


def _write_term_summary(
    conn: sqlite3.Connection,
    destination: Path,
) -> tuple[int, int]:
    """Deterministic term reducer, one active target/GO held in memory."""
    query = conn.execute("""
        SELECT release, target_id, aspect, go_id, polarity,
               evidence_code, support_count, direct_count
        FROM term_code_support
        ORDER BY release, target_id, aspect, go_id, polarity, evidence_code
    """)
    count = 0
    conflicts = 0
    previous: tuple[str, str, str, str] | None = None
    positive: dict[str, int] = {}
    negative: dict[str, int] = {}
    direct_pos = 0
    direct_not = 0

    with destination.open("w", encoding="utf-8", newline="") as handle:
        handle.write("\t".join(TERM_COLUMNS) + "\n")

        def flush() -> None:
            nonlocal count, conflicts
            if previous is None:
                return
            conflict = bool(positive and negative)
            values = (
                *previous,
                str(sum(positive.values())),
                str(sum(negative.values())),
                json.dumps(sorted(positive), separators=(",", ":")),
                json.dumps(sorted(negative), separators=(",", ":")),
                str(direct_pos),
                str(direct_not),
                str(int(conflict)),
            )
            handle.write("\t".join(values) + "\n")
            count += 1
            conflicts += int(conflict)

        for release, target, aspect, go, polarity, code, supports, direct in query:
            key = release, target, aspect, go
            if previous != key:
                flush()
                previous = key
                positive = {}
                negative = {}
                direct_pos = 0
                direct_not = 0
            if polarity == "POSITIVE":
                positive[code] = supports
                direct_pos += direct
            elif polarity == "NOT":
                negative[code] = supports
                direct_not += direct
            else:
                raise ValueError("Unexpected polarity in term support index")
        flush()
    return count, conflicts


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(8 * 1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def build_compact_interpretation(
    *,
    roles: Sequence[RoleTables],
    donors: DonorIndex,
    graph: GoClosureIndex,
    ontology_sha256: str,
    destination: Path,
    final_check: Callable[[], None] | None = None,
) -> Mapping[str, object]:
    """Atomically publish lossless indexed provenance + deduplicated GO terms.

    Caller must independently certify M8 manifest and Stage 07 donor bundle,
    and verify that graph derives from pinned ontology_sha256. Published input
    derived TSV bytes are checked at actual EOF. Any failure leaves destination
    absent; staging is removed. There is no original GAF scan.
    """
    if len(roles) != 2 or {r.role for r in roles} != {"START", "END"}:
        raise ValueError("Both independent START and END roles are required")
    if roles[0].gaf_source.release == roles[1].gaf_source.release:
        raise ValueError("START and END must have distinct releases")
    if not re.fullmatch(r"[a-f0-9]{64}", ontology_sha256):
        raise ValueError("Invalid pinned ontology fingerprint")
    destination = Path(destination)
    if destination.exists() or destination.is_symlink():
        raise FileExistsError("Refusing to replace completed interpretation")
    if destination.parent.is_symlink() or not destination.parent.is_dir():
        raise ValueError("Interpretation parent must be a real directory")
    staging = Path(tempfile.mkdtemp(prefix=".m10c_staging_", dir=destination.parent))
    try:
        db_path = staging / "direct_provenance.sqlite3"
        conn = _database(db_path)
        try:
            counts: dict[str, dict[str, int]] = {}
            for role in sorted(roles, key=lambda role: role.role):
                if donors.role_for(role.gaf_source.release) != role.role:
                    raise ValueError("M8 role does not match Stage 07 donors")
                sources, links = _insert_direct(conn, role, donors, graph)
                counts[role.role] = {
                    "retrieved_source_rows": sources,
                    "annotation_links": links,
                }
            if conn.execute("SELECT COUNT(*) FROM direct_support").fetchone()[0] != sum(
                role.expected_links for role in roles
            ):
                raise ValueError("Direct provenance cardinality mismatch")
            grouped, expanded = _propagate_unique_groups(conn, graph)
            terms, conflicts = _write_term_summary(
                conn, staging / "propagated_terms.tsv"
            )
            if conn.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                raise ValueError("SQLite integrity check failed")
            conn.execute("PRAGMA optimize")
        finally:
            conn.close()
        report: dict[str, object] = {
            "schema_version": SCHEMA_VERSION,
            "ontology_sha256": ontology_sha256,
            "graph_terms": graph.node_count,
            "graph_edges": graph.edge_count,
            "by_role": counts,
            "unique_direct_groups": grouped,
            "expanded_group_terms": expanded,
            "distinct_propagated_terms": terms,
            "positive_not_conflicts": conflicts,
            "evidence_policy": "unfiltered_literal_source_codes",
            "not_policy": "preserved_no_veto",
            "evaluation_policy": "not_computed",
            "provenance_retrieval": "indexed_direct_support_plus_go_closure",
            "source_payloads": {
                role.role: {
                    "source_sha256": role.source_tsv_sha256,
                    "links_sha256": role.links_tsv_sha256,
                    "gaf_source_sha256": role.gaf_source.sha256,
                }
                for role in roles
            },
        }
        report["outputs"] = {
            name: {
                "bytes": (staging / name).stat().st_size,
                "sha256": _sha256(staging / name),
            }
            for name in ("direct_provenance.sqlite3", "propagated_terms.tsv")
        }
        (staging / "manifest.json").write_text(
            json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        if final_check is not None:
            final_check()
        for path in staging.iterdir():
            with path.open("rb") as handle:
                os.fsync(handle.fileno())
        if destination.exists() or destination.is_symlink():
            raise FileExistsError("Another interpretation was published")
        os.rename(staging, destination)
        return report
    except BaseException:
        shutil.rmtree(staging)
        raise


def iter_term_provenance(
    *,
    database: Path,
    graph: GoClosureIndex,
    release: str,
    target_id: str,
    aspect: str,
    go_id: str,
    polarity: str,
) -> Iterator[tuple[object, ...]]:
    """Recover direct donor/line/code support for a propagated GO on demand.

    DB data are pointers to original M8 assertion IDs, not expanded donor rows.
    Consumers can join these IDs back to the immutable 17-field M8 source TSV.
    """
    if polarity not in {"POSITIVE", "NOT"}:
        raise ValueError("Invalid polarity")
    if graph.aspect(go_id) not in (
        aspect,
        {
            "F": "molecular_function",
            "P": "biological_process",
            "C": "cellular_component",
        }.get(aspect),
    ):
        raise ValueError("GO aspect mismatch")
    sources = (
        graph.descendants(go_id) if polarity == "POSITIVE" else graph.ancestors(go_id)
    )
    if not Path(database).is_file() or Path(database).is_symlink():
        raise ValueError("Provenance database must be a real file")
    with sqlite3.connect(f"file:{Path(database)}?mode=ro", uri=True) as conn:
        for position in range(0, len(sources), 400):
            block = sources[position : position + 400]
            placeholders = ",".join("?" for _ in block)
            stmt = (
                "SELECT rid, assertion_id, source_go, evidence_code, "
                "normalized_gaf_line, donor_accession, gaf_relation, "
                "gaf_qualifier FROM direct_support WHERE release=? "
                "AND target_id=? AND aspect=? AND polarity=? "
                f"AND source_go IN ({placeholders}) "
                "ORDER BY rid, assertion_id"
            )
            yield from conn.execute(
                stmt, (release, target_id, aspect, polarity, *block)
            )
