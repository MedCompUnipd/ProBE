import csv
import subprocess
from collections import Counter
from pathlib import Path

from probe.workflow.artifacts import atomic_stage_bundle, validate_bundle
from probe.workflow.outcome import write_stage_outcome
from probe.workflow.runtime import input_artifact, runtime_environment

context = globals()["snakemake"]

role = str(context.wildcards.role)
if role not in {"START", "END"}:
    raise ValueError(f"05D unknown role {role}")

final_union_manifest_path = Path(context.input.final_union_manifest)
taxonomy_audit_manifest_path = Path(context.input.taxonomy_audit_manifest)
full_v2_manifest_path = Path(context.input.full_v2_manifest)
binary = Path(context.input.binary)

destination = Path(context.output[0]).parent


STORE_HEADER = [
    "release",
    "full_v2_record_number",
    "rid",
    "primary_accession",
    "entry",
    "raw_ox",
    "frag",
    "sequence_length",
    "pe",
    "sv",
    "sec",
    "gene",
    "needed_by_primary",
    "needed_by_taxonomy_audit",
    "primary_reference_count",
    "audit_reference_count",
    "sequence",
]


def load_manifest(path: Path):
    return validate_bundle(path.parent, deep=False)


def canonical_rid(value: str, label: str) -> str:
    try:
        parsed = int(value)
    except ValueError as exc:
        raise ValueError(f"{label}: invalid RID {value!r}") from exc

    if parsed <= 0 or str(parsed) != value:
        raise ValueError(f"{label}: non-canonical RID {value!r}")

    return value


def load_primary_requests(
    path: Path,
    release_id: str,
    allowed_releases: set[str],
) -> Counter[str]:
    required = {
        "release",
        "rid",
        "candidate_primary_accession",
        "entry",
        "raw_ox",
        "frag",
        "sequence_length",
        "pe",
        "sv",
        "sec",
        "gene",
    }

    counts: Counter[str] = Counter()

    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle, delimiter="\t")

        if reader.fieldnames is None or not required.issubset(reader.fieldnames):
            missing = required.difference(reader.fieldnames or ())
            raise ValueError(f"{path}: missing columns {sorted(missing)}")

        for row in reader:
            release = row["release"]
            if release not in allowed_releases:
                raise ValueError(f"{path}: unexpected release {release!r}")

            if release != release_id:
                continue

            rid = canonical_rid(
                row["rid"],
                f"{path}:{release}",
            )
            counts[rid] += 1

    return counts


def load_audit_requests(
    path: Path,
    release_id: str,
    allowed_releases: set[str],
) -> Counter[str]:
    required = {
        "release",
        "audit_alignment_allowed",
        "rid",
        "candidate_primary_accession",
        "entry",
        "raw_ox",
        "frag",
        "sequence_length",
        "pe",
        "sv",
        "sec",
        "gene",
    }

    counts: Counter[str] = Counter()

    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle, delimiter="\t")

        if reader.fieldnames is None or not required.issubset(reader.fieldnames):
            missing = required.difference(reader.fieldnames or ())
            raise ValueError(f"{path}: missing columns {sorted(missing)}")

        for row in reader:
            release = row["release"]
            if release not in allowed_releases:
                raise ValueError(f"{path}: unexpected release {release!r}")

            allowed = row["audit_alignment_allowed"]
            if allowed not in {"0", "1"}:
                raise ValueError(f"{path}: invalid audit_alignment_allowed {allowed!r}")

            if release != release_id or allowed != "1":
                continue

            rid = canonical_rid(
                row["rid"],
                f"{path}:{release}",
            )
            counts[rid] += 1

    return counts


def write_empty_store(path: Path) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(
            handle,
            delimiter="\t",
            lineterminator="\n",
        )
        writer.writerow(STORE_HEADER)


def write_empty_summary(
    path: Path,
    *,
    release_id: str,
    primary_reference_rows: int,
    audit_reference_rows: int,
) -> None:
    metrics = [
        ("release", release_id),
        ("primary_reference_rows", primary_reference_rows),
        ("taxonomy_audit_reference_rows", audit_reference_rows),
        ("requested_unique_rids", 0),
        ("primary_unique_rids", 0),
        ("taxonomy_audit_unique_rids", 0),
        ("primary_audit_unique_rid_overlap", 0),
        ("max_requested_rid", 0),
        ("full_v2_records_scanned", 0),
        ("found_unique_rids", 0),
        ("missing_requested_rids", 0),
        ("metadata_conflicts", 0),
        ("sequence_length_mismatches", 0),
        ("total_candidate_sequence_residues", 0),
        ("max_candidate_sequence_length", 0),
    ]

    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(
            handle,
            delimiter="\t",
            lineterminator="\n",
        )
        writer.writerow(("metric", "value"))
        writer.writerows(metrics)


def read_summary(path: Path) -> dict[str, str]:
    result: dict[str, str] = {}

    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle, delimiter="\t")

        if reader.fieldnames != ["metric", "value"]:
            raise ValueError(f"{path}: invalid summary schema")

        for row in reader:
            key = row["metric"]
            if key in result:
                raise ValueError(f"{path}: duplicate metric {key}")
            result[key] = row["value"]

    return result


def summary_int(
    summary: dict[str, str],
    name: str,
) -> int:
    if name not in summary:
        raise ValueError(f"05D summary missing {name}")

    try:
        value = int(summary[name])
    except ValueError as exc:
        raise ValueError(f"05D summary has invalid {name}={summary[name]!r}") from exc

    if value < 0:
        raise ValueError(f"05D summary has negative {name}")

    return value


def validate_store(
    path: Path,
    *,
    release_id: str,
    requested_rids: set[str],
    primary_counts: Counter[str],
    audit_counts: Counter[str],
) -> None:
    observed: set[str] = set()

    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle, delimiter="\t")

        if reader.fieldnames != STORE_HEADER:
            raise ValueError(f"{path}: invalid 05D store schema")

        for row in reader:
            if row["release"] != release_id:
                raise ValueError(f"{path}: unexpected release {row['release']!r}")

            rid = canonical_rid(
                row["rid"],
                f"{path}:{release_id}",
            )

            if rid in observed:
                raise ValueError(f"{path}: duplicate RID {rid}")

            if row["full_v2_record_number"] != rid:
                raise ValueError(f"{path}: full_v2_record_number != rid for RID {rid}")

            if row["needed_by_primary"] not in {"0", "1"}:
                raise ValueError(f"{path}: invalid needed_by_primary for RID {rid}")

            if row["needed_by_taxonomy_audit"] not in {"0", "1"}:
                raise ValueError(
                    f"{path}: invalid needed_by_taxonomy_audit for RID {rid}"
                )

            expected_primary = int(rid in primary_counts)
            expected_audit = int(rid in audit_counts)

            if int(row["needed_by_primary"]) != expected_primary:
                raise RuntimeError(f"05D RID {rid} primary provenance mismatch")

            if int(row["needed_by_taxonomy_audit"]) != expected_audit:
                raise RuntimeError(f"05D RID {rid} taxonomy-audit provenance mismatch")

            if int(row["primary_reference_count"]) != primary_counts[rid]:
                raise RuntimeError(f"05D RID {rid} primary reference-count mismatch")

            if int(row["audit_reference_count"]) != audit_counts[rid]:
                raise RuntimeError(f"05D RID {rid} audit reference-count mismatch")

            try:
                declared_length = int(row["sequence_length"])
            except ValueError as exc:
                raise ValueError(
                    f"{path}: invalid sequence_length for RID {rid}"
                ) from exc

            if declared_length != len(row["sequence"]):
                raise RuntimeError(f"05D RID {rid} physical sequence-length mismatch")

            observed.add(rid)

    if observed != requested_rids:
        raise RuntimeError(
            "05D sequence-store RID set mismatch: "
            f"missing={sorted(requested_rids - observed)[:10]} "
            f"unexpected={sorted(observed - requested_rids)[:10]}"
        )


final_union_manifest = load_manifest(final_union_manifest_path)
taxonomy_audit_manifest = load_manifest(taxonomy_audit_manifest_path)
full_v2_manifest = load_manifest(full_v2_manifest_path)

if final_union_manifest.stage != "05A2":
    raise ValueError(
        f"05D final union expected stage 05A2, got {final_union_manifest.stage}"
    )

if final_union_manifest.role is not None or final_union_manifest.release_id is not None:
    raise ValueError("05D Stage 05A2 input must be a common bundle")

if taxonomy_audit_manifest.stage != "05C2":
    raise ValueError(
        f"05D taxonomy audit expected stage 05C2, got {taxonomy_audit_manifest.stage}"
    )

if (
    taxonomy_audit_manifest.role is not None
    or taxonomy_audit_manifest.release_id is not None
):
    raise ValueError("05D Stage 05C2 input must be a common bundle")

if full_v2_manifest.stage != "01A_FULL":
    raise ValueError(
        f"05D full V2 expected stage 01A_FULL, got {full_v2_manifest.stage}"
    )

if full_v2_manifest.role != role:
    raise ValueError(f"05D full V2 expected role {role}, got {full_v2_manifest.role}")

if full_v2_manifest.release_id is None:
    raise ValueError("05D Stage 01A_FULL input requires a release_id")

release_id = full_v2_manifest.release_id

union_parameters = dict(final_union_manifest.parameters)
audit_parameters = dict(taxonomy_audit_manifest.parameters)

start_release_id = union_parameters.get("start_release_id")
end_release_id = union_parameters.get("end_release_id")

if not isinstance(start_release_id, str) or not start_release_id:
    raise ValueError("05D Stage 05A2 missing start_release_id")

if not isinstance(end_release_id, str) or not end_release_id:
    raise ValueError("05D Stage 05A2 missing end_release_id")

if start_release_id == end_release_id:
    raise ValueError("05D START and END release IDs must be distinct")

if audit_parameters.get("start_release_id") != start_release_id:
    raise ValueError("05D 05A2/05C2 START release mismatch")

if audit_parameters.get("end_release_id") != end_release_id:
    raise ValueError("05D 05A2/05C2 END release mismatch")

expected_release_id = {
    "START": start_release_id,
    "END": end_release_id,
}[role]

if release_id != expected_release_id:
    raise ValueError(
        f"05D {role} release mismatch: "
        f"01A_FULL={release_id} final-union={expected_release_id}"
    )

union_name = "05A2_final_candidate_union.tsv"
audit_name = "05C2_taxonomy_audit.tsv"
fasta_name = "01A_full_v2.fasta"

for label, manifest, filename in (
    ("05A2", final_union_manifest, union_name),
    ("05C2", taxonomy_audit_manifest, audit_name),
    ("01A_FULL", full_v2_manifest, fasta_name),
):
    declared = {item.path for item in manifest.outputs}
    if filename not in declared:
        raise ValueError(f"{label} bundle does not declare {filename}")

candidate_union = final_union_manifest_path.parent / union_name
taxonomy_audit = taxonomy_audit_manifest_path.parent / audit_name
fasta = full_v2_manifest_path.parent / fasta_name

allowed_releases = {
    start_release_id,
    end_release_id,
}

primary_counts = load_primary_requests(
    candidate_union,
    release_id,
    allowed_releases,
)

audit_counts = load_audit_requests(
    taxonomy_audit,
    release_id,
    allowed_releases,
)

primary_reference_rows = sum(primary_counts.values())
audit_reference_rows = sum(audit_counts.values())

primary_rids = set(primary_counts)
audit_rids = set(audit_counts)
requested_rids = primary_rids | audit_rids

output_name = "05D_candidate_sequences.tsv"
summary_name = "05D_summary.tsv"
outcome_name = "stage_outcome.json"

binary_artifact = input_artifact(binary)

recorded_command = [
    str(binary.resolve()),
    "--release",
    release_id,
    "--candidate-union",
    str(candidate_union.resolve()),
    "--taxonomy-audit",
    str(taxonomy_audit.resolve()),
    "--fasta",
    str(fasta.resolve()),
    "--output",
    f"<stage-bundle>/{output_name}",
    "--summary",
    f"<stage-bundle>/{summary_name}",
]

parameters = {
    "start_release_id": start_release_id,
    "end_release_id": end_release_id,
    "release_id": release_id,
    "command": recorded_command,
    "command_executed": bool(requested_rids),
}

implementation = {
    "name": "probe-candidate-sequence-extract-v1",
    "version": "1",
    "language": "C++20",
    "binary_sha256": binary_artifact.sha256,
    "build_task": "build-candidate-sequence-extract",
}

inputs = (
    input_artifact(final_union_manifest_path),
    input_artifact(taxonomy_audit_manifest_path),
    input_artifact(full_v2_manifest_path),
    binary_artifact,
)

expected_outputs = {
    output_name,
    summary_name,
    outcome_name,
}

log_path = Path(context.log[0])
log_path.parent.mkdir(parents=True, exist_ok=True)

with (
    log_path.open("w", encoding="utf-8") as log,
    atomic_stage_bundle(
        destination,
        stage="05D",
        role=role,
        release_id=release_id,
        parameters=parameters,
        ontology=None,
        implementation=implementation,
        inputs=inputs,
        environment=runtime_environment(),
        allow_invalidated_destination=True,
    ) as staging,
):
    output_path = staging / output_name
    summary_path = staging / summary_name
    outcome_path = staging / outcome_name

    outcome_counts = {
        "primary_reference_rows": primary_reference_rows,
        "taxonomy_audit_reference_rows": audit_reference_rows,
        "requested_unique_rids": len(requested_rids),
        "found_unique_rids": 0,
    }

    if not requested_rids:
        write_empty_store(output_path)
        write_empty_summary(
            summary_path,
            release_id=release_id,
            primary_reference_rows=primary_reference_rows,
            audit_reference_rows=audit_reference_rows,
        )

        write_stage_outcome(
            outcome_path,
            state="SKIPPED_EMPTY_INPUT",
            reason_code="NO_CANDIDATE_SEQUENCES_REQUESTED",
            message=(
                "Stage 05D received no primary or alignment-permitted "
                "taxonomy-audit candidate RIDs for this release. "
                "The FULL-V2 materializer was not required."
            ),
            counts=outcome_counts,
        )

        log.write(f"05D {role}: no requested candidate RIDs; materializer skipped\n")

    else:
        command = [
            str(binary),
            "--release",
            release_id,
            "--candidate-union",
            str(candidate_union),
            "--taxonomy-audit",
            str(taxonomy_audit),
            "--fasta",
            str(fasta),
            "--output",
            str(output_path),
            "--summary",
            str(summary_path),
        ]

        subprocess.run(
            command,
            stdout=log,
            stderr=subprocess.STDOUT,
            check=True,
        )

        summary = read_summary(summary_path)

        if summary.get("release") != release_id:
            raise RuntimeError(
                "05D summary release mismatch: "
                f"expected={release_id} "
                f"observed={summary.get('release')!r}"
            )

        expected_metrics = {
            "primary_reference_rows": primary_reference_rows,
            "taxonomy_audit_reference_rows": audit_reference_rows,
            "requested_unique_rids": len(requested_rids),
            "primary_unique_rids": len(primary_rids),
            "taxonomy_audit_unique_rids": len(audit_rids),
            "primary_audit_unique_rid_overlap": len(primary_rids & audit_rids),
            "max_requested_rid": max(int(rid) for rid in requested_rids),
            "found_unique_rids": len(requested_rids),
            "missing_requested_rids": 0,
            "metadata_conflicts": 0,
            "sequence_length_mismatches": 0,
        }

        for name, expected in expected_metrics.items():
            observed = summary_int(summary, name)
            if observed != expected:
                raise RuntimeError(
                    f"05D summary mismatch for {name}: "
                    f"expected={expected} observed={observed}"
                )

        validate_store(
            output_path,
            release_id=release_id,
            requested_rids=requested_rids,
            primary_counts=primary_counts,
            audit_counts=audit_counts,
        )

        outcome_counts["found_unique_rids"] = len(requested_rids)

        write_stage_outcome(
            outcome_path,
            state="COMPLETED",
            reason_code="CANDIDATE_SEQUENCES_MATERIALIZED",
            message=(
                "Stage 05D materialized every requested primary and "
                "alignment-permitted taxonomy-audit candidate RID "
                "from the FULL V2 release."
            ),
            counts=outcome_counts,
        )

    produced = {
        path.relative_to(staging).as_posix()
        for path in staging.rglob("*")
        if path.is_file()
    }

    if produced != expected_outputs:
        raise RuntimeError(
            "05D output contract violation: "
            f"missing={sorted(expected_outputs - produced)} "
            f"unexpected={sorted(produced - expected_outputs)}"
        )
