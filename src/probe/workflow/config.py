"""Validate workflow configuration without reading biological inputs."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Any

ROLES = ("START", "END")

STAGE03_PARAMETER_KEYS = (
    "k",
    "max_required_shared",
    "min_target_kmer_fraction",
    "fragment_policy",
    "taxonomy_policy",
)

# Reference validated policy. Stage03 parameters listed above are tunable;
# every other scientific field remains frozen.
FROZEN_SCIENTIFIC = {
    "policy_version": "pre-resolver-v2-frozen-v1",
    "target_policy": "protein-sequence-v1",
    "adapter_sequence_policy": "literal_no_repair",
    "go_policy": "probe-goa-normalization-policy-v1",
    "closure_relations": [
        "http://www.w3.org/2000/01/rdf-schema#subClassOf",
        "http://purl.obolibrary.org/obo/BFO_0000050",
    ],
    "sequence": {
        "k": 7,
        "max_required_shared": 10,
        "min_target_kmer_fraction": 0.25,
        "fragment_policy": "exclude",
        "taxonomy_policy": "rank_aware",
        "primary_universe": "GO_POSITIVE_V2",
        "rescue_universe": "FULL_V2",
        "rescue_trigger": "BASELINE_UNCOVERED_AND_K_SEARCHABLE",
        "rescue_algorithm": "SAME_AS_STAGE_03",
    },
    "lineage": {
        "sources": ["TARGET_ACCESSION", "ELIGIBLE_DIRECT_ANCHORS"],
        "key_types": ["ACCESSION", "ORDERED_LOCUS", "ORF"],
        "recursive": False,
        "sequence_candidates_donate_keys": False,
    },
    "union": {
        "key": ["release", "target_id", "rid"],
        "provenance": ["GO_POSITIVE_SEQUENCE", "FULL_V2_RESCUE_SEQUENCE", "LINEAGE"],
    },
    "taxonomy_audit": {
        "primary": False,
        "same_species_wrong_subtaxon_alignment": True,
        "cross_species_alignment": False,
    },
    "alignment": {
        "modes": ["TARGET_GLOBAL", "CANDIDATE_GLOBAL"],
        "algorithm": "AFFINE_FULL_DP",
        "banding": False,
        "match": 2,
        "mismatch": -1,
        "gap_open": -3,
        "gap_extend": -1,
        "gap_cost": "open_plus_length_minus_one_extensions",
        "traceback_policy": "reference_06C0",
        "exact_pairs": "MATHEMATICAL_NO_DP",
    },
}


def _keys(value: Any, required: set[str], label: str) -> dict:
    if not isinstance(value, dict) or set(value) != required:
        raise ValueError(f"{label}: expected keys {sorted(required)}")
    return value


def _text(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label}: expected nonempty string")
    return value


def _paths(value: Any, label: str) -> list[str]:
    if not isinstance(value, list) or not value:
        raise ValueError(f"{label}: expected nonempty path list")
    return [_text(item, label) for item in value]


def _int_range(
    value: Any,
    label: str,
    minimum: int,
    maximum: int,
) -> int:
    if (
        type(value) is not int
        or value < minimum
        or value > maximum
    ):
        raise ValueError(
            f"{label}: expected integer in range "
            f"{minimum}..{maximum}"
        )
    return value


def _float_range(
    value: Any,
    label: str,
    minimum: float,
    maximum: float,
) -> float:
    if (
        type(value) is not float
        or not minimum <= value <= maximum
    ):
        raise ValueError(
            f"{label}: expected floating-point value in range "
            f"{minimum:.2f}..{maximum:.2f}"
        )
    return value


def _choice(
    value: Any,
    label: str,
    allowed: set[str],
) -> str:
    if type(value) is not str or value not in allowed:
        raise ValueError(
            f"{label}: expected one of {sorted(allowed)}"
        )
    return value


def _overlap(a: Path, b: Path) -> bool:
    return a == b or a in b.parents or b in a.parents


@dataclass(frozen=True)
class PipelineConfig:
    """Validated configuration; no scheduling, state, or restart logic."""

    data: dict[str, Any]

    @classmethod
    def from_mapping(cls, mapping: dict[str, Any]) -> PipelineConfig:
        data = deepcopy(mapping)
        _keys(
            data,
            {
                "schema_version",
                "targets",
                "releases",
                "ontology",
                "locations",
                "resources",
                "validation",
                "scientific",
            },
            "config",
        )
        if type(data["schema_version"]) is not int or data["schema_version"] != 1:
            raise ValueError("unsupported config schema_version")
        targets = _keys(
            data["targets"], {"mode", "fasta", "metadata", "adapter"}, "targets"
        )
        if targets["mode"] == "canonical":
            _text(targets["fasta"], "targets.fasta")
            _text(targets["metadata"], "targets.metadata")
            if targets["adapter"] is not None:
                raise ValueError("canonical mode must not specify an adapter")
        elif targets["mode"] == "adapter":
            adapter = _keys(
                targets["adapter"],
                {
                    "name",
                    "version",
                    "implementation",
                    "inputs",
                    "taxid_contract",
                    "id_policy",
                },
                "adapter",
            )
            for key in set(adapter) - {"inputs"}:
                _text(adapter[key], f"adapter.{key}")
            _paths(adapter["inputs"], "adapter.inputs")
        else:
            raise ValueError("targets.mode must be canonical or adapter")
        releases = _keys(data["releases"], set(ROLES), "releases")
        for role, release in releases.items():
            _keys(release, {"release_id", "uniprot_sources", "goa", "taxonomy"}, role)
            _text(release["release_id"], f"{role}.release_id")
            _paths(release["uniprot_sources"], f"{role}.uniprot_sources")
            _text(release["goa"], f"{role}.goa")
            tax = _keys(
                release["taxonomy"],
                {"snapshot_id", "nodes", "names", "merged", "deleted"},
                f"{role}.taxonomy",
            )
            for key, value in tax.items():
                _text(value, f"{role}.taxonomy.{key}")
        if releases["START"]["release_id"] == releases["END"]["release_id"]:
            raise ValueError("START and END release IDs must be distinct")
        ontology = _keys(
            data["ontology"],
            {
                "path",
                "expected_sha256",
                "canonical_url",
                "original_url",
                "retrieval_date",
                "version_iri",
            },
            "ontology",
        )
        _text(ontology["path"], "ontology.path")

        expected_sha256 = ontology["expected_sha256"]
        if expected_sha256 is not None:
            _text(expected_sha256, "ontology.expected_sha256")
            if len(expected_sha256) != 64 or any(
                character not in "0123456789abcdefABCDEF"
                for character in expected_sha256
            ):
                raise ValueError(
                    "ontology.expected_sha256 must be null or a 64-digit SHA256"
                )

        for key in (
            "canonical_url",
            "original_url",
            "retrieval_date",
            "version_iri",
        ):
            if ontology[key] is not None:
                _text(ontology[key], f"ontology.{key}")
        import json

        scientific = _keys(
            data["scientific"],
            set(FROZEN_SCIENTIFIC),
            "scientific",
        )
        sequence = _keys(
            scientific["sequence"],
            set(FROZEN_SCIENTIFIC["sequence"]),
            "scientific.sequence",
        )

        _int_range(
            sequence["k"],
            "scientific.sequence.k",
            4,
            8,
        )
        _int_range(
            sequence["max_required_shared"],
            "scientific.sequence.max_required_shared",
            1,
            65535,
        )
        _float_range(
            sequence["min_target_kmer_fraction"],
            "scientific.sequence.min_target_kmer_fraction",
            0.20,
            1.00,
        )
        _choice(
            sequence["fragment_policy"],
            "scientific.sequence.fragment_policy",
            {"exclude", "include"},
        )
        _choice(
            sequence["taxonomy_policy"],
            "scientific.sequence.taxonomy_policy",
            {"rank_aware", "strict_taxon"},
        )

        frozen_view = deepcopy(scientific)
        for key in STAGE03_PARAMETER_KEYS:
            frozen_view["sequence"][key] = (
                FROZEN_SCIENTIFIC["sequence"][key]
            )

        if json.dumps(frozen_view, sort_keys=True) != json.dumps(
            FROZEN_SCIENTIFIC,
            sort_keys=True,
        ):
            raise ValueError(
                "non-tunable scientific parameters differ from "
                "frozen validated policy"
            )
        resources = _keys(data["resources"], {"default", "stages"}, "resources")
        _keys(
            resources["default"],
            {"threads", "mem_mb", "runtime_minutes"},
            "resources.default",
        )
        if not isinstance(resources["stages"], dict):
            raise ValueError("resources.stages must be a mapping")
        for label, values in [
            ("default", resources["default"]),
            *resources["stages"].items(),
        ]:
            _keys(
                values, {"threads", "mem_mb", "runtime_minutes"}, f"resources.{label}"
            )
            for value in values.values():
                if type(value) is not int or value <= 0:
                    raise ValueError("resource values must be positive integers")
        validation = _keys(data["validation"], {"deep_checksums"}, "validation")
        if type(validation["deep_checksums"]) is not bool:
            raise ValueError("deep_checksums must be boolean")
        locations = _keys(data["locations"], {"work", "output", "logs"}, "locations")
        roots = [Path(_text(p, "location")).resolve() for p in locations.values()]
        repo = Path(__file__).resolve().parents[3]
        for root in roots:
            if "TESTING" in root.parts or any(
                _overlap(root, repo / name)
                for name in (
                    "src",
                    "tests",
                    "config",
                    "workflow",
                    "docs",
                    ".git",
                    "reference",
                )
            ):
                raise ValueError(
                    "output location overlaps protected repository content"
                )
        if any(_overlap(a, b) for i, a in enumerate(roots) for b in roots[i + 1 :]):
            raise ValueError("work/output/log locations must not overlap")
        parsed = cls(data)
        inputs = parsed.target_inputs() + [ontology["path"]]
        for release in releases.values():
            inputs += release["uniprot_sources"] + [release["goa"]]
            inputs += [v for k, v in release["taxonomy"].items() if k != "snapshot_id"]
        if any(_overlap(root, Path(p).resolve()) for root in roots for p in inputs):
            raise ValueError("output locations must not overlap input paths")
        return parsed

    @property
    def uses_adapter(self) -> bool:
        return self.data["targets"]["mode"] == "adapter"

    def target_inputs(self) -> list[str]:
        targets = self.data["targets"]
        if self.uses_adapter:
            adapter = targets["adapter"]
            return [*adapter["inputs"], adapter["implementation"]]
        return [targets["fasta"], targets["metadata"]]

    def release(self, role: str) -> dict[str, Any]:
        if role not in ROLES:
            raise ValueError(f"unknown release role: {role}")
        return self.data["releases"][role]

    def stage03_parameters(self) -> dict[str, Any]:
        sequence = self.data["scientific"]["sequence"]
        return {
            key: sequence[key]
            for key in STAGE03_PARAMETER_KEYS
        }

    def resources(self, stage: str) -> dict[str, int]:
        resources = self.data["resources"]
        return dict(resources["stages"].get(stage, resources["default"]))
