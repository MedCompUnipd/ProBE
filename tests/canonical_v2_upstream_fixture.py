"""Create source inputs for an actual canonical V2 Snakemake integration.

No canonical ledger, register, projection, handoff, seed or workload is
materialized here. Stage 01 targets, 02B taxonomy and 04B/04C/04D1 documentary
sources use their real producers. The small 03 scanner tables are synthetic
observations at the admitted DAG boundary; they do not certify those scanners.

T1 has literal EXACT records in both releases, including one FULL-only
cross-subtaxon record without positive GO. END-only metadata discovers START
NONEXACT records: one literal gene label, one normalization hint and two
colliding loci. Those observations cannot authorize donors. A GO-positive
cross-TaxID NONEXACT also reaches the workload but must not reach DP.

T2 has no EXACT in either release. Its changed direct accession is FRAG=1,
so it is excluded from similarity-only discovery. Eligible direct anchors
in both releases independently corroborate its typed locus. Only the new
documentary lookup materializes this candidate for the canonical workload.
Its donor admission also has direct accession continuity; the integration
must check the independent stable locus explicitly to test that evidence.
"""

from __future__ import annotations

import csv
import hashlib
import json
import subprocess
import sys
from collections.abc import Iterable
from pathlib import Path

from probe.canonical_v2.contracts import (
    FULL_SOURCE_REQUIRED,
    GO_SOURCE_REQUIRED,
    LINEAGE_REQUIRED,
    RESCUE_REQUIRED,
)
from probe.canonical_v2.dag_contract import destination
from probe.canonical_v2.documentary_science import (
    has_stable_documentary_locus,
    read_documentary_facts,
)
from probe.resolver_lineage import _D2_REQUIRED
from probe.stage08_donors import read_accepted_associations
from probe.stage2c_alignment_completion import (
    DP_OUTPUT,
    FINAL_OUTPUT,
    NONEXACT,
    POLICY_SKIP,
)
from probe.stage2c_alignment_preview import JOBS_OUT, UNION_OUT
from probe.stage2c_candidate_union import merge_discovery
from probe.target_preparation import prepare_targets
from probe.workflow.artifacts import Artifact, StageManifest, sha256_file

RELEASES = {"START": "2023_01", "END": "2026_03"}
TARGET_METADATA = [("T1", "111", "P99901"), ("T2", "111", "P20001")]
TARGET_SEQUENCES = {
    "T1": "ACDEFGHIKLMNPQRSTVWY",
    "T2": "YWVTSRQPNMLKIHGFEDCA",
}
SCANNER_K = 5
ANCHOR_FIELDS = (
    "release",
    "target_id",
    "target_optional_accession",
    "direct_match_type",
    "direct_match_accession",
    "primary_accession",
    "entry",
    "rid",
    "raw_ox",
    "frag",
    "sequence_length",
    "pe",
    "sv",
    "sec",
    "gene",
)


def _write_tsv(
    path: Path,
    fields: Iterable[str],
    rows: Iterable[dict[str, str]],
) -> None:
    with path.open("x", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(
            stream,
            fieldnames=list(fields),
            delimiter="\t",
            lineterminator="\n",
            extrasaction="raise",
        )
        writer.writeheader()
        writer.writerows(rows)


def _run_source_cli(repo: Path, script: str, arguments: list[str]) -> None:
    path = repo / "scripts" / "pipeline" / script
    if not path.is_file() or path.is_symlink():
        raise ValueError(f"Missing ordinary source producer: {path}")
    subprocess.run(
        [sys.executable, str(path), *arguments],
        cwd=repo,
        check=True,
    )


def _records(role: str) -> list[dict[str, str]]:
    first_gene = "N:OLD_QUERY" if role == "START" else "N:END_ONLY|L:COLL_LOC"
    entries = [
        ("P10001", "111", "0", first_gene, TARGET_SEQUENCES["T1"]),
        ("P10002", "112", "0", "", TARGET_SEQUENCES["T1"]),
    ]
    if role == "START":
        entries.extend(
            [
                ("P10003", "111", "0", "N:END_ONLY", "C" * 20),
                ("P10004", "111", "0", "N:END-ONLY", "D" * 20),
                ("P10005", "111", "0", "L:COLL_LOC", "E" * 20),
                ("P10006", "111", "0", "L:COLL_LOC", "F" * 20),
                (
                    "P10007",
                    "112",
                    "0",
                    "N:END_ONLY",
                    TARGET_SEQUENCES["T1"][:-1] + "F",
                ),
            ]
        )
    entries.append(
        (
            "P20001",
            "111",
            "1",
            "L:NOEX_LOC",
            TARGET_SEQUENCES["T2"][:-1] + ("M" if role == "START" else "N"),
        )
    )
    return [
        {
            "rid": str(rid),
            "accession": accession,
            "entry": f"ENTRY_{accession}",
            "raw_ox": taxid,
            "frag": frag,
            "gene": gene,
            "sequence": sequence,
            "sequence_length": str(len(sequence)),
            "pe": "1",
            "sv": "1",
            "sec": "",
        }
        for rid, (accession, taxid, frag, gene, sequence) in enumerate(entries, 1)
    ]


def _full_bundle(root: Path, role: str, records: list[dict[str, str]]) -> Path:
    directory = root / "01A_FULL"
    directory.mkdir()
    fasta = directory / "01A_full_v2.fasta"
    with fasta.open("x", encoding="ascii", newline="\n") as stream:
        for row in records:
            rid = row["rid"]
            gene = f" GENE={row['gene']}" if row["gene"] else ""
            stream.write(
                f">sp|{row['accession']}|{row['entry']} RID={rid} "
                f"SRC=1 REC={rid} LINE={2 * int(rid) - 1} "
                f"OX={row['raw_ox']} FRAG={row['frag']} "
                f"LEN={row['sequence_length']} PE=1 SV=1{gene}\n"
                f"{row['sequence']}\n"
            )
    manifest = directory / "manifest.json"
    StageManifest(
        stage="01A_FULL",
        role=role,
        release_id=RELEASES[role],
        parameters={"synthetic": True},
        ontology=None,
        implementation={"name": "canonical-v2-source-fixture", "version": "1"},
        inputs=(),
        outputs=(Artifact(fasta.name, fasta.stat().st_size, sha256_file(fasta)),),
    ).write(manifest)
    return manifest


def _taxonomy_sources(root: Path) -> Path:
    directory = root / "taxonomy_raw"
    directory.mkdir()
    nodes = (
        ("1", "1", "no rank"),
        ("100", "1", "species"),
        ("111", "100", "strain"),
        ("112", "100", "strain"),
    )
    with (directory / "nodes.dmp").open("x", encoding="ascii") as stream:
        for taxid, parent, rank in nodes:
            stream.write(
                f"{taxid}\t|\t{parent}\t|\t{rank}\t|\t\t|\t0\t|\t0\t|\t1\t|\t"
                "0\t|\t0\t|\t0\t|\t0\t|\t0\t|\t\t|\n"
            )
    with (directory / "names.dmp").open("x", encoding="ascii") as stream:
        for taxid, _, _ in nodes:
            stream.write(
                f"{taxid}\t|\tSynthetic taxon {taxid}\t|\t\t|\tscientific name\t|\n"
            )
    for filename in ("merged.dmp", "delnodes.dmp"):
        (directory / filename).touch(exist_ok=False)
    return directory


def _go_observation(row: dict[str, str], ordinal: int) -> dict[str, str]:
    """Observed k=5 seed metrics for one identical/prefix-identical pair."""
    target = TARGET_SEQUENCES["T1"]
    candidate = row["sequence"]
    target_kmers = {
        target[i : i + SCANNER_K] for i in range(len(target) - SCANNER_K + 1)
    }
    candidate_kmers = {
        candidate[i : i + SCANNER_K] for i in range(len(candidate) - SCANNER_K + 1)
    }
    shared = len(target_kmers & candidate_kmers)
    exact = target == candidate
    covered = len(target) if exact else len(target) - 1
    result = dict.fromkeys(sorted(GO_SOURCE_REQUIRED), "")
    result.update(
        {
            "target_id": "T1",
            "target_bucket": "S:100",
            "rid": row["rid"],
            "record_number": str(ordinal),
            "accession": row["accession"],
            "entry": row["entry"],
            "raw_ox": row["raw_ox"],
            "resolved_taxid": row["raw_ox"],
            "species_taxid": "100",
            "frag": "0",
            "pe": row["pe"],
            "sv": row["sv"],
            "sec": row["sec"],
            "gene": row["gene"],
            "target_length": str(len(target)),
            "candidate_length": str(len(candidate)),
            "length_ratio": "1",
            "candidate_over_target_length": "1",
            "target_distinct_kmers": str(len(target_kmers)),
            "shared_distinct_kmers": str(shared),
            "target_shared_fraction": str(shared / len(target_kmers)),
            "exact_sequence": str(int(exact)),
            "shared_unique_positional_kmers": str(shared),
            "target_seed_span_fraction": str(covered / len(target)),
            "candidate_seed_span_fraction": str(covered / len(candidate)),
            "target_seed_residue_coverage": str(covered / len(target)),
            "candidate_seed_residue_coverage": str(covered / len(candidate)),
            "lis_count": str(shared),
            "lis_fraction": "1",
            "diagonal_min": "0",
            "diagonal_max": "0",
            "diagonal_range": "0",
        }
    )
    return result


def _release_sources(
    root: Path,
    repo: Path,
    role: str,
    targets: Path,
    taxonomy_raw: Path,
) -> dict[str, str]:
    release = RELEASES[role]
    folder = root / role
    folder.mkdir()
    records = _records(role)
    full_manifest = _full_bundle(folder, role, records)
    raw_ox = folder / "raw_ox.txt"
    raw_ox.write_text("111\n112\n", encoding="ascii")
    taxonomy_index = folder / "02A_taxonomy.sqlite"
    taxonomy = folder / "02B_target_taxonomy.tsv"
    ox_taxonomy = folder / "02B_ox_taxonomy.tsv"
    _run_source_cli(
        repo,
        "02_prepare_taxonomy_contexts.py",
        [
            "--taxon-dir",
            str(taxonomy_raw),
            "--snapshot-id",
            f"SYNTHETIC-{release}",
            "--index",
            str(taxonomy_index),
            "--targets",
            str(targets),
            "--ox-list",
            str(raw_ox),
            "--target-output",
            str(taxonomy),
            "--ox-output",
            str(ox_taxonomy),
            "--stats",
            str(folder / "02B_taxonomy.summary.json"),
        ],
    )
    raw_anchor = folder / "04A_direct_anchors.tsv"
    anchor_record = records[-1]
    _write_tsv(
        raw_anchor,
        ANCHOR_FIELDS,
        [
            {
                "release": release,
                "target_id": "T2",
                "target_optional_accession": "P20001",
                "direct_match_type": "PRIMARY",
                "direct_match_accession": "P20001",
                "primary_accession": "P20001",
                **{
                    key: anchor_record[key]
                    for key in (
                        "entry",
                        "rid",
                        "raw_ox",
                        "frag",
                        "sequence_length",
                        "pe",
                        "sv",
                        "sec",
                        "gene",
                    )
                },
            }
        ],
    )
    anchors = folder / "04B_classified_anchors.tsv"
    _run_source_cli(
        repo,
        "04_classify_direct_anchors.py",
        [
            "--anchors",
            str(raw_anchor),
            "--target-taxonomy",
            str(taxonomy),
            "--taxonomy-index",
            str(taxonomy_index),
            "--taxonomy-policy",
            "rank_aware",
            "--output",
            str(anchors),
            "--summary",
            str(folder / "04B_classified_anchors.summary.json"),
        ],
    )
    go_pairs = folder / "03_go_positive_pairs.tsv"
    go_rows = [_go_observation(records[0], 1)]
    if role == "START":
        # Positive FULL records 1,3,4,5,6,7 precede this selected GO ordinal.
        go_rows.append(_go_observation(records[6], 6))
    _write_tsv(go_pairs, sorted(GO_SOURCE_REQUIRED), go_rows)
    full_pairs = folder / "03_full_exact_pairs.tsv"
    cross_exact = records[1]
    _write_tsv(
        full_pairs,
        sorted(FULL_SOURCE_REQUIRED),
        [
            {
                "release": release,
                "target_id": "T1",
                "rid": "2",
                "record_number": "2",
                "accession": cross_exact["accession"],
                "raw_ox": "112",
                "target_resolved_taxid": "111",
                "candidate_resolved_taxid": "112",
                "species_taxid": "100",
                "frag": "0",
                "exact_full_sequence": "1",
                "length_aa": cross_exact["sequence_length"],
                "discovery_channel": "FULL_V2_EXACT",
            }
        ],
    )
    discovery = folder / "03_unified_discovery.tsv"
    merge_discovery(
        release=release,
        target_taxonomy=taxonomy,
        go_positive_pairs=go_pairs,
        full_exact_pairs=full_pairs,
        output=discovery,
        summary=folder / "03_unified_discovery.summary.json",
    )
    lineage_pairs = folder / "04E_lineage_pairs.tsv"
    rescue_pairs = folder / "04_rescue_pairs.tsv"
    lineage = folder / "04D2_lineage_evidence.tsv"
    _write_tsv(lineage_pairs, sorted(LINEAGE_REQUIRED), [])
    _write_tsv(rescue_pairs, sorted(RESCUE_REQUIRED), [])
    _write_tsv(lineage, sorted(_D2_REQUIRED), [])
    return {
        name: str(path)
        for name, path in {
            "unified_discovery": discovery,
            "go_positive_pairs": go_pairs,
            "full_exact_pairs": full_pairs,
            "lineage_pairs": lineage_pairs,
            "rescue_pairs": rescue_pairs,
            "taxonomy": taxonomy,
            "anchors": anchors,
            "lineage": lineage,
            "ox_taxonomy": ox_taxonomy,
            "full_manifest": full_manifest,
        }.items()
    }


def _expected() -> dict:
    def keys(role: str, target: str, *rids: int) -> list[list[str]]:
        return [[RELEASES[role], target, str(rid)] for rid in rids]

    exact = keys("START", "T1", 1, 2) + keys("END", "T1", 1, 2)
    no_exact = keys("START", "T2", 8) + keys("END", "T2", 3)
    weak = keys("START", "T1", 3, 5, 6)
    cross_nonexact = keys("START", "T1", 7)
    normalized = keys("START", "T1", 4)
    ledger = exact[:2] + cross_nonexact + exact[2:]
    complete = ledger[:3] + weak + no_exact[:1] + ledger[3:] + no_exact[1:]
    return {
        "synthetic_only": True,
        "targets": ["T1", "T2"],
        "no_exact_target": "T2",
        "scanner_k": SCANNER_K,
        "ledger_keys": ledger,
        "exact_keys": exact,
        "full_only_exact_keys": keys("START", "T1", 2) + keys("END", "T1", 2),
        "lookup_keys": weak + normalized + no_exact,
        "normalized_deferred_keys": normalized,
        "documentary_added_keys": weak + no_exact,
        "complete_workload_keys": complete,
        "dp_selected_keys": weak + no_exact,
        "dp_skipped_keys": cross_nonexact,
        "dp_skipped_reason": "CROSS_TAXID_NONEXACT",
        "no_exact_documentary_keys": no_exact,
        "stable_independent_locus": {
            "target_id": "T2",
            "key_type": "ORDERED_LOCUS",
            "key_value": "NOEX_LOC",
            "source_kind": "INDEPENDENT_04D1",
            "source_roles": "END:ORDERED_LOCUS,START:ORDERED_LOCUS",
        },
        "end_to_start_literal": {
            "key": keys("START", "T1", 3)[0],
            "source_kind": "EXACT",
            "exact_source_rid": "1",
            "exact_origin_release": RELEASES["END"],
            "source_key_type": "GENE_NAME",
            "source_key_value": "END_ONLY",
            "match_class": "LITERAL",
            "stable_locus": False,
        },
        "collision_keys": keys("START", "T1", 5, 6),
        "collision_locus": "COLL_LOC",
        "candidate_key_multiplicity": "2",
        "denied_donor_keys": weak + cross_nonexact,
        "absent_resolver_keys": normalized,
        "donor_keys": {
            "same_species_exact": exact + no_exact,
            "strict": keys("START", "T1", 1) + keys("END", "T1", 1) + no_exact,
        },
        "counts": {
            "full_records": {"START": 8, "END": 3},
            "ledger": {"START": 3, "END": 2},
            "exact_pairs": {"START": 2, "END": 2},
            "lookup_candidates": {"START": 5, "END": 1},
            "lookup_evidence": {"START": 6, "END": 2},
            "documentary_added": {"START": 4, "END": 1},
            "workload": {"START": 7, "END": 3},
            "dp_selected": {"START": 4, "END": 1},
            "dp_policy_skipped": {"START": 1, "END": 0},
            "normalized_deferred": {"START": 1, "END": 0},
            "donors_same_species_exact": {"START": 3, "END": 3},
            "donors_strict": {"START": 2, "END": 2},
        },
    }


def create_upstream_inputs(root: Path, source_repo: Path) -> dict:
    """Return JSON-serializable DAG source bindings and scientific expectations.

    ``root`` must be isolated test storage outside the immutable DAG output
    root. The caller adds GO inputs, tools and resource limits to the spec.
    No expected result is written into a scientific input or output bundle.
    """
    root = root.resolve() / "upstream_inputs"
    root.mkdir(parents=True, exist_ok=False)
    repo = source_repo.resolve()
    original = root / "original_target_mapping.tsv"
    _write_tsv(
        original,
        ("target_id", "ncbi_taxid", "uniprot_accession"),
        [
            dict(
                zip(
                    ("target_id", "ncbi_taxid", "uniprot_accession"),
                    row,
                    strict=True,
                )
            )
            for row in TARGET_METADATA
        ],
    )
    target_fasta = root / "submitted_targets.fasta"
    with target_fasta.open("x", encoding="ascii") as stream:
        for target_id, sequence in TARGET_SEQUENCES.items():
            stream.write(f">{target_id}\n{sequence}\n")
    prepared = prepare_targets(
        target_fasta=target_fasta,
        metadata_tsv=original,
        output_directory=root / "01",
    )
    if prepared.accepted_count != 2 or prepared.rejected_count:
        raise AssertionError("Synthetic canonical targets were rejected")
    targets = prepared.targets_path
    raw_taxonomy = _taxonomy_sources(root)
    per_release = {
        role: _release_sources(root, repo, role, targets, raw_taxonomy)
        for role in RELEASES
    }
    keys = root / "04C_frozen_lineage_keys.tsv"
    common = [
        "--targets",
        str(targets),
        "--start-anchors",
        per_release["START"]["anchors"],
        "--end-anchors",
        per_release["END"]["anchors"],
        "--start-release-id",
        RELEASES["START"],
        "--end-release-id",
        RELEASES["END"],
    ]
    _run_source_cli(
        repo,
        "04_compile_direct_lineage_keys.py",
        [
            *common,
            "--output",
            str(keys),
            "--summary",
            str(root / "04C_frozen_lineage_keys.summary.json"),
        ],
    )
    index = root / "04D1_lineage_scan_index.tsv"
    _run_source_cli(
        repo,
        "04D1_prepare_lineage_scan_index.py",
        [
            *common,
            "--keys",
            str(keys),
            "--start-target-taxonomy",
            per_release["START"]["taxonomy"],
            "--end-target-taxonomy",
            per_release["END"]["taxonomy"],
            "--output",
            str(index),
            "--summary",
            str(root / "04D1_lineage_scan_index.summary.json"),
        ],
    )
    return {
        "releases": dict(RELEASES),
        "source_root": str(root),
        "inputs": {
            "targets": str(targets),
            "targets_input": str(original),
            "lineage_index": str(index),
        },
        "per_release": per_release,
        "target_rows": [list(row) for row in TARGET_METADATA],
        "target_sequence_sha256": {
            target_id: hashlib.sha256(sequence.encode("ascii")).hexdigest()
            for target_id, sequence in TARGET_SEQUENCES.items()
        },
        "expected": _expected(),
    }


def _read_tsv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as stream:
        reader = csv.DictReader(stream, delimiter="\t")
        fields = reader.fieldnames
        if fields is None or len(fields) != len(set(fields)):
            raise AssertionError(f"Malformed observed TSV header: {path}")
        rows = list(reader)
    if any(None in row or None in row.values() for row in rows):
        raise AssertionError(f"Malformed observed TSV record: {path}")
    return rows


def _key(row: dict[str, str], rid_field: str = "rid") -> tuple[str, str, str]:
    return row["release"], row["target_id"], row[rid_field]


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def _expect_keys(
    label: str,
    rows: list[dict[str, str]],
    expected: list[list[str]],
    *,
    rid_field: str = "rid",
    unique: bool = True,
) -> set[tuple[str, str, str]]:
    keys = {_key(row, rid_field) for row in rows}
    wanted = {tuple(item) for item in expected}
    _require(
        keys == wanted,
        f"{label}: missing={sorted(wanted - keys)} extra={sorted(keys - wanted)}",
    )
    if unique:
        _require(len(keys) == len(rows), f"{label}: duplicate pair rows")
    return keys


def _expect_release_counts(
    label: str,
    rows: list[dict[str, str]],
    counts: dict[str, int],
) -> None:
    observed = {
        role: sum(row["release"] == release for row in rows)
        for role, release in RELEASES.items()
    }
    _require(observed == counts, f"{label}: observed release counts {observed}")


def _validate_discovery_outputs(spec: dict, expected: dict) -> None:
    ledger_rows = []
    exact_rows = []
    lookup_rows = []
    for role, release in RELEASES.items():
        ledger = _read_tsv(
            destination(spec, "ledger", role) / "candidate_provenance.tsv"
        )
        ledger_rows.extend(ledger)
        projection = _read_tsv(
            destination(spec, "projection", role) / "primary_candidate_facts.tsv"
        )
        _expect_keys(
            f"{role} projection preserves the ledger",
            projection,
            [list(_key(row)) for row in ledger],
        )
        _require(
            all(row["target_id"] != expected["no_exact_target"] for row in ledger),
            "No-EXACT documentary target was preloaded into the ledger",
        )
        full_only = next(row for row in projection if row["rid"] == "2")
        _require(
            full_only["metadata_source"] == "FULL_ORDINAL_ONLY"
            and full_only["go_positive_sequence_discovered"] == "0",
            "FULL-only EXACT was artificially promoted to GO-positive discovery",
        )
        exact_rows.extend(
            _read_tsv(destination(spec, "exact", role) / "exact_documentary_keys.tsv")
        )
        directory = destination(spec, "lookup", role)
        local_lookup = _read_tsv(directory / "documentary_nonexact_matches.tsv")
        _require(
            all(row["release"] == release for row in local_lookup),
            "Lookup observations crossed release branches",
        )
        _require(
            all(
                row["candidate_resolved_taxid"] == row["target_resolved_taxid"]
                for row in local_lookup
            ),
            "A cross-TaxID NONEXACT entered documentary lookup results",
        )
        summary = json.loads(
            (directory / "documentary_nonexact_matches.summary.json").read_text(
                encoding="utf-8"
            )
        )
        _require(
            summary["documentary_query_mode"] == "RETROSPECTIVE_START_END_UNION",
            "Lookup summary omitted retrospective documentary query semantics",
        )
        _require(
            summary["count"]["full_records_scanned"]
            == expected["counts"]["full_records"][role],
            "Release-local documentary lookup did not scan its complete FULL",
        )
        lookup_rows.extend(local_lookup)
    _expect_keys("ledger", ledger_rows, expected["ledger_keys"])
    _expect_release_counts("ledger", ledger_rows, expected["counts"]["ledger"])
    _expect_keys(
        "EXACT discovery",
        exact_rows,
        expected["exact_keys"],
        rid_field="exact_candidate_rid",
        unique=False,
    )
    _expect_keys(
        "lookup",
        lookup_rows,
        expected["lookup_keys"],
        rid_field="candidate_rid",
        unique=False,
    )
    _expect_release_counts(
        "lookup evidence",
        lookup_rows,
        expected["counts"]["lookup_evidence"],
    )
    source = expected["end_to_start_literal"]
    retrospective = [
        row for row in lookup_rows if _key(row, "candidate_rid") == tuple(source["key"])
    ]
    _require(bool(retrospective), "The END-only key did not recover START")
    fields = (
        "source_kind",
        "exact_source_rid",
        "exact_origin_release",
        "source_key_type",
        "source_key_value",
        "match_class",
    )
    _require(
        all(all(row[name] == source[name] for name in fields) for row in retrospective),
        "Retrospective lookup lost the exact source's original release",
    )
    normalized = {tuple(key) for key in expected["normalized_deferred_keys"]}
    collision = {tuple(key) for key in expected["collision_keys"]}
    independent = {tuple(key) for key in expected["no_exact_documentary_keys"]}
    locus = expected["stable_independent_locus"]
    for row in lookup_rows:
        key = _key(row, "candidate_rid")
        if key in normalized:
            _require(
                row["match_class"] == "NORMALIZED_HINT",
                "A normalization hint was reported as a literal match",
            )
        if key in collision:
            _require(
                row["source_key_type"] == "ORDERED_LOCUS"
                and row["source_key_value"] == expected["collision_locus"]
                and row["candidate_key_multiplicity"] == "2",
                "The two distinct FULL records lost their locus collision",
            )
        if key in independent and row["source_key_type"] == "ORDERED_LOCUS":
            _require(
                row["source_kind"] == locus["source_kind"]
                and row["source_roles"] == locus["source_roles"]
                and row["source_key_value"] == locus["key_value"]
                and row["source_key_origin"] == "ORIGINAL_DOCUMENTARY_KEY"
                and row["target_key_multiplicity"] == "1"
                and row["candidate_key_multiplicity"] == "1",
                "Native independent dual-anchor locus provenance was changed",
            )


def _validate_workload_outputs(spec: dict, expected: dict) -> dict:
    b3 = destination(spec, "b3")
    seed_rows = _read_tsv(b3 / "05A2_final_candidate_union.tsv")
    _expect_keys(
        "seed before documentary additions", seed_rows, expected["ledger_keys"]
    )
    seed_summary = json.loads(
        (b3 / "canonical_stage05_06a.summary.json").read_text(encoding="utf-8")
    )
    _require(
        seed_summary["historical_05_07_inputs"] == 0,
        "The canonical seed consumed a historical Stage05/07 publication",
    )
    handoff = _read_tsv(
        destination(spec, "handoff") / "documentary_candidate_handoff.tsv"
    )
    _expect_keys("handoff", handoff, expected["lookup_keys"])
    _expect_release_counts(
        "handoff",
        handoff,
        expected["counts"]["lookup_candidates"],
    )
    _require(
        all(row["go_donor_authorized"] == "0" for row in handoff),
        "Documentary handoff attempted to authorize GO donors",
    )
    directory = destination(spec, "workload")
    primary = _read_tsv(directory / UNION_OUT)
    jobs = _read_tsv(directory / JOBS_OUT)
    _expect_keys(
        "complete candidate workload", primary, expected["complete_workload_keys"]
    )
    _expect_keys("complete alignment jobs", jobs, expected["complete_workload_keys"])
    _expect_release_counts("workload", jobs, expected["counts"]["workload"])
    deferred = _read_tsv(directory / "documentary_normalized_only_deferred.tsv")
    _expect_keys(
        "normalized-only deferred records",
        deferred,
        expected["normalized_deferred_keys"],
    )
    exact_keys = {tuple(key) for key in expected["exact_keys"]}
    added = {tuple(key) for key in expected["documentary_added_keys"]}
    no_exact = {tuple(key) for key in expected["no_exact_documentary_keys"]}
    target_buckets = {
        (release, row["target_id"]): row["primary_bucket"]
        for role, release in RELEASES.items()
        for row in _read_tsv(Path(spec["per_release"][role]["taxonomy"]))
    }
    for row in primary:
        if _key(row) in added:
            _require(
                row["target_primary_bucket"] == target_buckets[_key(row)[:2]],
                "Documentary addition lost the native target taxonomy bucket",
            )
    for row in jobs:
        _require(
            (row["exact_sequence"] == "1") == (_key(row) in exact_keys),
            "Literal EXACT status differs from source FULL sequences",
        )
        if _key(row) in added:
            _require(
                row["discovery_channel"] == "CANONICAL_DOCUMENTARY_V2"
                and row["sequence_discovered"] == "0",
                "Documentary-only candidate pretended to have scanner support",
            )
        if _key(row) in no_exact:
            _require(row["frag"] == "1", "Documented fragment flag was lost")
    facts = read_documentary_facts(
        path=directory / "documentary_evidence_preserved.tsv",
        releases=spec["releases"],
    )
    for key in no_exact:
        _require(
            has_stable_documentary_locus(facts.get(key)),
            f"Native 04D1 corroborated locus is not stable at {key}",
        )
    for key in expected["denied_donor_keys"]:
        _require(
            not has_stable_documentary_locus(facts.get(tuple(key))),
            f"Weak/colliding evidence became stable at {key}",
        )
    for key in expected["collision_keys"]:
        record = facts.get(tuple(key))
        _require(
            record is not None and record.collision_present,
            f"Candidate locus collision was lost at {key}",
        )
    return {"primary": primary, "jobs": jobs, "facts": facts}


def _validate_alignment_outputs(spec: dict, expected: dict, workload: dict) -> int:
    directory = destination(spec, "alignment")
    selected = _read_tsv(directory / NONEXACT)
    skipped = _read_tsv(directory / POLICY_SKIP)
    raw_dp = _read_tsv(directory / DP_OUTPUT)
    final = _read_tsv(directory / FINAL_OUTPUT)
    _expect_keys("NONEXACT selected for DP", selected, expected["dp_selected_keys"])
    _expect_keys("raw C++ DP rows", raw_dp, expected["dp_selected_keys"])
    _expect_keys("cross-TaxID policy exclusions", skipped, expected["dp_skipped_keys"])
    _require(
        all(row["reason"] == expected["dp_skipped_reason"] for row in skipped),
        "A NONEXACT candidate was skipped for an unexpected reason",
    )
    final_expected = expected["exact_keys"] + expected["dp_selected_keys"]
    _expect_keys("final directional alignment coverage", final, final_expected)
    primary = {_key(row): row for row in workload["primary"]}
    targets = {
        (release, row["target_id"]): row["resolved_taxid"]
        for role, release in RELEASES.items()
        for row in _read_tsv(Path(spec["per_release"][role]["taxonomy"]))
    }
    for row in selected:
        key = _key(row)
        _require(
            row["exact_sequence"] == "0"
            and primary[key]["resolved_taxid"] == targets[key[:2]],
            "DP input is not a NONEXACT same-resolved-TaxID pair",
        )
    exact_keys = {tuple(key) for key in expected["exact_keys"]}
    cells = 0
    for row in final:
        for prefix in ("target_global", "candidate_global"):
            observed = int(row[f"{prefix}_cells_evaluated"])
            if _key(row) in exact_keys:
                _require(
                    observed == 0 and row[f"{prefix}_status"] == "OK_EXACT",
                    "EXACT consumed DP or lost exact directional facts",
                )
            else:
                required = (int(row["target_length"]) + 1) * (
                    int(row["candidate_length"]) + 1
                )
                _require(
                    observed == required and row[f"{prefix}_status"] == "OK",
                    "NONEXACT lacks complete directional C++ DP evidence",
                )
                cells += observed
    summary = json.loads(
        (directory / "canonical_alignment_execution.summary.json").read_text(
            encoding="utf-8"
        )
    )
    _require(
        summary["dp_cells"] == cells and summary["dp_chunks"] > 0,
        "Recorded C++ DP work does not agree with numerical output",
    )
    return cells


def _validate_resolver_outputs(spec: dict, expected: dict) -> dict[str, int]:
    directory = destination(spec, "stage07")
    complete = _read_tsv(directory / "07_complete_decisions.tsv")
    _expect_keys(
        "resolver candidate coverage", complete, expected["complete_workload_keys"]
    )
    observed = {_key(row) for row in complete if row["annotation_donor"] == "YES"}
    wanted = {tuple(key) for key in expected["donor_keys"][spec["policy"]]}
    _require(
        observed == wanted, f"Resolver donor keys differ: observed={sorted(observed)}"
    )
    index = read_accepted_associations(
        directory / "07_accepted_associations.tsv",
        start_release_id=RELEASES["START"],
        end_release_id=RELEASES["END"],
        source_database="UniProtKB",
    )
    _require(
        {row.key for row in index.associations if row.may_donate} == wanted,
        "Native M8 donor reader sees a different Stage07 donor set",
    )
    records = {_key(row): row for row in complete}
    for key in expected["no_exact_documentary_keys"]:
        row = records[tuple(key)]
        _require(
            row["exact_sequence"] == "0"
            and row["decision_basis"] == "DIRECT_ACCESSION_CONTINUITY"
            and "FRAGMENT_RECORD" in row["warnings"],
            "No-EXACT donor lost direct/documentary fragment provenance",
        )
    for key in expected["denied_donor_keys"]:
        _require(
            records[tuple(key)]["annotation_donor"] == "NO",
            "Weak, colliding or cross-TaxID NONEXACT became a donor",
        )
    for key in expected["collision_keys"]:
        _require(
            "COLLIDING_DOCUMENTARY_KEY_PRESENT" in records[tuple(key)]["warnings"],
            "Resolver omitted the documentary collision warning",
        )
    donor_counts = {
        role: sum(key[0] == release for key in observed)
        for role, release in RELEASES.items()
    }
    _require(
        donor_counts == expected["counts"][f"donors_{spec['policy']}"],
        f"Unexpected per-release donor counts: {donor_counts}",
    )
    return donor_counts


def validate_upstream_outputs(spec: dict, fixture_expected: dict) -> dict:
    """Check actual DAG products through the native Stage07/M8 donor boundary.

    This reader does not run or repair any scientific stage. Receipt and bundle
    integrity, the M8/M10c/Temporal products and tool attestation are checked by
    the caller. The returned report exists only after every scientific check.
    """
    _require(spec["releases"] == RELEASES, "Unexpected synthetic release mapping")
    _require(spec["mode"] == "execute", "The full synthetic DAG did not execute")
    _require(
        spec["limits"]["documentary_fragment"] == "allow",
        "This positive documented-fragment fixture requires the allow policy",
    )
    _validate_discovery_outputs(spec, fixture_expected)
    workload = _validate_workload_outputs(spec, fixture_expected)
    cells = _validate_alignment_outputs(spec, fixture_expected, workload)
    donors = _validate_resolver_outputs(spec, fixture_expected)
    return {
        "synthetic_only": True,
        "policy": spec["policy"],
        "candidate_pairs": len(workload["jobs"]),
        "dp_pairs": len(fixture_expected["dp_selected_keys"]),
        "dp_policy_skipped": len(fixture_expected["dp_skipped_keys"]),
        "dp_directional_cells": cells,
        "donors": donors,
        "exact_discovery_separate_releases": True,
        "end_to_start_documentary_provenance_preserved": True,
        "no_exact_target_documentary_recovered": True,
        "independent_native_04d1_locus_preserved": True,
        "normalized_hint_deferred": True,
        "colliding_locus_denied": True,
    }
