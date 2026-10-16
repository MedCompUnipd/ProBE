#!/usr/bin/env python3
"""Project immutable Stage 2C Stage 07 results into the Stage 08 interface.

This adapter DOES NOT classify donors, parse GOA or run scientific stages.
It only preserves ACCEPTED associations and their donor flags. The normal
Stage 08 collection preflight and publisher remain the scientific consumers.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
from pathlib import Path
from typing import TextIO

BRIDGE_VERSION = "stage08-stage2c-accepted-projection-v1"
ACCEPTED = "07_accepted_associations.tsv"
SELECTION = "07_selection_metadata.json"
STAGE2C_ACCEPTED = "2C_07_accepted.tsv"
STAGE2C_SUMMARY = "2C_07_summary.json"


def _sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _check_source(plan: dict, policy: str) -> tuple[Path, dict, Path]:
    from probe.resolver_assembly import STAGE07_COLUMNS

    if policy not in {"strict", "same_species_exact"}:
        raise ValueError("invalid policy")
    if plan.get("schema") != "probe-stage2c-evidence-dag-plan-v2":
        raise ValueError("invalid Stage 2C plan schema")
    key = plan["key"]
    root = Path(plan["root"])
    source = Path(plan["paths"]["resolver"][policy])
    if (
        source != root / "resolver" / policy
        or root != (
            Path(plan["run"]) / "work/pipeline-v2/stage2c_evidence_variants" / key
        )
    ):
        raise ValueError("resolver source escaped immutable Stage 2C plan")
    receipt_path = source.parent / f"{policy}.ready.json"
    for item in (
        source,
        receipt_path,
        source / STAGE2C_ACCEPTED,
        source / STAGE2C_SUMMARY,
        source / "2C_07_complete.tsv",
    ):
        if item.is_symlink():
            raise ValueError(f"symlink forbidden: {item}")
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    summary_path = source / STAGE2C_SUMMARY
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    files = receipt.get("publication_sha256")
    expected = {"2C_07_complete.tsv", STAGE2C_ACCEPTED, STAGE2C_SUMMARY}
    if (
        receipt.get("schema") != plan["schema"]
        or receipt.get("integration_key") != key
        or receipt.get("role") != policy
        or receipt.get("phase") != "resolver"
        or receipt.get("summary") != str(summary_path)
        or not isinstance(files, dict)
        or set(files) != expected
        or receipt.get("summary_sha256") != _sha(summary_path)
        or any(_sha(source / name) != digest for name, digest in files.items())
    ):
        raise ValueError("Stage 2C ready receipt/content digest mismatch")
    if (
        summary.get("schema") != "stage2c-resolver-variant-v1"
        or summary.get("strain_policy") != policy
        or summary.get("discovery_reexecuted") is not False
        or summary.get("release_order") != [
            plan["releases"]["START"], plan["releases"]["END"]
        ]
        or summary.get("output_sha256", {}).get(STAGE2C_ACCEPTED)
        != files[STAGE2C_ACCEPTED]
    ):
        raise ValueError("Stage 2C resolver summary inconsistent")
    with (source / STAGE2C_ACCEPTED).open(
        "r", newline="", encoding="utf-8"
    ) as handle:
        header = tuple(next(csv.reader(handle, delimiter="\t"), ()))
    if header != STAGE07_COLUMNS:
        raise ValueError("unexpected 19-column Stage 2C accepted header")
    return source, summary, receipt_path


def _project(
    incoming: TextIO,
    outgoing: TextIO,
    *,
    releases: tuple[str, str],
    expected_accepted: int,
    expected_donors: int,
) -> tuple[int, int]:
    from probe.resolver_assembly import STAGE07_COLUMNS
    from probe.stage08_donors import ACCEPTED_COLUMNS, DonorAssociation

    reader = csv.DictReader(incoming, delimiter="\t", quoting=csv.QUOTE_NONE)
    if tuple(reader.fieldnames or ()) != STAGE07_COLUMNS:
        raise ValueError("Stage 2C accepted header differs from resolver contract")
    writer = csv.writer(
        outgoing, delimiter="\t", lineterminator="\n", quoting=csv.QUOTE_NONE
    )
    writer.writerow(ACCEPTED_COLUMNS)
    observed = donors = 0
    previous_key: tuple[int, str, int] | None = None
    group = None
    group_total = 0
    group_expected = 0
    for row in reader:
        if None in row or any(value is None for value in row.values()):
            raise ValueError(f"malformed Stage 2C row at {reader.line_num}")
        release = row["release"]
        if release not in releases:
            raise ValueError("Stage 2C row has unexpected release")
        if row["decision"] != "ACCEPTED":
            raise ValueError("Stage 2C accepted source contains non-ACCEPTED row")
        # The existing Stage 08 validator is the authority for field semantics.
        value = DonorAssociation(*(row[col] for col in ACCEPTED_COLUMNS))
        ordered = (releases.index(release), value.target_id, int(value.rid))
        if previous_key is not None and ordered <= previous_key:
            raise ValueError("duplicate or out-of-order Stage 2C association")
        previous_key = ordered
        current_group = release, value.target_id
        if group != current_group:
            if group is not None and group_total != group_expected:
                raise ValueError("Stage 2C accepted group multiplicity mismatch")
            group = current_group
            group_total = 0
            group_expected = value.declared_multiplicity
        if group_expected != value.declared_multiplicity:
            raise ValueError("inconsistent Stage 2C group multiplicity")
        group_total += 1
        writer.writerow(value.tsv_values())
        observed += 1
        donors += int(value.may_donate)
    if group is not None and group_total != group_expected:
        raise ValueError("last Stage 2C accepted group multiplicity mismatch")
    if (observed, donors) != (expected_accepted, expected_donors):
        raise ValueError("Stage 2C accepted/donor totals differ from summary")
    return observed, donors


def bridge(plan_path: Path, policy: str, destination: Path) -> dict:
    from probe.stage2c_workflow_plan import read_plan
    from probe.workflow.artifacts import (
        Artifact,
        atomic_stage_bundle,
        sha256_file,
        validate_bundle,
    )

    plan_path = plan_path.resolve(strict=True)
    plan = read_plan(plan_path)
    source, summary, receipt = _check_source(plan, policy)
    destination = destination.absolute()
    if destination.exists() or destination.is_symlink():
        raise FileExistsError("Stage 08 adapter destination already exists")
    if destination.parent.is_symlink() or not destination.parent.is_dir():
        raise ValueError("adapter parent must be an existing real directory")
    # A separate, versioned Stage 08 input area avoids changing Stage 2C/V5.
    root = Path(plan["run"]) / "work/pipeline-v2/stage2c_stage08_adapters"
    expected_destination = root / plan["key"] / policy
    if destination != expected_destination:
        raise ValueError("adapter destination differs from pinned Stage 2C key")
    start, end = (plan["releases"][role] for role in ("START", "END"))
    source_path = source / STAGE2C_ACCEPTED
    before = _sha(source_path)
    def artifact(path: Path) -> Artifact:
        return Artifact(str(path), path.stat().st_size, sha256_file(path))

    metadata = {
        "schema_version": "stage07-selection-metadata-v1",
        "stage": "07",
        "policy_version": BRIDGE_VERSION,
        "releases": {"START": start, "END": end},
        "effective_policies": {
            "resolver": {"strain_policy": policy},
            "annotation_donor": {
                "partial_sequence": summary["partial_sequence"],
                "documentary_fragment": summary["documentary_fragment"],
            },
        },
        "stage08_contract": {
            "association_source": ACCEPTED,
            "go_donor_criterion": "annotation_donor=YES",
            "preserve_nondonors": True,
            "preserve_audit_origin": True,
        },
        "source": {
            "type": "Stage 2C 19-column resolver accepted projection",
            "key": plan["key"],
            "policy": policy,
            "accepted_sha256": before,
        },
    }
    parameters = {
        "start_release_id": start,
        "end_release_id": end,
        "policy_version": BRIDGE_VERSION,
        "policies": metadata["effective_policies"],
        "source_key": plan["key"],
        "source_policy": policy,
        "bridge_only": True,
    }
    script = Path(__file__).resolve(strict=True)
    inputs = tuple(
        artifact(item)
        for item in (plan_path, receipt, source / STAGE2C_SUMMARY, source_path)
    )
    with atomic_stage_bundle(
        destination,
        stage="07",
        role=None,
        release_id=None,
        parameters=parameters,
        ontology=None,
        implementation={
            "name": "Stage 2C -> Stage 08 interface adapter",
            "version": BRIDGE_VERSION,
            "source_sha256": {script.name: _sha(script)},
        },
        inputs=inputs,
        hash_outputs=True,
    ) as temp:
        with (
            source_path.open("r", encoding="utf-8", newline="") as reader,
            (temp / ACCEPTED).open("w", encoding="utf-8", newline="") as writer,
        ):
            accepted, donors = _project(
                reader,
                writer,
                releases=(start, end),
                expected_accepted=summary["decisions"]["ACCEPTED"],
                expected_donors=summary["annotation_donor_yes"],
            )
        (temp / SELECTION).write_text(
            json.dumps(metadata, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        if _sha(source_path) != before or _sha(receipt) != inputs[1].sha256:
            raise ValueError("Stage 2C source changed during adaptation")
    manifest = validate_bundle(destination, deep=False)
    if manifest.stage != "07" or manifest.status != "complete":
        raise ValueError("adapter bundle publication incomplete")
    return {
        "policy": policy,
        "accepted": accepted,
        "donors": donors,
        "destination": str(destination),
        "source_key": plan["key"],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", required=True, type=Path)
    parser.add_argument(
        "--policy", required=True, choices=("strict", "same_species_exact")
    )
    parser.add_argument("--destination", required=True, type=Path)
    args = parser.parse_args()
    print(
        json.dumps(
            bridge(args.plan, args.policy, args.destination),
            indent=2,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
