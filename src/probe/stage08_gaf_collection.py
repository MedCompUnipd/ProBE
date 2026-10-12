"""Stream one normalized GAF to donor-only source and association-link tables.

A successful end-of-file and SHA256 check certify one release's donor coverage.
Outputs must be written to an unpublished staging directory; the caller owns
atomic bundle publication. No evidence policy, NOT veto, GO propagation or FASTA.
"""

from __future__ import annotations

import hashlib
import json
import os
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO

from probe.records import AnnotationRecord
from probe.stage08_annotations import ProjectedAssertion, SourceAssertion
from probe.stage08_collection_inputs import GafCollectionInput
from probe.stage08_donors import AssociationKey, DonorIndex

COLLECTION_SCHEMA = "stage08-gaf-collection-v1"
SOURCE_COLUMNS = (
    "assertion_id",
    "release",
    "normalized_gaf_line",
    "db",
    "db_object_id",
    "db_object_symbol",
    "qualifier",
    "go_id",
    "db_reference",
    "evidence_code",
    "with_or_from",
    "aspect",
    "db_object_name",
    "db_object_synonym",
    "db_object_type",
    "taxon",
    "date",
    "assigned_by",
    "annotation_extension",
    "gene_product_form_id",
)
LINK_COLUMNS = ("release", "target_id", "rid", "assertion_id")
COVERAGE_COLUMNS = (
    "release",
    "target_id",
    "rid",
    "candidate_accession",
    "annotation_donor",
    "positive_rows",
    "not_rows",
    "status",
)
INVENTORY_COLUMNS = (
    "release",
    "evidence_code",
    "aspect",
    "polarity",
    "source_rows",
    "association_links",
)


def _parts(value: str) -> tuple[str, ...]:
    return tuple(part for part in value.split("|") if part)


def _record(
    fields: tuple[str, ...], *, path: str, line_number: int
) -> AnnotationRecord:
    """Convert only matched rows using existing GafParser's field semantics.

    Keep this conversion in parity tests with probe.parsing.gaf.GafParser.
    Unlike its full-file reader, the streaming collector filters first.
    """
    if len(fields) != 17:
        raise ValueError(f"invalid GAF column count at line {line_number}")
    relation_parts = _parts(fields[3])
    relations = tuple(part for part in relation_parts if part != "NOT")
    if len(relations) > 1:
        raise ValueError(f"multiple GAF relations at line {line_number}")
    return AnnotationRecord(
        database=fields[0],
        subject_id=fields[1],
        symbol=fields[2],
        relation=relations[0] if relations else "",
        term_id=fields[4],
        negated="NOT" in relation_parts,
        references=_parts(fields[5]),
        evidence=fields[6],
        with_from=_parts(fields[7]),
        aspect=fields[8],
        name=fields[9],
        synonyms=_parts(fields[10]),
        object_type=fields[11],
        taxa=_parts(fields[12]),
        date=fields[13],
        assigned_by=fields[14],
        extensions=_parts(fields[15]),
        gene_product_form_id=fields[16],
        source=path,
        line=line_number,
    )


def _stamp(handle: BinaryIO) -> tuple[int, int, int, int, int]:
    status = os.fstat(handle.fileno())
    return (
        status.st_dev,
        status.st_ino,
        status.st_size,
        status.st_mtime_ns,
        status.st_ctime_ns,
    )


def _write_row(handle, values: tuple[str, ...]) -> None:
    """Write a plain TSV row, preserving quotes as literal GAF characters."""
    if any("\t" in value or "\n" in value or "\r" in value for value in values):
        raise ValueError("TSV field contains a forbidden separator")
    handle.write("\t".join(values) + "\n")


@dataclass(frozen=True, slots=True)
class GafScanResult:
    role: str
    release: str
    normalized_gaf_sha256: str
    physical_lines: int
    data_lines: int
    retrieved_source_rows: int
    annotation_links: int
    donor_yes_associations: int
    donor_yes_without_rows: int
    coverage_certified: bool

    def as_dict(self) -> dict[str, object]:
        return {
            "schema_version": COLLECTION_SCHEMA,
            **{name: getattr(self, name) for name in self.__dataclass_fields__},
            "inventory_scope": "retrieved_donor_assertions",
            "filtering_policy": "none",
        }


def collect_one_normalized_gaf(
    *,
    gaf: GafCollectionInput,
    donors: DonorIndex,
    staging_dir: Path,
) -> GafScanResult:
    """Write complete donor observations for one release in private staging.

    The caller must publish only after both releases succeed and validate.
    An exception leaves no coverage or completion report; partial staging files
    MUST NOT be interpreted as evidence of donor absence.
    """
    source = gaf.source
    if donors.role_for(source.release) != gaf.role:
        raise ValueError("GOA role/release does not match Stage 07 donor index")
    if gaf.size_bytes < 0:
        raise ValueError("invalid declared normalized GAF size")
    staging_dir = Path(staging_dir)
    if staging_dir.is_symlink() or not staging_dir.is_dir():
        raise ValueError("staging_dir must be an existing real directory")
    if any(staging_dir.iterdir()):
        raise ValueError("staging_dir must be empty")
    path = Path(source.path)
    if path.is_symlink() or not path.is_file():
        raise ValueError("normalized GAF must be a regular non-symlink file")

    role = gaf.role
    wanted = {
        accession.encode("utf-8")
        for accession in donors.donor_accessions(source.release)
    }
    donor_counts: Counter[AssociationKey] = Counter()
    negated_counts: Counter[AssociationKey] = Counter()
    inventory_rows: Counter[tuple[str, str, bool]] = Counter()
    inventory_links: Counter[tuple[str, str, bool]] = Counter()
    physical_lines = 0
    data_lines = 0
    source_rows = 0
    link_count = 0
    digest = hashlib.sha256()
    saw_version = False

    source_name = f"08_{role.lower()}_source_assertions.tsv"
    link_name = f"08_{role.lower()}_annotation_links.tsv"
    coverage_name = f"08_{role.lower()}_donor_coverage.tsv"
    inventory_name = f"08_{role.lower()}_evidence_inventory.tsv"
    summary_name = f"08_{role.lower()}_scan_summary.json"

    with (
        path.open("rb", buffering=8 * 1024 * 1024) as incoming,
        (staging_dir / source_name).open("w", encoding="utf-8", newline="") as src,
        (staging_dir / link_name).open("w", encoding="utf-8", newline="") as links,
    ):
        before = _stamp(incoming)
        if before[2] != gaf.size_bytes:
            raise ValueError("normalized GAF size differs from declared artifact")
        _write_row(src, SOURCE_COLUMNS)
        _write_row(links, LINK_COLUMNS)
        for raw_line in incoming:
            physical_lines += 1
            digest.update(raw_line)
            if not raw_line.strip(b"\r\n"):
                continue
            if raw_line.startswith(b"!"):
                if raw_line.lower().startswith(b"!gaf-version:"):
                    value = raw_line.split(b":", 1)[1].strip()
                    if not value.startswith(b"2"):
                        raise ValueError("normalized GAF must have version 2.x")
                    saw_version = True
                continue
            data_lines += 1
            first_tab = raw_line.find(b"\t")
            if first_tab < 0:
                raise ValueError(f"malformed GAF database column: {physical_lines}")
            if raw_line[:first_tab] != donors.source_database.encode("utf-8"):
                continue
            second_tab = raw_line.find(b"\t", first_tab + 1)
            if second_tab < 0:
                raise ValueError(f"malformed GAF accession column: {physical_lines}")
            accession = raw_line[first_tab + 1 : second_tab]
            if accession not in wanted:
                continue
            text = raw_line.rstrip(b"\r\n").decode("utf-8", errors="strict")
            fields = tuple(text.split("\t"))
            annotation = _record(fields, path=source.path, line_number=physical_lines)
            assertion = SourceAssertion(source, physical_lines, annotation, fields)
            matches = donors.donors_for(
                release=source.release,
                database=annotation.database,
                accession=annotation.subject_id,
            )
            if not matches:
                raise ValueError("prefilter matched an accession without donor")
            projected = ProjectedAssertion(assertion, matches)
            _write_row(
                src,
                (assertion.assertion_id, source.release, str(physical_lines), *fields),
            )
            source_rows += 1
            inventory_key = (annotation.evidence, annotation.aspect, annotation.negated)
            inventory_rows[inventory_key] += 1
            for donor, link in zip(matches, projected.links(), strict=True):
                _write_row(
                    links,
                    (link.release, link.target_id, link.rid, link.assertion_id),
                )
                counts = negated_counts if annotation.negated else donor_counts
                counts[donor.key] += 1
                inventory_links[inventory_key] += 1
                link_count += 1
        after = _stamp(incoming)
        if after != before:
            raise ValueError("normalized GAF changed during scan")
    if not saw_version:
        raise ValueError("normalized GAF missing !gaf-version header")
    if digest.hexdigest() != source.sha256:
        raise ValueError("normalized GAF SHA256 differs from upstream manifest")

    yes_count = 0
    yes_without = 0
    with (staging_dir / coverage_name).open(
        "w", encoding="utf-8", newline=""
    ) as coverage:
        _write_row(coverage, COVERAGE_COLUMNS)
        for donor in donors.associations:
            if donor.release != source.release:
                continue
            positive = donor_counts[donor.key]
            negative = negated_counts[donor.key]
            if donor.may_donate:
                yes_count += 1
                status = (
                    "ROWS_RETRIEVED" if positive + negative else "NO_RETAINED_GOA_ROWS"
                )
                if positive + negative == 0:
                    yes_without += 1
            else:
                status = "DONOR_DISABLED"
                if positive or negative:
                    raise ValueError("disabled donor received annotation rows")
            _write_row(
                coverage,
                (
                    donor.release,
                    donor.target_id,
                    donor.rid,
                    donor.candidate_accession,
                    donor.annotation_donor,
                    str(positive),
                    str(negative),
                    status,
                ),
            )
    with (staging_dir / inventory_name).open(
        "w", encoding="utf-8", newline=""
    ) as inventory:
        _write_row(inventory, INVENTORY_COLUMNS)
        for key in sorted(inventory_rows):
            evidence, aspect, negated = key
            _write_row(
                inventory,
                (
                    source.release,
                    evidence,
                    aspect,
                    "NOT" if negated else "POSITIVE",
                    str(inventory_rows[key]),
                    str(inventory_links[key]),
                ),
            )
    result = GafScanResult(
        role,
        source.release,
        digest.hexdigest(),
        physical_lines,
        data_lines,
        source_rows,
        link_count,
        yes_count,
        yes_without,
        True,
    )
    (staging_dir / summary_name).write_text(
        json.dumps(result.as_dict(), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return result
