#!/usr/bin/env python3
"""Read Stage 2C evidence, run Stage 07, and isolate strain-policy results.

A dry run is the default. Scientific execution requires --execute and the
already-published Stage 2C preview and alignment-completion directories.
No upstream artifact is modified. Do not use this CLI for canonical Stage 07.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import tempfile
from collections import Counter
from pathlib import Path

from probe.resolver_alignments import collect_alignments
from probe.resolver_anchors import _load_target_taxonomy
from probe.resolver_assembly import STAGE07_COLUMNS
from probe.resolver_candidates import (
    collect_candidate_provenance,
    load_candidate_universe,
)
from probe.stage2c_alignment_completion import (
    FINAL_OUTPUT,
    POLICY_SKIP,
    SUMMARY,
)
from probe.stage2c_alignment_preview import (
    AUDIT_OUT,
    END_STORE,
    JOBS_OUT,
    START_STORE,
    SUMMARY_OUT,
    UNION_OUT,
)
from probe.stage2c_resolver_variant import resolve_stage2c_variant

SCHEMA = "stage2c-resolver-variant-v1"
COMPLETE = "2C_07_complete.tsv"
ACCEPTED = "2C_07_accepted.tsv"
REPORT = "2C_07_summary.json"


def _sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _validated(path: Path, required: set[str]):
    with path.open("r", encoding="utf-8", newline="") as stream:
        reader = csv.DictReader(stream, delimiter="\t")
        header = reader.fieldnames or []
        if len(header) != len(set(header)) or not required.issubset(header):
            raise ValueError(f"Invalid evidence TSV columns: {path}")
        for number, row in enumerate(reader, start=2):
            if None in row or any(value is None for value in row.values()):
                raise ValueError(f"Malformed evidence row: {path}:{number}")
            yield row


def _check_manifest(directory: Path, name: str, outputs: tuple[str, ...]):
    report = json.loads((directory / name).read_text(encoding="utf-8"))
    declared = report.get("output_sha256")
    if not isinstance(declared, dict):
        raise ValueError(f"Missing Stage 2C manifest SHA256: {name}")
    for output in outputs:
        if declared.get(output) != _sha(directory / output):
            raise ValueError(f"Stage 2C input SHA256 mismatch: {output}")
    return report


def _policy_skipped_keys(
    path: Path, releases: tuple[str, str]
) -> frozenset[tuple[str, str, str]]:
    """Read immutable 06C0 policy exclusions, never infer missing rows."""
    keys: set[tuple[str, str, str]] = set()
    previous: tuple[str, str, int] | None = None
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle, delimiter="\t")
        if reader.fieldnames != ["release", "target_id", "rid", "reason"]:
            raise ValueError("Invalid Stage 2C policy skip schema")
        for row in reader:
            if None in row or any(value is None for value in row.values()):
                raise ValueError("Malformed policy skip row")
            release, target, rid = (row["release"], row["target_id"], row["rid"])
            if (
                release not in releases
                or not target
                or not rid.isascii()
                or not rid.isdecimal()
                or int(rid) < 1
                or str(int(rid)) != rid
                or row["reason"] != "CROSS_TAXID_NONEXACT"
            ):
                raise ValueError("Invalid policy skip key/reason")
            ordered = (release, target, int(rid))
            if previous is not None and ordered <= previous:
                raise ValueError("Unsorted or duplicate policy skip key")
            previous = ordered
            keys.add((release, target, rid))
    return frozenset(keys)


def _sequence_proofs(
    *,
    wanted: set[tuple[str, str, str]],
    candidates: dict,
    targets: Path,
    stores: dict[str, Path],
) -> dict[tuple[str, str, str], tuple[str, str]]:
    if not wanted:
        return {}
    targets_needed = {key[1] for key in wanted}
    target_strings: dict[str, str] = {}
    required = {"target_id", "normalized_sequence", "sequence_length"}
    for row in _validated(targets, required):
        target = row["target_id"]
        if target not in targets_needed:
            continue
        if target in target_strings:
            raise ValueError(f"Duplicate target sequence: {target}")
        sequence = row["normalized_sequence"]
        if not sequence or str(len(sequence)) != row["sequence_length"]:
            raise ValueError(f"Invalid target sequence: {target}")
        target_strings[target] = sequence
    if set(target_strings) != targets_needed:
        raise ValueError("Stage 01 target coverage missing for literal proof")
    requested = {(release, rid) for release, _, rid in wanted}
    found: dict[tuple[str, str], str] = {}
    keys_by_record: dict[tuple[str, str], list] = {}
    for key in wanted:
        keys_by_record.setdefault((key[0], key[2]), []).append(key)
    required = {
        "release",
        "rid",
        "primary_accession",
        "raw_ox",
        "frag",
        "sequence_length",
        "sequence",
    }
    for release, path in stores.items():
        for row in _validated(path, required):
            record = (release, row["rid"])
            if row["release"] != release:
                raise ValueError("05D store release disagrees with release context")
            if record not in requested:
                continue
            if record in found:
                raise ValueError(f"Duplicate RID in 05D variant: {record}")
            sequence = row["sequence"]
            if not sequence or str(len(sequence)) != row["sequence_length"]:
                raise ValueError(f"Invalid candidate sequence: {record}")
            found[record] = sequence
            for key in keys_by_record[record]:
                candidate = candidates[key]
                if (
                    row["primary_accession"] != candidate.candidate_accession
                    or row["raw_ox"] != candidate.candidate_raw_ox
                    or row["frag"] != str(int(candidate.fragment_record))
                ):
                    raise ValueError(f"05D candidate metadata mismatch: {key}")
    if set(found) != requested:
        raise ValueError("Stage 05D lacks required candidate sequences")
    return {key: (target_strings[key[1]], found[(key[0], key[2])]) for key in wanted}


def _write(publication: Path, result, metadata: dict) -> None:
    rows = sorted(
        result.rows.values(),
        key=lambda row: (
            metadata["release_order"].index(row.key[0]),
            row.key[1],
            int(row.key[2]),
        ),
    )
    complete = publication / COMPLETE
    accepted = publication / ACCEPTED
    with complete.open("x", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=STAGE07_COLUMNS,
            delimiter="\t",
            lineterminator="\n",
        )
        writer.writeheader()
        for row in rows:
            writer.writerow(row.tsv_row())
    with accepted.open("x", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=STAGE07_COLUMNS,
            delimiter="\t",
            lineterminator="\n",
        )
        writer.writeheader()
        for row in rows:
            if row.decision.decision == "ACCEPTED":
                writer.writerow(row.tsv_row())
    decisions = Counter(row.decision.decision for row in rows)
    donors = sum(row.decision.annotation_donor for row in rows)
    if donors > decisions["ACCEPTED"]:
        raise ValueError("A nonaccepted candidate became GO donor")
    metadata.update(
        {
            "schema": SCHEMA,
            "candidates": len(rows),
            "decisions": dict(sorted(decisions.items())),
            "annotation_donor_yes": donors,
            "cross_strain_candidates": result.cross_strain_total,
            "cross_strain_donors": result.cross_strain_donors,
            "discovery_reexecuted": False,
            "output_sha256": {
                name: _sha(publication / name) for name in (COMPLETE, ACCEPTED)
            },
        }
    )
    (publication / REPORT).write_text(
        json.dumps(metadata, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def run(args: argparse.Namespace) -> None:
    releases = (args.start_release_id, args.end_release_id)
    if not all(releases) or releases[0] == releases[1]:
        raise ValueError("Two distinct releases are required")
    work = args.work_root
    preview = args.preview
    completion = args.completion
    inputs = {
        "primary": preview / UNION_OUT,
        "audit": preview / AUDIT_OUT,
        "jobs": preview / JOBS_OUT,
        "alignments": completion / FINAL_OUTPUT,
    }
    preview_summary = _check_manifest(
        preview,
        SUMMARY_OUT,
        (UNION_OUT, AUDIT_OUT, JOBS_OUT, START_STORE, END_STORE),
    )
    complete_summary = _check_manifest(
        completion,
        SUMMARY,
        (FINAL_OUTPUT,),
    )
    if preview_summary.get("releases") != dict(
        zip(("START", "END"), releases, strict=True)
    ):
        raise ValueError("Stage 2C preview release mismatch")
    if complete_summary.get("releases") != preview_summary.get("releases"):
        raise ValueError("Stage 2C completion release mismatch")
    skipped_file = completion / POLICY_SKIP
    policy_skipped_keys = frozenset()
    if skipped_file.exists() or skipped_file.is_symlink():
        if not skipped_file.is_file() or skipped_file.is_symlink():
            raise ValueError("Invalid policy skip publication file")
        if complete_summary.get("output_sha256", {}).get(POLICY_SKIP) != _sha(
            skipped_file
        ):
            raise ValueError("Policy skip publication SHA256 mismatch")
        policy_skipped_keys = _policy_skipped_keys(skipped_file, releases)
        if complete_summary.get("policy_skipped_nonexact_count") != len(
            policy_skipped_keys
        ):
            raise ValueError("Policy skip publication count mismatch")
    target_file = work / "common/01/01_targets.tsv"
    taxonomy_paths = {
        release: work / role / "02B/02B_target_taxonomy.tsv"
        for role, release in zip(("START", "END"), releases, strict=True)
    }
    anchor_paths = {
        release: work / role / "04B/04B_classified_direct_anchors.tsv"
        for role, release in zip(("START", "END"), releases, strict=True)
    }
    lineage_paths = {
        release: work / role / "04D2/04D2_lineage_evidence.tsv"
        for role, release in zip(("START", "END"), releases, strict=True)
    }
    if not args.execute:
        print("STAGE_2C_RESOLVER_DRY_RUN=PASS")
        print("EXECUTE_REQUIRED=--execute")
        return
    if args.output_dir.exists() or args.output_dir.is_symlink():
        raise FileExistsError("Stage 2C resolver output already exists")
    candidates = load_candidate_universe(
        targets_path=target_file,
        primary_path=inputs["primary"],
        audit_path=inputs["audit"],
        release_ids=frozenset(releases),
    )
    provenance = collect_candidate_provenance(
        candidates=candidates,
        target_taxonomy_paths=taxonomy_paths,
        classified_anchor_paths=anchor_paths,
        index_path=work / "common/04D1/04D1_lineage_scan_index.tsv",
        evidence_paths=lineage_paths,
    )
    alignment = collect_alignments(
        candidates=candidates,
        jobs_path=inputs["jobs"],
        alignments_path=inputs["alignments"],
        policy_skipped_keys=policy_skipped_keys,
    )
    contexts = _load_target_taxonomy(
        taxonomy_paths,
        {(release, target) for release, target, _ in candidates},
    )
    taxonomy = {
        key: (value.resolved_taxid, value.species_taxid)
        for key, value in contexts.items()
    }
    wanted = {
        key
        for key, fact in provenance.items()
        if (
            fact.direct.target_resolved_taxid is not None
            and fact.candidate.candidate_resolved_taxid is not None
            and key not in policy_skipped_keys
            and (
                (
                    fact.direct.target_resolved_taxid
                    == fact.candidate.candidate_resolved_taxid
                    and fact.candidate.scope == "PRIMARY"
                    and alignment[key].aligned
                    and alignment[key].exact_sequence is True
                )
                or (
                    args.strain_policy == "same_species_exact"
                    and fact.direct.target_resolved_taxid
                    != fact.candidate.candidate_resolved_taxid
                    and fact.candidate.candidate_species_taxid
                    == taxonomy[key[:2]][1]
                )
            )
        )
    }
    proof = _sequence_proofs(
        wanted=wanted,
        candidates=candidates,
        targets=target_file,
        stores={releases[0]: preview / START_STORE, releases[1]: preview / END_STORE},
    )
    result = resolve_stage2c_variant(
        provenance=provenance,
        alignments=alignment,
        taxonomy=taxonomy,
        sequences=proof,
        release_order=releases,
        strain_policy=args.strain_policy,
        partial_sequence=args.partial_sequence,
        documentary_fragment=args.documentary_fragment,
    )
    parent = args.output_dir.parent
    if not parent.is_dir() or parent.is_symlink():
        raise ValueError("Output parent must be an existing real directory")
    lock = parent / f".{args.output_dir.name}.lock"
    lock.mkdir()
    try:
        with tempfile.TemporaryDirectory(dir=parent, prefix=".stage2c07-") as tmp:
            product = Path(tmp) / "ready"
            product.mkdir()
            _write(
                product,
                result,
                {
                    "strain_policy": args.strain_policy,
                    "partial_sequence": args.partial_sequence,
                    "documentary_fragment": args.documentary_fragment,
                    "release_order": list(releases),
                    "input_sha256": {name: _sha(path) for name, path in inputs.items()},
                },
            )
            if args.output_dir.exists() or args.output_dir.is_symlink():
                raise FileExistsError("Output appeared during publication")
            os.rename(product, args.output_dir)
    finally:
        lock.rmdir()
    print("STAGE_2C_RESOLVER_VARIANT_PUBLISHED=PASS")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--work-root", type=Path, required=True)
    parser.add_argument("--preview", type=Path, required=True)
    parser.add_argument("--completion", type=Path, required=True)
    parser.add_argument("--start-release-id", required=True)
    parser.add_argument("--end-release-id", required=True)
    parser.add_argument(
        "--strain-policy",
        choices=("strict", "same_species_exact"),
        required=True,
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
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--execute", action="store_true")
    run(parser.parse_args())


if __name__ == "__main__":
    main()
