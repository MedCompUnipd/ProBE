"""Immutable synthetic GO inputs for the canonical V2 upstream execution gate.

These are source-boundary fixtures. The production GO-policy compiler builds
01B0; tiny normalized GAFs are declared as synthetic 01B1 inputs. Stage 07,
M8, M10c and Temporal V2 must be produced by the actual Snakemake rules.
"""

from __future__ import annotations

import csv
import hashlib
import json
import re
import sqlite3
import xml.etree.ElementTree as ET
from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from contextlib import closing
from dataclasses import asdict, dataclass
from pathlib import Path

from probe.go_policy import compile_go_policy
from probe.stage08_compact_interpretation import TERM_COLUMNS
from probe.stage08_gaf_collection import LINK_COLUMNS, SOURCE_COLUMNS
from probe.stage08_temporal_benchmark import load_target_ids
from probe.stage08_temporal_v2 import (
    ASPECT_HEADER,
    AUDIT_HEADER,
    EVENT_HEADER,
    NEG_HEADER,
    TERM_HEADER,
    load_classes,
)
from probe.workflow.artifacts import (
    Artifact,
    atomic_stage_bundle,
    sha256_file,
    validate_bundle,
)

RELEASES = {"START": "2023_01", "END": "2026_03"}
REGISTRY_ID = "canonical-v2-upstream-synthetic-evidence-v1"
ONTOLOGY_IRI = "https://example.org/probe/synthetic/go.owl"
VERSION_IRI = "https://example.org/probe/releases/2026-03-01/synthetic-go.owl"
ROOTS = {
    "F": ("GO:0003674", "molecular_function"),
    "P": ("GO:0008150", "biological_process"),
    "C": ("GO:0005575", "cellular_component"),
}
TARGET_ROWS = (("T1", "111", "P99901"), ("T2", "111", "P20001"))


@dataclass(frozen=True)
class GoAnnotation:
    """One source GAF assertion, independent of candidate/donor eligibility."""

    accession: str
    go_id: str
    evidence_code: str = "IDA"
    taxid: str = "111"
    aspect: str = "F"
    negated: bool = False


START_ANNOTATIONS = (
    GoAnnotation("P10001", "GO:0000001", "IEA"),
    GoAnnotation("P10001", "GO:0000002"),
    GoAnnotation("P10001", "GO:0000012", "IKR", negated=True),
    GoAnnotation("P20001", "GO:0000003", "IEA"),
    GoAnnotation("P20001", "GO:0000004"),
    # Positive GO on blocked candidates makes accidental donation observable.
    GoAnnotation("P10007", "GO:0000005", taxid="112"),
    GoAnnotation("P10004", "GO:0000006"),
    GoAnnotation("P10003", "GO:0000007"),
    GoAnnotation("P10005", "GO:0000008"),
    GoAnnotation("P10006", "GO:0000009"),
)
END_ANNOTATIONS = (
    GoAnnotation("P10001", "GO:0000001"),
    GoAnnotation("P10001", "GO:0000010"),
    GoAnnotation("P10001", "GO:0000012"),
    GoAnnotation("P20001", "GO:0000003"),
    GoAnnotation("P20001", "GO:0000011"),
)

# Expectations depend on the companion upstream fixture admitting exactly
# P10001 and P20001 in each release. They never materialize donor decisions.
EXPECTED_DEFAULT = {
    "retrieved_source_rows": {"START": 5, "END": 5},
    "direct_support_rows": 10,
    "term_code_support_rows": 16,
    "distinct_propagated_terms": 14,
    "benchmark_counts": {"EVAL": 4, "SKIP": 2, "NEUTRAL": 1},
    "root_excluded": 2,
    "confirmation": (("T1", "GO:0000001"), ("T2", "GO:0000003")),
    "novel": (("T1", "GO:0000010"), ("T2", "GO:0000011")),
    "neutral": (("T1", "GO:0000012", "CONFLICT_IKR_IRD"),),
    "start_only": (("T1", "GO:0000002"), ("T2", "GO:0000004")),
    "blocked_go_ids": tuple(f"GO:{number:07d}" for number in range(5, 10)),
}


def _write_json(path: Path, value: object) -> None:
    with path.open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(value, stream, indent=2, sort_keys=True, allow_nan=False)
        stream.write("\n")


def _artifact(path: Path) -> Artifact:
    return Artifact(str(path.resolve()), path.stat().st_size, sha256_file(path))


def _write_ontology(path: Path, annotations: Sequence[GoAnnotation]) -> None:
    namespaces = {
        "rdf": "http://www.w3.org/1999/02/22-rdf-syntax-ns#",
        "rdfs": "http://www.w3.org/2000/01/rdf-schema#",
        "owl": "http://www.w3.org/2002/07/owl#",
        "oboInOwl": "http://www.geneontology.org/formats/oboInOwl#",
    }
    for prefix, iri in namespaces.items():
        ET.register_namespace(prefix, iri)

    def tag(prefix: str, local: str) -> str:
        return f"{{{namespaces[prefix]}}}{local}"

    def term_iri(go_id: str) -> str:
        return "http://purl.obolibrary.org/obo/" + go_id.replace(":", "_")

    graph = ET.Element(tag("rdf", "RDF"))
    ontology = ET.SubElement(
        graph, tag("owl", "Ontology"), {tag("rdf", "about"): ONTOLOGY_IRI}
    )
    ET.SubElement(
        ontology,
        tag("owl", "versionIRI"),
        {tag("rdf", "resource"): VERSION_IRI},
    )
    terms = {go_id: aspect for aspect, (go_id, _) in ROOTS.items()}
    for annotation in annotations:
        previous = terms.setdefault(annotation.go_id, annotation.aspect)
        if previous != annotation.aspect:
            raise ValueError("Synthetic GO term has inconsistent aspects")
    for go_id, aspect in sorted(terms.items()):
        root_id, namespace = ROOTS[aspect]
        node = ET.SubElement(
            graph, tag("owl", "Class"), {tag("rdf", "about"): term_iri(go_id)}
        )
        ET.SubElement(node, tag("rdfs", "label")).text = f"Synthetic {go_id}"
        ET.SubElement(node, tag("oboInOwl", "hasOBONamespace")).text = namespace
        if go_id != root_id:
            ET.SubElement(
                node,
                tag("rdfs", "subClassOf"),
                {tag("rdf", "resource"): term_iri(root_id)},
            )
    ET.indent(graph, space="  ")
    with path.open("xb") as stream:
        ET.ElementTree(graph).write(stream, encoding="utf-8", xml_declaration=True)
        stream.write(b"\n")


def _gaf_line(annotation: GoAnnotation, date: str) -> str:
    relation = {"F": "enables", "P": "involved_in", "C": "located_in"}[
        annotation.aspect
    ]
    qualifier = "NOT|" + relation if annotation.negated else relation
    fields = (
        "UniProtKB",
        annotation.accession,
        annotation.accession,
        qualifier,
        annotation.go_id,
        "GO_REF:0000001",
        annotation.evidence_code,
        "",
        annotation.aspect,
        "Synthetic upstream execution fixture",
        "",
        "protein",
        "taxon:" + annotation.taxid,
        date,
        "PROBE_SYNTHETIC",
        "",
        "",
    )
    return "\t".join(fields) + "\n"


def _validate_annotations(annotations: Mapping[str, Sequence[GoAnnotation]]) -> None:
    if set(annotations) != {"START", "END"}:
        raise ValueError("Synthetic GAFs require exactly START and END")
    for rows in annotations.values():
        for row in rows:
            if (
                not row.accession
                or any(character.isspace() for character in row.accession)
                or re.fullmatch(r"GO:[0-9]{7}", row.go_id) is None
                or row.evidence_code not in {"IDA", "IEA", "IKR"}
                or not row.taxid.isdecimal()
                or row.aspect not in ROOTS
            ):
                raise ValueError("Invalid synthetic GO assertion")


def _write_registry_and_policy(registry: Path, policy: Path) -> None:
    _write_json(
        registry,
        {
            "schema_version": "probe-evidence-registry-v1",
            "registry_id": REGISTRY_ID,
            "origin": "synthetic canonical V2 upstream execution gate",
            "codes": {
                code: {"source": "synthetic fixture", "description": code}
                for code in ("IDA", "IEA", "IKR")
            },
            "groups": {},
        },
    )
    _write_json(
        policy,
        {
            "schema_version": "stage08-evidence-classes-v1",
            "registry_id": REGISTRY_ID,
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
                    "NON_SCORE": ["IEA", "IKR"],
                    "EXCLUDED": [],
                }.items()
            },
        },
    )
    load_classes(registry, policy)


def create_go_inputs(
    destination: Path,
    *,
    target_rows: Sequence[tuple[str, str, str]] = TARGET_ROWS,
    targets_input: Path | None = None,
    annotations: Mapping[str, Sequence[GoAnnotation]] | None = None,
    releases: Mapping[str, str] | None = None,
) -> dict:
    """Create source fixtures once and return DAG input fragments as strings.

    ``inputs`` contains targets_input, ontology, evidence_registry,
    temporal_policy and go_policy_bundle. ``per_release`` contains only each
    role's goa_bundle; the companion upstream fixture supplies other fields.
    Pass its original ``targets_input`` to retain the real preparation input.
    No existing directory or immutable bundle is replaced or adapted.
    """
    selected = (
        {"START": START_ANNOTATIONS, "END": END_ANNOTATIONS}
        if annotations is None
        else dict(annotations)
    )
    identities = dict(RELEASES if releases is None else releases)
    if (
        set(identities) != {"START", "END"}
        or len(set(identities.values())) != 2
        or any(
            re.fullmatch(r"[0-9]{4}_[0-9]{2}", x) is None for x in identities.values()
        )
    ):
        raise ValueError("Synthetic GO inputs need distinct YYYY_MM releases")
    _validate_annotations(selected)
    destination = destination.absolute()
    destination.mkdir(parents=True, exist_ok=False)
    ontology_path = destination / "synthetic-go.owl"
    _write_ontology(
        ontology_path, tuple(row for rows in selected.values() for row in rows)
    )
    ontology = {
        "sha256": sha256_file(ontology_path),
        "original_url": None,
        "retrieval_date": None,
        "version_iri": VERSION_IRI,
    }
    implementation = {
        "name": "canonical-v2-upstream-synthetic-source-fixture",
        "version": "1",
    }
    go_policy = destination / "01B0"
    with atomic_stage_bundle(
        go_policy,
        stage="01B0",
        role=None,
        release_id=None,
        parameters={"synthetic_source_fixture": True},
        ontology=ontology,
        implementation=implementation,
        inputs=(_artifact(ontology_path),),
    ) as staged:
        compile_go_policy(
            owl_path=ontology_path,
            output_directory=staged,
            expected_owl_sha256=ontology["sha256"],
        )
    validate_bundle(go_policy, deep=True)
    reference = _artifact(go_policy / "manifest.json")
    per_release = {}
    for role in ("START", "END"):
        release = identities[role]
        bundle = destination / f"{role}-01B1"
        with (
            atomic_stage_bundle(
                bundle,
                stage="01B1",
                role=role,
                release_id=release,
                parameters={"synthetic_source_fixture": True},
                ontology=ontology,
                implementation=implementation,
                inputs=(reference,),
            ) as staged,
            (staged / "goa.normalized.gaf").open(
                "x", encoding="utf-8", newline="\n"
            ) as stream,
        ):
            stream.write("!gaf-version: 2.2\n")
            stream.write("!generated-by: ProBE synthetic upstream gate\n")
            date = release.replace("_", "") + "01"
            stream.writelines(_gaf_line(row, date) for row in selected[role])
        validate_bundle(bundle, deep=True)
        per_release[role] = {"goa_bundle": str(bundle)}
    mapping = (
        destination / "targets.input.tsv"
        if targets_input is None
        else targets_input.absolute()
    )
    if targets_input is None:
        with mapping.open("x", encoding="utf-8", newline="") as stream:
            writer = csv.writer(stream, delimiter="\t", lineterminator="\n")
            writer.writerow(("target_id", "ncbi_taxid", "uniprot_accession"))
            writer.writerows(target_rows)
    expected_mapping = {target: accession for target, _, accession in target_rows}
    if load_target_ids(mapping) != expected_mapping:
        raise ValueError("Original target identities differ from the GO fixture")
    registry = destination / "evidence.registry.json"
    temporal_policy = destination / "temporal.policy.json"
    _write_registry_and_policy(registry, temporal_policy)
    result = {
        "inputs": {
            "targets_input": str(mapping),
            "ontology": str(ontology_path),
            "evidence_registry": str(registry),
            "temporal_policy": str(temporal_policy),
            "go_policy_bundle": str(go_policy),
        },
        "per_release": per_release,
        "source_annotations": {
            role: [asdict(row) for row in rows] for role, rows in selected.items()
        },
        "source_row_counts": {role: len(rows) for role, rows in selected.items()},
        "ontology_sha256": ontology["sha256"],
        "expected": None,
    }
    if annotations is None:
        result["expected"] = {
            **EXPECTED_DEFAULT,
            "releases": identities,
            "query_ids": expected_mapping,
            "source_annotations": result["source_annotations"],
            "source_gaf_sha256": {
                role: sha256_file(Path(values["goa_bundle"]) / "goa.normalized.gaf")
                for role, values in per_release.items()
            },
            "ontology_sha256": ontology["sha256"],
            "policy_sha256": sha256_file(temporal_policy),
            "direct_donors": {"P10001": "T1", "P20001": "T2"},
            "direct_donor_rids": {
                "START": {"P10001": "1", "P20001": "8"},
                "END": {"P10001": "1", "P20001": "3"},
            },
        }
    _write_json(destination / "synthetic_go_inputs.json", result)
    return result


def _check_equal(actual: object, expected: object, label: str) -> None:
    if actual != expected:
        raise AssertionError(f"{label}: expected {expected!r}; observed {actual!r}")


def _read_table(path: Path, columns: tuple[str, ...]) -> list[tuple[str, ...]]:
    """Read the published TSV literally, as the production M8 adapter does."""
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"Missing or unsafe synthetic gate output: {path}")
    rows = []
    with path.open("r", encoding="utf-8", newline="") as stream:
        reader = csv.reader(stream, delimiter="\t", quoting=csv.QUOTE_NONE)
        _check_equal(tuple(next(reader, ())), columns, f"{path.name} header")
        for number, row in enumerate(reader, start=2):
            _check_equal(len(row), len(columns), f"{path.name} line {number}")
            rows.append(tuple(row))
    return rows


def _expected_sources(expected: dict, role: str) -> list[tuple[str, ...]]:
    release = expected["releases"][role]
    digest = expected["source_gaf_sha256"][role]
    date = release.replace("_", "") + "01"
    rows = []
    # The two generated GAF comments occupy physical source lines 1 and 2.
    for line, raw in enumerate(expected["source_annotations"][role], start=3):
        annotation = GoAnnotation(**raw)
        if annotation.accession not in expected["direct_donors"]:
            continue
        identity = json.dumps(
            ["stage08-normalized-gaf-assertion-v1", release, digest, line],
            separators=(",", ":"),
            ensure_ascii=True,
        )
        assertion_id = hashlib.sha256(identity.encode("utf-8")).hexdigest()
        fields = _gaf_line(annotation, date).rstrip("\n").split("\t")
        rows.append((assertion_id, release, str(line), *fields))
    return rows


def _validate_m8(expected: dict, m8: Path) -> tuple[list[tuple], dict]:
    summary = json.loads((m8 / "08_collection_summary.json").read_text())
    _check_equal(
        summary["schema_version"],
        "stage08-raw-collection-bundle-v1",
        "M8 schema",
    )
    _check_equal(summary["annotation_coverage_certified"], True, "M8 coverage")
    direct = []
    observed = {}
    donor_rids = {}
    for role in ("START", "END"):
        release = expected["releases"][role]
        prefix = f"08_{role.lower()}_"
        source_path = m8 / f"{prefix}source_assertions.tsv"
        links_path = m8 / f"{prefix}annotation_links.tsv"
        sources = _read_table(source_path, SOURCE_COLUMNS)
        _check_equal(sources, _expected_sources(expected, role), f"M8 {role} sources")
        by_id = {row[0]: dict(zip(SOURCE_COLUMNS, row, strict=True)) for row in sources}
        links = _read_table(links_path, LINK_COLUMNS)
        _check_equal(
            Counter(row[3] for row in links),
            Counter(by_id.keys()),
            f"M8 {role} one expected target link per source",
        )
        for link_release, target, rid, assertion_id in links:
            source = by_id[assertion_id]
            accession = source["db_object_id"]
            _check_equal(link_release, release, "M8 link release")
            _check_equal(target, expected["direct_donors"][accession], "M8 target")
            _check_equal(
                rid,
                expected["direct_donor_rids"][role][accession],
                "M8 donor RID matches the source FULL record",
            )
            if not rid.isascii() or not rid.isdecimal() or int(rid) < 1:
                raise AssertionError("M8 link has an invalid RID")
            association = release, target, accession
            previous = donor_rids.setdefault(association, rid)
            _check_equal(rid, previous, "Stable donor RID within a release")
            qualifier = source["qualifier"]
            polarity = "NOT" if "NOT" in qualifier.split("|") else "POSITIVE"
            relation = qualifier.removeprefix("NOT|")
            direct.append(
                (
                    release,
                    target,
                    source["aspect"],
                    source["go_id"],
                    polarity,
                    source["evidence_code"],
                    rid,
                    assertion_id,
                    int(source["normalized_gaf_line"]),
                    accession,
                    relation,
                    qualifier,
                )
            )
        role_summary = summary["by_role"][role]
        for key, value in {
            "role": role,
            "release": release,
            "normalized_gaf_sha256": expected["source_gaf_sha256"][role],
            "data_lines": len(expected["source_annotations"][role]),
            "retrieved_source_rows": len(sources),
            "annotation_links": len(links),
            "coverage_certified": True,
        }.items():
            _check_equal(role_summary[key], value, f"M8 {role} {key}")
        observed[role] = {
            "source_rows": len(sources),
            "annotation_links": len(links),
            "source_sha256": sha256_file(source_path),
            "links_sha256": sha256_file(links_path),
            "gaf_source_sha256": expected["source_gaf_sha256"][role],
        }
    for key in ("retrieved_source_rows", "annotation_links"):
        _check_equal(summary[key], len(direct), f"M8 complete {key}")
    return direct, observed


def _expected_term_codes(direct: list[tuple]) -> dict[tuple, tuple[int, int]]:
    """The fixture graph is a star: positive leaves inherit one root only.

    All source NOTs are leaves and remain direct. Counts are derived from the
    expected GAF links, never copied from M10c's manifest or SQL output.
    """
    supports: Counter = Counter()
    direct_counts: Counter = Counter()
    for release, target, aspect, go_id, polarity, code, *_ in direct:
        key = release, target, aspect, go_id, polarity, code
        supports[key] += 1
        direct_counts[key] += 1
        if polarity == "POSITIVE":
            root = ROOTS[aspect][0]
            if go_id == root:
                raise AssertionError("This fixture requires only leaf source GO")
            supports[release, target, aspect, root, polarity, code] += 1
    return {key: (count, direct_counts[key]) for key, count in supports.items()}


def _expected_term_summary(codes: dict[tuple, tuple[int, int]]) -> list[tuple]:
    groups = defaultdict(dict)
    for key, values in codes.items():
        groups[key[:4]][key[4:]] = values
    rows = []
    for key, values in sorted(groups.items()):
        positive = {
            code: n for (polarity, code), n in values.items() if polarity == "POSITIVE"
        }
        negative = {
            code: n for (polarity, code), n in values.items() if polarity == "NOT"
        }
        rows.append(
            (
                *key,
                str(sum(n[0] for n in positive.values())),
                str(sum(n[0] for n in negative.values())),
                json.dumps(sorted(positive), separators=(",", ":")),
                json.dumps(sorted(negative), separators=(",", ":")),
                str(sum(n[1] for n in positive.values())),
                str(sum(n[1] for n in negative.values())),
                str(int(bool(positive and negative))),
            )
        )
    return rows


def _validate_m10c(expected: dict, m10c: Path, direct: list[tuple], m8: dict) -> dict:
    manifest = json.loads((m10c / "manifest.json").read_text())
    for key, value in {
        "schema_version": "stage08-m10c-compact-interpretation-v1",
        "ontology_sha256": expected["ontology_sha256"],
        "evaluation_policy": "not_computed",
        "not_policy": "preserved_no_veto",
        "evidence_policy": "unfiltered_literal_source_codes",
    }.items():
        _check_equal(manifest[key], value, f"M10c {key}")
    for role, values in m8.items():
        for key in ("source_sha256", "links_sha256", "gaf_source_sha256"):
            _check_equal(
                manifest["source_payloads"][role][key],
                values[key],
                f"M10c {role} source ancestry {key}",
            )
    database = m10c / "direct_provenance.sqlite3"
    if database.is_symlink() or not database.is_file():
        raise AssertionError("Missing or unsafe M10c provenance database")
    uri = database.resolve().as_uri() + "?mode=ro&immutable=1"
    codes = _expected_term_codes(direct)
    with closing(sqlite3.connect(uri, uri=True)) as connection:
        _check_equal(
            connection.execute("PRAGMA quick_check").fetchone(),
            ("ok",),
            "M10c SQLite integrity",
        )
        actual_direct = connection.execute("""
            SELECT release, target_id, aspect, source_go, polarity,
                evidence_code, rid, assertion_id, normalized_gaf_line,
                donor_accession, gaf_relation, gaf_qualifier
            FROM direct_support
        """).fetchall()
        _check_equal(Counter(actual_direct), Counter(direct), "M10c direct lineage")
        actual_codes = connection.execute("""
            SELECT release, target_id, aspect, go_id, polarity, evidence_code,
                support_count, direct_count FROM term_code_support
        """).fetchall()
    wanted_codes = [(*key, *counts) for key, counts in codes.items()]
    _check_equal(Counter(actual_codes), Counter(wanted_codes), "M10c GO propagation")
    term_rows = _read_table(m10c / "propagated_terms.tsv", TERM_COLUMNS)
    wanted_terms = _expected_term_summary(codes)
    _check_equal(term_rows, wanted_terms, "M10c propagated TSV/GO source parity")
    _check_equal(manifest["distinct_propagated_terms"], len(term_rows), "M10c terms")
    # Exact equality of the complete tables also forbids GO5..GO9, GO START
    # leaking into END, GO END leaking into START, and foreign donor identities.
    return {
        "direct_support_rows": len(actual_direct),
        "term_code_support_rows": len(actual_codes),
        "distinct_propagated_terms": len(term_rows),
        "term_codes": codes,
    }


def _temporal_cases(expected: dict) -> dict[tuple[str, str, str], tuple[str, str]]:
    cases = {}
    for name, status, reason in (
        ("confirmation", "EVAL", "CONFIRMATION"),
        ("novel", "EVAL", "NOVEL"),
        ("start_only", "SKIP", "SCORE_START_ONLY"),
    ):
        for target, go_id in expected[name]:
            cases[target, "F", go_id] = status, reason
    for target, go_id, reason in expected["neutral"]:
        cases[target, "F", go_id] = "NEUTRAL", reason
    return cases


def _temporal_presence_flags(expected: dict, codes: dict) -> dict:
    flags = defaultdict(lambda: dict.fromkeys(AUDIT_HEADER[6:], "0"))
    roles = {release: role.lower() for role, release in expected["releases"].items()}
    for release, target, aspect, go_id, polarity, code in codes:
        values = flags[target, aspect, go_id]
        group = "score" if code == "IDA" else "non_score"
        sign = "positive" if polarity == "POSITIVE" else "not"
        values[f"{roles[release]}_{group}_{sign}"] = "1"
        if polarity == "NOT" and code == "IKR":
            values["curated_not_ikr_ird"] = "1"
    return flags


def validate_go_outputs(
    expected: dict,
    *,
    m8: Path,
    m10c: Path,
    temporal: Path,
) -> dict:
    """Fail closed unless actual M8 -> Temporal outputs match the fixture.

    The caller checks bundle integrity and Stage 07 donors separately. Here
    all tables are read from the published outputs. Neither bundles nor SQLite
    files are edited. Assertions cover full source provenance and precise
    release-local GO, including the original prediction query identifiers.
    """
    if not isinstance(expected, dict) or "source_annotations" not in expected:
        raise ValueError("GO validation requires create_go_inputs()['expected']")
    direct, observed_m8 = _validate_m8(expected, m8)
    observed_m10c = _validate_m10c(expected, m10c, direct, observed_m8)
    codes = observed_m10c.pop("term_codes")
    cases = _temporal_cases(expected)
    query_ids = expected["query_ids"]
    presence = _temporal_presence_flags(expected, codes)
    wanted_audit = []
    wanted_terms = []
    for key, (status, reason) in sorted(cases.items()):
        target, aspect, go_id = key
        query = query_ids[target]
        wanted_terms.append((query, aspect, go_id, status))
        wanted_audit.append(
            (
                query,
                target,
                aspect,
                go_id,
                status,
                reason,
                *(presence[key][name] for name in AUDIT_HEADER[6:]),
            )
        )
    terms = _read_table(temporal / "benchmark_terms.tsv", TERM_HEADER)
    audit = _read_table(temporal / "term_audit.tsv", AUDIT_HEADER)
    _check_equal(Counter(terms), Counter(wanted_terms), "Temporal benchmark terms")
    _check_equal(Counter(audit), Counter(wanted_audit), "Temporal reasons and flags")
    wanted_events = [
        (
            query_ids[target],
            target,
            aspect,
            go_id,
            reason if status == "EVAL" else "DIRECT_CONFLICT_OR_REVERSAL",
            status,
        )
        for (target, aspect, go_id), (status, reason) in cases.items()
        if status in {"EVAL", "NEUTRAL"}
    ]
    events = _read_table(temporal / "temporal_events.tsv", EVENT_HEADER)
    _check_equal(Counter(events), Counter(wanted_events), "Temporal direct events")
    wanted_negatives = [
        (query_ids[target], target, "F", go_id, "1", "0", "NEUTRAL", reason)
        for target, go_id, reason in expected["neutral"]
    ]
    negatives = _read_table(temporal / "negative_terms.tsv", NEG_HEADER)
    _check_equal(negatives, wanted_negatives, "Temporal retained NOT observations")
    wanted_aspects = []
    for target in sorted(query_ids):
        counts = Counter(
            status for key, (status, _) in cases.items() if key[0] == target
        )
        wanted_aspects.append(
            (
                query_ids[target],
                "F",
                str(counts["EVAL"]),
                str(counts["SKIP"]),
                str(counts["NEUTRAL"]),
                "0",
                "1",
                "1",
            )
        )
    aspects = _read_table(temporal / "target_aspects.tsv", ASPECT_HEADER)
    _check_equal(aspects, wanted_aspects, "Temporal target/aspect scoring universe")
    manifest = json.loads((temporal / "manifest.json").read_text())
    for key, value in {
        "schema_version": "stage08-temporal-eval-skip-neutral-v3",
        "mode": "EXECUTE",
        "output_published": True,
        "assessment_mode": "acquisition_and_confirmation",
        "start_release": expected["releases"]["START"],
        "end_release": expected["releases"]["END"],
        "ontology_sha256": expected["ontology_sha256"],
        "policy_sha256": expected["policy_sha256"],
        "m10c_manifest_sha256": sha256_file(m10c / "manifest.json"),
        "m8_summary_sha256": sha256_file(m8 / "08_collection_summary.json"),
        "classes": {"SCORE": ["IDA"], "NON_SCORE": ["IEA", "IKR"], "EXCLUDED": []},
    }.items():
        _check_equal(manifest[key], value, f"Temporal manifest {key}")
    status_counts = Counter(row[3] for row in terms)
    for status in ("EVAL", "SKIP", "NEUTRAL"):
        _check_equal(
            manifest["counts"][f"benchmark_{status}"],
            status_counts[status],
            f"Temporal observed {status} count",
        )
    return {
        "status": "PASS",
        "m8": observed_m8,
        "m10c": observed_m10c,
        "temporal": {
            "benchmark_counts": dict(sorted(status_counts.items())),
            "audit_rows": len(audit),
            "events": len(events),
            "negative_observations": len(negatives),
            "original_query_ids": query_ids,
        },
        "checks": {
            "source_gaf_row_identity_and_physical_lines": "PASS",
            "m8_to_m10c_target_donor_provenance": "PASS",
            "release_local_direct_and_propagated_go": "PASS",
            "blocked_candidate_go_absent": "PASS",
            "confirmation_and_novel_acquisition": "PASS",
            "not_preserved_and_ikr_conflict_neutral": "PASS",
            "original_prediction_query_ids": "PASS",
            "published_consumer_input_ancestry": "PASS",
        },
    }
