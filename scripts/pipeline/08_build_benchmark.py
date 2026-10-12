#!/usr/bin/env python3
"""Build M11/M12 temporal EVAL/SKIP benchmark from immutable M10c outputs.

Preflight is the default. --execute is required to process M10c's ~17.7M
propagated target-release-GO terms. Never opens normalized GAF files.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from probe.ontology import GeneOntology
from probe.stage08_go_closure import GoClosureIndex
from probe.stage08_temporal_benchmark import (
    build_temporal_benchmark,
    file_sha256,
    inspect_m10c_bundle,
)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--m10c-bundle", type=Path, required=True)
    parser.add_argument("--m8-summary", type=Path, required=True)
    parser.add_argument("--targets-input", type=Path, required=True)
    parser.add_argument("--stage01-targets", type=Path, required=True)
    parser.add_argument("--evidence-registry", type=Path, required=True)
    parser.add_argument("--policy", type=Path, required=True)
    parser.add_argument("--ontology", type=Path, required=True)
    parser.add_argument("--start-release", required=True)
    parser.add_argument("--end-release", required=True)
    parser.add_argument("--destination", type=Path, required=True)
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    _, manifest = inspect_m10c_bundle(args.m10c_bundle)
    if args.ontology.is_symlink() or not args.ontology.is_file():
        raise ValueError("Pinned ontology is not a regular file")
    if file_sha256(args.ontology) != manifest["ontology_sha256"]:
        raise ValueError("Pinned ontology SHA256 differs from M10c")
    graph = None
    if args.execute:
        graph = GoClosureIndex.from_gene_ontology(
            GeneOntology.from_owl(args.ontology, strict=False)
        )
        if graph.node_count != manifest["graph_terms"]:
            raise ValueError("M10c ontology term count mismatch")
        if graph.edge_count != manifest["graph_edges"]:
            raise ValueError("M10c ontology direct edge count mismatch")
    report = build_temporal_benchmark(
        m10c_bundle=args.m10c_bundle,
        targets_input=args.targets_input,
        stage01_targets=args.stage01_targets,
        registry_path=args.evidence_registry,
        policy_path=args.policy,
        destination=args.destination,
        graph=graph,
        start_release=args.start_release,
        end_release=args.end_release,
        m8_summary=args.m8_summary,
        execute=args.execute,
        implementation_script=Path(__file__) if args.execute else None,
    )
    print(json.dumps(report, sort_keys=True))
    if report["output_published"]:
        print("M11_M12_BENCHMARK_PUBLISHED=YES")
    else:
        print("M11_M12_PREFLIGHT_PASS=YES")


if __name__ == "__main__":
    main()
