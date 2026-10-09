"""Stage 07 CLI serialization and policy-forwarding tests."""

from __future__ import annotations

import csv
import importlib.util
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from probe.resolver_assembly import STAGE07_COLUMNS

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/pipeline/07_resolve_candidate_associations.py"


@pytest.fixture
def cli():
    spec = importlib.util.spec_from_file_location("stage07_cli", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class FakeResolved:
    """Only the public row contract consumed by the CLI."""

    def __init__(self, key, decision, donor=False, scope="PRIMARY"):
        self.key = key
        self.candidate = SimpleNamespace(
            release=key[0],
            target_id=key[1],
            scope=scope,
        )
        self.decision = SimpleNamespace(
            decision=decision,
            decision_basis=(
                "DIRECT_PRIMARY_ACCESSION"
                if decision == "ACCEPTED"
                else ("TAXONOMY_AUDIT" if decision == "AUDIT_ONLY" else "SEQUENCE_ONLY")
            ),
            evaluation_scope=scope,
            audit_outcome=(
                "REJECTED_BY_TAXONOMY_POLICY" if scope == "TAXONOMY_AUDIT" else None
            ),
            annotation_donor=donor,
        )
        self.sequence_relation = (
            "NOT_ALIGNED" if decision == "AUDIT_ONLY" else "EXACT_SEQUENCE"
        )

    def tsv_row(self):
        row = dict.fromkeys(STAGE07_COLUMNS, ".")
        row.update(
            {
                "release": self.key[0],
                "target_id": self.key[1],
                "rid": self.key[2],
                "scope": self.candidate.scope,
                "evaluation_scope": self.decision.evaluation_scope,
                "decision": self.decision.decision,
                "decision_basis": self.decision.decision_basis,
                "annotation_donor": ("YES" if self.decision.annotation_donor else "NO"),
                "sequence_relation": self.sequence_relation,
                "accepted_candidates_for_target_release": (
                    "." if self.decision.decision == "AUDIT_ONLY" else "2"
                ),
                "audit_outcome": self.decision.audit_outcome or ".",
            }
        )
        return row


def synthetic_rows():
    entries = [
        (("2023_01", "T1", "2"), "ACCEPTED", True, "PRIMARY"),
        (("2023_01", "T1", "10"), "ACCEPTED", True, "PRIMARY"),
        (("2023_01", "T2", "1"), "UNRESOLVED", False, "PRIMARY"),
        (
            ("2026_03", "T3", "1"),
            "AUDIT_ONLY",
            False,
            "TAXONOMY_AUDIT",
        ),
    ]
    return {
        key: FakeResolved(key, decision, donor, scope)
        for key, decision, donor, scope in entries
    }


def invoke(cli, monkeypatch, tmp_path, *, options=(), bad_donor=False):
    rows = synthetic_rows()
    if bad_donor:
        key = ("2023_01", "T2", "1")
        rows[key] = FakeResolved(key, "UNRESOLVED", True)

    marker = object()
    recorded = {}

    def load_candidates(**kwargs):
        recorded["candidate_arguments"] = kwargs
        return dict.fromkeys(rows)

    def collect_provenance(**kwargs):
        recorded["provenance_arguments"] = kwargs
        return marker

    def collect_alignment_evidence(**kwargs):
        recorded["alignment_arguments"] = kwargs
        return marker

    def resolve(**kwargs):
        recorded["policy"] = kwargs["policy"]
        assert kwargs["provenance"] is marker
        assert kwargs["alignments"] is marker
        assert kwargs["release_order"] == ("2023_01", "2026_03")
        return rows

    monkeypatch.setattr(cli, "load_candidate_universe", load_candidates)
    monkeypatch.setattr(cli, "collect_candidate_provenance", collect_provenance)
    monkeypatch.setattr(cli, "collect_alignments", collect_alignment_evidence)
    monkeypatch.setattr(cli, "resolve_joined_candidates", resolve)

    output = tmp_path / "associations.tsv"
    summary = tmp_path / "summary.json"
    args = [
        str(SCRIPT),
        "--work-root",
        str(tmp_path / "work"),
        "--results-root",
        str(tmp_path / "results"),
        "--start-release-id",
        "2023_01",
        "--end-release-id",
        "2026_03",
        "--output",
        str(output),
        "--summary",
        str(summary),
        *options,
    ]
    monkeypatch.setattr(sys, "argv", args)
    return output, summary, recorded


def test_cli_serializes_19_columns_and_summary(cli, monkeypatch, tmp_path):
    output, summary, recorded = invoke(cli, monkeypatch, tmp_path)
    assert cli.main() == 0

    with output.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle, delimiter="\t")
        assert tuple(reader.fieldnames) == STAGE07_COLUMNS
        rows = list(reader)

    assert len(rows) == 4
    assert [(r["release"], r["target_id"], r["rid"]) for r in rows] == [
        ("2023_01", "T1", "2"),
        ("2023_01", "T1", "10"),
        ("2023_01", "T2", "1"),
        ("2026_03", "T3", "1"),
    ]
    assert rows[-1]["annotation_donor"] == "NO"
    assert rows[-1]["accepted_candidates_for_target_release"] == "."

    data = json.loads(summary.read_text(encoding="utf-8"))
    assert data["candidate_pair_count"] == 4
    assert data["decision_counts"] == {
        "ACCEPTED": 2,
        "AUDIT_ONLY": 1,
        "UNRESOLVED": 1,
    }
    assert data["annotation_donor_counts"] == {"NO": 2, "YES": 2}
    assert data["multiple_accepted_groups"] == 1
    assert data["maximum_accepted_multiplicity"] == 2
    assert data["policy_version"] == "stage07-resolver-v1"
    assert recorded["policy"].subtaxon_policy == "strict"


def test_cli_forwards_nondefault_policies(cli, monkeypatch, tmp_path):
    options = (
        "--subtaxon-policy",
        "include_audited_same_species",
        "--partial-sequence",
        "report_only",
        "--documentary-fragment",
        "report_only",
    )
    _, summary, recorded = invoke(cli, monkeypatch, tmp_path, options=options)
    assert cli.main() == 0

    policy = recorded["policy"]
    assert policy.subtaxon_policy == "include_audited_same_species"
    assert policy.partial_sequence == "report_only"
    assert policy.documentary_fragment == "report_only"

    data = json.loads(summary.read_text(encoding="utf-8"))
    assert data["policies"] == {
        "resolver": {
            "subtaxon_policy": "include_audited_same_species",
        },
        "annotation_donor": {
            "partial_sequence": "report_only",
            "documentary_fragment": "report_only",
        },
    }


def test_cli_rejects_donor_on_unresolved(cli, monkeypatch, tmp_path):
    output, summary, _ = invoke(cli, monkeypatch, tmp_path, bad_donor=True)
    with pytest.raises(RuntimeError, match="cannot donate"):
        cli.main()

    assert not output.exists()
    assert not summary.exists()


def test_cli_does_not_overwrite_existing_outputs(cli, monkeypatch, tmp_path):
    output, summary, _ = invoke(cli, monkeypatch, tmp_path)
    output.write_text("existing\n", encoding="utf-8")

    with pytest.raises(FileExistsError):
        cli.main()

    assert output.read_text(encoding="utf-8") == "existing\n"
    assert not summary.exists()


def test_cli_rejects_identical_output_paths(cli, monkeypatch, tmp_path):
    output, _, _ = invoke(cli, monkeypatch, tmp_path)
    argv = sys.argv.copy()
    argv[argv.index("--summary") + 1] = str(output)
    monkeypatch.setattr(sys, "argv", argv)

    with pytest.raises(ValueError, match="distinct"):
        cli.main()

    assert not output.exists()


def test_cli_accepted_projection_preserves_nondonors(cli, monkeypatch, tmp_path):
    accepted_path = tmp_path / "accepted.tsv"

    output, summary, _ = invoke(
        cli,
        monkeypatch,
        tmp_path,
        options=("--accepted-output", str(accepted_path)),
    )

    original_resolve = cli.resolve_joined_candidates

    def resolve_with_nondonor(**kwargs):
        rows = original_resolve(**kwargs)
        key = ("2023_01", "T1", "10")
        rows[key] = FakeResolved(key, "ACCEPTED", False)
        return rows

    monkeypatch.setattr(cli, "resolve_joined_candidates", resolve_with_nondonor)

    assert cli.main() == 0

    with output.open(encoding="utf-8", newline="") as handle:
        complete = list(csv.DictReader(handle, delimiter="\t"))

    with accepted_path.open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle, delimiter="\t")
        assert tuple(reader.fieldnames or ()) == cli.ACCEPTED_COLUMNS
        projected = list(reader)

    expected = [
        {field: row[field] for field in cli.ACCEPTED_COLUMNS}
        for row in complete
        if row["decision"] == "ACCEPTED"
    ]

    assert projected == expected
    assert len(projected) == 2
    assert [row["annotation_donor"] for row in projected] == ["YES", "NO"]

    data = json.loads(summary.read_text(encoding="utf-8"))
    assert data["decision_counts"]["ACCEPTED"] == 2
    assert data["annotation_donor_counts"]["YES"] == 1


def test_cli_rejects_colliding_accepted_output(cli, monkeypatch, tmp_path):
    output, summary, _ = invoke(
        cli,
        monkeypatch,
        tmp_path,
        options=(
            "--accepted-output",
            str(tmp_path / "summary.json"),
        ),
    )

    with pytest.raises(ValueError, match="distinct"):
        cli.main()

    assert not output.exists()
    assert not summary.exists()


def test_write_accepted_projection_standalone(cli, tmp_path):
    complete = tmp_path / "complete.tsv"
    accepted = tmp_path / "accepted.tsv"

    rows = []
    for rid, decision, donor in (
        ("101", "ACCEPTED", "NO"),
        ("102", "UNRESOLVED", "NO"),
        ("103", "ACCEPTED", "YES"),
    ):
        row = dict.fromkeys(cli.STAGE07_COLUMNS, "")
        row.update(
            release="2023_01",
            target_id="T1",
            rid=rid,
            decision=decision,
            annotation_donor=donor,
        )
        rows.append(row)

    with complete.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=cli.STAGE07_COLUMNS,
            delimiter="\t",
            lineterminator="\n",
        )
        writer.writeheader()
        writer.writerows(rows)

    count = cli.write_accepted_projection(complete, accepted, expected_count=2)
    assert count == 2

    with accepted.open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle, delimiter="\t")
        assert tuple(reader.fieldnames or ()) == cli.ACCEPTED_COLUMNS
        selected = list(reader)

    assert [row["rid"] for row in selected] == ["101", "103"]
    assert [row["annotation_donor"] for row in selected] == ["NO", "YES"]

    with pytest.raises(FileExistsError):
        cli.write_accepted_projection(complete, accepted)

    failed = tmp_path / "failed.tsv"
    with pytest.raises(RuntimeError, match="count mismatch"):
        cli.write_accepted_projection(complete, failed, expected_count=3)
    assert not failed.exists()

    malformed = tmp_path / "malformed.tsv"
    malformed.write_text("wrong\theader\n", encoding="utf-8")

    rejected = tmp_path / "rejected.tsv"
    with pytest.raises(ValueError, match="schema mismatch"):
        cli.write_accepted_projection(malformed, rejected)

    assert not rejected.exists()


@pytest.mark.parametrize(
    ("partial", "fragment"),
    [
        ("allow", "allow"),
        ("report_only", "report_only"),
    ],
)
def test_cli_selection_metadata_matches_summary(
    cli, monkeypatch, tmp_path, partial, fragment
):
    metadata_path = tmp_path / "selection_metadata.json"
    accepted_path = tmp_path / "accepted.tsv"

    output, summary_path, _ = invoke(
        cli,
        monkeypatch,
        tmp_path,
        options=(
            "--partial-sequence",
            partial,
            "--documentary-fragment",
            fragment,
            "--accepted-output",
            str(accepted_path),
            "--selection-metadata",
            str(metadata_path),
        ),
    )

    assert cli.main() == 0

    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))

    assert output.is_file()
    assert accepted_path.is_file()

    assert metadata["stage"] == "07"
    assert metadata["policy_version"] == summary["policy_version"]
    assert metadata["releases"] == summary["releases"]
    assert metadata["effective_policies"] == summary["policies"]

    assert len(metadata["decision_rule_precedence"]) == 15
    assert metadata["stage08_contract"]["go_donor_criterion"] == (
        "annotation_donor=YES"
    )

    expected_serialization = (
        json.dumps(
            metadata,
            indent=2,
            sort_keys=True,
            allow_nan=False,
        )
        + "\n"
    )

    assert metadata_path.read_text(encoding="utf-8") == (expected_serialization)


def test_cli_rejects_selection_metadata_path_collision(cli, monkeypatch, tmp_path):
    output, summary_path, _ = invoke(
        cli,
        monkeypatch,
        tmp_path,
        options=(
            "--selection-metadata",
            str(tmp_path / "summary.json"),
        ),
    )

    with pytest.raises(ValueError, match="distinct"):
        cli.main()

    assert not output.exists()
    assert not summary_path.exists()
