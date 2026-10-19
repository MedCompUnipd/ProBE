"""Publish a checked, decision-preserving Stage 07 input bundle for Stage 08.

No donor eligibility decisions are made here. This is a format projection
of the already resolved Stage 2C association table.
"""

from __future__ import annotations

import csv
import hashlib
import json
from pathlib import Path

from probe.resolver_assembly import STAGE07_COLUMNS
from probe.stage08_donors import (
    ACCEPTED_COLUMNS,
    DonorAssociation,
    read_accepted_associations,
)
from probe.workflow.artifacts import (
    Artifact,
    atomic_stage_bundle,
    validate_bundle,
)

SOURCE = "2C_07_accepted.tsv"
SOURCE_COMPLETE = "2C_07_complete.tsv"
SOURCE_SUMMARY = "2C_07_summary.json"
ACCEPTED = "07_accepted_associations.tsv"
SELECTION = "07_selection_metadata.json"
VERSION = "probe-canonical-stage07-bridge-v1"
POLICIES = frozenset({"same_species_exact", "strict"})


def _sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _artifact(path: Path) -> Artifact:
    if not path.is_file() or path.is_symlink():
        raise ValueError(f"Missing or symlinked resolver source: {path}")
    return Artifact(str(path), path.stat().st_size, _sha(path))


def _check(resolver: Path, policy: str, releases: dict[str, str]) -> dict:
    if not resolver.is_dir() or resolver.is_symlink():
        raise ValueError("Missing resolver publication")
    if policy not in POLICIES or set(releases) != {"START", "END"}:
        raise ValueError("Invalid policy/release roles")
    if releases["START"] == releases["END"]:
        raise ValueError("Identical START/END releases")
    expected = (SOURCE_COMPLETE, SOURCE, SOURCE_SUMMARY)
    if {part.name for part in resolver.iterdir()} != set(expected):
        raise ValueError("Unexpected resolver publication payloads")
    for filename in expected:
        _artifact(resolver / filename)
    summary = json.loads((resolver / SOURCE_SUMMARY).read_text("utf-8"))
    if (
        summary.get("schema") != "stage2c-resolver-variant-v1"
        or summary.get("strain_policy") != policy
        or summary.get("release_order")
        != [releases["START"], releases["END"]]
        or summary.get("discovery_reexecuted") is not False
    ):
        raise ValueError("Resolver scientific summary contract mismatch")
    declared = summary.get("output_sha256")
    if not isinstance(declared, dict):
        raise ValueError("Resolver output checksums missing")
    for filename in (SOURCE_COMPLETE, SOURCE):
        if declared.get(filename) != _sha(resolver / filename):
            raise ValueError(f"Resolver output SHA256 mismatch: {filename}")
    for name in ("partial_sequence", "documentary_fragment"):
        if summary.get(name) not in {"allow", "report_only"}:
            raise ValueError(f"Invalid resolver donor policy: {name}")
    counts = summary.get("decisions")
    if (
        not isinstance(counts, dict)
        or type(counts.get("ACCEPTED")) is not int
        or type(summary.get("annotation_donor_yes")) is not int
        or counts["ACCEPTED"] < 0
        or not 0 <= summary["annotation_donor_yes"] <= counts["ACCEPTED"]
    ):
        raise ValueError("Invalid resolver association counts")
    return summary


def _project(
    source: Path,
    destination: Path,
    releases: tuple[str, str],
    expected: int,
    expected_donors: int,
) -> tuple[int, int]:
    found = donated = 0
    previous: tuple[int, str, int] | None = None
    current_group: tuple[str, str] | None = None
    group_count = group_size = 0
    with (
        source.open(encoding="utf-8", newline="") as incoming,
        destination.open("x", encoding="utf-8", newline="") as outgoing,
    ):
        reader = csv.DictReader(incoming, delimiter="\t", quoting=csv.QUOTE_NONE)
        if tuple(reader.fieldnames or ()) != STAGE07_COLUMNS:
            raise ValueError("Invalid 19-column resolver contract")
        writer = csv.writer(
            outgoing, delimiter="\t", lineterminator="\n", quoting=csv.QUOTE_NONE
        )
        writer.writerow(ACCEPTED_COLUMNS)
        for record in reader:
            if None in record or any(item is None for item in record.values()):
                raise ValueError("Malformed resolver ACCEPTED row")
            if record["decision"] != "ACCEPTED":
                raise ValueError("Non-ACCEPTED row in resolver projection")
            association = DonorAssociation(
                *(record[field] for field in ACCEPTED_COLUMNS)
            )
            if association.release not in releases:
                raise ValueError("Unknown release in resolver output")
            order = (
                releases.index(association.release),
                association.target_id,
                int(association.rid),
            )
            if previous is not None and order <= previous:
                raise ValueError("Duplicate or unsorted resolver projection")
            previous = order
            group = association.release, association.target_id
            if group != current_group:
                if current_group is not None and group_count != group_size:
                    raise ValueError("ACCEPTED group multiplicity mismatch")
                current_group = group
                group_count = 0
                group_size = association.declared_multiplicity
            if group_size != association.declared_multiplicity:
                raise ValueError("Inconsistent ACCEPTED group multiplicity")
            writer.writerow(association.tsv_values())
            group_count += 1
            found += 1
            donated += int(association.may_donate)
    if current_group is not None and group_count != group_size:
        raise ValueError("Final ACCEPTED group multiplicity mismatch")
    if (found, donated) != (expected, expected_donors):
        raise ValueError("Resolver accepted/donor count mismatch")
    return found, donated


def publish_stage07_adapter(
    *,
    resolver: Path,
    releases: dict[str, str],
    policy: str,
    destination: Path,
) -> dict:
    """Create a new manifest-attested Stage 07 bundle, never overwrite."""
    summary = _check(resolver, policy, releases)
    if destination.exists() or destination.is_symlink():
        raise FileExistsError("Stage 07 adapter destination occupied")
    if not destination.parent.is_dir() or destination.parent.is_symlink():
        raise ValueError("Stage 07 adapter parent missing or symlinked")
    if destination == resolver or destination in resolver.parents:
        raise ValueError("Stage 07 source/destination cannot alias")
    if resolver in destination.parents:
        raise ValueError("Stage 07 output cannot be nested in source")
    policies = {
        "resolver": {"strain_policy": policy},
        "annotation_donor": {
            "partial_sequence": summary["partial_sequence"],
            "documentary_fragment": summary["documentary_fragment"],
        },
    }
    selection = {
        "schema_version": "stage07-selection-metadata-v1",
        "stage": "07",
        "policy_version": VERSION,
        "releases": releases,
        "effective_policies": policies,
        "stage08_contract": {
            "association_source": ACCEPTED,
            "go_donor_criterion": "annotation_donor=YES",
            "preserve_nondonors": True,
            "preserve_audit_origin": True,
        },
        "source": {
            "type": "canonical Stage 2C resolved associations",
            "accepted_sha256": _sha(resolver / SOURCE),
            "policy": policy,
        },
    }
    inputs = tuple(
        _artifact(resolver / name)
        for name in (SOURCE, SOURCE_COMPLETE, SOURCE_SUMMARY)
    )
    with atomic_stage_bundle(
        destination,
        stage="07",
        role=None,
        release_id=None,
        parameters={
            "start_release_id": releases["START"],
            "end_release_id": releases["END"],
            "policy_version": VERSION,
            "policies": policies,
            "bridge_only": True,
        },
        ontology=None,
        implementation={"name": "canonical-v2-stage07-projection", "version": VERSION},
        inputs=inputs,
        hash_outputs=True,
    ) as staged:
        accepted, donors = _project(
            resolver / SOURCE,
            staged / ACCEPTED,
            (releases["START"], releases["END"]),
            summary["decisions"]["ACCEPTED"],
            summary["annotation_donor_yes"],
        )
        (staged / SELECTION).write_text(
            json.dumps(selection, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        for artifact in inputs:
            if _sha(Path(artifact.path)) != artifact.sha256:
                raise ValueError("Resolver source changed during projection")
    manifest = validate_bundle(destination, deep=False)
    if manifest.stage != "07" or manifest.status != "complete":
        raise ValueError("Stage 07 adapter publication incomplete")
    index = read_accepted_associations(
        destination / ACCEPTED,
        start_release_id=releases["START"],
        end_release_id=releases["END"],
        source_database="UniProtKB",
    )
    if len(index.associations) != accepted:
        raise ValueError("Stage 07 donor index changed after publication")
    return {"accepted": accepted, "donors": donors, "policy": policy}
