"""Read-only canonical Stage 07 replay and strict dry-run rowwise comparison.

This diagnostic is NOT the production Stage 07 publisher. It reads existing
upstream bundles and an independent frozen dry-run oracle. It writes only
comparison reports in an explicitly chosen new diagnostic directory.
"""

from __future__ import annotations

import argparse
import csv
import gzip
import json
from collections import Counter
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from probe.resolver import ResolverPolicy
from probe.resolver_alignments import collect_alignments
from probe.resolver_assembly import STAGE07_COLUMNS, resolve_joined_candidates
from probe.resolver_candidates import (
    CandidateKey,
    collect_candidate_provenance,
    load_candidate_universe,
)

DRY_RUN_COLUMNS = tuple(
    column
    for column in STAGE07_COLUMNS
    if column not in {"evaluation_scope", "audit_outcome"}
)
KEY_COLUMNS = ("release", "target_id", "rid")


def _key(row: Mapping[str, str]) -> CandidateKey:
    key = tuple(row[name] for name in KEY_COLUMNS)
    if not all(key):
        raise ValueError("Empty dry-run candidate identity")
    return key  # type: ignore[return-value]


def _warning_tokens(value: str) -> tuple[str, ...]:
    if value in {"", "."}:
        return ()
    tokens = value.split(",")
    if any(not token or token.strip() != token for token in tokens):
        raise ValueError(f"Malformed warning tokens: {value!r}")
    if len(tokens) != len(set(tokens)):
        raise ValueError(f"Repeated warning tokens: {value!r}")
    return tuple(sorted(tokens))


def _equivalent(field: str, expected: str, observed: str) -> bool:
    if field == "warnings":
        return _warning_tokens(expected) == _warning_tokens(observed)
    return expected == observed


def compare_dry_run(
    *,
    actual: Mapping[CandidateKey, Any],
    expected_path: Path,
    output_dir: Path,
    max_examples: int = 200,
) -> dict[str, Any]:
    """Compare all keys and 17 oracle fields; ignore warning token order.

    The frozen oracle has no evaluation_scope or audit_outcome fields.
    Those two new fields are intentionally NOT claimed to be oracle-verified.
    Every other field is compared for every input pair, not just counts.
    """
    if max_examples < 0:
        raise ValueError("max_examples must be nonnegative")
    if not expected_path.is_file():
        raise FileNotFoundError(expected_path)
    if output_dir.exists():
        raise FileExistsError(f"Diagnostic directory already exists: {output_dir}")
    if len(actual) != len(set(actual)):
        raise ValueError("Duplicate actual candidate identities")
    if any(key != item.key for key, item in actual.items()):
        raise ValueError("Contradictory actual candidate identities")

    output_dir.mkdir(parents=True)
    mismatches_path = output_dir / "mismatch_examples.tsv"
    report_path = output_dir / "comparison_summary.json"
    field_counts: Counter[str] = Counter()
    oracle_decisions: Counter[str] = Counter()
    actual_decisions: Counter[str] = Counter()
    seen: set[CandidateKey] = set()
    oracle_rows = 0
    mismatch_pairs: set[CandidateKey] = set()
    examples_written = 0

    opener = gzip.open if expected_path.suffix == ".gz" else open
    with (
        opener(expected_path, "rt", encoding="utf-8", newline="") as inp,
        mismatches_path.open("w", encoding="utf-8", newline="") as out,
    ):
        reader = csv.DictReader(inp, delimiter="\t")
        if tuple(reader.fieldnames or ()) != DRY_RUN_COLUMNS:
            raise ValueError("Unexpected frozen dry-run header/schema")
        writer = csv.writer(out, delimiter="\t", lineterminator="\n")
        writer.writerow((*KEY_COLUMNS, "field", "expected", "observed"))
        for row in reader:
            if None in row or any(value is None for value in row.values()):
                raise ValueError("Malformed reference dry-run TSV row")
            key = _key(row)
            if key in seen:
                raise ValueError(f"Duplicate reference key: {key}")
            seen.add(key)
            oracle_rows += 1
            oracle_decisions[row["decision"]] += 1
            candidate = actual.get(key)
            if candidate is None:
                mismatch_pairs.add(key)
                field_counts["MISSING_ACTUAL_PAIR"] += 1
                if examples_written < max_examples:
                    writer.writerow((*key, "MISSING_ACTUAL_PAIR", "present", "."))
                    examples_written += 1
                continue
            observed = candidate.tsv_row()
            actual_decisions[observed["decision"]] += 1
            for field in DRY_RUN_COLUMNS:
                reference_value = row[field]
                observed_value = observed[field]
                if not _equivalent(field, reference_value, observed_value):
                    mismatch_pairs.add(key)
                    field_counts[field] += 1
                    if examples_written < max_examples:
                        writer.writerow((*key, field, reference_value, observed_value))
                        examples_written += 1

        extra_keys = set(actual) - seen
        if extra_keys:
            field_counts["EXTRA_ACTUAL_PAIR"] += len(extra_keys)
        for key in sorted(extra_keys):
            mismatch_pairs.add(key)
            if examples_written < max_examples:
                writer.writerow((*key, "EXTRA_ACTUAL_PAIR", ".", "present"))
                examples_written += 1
            actual_decisions[actual[key].tsv_row()["decision"]] += 1

    report: dict[str, Any] = {
        "reference_rows": oracle_rows,
        "actual_rows": len(actual),
        "reference_decisions": dict(sorted(oracle_decisions.items())),
        "actual_decisions": dict(sorted(actual_decisions.items())),
        "pairs_with_differences": len(mismatch_pairs),
        "field_difference_counts": dict(sorted(field_counts.items())),
        "examples_written": examples_written,
        "oracle_fields_compared": list(DRY_RUN_COLUMNS),
        "new_fields_not_in_oracle": ["evaluation_scope", "audit_outcome"],
        "warning_order_ignored": True,
        "status": "PASS" if not mismatch_pairs else "DIFFERENCES_FOUND",
    }
    report_path.write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return report


def replay_from_run(
    run_dir: Path,
    start_id: str,
    end_id: str,
) -> dict[CandidateKey, Any]:
    """Replay the strict default policy from all validated frozen evidence."""
    if not run_dir.is_dir():
        raise NotADirectoryError(run_dir)
    if not start_id or not end_id or start_id == end_id:
        raise ValueError("Expected two distinct concrete release IDs")
    work = run_dir / "work/pipeline-v2"
    results = run_dir / "results/pipeline-v2"
    releases = {"START": start_id, "END": end_id}
    target_taxonomy = {
        release: work / role / "02B/02B_target_taxonomy.tsv"
        for role, release in releases.items()
    }
    anchors = {
        release: work / role / "04B/04B_classified_direct_anchors.tsv"
        for role, release in releases.items()
    }
    lineage = {
        release: work / role / "04D2/04D2_lineage_evidence.tsv"
        for role, release in releases.items()
    }
    print("[stage07] Read 01 / 05A2 / 05C2 candidates", flush=True)
    candidates = load_candidate_universe(
        targets_path=work / "common/01/01_targets.tsv",
        primary_path=results / "common/05A2/05A2_final_candidate_union.tsv",
        audit_path=results / "common/05C2/05C2_taxonomy_audit.tsv",
        release_ids=frozenset(releases.values()),
    )
    print(f"[stage07] Candidate pairs: {len(candidates)}", flush=True)
    print("[stage07] Validate 02B/04B/04D1/04D2 provenance", flush=True)
    provenance = collect_candidate_provenance(
        candidates=candidates,
        target_taxonomy_paths=target_taxonomy,
        classified_anchor_paths=anchors,
        index_path=work / "common/04D1/04D1_lineage_scan_index.tsv",
        evidence_paths=lineage,
    )
    print("[stage07] Validate 06A / 06C1 alignment evidence", flush=True)
    alignments = collect_alignments(
        candidates=candidates,
        jobs_path=work / "common/06A/06A_alignment_jobs.tsv",
        alignments_path=(results / "common/06C1/06C1_canonical_directional_glocal.tsv"),
    )
    print("[stage07] Classify all pairs under strict default", flush=True)
    return resolve_joined_candidates(
        provenance=provenance,
        alignments=alignments,
        release_order=(start_id, end_id),
        policy=ResolverPolicy(),
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--start-id", required=True)
    parser.add_argument("--end-id", required=True)
    parser.add_argument("--reference", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--max-examples", type=int, default=200)
    args = parser.parse_args(argv)
    resolved = replay_from_run(args.run_dir, args.start_id, args.end_id)
    print("[stage07] Compare all keys against frozen strict dry-run", flush=True)
    summary = compare_dry_run(
        actual=resolved,
        expected_path=args.reference,
        output_dir=args.output_dir,
        max_examples=args.max_examples,
    )
    print(json.dumps(summary, indent=2, sort_keys=True), flush=True)
    print(f"[stage07] Reports: {args.output_dir}", flush=True)
    return 0 if summary["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
