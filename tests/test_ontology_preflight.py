from __future__ import annotations

import hashlib

import pytest

from probe import (
    GOAspect,
    OntologyPreflightError,
    OntologyPreflightPolicy,
    OwlLoader,
    ReachabilityFinding,
    preflight_ontology,
)


def _context(tmp_path, body: str):
    path = tmp_path / "go.owl"
    path.write_text(
        f"""<?xml version="1.0"?>
<rdf:RDF
  xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#"
  xmlns:rdfs="http://www.w3.org/2000/01/rdf-schema#"
  xmlns:owl="http://www.w3.org/2002/07/owl#"
  xmlns:obo="http://purl.obolibrary.org/obo/"
  xmlns:oboInOwl="http://www.geneontology.org/formats/oboInOwl#">
  <owl:Ontology rdf:about="http://purl.obolibrary.org/obo/go.owl">
    <owl:versionIRI rdf:resource="http://purl.obolibrary.org/obo/go/releases/2025-02-06/go.owl"/>
  </owl:Ontology>
  {body}
</rdf:RDF>
""",
        encoding="utf-8",
    )
    return OwlLoader().load_context(
        path,
        expected_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
        source_url="http://purl.obolibrary.org/obo/go.owl",
        retrieval_date="2026-09-18",
        strict=False,
    )


def _go_class(identifier: str, namespace: str, expressions: str = "") -> str:
    return f"""
  <owl:Class rdf:about="http://purl.obolibrary.org/obo/{identifier.replace(":", "_")}">
    <oboInOwl:hasOBONamespace>{namespace}</oboInOwl:hasOBONamespace>
    {expressions}
  </owl:Class>
"""


def _part_of(parent: str) -> str:
    parent_iri = parent.replace(":", "_")
    return f"""
    <rdfs:subClassOf>
      <owl:Restriction>
        <owl:onProperty rdf:resource="http://purl.obolibrary.org/obo/BFO_0000050"/>
        <owl:someValuesFrom rdf:resource="http://purl.obolibrary.org/obo/{parent_iri}"/>
      </owl:Restriction>
    </rdfs:subClassOf>
"""


def _is_a(parent: str) -> str:
    return (
        '<rdfs:subClassOf rdf:resource="http://purl.obolibrary.org/obo/'
        f'{parent.replace(":", "_")}"/>'
    )


def test_preflight_separates_is_a_part_of_only_and_cross_aspect_paths(tmp_path):
    body = "".join(
        [
            _go_class("GO:0008150", "biological_process"),
            _go_class("GO:0003674", "molecular_function"),
            _go_class("GO:0005575", "cellular_component"),
            _go_class("GO:0000001", "biological_process", _is_a("GO:0008150")),
            _go_class("GO:0000002", "biological_process", _part_of("GO:0008150")),
            _go_class(
                "GO:0000003",
                "biological_process",
                _is_a("GO:0008150") + _part_of("GO:0003674"),
            ),
        ]
    )
    report = preflight_ontology(
        _context(tmp_path, body),
        policy=OntologyPreflightPolicy.synthetic_fixture(),
        strict=True,
    )
    reachability = {item.term_id: item for item in report.reachability}

    assert reachability["GO:0008150"].findings == {ReachabilityFinding.ROOT_TERM}
    assert (
        ReachabilityFinding.VALID_IS_A_ROOT_PATH in reachability["GO:0000001"].findings
    )
    assert (
        ReachabilityFinding.NO_IS_A_ROOT_PATH_BUT_SAFE_PART_OF_PATH
        in reachability["GO:0000002"].findings
    )
    assert (
        ReachabilityFinding.CROSS_ASPECT_EDGE_IGNORED
        in reachability["GO:0000003"].findings
    )
    assert (
        ReachabilityFinding.VALID_IS_A_ROOT_PATH in reachability["GO:0000003"].findings
    )
    assert report.active_terms_by_aspect == (
        (GOAspect.MF, 1),
        (GOAspect.BP, 4),
        (GOAspect.CC, 1),
    )


def test_preflight_reports_scoring_cycles_deterministically(tmp_path):
    body = "".join(
        [
            _go_class("GO:0008150", "biological_process"),
            _go_class("GO:0003674", "molecular_function"),
            _go_class("GO:0005575", "cellular_component"),
            _go_class("GO:0000001", "biological_process", _is_a("GO:0000002")),
            _go_class("GO:0000002", "biological_process", _part_of("GO:0000001")),
        ]
    )
    context = _context(tmp_path, body)

    policy = OntologyPreflightPolicy.synthetic_fixture()
    first = preflight_ontology(context, policy=policy, strict=False)
    second = preflight_ontology(context, policy=policy, strict=False)

    assert first.report_id == second.report_id
    assert first.scoring_hierarchy_cycles == second.scoring_hierarchy_cycles
    assert first.scoring_hierarchy_cycles
    assert any(issue.code == "SCORING_HIERARCHY_CYCLE" for issue in first.issues)

    with pytest.raises(OntologyPreflightError):
        preflight_ontology(context, policy=policy, strict=True)


def test_official_preflight_accepts_core_go_ontology(tmp_path):
    context = _context(
        tmp_path,
        "".join(
            [
                _go_class("GO:0008150", "biological_process"),
                _go_class("GO:0003674", "molecular_function"),
                _go_class("GO:0005575", "cellular_component"),
            ]
        ),
    )

    report = preflight_ontology(context, strict=False)

    assert not any(issue.code == "NON_GO_ONTOLOGY" for issue in report.errors)
