"""Independent Stage08 V2 policy projection; does not alter M10c or V1 pilot.

The three output statuses are EVAL, SKIP and NEUTRAL. A resolved negative
never becomes SKIP by accident: it is omitted from the scoring mask and is
an operational FP if predicted for an assessable target/aspect.
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
from dataclasses import dataclass
from pathlib import Path

from probe.stage08_evidence_registry import EvidenceRegistry, read_json_object
from probe.stage08_temporal_benchmark import (
    ROOTS,
    _stamp,
    crosscheck_stage01_mapping,
    file_sha256,
    inspect_m10c_bundle,
    load_target_ids,
    resolve_identical_target_aliases,
)

SCHEMA = "stage08-temporal-eval-skip-neutral-v3"
POLICY_SCHEMA = "stage08-evidence-classes-v1"

# Term flags are always presence flags, never implicit weights.
P0S = 1 << 0
P0N = 1 << 1
P1S = 1 << 2
P1N = 1 << 3
N0S = 1 << 4
N0N = 1 << 5
N1S = 1 << 6
N1N = 1 << 7
IKR0 = 1 << 8
IKR1 = 1 << 9
DS0 = 1 << 10
DS1 = 1 << 11
DN0 = 1 << 12
DN1 = 1 << 13
ANY0_DIRECT = 1 << 14
ANY1_DIRECT = 1 << 15

TERM_HEADER = ("query_id", "aspect", "go_id", "status")
ASPECT_HEADER = (
    "query_id",
    "aspect",
    "eval_terms",
    "skip_terms",
    "neutral_terms",
    "negative_terms",
    "known_start_score",
    "known_start_non_score",
)
AUDIT_HEADER = (
    "query_id",
    "target_id",
    "aspect",
    "go_id",
    "status",
    "reason",
    "start_score_positive",
    "start_non_score_positive",
    "end_score_positive",
    "end_non_score_positive",
    "start_score_not",
    "start_non_score_not",
    "end_score_not",
    "end_non_score_not",
    "curated_not_ikr_ird",
)
NEG_HEADER = (
    "query_id",
    "target_id",
    "aspect",
    "go_id",
    "not_start",
    "not_end",
    "decision",
    "reason",
)
EVENT_HEADER = (
    "query_id",
    "target_id",
    "aspect",
    "go_id",
    "event",
    "status",
)

# Persisted reason dictionary keeps science distinct from 3-state scoring.
REASONS = {
    "NOVEL": "END SCORE positive; no selected positive at START.",
    "CONFIRMATION": (
        "END SCORE positive confirms START NON_SCORE positive; no START SCORE positive."
    ),
    "ALREADY_SCORE_AT_START": "Positive SCORE already present at START.",
    "SCORE_START_ONLY": "Earlier SCORE support; no END SCORE support.",
    "NON_SCORE_START_ONLY": "Earlier NON_SCORE positive, not END SCORE.",
    "NON_SCORE_END_ONLY": "END NON_SCORE positive without END SCORE.",
    "CONFLICT_IKR_IRD": (
        "IKR or IRD negative overlaps END SCORE positive, review required."
    ),
    "CONFLICT_SCORE_END": ("END SCORE positive and END SCORE negative overlap."),
    "TEMPORAL_REVERSAL_NOT_TO_POSITIVE": (
        "START SCORE negative overlaps END SCORE positive."
    ),
    "TEMPORAL_REVERSAL_POSITIVE_TO_NOT": (
        "START SCORE positive overlaps END SCORE negative."
    ),
    "CONFLICT_NON_SCORE_END": (
        "END NON_SCORE positive and negative without SCORE evidence."
    ),
    "RESOLVED_NOT_SCORE": ("SCORE NOT wins absent conflicting SCORE positive."),
    "RESOLVED_NOT_NON_SCORE": ("NON_SCORE NOT without selected positive support."),
    "NO_SELECTED_POSITIVE": "No retained positive support in GO closure.",
}


@dataclass(frozen=True)
class Classes:
    score: frozenset[str]
    non_score: frozenset[str]
    excluded: frozenset[str]
    digest: str


def _codes(registry: EvidenceRegistry, selection: object) -> frozenset[str]:
    if not isinstance(selection, dict) or set(selection) != {
        "include_groups",
        "include_codes",
        "exclude_codes",
    }:
        raise ValueError("Each class needs three selection lists")
    groups = selection["include_groups"]
    include = selection["include_codes"]
    exclude = selection["exclude_codes"]
    if not all(isinstance(a, list) for a in (groups, include, exclude)):
        raise ValueError("Selection entries must be arrays")
    if any(
        len(items) != len(set(items)) or not all(isinstance(x, str) for x in items)
        for items in (groups, include, exclude)
    ):
        raise ValueError("Duplicate or invalid selection value")
    if set(groups) - registry.groups.keys():
        raise ValueError("Unknown registry groups")
    if (set(include) | set(exclude)) - registry.definitions.keys():
        raise ValueError("Unregistered evidence code in class")
    codes = set(include)
    for group in groups:
        codes.update(registry.groups[group])
    return frozenset(codes - set(exclude))


def load_classes(
    registry_path: Path,
    policy_path: Path,
) -> tuple[EvidenceRegistry, Classes, dict]:
    registry = EvidenceRegistry.from_json(registry_path)
    policy = read_json_object(policy_path)
    if (
        set(policy)
        != {
            "schema_version",
            "registry_id",
            "classes",
            "scoring_scope",
            "negative_policy",
            "assessment_mode",
        }
        or policy["schema_version"] != POLICY_SCHEMA
    ):
        raise ValueError("Unexpected V2 policy contract")
    if policy["registry_id"] != registry.registry_id:
        raise ValueError("Registry reference mismatch")
    if policy["scoring_scope"] != "target_aspects_with_eval":
        raise ValueError("Unsupported scoring universe")
    if policy["negative_policy"] != "evidence_sensitive_neutral":
        raise ValueError("Unsupported negative precedence")
    if policy["assessment_mode"] != "acquisition_and_confirmation":
        raise ValueError("Unsupported temporal profile")
    raw = policy["classes"]
    if not isinstance(raw, dict) or set(raw) != {
        "SCORE",
        "NON_SCORE",
        "EXCLUDED",
    }:
        raise ValueError("Explicit SCORE/NON_SCORE/EXCLUDED classes required")
    score = _codes(registry, raw["SCORE"])
    non_score = _codes(registry, raw["NON_SCORE"])
    excluded = _codes(registry, raw["EXCLUDED"])
    if not score:
        raise ValueError("SCORE class may not be empty")
    if (score & non_score) or (score & excluded) or (non_score & excluded):
        raise ValueError("Evidence classes must be disjoint")
    if score | non_score | excluded != set(registry.definitions):
        raise ValueError("Every registered evidence code must have one class")
    digest_data = {
        "registry_fingerprint": registry.fingerprint,
        "score": sorted(score),
        "non_score": sorted(non_score),
        "excluded": sorted(excluded),
        "schema": POLICY_SCHEMA,
    }
    digest = hashlib.sha256(
        json.dumps(
            digest_data,
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()
    return registry, Classes(score, non_score, excluded, digest), policy


def decide(flags: int) -> tuple[str, str]:
    """Return (status, audit reason); empty status means operational FP.

    Both releases are considered. IKR and IRD are a special reviewed-negative
    class regardless of whether user assigned them to SCORE or NON_SCORE.
    Excluded codes never reach flags and have no effect.
    """
    a, b = bool(flags & P0S), bool(flags & P1S)
    c, d = bool(flags & N0S), bool(flags & N1S)
    na, nb = bool(flags & P0N), bool(flags & P1N)
    nc, nd = bool(flags & N0N), bool(flags & N1N)
    curated = bool(flags & (IKR0 | IKR1))

    if b and curated:
        return "NEUTRAL", "CONFLICT_IKR_IRD"
    if b and nb:
        return "NEUTRAL", "CONFLICT_SCORE_END"
    if b and na:
        return "NEUTRAL", "TEMPORAL_REVERSAL_NOT_TO_POSITIVE"
    if a and nb:
        return "NEUTRAL", "TEMPORAL_REVERSAL_POSITIVE_TO_NOT"
    if b and a:
        return "SKIP", "ALREADY_SCORE_AT_START"
    if b:
        if c:
            return "EVAL", "CONFIRMATION"
        return "EVAL", "NOVEL"
    if nb or na:
        return "", "RESOLVED_NOT_SCORE"
    if d and nd:
        return "NEUTRAL", "CONFLICT_NON_SCORE_END"
    if a:
        return "SKIP", "SCORE_START_ONLY"
    if c:
        return "SKIP", "NON_SCORE_START_ONLY"
    if d:
        return "SKIP", "NON_SCORE_END_ONLY"
    if nc or nd:
        return "", "RESOLVED_NOT_NON_SCORE"
    return "", "NO_SELECTED_POSITIVE"


def _state_db(path: Path) -> sqlite3.Connection:
    db = sqlite3.connect(path)
    db.executescript("""
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
    return db


def _import_terms(
    source: sqlite3.Connection,
    output: sqlite3.Connection,
    classes: Classes,
    registry: EvidenceRegistry,
    start: str,
    end: str,
    mapping: Mapping[str, str],
    aliases: Mapping[str, str],
) -> tuple[int, int]:
    flags_for = {
        (start, "POSITIVE", "SCORE"): (P0S, DS0),
        (end, "POSITIVE", "SCORE"): (P1S, DS1),
        (start, "POSITIVE", "NON_SCORE"): (N0S, ANY0_DIRECT),
        (end, "POSITIVE", "NON_SCORE"): (N1S, ANY1_DIRECT),
        (start, "NOT", "SCORE"): (P0N, DN0),
        (end, "NOT", "SCORE"): (P1N, DN1),
        (start, "NOT", "NON_SCORE"): (N0N, 0),
        (end, "NOT", "NON_SCORE"): (N1N, 0),
    }
    batches: list[tuple[str, str, str, int]] = []
    upsert = """INSERT INTO terms VALUES (?, ?, ?, ?)
        ON CONFLICT(target_id, aspect, go_id)
        DO UPDATE SET flags = terms.flags | excluded.flags"""
    logical = 0
    observed: set[str] = set()
    releases: set[str] = set()
    previous = None
    flags = 0

    def flush() -> None:
        nonlocal logical
        if previous is None:
            return
        _, target, aspect, go = previous
        batches.append((aliases.get(target, target), aspect, go, flags))
        logical += 1
        if len(batches) >= 8192:
            output.executemany(upsert, batches)
            batches.clear()
            output.commit()

    sql = """SELECT release, target_id, aspect, go_id, polarity,
        evidence_code, support_count, direct_count
        FROM term_code_support
        ORDER BY release, target_id, aspect, go_id, polarity, evidence_code"""
    for release, target, aspect, go, polarity, code, count, direct in source.execute(
        sql
    ):
        key = (release, target, aspect, go)
        if key != previous:
            flush()
            flags = 0
            previous = key
        if target not in mapping or aspect not in ("F", "P", "C"):
            raise ValueError("Unmapped M10c target or invalid aspect")
        if release not in (start, end) or polarity not in (
            "POSITIVE",
            "NOT",
        ):
            raise ValueError("Invalid source release/polarity")
        if count <= 0 or direct < 0 or direct > count:
            raise ValueError("Invalid source support cardinalities")
        releases.add(release)
        observed.add(code)
        if code in classes.excluded:
            continue
        klass = "SCORE" if code in classes.score else "NON_SCORE"
        base_flag, direct_flag = flags_for[(release, polarity, klass)]
        flags |= base_flag
        if direct:
            flags |= direct_flag
        if code in ("IKR", "IRD") and polarity == "NOT":
            flags |= IKR0 if release == start else IKR1
    flush()
    if batches:
        output.executemany(upsert, batches)
        output.commit()
    registry.require_observed_reviewed(observed)
    if releases != {start, end}:
        raise ValueError("Both source releases required")
    return logical, len(observed)


def _write_outputs(
    state: sqlite3.Connection,
    staging: Path,
    mapping: Mapping[str, str],
) -> Counter:
    counters: Counter = Counter()
    names = {
        "benchmark_terms.tsv": TERM_HEADER,
        "target_aspects.tsv": ASPECT_HEADER,
        "term_audit.tsv": AUDIT_HEADER,
        "negative_terms.tsv": NEG_HEADER,
        "temporal_events.tsv": EVENT_HEADER,
    }
    from contextlib import ExitStack

    with ExitStack() as stack:
        writers = {}
        for name, header in names.items():
            handle = stack.enter_context(
                (staging / name).open("w", encoding="utf-8", newline="")
            )
            writer = csv.writer(handle, delimiter="\t", lineterminator="\n")
            writer.writerow(header)
            writers[name] = writer
        terms = writers["benchmark_terms.tsv"]
        aspects = writers["target_aspects.tsv"]
        audit = writers["term_audit.tsv"]
        negatives = writers["negative_terms.tsv"]
        events = writers["temporal_events.tsv"]
        cursor = state.execute("""SELECT target_id, aspect, go_id, flags
            FROM terms ORDER BY target_id, aspect, go_id""")
        last_target = None
        current: dict[str, list[tuple[str, int]]] = {}

        def flush() -> None:
            if last_target is None:
                return
            query_id = mapping[last_target]
            for aspect, values in sorted(current.items()):
                decisions: list[tuple[str, int, str, str]] = []
                for go, flags in values:
                    if go in ROOTS:
                        counters["root_excluded"] += 1
                        continue
                    status, reason = decide(flags)
                    decisions.append((go, flags, status, reason))
                    counters["decision_" + reason] += 1
                evaluate = sum(s == "EVAL" for _, _, s, _ in decisions)
                if not evaluate:
                    counters["target_aspects_without_eval"] += 1
                else:
                    counters["eligible_target_aspects"] += 1
                    score0 = any(f & P0S for _, f in values)
                    non0 = any(f & N0S for _, f in values)
                    status_count = Counter(s for _, _, s, _ in decisions)
                    aspects.writerow(
                        (
                            query_id,
                            aspect,
                            evaluate,
                            status_count["SKIP"],
                            status_count["NEUTRAL"],
                            status_count[""],
                            int(score0),
                            int(non0),
                        )
                    )
                for go, flags, status, reason in decisions:
                    if evaluate and status:
                        terms.writerow((query_id, aspect, go, status))
                        counters["benchmark_" + status] += 1
                    if (
                        status in ("EVAL", "NEUTRAL")
                        or (status == "SKIP" and evaluate)
                        or reason
                        in (
                            "RESOLVED_NOT_SCORE",
                            "RESOLVED_NOT_NON_SCORE",
                        )
                    ):
                        # All ambiguous and negative decisions are audited,
                        # including unassessed aspects. No hidden conflicts.
                        audit.writerow(
                            (
                                query_id,
                                last_target,
                                aspect,
                                go,
                                status if status else "NEGATIVE",
                                reason,
                                int(bool(flags & P0S)),
                                int(bool(flags & N0S)),
                                int(bool(flags & P1S)),
                                int(bool(flags & N1S)),
                                int(bool(flags & P0N)),
                                int(bool(flags & N0N)),
                                int(bool(flags & P1N)),
                                int(bool(flags & N1N)),
                                int(bool(flags & (IKR0 | IKR1))),
                            )
                        )
                    if flags & (P0N | P1N | N0N | N1N):
                        negatives.writerow(
                            (
                                query_id,
                                last_target,
                                aspect,
                                go,
                                int(bool(flags & (P0N | N0N))),
                                int(bool(flags & (P1N | N1N))),
                                status if status else "NEGATIVE",
                                reason,
                            )
                        )
                        counters["negative_observations"] += 1
                    if flags & DS1:
                        if status == "EVAL":
                            event = reason
                        elif status == "NEUTRAL":
                            event = "DIRECT_CONFLICT_OR_REVERSAL"
                        elif flags & DS0:
                            event = "DIRECT_MAINTAINED_SCORE"
                        else:
                            event = "DIRECT_NON_EVAL"
                        events.writerow(
                            (
                                query_id,
                                last_target,
                                aspect,
                                go,
                                event,
                                status if status else "NEGATIVE",
                            )
                        )
                        counters[event] += 1

        for target, aspect, go, flags in cursor:
            if target != last_target:
                flush()
                last_target = target
                current = {}
            current.setdefault(aspect, []).append((go, flags))
        flush()
    return counters


def build_temporal_benchmark_v2(
    *,
    m10c_bundle: Path,
    targets_input: Path,
    stage01_targets: Path,
    registry_path: Path,
    policy_path: Path,
    destination: Path,
    start_release: str,
    end_release: str,
    m8_summary: Path | None = None,
    execute: bool = False,
    implementation_script: Path | None = None,
) -> dict:
    """Validate sources, then publish immutable V2 output only on --execute."""
    if not re.fullmatch(r"[0-9]{4}_[0-9]{2}", start_release):
        raise ValueError("Invalid START release")
    if not re.fullmatch(r"[0-9]{4}_[0-9]{2}", end_release):
        raise ValueError("Invalid END release")
    if start_release >= end_release:
        raise ValueError("START must precede END")
    db, m10c = inspect_m10c_bundle(m10c_bundle)
    registry, classes, policy = load_classes(registry_path, policy_path)
    mapping = load_target_ids(targets_input, allow_identical_duplicates=True)
    count = crosscheck_stage01_mapping(stage01_targets, mapping)
    aliases, duplicate_ledger = resolve_identical_target_aliases(
        targets_input, stage01_targets, mapping
    )
    if destination.exists() or destination.is_symlink():
        raise FileExistsError("V2 destination must be new")
    if not destination.parent.is_dir() or destination.parent.is_symlink():
        raise ValueError("Output parent must be real existing directory")
    if m8_summary is not None:
        m8 = read_json_object(m8_summary)
        for role, release in (("START", start_release), ("END", end_release)):
            if m8["by_role"][role]["release"] != release:
                raise ValueError("M8 role/release mismatch")
            source_sha = m10c["source_payloads"][role]["gaf_source_sha256"]
            if m8["by_role"][role]["normalized_gaf_sha256"] != source_sha:
                raise ValueError("M8/M10c source identity mismatch")
    output = {
        "schema_version": SCHEMA,
        "mode": "EXECUTE" if execute else "PREFLIGHT_ONLY",
        "start_release": start_release,
        "end_release": end_release,
        "source_target_count": count,
        "distinct_original_query_ids": count - len(aliases),
        "identical_duplicate_target_aliases": len(aliases),
        "m10c_manifest_sha256": file_sha256(m10c_bundle / "manifest.json"),
        "m8_summary_sha256": (
            file_sha256(m8_summary) if m8_summary is not None else None
        ),
        "registry_fingerprint": registry.fingerprint,
        "ontology_sha256": m10c["ontology_sha256"],
        "class_fingerprint": classes.digest,
        "policy_sha256": file_sha256(policy_path),
        "classes": {
            "SCORE": sorted(classes.score),
            "NON_SCORE": sorted(classes.non_score),
            "EXCLUDED": sorted(classes.excluded),
        },
        "assessment_mode": policy["assessment_mode"],
        "output_published": False,
    }
    if not execute:
        return output
    input_files = [
        m10c_bundle / "manifest.json",
        db,
        m10c_bundle / "propagated_terms.tsv",
        targets_input,
        stage01_targets,
        registry_path,
        policy_path,
    ]
    if m8_summary is not None:
        input_files.append(m8_summary)
    before = {str(p): _stamp(p) for p in input_files}
    for name, info in m10c["outputs"].items():
        if file_sha256(m10c_bundle / name) != info["sha256"]:
            raise ValueError("M10c SHA256 mismatch: " + name)
    source = sqlite3.connect(f"file:{db}?mode=ro&immutable=1", uri=True)
    code_files = [Path(__file__)]
    if implementation_script is not None:
        code_files.append(implementation_script)
    before_code = {str(p): file_sha256(p) for p in code_files}
    staging = Path(
        tempfile.mkdtemp(prefix=".stage08_v2_staging_", dir=destination.parent)
    )
    try:
        state = _state_db(staging / "state.sqlite3")
        try:
            logical, observed = _import_terms(
                source,
                state,
                classes,
                registry,
                start_release,
                end_release,
                mapping,
                aliases,
            )
            if logical != m10c["distinct_propagated_terms"]:
                raise ValueError("M10c term cardinality mismatch")
            counters = _write_outputs(state, staging, mapping)
            if not counters["benchmark_EVAL"]:
                raise ValueError("No EVAL cases with selected classes")
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
            writer.writerows(duplicate_ledger)
        (staging / "state.sqlite3").unlink()
        (staging / "audit_reasons.json").write_text(
            json.dumps(REASONS, sort_keys=True, indent=2) + "\n",
            encoding="utf-8",
        )
        names = (
            "benchmark_terms.tsv",
            "target_aspects.tsv",
            "term_audit.tsv",
            "negative_terms.tsv",
            "temporal_events.tsv",
            "duplicate_targets.tsv",
            "audit_reasons.json",
        )
        checksums = {
            name: {
                "sha256": file_sha256(staging / name),
                "bytes": (staging / name).stat().st_size,
            }
            for name in names
        }
        for item in input_files:
            if _stamp(item) != before[str(item)]:
                raise ValueError("Upstream changed during processing")
        for p in code_files:
            if file_sha256(p) != before_code[str(p)]:
                raise ValueError("Implementation changed during V2 execution")
        output.update(
            {
                "output_published": True,
                "raw_term_code_rows": logical,
                "observed_evidence_codes": observed,
                "counts": dict(sorted(counters.items())),
                "outputs": checksums,
                "source_m10c_outputs": m10c["outputs"],
                "implementation_sha256": {str(p): file_sha256(p) for p in code_files},
            }
        )
        (staging / "manifest.json").write_text(
            json.dumps(output, sort_keys=True, indent=2) + "\n",
            encoding="utf-8",
        )
        for p in staging.iterdir():
            with p.open("rb") as handle:
                os.fsync(handle.fileno())
        if destination.exists() or destination.is_symlink():
            raise FileExistsError("Output published concurrently")
        os.rename(staging, destination)
        return output
    finally:
        source.close()
        if staging.exists():
            shutil.rmtree(staging)
