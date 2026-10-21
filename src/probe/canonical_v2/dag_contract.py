"""Explicit, release-local input contract for the new canonical V2 DAG.

Source 03/04 scanner publications are upstream inputs, not old Stage 05
outputs. Nothing here authorizes a donor or mixes START/END GO annotations.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

VERSION = "probe-canonical-v2-dag-input-v2"
SCIENTIFIC_CONTRACTS = {
    "exact_identity": "literal-iupac26-v1",
    "nonexact_admission": "same-resolved-taxid-documentary-literal-v1",
    "dp_backend": "certified-geometric-bounds-v2",
    "dp_scoring": "literal-match2-mismatch-1-affine-open-3-extend-1-v1",
}
ROLES = ("START", "END")
RELEASE_INPUTS = frozenset(
    {
        "unified_discovery",
        "go_positive_pairs",
        "full_exact_pairs",
        "lineage_pairs",
        "rescue_pairs",
        "taxonomy",
        "anchors",
        "lineage",
        "ox_taxonomy",
        "full_manifest",
        "goa_bundle",
    }
)
COMMON_INPUTS = frozenset(
    {
        "targets",
        "targets_input",
        "ontology",
        "evidence_registry",
        "temporal_policy",
        "lineage_index",
        "go_policy_bundle",
    }
)
TOOLS = frozenset(
    {
        "stage05c2_cli",
        "stage06a_cli",
        "nonexact_script",
        "full_dp_binary",
        "finalize_script",
        "collection_cli",
        "m10c_cli",
        "temporal_cli",
    }
)


def _path(value: object, label: str) -> Path:
    if not isinstance(value, str) or not value:
        raise ValueError(f"Missing canonical path {label}")
    path = Path(value)
    if not path.is_absolute() or ".." in path.parts:
        raise ValueError(f"Non-absolute or unsafe canonical path {label}")
    return path


def validate_scientific_contracts(spec: dict) -> None:
    """Require explicit revised science; old specifications are not upgraded."""
    if spec.get("schema") != VERSION:
        raise ValueError("Unsupported canonical V2 scientific contract version")
    contracts = spec.get("scientific_contracts")
    if not isinstance(contracts, dict) or contracts != SCIENTIFIC_CONTRACTS:
        raise ValueError("Missing or unsupported canonical scientific contracts")


def read_dag_spec(path: Path) -> dict:
    """Reject unknown options, missing release identities and unsafe roots."""
    if not path.is_file() or path.is_symlink():
        raise ValueError("Missing or unsafe canonical V2 DAG specification")
    data = json.loads(path.read_text(encoding="utf-8"))
    required_keys = {
        "schema",
        "mode",
        "policy",
        "releases",
        "output_root",
        "inputs",
        "per_release",
        "tools",
        "limits",
        "scientific_contracts",
    }
    if (
        not isinstance(data, dict)
        or not required_keys.issubset(data)
        or set(data) - required_keys - {"content_identity_sha256"}
    ):
        raise ValueError("Unexpected canonical V2 DAG specification keys")
    if "content_identity_sha256" in data:
        identity = data["content_identity_sha256"]
        if (
            not isinstance(identity, str)
            or len(identity) != 64
            or any(character not in "0123456789abcdef" for character in identity)
        ):
            raise ValueError("Invalid canonical scientific content identity SHA256")
    if data["schema"] != VERSION or data["mode"] not in {
        "prepare",
        "execute",
    }:
        raise ValueError("Invalid canonical V2 mode or contract version")
    validate_scientific_contracts(data)
    if data["policy"] not in {"strict", "same_species_exact"}:
        raise ValueError("Invalid EXACT donor policy")
    releases = data["releases"]
    if (
        not isinstance(releases, dict)
        or set(releases) != set(ROLES)
        or len(set(releases.values())) != 2
        or any(
            not isinstance(item, str) or not item or item != item.strip()
            for item in releases.values()
        )
    ):
        raise ValueError("Invalid START/END release identity")
    for name, expected in (
        ("inputs", COMMON_INPUTS),
        ("tools", TOOLS),
    ):
        value = data[name]
        if not isinstance(value, dict) or set(value) != expected:
            raise ValueError(f"Incomplete canonical {name} mapping")
    specific = data["per_release"]
    if not isinstance(specific, dict) or set(specific) != set(ROLES):
        raise ValueError("Missing release-local source mapping")
    for role in ROLES:
        if (
            not isinstance(specific[role], dict)
            or set(specific[role]) != RELEASE_INPUTS
        ):
            raise ValueError(f"Incomplete canonical sources for {role}")
    root = _path(data["output_root"], "output_root")
    if root == Path("/") or root.is_symlink():
        raise ValueError("Unsafe output root")
    for group in (data["inputs"], data["tools"], *specific.values()):
        for label, value in group.items():
            source = _path(value, label)
            if source == root or root in source.parents:
                raise ValueError("Canonical source lies inside output root")
    limits = data["limits"]
    required_limits = {
        "threads",
        "max_pair_cells",
        "max_total_cells",
        "max_chunk_cells",
        "partial_sequence",
        "documentary_fragment",
    }
    if not isinstance(limits, dict) or set(limits) != required_limits:
        raise ValueError("Incomplete canonical resource/eligibility limits")
    for label in ("threads", "max_pair_cells", "max_total_cells"):
        if type(limits[label]) is not int or limits[label] < 1:
            raise ValueError(f"Invalid canonical execution limit: {label}")
    chunk = limits["max_chunk_cells"]
    if chunk is not None and (type(chunk) is not int or chunk < 1):
        raise ValueError("Invalid optional DP chunk limit")
    for label in ("partial_sequence", "documentary_fragment"):
        if limits[label] not in ("allow", "report_only"):
            raise ValueError(f"Invalid scientific policy: {label}")
    return data


def discovery_fingerprint(spec: dict) -> str:
    """Pin discovery and DP independently of the donor policy and GOA."""
    params = {
        "schema": spec["schema"],
        "scientific_contracts": spec["scientific_contracts"],
        "releases": spec["releases"],
        "targets": spec["inputs"]["targets"],
        "lineage_index": spec["inputs"]["lineage_index"],
        "per_release": {
            role: {
                name: value
                for name, value in spec["per_release"][role].items()
                if name != "goa_bundle"
            }
            for role in ROLES
        },
        "tools": {
            name: value
            for name, value in spec["tools"].items()
            if name not in {"collection_cli", "m10c_cli", "temporal_cli"}
        },
        "dp_limits": {
            name: value
            for name, value in spec["limits"].items()
            if name not in {"partial_sequence", "documentary_fragment"}
        },
    }
    # Existing published V2 specifications retain their exact fingerprint.
    # Fresh executions opt into content-certified upstream/source identities.
    if "content_identity_sha256" in spec:
        params["content_identity_sha256"] = spec["content_identity_sha256"]
    encoded = json.dumps(params, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def policy_fingerprint(spec: dict) -> str:
    """Pin downstream GO decisions without invalidating EXACT discovery."""
    params = {
        key: value for key, value in spec.items() if key not in {"mode", "output_root"}
    }
    encoded = json.dumps(params, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def destination(spec: dict, stage: str, role: str | None = None) -> Path:
    """Version upstream independently of the downstream donor policy."""
    downstream = {"stage07", "m8", "m10c", "temporal"}
    digest = (
        policy_fingerprint(spec) if stage in downstream else discovery_fingerprint(spec)
    )
    kind = "policy" if stage in downstream else "discovery"
    root = Path(spec["output_root"]) / f"v2-{kind}-{digest[:20]}"
    if role is not None and role not in ROLES:
        raise ValueError("Foreign canonical release role")
    allowed = {
        "exact",
        "lookup",
        "ledger",
        "projection",
        "b3",
        "handoff",
        "workload",
        "alignment",
        "stage07",
        "m8",
        "m10c",
        "temporal",
    }
    if stage not in allowed:
        raise ValueError("Unknown canonical DAG stage")
    if (stage in {"exact", "lookup", "ledger", "projection"}) != (role is not None):
        raise ValueError("Canonical node role mismatch")
    suffix = (role,) if role is not None else ()
    return root.joinpath(stage, *suffix)
