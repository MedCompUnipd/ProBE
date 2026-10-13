"""Validate Stage 07 provenance and publish an atomic production bundle."""

import csv
import json
import subprocess
import sys
from collections import Counter
from pathlib import Path

from probe.resolver import ResolverPolicy
from probe.resolver_assembly import STAGE07_COLUMNS
from probe.resolver_metadata import (
    SCHEMA_VERSION as SELECTION_METADATA_SCHEMA_VERSION,
)
from probe.resolver_metadata import (
    build_selection_metadata,
)
from probe.workflow.artifacts import atomic_stage_bundle, validate_bundle
from probe.workflow.outcome import write_stage_outcome
from probe.workflow.runtime import input_artifact, runtime_environment

context = globals()["snakemake"]

OUTPUT_NAME = "07_resolved_candidate_associations.tsv"
SUMMARY_NAME = "07_summary.json"
OUTCOME_NAME = "stage_outcome.json"
ACCEPTED_NAME = "07_accepted_associations.tsv"
METADATA_NAME = "07_selection_metadata.json"

INVALIDATED_PAYLOADS = frozenset(
    (OUTPUT_NAME, ACCEPTED_NAME, METADATA_NAME, SUMMARY_NAME, OUTCOME_NAME)
)

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

IMPLEMENTATION_MODULES = frozenset(
    {
        "resolver.py",
        "resolver_alignments.py",
        "resolver_anchors.py",
        "resolver_assembly.py",
        "resolver_candidates.py",
        "resolver_lineage.py",
        "resolver_metadata.py",
    }
)

# name, stage, role, root, bundle directory, required payload
INPUT_SPECS = (
    (
        "targets_manifest",
        "01",
        None,
        "work",
        "common/01",
        "01_targets.tsv",
    ),
    (
        "primary_manifest",
        "05A2",
        None,
        "results",
        "common/05A2",
        "05A2_final_candidate_union.tsv",
    ),
    (
        "audit_manifest",
        "05C2",
        None,
        "results",
        "common/05C2",
        "05C2_taxonomy_audit.tsv",
    ),
    (
        "start_taxonomy_manifest",
        "02B",
        "START",
        "work",
        "START/02B",
        "02B_target_taxonomy.tsv",
    ),
    (
        "end_taxonomy_manifest",
        "02B",
        "END",
        "work",
        "END/02B",
        "02B_target_taxonomy.tsv",
    ),
    (
        "start_anchors_manifest",
        "04B",
        "START",
        "work",
        "START/04B",
        "04B_classified_direct_anchors.tsv",
    ),
    (
        "end_anchors_manifest",
        "04B",
        "END",
        "work",
        "END/04B",
        "04B_classified_direct_anchors.tsv",
    ),
    (
        "lineage_index_manifest",
        "04D1",
        None,
        "work",
        "common/04D1",
        "04D1_lineage_scan_index.tsv",
    ),
    (
        "start_lineage_manifest",
        "04D2",
        "START",
        "work",
        "START/04D2",
        "04D2_lineage_evidence.tsv",
    ),
    (
        "end_lineage_manifest",
        "04D2",
        "END",
        "work",
        "END/04D2",
        "04D2_lineage_evidence.tsv",
    ),
    (
        "jobs_manifest",
        "06A",
        None,
        "work",
        "common/06A",
        "06A_alignment_jobs.tsv",
    ),
    (
        "alignments_manifest",
        "06C1",
        None,
        "results",
        "common/06C1",
        "06C1_canonical_directional_glocal.tsv",
    ),
)


def count(value: object, label: str) -> int:
    if type(value) is not int or value < 0:
        raise ValueError(f"07 {label}: expected nonnegative integer")
    return value


def require_summary_counts(summary: dict) -> dict[str, int]:
    total = count(summary.get("candidate_pair_count"), "candidate pairs")

    def distribution(name: str) -> dict[str, int]:
        values = summary.get(name)
        if not isinstance(values, dict):
            raise ValueError(f"07 summary missing {name}")
        result = {key: count(value, f"{name}.{key}") for key, value in values.items()}
        if sum(result.values()) != total:
            raise ValueError(f"07 {name}: count sum mismatch")
        return result

    decisions = distribution("decision_counts")
    donors = distribution("annotation_donor_counts")
    distribution("scope_counts")
    distribution("evaluation_scope_counts")
    distribution("decision_basis_counts")
    distribution("sequence_relation_counts")

    if set(decisions) - {"ACCEPTED", "UNRESOLVED", "AUDIT_ONLY"}:
        raise ValueError("07 unknown decision class")
    if set(donors) - {"YES", "NO"}:
        raise ValueError("07 unknown annotation donor class")

    accepted = decisions.get("ACCEPTED", 0)
    donor_yes = donors.get("YES", 0)

    if donor_yes > accepted:
        raise ValueError("07 donors exceed accepted candidates")

    return {
        "candidate_pairs": total,
        "accepted": accepted,
        "unresolved": decisions.get("UNRESOLVED", 0),
        "audit_only": decisions.get("AUDIT_ONLY", 0),
        "annotation_donors": donor_yes,
    }


def validate_serialized_tsv(path: Path, summary: dict) -> None:
    """Validate serialized associations against the summary before publication."""

    distribution_fields = {
        "decision": "decision_counts",
        "decision_basis": "decision_basis_counts",
        "scope": "scope_counts",
        "evaluation_scope": "evaluation_scope_counts",
        "annotation_donor": "annotation_donor_counts",
        "sequence_relation": "sequence_relation_counts",
    }

    distributions = {field: Counter() for field in distribution_fields}
    audit_outcomes = Counter()
    accepted_groups = Counter()
    seen = set()
    expected_releases = set(summary["releases"].values())

    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle, delimiter="\t")

        if tuple(reader.fieldnames or ()) != STAGE07_COLUMNS:
            raise ValueError("07 TSV schema mismatch")

        for line_number, row in enumerate(reader, start=2):
            if None in row or any(value is None for value in row.values()):
                raise ValueError(f"07 malformed TSV row at line {line_number}")

            key = (row["release"], row["target_id"], row["rid"])

            if any(value in {"", "."} for value in key):
                raise ValueError(f"07 empty candidate identity at line {line_number}")

            if key in seen:
                raise ValueError(f"07 duplicate TSV candidate identity: {key}")

            seen.add(key)

            if row["release"] not in expected_releases:
                raise ValueError(f"07 unexpected TSV release: {key}")

            decision = row["decision"]
            donor = row["annotation_donor"]

            if decision not in {"ACCEPTED", "UNRESOLVED", "AUDIT_ONLY"}:
                raise ValueError(f"07 invalid TSV decision: {key}")

            if donor not in {"YES", "NO"}:
                raise ValueError(f"07 invalid TSV donor status: {key}")

            if donor == "YES" and decision != "ACCEPTED":
                raise ValueError(f"07 non-accepted TSV candidate is donor: {key}")

            if row["scope"] not in {"PRIMARY", "TAXONOMY_AUDIT"}:
                raise ValueError(f"07 invalid TSV scope: {key}")

            if row["evaluation_scope"] not in {"PRIMARY", "TAXONOMY_AUDIT"}:
                raise ValueError(f"07 invalid TSV evaluation scope: {key}")

            if row["scope"] == "PRIMARY":
                if row["evaluation_scope"] != "PRIMARY":
                    raise ValueError(
                        f"07 primary TSV candidate has audit evaluation: {key}"
                    )
                if row["audit_outcome"] != ".":
                    raise ValueError(
                        f"07 primary TSV candidate has audit outcome: {key}"
                    )

            if decision == "AUDIT_ONLY":
                if row["scope"] != "TAXONOMY_AUDIT":
                    raise ValueError(
                        f"07 AUDIT_ONLY outside taxonomy audit scope: {key}"
                    )
                if row["accepted_candidates_for_target_release"] != ".":
                    raise ValueError(f"07 AUDIT_ONLY has accepted multiplicity: {key}")

            if row["scope"] == "TAXONOMY_AUDIT" and row["audit_outcome"] in {"", "."}:
                raise ValueError(f"07 taxonomy audit missing outcome: {key}")

            for field, counter in distributions.items():
                value = row[field]
                if not value or value == ".":
                    raise ValueError(f"07 missing TSV {field} at line {line_number}")
                counter[value] += 1

            audit_outcome = row["audit_outcome"]
            if audit_outcome not in {"", "."}:
                audit_outcomes[audit_outcome] += 1

            if decision == "ACCEPTED":
                accepted_groups[(row["release"], row["target_id"])] += 1

    total = count(summary.get("candidate_pair_count"), "candidate pairs")

    if len(seen) != total:
        raise ValueError(
            f"07 TSV candidate count mismatch: TSV={len(seen)} summary={total}"
        )

    for field, summary_name in distribution_fields.items():
        expected = summary.get(summary_name)
        observed = dict(distributions[field])

        if not isinstance(expected, dict) or observed != expected:
            raise ValueError(
                f"07 TSV {summary_name} mismatch: "
                f"observed={observed} expected={expected}"
            )

    if dict(audit_outcomes) != summary.get("audit_outcome_counts"):
        raise ValueError("07 TSV audit_outcome_counts mismatch")

    multiple_groups = sum(multiplicity > 1 for multiplicity in accepted_groups.values())
    maximum_multiplicity = max(accepted_groups.values(), default=0)

    if multiple_groups != count(
        summary.get("multiple_accepted_groups"),
        "multiple accepted groups",
    ):
        raise ValueError("07 TSV multiple accepted groups mismatch")

    if maximum_multiplicity != count(
        summary.get("maximum_accepted_multiplicity"),
        "maximum accepted multiplicity",
    ):
        raise ValueError("07 TSV maximum accepted multiplicity mismatch")

    # Verify per-row multiplicity against the complete ACCEPTED universe.
    # AUDIT_ONLY has no numeric multiplicity by contract and was already
    # checked during the first pass.
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle, delimiter="\t")

        if tuple(reader.fieldnames or ()) != STAGE07_COLUMNS:
            raise ValueError("07 TSV schema mismatch on second pass")

        for row in reader:
            if row["decision"] == "AUDIT_ONLY":
                continue

            group = (row["release"], row["target_id"])
            expected = accepted_groups.get(group, 0)
            observed = row["accepted_candidates_for_target_release"]

            if observed != str(expected):
                raise ValueError(
                    "07 TSV accepted multiplicity mismatch: "
                    f"group={group} rid={row['rid']} "
                    f"expected={expected} observed={observed!r}"
                )


def validate_accepted_projection(
    complete_path: Path, accepted_path: Path, *, expected_count: int
) -> None:
    """Prove that every ACCEPTED row is projected exactly once and in order."""
    count(expected_count, "accepted associations")
    observed_count = 0

    with (
        complete_path.open("r", encoding="utf-8", newline="") as complete_handle,
        accepted_path.open("r", encoding="utf-8", newline="") as accepted_handle,
    ):
        complete = csv.DictReader(complete_handle, delimiter="\t")
        accepted = csv.DictReader(accepted_handle, delimiter="\t")

        if tuple(complete.fieldnames or ()) != STAGE07_COLUMNS:
            raise ValueError("07 complete TSV schema mismatch during projection check")
        if tuple(accepted.fieldnames or ()) != ACCEPTED_COLUMNS:
            raise ValueError("07 accepted projection TSV schema mismatch")

        for row in complete:
            if row["decision"] != "ACCEPTED":
                continue
            observed_count += 1
            actual = next(accepted, None)
            if actual is None:
                raise ValueError("07 accepted projection is missing an ACCEPTED row")
            if None in actual or any(value is None for value in actual.values()):
                raise ValueError("07 accepted projection contains a malformed TSV row")
            expected = {column: row[column] for column in ACCEPTED_COLUMNS}
            if actual != expected:
                key = (row["release"], row["target_id"], row["rid"])
                raise ValueError(f"07 accepted projection row mismatch: {key}")

        if next(accepted, None) is not None:
            raise ValueError("07 accepted projection contains unexpected extra rows")

    if observed_count != expected_count:
        raise ValueError(
            "07 accepted projection count mismatch: "
            f"observed={observed_count} expected={expected_count}"
        )


def validate_selection_metadata(
    path: Path, summary: dict, policy: ResolverPolicy
) -> None:
    """Verify the serialized science description against recorded policies."""
    raw = path.read_text(encoding="utf-8")
    metadata = json.loads(raw)
    if not isinstance(metadata, dict):
        raise ValueError("07 selection metadata must be a JSON object")
    if metadata.get("schema_version") != SELECTION_METADATA_SCHEMA_VERSION:
        raise ValueError("07 selection metadata schema version mismatch")
    if metadata.get("policy_version") != summary["policy_version"]:
        raise ValueError("07 selection metadata policy version mismatch")
    if metadata.get("releases") != summary["releases"]:
        raise ValueError("07 selection metadata release mismatch")
    if metadata.get("effective_policies") != summary["policies"]:
        raise ValueError("07 selection metadata effective policy mismatch")

    expected = build_selection_metadata(
        start_release_id=summary["releases"]["START"],
        end_release_id=summary["releases"]["END"],
        policy=policy,
        policy_version=summary["policy_version"],
    )
    if metadata != expected:
        raise ValueError("07 selection metadata scientific contract mismatch")
    serialized = json.dumps(metadata, indent=2, sort_keys=True, allow_nan=False) + "\n"
    if raw != serialized:
        raise ValueError("07 selection metadata serialization mismatch")


def check_publication_destination(destination: Path) -> None:
    """Allow only safe reconstruction of an invalidated Stage 07 bundle."""
    if destination.is_symlink():
        raise FileExistsError("07 publication destination is a symlink")

    if not destination.exists():
        return

    if not destination.is_dir():
        raise FileExistsError("07 publication destination is not a directory")

    manifest = destination / "manifest.json"
    if manifest.exists() or manifest.is_symlink():
        raise FileExistsError("07 completed destination cannot be replaced")

    for entry in destination.iterdir():
        if (
            entry.name not in INVALIDATED_PAYLOADS
            or entry.is_symlink()
            or not entry.is_file()
        ):
            raise FileExistsError(
                f"07 invalidated destination contains unexpected entry: {entry}"
            )


def run() -> None:
    work_root = Path(context.params.work_root).resolve()
    results_root = Path(context.params.results_root).resolve()

    roots = {"work": work_root, "results": results_root}

    start_id = context.params.start_release_id
    end_id = context.params.end_release_id

    if (
        not isinstance(start_id, str)
        or not start_id
        or not isinstance(end_id, str)
        or not end_id
        or start_id == end_id
    ):
        raise ValueError("07 requires distinct concrete release IDs")

    policy_data = context.params.stage07_policy
    policy = ResolverPolicy(
        subtaxon_policy=policy_data["resolver"]["subtaxon_policy"],
        partial_sequence=policy_data["annotation_donor"]["partial_sequence"],
        documentary_fragment=(policy_data["annotation_donor"]["documentary_fragment"]),
    )

    publication_root = Path(context.params.publication_root)
    if publication_root.is_symlink() or not publication_root.is_dir():
        raise ValueError("07 publication root must be a physical directory")

    destination = Path(context.output[0]).parent
    variant_spec = getattr(context.params, "resolver_variant_spec", None)
    if variant_spec is None:
        expected_destination = publication_root.resolve() / "common/07"
    else:
        from probe.stage07_variants import read_spec

        variant = read_spec(Path(variant_spec))
        if variant["policy"] != policy_data:
            raise ValueError("07 variant policy differs from resolved spec")
        expected_destination = Path(variant["paths"]["stage07"])
        if publication_root.resolve() not in expected_destination.parents:
            raise ValueError("07 variant publication escaped results root")

    if destination.resolve() != expected_destination.resolve():
        raise ValueError("07 unexpected publication destination")
    if (
        variant_spec is not None
        and destination.exists()
        and (destination.is_symlink() or any(destination.iterdir()))
    ):
        raise FileExistsError("07 variant refuses nonempty destination")
    check_publication_destination(destination)

    validated = {}
    input_artifacts = []

    for (
        label,
        expected_stage,
        role,
        root_name,
        relative_dir,
        filename,
    ) in INPUT_SPECS:
        manifest_path = Path(getattr(context.input, label))
        expected_path = roots[root_name] / relative_dir / "manifest.json"

        if manifest_path.resolve() != expected_path.resolve():
            raise ValueError(f"07 unexpected upstream path: {label}")

        manifest = validate_bundle(manifest_path.parent, deep=False)

        if manifest.stage != expected_stage:
            raise ValueError(f"07 wrong upstream stage: {label}")

        if manifest.role != role:
            raise ValueError(f"07 wrong upstream role: {label}")

        expected_release = (
            start_id if role == "START" else end_id if role == "END" else None
        )
        if manifest.release_id != expected_release:
            raise ValueError(f"07 wrong upstream release: {label}")

        declared = {artifact.path for artifact in manifest.outputs}
        if filename not in declared:
            raise ValueError(f"07 {label} does not declare {filename}")

        declared_artifact = next(
            item for item in manifest.outputs if item.path == filename
        )

        if not declared_artifact.sha256:
            raise ValueError(f"07 {label}: upstream SHA256 checksum missing")

        payload_artifact = input_artifact(manifest_path.parent / filename)

        if payload_artifact.sha256 != declared_artifact.sha256:
            raise ValueError(f"07 {label}: upstream SHA256 checksum mismatch")

        validated[label] = manifest
        input_artifacts.extend((input_artifact(manifest_path), payload_artifact))

    alignment_manifest = validated["jobs_manifest"]
    canonical_manifest = validated["alignments_manifest"]

    for label, manifest in (
        ("06A", alignment_manifest),
        ("06C1", canonical_manifest),
    ):
        params = manifest.parameters
        if (
            params.get("start_release_id") != start_id
            or params.get("end_release_id") != end_id
        ):
            raise ValueError(f"07 {label} release provenance mismatch")

    if alignment_manifest.ontology != canonical_manifest.ontology:
        raise ValueError("07 alignment ontology provenance mismatch")

    cli_script = Path(context.input.implementation_script).resolve()
    runner_script = Path(context.input.runner_script).resolve()
    project_root = cli_script.parents[2]

    expected_cli = (
        project_root / "scripts/pipeline/07_resolve_candidate_associations.py"
    )
    expected_runner = project_root / "workflow/scripts/run_stage_07.py"

    if cli_script != expected_cli.resolve():
        raise ValueError("07 unexpected CLI implementation path")

    if runner_script != expected_runner.resolve():
        raise ValueError("07 unexpected runner implementation path")

    module_paths = [Path(path).resolve() for path in context.input.resolver_modules]
    expected_modules = {
        (project_root / "src/probe" / name).resolve() for name in IMPLEMENTATION_MODULES
    }

    if set(module_paths) != expected_modules:
        raise ValueError("07 scientific-module provenance mismatch")
    if len(module_paths) != len(expected_modules):
        raise ValueError("07 duplicate scientific module")

    source_paths = [
        cli_script,
        runner_script,
        *sorted(module_paths),
    ]

    source_artifacts = [input_artifact(path) for path in source_paths]
    inputs = tuple(input_artifacts) + tuple(source_artifacts)

    implementation = {
        "name": "07_resolve_candidate_associations",
        "version": "stage07-resolver-v1",
        "language": "Python",
        "source_sha256": {
            artifact.path: artifact.sha256 for artifact in source_artifacts
        },
    }

    command = [
        sys.executable,
        str(cli_script),
        "--work-root",
        str(work_root),
        "--results-root",
        str(results_root),
        "--start-release-id",
        start_id,
        "--end-release-id",
        end_id,
        "--subtaxon-policy",
        policy.subtaxon_policy,
        "--partial-sequence",
        policy.partial_sequence,
        "--documentary-fragment",
        policy.documentary_fragment,
    ]

    parameters = {
        "start_release_id": start_id,
        "end_release_id": end_id,
        "policy_version": "stage07-resolver-v1",
        "policies": policy_data,
        "command": [
            *command,
            "--output",
            f"<stage-bundle>/{OUTPUT_NAME}",
            "--summary",
            f"<stage-bundle>/{SUMMARY_NAME}",
            "--accepted-output",
            f"<stage-bundle>/{ACCEPTED_NAME}",
            "--selection-metadata",
            f"<stage-bundle>/{METADATA_NAME}",
        ],
    }

    log_path = Path(context.log[0])
    log_path.parent.mkdir(parents=True, exist_ok=True)

    expected_files = {
        OUTPUT_NAME,
        ACCEPTED_NAME,
        METADATA_NAME,
        SUMMARY_NAME,
        OUTCOME_NAME,
    }

    with (
        log_path.open("w", encoding="utf-8") as log,
        atomic_stage_bundle(
            destination,
            stage="07",
            role=None,
            release_id=None,
            parameters=parameters,
            ontology=canonical_manifest.ontology,
            implementation=implementation,
            inputs=inputs,
            environment=runtime_environment(),
            allow_invalidated_destination=True,
        ) as staging,
    ):
        output = staging / OUTPUT_NAME
        summary_path = staging / SUMMARY_NAME
        accepted_path = staging / ACCEPTED_NAME
        metadata_path = staging / METADATA_NAME

        subprocess.run(
            [
                *command,
                "--output",
                str(output),
                "--summary",
                str(summary_path),
                "--accepted-output",
                str(accepted_path),
                "--selection-metadata",
                str(metadata_path),
            ],
            stdout=log,
            stderr=subprocess.STDOUT,
            check=True,
        )

        summary = json.loads(summary_path.read_text(encoding="utf-8"))

        if summary.get("stage") != "07":
            raise ValueError("07 summary stage mismatch")

        if summary.get("policy_version") != "stage07-resolver-v1":
            raise ValueError("07 summary policy version mismatch")

        if summary.get("releases") != {
            "START": start_id,
            "END": end_id,
        }:
            raise ValueError("07 summary release mismatch")

        if summary.get("policies") != policy_data:
            raise ValueError("07 summary policy mismatch")

        observed = require_summary_counts(summary)
        validate_serialized_tsv(output, summary)
        validate_accepted_projection(
            output, accepted_path, expected_count=observed["accepted"]
        )
        validate_selection_metadata(metadata_path, summary, policy)

        write_stage_outcome(
            staging / OUTCOME_NAME,
            state=("COMPLETED" if observed["candidate_pairs"] > 0 else "EMPTY_RESULT"),
            reason_code=(
                "RESOLVER_ASSOCIATIONS_CLASSIFIED"
                if observed["candidate_pairs"] > 0
                else "NO_CANDIDATE_PAIRS"
            ),
            message=(
                "Stage 07 classified the complete upstream "
                "candidate universe under the recorded resolver policy."
            ),
            counts=observed,
        )

        produced = {
            path.relative_to(staging).as_posix()
            for path in staging.rglob("*")
            if path.is_file()
        }

        if produced != expected_files:
            raise RuntimeError(
                "07 output contract violation: "
                f"missing={sorted(expected_files - produced)} "
                f"unexpected={sorted(produced - expected_files)}"
            )


run()
