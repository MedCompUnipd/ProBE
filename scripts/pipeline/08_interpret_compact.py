#!/usr/bin/env python3
"""Explicit M10c full derived-TSV interpretation; never scan original GAFs.

Preflight is default. The execute flag is mandatory for full START+END work.
Outputs are compact and include indexable unfiltered original provenance.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from probe.ontology import GeneOntology
from probe.stage08_collection_inputs import prepare_collection_inputs
from probe.stage08_compact_interpretation import (
    RoleTables,
    build_compact_interpretation,
)
from probe.stage08_go_closure import GoClosureIndex
from probe.workflow.artifacts import sha256_file, validate_bundle


def _artifact(manifest, name: str):
    matching = [part for part in manifest.outputs if part.path == name]
    if len(matching) != 1 or matching[0].sha256 is None:
        raise ValueError(f"Missing unique certified M8 output: {name}")
    return matching[0]


def _json_input(path: Path, sha256: str) -> dict:
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"Missing non-symlink input JSON: {path}")
    if sha256_file(path) != sha256:
        raise ValueError(f"Source JSON integrity mismatch: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def _file_state(path: Path):
    s = path.stat()
    return (s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns, s.st_ctime_ns)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stage07-bundle", required=True, type=Path)
    parser.add_argument("--start-goa-bundle", required=True, type=Path)
    parser.add_argument("--end-goa-bundle", required=True, type=Path)
    parser.add_argument("--go-policy-bundle", required=True, type=Path)
    parser.add_argument("--m8-bundle", required=True, type=Path)
    parser.add_argument("--ontology", required=True, type=Path)
    parser.add_argument("--destination", required=True, type=Path)
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()

    upstream = {
        "stage07_bundle": args.stage07_bundle,
        "start_goa_bundle": args.start_goa_bundle,
        "end_goa_bundle": args.end_goa_bundle,
        "go_policy_bundle": args.go_policy_bundle,
    }
    inputs = prepare_collection_inputs(**upstream)
    m8 = validate_bundle(args.m8_bundle, deep=False)
    if m8.stage != "08" or m8.parameters.get("schema_version") != (
        "stage08-raw-collection-bundle-v1"
    ):
        raise ValueError("M8 is not a canonical Stage 08 raw-collection bundle")
    if m8.parameters.get("input_fingerprint") != inputs.fingerprint:
        raise ValueError("Stage 07/01B1 fingerprints disagree with M8")
    if args.ontology.is_symlink() or not args.ontology.is_file():
        raise ValueError("Pinned ontology must be a real file")
    ontology = json.loads(inputs.ontology_json)
    expected_owl = ontology["sha256"]
    if sha256_file(args.ontology) != expected_owl:
        raise ValueError("Chosen GO OWL does not match M8 pinned ontology")
    summary_name = "08_collection_summary.json"
    summary = _json_input(
        args.m8_bundle / summary_name,
        _artifact(m8, summary_name).sha256,
    )
    if (
        summary.get("schema_version") != "stage08-raw-collection-bundle-v1"
        or summary.get("input_fingerprint") != inputs.fingerprint
        or summary.get("annotation_coverage_certified") is not True
        or set(summary.get("by_role", {})) != {"START", "END"}
    ):
        raise ValueError("M8 collection summary fails canonical contract")
    roles = []
    for gaf in inputs.gafs:
        role = gaf.role
        identity = summary["by_role"][role]
        if (
            identity.get("release") != gaf.source.release
            or identity.get("normalized_gaf_sha256") != gaf.source.sha256
            or identity.get("coverage_certified") is not True
        ):
            raise ValueError("M8 role/release/GAF SHA256 mismatch")
        prefix = f"08_{role.lower()}_"
        source_name = f"{prefix}source_assertions.tsv"
        links_name = f"{prefix}annotation_links.tsv"
        source = _artifact(m8, source_name)
        links = _artifact(m8, links_name)
        roles.append(
            RoleTables(
                role=role,
                source_tsv=args.m8_bundle / source_name,
                links_tsv=args.m8_bundle / links_name,
                gaf_source=gaf.source,
                expected_source_rows=identity["retrieved_source_rows"],
                expected_links=identity["annotation_links"],
                source_tsv_sha256=source.sha256,
                links_tsv_sha256=links.sha256,
            )
        )
    if sum(x.expected_links for x in roles) != summary["annotation_links"]:
        raise ValueError("M8 aggregate link count mismatch")
    if sum(x.expected_source_rows for x in roles) != summary["retrieved_source_rows"]:
        raise ValueError("M8 aggregate source count mismatch")
    if args.destination.exists() or args.destination.is_symlink():
        raise FileExistsError("Interpretation bundle already exists")
    print(
        json.dumps(
            {
                "mode": "EXECUTE" if args.execute else "PREFLIGHT_ONLY",
                "m8_fingerprint": inputs.fingerprint,
                "ontology_sha256": expected_owl,
                "by_role": {
                    role.role: {
                        "source_rows": role.expected_source_rows,
                        "donor_links": role.expected_links,
                    }
                    for role in roles
                },
                "destination": str(args.destination),
                "gaf_contents_read": False,
                "output_published": False,
            },
            sort_keys=True,
        )
    )
    if not args.execute:
        return

    original_stamps = {
        str(path): _file_state(path)
        for path in [
            args.stage07_bundle / "manifest.json",
            args.m8_bundle / "manifest.json",
            args.m8_bundle / summary_name,
            args.ontology,
            *(p for role in roles for p in (role.source_tsv, role.links_tsv)),
        ]
    }
    implementation_files = [
        Path(__file__),
        Path(__file__).resolve().parents[2]
        / "src/probe/stage08_compact_interpretation.py",
        Path(__file__).resolve().parents[2] / "src/probe/stage08_m8_adapter.py",
        Path(__file__).resolve().parents[2] / "src/probe/stage08_go_supports.py",
        Path(__file__).resolve().parents[2] / "src/probe/stage08_go_closure.py",
    ]
    code_hashes = {str(path): sha256_file(path) for path in implementation_files}

    def final_check() -> None:
        for path, stamp in original_stamps.items():
            if _file_state(Path(path)) != stamp:
                raise ValueError(f"Input metadata changed during M10c: {path}")
        for path, sha in code_hashes.items():
            if sha256_file(Path(path)) != sha:
                raise ValueError(f"M10c implementation changed: {path}")
        if prepare_collection_inputs(**upstream).fingerprint != inputs.fingerprint:
            raise ValueError("Upstream manifest fingerprint changed during M10c")

    graph = GoClosureIndex.from_gene_ontology(
        GeneOntology.from_owl(args.ontology, strict=False)
    )
    report = build_compact_interpretation(
        roles=roles,
        donors=inputs.donors,
        graph=graph,
        ontology_sha256=expected_owl,
        destination=args.destination,
        final_check=final_check,
    )
    print(
        json.dumps(
            {
                "mode": "EXECUTE",
                "status": "M10C_PUBLISHED",
                "summary": report,
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
