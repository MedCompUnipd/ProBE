"""Stage 07 production runner publication and failure-safety tests."""

from __future__ import annotations

import csv
import json
import runpy
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from probe.resolver import ResolverPolicy
from probe.resolver_assembly import STAGE07_COLUMNS
from probe.resolver_metadata import build_selection_metadata
from probe.workflow.artifacts import (
    Artifact,
    StageManifest,
    sha256_file,
    validate_bundle,
)

ROOT = Path(__file__).resolve().parents[1]
RUNNER = ROOT / "workflow/scripts/run_stage_07.py"
CLI = ROOT / "scripts/pipeline/07_resolve_candidate_associations.py"

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

SPECS = (
    ("targets_manifest", "01", None, "work", "common/01", "01_targets.tsv"),
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
    ("jobs_manifest", "06A", None, "work", "common/06A", "06A_alignment_jobs.tsv"),
    (
        "alignments_manifest",
        "06C1",
        None,
        "results",
        "common/06C1",
        "06C1_canonical_directional_glocal.tsv",
    ),
)

DEFAULT_POLICY = {
    "resolver": {"subtaxon_policy": "strict"},
    "annotation_donor": {
        "partial_sequence": "allow",
        "documentary_fragment": "allow",
    },
}


def write_fixture_bundle(path, stage, role, filename):
    path.mkdir(parents=True)
    payload = path / filename
    payload.write_text("synthetic evidence\n", encoding="utf-8")

    release = {
        "START": "2023_01",
        "END": "2026_03",
    }.get(role)

    parameters = {}
    if stage in {"06A", "06C1"}:
        parameters = {
            "start_release_id": "2023_01",
            "end_release_id": "2026_03",
        }

    StageManifest(
        stage=stage,
        role=role,
        release_id=release,
        parameters=parameters,
        ontology=None,
        implementation={"name": "synthetic_fixture", "version": "1"},
        inputs=(),
        outputs=(
            Artifact(
                path=filename,
                size_bytes=payload.stat().st_size,
                sha256=sha256_file(payload),
            ),
        ),
    ).write(path / "manifest.json")

    return path / "manifest.json"


def write_cli_outputs(command, policy, *, invalid=False):
    output = Path(command[command.index("--output") + 1])
    summary_path = Path(command[command.index("--summary") + 1])

    rows = []
    for rid, decision, donor in (
        ("1", "ACCEPTED", "YES"),
        ("2", "UNRESOLVED", "NO"),
    ):
        row = dict.fromkeys(STAGE07_COLUMNS, ".")
        row.update(
            {
                "release": "2023_01",
                "target_id": "T1",
                "rid": rid,
                "scope": "PRIMARY",
                "evaluation_scope": "PRIMARY",
                "decision": decision,
                "decision_basis": (
                    "DIRECT_PRIMARY_ACCESSION"
                    if decision == "ACCEPTED"
                    else "SEQUENCE_ONLY"
                ),
                "annotation_donor": donor,
                "sequence_relation": (
                    "EXACT_SEQUENCE"
                    if decision == "ACCEPTED"
                    else "NONEXACT_SEQUENCE_ONLY"
                ),
                "accepted_candidates_for_target_release": "1",
            }
        )
        rows.append(row)

    with output.open("x", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=STAGE07_COLUMNS,
            delimiter="\t",
            lineterminator="\n",
        )
        writer.writeheader()
        writer.writerows(rows)

    summary = {
        "stage": "07",
        "policy_version": "stage07-resolver-v1",
        "releases": {"START": "2023_01", "END": "2026_03"},
        "policies": policy,
        "candidate_pair_count": 2,
        "decision_counts": {"ACCEPTED": 1, "UNRESOLVED": 1},
        "annotation_donor_counts": {"YES": 1, "NO": 1},
        "scope_counts": {"PRIMARY": 2},
        "evaluation_scope_counts": {"PRIMARY": 2},
        "decision_basis_counts": {
            "DIRECT_PRIMARY_ACCESSION": 1,
            "SEQUENCE_ONLY": 1,
        },
        "sequence_relation_counts": {
            "EXACT_SEQUENCE": 1,
            "NONEXACT_SEQUENCE_ONLY": 1,
        },
        "audit_outcome_counts": {},
        "multiple_accepted_groups": 0,
        "maximum_accepted_multiplicity": 1,
    }

    if invalid:
        summary["annotation_donor_counts"]["YES"] = 3

    summary_path.write_text(json.dumps(summary) + "\n", encoding="utf-8")

    accepted_path = Path(command[command.index("--accepted-output") + 1])
    with accepted_path.open("x", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=ACCEPTED_COLUMNS,
            delimiter="\t",
            lineterminator="\n",
        )
        writer.writeheader()
        writer.writerows(
            {column: row[column] for column in ACCEPTED_COLUMNS}
            for row in rows
            if row["decision"] == "ACCEPTED"
        )

    metadata_path = Path(command[command.index("--selection-metadata") + 1])
    resolver_policy = ResolverPolicy(
        subtaxon_policy=policy["resolver"]["subtaxon_policy"],
        partial_sequence=policy["annotation_donor"]["partial_sequence"],
        documentary_fragment=policy["annotation_donor"]["documentary_fragment"],
    )
    metadata = build_selection_metadata(
        start_release_id=summary["releases"]["START"],
        end_release_id=summary["releases"]["END"],
        policy=resolver_policy,
        policy_version=summary["policy_version"],
    )
    metadata_path.write_text(
        json.dumps(metadata, indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
    )


@pytest.fixture
def environment(tmp_path, monkeypatch):
    work = tmp_path / "work"
    results = tmp_path / "results"
    inputs = {}

    for label, stage, role, root, subdir, filename in SPECS:
        base = work if root == "work" else results
        inputs[label] = str(write_fixture_bundle(base / subdir, stage, role, filename))

    modules = sorted(
        str(ROOT / "src/probe" / name)
        for name in (
            "resolver.py",
            "resolver_alignments.py",
            "resolver_anchors.py",
            "resolver_assembly.py",
            "resolver_candidates.py",
            "resolver_lineage.py",
            "resolver_metadata.py",
        )
    )

    inputs["implementation_script"] = str(CLI)
    inputs["runner_script"] = str(RUNNER)
    inputs["resolver_modules"] = modules

    destination = results / "common/07"

    context = SimpleNamespace(
        input=SimpleNamespace(**inputs),
        output=[str(destination / "manifest.json")],
        log=[str(tmp_path / "stage07.log")],
        params=SimpleNamespace(
            work_root=str(work),
            results_root=str(results),
            publication_root=str(results),
            start_release_id="2023_01",
            end_release_id="2026_03",
            stage07_policy={
                "resolver": dict(DEFAULT_POLICY["resolver"]),
                "annotation_donor": dict(DEFAULT_POLICY["annotation_donor"]),
            },
        ),
    )

    state = {"calls": [], "fail": False, "invalid_summary": False}

    def fake_run(command, *, stdout, stderr, check):
        assert check is True
        assert stderr == subprocess.STDOUT
        state["calls"].append(command)

        if state["fail"]:
            raise subprocess.CalledProcessError(1, command)

        write_cli_outputs(
            command,
            context.params.stage07_policy,
            invalid=state["invalid_summary"],
        )
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(
        "probe.workflow.runtime.runtime_environment",
        lambda: {"python": "pytest", "platform": "synthetic-test"},
    )
    monkeypatch.setattr(subprocess, "run", fake_run)

    return context, state, destination


def execute(context):
    runpy.run_path(
        str(RUNNER),
        init_globals={"snakemake": context},
    )


def test_successful_atomic_publication(environment):
    context, state, destination = environment

    execute(context)

    manifest = validate_bundle(destination, deep=True)
    assert manifest.stage == "07"
    assert manifest.role is None
    assert manifest.release_id is None
    assert manifest.parameters["policies"] == DEFAULT_POLICY

    assert len(manifest.inputs) == 2 * len(SPECS) + 9
    recorded_sources = {Path(item.path).name for item in manifest.inputs}
    assert {
        "resolver.py",
        "resolver_alignments.py",
        "resolver_anchors.py",
        "resolver_assembly.py",
        "resolver_candidates.py",
        "resolver_lineage.py",
        "resolver_metadata.py",
        "07_resolve_candidate_associations.py",
        "run_stage_07.py",
    }.issubset(recorded_sources)

    summary = json.loads((destination / "07_summary.json").read_text())
    outcome = json.loads((destination / "stage_outcome.json").read_text())

    assert summary["candidate_pair_count"] == 2
    assert outcome["state"] == "COMPLETED"
    assert outcome["counts"]["accepted"] == 1
    assert (destination / "07_accepted_associations.tsv").is_file()
    metadata = json.loads((destination / "07_selection_metadata.json").read_text())
    assert metadata["effective_policies"] == DEFAULT_POLICY
    assert len(metadata["decision_rule_precedence"]) == 15
    assert len(state["calls"]) == 1


def test_nondefault_policies_are_forwarded(environment):
    context, state, destination = environment
    context.params.stage07_policy = {
        "resolver": {
            "subtaxon_policy": "include_audited_same_species",
        },
        "annotation_donor": {
            "partial_sequence": "report_only",
            "documentary_fragment": "report_only",
        },
    }

    execute(context)

    command = state["calls"][0]
    for option, expected in (
        ("--subtaxon-policy", "include_audited_same_species"),
        ("--partial-sequence", "report_only"),
        ("--documentary-fragment", "report_only"),
    ):
        assert command[command.index(option) + 1] == expected

    manifest = validate_bundle(destination)
    assert manifest.parameters["policies"] == context.params.stage07_policy


def test_existing_bundle_is_never_replaced(environment):
    context, state, destination = environment

    destination.mkdir()
    sentinel = destination / "DO_NOT_DELETE"
    sentinel.write_text("preserve\n")

    with pytest.raises(FileExistsError):
        execute(context)

    assert sentinel.read_text() == "preserve\n"
    assert not state["calls"]


def test_wrong_upstream_release_fails_closed(environment):
    context, state, destination = environment
    path = Path(context.input.start_taxonomy_manifest)
    manifest = StageManifest.read(path)
    broken = StageManifest(
        stage=manifest.stage,
        role=manifest.role,
        release_id="wrong-release",
        parameters=manifest.parameters,
        ontology=manifest.ontology,
        implementation=manifest.implementation,
        inputs=manifest.inputs,
        outputs=manifest.outputs,
    )
    broken.write(path)

    with pytest.raises(ValueError, match="wrong upstream release"):
        execute(context)

    assert not destination.exists()
    assert not state["calls"]


def test_cli_failure_does_not_publish(environment):
    context, state, destination = environment
    state["fail"] = True

    with pytest.raises(subprocess.CalledProcessError):
        execute(context)

    assert not destination.exists()


def test_invalid_summary_does_not_publish(environment):
    context, state, destination = environment
    state["invalid_summary"] = True

    with pytest.raises(ValueError, match="count sum mismatch"):
        execute(context)

    assert not destination.exists()


def test_same_size_upstream_tampering_fails_closed(environment):
    context, state, destination = environment

    payload = (
        Path(context.input.start_taxonomy_manifest).parent / "02B_target_taxonomy.tsv"
    )
    original = payload.read_bytes()
    payload.write_bytes(bytes([original[0] ^ 1]) + original[1:])

    assert payload.stat().st_size == len(original)

    with pytest.raises(ValueError, match="(?i)(checksum|sha256|integrity)"):
        execute(context)

    assert not destination.exists()
    assert not state["calls"]


def test_tsv_summary_count_disagreement_fails_closed(environment, monkeypatch):
    context, state, destination = environment

    original_run = subprocess.run

    def inconsistent_cli(command, **kwargs):
        result = original_run(command, **kwargs)

        output = Path(command[command.index("--output") + 1])
        lines = output.read_text(encoding="utf-8").splitlines(keepends=True)
        assert len(lines) == 3

        # Preserve header and one row, but leave JSON count at two.
        output.write_text("".join(lines[:2]), encoding="utf-8")
        return result

    monkeypatch.setattr(subprocess, "run", inconsistent_cli)

    with pytest.raises(ValueError, match="(?i)(TSV|candidate|count)"):
        execute(context)

    assert not destination.exists()
    assert len(state["calls"]) == 1


def mutate_serialized_tsv(monkeypatch, mutation):
    """Alter a synthetic CLI output before runner validation."""

    original_run = subprocess.run

    def altered_run(command, **kwargs):
        result = original_run(command, **kwargs)

        path = Path(command[command.index("--output") + 1])

        with path.open("r", encoding="utf-8", newline="") as handle:
            reader = csv.DictReader(handle, delimiter="\t")
            columns = tuple(reader.fieldnames or ())
            rows = list(reader)

        assert columns == STAGE07_COLUMNS
        assert len(rows) == 2

        mutation(rows)

        with path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(
                handle,
                fieldnames=columns,
                delimiter="\t",
                lineterminator="\n",
            )
            writer.writeheader()
            writer.writerows(rows)

        return result

    monkeypatch.setattr(subprocess, "run", altered_run)


def test_duplicate_serialized_candidate_fails_closed(environment, monkeypatch):
    context, state, destination = environment

    def duplicate_identity(rows):
        rows[1]["rid"] = rows[0]["rid"]

    mutate_serialized_tsv(monkeypatch, duplicate_identity)

    with pytest.raises(ValueError, match="duplicate TSV candidate identity"):
        execute(context)

    assert not destination.exists()
    assert len(state["calls"]) == 1


def test_wrong_decision_distribution_fails_closed(environment, monkeypatch):
    context, state, destination = environment

    def change_decision(rows):
        rows[1]["decision"] = "ACCEPTED"

    mutate_serialized_tsv(monkeypatch, change_decision)

    with pytest.raises(ValueError, match="decision_counts mismatch"):
        execute(context)

    assert not destination.exists()
    assert len(state["calls"]) == 1


def test_wrong_individual_multiplicity_fails_closed(environment, monkeypatch):
    context, state, destination = environment

    def change_multiplicity(rows):
        rows[0]["accepted_candidates_for_target_release"] = "2"

    mutate_serialized_tsv(monkeypatch, change_multiplicity)

    with pytest.raises(ValueError, match="accepted multiplicity"):
        execute(context)

    assert not destination.exists()
    assert len(state["calls"]) == 1


def test_independent_publication_root(environment, tmp_path):
    context, state, original_destination = environment

    publication_root = tmp_path / "isolated_publication"
    publication_root.mkdir()

    destination = publication_root / "common/07"

    context.params.publication_root = str(publication_root)
    context.output[:] = [str(destination / "manifest.json")]

    execute(context)

    manifest = validate_bundle(destination, deep=True)

    assert manifest.stage == "07"
    assert len(manifest.inputs) == 33
    assert len(state["calls"]) == 1
    assert not original_destination.exists()


def test_missing_accepted_projection_does_not_publish(environment, monkeypatch):
    context, state, destination = environment
    original_run = subprocess.run

    def missing_accepted(command, **kwargs):
        result = original_run(command, **kwargs)
        Path(command[command.index("--accepted-output") + 1]).unlink()
        return result

    monkeypatch.setattr(subprocess, "run", missing_accepted)
    with pytest.raises(FileNotFoundError):
        execute(context)
    assert not destination.exists()
    assert len(state["calls"]) == 1


def test_missing_selection_metadata_does_not_publish(environment, monkeypatch):
    context, state, destination = environment
    original_run = subprocess.run

    def missing_metadata(command, **kwargs):
        result = original_run(command, **kwargs)
        Path(command[command.index("--selection-metadata") + 1]).unlink()
        return result

    monkeypatch.setattr(subprocess, "run", missing_metadata)
    with pytest.raises(FileNotFoundError):
        execute(context)
    assert not destination.exists()
    assert len(state["calls"]) == 1


def test_corrupt_accepted_projection_does_not_publish(environment, monkeypatch):
    context, state, destination = environment
    original_run = subprocess.run

    def altered_projection(command, **kwargs):
        result = original_run(command, **kwargs)
        path = Path(command[command.index("--accepted-output") + 1])
        with path.open("r", encoding="utf-8", newline="") as handle:
            reader = csv.DictReader(handle, delimiter="\t")
            rows = list(reader)
        assert len(rows) == 1
        rows[0]["annotation_donor"] = "NO"
        with path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(
                handle,
                fieldnames=ACCEPTED_COLUMNS,
                delimiter="\t",
                lineterminator="\n",
            )
            writer.writeheader()
            writer.writerows(rows)
        return result

    monkeypatch.setattr(subprocess, "run", altered_projection)
    with pytest.raises(ValueError, match="accepted projection row mismatch"):
        execute(context)
    assert not destination.exists()
    assert len(state["calls"]) == 1


def test_corrupt_selection_metadata_does_not_publish(environment, monkeypatch):
    context, state, destination = environment
    original_run = subprocess.run

    def altered_metadata(command, **kwargs):
        result = original_run(command, **kwargs)
        path = Path(command[command.index("--selection-metadata") + 1])
        metadata = json.loads(path.read_text(encoding="utf-8"))
        metadata["schema_version"] = "corrupt"
        path.write_text(
            json.dumps(metadata, indent=2, sort_keys=True, allow_nan=False) + "\n",
            encoding="utf-8",
        )
        return result

    monkeypatch.setattr(subprocess, "run", altered_metadata)
    with pytest.raises(ValueError, match="selection metadata schema version mismatch"):
        execute(context)
    assert not destination.exists()
    assert len(state["calls"]) == 1


def test_accepted_non_donor_is_retained(environment, monkeypatch):
    context, state, destination = environment
    original_run = subprocess.run

    def non_donor_cli(command, **kwargs):
        result = original_run(command, **kwargs)
        full_path = Path(command[command.index("--output") + 1])
        accepted_path = Path(command[command.index("--accepted-output") + 1])
        summary_path = Path(command[command.index("--summary") + 1])

        with full_path.open("r", encoding="utf-8", newline="") as handle:
            complete = list(csv.DictReader(handle, delimiter="\t"))
        assert complete[0]["decision"] == "ACCEPTED"
        complete[0]["annotation_donor"] = "NO"
        with full_path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(
                handle, fieldnames=STAGE07_COLUMNS, delimiter="\t", lineterminator="\n"
            )
            writer.writeheader()
            writer.writerows(complete)

        with accepted_path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(
                handle, fieldnames=ACCEPTED_COLUMNS, delimiter="\t", lineterminator="\n"
            )
            writer.writeheader()
            writer.writerow(
                {column: complete[0][column] for column in ACCEPTED_COLUMNS}
            )

        summary = json.loads(summary_path.read_text(encoding="utf-8"))
        summary["annotation_donor_counts"] = {"NO": 2}
        summary_path.write_text(json.dumps(summary) + "\n", encoding="utf-8")
        return result

    monkeypatch.setattr(subprocess, "run", non_donor_cli)
    execute(context)
    assert len(state["calls"]) == 1
    manifest = validate_bundle(destination, deep=True)
    assert manifest.stage == "07"
    with (destination / "07_accepted_associations.tsv").open(
        "r", encoding="utf-8", newline=""
    ) as handle:
        rows = list(csv.DictReader(handle, delimiter="\t"))
    assert len(rows) == 1
    assert rows[0]["annotation_donor"] == "NO"


def test_completed_bundle_cannot_be_replaced(environment):
    context, state, destination = environment

    execute(context)
    manifest_path = destination / "manifest.json"
    original = manifest_path.read_bytes()

    with pytest.raises(FileExistsError, match="completed destination"):
        execute(context)

    assert manifest_path.read_bytes() == original
    assert len(state["calls"]) == 1


def test_snakefile_invalidated_bundle_can_be_rebuilt(environment):
    context, state, destination = environment

    execute(context)
    (destination / "manifest.json").unlink()

    execute(context)

    manifest = validate_bundle(destination, deep=True)
    assert manifest.stage == "07"
    assert len(manifest.inputs) == 33
    assert len(state["calls"]) == 2


def test_invalidated_bundle_with_foreign_file_is_preserved(environment):
    context, state, destination = environment

    destination.mkdir(parents=True)
    (destination / "07_summary.json").write_text("incomplete\n", encoding="utf-8")
    foreign = destination / "DO_NOT_DELETE"
    foreign.write_text("preserve\n", encoding="utf-8")

    with pytest.raises(FileExistsError, match="unexpected entry"):
        execute(context)

    assert foreign.read_text(encoding="utf-8") == "preserve\n"
    assert not state["calls"]


def test_invalidated_bundle_recovers_after_cli_failure(environment):
    context, state, destination = environment

    execute(context)
    (destination / "manifest.json").unlink()

    state["fail"] = True
    with pytest.raises(subprocess.CalledProcessError):
        execute(context)

    assert not (destination / "manifest.json").exists()

    state["fail"] = False
    execute(context)

    assert validate_bundle(destination, deep=True).stage == "07"
    assert len(state["calls"]) == 3
