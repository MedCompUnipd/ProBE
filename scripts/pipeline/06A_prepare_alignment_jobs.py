#!/usr/bin/env python3
"""
Production ProBE candidate pipeline.

Stage 06A: freeze and audit the canonical pairwise alignment workload.

Inputs:
- canonical prepared targets;
- Stage 05A2 final primary candidate union;
- Stage 05C2 taxonomy audit channel;
- START and END Stage 05D candidate sequence stores.

Output:
- one canonical row per unique (release, target_id, RID);
- scope PRIMARY or TAXONOMY_AUDIT.

No dynamic-programming alignment and no SAME_ENTITY decision are made here.
Exact string identity is checked directly from the materialized sequence bytes.

If an alignment-permitted taxonomy-audit row refers to the same
(release, target_id, RID) already present in the primary universe, one PRIMARY
job is emitted and the audit provenance is attached to that row. The physical
alignment pair is never duplicated.
"""

from __future__ import annotations

import argparse
import csv
import json
from collections import Counter
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Target:
    accession: str
    sequence: str
    length: int


@dataclass(frozen=True)
class SequenceRecord:
    rid: str
    primary_accession: str
    entry: str
    raw_ox: str
    frag: str
    sequence_length: int
    pe: str
    sv: str
    sequence: str


OUTPUT_FIELDS = [
    "release",
    "target_id",
    "rid",
    "job_scope",
    "discovery_channel",
    "sequence_discovered",
    "lineage_discovered",
    "audit_class",
    "audit_key_source_reason",
    "target_optional_accession",
    "candidate_primary_accession",
    "entry",
    "raw_ox",
    "frag",
    "pe",
    "sv",
    "target_length",
    "candidate_length",
    "length_delta_candidate_minus_target",
    "length_ratio_short_over_long",
    "exact_sequence",
    "sequence_search_exact_sequence",
]


def positive_int(value: str, label: str) -> int:
    try:
        result = int(value)
    except ValueError as exc:
        raise ValueError(f"{label}: invalid integer {value!r}") from exc

    if result <= 0:
        raise ValueError(f"{label}: expected positive integer")

    return result


def canonical_rid(value: str, label: str) -> str:
    result = positive_int(value, label)
    if str(result) != value:
        raise ValueError(f"{label}: non-canonical RID {value!r}")
    return value


def load_targets(path: Path) -> dict[str, Target]:
    out: dict[str, Target] = {}

    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle, delimiter="\t")

        required = {
            "target_id",
            "optional_accession",
            "normalized_sequence",
            "sequence_length",
        }

        if reader.fieldnames is None or not required.issubset(reader.fieldnames):
            missing = required.difference(reader.fieldnames or ())
            raise ValueError(f"{path}: missing columns {sorted(missing)}")

        for row in reader:
            target_id = row["target_id"]

            if not target_id:
                raise ValueError(f"{path}: empty target_id")

            if target_id in out:
                raise ValueError(f"{path}: duplicate target_id {target_id}")

            sequence = row["normalized_sequence"]
            length = positive_int(
                row["sequence_length"],
                f"{path}:{target_id}:sequence_length",
            )

            if len(sequence) != length:
                raise ValueError(f"{path}: target {target_id} length mismatch")

            out[target_id] = Target(
                accession=row["optional_accession"],
                sequence=sequence,
                length=length,
            )

    return out


def load_store(
    path: Path,
    expected_release: str,
) -> dict[str, SequenceRecord]:
    out: dict[str, SequenceRecord] = {}

    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle, delimiter="\t")

        required = {
            "release",
            "rid",
            "primary_accession",
            "entry",
            "raw_ox",
            "frag",
            "sequence_length",
            "pe",
            "sv",
            "sequence",
        }

        if reader.fieldnames is None or not required.issubset(reader.fieldnames):
            missing = required.difference(reader.fieldnames or ())
            raise ValueError(f"{path}: missing columns {sorted(missing)}")

        for row in reader:
            if row["release"] != expected_release:
                raise ValueError(
                    f"{path}: expected release {expected_release}, "
                    f"found {row['release']}"
                )

            rid = canonical_rid(
                row["rid"],
                f"{path}:RID",
            )

            if rid in out:
                raise ValueError(f"{path}: duplicate RID {rid}")

            sequence = row["sequence"]
            length = positive_int(
                row["sequence_length"],
                f"{path}:RID={rid}:sequence_length",
            )

            if len(sequence) != length:
                raise ValueError(f"{path}: RID {rid} length mismatch")

            out[rid] = SequenceRecord(
                rid=rid,
                primary_accession=row["primary_accession"],
                entry=row["entry"],
                raw_ox=row["raw_ox"],
                frag=row["frag"],
                sequence_length=length,
                pe=row["pe"],
                sv=row["sv"],
                sequence=sequence,
            )

    return out


def short_over_long(a: int, b: int) -> float:
    return min(a, b) / max(a, b)


def build_job(
    *,
    release: str,
    target_id: str,
    rid: str,
    job_scope: str,
    discovery_channel: str,
    sequence_discovered: str,
    lineage_discovered: str,
    sequence_search_exact: str,
    audit_class: str,
    audit_reason: str,
    targets: dict[str, Target],
    store: dict[str, SequenceRecord],
) -> dict[str, str | int]:
    if target_id not in targets:
        raise ValueError(f"unknown target_id {target_id}")

    if rid not in store:
        raise ValueError(f"RID {rid} missing from sequence store for {release}")

    if sequence_discovered not in {"0", "1"}:
        raise ValueError(f"invalid sequence_discovered for {release}/{target_id}/{rid}")

    if lineage_discovered not in {"0", "1"}:
        raise ValueError(f"invalid lineage_discovered for {release}/{target_id}/{rid}")

    target = targets[target_id]
    record = store[rid]

    exact = target.sequence == record.sequence

    if job_scope == "PRIMARY" and sequence_discovered == "1":
        if sequence_search_exact not in {"0", "1"}:
            raise ValueError(
                f"invalid sequence-search exact flag for "
                f"{release}/{target_id}/{rid}: "
                f"{sequence_search_exact!r}"
            )

        if (sequence_search_exact == "1") != exact:
            raise ValueError(
                "sequence-search exact flag disagrees with "
                "materialized sequence bytes for "
                f"{release}/{target_id}/{rid}"
            )

    return {
        "release": release,
        "target_id": target_id,
        "rid": rid,
        "job_scope": job_scope,
        "discovery_channel": discovery_channel,
        "sequence_discovered": sequence_discovered,
        "lineage_discovered": lineage_discovered,
        "audit_class": audit_class,
        "audit_key_source_reason": audit_reason,
        "target_optional_accession": target.accession,
        "candidate_primary_accession": record.primary_accession,
        "entry": record.entry,
        "raw_ox": record.raw_ox,
        "frag": record.frag,
        "pe": record.pe,
        "sv": record.sv,
        "target_length": target.length,
        "candidate_length": record.sequence_length,
        "length_delta_candidate_minus_target": record.sequence_length - target.length,
        "length_ratio_short_over_long": (
            f"{short_over_long(target.length, record.sequence_length):.10f}"
        ),
        "exact_sequence": int(exact),
        "sequence_search_exact_sequence": sequence_search_exact
        if sequence_discovered == "1"
        else "",
    }


def main() -> int:
    ap = argparse.ArgumentParser()

    ap.add_argument("--targets", type=Path, required=True)
    ap.add_argument("--candidate-union", type=Path, required=True)
    ap.add_argument("--taxonomy-audit", type=Path, required=True)
    ap.add_argument("--start-sequence-store", type=Path, required=True)
    ap.add_argument("--end-sequence-store", type=Path, required=True)
    ap.add_argument("--start-release-id", required=True)
    ap.add_argument("--end-release-id", required=True)
    ap.add_argument("--output", type=Path, required=True)
    ap.add_argument("--summary", type=Path, required=True)

    args = ap.parse_args()

    if args.start_release_id == args.end_release_id:
        raise ValueError("START and END release IDs must be distinct")

    for path in (
        args.targets,
        args.candidate_union,
        args.taxonomy_audit,
        args.start_sequence_store,
        args.end_sequence_store,
    ):
        if not path.is_file():
            raise FileNotFoundError(path)

    for path in (args.output, args.summary):
        if path.exists():
            raise FileExistsError(f"refusing to overwrite: {path}")
        path.parent.mkdir(parents=True, exist_ok=True)

    releases = (
        args.start_release_id,
        args.end_release_id,
    )
    release_set = set(releases)

    targets = load_targets(args.targets)

    stores = {
        args.start_release_id: load_store(
            args.start_sequence_store,
            args.start_release_id,
        ),
        args.end_release_id: load_store(
            args.end_sequence_store,
            args.end_release_id,
        ),
    }

    jobs: dict[
        tuple[str, str, str],
        dict[str, str | int],
    ] = {}

    primary_counts = Counter(
        {
            args.start_release_id: 0,
            args.end_release_id: 0,
        }
    )

    audit_allowed_counts = Counter(
        {
            args.start_release_id: 0,
            args.end_release_id: 0,
        }
    )

    audit_job_counts = Counter(
        {
            args.start_release_id: 0,
            args.end_release_id: 0,
        }
    )

    audit_primary_overlap_counts = Counter(
        {
            args.start_release_id: 0,
            args.end_release_id: 0,
        }
    )

    with args.candidate_union.open(
        "r",
        encoding="utf-8",
        newline="",
    ) as handle:
        reader = csv.DictReader(handle, delimiter="\t")

        required = {
            "release",
            "target_id",
            "rid",
            "discovery_channel",
            "sequence_discovered",
            "lineage_discovered",
            "seq_exact_sequence",
        }

        if reader.fieldnames is None or not required.issubset(reader.fieldnames):
            missing = required.difference(reader.fieldnames or ())
            raise ValueError(
                f"{args.candidate_union}: missing columns {sorted(missing)}"
            )

        for row in reader:
            release = row["release"]

            if release not in release_set:
                raise ValueError(
                    f"{args.candidate_union}: unexpected release {release!r}"
                )

            rid = canonical_rid(
                row["rid"],
                f"{args.candidate_union}:{release}:RID",
            )

            key = (
                release,
                row["target_id"],
                rid,
            )

            if key in jobs:
                raise ValueError(
                    f"{args.candidate_union}: duplicate primary alignment key {key}"
                )

            jobs[key] = build_job(
                release=release,
                target_id=row["target_id"],
                rid=rid,
                job_scope="PRIMARY",
                discovery_channel=row["discovery_channel"],
                sequence_discovered=row["sequence_discovered"],
                lineage_discovered=row["lineage_discovered"],
                sequence_search_exact=row["seq_exact_sequence"],
                audit_class="",
                audit_reason="",
                targets=targets,
                store=stores[release],
            )

            primary_counts[release] += 1

    seen_audit_keys: set[tuple[str, str, str]] = set()

    with args.taxonomy_audit.open(
        "r",
        encoding="utf-8",
        newline="",
    ) as handle:
        reader = csv.DictReader(handle, delimiter="\t")

        required = {
            "release",
            "target_id",
            "rid",
            "audit_alignment_allowed",
            "audit_class",
            "key_source_reason",
            "record_already_in_primary_union_for_target",
        }

        if reader.fieldnames is None or not required.issubset(reader.fieldnames):
            missing = required.difference(reader.fieldnames or ())
            raise ValueError(
                f"{args.taxonomy_audit}: missing columns {sorted(missing)}"
            )

        for row in reader:
            release = row["release"]

            if release not in release_set:
                raise ValueError(
                    f"{args.taxonomy_audit}: unexpected release {release!r}"
                )

            allowed = row["audit_alignment_allowed"]
            overlap_declared = row["record_already_in_primary_union_for_target"]

            if allowed not in {"0", "1"}:
                raise ValueError(
                    f"{args.taxonomy_audit}: invalid audit_alignment_allowed"
                )

            if overlap_declared not in {"0", "1"}:
                raise ValueError(
                    f"{args.taxonomy_audit}: invalid "
                    "record_already_in_primary_union_for_target"
                )

            rid = canonical_rid(
                row["rid"],
                f"{args.taxonomy_audit}:{release}:RID",
            )

            key = (
                release,
                row["target_id"],
                rid,
            )

            if key in seen_audit_keys:
                raise ValueError(f"{args.taxonomy_audit}: duplicate audit key {key}")

            seen_audit_keys.add(key)

            actual_overlap = key in jobs

            if (overlap_declared == "1") != actual_overlap:
                raise RuntimeError(
                    "06A 05C2 primary-overlap flag mismatch for "
                    f"{key}: declared={overlap_declared} "
                    f"observed={int(actual_overlap)}"
                )

            if allowed != "1":
                continue

            audit_allowed_counts[release] += 1

            if actual_overlap:
                existing = jobs[key]

                if existing["job_scope"] != "PRIMARY":
                    raise RuntimeError(f"06A overlap key is not PRIMARY: {key}")

                if existing["audit_class"]:
                    raise RuntimeError(f"06A duplicate audit provenance for {key}")

                existing["audit_class"] = row["audit_class"]
                existing["audit_key_source_reason"] = row["key_source_reason"]

                audit_primary_overlap_counts[release] += 1
                continue

            jobs[key] = build_job(
                release=release,
                target_id=row["target_id"],
                rid=rid,
                job_scope="TAXONOMY_AUDIT",
                discovery_channel="TAXONOMY_AUDIT",
                sequence_discovered="0",
                lineage_discovered="0",
                sequence_search_exact="",
                audit_class=row["audit_class"],
                audit_reason=row["key_source_reason"],
                targets=targets,
                store=stores[release],
            )

            audit_job_counts[release] += 1

    for release in releases:
        if (
            audit_allowed_counts[release]
            != audit_job_counts[release] + audit_primary_overlap_counts[release]
        ):
            raise RuntimeError(f"06A {release} taxonomy-audit arithmetic violation")

    rows = sorted(
        jobs.values(),
        key=lambda row: (
            str(row["release"]),
            str(row["target_id"]),
            int(str(row["rid"])),
        ),
    )

    with args.output.open(
        "w",
        encoding="utf-8",
        newline="",
    ) as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=OUTPUT_FIELDS,
            delimiter="\t",
            lineterminator="\n",
        )
        writer.writeheader()
        writer.writerows(rows)

    release_scope_counts = {release: Counter() for release in releases}

    release_exact_counts = {release: Counter() for release in releases}

    release_channel_counts = {release: Counter() for release in releases}

    for row in rows:
        release = str(row["release"])

        release_scope_counts[release][str(row["job_scope"])] += 1

        release_exact_counts[release][
            "EXACT" if int(row["exact_sequence"]) else "NONEXACT"
        ] += 1

        release_channel_counts[release][str(row["discovery_channel"])] += 1

    summary = {
        "stage": "06A",
        "releases": {
            "START": args.start_release_id,
            "END": args.end_release_id,
        },
        "target_count": len(targets),
        "sequence_store_unique_rid_counts": {
            release: len(stores[release]) for release in releases
        },
        "primary_job_counts": {
            release: primary_counts[release] for release in releases
        },
        "taxonomy_audit_alignment_allowed_row_counts": {
            release: audit_allowed_counts[release] for release in releases
        },
        "taxonomy_audit_primary_overlap_counts": {
            release: audit_primary_overlap_counts[release] for release in releases
        },
        "taxonomy_audit_job_counts": {
            release: audit_job_counts[release] for release in releases
        },
        "alignment_job_count": len(rows),
        "release_scope_counts": {
            release: dict(sorted(release_scope_counts[release].items()))
            for release in releases
        },
        "release_exact_counts": {
            release: dict(sorted(release_exact_counts[release].items()))
            for release in releases
        },
        "release_discovery_channel_counts": {
            release: dict(sorted(release_channel_counts[release].items()))
            for release in releases
        },
        "policy": {
            "same_entity_decision_made_here": False,
            "alignment_made_here": False,
            "exact_string_comparison_only": True,
            "exact_pairs_may_bypass_dynamic_programming_later": True,
            "taxonomy_audit_kept_separate_from_primary": True,
            "primary_taxonomy_audit_pair_overlap_emits_primary_only": True,
        },
    }

    with args.summary.open(
        "w",
        encoding="utf-8",
        newline="\n",
    ) as handle:
        json.dump(
            summary,
            handle,
            indent=2,
            sort_keys=True,
        )
        handle.write("\n")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
