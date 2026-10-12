"""Read-only preflight for Stage 08 collection inputs, not a GAF scanner.

Reuse the existing bundle validator. Hash the manifests and consumed Stage 07
payloads, but only stat the normalized GAFs. Their declared digests are NOT
independent content verification, and this preflight certifies no GO coverage.
"""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from probe.stage08_annotations import NormalizedGafSource
from probe.stage08_donors import DonorIndex, read_accepted_associations
from probe.workflow.artifacts import (
    MANIFEST_NAME,
    Artifact,
    StageManifest,
    sha256_file,
    validate_bundle,
)

INPUT_SCHEMA = "stage08-collection-inputs-v1"
ACCEPTED_NAME = "07_accepted_associations.tsv"
SELECTION_NAME = "07_selection_metadata.json"
GAF_NAME = "goa.normalized.gaf"
POLICY_NAME = "01B0_go_policy.tsv"
DONOR_DATABASE = "UniProtKB"

FileStamp = tuple[int, int, int, int, int]


def _stamp(path: Path) -> FileStamp:
    stat = path.stat()
    return (
        stat.st_dev,
        stat.st_ino,
        stat.st_size,
        stat.st_mtime_ns,
        stat.st_ctime_ns,
    )


def _canonical(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _identity(artifact: Artifact) -> dict[str, object]:
    """Content identity is not tied to the current mount path."""
    return {"sha256": artifact.sha256, "size_bytes": artifact.size_bytes}


def _unique_object(items: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in items:
        if key in result:
            raise ValueError(f"duplicate selection-metadata key: {key}")
        result[key] = value
    return result


def _output(manifest: StageManifest, name: str) -> Artifact:
    for item in manifest.outputs:
        if item.path == name:
            if item.sha256 is None:
                raise ValueError(f"missing declared SHA256: {manifest.stage}/{name}")
            return item
    raise ValueError(f"missing declared output: {manifest.stage}/{name}")


def _ontology(manifest: StageManifest) -> dict[str, Any]:
    if manifest.ontology is None:
        raise ValueError(f"missing ontology provenance: {manifest.stage}")
    value = dict(manifest.ontology)
    expected = value.get("expected_sha256")
    if expected is not None and expected != value["sha256"]:
        raise ValueError(
            f"ontology expected/observed SHA256 mismatch: {manifest.stage}"
        )
    return value


@dataclass(frozen=True, slots=True)
class GafCollectionInput:
    """One immutable-source declaration; no file contents are read here."""

    role: str
    source: NormalizedGafSource
    size_bytes: int
    upstream_manifest: Artifact
    policy_reference_path: str

    @property
    def artifact(self) -> Artifact:
        return Artifact(self.source.path, self.size_bytes, self.source.sha256)


@dataclass(frozen=True, slots=True)
class CollectionInputs:
    donors: DonorIndex
    stage07_manifest: Artifact
    accepted_associations: Artifact
    selection_metadata: Artifact
    policy_manifest: Artifact
    gafs: tuple[GafCollectionInput, GafCollectionInput]
    ontology_json: str
    fingerprint: str

    @property
    def provenance_inputs(self) -> tuple[Artifact, ...]:
        """Include GAF declarations, without claiming their contents were verified."""
        return (
            self.stage07_manifest,
            self.accepted_associations,
            self.selection_metadata,
            self.policy_manifest,
            *(item.upstream_manifest for item in self.gafs),
            *(item.artifact for item in self.gafs),
        )

    def report(self) -> dict[str, Any]:
        counts: Counter[str] = Counter()
        yes_counts: Counter[str] = Counter()
        for row in self.donors.associations:
            counts[row.release] += 1
            if row.may_donate:
                yes_counts[row.release] += 1
        return {
            "schema_version": INPUT_SCHEMA,
            "input_fingerprint": self.fingerprint,
            "associations_loaded": len(self.donors.associations),
            "donor_database": self.donors.source_database,
            "ontology": json.loads(self.ontology_json),
            "by_role": {
                item.role: {
                    "release": item.source.release,
                    "normalized_gaf": item.source.path,
                    "normalized_gaf_size_bytes": item.size_bytes,
                    "normalized_gaf_declared_sha256": item.source.sha256,
                    "normalized_gaf_content_check": "NOT_PERFORMED",
                    "policy_reference_recorded_path": item.policy_reference_path,
                    "accepted_associations": counts[item.source.release],
                    "donor_yes_associations": yes_counts[item.source.release],
                    "donor_no_associations": (
                        counts[item.source.release] - yes_counts[item.source.release]
                    ),
                    "unique_donor_accessions": len(
                        self.donors.donor_accessions(item.source.release)
                    ),
                }
                for item in self.gafs
            },
            "checks": {
                "bundle_schema_payload_names_and_sizes": "PASS",
                "consumed_stage07_payload_sha256": "PASS",
                "stage07_release_and_policy_consistency": "PASS",
                "goa_roles_releases_and_shared_ontology": "PASS",
                "goa_reference_to_supplied_01B0_manifest": "PASS",
                "required_input_stamps_unchanged_during_preflight": "PASS",
            },
            "normalized_gaf_contents_read": False,
            "annotation_coverage_certified": False,
            "recursive_upstream_validation": False,
        }


class _InputReader:
    def __init__(self) -> None:
        self.watched: dict[Path, FileStamp] = {}

    def watch(self, path: Path) -> None:
        self.watched.setdefault(path, _stamp(path))

    def unchanged(self) -> None:
        for path, previous in self.watched.items():
            if _stamp(path) != previous:
                raise ValueError(f"input changed during preflight: {path}")

    def bundle(
        self, path: Path, stage: str, role: str | None
    ) -> tuple[Path, StageManifest, Artifact]:
        path = Path(path)
        # Do not resolve the leaf symlink before the upstream validator sees it.
        self.watch(path / MANIFEST_NAME)
        manifest = validate_bundle(path, deep=False)
        if manifest.stage != stage or manifest.role != role:
            raise ValueError(f"unexpected bundle stage/role: expected {stage}/{role}")
        path = path.resolve()
        manifest_path = path / MANIFEST_NAME
        artifact = self.hashed_file(manifest_path)
        self.unchanged()
        return path, manifest, artifact

    def hashed_file(self, path: Path) -> Artifact:
        self.watch(path)
        result = Artifact(str(path), path.stat().st_size, sha256_file(path))
        self.unchanged()
        return result

    def verified_payload(
        self, root: Path, manifest: StageManifest, name: str
    ) -> Artifact:
        declared = _output(manifest, name)
        result = self.hashed_file(root / name)
        if _identity(result) != _identity(declared):
            raise ValueError(f"consumed payload SHA256/size mismatch: {name}")
        return result


def _selection(path: Path, stage07: StageManifest) -> tuple[str, str]:
    value = json.loads(
        path.read_text(encoding="utf-8"), object_pairs_hook=_unique_object
    )
    if not isinstance(value, dict):
        raise ValueError("selection metadata must be an object")
    if (
        value.get("schema_version") != "stage07-selection-metadata-v1"
        or value.get("stage") != "07"
    ):
        raise ValueError("unsupported Stage 07 selection metadata schema/stage")
    releases = value.get("releases")
    if not isinstance(releases, dict) or set(releases) != {"START", "END"}:
        raise ValueError("selection metadata must declare exactly START and END")
    for role in ("START", "END"):
        release = releases[role]
        if not isinstance(release, str) or not release or release != release.strip():
            raise ValueError("invalid selection-metadata release identifier")
        if stage07.parameters.get(f"{role.lower()}_release_id") != release:
            raise ValueError("Stage 07 manifest/selection release mismatch")
    if releases["START"] == releases["END"]:
        raise ValueError("START and END must identify distinct releases")
    policy_version = value.get("policy_version")
    if not isinstance(policy_version, str) or not policy_version:
        raise ValueError("missing Stage 07 selection policy version")
    if policy_version != stage07.parameters.get("policy_version"):
        raise ValueError("Stage 07 manifest/selection policy version mismatch")
    policies = value.get("effective_policies")
    if not isinstance(policies, dict) or not policies:
        raise ValueError("missing Stage 07 effective policies")
    if policies != stage07.parameters.get("policies"):
        raise ValueError("Stage 07 manifest/selection effective policy mismatch")
    contract = value.get("stage08_contract")
    if not isinstance(contract, dict) or (
        contract.get("association_source") != ACCEPTED_NAME
        or contract.get("go_donor_criterion") != "annotation_donor=YES"
        or contract.get("preserve_nondonors") is not True
        or contract.get("preserve_audit_origin") is not True
    ):
        raise ValueError("unsupported Stage 07 donor-consumption contract")
    return releases["START"], releases["END"]


def _policy_reference(manifest: StageManifest, policy: Artifact) -> str:
    matches = [
        item.path
        for item in manifest.inputs
        if Path(item.path).name == MANIFEST_NAME
        and _identity(item) == _identity(policy)
    ]
    if not matches:
        raise ValueError("01B1 does not reference the supplied 01B0 manifest identity")
    # Content identity permits relocation of unchanged bundles. Do not reopen
    # historical input paths or use current path spelling as scientific identity.
    return sorted(matches)[0]


def prepare_collection_inputs(
    *,
    stage07_bundle: Path,
    start_goa_bundle: Path,
    end_goa_bundle: Path,
    go_policy_bundle: Path,
) -> CollectionInputs:
    """Validate four existing bundles and prepare M1's complete donor index.

    No GAF, FASTA, ontology or evidence registry is opened. Existing validators
    check declared payload names/sizes; only consumed Stage 07 payloads and the
    four manifests are hashed. Verify normalized-GAF contents during collection
    or by a separately requested deep-integrity check, not by calling this again.
    """
    reader = _InputReader()
    root07, manifest07, artifact07 = reader.bundle(stage07_bundle, "07", None)
    accepted = reader.verified_payload(root07, manifest07, ACCEPTED_NAME)
    selection = reader.verified_payload(root07, manifest07, SELECTION_NAME)
    start_id, end_id = _selection(Path(selection.path), manifest07)
    donors = read_accepted_associations(
        Path(accepted.path),
        start_release_id=start_id,
        end_release_id=end_id,
        source_database=DONOR_DATABASE,
    )
    policy_root, policy, policy_artifact = reader.bundle(go_policy_bundle, "01B0", None)
    _output(policy, POLICY_NAME)
    reader.watch(policy_root / POLICY_NAME)
    ontology = _ontology(policy)
    if (
        manifest07.ontology is not None
        and _ontology(manifest07)["sha256"] != ontology["sha256"]
    ):
        raise ValueError("Stage 07/01B0 ontology mismatch")
    gafs: list[GafCollectionInput] = []
    for role, release_id, root in (
        ("START", start_id, start_goa_bundle),
        ("END", end_id, end_goa_bundle),
    ):
        root, manifest, manifest_artifact = reader.bundle(root, "01B1", role)
        if manifest.release_id != release_id:
            raise ValueError(f"01B1/Stage 07 release mismatch for {role}")
        if _ontology(manifest)["sha256"] != ontology["sha256"]:
            raise ValueError(f"01B1/01B0 ontology mismatch for {role}")
        policy_reference = _policy_reference(manifest, policy_artifact)
        payload = _output(manifest, GAF_NAME)
        path = root / GAF_NAME
        reader.watch(path)
        # _output() requires a digest; Artifact.validate() checks its syntax.
        assert payload.sha256 is not None
        gafs.append(
            GafCollectionInput(
                role,
                NormalizedGafSource(release_id, str(path), payload.sha256),
                payload.size_bytes,
                manifest_artifact,
                policy_reference,
            )
        )
    reader.unchanged()
    fingerprint_payload = {
        "schema_version": INPUT_SCHEMA,
        "donor_database": DONOR_DATABASE,
        "stage07_manifest": _identity(artifact07),
        "accepted_associations": _identity(accepted),
        "selection_metadata": _identity(selection),
        "go_policy_manifest": _identity(policy_artifact),
        "gafs": [
            {
                "role": item.role,
                "release": item.source.release,
                "manifest": _identity(item.upstream_manifest),
                "payload": _identity(item.artifact),
            }
            for item in gafs
        ],
    }
    fingerprint = hashlib.sha256(_canonical(fingerprint_payload).encode()).hexdigest()
    return CollectionInputs(
        donors,
        artifact07,
        accepted,
        selection,
        policy_artifact,
        (gafs[0], gafs[1]),
        _canonical(ontology),
        fingerprint,
    )
