"""Bounded streaming reconstruction of Stage 08 M8 donor-linked assertions.

M10c adapter foundation. Reads only the two published *derived* TSV tables;
never opens the original GAF. Its caller supplies SHA256 and expected row counts
from an independently validated Stage 08 manifest/summary. Complete-stream
verification is performed only when the iterator is exhausted successfully.

No GO selection, biological interpretation, aggregation, disk output or scoring.
"""

from __future__ import annotations

import hashlib
import os
import re
from collections.abc import Iterator
from pathlib import Path

from probe.stage08_annotations import (
    NormalizedGafSource,
    ProjectedAssertion,
    SourceAssertion,
)
from probe.stage08_donors import DonorIndex
from probe.stage08_gaf_collection import (
    LINK_COLUMNS,
    SOURCE_COLUMNS,
    _record,
)


def _stamp(handle: object) -> tuple[int, int, int, int, int]:
    stat = os.fstat(handle.fileno())
    return (
        stat.st_dev,
        stat.st_ino,
        stat.st_size,
        stat.st_mtime_ns,
        stat.st_ctime_ns,
    )


def _validated_rows(
    path: Path,
    *,
    columns: tuple[str, ...],
    expected_sha256: str,
) -> Iterator[tuple[str, ...]]:
    """Yield literal TSV rows, checking the complete bytes at actual EOF."""
    if not re.fullmatch(r"[0-9a-f]{64}", expected_sha256):
        raise ValueError("Expected TSV SHA256 must be lowercase hex")
    if path.is_symlink() or not path.is_file():
        raise ValueError("Stage 08 TSV must be a regular non-symlink file")
    digest = hashlib.sha256()
    with path.open("rb", buffering=8 * 1024 * 1024) as stream:
        before = _stamp(stream)
        number = 0
        for number, raw in enumerate(stream, start=1):
            digest.update(raw)
            if not raw.endswith(b"\n") or b"\r" in raw:
                raise ValueError(f"{path}: line {number}: invalid line ending")
            values = tuple(raw[:-1].decode("utf-8", "strict").split("\t"))
            if number == 1:
                if values != columns:
                    raise ValueError(f"{path}: unexpected Stage 08 TSV header")
                continue
            if len(values) != len(columns):
                raise ValueError(f"{path}: line {number}: invalid column count")
            yield values
        if number == 0:
            raise ValueError(f"{path}: empty Stage 08 TSV")
        if _stamp(stream) != before:
            raise ValueError(f"{path}: changed during Stage 08 TSV read")
    if digest.hexdigest() != expected_sha256:
        raise ValueError(f"{path}: SHA256 disagrees with published manifest")


def _positive_decimal(value: str, *, name: str) -> int:
    if not value.isascii() or not value.isdecimal() or int(value) <= 0:
        raise ValueError(f"{name}: expected positive ASCII integer")
    return int(value)


def iter_m8_projected_assertions(
    *,
    source_tsv: Path,
    links_tsv: Path,
    gaf_source: NormalizedGafSource,
    donors: DonorIndex,
    expected_source_rows: int,
    expected_links: int,
    source_tsv_sha256: str,
    links_tsv_sha256: str,
) -> Iterator[ProjectedAssertion]:
    """Join M8 source/link tables in collector order with bounded memory.

    Contract: the M8 collector writes sources in physical GAF line order,
    immediately followed by their one-or-more links in matching source order.
    No external sort or whole-file index is necessary for this join. The
    original 17 GAF fields and M1 assertion IDs are revalidated per row.

    `donors` must come from the same validated Stage 07 input as M8. Callers
    must independently validate the published M8 manifest and pass its output
    hashes and summary counts. Stopping early does NOT certify completeness.
    """
    if expected_source_rows < 0 or expected_links < 0:
        raise ValueError("Expected Stage 08 counts cannot be negative")
    donors.role_for(gaf_source.release)
    if Path(source_tsv).resolve() == Path(links_tsv).resolve():
        raise ValueError("Source and link TSV files must be distinct")

    sources = _validated_rows(
        Path(source_tsv),
        columns=SOURCE_COLUMNS,
        expected_sha256=source_tsv_sha256,
    )
    links = _validated_rows(
        Path(links_tsv),
        columns=LINK_COLUMNS,
        expected_sha256=links_tsv_sha256,
    )
    seen_source_count = 0
    seen_link_count = 0
    last_line = 0
    try:
        next_link = next(links, None)
        for values in sources:
            seen_source_count += 1
            asserted_id, release, physical_line, *gaf_fields = values
            if release != gaf_source.release:
                raise ValueError("M8 source release differs from pinned release")
            line_number = _positive_decimal(
                physical_line, name="normalized GAF physical line"
            )
            if line_number <= last_line:
                raise ValueError("M8 source rows not in physical GAF order")
            last_line = line_number
            fields = tuple(gaf_fields)
            annotation = _record(fields, path=gaf_source.path, line_number=line_number)
            assertion = SourceAssertion(gaf_source, line_number, annotation, fields)
            if assertion.assertion_id != asserted_id:
                raise ValueError("M8 source assertion ID disagrees with provenance")
            if next_link is None or next_link[3] != asserted_id:
                raise ValueError("M8 source has no immediately matching donor link")

            group = []
            donor_keys = set()
            while next_link is not None and next_link[3] == asserted_id:
                link_release, target_id, rid, _ = next_link
                if link_release != release:
                    raise ValueError("M8 link release differs from source release")
                _positive_decimal(rid, name="Stage 07 donor RID")
                key = (release, target_id, rid)
                if key in donor_keys:
                    raise ValueError("M8 contains repeated donor link")
                donor_keys.add(key)
                donor = donors.by_key.get(key)
                if (
                    donor is None
                    or not donor.may_donate
                    or donor.candidate_accession != annotation.subject_id
                    or annotation.database != donors.source_database
                ):
                    raise ValueError("M8 link does not match authorized donor")
                group.append(donor)
                seen_link_count += 1
                next_link = next(links, None)
            yield ProjectedAssertion(assertion, tuple(group))
        if next_link is not None:
            raise ValueError("Orphan M8 donor link after final source row")
        if seen_source_count != expected_source_rows:
            raise ValueError("M8 source-row count differs from published summary")
        if seen_link_count != expected_links:
            raise ValueError("M8 link count differs from published summary")
    finally:
        sources.close()
        links.close()
