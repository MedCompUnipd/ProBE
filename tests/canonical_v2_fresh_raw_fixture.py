"""Tiny original DAT/GAF/taxdump/OWL inputs for a real fresh workflow test.

No Stage 01, discovery, alignment, resolver, or GO output is prepared here.
"""

from __future__ import annotations

import csv
import hashlib
import json
import xml.etree.ElementTree as ET
from copy import deepcopy
from pathlib import Path

import yaml

from probe.canonical_v2.fresh_dag import SCHEMA
from probe.workflow.config import FROZEN_SCIENTIFIC

RELEASES = {"START": "2023_01", "END": "2026_03"}
TARGETS = {
    "T1": ("P10001", "ACDEFGHIKLMNPQRSTVWY"),
    "T2": ("P20001", "ABJXZCDEFGHIKLMNPQRS"),
    "T3": ("P30001", "MNPQRSTVWYACDEFGHIKLM"),
}
CLI_NAMES = {
    "stage05c2_cli": "05C2_freeze_taxonomy_audit_channel.py",
    "stage06a_cli": "06A_prepare_alignment_jobs.py",
    "nonexact_script": "NONEXACT_select_alignment_jobs.py",
    "finalize_script": "06C1_finalize_directional_glocal.py",
    "collection_cli": "08_collect_annotations.py",
    "m10c_cli": "08_interpret_compact.py",
    "temporal_cli": "08_build_benchmark_v2.py",
}
ANNOTATIONS = {
    "START": (
        ("P10001", "GO:0000001", "IEA"),
        ("P10001", "GO:0000003", "IDA"),
        ("P20001", "GO:0000004", "IDA"),
        ("P30001", "GO:0000005", "IEA"),
    ),
    "END": (
        ("P10001", "GO:0000001", "IDA"),
        ("P10001", "GO:0000002", "IDA"),
        ("P20001", "GO:0000004", "IDA"),
        ("P30001", "GO:0000005", "IDA"),
        ("P30001", "GO:0000006", "IDA"),
    ),
}


def write_json(path: Path, data: dict) -> None:
    with path.open("x", encoding="utf-8") as stream:
        json.dump(data, stream, sort_keys=True, indent=2)
        stream.write("\n")


def _dat_record(
    accession: str,
    sequence: str,
    *,
    taxid: str = "111",
    fragment: bool = False,
    locus: str,
) -> str:
    flags = "DE   Flags: Fragment;\n" if fragment else ""
    return (
        f"ID   {accession}_SYNTH Reviewed; {len(sequence)} AA.\n"
        f"AC   {accession};\n"
        f"DE   RecName: Full=Synthetic {accession};\n"
        f"{flags}"
        f"GN   Name=GENE{locus}; OrderedLocusNames={locus};\n"
        f"OX   NCBI_TaxID={taxid};\n"
        "PE   1: Evidence at protein level;\n"
        "DT   01-JAN-2000, sequence version 1.\n"
        f"SQ   SEQUENCE {len(sequence)} AA; 1000 MW; ABCDEF CRC64;\n"
        f"     {sequence}\n//\n"
    )


def _node(taxid: str, parent: str, rank: str) -> str:
    return (
        f"{taxid}\t|\t{parent}\t|\t{rank}\t|\t\t|\t0\t|\t0\t|\t1\t|\t"
        "0\t|\t0\t|\t0\t|\t0\t|\t0\t|\t\t|\n"
    )


def _ontology(path: Path) -> None:
    ns = {
        "rdf": "http://www.w3.org/1999/02/22-rdf-syntax-ns#",
        "rdfs": "http://www.w3.org/2000/01/rdf-schema#",
        "owl": "http://www.w3.org/2002/07/owl#",
        "oboInOwl": "http://www.geneontology.org/formats/oboInOwl#",
    }
    for prefix, uri in ns.items():
        ET.register_namespace(prefix, uri)

    def tag(prefix: str, local: str) -> str:
        return "{" + ns[prefix] + "}" + local

    graph = ET.Element(tag("rdf", "RDF"))
    ont = ET.SubElement(
        graph,
        tag("owl", "Ontology"),
        {tag("rdf", "about"): "https://example.org/probe/fresh/go.owl"},
    )
    ET.SubElement(
        ont,
        tag("owl", "versionIRI"),
        {tag("rdf", "resource"): "https://example.org/probe/fresh/2026-03/go.owl"},
    )
    roots = {
        "GO:0003674": "molecular_function",
        "GO:0008150": "biological_process",
        "GO:0005575": "cellular_component",
    }
    terms = {**roots, **{f"GO:{i:07d}": "molecular_function" for i in range(1, 7)}}
    for go_id, aspect in sorted(terms.items()):
        uri = "http://purl.obolibrary.org/obo/" + go_id.replace(":", "_")
        node = ET.SubElement(graph, tag("owl", "Class"), {tag("rdf", "about"): uri})
        ET.SubElement(node, tag("rdfs", "label")).text = "Synthetic " + go_id
        ET.SubElement(node, tag("oboInOwl", "hasOBONamespace")).text = aspect
        if go_id not in roots:
            ET.SubElement(
                node,
                tag("rdfs", "subClassOf"),
                {tag("rdf", "resource"): "http://purl.obolibrary.org/obo/GO_0003674"},
            )
    ET.indent(graph, space="  ")
    with path.open("xb") as stream:
        ET.ElementTree(graph).write(stream, encoding="utf-8", xml_declaration=True)
        stream.write(b"\n")


def create_raw_fixture(*, repo: Path, run: Path, dp_binary: Path) -> Path:
    """Return a fresh recipe; all source fixtures are original format files."""
    repo = repo.resolve(strict=True)
    run = run.absolute()
    run.mkdir(parents=True, exist_ok=False)
    inputs = run / "inputs"
    inputs.mkdir()
    target_fasta = inputs / "targets.fasta"
    target_metadata = inputs / "targets.tsv"
    with target_fasta.open("x", encoding="utf-8") as stream:
        for target, (_, sequence) in TARGETS.items():
            stream.write(f">{target}\n{sequence}\n")
    with target_metadata.open("x", encoding="utf-8", newline="") as stream:
        writer = csv.writer(stream, delimiter="\t", lineterminator="\n")
        writer.writerow(("target_id", "ncbi_taxid", "uniprot_accession"))
        writer.writerows(
            (target, "111", accession) for target, (accession, _) in TARGETS.items()
        )
    owl = inputs / "go.owl"
    _ontology(owl)
    releases = {}
    for role, release_id in RELEASES.items():
        root = inputs / role
        root.mkdir()
        source = root / "uniprot.dat"
        with source.open("x", encoding="utf-8") as stream:
            stream.write(_dat_record("P10001", TARGETS["T1"][1], locus="LOC1"))
            stream.write(
                _dat_record("P20001", TARGETS["T2"][1], fragment=True, locus="LOC2")
            )
            seq = TARGETS["T3"][1]
            if role == "START":
                seq = seq[:-1] + "N"
            stream.write(_dat_record("P30001", seq, locus="LOC3"))
            # This raw OX never appears in GOA. Only the FULL taxonomy index
            # covers its documentary candidate, resolved to the same TaxID.
            stream.write(
                _dat_record(
                    "P30002", TARGETS["T3"][1][:-1] + "Q", taxid="222", locus="LOC3"
                )
            )
        goa = root / "goa.gaf"
        date = "20230101" if role == "START" else "20260301"
        with goa.open("x", encoding="utf-8") as stream:
            stream.write("!gaf-version: 2.2\n")
            for accession, go_id, evidence in ANNOTATIONS[role]:
                fields = (
                    "UniProtKB",
                    accession,
                    accession,
                    "enables",
                    go_id,
                    "GO_REF:0000001",
                    evidence,
                    "",
                    "F",
                    "Synthetic original source",
                    "",
                    "protein",
                    "taxon:111",
                    date,
                    "PROBE_SYNTHETIC",
                    "",
                    "",
                )
                stream.write("\t".join(fields) + "\n")
        taxonomy = root / "taxonomy"
        taxonomy.mkdir()
        (taxonomy / "nodes.dmp").write_text(
            _node("1", "1", "no rank") + _node("111", "1", "species"), encoding="utf-8"
        )
        (taxonomy / "names.dmp").write_text(
            "1\t|\troot\t|\t\t|\tscientific name\t|\n"
            "111\t|\tSynthetic species\t|\t\t|\tscientific name\t|\n",
            encoding="utf-8",
        )
        (taxonomy / "merged.dmp").write_text("222\t|\t111\t|\n", encoding="utf-8")
        (taxonomy / "delnodes.dmp").write_text("333\t|\n", encoding="utf-8")
        releases[role] = {
            "release_id": release_id,
            "uniprot_sources": [str(source)],
            "goa": str(goa),
            "taxonomy": {
                "snapshot_id": release_id,
                **{
                    name: str(taxonomy / filename)
                    for name, filename in {
                        "nodes": "nodes.dmp",
                        "names": "names.dmp",
                        "merged": "merged.dmp",
                        "deleted": "delnodes.dmp",
                    }.items()
                },
            },
        }
    registry = inputs / "evidence.registry.json"
    policy = inputs / "temporal.policy.json"
    registry_id = "probe-fresh-raw-synthetic-evidence-v1"
    write_json(
        registry,
        {
            "schema_version": "probe-evidence-registry-v1",
            "registry_id": registry_id,
            "origin": "raw source integration test",
            "codes": {
                code: {"source": "synthetic fixture", "description": code}
                for code in ("IDA", "IEA")
            },
            "groups": {},
        },
    )
    write_json(
        policy,
        {
            "schema_version": "stage08-evidence-classes-v1",
            "registry_id": registry_id,
            "scoring_scope": "target_aspects_with_eval",
            "negative_policy": "evidence_sensitive_neutral",
            "assessment_mode": "acquisition_and_confirmation",
            "classes": {
                label: {
                    "include_groups": [],
                    "include_codes": codes,
                    "exclude_codes": [],
                }
                for label, codes in {
                    "SCORE": ["IDA"],
                    "NON_SCORE": ["IEA"],
                    "EXCLUDED": [],
                }.items()
            },
        },
    )
    pipeline = {
        "schema_version": 1,
        "targets": {
            "mode": "canonical",
            "fasta": str(target_fasta),
            "metadata": str(target_metadata),
            "adapter": None,
        },
        "releases": releases,
        "ontology": {
            "path": str(owl),
            "expected_sha256": hashlib.sha256(owl.read_bytes()).hexdigest(),
            "canonical_url": None,
            "original_url": None,
            "retrieval_date": None,
            "version_iri": None,
        },
        "locations": {
            name: str(run / prefix / "pipeline-v2")
            for name, prefix in (
                ("work", "work"),
                ("output", "results"),
                ("logs", "logs"),
            )
        },
        "resources": {
            "default": {"threads": 1, "mem_mb": 2048, "runtime_minutes": 60},
            "stages": {},
        },
        "validation": {"deep_checksums": True},
        "scientific": deepcopy(FROZEN_SCIENTIFIC),
        "resolver": {"subtaxon_policy": "include_audited_same_species"},
        "annotation_donor": {
            "partial_sequence": "allow",
            "documentary_fragment": "allow",
        },
        "stage08_collection": {"execute_full_scan": True},
    }
    pipeline_path = run / "pipeline.stage08.collection.yaml"
    pipeline_path.write_text(
        yaml.safe_dump(pipeline, sort_keys=False), encoding="utf-8"
    )
    source_inventory = {
        str(path.relative_to(inputs)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(inputs.rglob("*"))
        if path.is_file()
    }
    identity = hashlib.sha256(
        json.dumps(source_inventory, sort_keys=True).encode()
    ).hexdigest()
    recipe = {
        "schema": SCHEMA,
        "repo_root": str(repo),
        "run_root": str(run),
        "pipeline_config": str(pipeline_path),
        "identity_sha256": identity,
        "policy": "same_species_exact",
        "limits": {
            "threads": 1,
            "max_pair_cells": 50000000,
            "max_total_cells": 500000000,
            "max_chunk_cells": 50000000,
            "partial_sequence": "allow",
            "documentary_fragment": "allow",
        },
        "tools": {
            **{
                key: str(repo / "scripts/pipeline" / filename)
                for key, filename in CLI_NAMES.items()
            },
            "full_dp_binary": str(dp_binary),
        },
        "targets_input": str(target_metadata),
        "evidence_registry": str(registry),
        "temporal_policy": str(policy),
    }
    recipe_path = run / "fresh.recipe.json"
    write_json(recipe_path, recipe)
    write_json(
        run / "raw_fixture.identity.json",
        {
            "schema": "probe-fresh-raw-synthetic-v1",
            "inputs": source_inventory,
            "releases": RELEASES,
            "target_ids": list(TARGETS),
        },
    )
    return recipe_path
