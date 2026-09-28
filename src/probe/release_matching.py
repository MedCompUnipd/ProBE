"""Single-pass exact matching of prepared targets against a UniProt release."""

from __future__ import annotations

import csv
import json
import os
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from probe.identity import sequence_digest
from probe.parsing.uniprot import UniProtDatParser
from probe.records import UniProtRecord, UniProtSection
from probe.source import Source
from probe.target_preparation import PreparedTarget
from probe.taxonomy import (
    NcbiTaxonomyIndex,
    SpeciesAnchor,
    SpeciesAnchorStatus,
    TaxonomicRelationship,
    TaxonomyResolution,
    TaxonomyResolutionStatus,
)

START_MATCHES_FILENAME = "02_start_matches.tsv"
START_ISSUES_FILENAME = "02_start_issues.tsv"
SUMMARY_FILENAME = "02_summary.json"
_SECTION_ORDER = (UniProtSection.SWISS_PROT, UniProtSection.TREMBL)


@dataclass(frozen=True, slots=True)
class TargetTaxonomicContext:
    target: PreparedTarget
    resolution: TaxonomyResolution
    species_anchor: SpeciesAnchor | None


@dataclass(frozen=True, slots=True)
class ReleaseMatchResult:
    matches_path: Path
    issues_path: Path
    summary_path: Path
    admitted_matches: int


@dataclass(frozen=True, slots=True)
class ReleaseMatchConfiguration:
    """Release-specific output and audit labels for the shared matcher."""

    role: str
    matches_filename: str
    issues_filename: str
    summary_filename: str

    @property
    def no_strict_match_status(self) -> str:
        return f"NO_STRICT_{self.role.upper()}_MATCH"


START_RELEASE = ReleaseMatchConfiguration(
    role="START",
    matches_filename=START_MATCHES_FILENAME,
    issues_filename=START_ISSUES_FILENAME,
    summary_filename=SUMMARY_FILENAME,
)


class FragmentMatcher(Protocol):
    """Future extension point invoked during the existing UniProt stream pass."""

    def examine(self, record: UniProtRecord) -> None:
        """Inspect a fragment candidate without admitting a match."""


class DeferredFragmentMatcher:
    """Current fragment policy: retain no fragment-derived match decisions.

    TODO: In this same streaming pass, resolve fragment TaxID first; restrict
    candidates to the target bucket with the identical resolved TaxID; use a
    target-side seed/k-mer candidate index; require exact contiguous containment
    and strong locus/record compatibility; reject ambiguous mappings; and retain
    fragment-derived matches separately from exact full-length matches.
    """

    def examine(self, record: UniProtRecord) -> None:
        del record


def _load_prepared_targets(source: Source) -> tuple[PreparedTarget, ...]:
    required = {
        "target_id",
        "raw_taxid",
        "optional_accession",
        "normalized_sequence",
        "sequence_length",
        "sequence_sha256",
        "normalization_policy_version",
    }
    targets: list[PreparedTarget] = []
    seen: set[str] = set()
    with source.open_text() as handle:
        reader = csv.DictReader(handle, delimiter="\t")
        if reader.fieldnames is None or not required.issubset(reader.fieldnames):
            raise ValueError("prepared targets TSV has an unsupported schema")
        for line_number, row in enumerate(reader, start=2):
            try:
                target = PreparedTarget(
                    target_id=row["target_id"],
                    raw_taxid=row["raw_taxid"],
                    optional_accession=row["optional_accession"],
                    normalized_sequence=row["normalized_sequence"],
                    sequence_length=int(row["sequence_length"]),
                    sequence_sha256=row["sequence_sha256"],
                    normalization_policy_version=row["normalization_policy_version"],
                )
            except (KeyError, TypeError, ValueError) as error:
                raise ValueError(
                    f"invalid prepared target at {source.name}:{line_number}"
                ) from error
            if not target.target_id or target.target_id in seen:
                raise ValueError(
                    f"duplicate or empty target_id at {source.name}:{line_number}"
                )
            if not target.raw_taxid:
                raise ValueError(f"missing raw_taxid at {source.name}:{line_number}")
            if target.sequence_length != len(target.normalized_sequence):
                raise ValueError(
                    f"invalid sequence length at {source.name}:{line_number}"
                )
            if target.sequence_sha256 != sequence_digest(target.normalized_sequence):
                raise ValueError(
                    f"invalid sequence SHA-256 at {source.name}:{line_number}"
                )
            seen.add(target.target_id)
            targets.append(target)
    return tuple(targets)


def _classify_contexts(
    target: TargetTaxonomicContext, record_anchor: SpeciesAnchor
) -> TaxonomicRelationship:
    """Apply D2 relationship semantics to already-resolved target context."""

    if (
        target.resolution.resolved_taxid is None
        or record_anchor.resolution.resolved_taxid is None
    ):
        return TaxonomicRelationship.UNRESOLVED
    if target.resolution.resolved_taxid == record_anchor.resolution.resolved_taxid:
        return TaxonomicRelationship.SAME_TAXON
    if (
        target.species_anchor is None
        or target.species_anchor.status is not SpeciesAnchorStatus.RESOLVED
        or target.species_anchor.species_anchor is None
        or record_anchor.status is not SpeciesAnchorStatus.RESOLVED
        or record_anchor.species_anchor is None
    ):
        return TaxonomicRelationship.UNRESOLVED
    if target.species_anchor.species_anchor.taxid == record_anchor.species_anchor.taxid:
        return TaxonomicRelationship.SAME_SPECIES_DIFFERENT_SUBTAXON
    return TaxonomicRelationship.CROSS_SPECIES


def _issue(
    *,
    target: TargetTaxonomicContext,
    status: str,
    reason: str,
    record: UniProtRecord | None = None,
    record_resolution: TaxonomyResolution | None = None,
    relationship: TaxonomicRelationship | None = None,
) -> dict[str, str]:
    return {
        "target_id": target.target.target_id,
        "target_raw_taxid": target.target.raw_taxid,
        "target_resolved_taxid": target.resolution.resolved_taxid or "",
        "target_optional_accession": target.target.optional_accession,
        "optional_accession_match": _optional_accession_match(target, record),
        "uniprot_primary_accession": "" if record is None else record.primary_accession,
        "uniprot_raw_taxid": "" if record is None else record.raw_taxid,
        "uniprot_resolved_taxid": ""
        if record_resolution is None or record_resolution.resolved_taxid is None
        else record_resolution.resolved_taxid,
        "taxonomic_relationship": "" if relationship is None else relationship.value,
        "source_path": "" if record is None or record.source is None else record.source,
        "source_line": ""
        if record is None or record.line is None
        else str(record.line),
        "status": status,
        "reason": reason,
    }


def _optional_accession_match(
    target: TargetTaxonomicContext, record: UniProtRecord | None
) -> str:
    accession = target.target.optional_accession
    if not accession:
        return "NOT_SUPPLIED"
    if record is None:
        return "NOT_EVALUATED"
    if accession == record.primary_accession:
        return "PRIMARY"
    if accession in record.secondary_accessions:
        return "SECONDARY"
    return "MISMATCH"


_MATCH_COLUMNS = (
    "target_id",
    "target_raw_taxid",
    "target_resolved_taxid",
    "target_optional_accession",
    "optional_accession_match",
    "target_sequence_sha256",
    "target_sequence_length",
    "uniprot_primary_accession",
    "uniprot_accessions",
    "uniprot_entry_name",
    "uniprot_section",
    "uniprot_raw_taxid",
    "uniprot_resolved_taxid",
    "taxonomic_relationship",
    "source_path",
    "source_line",
    "match_type",
)
_ISSUE_COLUMNS = (
    "target_id",
    "target_raw_taxid",
    "target_resolved_taxid",
    "target_optional_accession",
    "optional_accession_match",
    "uniprot_primary_accession",
    "uniprot_raw_taxid",
    "uniprot_resolved_taxid",
    "taxonomic_relationship",
    "source_path",
    "source_line",
    "status",
    "reason",
)


def match_prepared_targets(
    *,
    prepared_targets_tsv: Source | str | Path,
    sources: Mapping[UniProtSection, Source | str | Path],
    taxonomy: NcbiTaxonomyIndex,
    output_directory: str | Path,
    fragment_matcher: FragmentMatcher | None = None,
    release: ReleaseMatchConfiguration = START_RELEASE,
) -> ReleaseMatchResult:
    """Match a release by scanning each supplied UniProt DAT source once."""

    prepared_source = Source.from_value(prepared_targets_tsv)
    source_by_section = {
        section: Source.from_value(source) for section, source in sources.items()
    }
    if set(source_by_section) != set(_SECTION_ORDER):
        raise ValueError(
            "release matching requires exactly Swiss-Prot and TrEMBL sources"
        )
    if len({source.path.resolve() for source in source_by_section.values()}) != 2:
        raise ValueError("Swiss-Prot and TrEMBL release sources must be distinct files")
    output_directory = Path(output_directory)
    output_directory.mkdir(parents=True, exist_ok=True)
    output_paths = tuple(
        output_directory / name
        for name in (
            release.matches_filename,
            release.issues_filename,
            release.summary_filename,
        )
    )
    input_paths = {
        prepared_source.path.resolve(),
        *(source.path.resolve() for source in source_by_section.values()),
    }
    if any(path.resolve() in input_paths for path in output_paths):
        raise ValueError("release matching outputs must not overwrite inputs")

    contexts: list[TargetTaxonomicContext] = []
    lookup: dict[tuple[str, int], list[TargetTaxonomicContext]] = {}
    status_counts = {status.value: 0 for status in TaxonomyResolutionStatus}
    for target in _load_prepared_targets(prepared_source):
        resolution = taxonomy.resolve_taxid(target.raw_taxid)
        status_counts[resolution.status.value] += 1
        anchor = (
            taxonomy.species_anchor(target.raw_taxid)
            if resolution.resolved_taxid
            else None
        )
        context = TargetTaxonomicContext(target, resolution, anchor)
        contexts.append(context)
        if resolution.status in {
            TaxonomyResolutionStatus.DELETED,
            TaxonomyResolutionStatus.UNRESOLVED,
        }:
            continue
        lookup.setdefault((target.sequence_sha256, target.sequence_length), []).append(
            context
        )

    matcher = (
        fragment_matcher if fragment_matcher is not None else DeferredFragmentMatcher()
    )
    matched_target_ids: set[str] = set()
    counters = {
        "fragment_records_skipped": 0,
        "hash_length_candidate_records": 0,
        "literal_sequence_matches": 0,
        "same_taxon_admitted_matches": 0,
        "same_species_different_subtaxon_candidates": 0,
        "cross_species_candidates": 0,
        "unresolved_record_taxonomy_candidates": 0,
    }
    records_by_section = {section.value: 0 for section in _SECTION_ORDER}
    records_by_source = {
        source_by_section[section].name: 0 for section in _SECTION_ORDER
    }

    with tempfile.TemporaryDirectory(dir=output_directory) as temporary_directory:
        temporary = Path(temporary_directory)
        temporary_matches, temporary_issues, temporary_summary = (
            temporary / path.name for path in output_paths
        )
        with (
            temporary_matches.open("w", encoding="utf-8", newline="\n") as match_handle,
            temporary_issues.open("w", encoding="utf-8", newline="\n") as issue_handle,
        ):
            match_writer = csv.DictWriter(
                match_handle,
                fieldnames=_MATCH_COLUMNS,
                delimiter="\t",
                lineterminator="\n",
            )
            issue_writer = csv.DictWriter(
                issue_handle,
                fieldnames=_ISSUE_COLUMNS,
                delimiter="\t",
                lineterminator="\n",
            )
            match_writer.writeheader()
            issue_writer.writeheader()
            for target in contexts:
                if target.resolution.status in {
                    TaxonomyResolutionStatus.DELETED,
                    TaxonomyResolutionStatus.UNRESOLVED,
                }:
                    issue_writer.writerow(
                        _issue(
                            target=target,
                            status=f"TARGET_TAXID_{target.resolution.status.value}",
                            reason=(
                                "target TaxID cannot participate in strict "
                                f"{release.role} matching"
                            ),
                        )
                    )
            for section in _SECTION_ORDER:
                parser = UniProtDatParser(section)
                for record in parser.iter_records(source_by_section[section]):
                    records_by_section[section.value] += 1
                    records_by_source[source_by_section[section].name] += 1
                    if record.is_fragment:
                        counters["fragment_records_skipped"] += 1
                        matcher.examine(record)
                        continue
                    candidates = lookup.get(
                        (record.sequence_sha256, record.sequence_length)
                    )
                    if not candidates:
                        continue
                    counters["hash_length_candidate_records"] += 1
                    literal_targets = [
                        target
                        for target in candidates
                        if target.target.normalized_sequence == record.sequence
                    ]
                    for target in candidates:
                        if target not in literal_targets:
                            issue_writer.writerow(
                                _issue(
                                    target=target,
                                    record=record,
                                    status="LITERAL_SEQUENCE_MISMATCH",
                                    reason=(
                                        "hash and length candidate did not have "
                                        "identical normalized sequence"
                                    ),
                                )
                            )
                    if not literal_targets:
                        continue
                    record_anchor = taxonomy.species_anchor(record.raw_taxid)
                    record_resolution = record_anchor.resolution
                    for target in literal_targets:
                        counters["literal_sequence_matches"] += 1
                        relationship = _classify_contexts(target, record_anchor)
                        if relationship is TaxonomicRelationship.SAME_TAXON:
                            match_writer.writerow(
                                {
                                    "target_id": target.target.target_id,
                                    "target_raw_taxid": target.target.raw_taxid,
                                    "target_resolved_taxid": (
                                        target.resolution.resolved_taxid or ""
                                    ),
                                    "target_optional_accession": (
                                        target.target.optional_accession
                                    ),
                                    "optional_accession_match": (
                                        _optional_accession_match(target, record)
                                    ),
                                    "target_sequence_sha256": (
                                        target.target.sequence_sha256
                                    ),
                                    "target_sequence_length": str(
                                        target.target.sequence_length
                                    ),
                                    "uniprot_primary_accession": (
                                        record.primary_accession
                                    ),
                                    "uniprot_accessions": ";".join(
                                        (
                                            record.primary_accession,
                                            *record.secondary_accessions,
                                        )
                                    ),
                                    "uniprot_entry_name": record.entry_name,
                                    "uniprot_section": record.section.value,
                                    "uniprot_raw_taxid": record.raw_taxid,
                                    "uniprot_resolved_taxid": (
                                        record_resolution.resolved_taxid or ""
                                    ),
                                    "taxonomic_relationship": relationship.value,
                                    "source_path": record.source or "",
                                    "source_line": ""
                                    if record.line is None
                                    else str(record.line),
                                    "match_type": "EXACT_FULL_LENGTH",
                                }
                            )
                            matched_target_ids.add(target.target.target_id)
                            counters["same_taxon_admitted_matches"] += 1
                        else:
                            counter = {
                                TaxonomicRelationship.SAME_SPECIES_DIFFERENT_SUBTAXON: (
                                    "same_species_different_subtaxon_candidates"
                                ),
                                TaxonomicRelationship.CROSS_SPECIES: (
                                    "cross_species_candidates"
                                ),
                                TaxonomicRelationship.UNRESOLVED: (
                                    "unresolved_record_taxonomy_candidates"
                                ),
                            }[relationship]
                            counters[counter] += 1
                            issue_writer.writerow(
                                _issue(
                                    target=target,
                                    record=record,
                                    record_resolution=record_resolution,
                                    relationship=relationship,
                                    status="NON_STRICT_TAXONOMIC_RELATIONSHIP",
                                    reason="exact sequence candidate is not SAME_TAXON",
                                )
                            )
            for target in contexts:
                if (
                    target.resolution.resolved_taxid is not None
                    and target.target.target_id not in matched_target_ids
                ):
                    issue_writer.writerow(
                        _issue(
                            target=target,
                            status=release.no_strict_match_status,
                            reason=(
                                "target has no strict SAME_TAXON exact full-length "
                                f"{release.role} match"
                            ),
                        )
                    )
            match_handle.flush()
            os.fsync(match_handle.fileno())
            issue_handle.flush()
            os.fsync(issue_handle.fileno())
        summary = {
            "fragment_records_skipped": counters["fragment_records_skipped"],
            "hash_length_candidate_records": counters["hash_length_candidate_records"],
            "literal_sequence_matches": counters["literal_sequence_matches"],
            "records_streamed_by_section": records_by_section,
            "records_streamed_by_source": records_by_source,
            "release_role": release.role,
            "same_taxon_admitted_matches": counters["same_taxon_admitted_matches"],
            "same_species_different_subtaxon_candidates": counters[
                "same_species_different_subtaxon_candidates"
            ],
            "cross_species_candidates": counters["cross_species_candidates"],
            "target_taxonomy_statuses": status_counts,
            "targets_read": len(contexts),
            "targets_with_strict_match": len(matched_target_ids),
            "targets_without_strict_match": sum(
                1
                for target in contexts
                if target.resolution.resolved_taxid is not None
                and target.target.target_id not in matched_target_ids
            ),
            "unresolved_record_taxonomy_candidates": counters[
                "unresolved_record_taxonomy_candidates"
            ],
        }
        with temporary_summary.open("w", encoding="utf-8", newline="\n") as handle:
            json.dump(summary, handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        for temporary_path, output_path in zip(
            (temporary_matches, temporary_issues, temporary_summary),
            output_paths,
            strict=True,
        ):
            os.replace(temporary_path, output_path)
    return ReleaseMatchResult(
        output_paths[0],
        output_paths[1],
        output_paths[2],
        counters["same_taxon_admitted_matches"],
    )


__all__ = [
    "DeferredFragmentMatcher",
    "FragmentMatcher",
    "ReleaseMatchConfiguration",
    "ReleaseMatchResult",
    "START_RELEASE",
    "TargetTaxonomicContext",
    "match_prepared_targets",
]
