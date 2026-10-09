#!/usr/bin/env python3
"""Stage 07 production decision-table generator.

This script does not publish bundles or modify upstream evidence.
The Snakemake runner owns manifest validation and atomic publication.
"""

from __future__ import annotations

import argparse
import csv
import json
from collections import Counter
from pathlib import Path

from probe.resolver import ResolverPolicy
from probe.resolver_alignments import collect_alignments
from probe.resolver_assembly import STAGE07_COLUMNS, resolve_joined_candidates
from probe.resolver_candidates import (
    collect_candidate_provenance,
    load_candidate_universe,
)
from probe.resolver_metadata import build_selection_metadata

POLICY_VERSION = "stage07-resolver-v1"


ACCEPTED_COLUMNS = (
    "release",
    "target_id",
    "rid",
    "target_accession",
    "candidate_accession",
    "annotation_donor",
    "decision_basis",
    "sequence_relation",
    "exact_sequence",
    "scope",
    "evaluation_scope",
    "audit_outcome",
    "accepted_candidates_for_target_release",
)


def write_accepted_projection(
    complete_path: Path,
    accepted_path: Path,
    *,
    expected_count: int | None = None,
) -> int:
    """Project ACCEPTED rows without repeating scientific classification."""
    if complete_path.resolve() == accepted_path.resolve():
        raise ValueError("Complete and accepted TSV paths must differ")

    if accepted_path.exists() or accepted_path.is_symlink():
        raise FileExistsError(accepted_path)

    accepted_count = 0

    with complete_path.open("r", encoding="utf-8", newline="") as complete_handle:
        reader = csv.DictReader(complete_handle, delimiter="\t")

        if tuple(reader.fieldnames or ()) != STAGE07_COLUMNS:
            raise ValueError("Stage 07 complete TSV schema mismatch")

        accepted_handle = accepted_path.open("x", encoding="utf-8", newline="")

        try:
            with accepted_handle:
                writer = csv.DictWriter(
                    accepted_handle,
                    fieldnames=ACCEPTED_COLUMNS,
                    delimiter="\t",
                    lineterminator="\n",
                )
                writer.writeheader()

                for row in reader:
                    if None in row or any(value is None for value in row.values()):
                        raise ValueError("Malformed Stage 07 complete TSV row")

                    if row["decision"] != "ACCEPTED":
                        continue

                    writer.writerow({field: row[field] for field in ACCEPTED_COLUMNS})
                    accepted_count += 1

            if expected_count is not None and accepted_count != expected_count:
                raise RuntimeError("Stage 07 accepted projection count mismatch")
        except BaseException:
            accepted_path.unlink(missing_ok=True)
            raise

    return accepted_count


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--work-root", required=True, type=Path)
    parser.add_argument("--results-root", required=True, type=Path)
    parser.add_argument("--start-release-id", required=True)
    parser.add_argument("--end-release-id", required=True)
    parser.add_argument(
        "--subtaxon-policy",
        choices=("strict", "include_audited_same_species"),
        default="strict",
    )
    parser.add_argument(
        "--partial-sequence",
        choices=("allow", "report_only"),
        default="allow",
    )
    parser.add_argument(
        "--documentary-fragment",
        choices=("allow", "report_only"),
        default="allow",
    )
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--summary", required=True, type=Path)
    parser.add_argument("--accepted-output", type=Path)
    parser.add_argument("--selection-metadata", type=Path)
    args = parser.parse_args()

    policy = ResolverPolicy(
        subtaxon_policy=args.subtaxon_policy,
        partial_sequence=args.partial_sequence,
        documentary_fragment=args.documentary_fragment,
    )

    start = args.start_release_id
    end = args.end_release_id

    if not start or not end or start == end:
        raise ValueError("START and END must be distinct release IDs")

    if args.output.resolve() == args.summary.resolve():
        raise ValueError("TSV and summary paths must be distinct")

    if args.output.exists() or args.summary.exists():
        raise FileExistsError("Stage 07 output already exists")

    if args.accepted_output is not None:
        if args.accepted_output.resolve() in {
            args.output.resolve(),
            args.summary.resolve(),
        }:
            raise ValueError("Stage 07 output paths must be distinct")

        if args.accepted_output.exists() or args.accepted_output.is_symlink():
            raise FileExistsError(
                f"Stage 07 accepted output exists: {args.accepted_output}"
            )

    if args.selection_metadata is not None:
        other_outputs = (
            args.output,
            args.summary,
            args.accepted_output,
        )
        if args.selection_metadata.resolve() in {
            path.resolve() for path in other_outputs if path is not None
        }:
            raise ValueError("Stage 07 output paths must be distinct")

        if args.selection_metadata.exists() or args.selection_metadata.is_symlink():
            raise FileExistsError(args.selection_metadata)

    work = args.work_root
    results = args.results_root
    releases = {"START": start, "END": end}

    taxonomy = {
        rid: work / role / "02B/02B_target_taxonomy.tsv"
        for role, rid in releases.items()
    }
    anchors = {
        rid: work / role / "04B/04B_classified_direct_anchors.tsv"
        for role, rid in releases.items()
    }
    lineage = {
        rid: work / role / "04D2/04D2_lineage_evidence.tsv"
        for role, rid in releases.items()
    }

    print("[07] Loading candidate universe", flush=True)

    candidates = load_candidate_universe(
        targets_path=work / "common/01/01_targets.tsv",
        primary_path=(results / "common/05A2/05A2_final_candidate_union.tsv"),
        audit_path=results / "common/05C2/05C2_taxonomy_audit.tsv",
        release_ids=frozenset(releases.values()),
    )

    print(f"[07] Candidates: {len(candidates)}", flush=True)
    print("[07] Validating documentary provenance", flush=True)

    provenance = collect_candidate_provenance(
        candidates=candidates,
        target_taxonomy_paths=taxonomy,
        classified_anchor_paths=anchors,
        index_path=work / "common/04D1/04D1_lineage_scan_index.tsv",
        evidence_paths=lineage,
    )

    print("[07] Validating alignment evidence", flush=True)

    alignments = collect_alignments(
        candidates=candidates,
        jobs_path=work / "common/06A/06A_alignment_jobs.tsv",
        alignments_path=(results / "common/06C1/06C1_canonical_directional_glocal.tsv"),
    )

    print("[07] Applying resolver policy", flush=True)

    resolved = resolve_joined_candidates(
        provenance=provenance,
        alignments=alignments,
        release_order=(start, end),
        policy=policy,
    )

    if len(resolved) != len(candidates):
        raise RuntimeError("Stage 07 candidate coverage mismatch")

    decision_counts = Counter()
    basis_counts = Counter()
    scope_counts = Counter()
    evaluation_scope_counts = Counter()
    audit_outcome_counts = Counter()
    donor_counts = Counter()
    relation_counts = Counter()
    accepted_groups = Counter()

    for row in resolved.values():
        decision = row.decision
        candidate = row.candidate

        decision_counts[decision.decision] += 1
        basis_counts[decision.decision_basis] += 1
        scope_counts[candidate.scope] += 1
        evaluation_scope_counts[decision.evaluation_scope] += 1
        relation_counts[row.sequence_relation] += 1
        donor_counts["YES" if decision.annotation_donor else "NO"] += 1

        if decision.audit_outcome is not None:
            audit_outcome_counts[decision.audit_outcome] += 1

        if decision.decision == "ACCEPTED":
            accepted_groups[(candidate.release, candidate.target_id)] += 1
        elif decision.annotation_donor:
            raise RuntimeError("Non-accepted candidate cannot donate GO")

    with args.output.open("x", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=STAGE07_COLUMNS,
            delimiter="\t",
            lineterminator="\n",
        )
        writer.writeheader()
        for row in resolved.values():
            writer.writerow(row.tsv_row())

    summary = {
        "stage": "07",
        "policy_version": POLICY_VERSION,
        "releases": releases,
        "policies": {
            "resolver": {"subtaxon_policy": policy.subtaxon_policy},
            "annotation_donor": {
                "partial_sequence": policy.partial_sequence,
                "documentary_fragment": policy.documentary_fragment,
            },
        },
        "candidate_pair_count": len(resolved),
        "decision_counts": dict(sorted(decision_counts.items())),
        "decision_basis_counts": dict(sorted(basis_counts.items())),
        "scope_counts": dict(sorted(scope_counts.items())),
        "evaluation_scope_counts": dict(sorted(evaluation_scope_counts.items())),
        "audit_outcome_counts": dict(sorted(audit_outcome_counts.items())),
        "annotation_donor_counts": dict(sorted(donor_counts.items())),
        "sequence_relation_counts": dict(sorted(relation_counts.items())),
        "multiple_accepted_groups": sum(
            count > 1 for count in accepted_groups.values()
        ),
        "maximum_accepted_multiplicity": max(accepted_groups.values(), default=0),
    }

    if args.accepted_output is not None:
        write_accepted_projection(
            args.output,
            args.accepted_output,
            expected_count=decision_counts["ACCEPTED"],
        )

    with args.summary.open("x", encoding="utf-8") as handle:
        json.dump(summary, handle, indent=2, sort_keys=True, allow_nan=False)
        handle.write("\n")

    if args.selection_metadata is not None:
        metadata = build_selection_metadata(
            start_release_id=start,
            end_release_id=end,
            policy=policy,
            policy_version=POLICY_VERSION,
        )

        if metadata["effective_policies"] != summary["policies"]:
            raise ValueError("Stage 07 metadata policy mismatch")

        if metadata["releases"] != summary["releases"]:
            raise ValueError("Stage 07 metadata release mismatch")

        with args.selection_metadata.open("x", encoding="utf-8") as handle:
            json.dump(
                metadata,
                handle,
                indent=2,
                sort_keys=True,
                allow_nan=False,
            )
            handle.write("\n")

    print(
        f"[07] Complete: {len(resolved)} candidates, "
        f"{decision_counts['ACCEPTED']} accepted, "
        f"{donor_counts['YES']} donors",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
