"""Synthetic contract tests for Stage 2C -> Stage 08 donor projection."""

from __future__ import annotations

import csv
import hashlib
import importlib.util
import io
import json
from pathlib import Path

import pytest

from probe.resolver_assembly import STAGE07_COLUMNS
from probe.stage08_donors import ACCEPTED_COLUMNS

SCRIPT = (
    Path(__file__).resolve().parents[1]
    / "scripts/pipeline/08_bridge_stage2c_to_stage08.py"
)
spec = importlib.util.spec_from_file_location("stage08_stage2c_bridge_cli", SCRIPT)
assert spec is not None and spec.loader is not None
bridge = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bridge)
RELEASES = ("2023_01", "2026_03")


def row(*, release="2023_01", rid="1", target="T1", donor="YES", n="1"):
    result = dict.fromkeys(STAGE07_COLUMNS, ".")
    result.update(
        release=release,
        target_id=target,
        rid=rid,
        scope="PRIMARY",
        evaluation_scope="PRIMARY",
        target_accession="P1",
        candidate_accession="P1" if rid == "1" else "P2",
        decision="ACCEPTED",
        annotation_donor=donor,
        exact_sequence="1",
        decision_basis="SAME_SPECIES_LITERAL_EXACT_SEQUENCE",
        sequence_relation="EXACT_SEQUENCE",
        accepted_candidates_for_target_release=n,
    )
    return result


def project(rows, *, expected=None, donors=None):
    source = io.StringIO()
    writer = csv.DictWriter(
        source, fieldnames=STAGE07_COLUMNS, delimiter="\t", lineterminator="\n"
    )
    writer.writeheader()
    writer.writerows(rows)
    source.seek(0)
    dest = io.StringIO()
    total = len(rows) if expected is None else expected
    yes = sum(value["annotation_donor"] == "YES" for value in rows)
    yes = yes if donors is None else donors
    result = bridge._project(
        source,
        dest,
        releases=RELEASES,
        expected_accepted=total,
        expected_donors=yes,
    )
    dest.seek(0)
    return result, list(csv.DictReader(dest, delimiter="\t"))


def test_project_complete_accepted_and_no_locus_exact():
    rows = [
        row(rid="1", n="2"),
        row(rid="2", donor="NO", n="2"),
        row(release="2026_03", rid="3"),
    ]
    counts, output = project(rows)
    assert counts == (3, 2)
    assert tuple(output[0]) == ACCEPTED_COLUMNS
    assert [value["annotation_donor"] for value in output] == [
        "YES", "NO", "YES"
    ]
    assert all("audit_reason" not in value for value in output)
    assert output[0]["decision_basis"] == "SAME_SPECIES_LITERAL_EXACT_SEQUENCE"
    assert output[0]["accepted_candidates_for_target_release"] == "2"


@pytest.mark.parametrize("change", [
    {"expected": 4},
    {"donors": 0},
])
def test_summary_mismatch_fails(change):
    with pytest.raises(ValueError, match="totals differ"):
        project([row()], **change)


def test_missing_or_inconsistent_multiplicity_fails():
    with pytest.raises(ValueError, match="multiplicity"):
        project([row(n="2")])
    with pytest.raises(ValueError, match="multiplicity"):
        project([row(n="2"), row(rid="2", n="3")])


def test_duplicate_or_unsorted_rows_fail():
    with pytest.raises(ValueError, match="duplicate or out-of-order"):
        project([row(), row()])
    with pytest.raises(ValueError, match="duplicate or out-of-order"):
        project([row(rid="2"), row(rid="1")])


def test_invalid_release_and_nonaccepted_fail():
    with pytest.raises(ValueError, match="unexpected release"):
        project([row(release="other")])
    bad = row()
    bad["decision"] = "UNRESOLVED"
    with pytest.raises(ValueError, match="non-ACCEPTED"):
        project([bad])


def test_missing_schema_column_fails():
    original = io.StringIO("release\ttarget_id\nS\tT1\n")
    with pytest.raises(ValueError, match="header"):
        bridge._project(
            original,
            io.StringIO(),
            releases=RELEASES,
            expected_accepted=1,
            expected_donors=1,
        )


def published_source(tmp_path: Path):
    key = "a" * 64
    root = tmp_path / "work/pipeline-v2/stage2c_evidence_variants" / key
    source = root / "resolver/strict"
    source.mkdir(parents=True)
    for name in ("2C_07_complete.tsv", bridge.STAGE2C_ACCEPTED):
        (source / name).write_text("synthetic\n", encoding="utf-8")
    def sha(path: Path) -> str:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    report = {
        "schema": "stage2c-resolver-variant-v1",
        "strain_policy": "strict",
        "discovery_reexecuted": False,
        "release_order": list(RELEASES),
        "output_sha256": {
            bridge.STAGE2C_ACCEPTED: sha(source / bridge.STAGE2C_ACCEPTED)
        },
    }
    (source / bridge.STAGE2C_SUMMARY).write_text(json.dumps(report))
    files = {name: sha(source / name) for name in (
        "2C_07_complete.tsv", bridge.STAGE2C_ACCEPTED, bridge.STAGE2C_SUMMARY
    )}
    (root / "resolver/strict.ready.json").write_text(json.dumps({
        "schema": "probe-stage2c-evidence-dag-plan-v2",
        "integration_key": key,
        "role": "strict",
        "phase": "resolver",
        "summary": str(source / bridge.STAGE2C_SUMMARY),
        "summary_sha256": files[bridge.STAGE2C_SUMMARY],
        "publication_sha256": files,
    }))
    plan = {
        "schema": "probe-stage2c-evidence-dag-plan-v2",
        "key": key,
        "root": str(root),
        "run": str(tmp_path),
        "paths": {"resolver": {"strict": str(source)}},
        "releases": dict(zip(("START", "END"), RELEASES, strict=True)),
    }
    return plan, source


def test_source_receipts_detect_tampering(tmp_path):
    plan, source = published_source(tmp_path)
    with pytest.raises(ValueError, match="header"):
        bridge._check_source(plan, "strict")
    (source / bridge.STAGE2C_ACCEPTED).write_text(
        "altered\n", encoding="utf-8"
    )
    with pytest.raises(ValueError, match="digest"):
        bridge._check_source(plan, "strict")


def test_reject_unpinned_resolver_directory(tmp_path):
    plan, source = published_source(tmp_path)
    plan["paths"]["resolver"]["strict"] = str(source.parent)
    with pytest.raises(ValueError, match="escaped"):
        bridge._check_source(plan, "strict")
