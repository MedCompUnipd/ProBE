"""M11/M12 benchmark projection of immutable M10c propagated GO supports.

The GO universe is already closed in M10c. This stage selects evidence,
compares START/END, and emits EVAL/SKIP without fabricating negative truth.
A selected NOT overrides either positive EVAL or SKIP to make the prediction
score as FP in an otherwise assessable target/aspect.
"""

from __future__ import annotations

import csv
import hashlib
import json
import os
import re
import shutil
import sqlite3
import tempfile
from collections import Counter
from collections.abc import Mapping
from pathlib import Path

from probe.stage08_evidence_registry import (
    EvidenceRegistry,
    EvidenceSelections,
    compile_selections,
    read_json_object,
)

SCHEMA = "stage08-temporal-eval-skip-v2"
POLICY_SCHEMA = "stage08-temporal-eval-skip-policy-v1"
ROOTS = frozenset({"GO:0003674", "GO:0008150", "GO:0005575"})

START_ANY = 1
START_PRIOR = 2
START_PRIOR_DIRECT = 4
START_ANY_DIRECT = 8
END_ANY = 16
END_ANY_DIRECT = 32
END_SCORE = 64
END_SCORE_DIRECT = 128
END_NEUTRAL = 256
NOT_START = 512
NOT_END = 1024
START_SCORE_DIRECT = 2048

TERM_HEADER = ("query_id", "aspect", "go_id", "status")
ASPECT_HEADER = (
    "query_id",
    "aspect",
    "eval_terms",
    "skip_terms",
    "not_terms",
    "known_start_aspect",
    "known_start_global",
)
EVENT_HEADER = (
    "query_id",
    "target_id",
    "aspect",
    "go_id",
    "event",
    "direct_end_score",
    "start_known",
    "end_known",
    "benchmark_status",
)
NOT_HEADER = (
    "query_id",
    "target_id",
    "aspect",
    "go_id",
    "not_start",
    "not_end",
)


def file_sha256(path: Path) -> str:
    sha = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(8 * 1024 * 1024), b""):
            sha.update(block)
    return sha.hexdigest()


def _stamp(path: Path) -> tuple[int, ...]:
    stat = path.stat()
    return (stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns)


def load_target_ids(
    path: Path,
    *,
    allow_identical_duplicates: bool = False,
) -> dict[str, str]:
    """Use the input's original UniProt accession, never donor accession."""
    if path.is_symlink() or not path.is_file():
        raise ValueError("Original target mapping must be a regular file")
    mapping: dict[str, str] = {}
    accession_owner: dict[str, str] = {}
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.reader(handle, delimiter="\t")
        if next(reader, None) != ["target_id", "ncbi_taxid", "uniprot_accession"]:
            raise ValueError("Unexpected inputs/targets.tsv header")
        for line, row in enumerate(reader, start=2):
            if len(row) != 3 or not all(row):
                raise ValueError(f"Invalid target mapping at line {line}")
            internal, taxon, accession = row
            if not re.fullmatch(r"T[0-9]+", internal):
                raise ValueError(f"Invalid internal target ID at line {line}")
            if not taxon.isdecimal() or any(c.isspace() for c in accession):
                raise ValueError(f"Invalid target metadata at line {line}")
            if internal in mapping:
                raise ValueError(f"Duplicate target ID at line {line}")
            if accession in accession_owner and not allow_identical_duplicates:
                raise ValueError(f"Duplicate original accession at line {line}")
            mapping[internal] = accession
            accession_owner[accession] = internal
    if not mapping:
        raise ValueError("Empty original target mapping")
    return mapping


def crosscheck_stage01_mapping(
    path: Path,
    mapping: Mapping[str, str],
) -> int:
    """Independently compare every 01_targets accession with input map."""
    if path.is_symlink() or not path.is_file():
        raise ValueError("Stage 01 canonical target table is missing")
    count = 0
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.reader(handle, delimiter="\t")
        if next(reader, None) != [
            "target_id",
            "raw_taxid",
            "optional_accession",
            "normalized_sequence",
            "sequence_length",
            "sequence_sha256",
            "normalization_policy_version",
        ]:
            raise ValueError("Unexpected 01_targets.tsv schema")
        for line, row in enumerate(reader, start=2):
            if len(row) != 7 or row[0] not in mapping:
                raise ValueError(f"Missing/malformed Stage 01 target: {line}")
            if row[2] not in {"", ".", mapping[row[0]]}:
                raise ValueError(
                    f"Input/Stage 01 original accession mismatch: {row[0]}"
                )
            count += 1
    if count != len(mapping):
        raise ValueError("Stage 01 target mapping cardinality mismatch")
    return count


def resolve_identical_target_aliases(
    targets_input: Path,
    stage01_targets: Path,
    mapping: Mapping[str, str],
) -> tuple[dict[str, str], list[tuple[str, str, str, str, str]]]:
    """Coalesce original-ID collisions only for identical biological targets.

    Keep M8/M10c target IDs intact. Only the derived benchmark state uses
    the lexically first internal ID as the canonical key. The alias ledger
    preserves both internal IDs without inventing a new prediction query ID.
    """
    groups: dict[str, list[str]] = {}
    for target, accession in mapping.items():
        groups.setdefault(accession, []).append(target)
    repeated = {accession: ids for accession, ids in groups.items() if len(ids) > 1}
    if not repeated:
        return {}, []
    candidate_ids = {target for ids in repeated.values() for target in ids}
    input_taxa: dict[str, str] = {}
    with targets_input.open("r", encoding="utf-8", newline="") as stream:
        reader = csv.reader(stream, delimiter="\t")
        next(reader)
        for row in reader:
            if row[0] in candidate_ids:
                input_taxa[row[0]] = row[1]
    source_data: dict[str, tuple[str, str, str]] = {}
    with stage01_targets.open("r", encoding="utf-8", newline="") as stream:
        reader = csv.reader(stream, delimiter="\t")
        next(reader)
        for row in reader:
            target = row[0]
            if target not in candidate_ids:
                continue
            if len(row) != 7 or row[1] != input_taxa[target]:
                raise ValueError(f"Collision target/taxon mismatch: {target}")
            if row[2] != mapping[target]:
                raise ValueError(f"Collision original accession mismatch: {target}")
            sequence = row[3]
            if (
                len(sequence) != int(row[4])
                or hashlib.sha256(sequence.encode("ascii")).hexdigest() != row[5]
            ):
                raise ValueError(f"Collision sequence integrity error: {target}")
            source_data[target] = (row[1], row[5], row[6])
    if set(source_data) != candidate_ids:
        raise ValueError("Missing Stage 01 records for accession collision")
    aliases: dict[str, str] = {}
    ledger: list[tuple[str, str, str, str, str]] = []
    for accession, ids in sorted(repeated.items()):
        if len({source_data[target] for target in ids}) != 1:
            raise ValueError(
                f"Non-identical targets share original accession: {accession}"
            )
        canonical = min(ids)
        taxon, sequence_sha, _ = source_data[canonical]
        for target in sorted(ids):
            if target != canonical:
                aliases[target] = canonical
                ledger.append(
                    (
                        accession,
                        canonical,
                        target,
                        taxon,
                        sequence_sha,
                    )
                )
    return aliases, ledger


def read_policy(
    registry_path: Path,
    policy_path: Path,
) -> tuple[EvidenceRegistry, EvidenceSelections, dict]:
    registry = EvidenceRegistry.from_json(registry_path)
    policy = read_json_object(policy_path)
    if (
        set(policy)
        != {
            "schema_version",
            "evidence_selections",
            "not_roles",
            "scoring_scope",
            "not_precedence",
            "benchmark_mode",
        }
        or policy["schema_version"] != POLICY_SCHEMA
    ):
        raise ValueError("Invalid temporal benchmark policy schema")
    if policy["benchmark_mode"] != "novel_acquisition":
        raise ValueError("Only explicit novel_acquisition mode is implemented")
    if policy["scoring_scope"] != "target_aspects_with_eval":
        raise ValueError("Explicit target_aspects_with_eval required")
    if policy["not_precedence"] != "not_is_fp":
        raise ValueError("NOT must be an FP, never silently SKIP")
    roles = policy["not_roles"]
    if not isinstance(roles, list) or not roles:
        raise ValueError("Explicit nonempty not_roles required")
    if len(roles) != len(set(roles)) or not set(roles) <= {"START", "END"}:
        raise ValueError("not_roles must select START and/or END")
    selections = compile_selections(registry, policy["evidence_selections"])
    if not selections.by_purpose["score"].allowed_codes:
        raise ValueError("A nonempty END scoring evidence profile is required")
    return registry, selections, policy


def inspect_m10c_bundle(bundle: Path) -> tuple[Path, dict]:
    if bundle.is_symlink() or not bundle.is_dir():
        raise ValueError("M10c must be a non-symlink directory")
    manifest_file = bundle / "manifest.json"
    if manifest_file.is_symlink() or not manifest_file.is_file():
        raise ValueError("M10c manifest missing/nonregular")
    manifest = read_json_object(manifest_file)
    if (
        manifest.get("schema_version") != "stage08-m10c-compact-interpretation-v1"
        or manifest.get("evaluation_policy") != "not_computed"
        or manifest.get("not_policy") != "preserved_no_veto"
        or set(manifest.get("outputs", {}))
        != {"direct_provenance.sqlite3", "propagated_terms.tsv"}
    ):
        raise ValueError("M10c contract mismatch")
    for name, metadata in manifest["outputs"].items():
        path = bundle / name
        if path.is_symlink() or not path.is_file():
            raise ValueError(f"M10c output absent or symlink: {name}")
        if path.stat().st_size != metadata["bytes"]:
            raise ValueError(f"M10c output size mismatch: {name}")
        if not re.fullmatch(r"[a-f0-9]{64}", metadata["sha256"]):
            raise ValueError(f"Invalid M10c output SHA256: {name}")
    db = bundle / "direct_provenance.sqlite3"
    return db, manifest


def _state_db(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.executescript("""
        PRAGMA journal_mode=DELETE;
        PRAGMA synchronous=FULL;
        PRAGMA temp_store=FILE;
        PRAGMA cache_size=-32768;
        CREATE TABLE terms (
            target_id TEXT NOT NULL,
            aspect TEXT NOT NULL,
            go_id TEXT NOT NULL,
            flags INTEGER NOT NULL,
            PRIMARY KEY (target_id, aspect, go_id)
        ) WITHOUT ROWID;
    """)
    return conn


def _import_codes(
    source: sqlite3.Connection,
    output: sqlite3.Connection,
    selections: EvidenceSelections,
    registry: EvidenceRegistry,
    not_roles: frozenset[str],
    start_release: str,
    end_release: str,
    mapping: Mapping[str, str],
    canonical_aliases: Mapping[str, str],
) -> tuple[int, int]:
    codes = {
        purpose: profile.allowed_codes
        for purpose, profile in selections.by_purpose.items()
    }
    current: tuple[str, str, str, str] | None = None
    combined_flags = 0
    logical_rows = 0
    observed_codes: set[str] = set()
    seen_releases: set[str] = set()
    batches: list[tuple[str, str, str, int]] = []
    upsert = """INSERT INTO terms VALUES (?, ?, ?, ?)
        ON CONFLICT(target_id, aspect, go_id)
        DO UPDATE SET flags = terms.flags | excluded.flags"""

    def flush() -> None:
        nonlocal logical_rows, combined_flags
        if current is None:
            return
        _, target, aspect, go = current
        canonical = canonical_aliases.get(target, target)
        batches.append((canonical, aspect, go, combined_flags))
        logical_rows += 1
        if len(batches) >= 8192:
            output.executemany(upsert, batches)
            batches.clear()
            output.commit()

    incoming = source.execute("""
        SELECT release, target_id, aspect, go_id, polarity, evidence_code,
               support_count, direct_count
        FROM term_code_support
        ORDER BY release, target_id, aspect, go_id, polarity, evidence_code
    """)
    for release, target, aspect, go, polarity, code, count, direct in incoming:
        key = (release, target, aspect, go)
        if current != key:
            flush()
            current = key
            combined_flags = 0
        if target not in mapping or aspect not in {"F", "P", "C"}:
            raise ValueError(f"Unmapped target or unknown aspect: {target}")
        if release not in {start_release, end_release}:
            raise ValueError(f"Unexpected M10c release: {release}")
        seen_releases.add(release)
        if count <= 0 or direct < 0 or direct > count:
            raise ValueError("Invalid M10c support count")
        observed_codes.add(code)
        if polarity == "POSITIVE":
            if release == start_release:
                combined_flags |= START_ANY
                if direct:
                    combined_flags |= START_ANY_DIRECT
                    if code in codes["score"]:
                        combined_flags |= START_SCORE_DIRECT
                if code in codes["prior"]:
                    combined_flags |= START_PRIOR
                    if direct:
                        combined_flags |= START_PRIOR_DIRECT
            else:
                combined_flags |= END_ANY
                if direct:
                    combined_flags |= END_ANY_DIRECT
                if code in codes["score"]:
                    combined_flags |= END_SCORE
                    if direct:
                        combined_flags |= END_SCORE_DIRECT
                if code in codes["neutral"]:
                    combined_flags |= END_NEUTRAL
        elif polarity == "NOT":
            if code in codes["not"]:
                if release == start_release and "START" in not_roles:
                    combined_flags |= NOT_START
                if release == end_release and "END" in not_roles:
                    combined_flags |= NOT_END
        else:
            raise ValueError("M10c source polarity is invalid")
    flush()
    if batches:
        output.executemany(upsert, batches)
        output.commit()
    registry.require_observed_reviewed(observed_codes)
    if seen_releases != {start_release, end_release}:
        raise ValueError("Both START and END must exist in M10c codes")
    return logical_rows, len(observed_codes)


def _status(flags: int) -> str:
    if flags & (NOT_START | NOT_END):
        return ""
    if flags & (START_PRIOR | END_NEUTRAL):
        return "SKIP"
    if flags & END_SCORE:
        return "EVAL"
    return ""


def _write_outputs(
    state: sqlite3.Connection,
    destination: Path,
    mapping: Mapping[str, str],
    graph: object,
) -> Counter:
    counts: Counter = Counter()
    with (
        (destination / "benchmark_terms.tsv").open(
            "w", encoding="utf-8", newline=""
        ) as out_terms,
        (destination / "target_aspects.tsv").open(
            "w", encoding="utf-8", newline=""
        ) as out_aspects,
        (destination / "temporal_events.tsv").open(
            "w", encoding="utf-8", newline=""
        ) as out_events,
        (destination / "negative_terms.tsv").open(
            "w", encoding="utf-8", newline=""
        ) as out_not,
    ):
        terms = csv.writer(out_terms, delimiter="\t", lineterminator="\n")
        aspects = csv.writer(out_aspects, delimiter="\t", lineterminator="\n")
        events = csv.writer(out_events, delimiter="\t", lineterminator="\n")
        negatives = csv.writer(out_not, delimiter="\t", lineterminator="\n")
        for writer, header in (
            (terms, TERM_HEADER),
            (aspects, ASPECT_HEADER),
            (events, EVENT_HEADER),
            (negatives, NOT_HEADER),
        ):
            writer.writerow(header)

        # Process one target across its three aspects; bounded GO state.
        cursor = state.execute("""
            SELECT target_id, aspect, go_id, flags FROM terms
            ORDER BY target_id, aspect, go_id
        """)
        pending_target: str | None = None
        per_target: dict[str, list[tuple[str, int]]] = {}

        def emit_target() -> None:
            if pending_target is None:
                return
            query_id = mapping[pending_target]
            global_prior = any(
                flags & START_PRIOR
                for values in per_target.values()
                for _, flags in values
            )
            for aspect, values in sorted(per_target.items()):
                priors = {go for go, flags in values if flags & START_PRIOR_DIRECT}
                aspect_prior = any(flags & START_PRIOR for _, flags in values)
                eval_count = skip_count = negative_count = 0
                held: list[tuple[str, str]] = []
                for go, flags in values:
                    if go in ROOTS:
                        counts["root_excluded"] += 1
                        continue
                    label = _status(flags)
                    has_not = bool(flags & (NOT_START | NOT_END))
                    if has_not:
                        negative_count += 1
                        negatives.writerow(
                            (
                                query_id,
                                pending_target,
                                aspect,
                                go,
                                int(bool(flags & NOT_START)),
                                int(bool(flags & NOT_END)),
                            )
                        )
                        if flags & (START_PRIOR | END_NEUTRAL | END_SCORE):
                            counts["not_positive_overlap"] += 1
                    if label:
                        held.append((go, label))
                        if label == "EVAL":
                            eval_count += 1
                        else:
                            skip_count += 1
                    if flags & END_SCORE_DIRECT:
                        if flags & START_SCORE_DIRECT:
                            event = "DIRECT_MAINTAINED"
                        elif flags & START_ANY_DIRECT:
                            event = "DIRECT_CONFIRMATION"
                        elif flags & START_PRIOR:
                            event = "DIRECT_PREVIOUSLY_IMPLIED"
                        elif not global_prior:
                            event = "DIRECT_NEW_GLOBAL"
                        elif not aspect_prior:
                            event = "DIRECT_NEW_ASPECT"
                        elif any(
                            parent in priors
                            for parent in graph.ancestors(go)
                            if parent not in ROOTS and parent != go
                        ):
                            event = "DIRECT_REFINEMENT"
                        else:
                            event = "DIRECT_BRANCH_ACQUISITION"
                        events.writerow(
                            (
                                query_id,
                                pending_target,
                                aspect,
                                go,
                                event,
                                "1",
                                int(bool(flags & START_PRIOR)),
                                int(bool(flags & END_ANY)),
                                label or "NONE",
                            )
                        )
                        counts[event] += 1
                    if flags & START_ANY_DIRECT and not flags & END_ANY_DIRECT:
                        events.writerow(
                            (
                                query_id,
                                pending_target,
                                aspect,
                                go,
                                "DIRECT_LOSS",
                                "0",
                                int(bool(flags & START_PRIOR)),
                                int(bool(flags & END_ANY)),
                                label or "NONE",
                            )
                        )
                        counts["DIRECT_LOSS"] += 1
                    if flags & START_ANY and not flags & END_ANY:
                        counts["propagated_support_lost"] += 1
                if eval_count:
                    for go, label in held:
                        terms.writerow((query_id, aspect, go, label))
                        counts[f"benchmark_{label}"] += 1
                    aspects.writerow(
                        (
                            query_id,
                            aspect,
                            eval_count,
                            skip_count,
                            negative_count,
                            int(aspect_prior),
                            int(global_prior),
                        )
                    )
                    counts["eligible_target_aspects"] += 1
                else:
                    counts["target_aspects_without_eval"] += 1

        for target, aspect, go, flags in cursor:
            if pending_target != target:
                emit_target()
                pending_target = target
                per_target = {}
            per_target.setdefault(aspect, []).append((go, flags))
        emit_target()
    return counts


def build_temporal_benchmark(
    *,
    m10c_bundle: Path,
    targets_input: Path,
    stage01_targets: Path,
    registry_path: Path,
    policy_path: Path,
    destination: Path,
    graph: object,
    start_release: str,
    end_release: str,
    m8_summary: Path | None = None,
    execute: bool = False,
    implementation_script: Path | None = None,
) -> dict:
    """Preflight by default; explicit execution atomically publishes outputs."""
    db, manifest = inspect_m10c_bundle(m10c_bundle)
    registry, selections, policy = read_policy(registry_path, policy_path)
    mapping = load_target_ids(targets_input, allow_identical_duplicates=True)
    mapped = crosscheck_stage01_mapping(stage01_targets, mapping)
    canonical_aliases, alias_ledger = resolve_identical_target_aliases(
        targets_input, stage01_targets, mapping
    )
    if not re.fullmatch(r"[0-9]{4}_[0-9]{2}", start_release):
        raise ValueError("Invalid explicit START release")
    if not re.fullmatch(r"[0-9]{4}_[0-9]{2}", end_release):
        raise ValueError("Invalid explicit END release")
    if start_release >= end_release:
        raise ValueError("START release must precede END release")
    releases = [start_release, end_release]
    if m8_summary is not None:
        if m8_summary.is_symlink() or not m8_summary.is_file():
            raise ValueError("M8 summary must be a regular file")
        m8_report = read_json_object(m8_summary)
        for role, release in (("START", start_release), ("END", end_release)):
            if m8_report["by_role"][role]["release"] != release:
                raise ValueError(f"M8 {role} release does not match selection")
            source_info = manifest["source_payloads"][role]
            expected_sha = m8_report["by_role"][role]["normalized_gaf_sha256"]
            if source_info["gaf_source_sha256"] != expected_sha:
                raise ValueError(f"M8/M10c {role} source identity mismatch")
    if destination.is_symlink() or destination.exists():
        raise FileExistsError("Temporal benchmark destination already exists")
    if destination.parent.is_symlink() or not destination.parent.is_dir():
        raise ValueError("Output parent must be a real directory")
    report = {
        "schema_version": SCHEMA,
        "mode": "EXECUTE" if execute else "PREFLIGHT_ONLY",
        "original_target_ids": mapped,
        "distinct_original_query_ids": mapped - len(canonical_aliases),
        "identical_duplicate_target_aliases": len(canonical_aliases),
        "identity_collapse_policy": "same_accession_taxon_sequence_only",
        "start_release": start_release,
        "end_release": end_release,
        "m8_summary_sha256": (
            file_sha256(m8_summary) if m8_summary is not None else None
        ),
        "registry_fingerprint": registry.fingerprint,
        "selection_fingerprint": selections.fingerprint,
        "ontology_sha256": manifest["ontology_sha256"],
        "m10c_manifest_sha256": file_sha256(m10c_bundle / "manifest.json"),
        "policy_sha256": file_sha256(policy_path),
        "registry_sha256": file_sha256(registry_path),
        "not_roles": sorted(policy["not_roles"]),
        "scoring_scope": policy["scoring_scope"],
        "output_published": False,
    }
    if not execute:
        return report
    upstream_paths = [
        m10c_bundle / "manifest.json",
        db,
        m10c_bundle / "propagated_terms.tsv",
        targets_input,
        stage01_targets,
        registry_path,
        policy_path,
    ]
    if m8_summary is not None:
        upstream_paths.append(m8_summary)
    before = {str(path): _stamp(path) for path in upstream_paths}
    code_files = [Path(__file__)]
    if implementation_script is not None:
        code_files.append(implementation_script)
    code_hashes = {str(path): file_sha256(path) for path in code_files}
    for name, data in manifest["outputs"].items():
        if file_sha256(m10c_bundle / name) != data["sha256"]:
            raise ValueError(f"M10c content checksum mismatch: {name}")
    source = sqlite3.connect(f"file:{db}?mode=ro&immutable=1", uri=True)
    staging = Path(tempfile.mkdtemp(prefix=".m11m12_staging_", dir=destination.parent))
    try:
        state = _state_db(staging / "build_state.sqlite3")
        try:
            logical, observed = _import_codes(
                source,
                state,
                selections,
                registry,
                frozenset(policy["not_roles"]),
                releases[0],
                releases[1],
                mapping,
                canonical_aliases,
            )
            if logical != manifest["distinct_propagated_terms"]:
                raise ValueError("M10c distinct term count mismatch at EOF")
            totals = _write_outputs(state, staging, mapping, graph)
            if not totals["benchmark_EVAL"]:
                raise ValueError("No EVAL terms under selected policy")
        finally:
            state.close()
        with (staging / "duplicate_targets.tsv").open(
            "w", encoding="utf-8", newline=""
        ) as handle:
            writer = csv.writer(handle, delimiter="\t", lineterminator="\n")
            writer.writerow(
                (
                    "query_id",
                    "canonical_target_id",
                    "duplicate_target_id",
                    "ncbi_taxid",
                    "sequence_sha256",
                )
            )
            writer.writerows(alias_ledger)
        # Scratch state is not part of the publication contract.
        (staging / "build_state.sqlite3").unlink()
        files = {}
        for name in (
            "benchmark_terms.tsv",
            "target_aspects.tsv",
            "temporal_events.tsv",
            "negative_terms.tsv",
            "duplicate_targets.tsv",
        ):
            path = staging / name
            files[name] = {
                "bytes": path.stat().st_size,
                "sha256": file_sha256(path),
            }
        for path in upstream_paths:
            if _stamp(path) != before[str(path)]:
                raise ValueError("Source modified during temporal assessment")
        for path in code_files:
            if file_sha256(path) != code_hashes[str(path)]:
                raise ValueError("Implementation changed during assessment")
        published = {
            **report,
            "mode": "EXECUTE",
            "output_published": True,
            "raw_term_code_rows": logical,
            "observed_evidence_codes": observed,
            "counts": dict(sorted(totals.items())),
            "outputs": files,
            "benchmark_mode": policy["benchmark_mode"],
            "not_precedence": policy["not_precedence"],
            "negative_terms_are_fp_not_eval_or_skip": True,
            "source_m10c_outputs": manifest["outputs"],
            "original_mapping_sha256": file_sha256(targets_input),
            "stage01_target_table_sha256": file_sha256(stage01_targets),
            "implementation_sha256": code_hashes,
        }
        (staging / "manifest.json").write_text(
            json.dumps(published, sort_keys=True, indent=2) + "\n",
            encoding="utf-8",
        )
        for path in staging.iterdir():
            with path.open("rb") as handle:
                os.fsync(handle.fileno())
        if destination.exists() or destination.is_symlink():
            raise FileExistsError("Assessment destination was published")
        os.rename(staging, destination)
        return published
    finally:
        source.close()
        if staging.exists():
            shutil.rmtree(staging)
