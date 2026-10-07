import importlib.util
from pathlib import Path
from types import SimpleNamespace

from probe.taxonomy import SpeciesAnchorStatus

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "pipeline" / "04_classify_direct_anchors.py"

spec = importlib.util.spec_from_file_location(
    "probe_direct_anchor_classification",
    SCRIPT,
)
assert spec is not None
assert spec.loader is not None

module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class FakeIndex:
    def __init__(
        self,
        *,
        resolved_taxid: str | None,
        species_taxid: str | None,
        rank: str = "subspecies",
    ) -> None:
        self.resolved_taxid = resolved_taxid
        self.species_taxid = species_taxid
        self.rank = rank

    def species_anchor(self, raw_taxid: str):
        del raw_taxid

        resolution = SimpleNamespace(
            resolved_taxid=self.resolved_taxid,
            status=SimpleNamespace(
                value="CURRENT" if self.resolved_taxid else "UNRESOLVED"
            ),
        )

        species_anchor = (
            SimpleNamespace(taxid=self.species_taxid)
            if self.species_taxid
            else None
        )

        return SimpleNamespace(
            resolution=resolution,
            species_anchor=species_anchor,
            status=(
                SpeciesAnchorStatus.RESOLVED
                if self.species_taxid
                else SpeciesAnchorStatus.UNRESOLVED
            ),
        )

    def node(self, taxid: str):
        if not taxid:
            return None
        return SimpleNamespace(rank=self.rank)


def species_target() -> dict[str, str]:
    return {
        "resolved_taxid": "9606",
        "species_taxid": "9606",
        "primary_bucket": "S:9606",
    }


def subtaxon_target() -> dict[str, str]:
    return {
        "resolved_taxid": "63221",
        "species_taxid": "9606",
        "primary_bucket": "T:63221",
    }


def test_rank_aware_accepts_same_species_different_subtaxon():
    index = FakeIndex(
        resolved_taxid="12345",
        species_taxid="9606",
    )

    result = module.classify_anchor(
        index,
        species_target(),
        "12345",
        "rank_aware",
    )

    assert result["taxonomy_relation"] == "SAME_SPECIES_DIFFERENT_SUBTAXON"
    assert result["key_source_eligible"] == "1"
    assert result["key_source_reason"] == "ELIGIBLE_SAME_SPECIES"


def test_strict_taxon_rejects_same_species_different_taxon():
    index = FakeIndex(
        resolved_taxid="12345",
        species_taxid="9606",
    )

    result = module.classify_anchor(
        index,
        species_target(),
        "12345",
        "strict_taxon",
    )

    assert result["taxonomy_relation"] == "SAME_SPECIES_DIFFERENT_SUBTAXON"
    assert result["key_source_eligible"] == "0"
    assert result["key_source_reason"] == "SAME_SPECIES_DIFFERENT_TAXON"


def test_strict_taxon_accepts_exact_resolved_taxid():
    index = FakeIndex(
        resolved_taxid="9606",
        species_taxid="9606",
        rank="species",
    )

    result = module.classify_anchor(
        index,
        species_target(),
        "9606",
        "strict_taxon",
    )

    assert result["taxonomy_relation"] == "SAME_TAXON"
    assert result["key_source_eligible"] == "1"
    assert result["key_source_reason"] == "ELIGIBLE_SAME_TAXON"


def test_rank_aware_below_species_requires_exact_taxon():
    index = FakeIndex(
        resolved_taxid="12345",
        species_taxid="9606",
    )

    result = module.classify_anchor(
        index,
        subtaxon_target(),
        "12345",
        "rank_aware",
    )

    assert result["taxonomy_relation"] == "SAME_SPECIES_DIFFERENT_SUBTAXON"
    assert result["key_source_eligible"] == "0"
    assert result["key_source_reason"] == "SAME_SPECIES_WRONG_SUBTAXON"


def test_multi_ox_is_ineligible_for_both_policies():
    index = FakeIndex(
        resolved_taxid="9606",
        species_taxid="9606",
    )

    for policy in ("rank_aware", "strict_taxon"):
        result = module.classify_anchor(
            index,
            species_target(),
            "9606,10090",
            policy,
        )

        assert result["taxonomy_relation"] == "UNRESOLVED"
        assert result["key_source_eligible"] == "0"
        assert result["key_source_reason"] == "MULTIPLE_OR_MISSING_OX"
