"""Versioned, independently configurable evidence-code selections for Stage 08.

This module does not interpret GO terms, propagate annotations, or read GAFs.
It never changes the immutable Stage 08 raw collection.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any

REGISTRY_SCHEMA = "probe-evidence-registry-v1"
SELECTION_SCHEMA = "probe-evidence-selections-v1"
PURPOSES = ("score", "prior", "neutral", "not")


def _object(value: Any, label: str, expected: set[str]) -> dict:
    if not isinstance(value, dict) or set(value) != expected:
        raise ValueError(f"{label}: expected exactly {sorted(expected)}")
    return value


def _text(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        raise ValueError(f"{label}: expected nonempty, trimmed text")
    return value


def _code(value: Any, label: str) -> str:
    value = _text(value, label)
    if any(character.isspace() for character in value):
        raise ValueError(f"{label}: evidence code may not contain whitespace")
    return value


def _codes(value: Any, label: str) -> frozenset[str]:
    if not isinstance(value, list):
        raise ValueError(f"{label}: expected a list")
    observed = [_code(item, label) for item in value]
    if len(observed) != len(set(observed)):
        raise ValueError(f"{label}: duplicate entry")
    return frozenset(observed)


def _fingerprint(document: dict) -> str:
    canonical = json.dumps(
        document,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    )
    return hashlib.sha256(canonical.encode("ascii")).hexdigest()


def _no_duplicate_keys(items: list[tuple[str, Any]]) -> dict:
    result = {}
    for name, value in items:
        if name in result:
            raise ValueError(f"Duplicate JSON object key: {name}")
        result[name] = value
    return result


def read_json_object(path: Path) -> dict:
    """Read a JSON configuration, rejecting duplicate keys and invalid roots."""
    with path.open("r", encoding="utf-8") as stream:
        data = json.load(stream, object_pairs_hook=_no_duplicate_keys)
    if not isinstance(data, dict):
        raise ValueError("Configuration root must be an object")
    return data


@dataclass(frozen=True)
class EvidenceDefinition:
    code: str
    source: str
    description: str


@dataclass(frozen=True)
class EvidenceRegistry:
    registry_id: str
    origin: str
    definitions: Mapping[str, EvidenceDefinition]
    groups: Mapping[str, frozenset[str]]
    fingerprint: str

    @classmethod
    def from_mapping(cls, value: dict) -> EvidenceRegistry:
        record = _object(
            value,
            "registry",
            {"schema_version", "registry_id", "origin", "codes", "groups"},
        )
        if record["schema_version"] != REGISTRY_SCHEMA:
            raise ValueError("Unsupported registry schema")
        registry_id = _text(record["registry_id"], "registry_id")
        origin = _text(record["origin"], "origin")
        if not isinstance(record["codes"], dict) or not record["codes"]:
            raise ValueError("Registry requires at least one registered code")
        definitions = {}
        for code, raw in record["codes"].items():
            _code(code, "registered code")
            entry = _object(raw, f"code {code}", {"source", "description"})
            source = _text(entry["source"], f"{code}.source")
            description = _text(entry["description"], f"{code}.description")
            definitions[code] = EvidenceDefinition(code, source, description)
        if not isinstance(record["groups"], dict):
            raise ValueError("groups must be an object")
        groups = {}
        for name, members in record["groups"].items():
            _text(name, "group name")
            group_codes = _codes(members, f"group {name}")
            unknown = group_codes - definitions.keys()
            if unknown:
                raise ValueError(f"group {name}: unregistered codes {sorted(unknown)}")
            groups[name] = group_codes
        semantic = {
            "schema_version": REGISTRY_SCHEMA,
            "registry_id": registry_id,
            "origin": origin,
            "codes": {
                code: {
                    "source": item.source,
                    "description": item.description,
                }
                for code, item in sorted(definitions.items())
            },
            "groups": {
                name: sorted(members) for name, members in sorted(groups.items())
            },
        }
        return cls(
            registry_id,
            origin,
            MappingProxyType(definitions),
            MappingProxyType(groups),
            _fingerprint(semantic),
        )

    @classmethod
    def from_json(cls, path: Path) -> EvidenceRegistry:
        return cls.from_mapping(read_json_object(path))

    def unregistered_observed(self, codes: set[str]) -> tuple[str, ...]:
        """Preserve unknown observations; return an explicit review list."""
        return tuple(sorted(codes - self.definitions.keys()))

    def require_observed_reviewed(self, codes: set[str]) -> None:
        unknown = self.unregistered_observed(codes)
        if unknown:
            raise ValueError(f"Observed evidence codes need review: {unknown}")


@dataclass(frozen=True)
class CompiledEvidenceProfile:
    name: str
    allowed_codes: frozenset[str]
    fingerprint: str

    def accepts(self, observed: set[str] | frozenset[str]) -> bool:
        return not self.allowed_codes.isdisjoint(observed)


@dataclass(frozen=True)
class EvidenceSelections:
    registry_fingerprint: str
    by_purpose: Mapping[str, CompiledEvidenceProfile]
    fingerprint: str

    def unobserved_selected(self, observed: set[str]) -> dict[str, tuple[str, ...]]:
        """Informational only: a registered code need not occur in this release."""
        return {
            purpose: tuple(sorted(profile.allowed_codes - observed))
            for purpose, profile in self.by_purpose.items()
        }


def compile_selections(registry: EvidenceRegistry, value: dict) -> EvidenceSelections:
    """Compile four separate scopes, without implicit scientific defaults."""
    cfg = _object(
        value,
        "selections",
        {"schema_version", "registry_id", "profiles", "purposes"},
    )
    if cfg["schema_version"] != SELECTION_SCHEMA:
        raise ValueError("Unsupported selection schema")
    if cfg["registry_id"] != registry.registry_id:
        raise ValueError("Selection references a different evidence registry")
    if not isinstance(cfg["profiles"], dict):
        raise ValueError("profiles must be an object")
    definitions = cfg["profiles"]
    purposes = _object(cfg["purposes"], "purposes", set(PURPOSES))
    compiled = {}
    for name, raw in definitions.items():
        _text(name, "profile name")
        profile = _object(
            raw,
            f"profile {name}",
            {"include_groups", "include_codes", "exclude_codes"},
        )
        named_groups = _codes(profile["include_groups"], "include_groups")
        missing_groups = named_groups - registry.groups.keys()
        if missing_groups:
            raise ValueError(f"Unregistered groups: {sorted(missing_groups)}")
        include = _codes(profile["include_codes"], "include_codes")
        exclude = _codes(profile["exclude_codes"], "exclude_codes")
        mentioned = include | exclude
        unknown_codes = mentioned - registry.definitions.keys()
        if unknown_codes:
            raise ValueError(f"Unregistered codes: {sorted(unknown_codes)}")
        expanded = set(include)
        for group in named_groups:
            expanded.update(registry.groups[group])
        allowed = frozenset(expanded - exclude)
        compiled[name] = CompiledEvidenceProfile(
            name,
            allowed,
            _fingerprint(
                {
                    "registry_fingerprint": registry.fingerprint,
                    "allowed_codes": sorted(allowed),
                }
            ),
        )
    resolved = {}
    for purpose, profile_name in purposes.items():
        selected_name = _text(profile_name, f"purposes.{purpose}")
        if selected_name not in compiled:
            raise ValueError(f"Missing profile {selected_name} for {purpose}")
        resolved[purpose] = compiled[selected_name]
    frozen = MappingProxyType(resolved)
    document = {
        "schema_version": SELECTION_SCHEMA,
        "registry_fingerprint": registry.fingerprint,
        "purposes": {
            purpose: sorted(frozen[purpose].allowed_codes)
            for purpose in sorted(PURPOSES)
        },
    }
    return EvidenceSelections(registry.fingerprint, frozen, _fingerprint(document))
