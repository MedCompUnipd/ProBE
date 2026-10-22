"""Canonical V2 TaxID-gated directional completion without legacy 05A2.

Only same-resolved-TaxID NONEXACT pairs with prior literal documents enter DP.
Existing scientific 06C0/06C1 executables perform numerical calculations.
No locus equivalence or GO donor decision is made here.
"""

from __future__ import annotations

import csv
import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path

from probe.canonical_v2.bounded_dp import (
    BACKEND,
    PLAN,
    BudgetExceeded,
    execute_bounded_dp,
    plan_bounded_dp,
)
from probe.canonical_v2.documentary_handoff import EVIDENCE_FIELDS
from probe.canonical_v2.documentary_science import read_documentary_facts
from probe.canonical_v2.exact_documentary_registry import _gene_keys
from probe.canonical_v2.large_dp import (
    BACKEND as EXECUTION_BACKEND,
)
from probe.canonical_v2.large_dp import (
    ROUTING,
    execute_large_dp,
    plan_large_dp,
)
from probe.canonical_v2.release_workload import EVIDENCE
from probe.resolver import is_known_nonidentifying_locus
from probe.resolver_anchors import _load_target_taxonomy
from probe.resolver_lineage import (
    CandidateReference,
    JoinedLineage,
    collect_lineage,
)
from probe.stage2c_alignment_completion import (
    DP_OUTPUT,
    DP_SUMMARY,
    FINAL_OUTPUT,
    FINAL_SUMMARY,
    NONEXACT,
    NONEXACT_SUMMARY,
    POLICY_SKIP,
    _dp_budget,
    _metric_summary,
    _preview,
    _run_dp_chunks,
    _write_empty_dp,
    sha256,
)
from probe.stage2c_alignment_preview import (
    AUDIT_OUT,
    END_STORE,
    JOBS_OUT,
    START_STORE,
    UNION_OUT,
)

SCHEMA = "probe-canonical-v2-alignment-execution-v1"
BOUNDED_SCHEMA = "probe-canonical-v2-alignment-execution-v3"
SUMMARY = "canonical_alignment_execution.summary.json"
DP_GATE_CONTRACT = "same-resolved-taxid-documentary-literal-v1"
NO_DOCUMENTARY_WITNESS = "NO_DOCUMENTARY_WITNESS"
POLICY_SKIP_REASONS = frozenset(
    {
        "CROSS_TAXID_NONEXACT",
        NO_DOCUMENTARY_WITNESS,
    }
)
OUTPUTS = (
    NONEXACT,
    NONEXACT_SUMMARY,
    POLICY_SKIP,
    DP_OUTPUT,
    DP_SUMMARY,
    FINAL_OUTPUT,
    FINAL_SUMMARY,
)


def _rows(path: Path, required: set[str]):
    if not path.is_file() or path.is_symlink():
        raise ValueError(f"Invalid canonical input: {path}")
    with path.open(encoding="utf-8", newline="") as stream:
        reader = csv.DictReader(stream, delimiter="\t")
        fields = reader.fieldnames
        if fields is None or len(fields) != len(set(fields)):
            raise ValueError(f"Invalid TSV header: {path}")
        if not required.issubset(fields):
            raise ValueError(f"Missing canonical input fields: {path}")
        for number, row in enumerate(reader, 2):
            if None in row or any(value is None for value in row.values()):
                raise ValueError(f"Malformed TSV row {path}:{number}")
            yield row


def _rid(value: str) -> int:
    if not value or not value.isascii() or not value.isdecimal():
        raise ValueError("Invalid candidate RID")
    number = int(value)
    if number < 1 or str(number) != value:
        raise ValueError("Noncanonical candidate RID")
    return number


def _source_index(
    db: sqlite3.Connection,
    primary: Path,
    audit: Path,
    releases: dict[str, str],
) -> None:
    fields = {
        "release",
        "target_id",
        "rid",
        "candidate_primary_accession",
        "raw_ox",
        "resolved_taxid",
        "sequence_length",
        "discovery_channel",
    }
    for row in _rows(primary, fields):
        if row["release"] not in releases.values() or not row["target_id"]:
            raise ValueError("Invalid primary release/target")
        db.execute(
            "INSERT INTO candidate VALUES (?,?,?,?,?,?,?,?,?)",
            (
                row["release"],
                row["target_id"],
                _rid(row["rid"]),
                "PRIMARY",
                row["candidate_primary_accession"],
                row["raw_ox"],
                row["resolved_taxid"],
                row["sequence_length"],
                row["discovery_channel"],
            ),
        )
    audit_fields = {
        "release",
        "target_id",
        "rid",
        "candidate_primary_accession",
        "raw_ox",
        "anchor_resolved_taxid",
        "audit_alignment_allowed",
        "record_already_in_primary_union_for_target",
        "key_source_reason",
        "audit_class",
    }
    for row in _rows(audit, audit_fields):
        if row["release"] not in releases.values():
            raise ValueError("Foreign taxonomy audit release")
        key = row["release"], row["target_id"], _rid(row["rid"])
        current = db.execute(
            "SELECT scope,accession,ox,taxid FROM candidate "
            "WHERE release=? AND target=? AND rid=?",
            key,
        ).fetchone()
        overlap = row["record_already_in_primary_union_for_target"]
        if overlap not in {"0", "1"} or (current is not None) != (overlap == "1"):
            raise ValueError("Taxonomy audit overlap contradiction")
        if current is not None:
            if current[1:] != (
                row["candidate_primary_accession"],
                row["raw_ox"],
                row["anchor_resolved_taxid"],
            ):
                raise ValueError("Contradictory primary/audit identity")
            continue
        if (
            row["audit_alignment_allowed"] == "1"
            and row["key_source_reason"] == "SAME_SPECIES_WRONG_SUBTAXON"
            and row["audit_class"] == "TAXONOMY_RESCUE_AUDIT"
        ):
            db.execute(
                "INSERT INTO candidate VALUES (?,?,?,?,?,?,?,?,?)",
                (
                    *key,
                    "TAXONOMY_AUDIT",
                    row["candidate_primary_accession"],
                    row["raw_ox"],
                    row["anchor_resolved_taxid"],
                    "",
                    "TAXONOMY_AUDIT",
                ),
            )


def _verify_job_coverage(
    db: sqlite3.Connection,
    jobs: Path,
    required: set[str],
    releases: dict[str, str],
) -> None:
    """Prove no EXACT, documentary or permitted-audit candidate was lost."""
    db.execute(
        "CREATE TABLE jobs (release TEXT,target TEXT,rid INTEGER,"
        "PRIMARY KEY(release,target,rid))"
    )
    previous = None
    ordering = {releases["START"]: 0, releases["END"]: 1}
    for row in _rows(jobs, required):
        role = ordering.get(row["release"])
        if role is None:
            raise ValueError("Foreign canonical job release")
        numeric = _rid(row["rid"])
        key = row["release"], row["target_id"], numeric
        order = role, row["target_id"], numeric
        if previous is not None and order <= previous:
            raise ValueError("Duplicate or unsorted canonical 06A jobs")
        previous = order
        if row["exact_sequence"] not in {"0", "1"}:
            raise ValueError("Invalid literal 06A exactness")
        candidate = db.execute(
            "SELECT scope,accession,ox,length,channel FROM candidate "
            "WHERE release=? AND target=? AND rid=?",
            key,
        ).fetchone()
        if candidate is None:
            raise ValueError("06A job without original candidate")
        scope, accession, ox, length, channel = candidate
        if (
            scope != row["job_scope"]
            or accession != row["candidate_primary_accession"]
            or ox != row["raw_ox"]
            or channel != row["discovery_channel"]
            or (scope == "PRIMARY" and length != row["candidate_length"])
        ):
            raise ValueError("06A/candidate identity disagreement")
        db.execute("INSERT INTO jobs VALUES (?,?,?)", key)
    missing = db.execute(
        "SELECT COUNT(*) FROM candidate c LEFT JOIN jobs j "
        "ON c.release=j.release AND c.target=j.target AND c.rid=j.rid "
        "WHERE j.rid IS NULL"
    ).fetchone()[0]
    if missing:
        raise ValueError(f"Canonical 06A omitted {missing} candidate records")


def has_documentary_witness(facts: JoinedLineage | None) -> bool:
    """A literal search witness does not assert locus stability or GO eligibility."""
    if facts is None:
        return False
    return any(
        kind in {"ACCESSION", "GENE_NAME", "GENE_SYNONYM", "ORDERED_LOCUS", "ORF"}
        and bool(value)
        and not is_known_nonidentifying_locus(kind, value)
        for kind, value in facts.matched_typed_keys
    )


def _verify_literal_observation(row: dict[str, str]) -> None:
    """Bind literal candidate keys to native FULL metadata and typed origins."""
    typed_origins = {
        "ACCESSION": {"PRIMARY_ACCESSION", "SECONDARY_ACCESSION"},
        "GENE_NAME": {"GENE_N"},
        "GENE_SYNONYM": {"GENE_S"},
        "ORDERED_LOCUS": {"GENE_L"},
        "ORF": {"GENE_O"},
    }
    candidate_key = (
        row["candidate_key_type"],
        row["candidate_key_value"],
        row["candidate_key_origin"],
    )
    if candidate_key not in set(
        _gene_keys(
            row["candidate_gene"],
            row["candidate_accession"],
            row["candidate_sec"],
        )
    ):
        raise ValueError("Literal documentary key absent from candidate metadata")
    _rid(row["exact_origin_target_taxid"])
    if row["source_kind"] == "EXACT":
        if (
            row["source_roles"] != "EXACT_VERIFIED"
            or row["source_key_origin"] not in typed_origins[row["source_key_type"]]
        ):
            raise ValueError("Unverified typed EXACT documentary origin")
    elif (
        row["source_key_type"] not in {"ACCESSION", "ORDERED_LOCUS", "ORF"}
        or row["source_key_origin"] != "ORIGINAL_DOCUMENTARY_KEY"
        or row["exact_source_tax_scope"] != "INDEPENDENT"
        or row["exact_origin_target_taxid"] != row["target_resolved_taxid"]
    ):
        raise ValueError("Unverified independent documentary origin")


def _documentary_witnesses(
    *,
    primary: Path,
    documentary_evidence: Path | None,
    lineage_index: Path | None,
    lineage_paths: dict[str, Path] | None,
    releases: dict[str, str],
    wanted: dict[tuple[str, str, str], str],
) -> frozenset[tuple[str, str, str]]:
    """Verify original 04D1/04D2 and preserved V2 lookup observations.

    Discovery channel names and collapsed lineage flags are not witnesses.
    Candidate identity is bound to release, target, RID, accession and TaxID.
    Normalized-only hints and known nonidentifying locus tokens never qualify.
    """
    if not wanted:
        return frozenset()
    if (
        documentary_evidence is None
        or lineage_index is None
        or lineage_paths is None
        or set(lineage_paths) != set(releases.values())
    ):
        raise ValueError("Missing prior documentary sources for NONEXACT DP")
    for path in (documentary_evidence, lineage_index, *lineage_paths.values()):
        if not path.is_file() or path.is_symlink():
            raise ValueError(f"Missing or unsafe documentary source: {path}")
    identities = {}
    references = {}
    for row in _rows(
        primary,
        {
            "release",
            "target_id",
            "rid",
            "candidate_primary_accession",
            "raw_ox",
            "resolved_taxid",
            "frag",
            "target_primary_bucket",
        },
    ):
        key = row["release"], row["target_id"], row["rid"]
        if key not in wanted:
            continue
        if key in identities or row["resolved_taxid"] != wanted[key]:
            raise ValueError("Contradictory documentary candidate identity")
        identities[key] = (
            row["candidate_primary_accession"],
            row["raw_ox"],
            row["resolved_taxid"],
            row["frag"],
        )
        references[key] = CandidateReference(
            row["target_primary_bucket"],
            row["candidate_primary_accession"],
        )
    if set(identities) != set(wanted):
        raise ValueError("Same-TaxID documentary candidate absent from primary")
    original = collect_lineage(
        index_path=lineage_index,
        evidence_paths=lineage_paths,
        candidates=references,
    )
    latest = read_documentary_facts(
        path=documentary_evidence,
        releases=releases,
    )
    original_index = {}
    contexts = {
        (key[0], key[1], reference.primary_bucket)
        for key, reference in references.items()
    }
    for row in _rows(
        lineage_index,
        {
            "release",
            "target_id",
            "primary_bucket",
            "key_type",
            "key_value",
            "source_roles",
        },
    ):
        context = row["release"], row["target_id"], row["primary_bucket"]
        if context in contexts:
            original_index[(*context, row["key_type"], row["key_value"])] = row
    for row in _rows(documentary_evidence, set(EVIDENCE_FIELDS)):
        key = row["release"], row["target_id"], row["candidate_rid"]
        identity = identities.get(key)
        if identity is None:
            continue
        observed = (
            row["candidate_accession"],
            row["candidate_raw_ox"],
            row["candidate_resolved_taxid"],
            row["candidate_frag"],
        )
        if observed != identity or row["target_resolved_taxid"] != wanted[key]:
            raise ValueError("Documentary evidence/candidate identity mismatch")
        if (
            row["match_class"] != "LITERAL"
            or row["source_key_type"] != row["candidate_key_type"]
            or row["source_key_value"] != row["candidate_key_value"]
            or is_known_nonidentifying_locus(
                row["source_key_type"],
                row["source_key_value"],
            )
        ):
            continue
        _verify_literal_observation(row)
        if row["source_kind"] == "INDEPENDENT_04D1":
            index_key = (
                key[0],
                key[1],
                references[key].primary_bucket,
                row["source_key_type"],
                row["source_key_value"],
            )
            source = original_index.get(index_key)
            if source is None or source["source_roles"] != row["source_roles"]:
                raise ValueError("Lookup key lacks authoritative 04D1 provenance")
    return frozenset(
        key
        for key in wanted
        if has_documentary_witness(original.get(key))
        or has_documentary_witness(latest.get(key))
    )


def select_nonexact_dp(
    *,
    jobs: Path,
    primary: Path,
    audit: Path,
    taxonomy: dict[str, Path],
    releases: dict[str, str],
    selected: Path,
    skipped: Path,
    full_jobs: Path | None = None,
    documentary_evidence: Path | None = None,
    lineage_index: Path | None = None,
    lineage_paths: dict[str, Path] | None = None,
) -> dict[str, int]:
    """Select only same-TaxID pairs with verified prior literal documents."""
    if set(releases) != {"START", "END"} or set(taxonomy) != set(releases):
        raise ValueError("Invalid canonical release/taxonomy roles")
    if len(set(releases.values())) != 2:
        raise ValueError("Release IDs must differ")
    required = {
        "release",
        "target_id",
        "rid",
        "job_scope",
        "exact_sequence",
        "candidate_primary_accession",
        "raw_ox",
        "discovery_channel",
        "candidate_length",
    }
    wanted = {
        (row["release"], row["target_id"])
        for row in _rows(jobs, required)
        if row["exact_sequence"] == "0"
    }
    facts = _load_target_taxonomy(
        {releases[role]: taxonomy[role] for role in releases}, wanted
    )
    if set(facts) != wanted:
        raise ValueError("Missing Stage 02B target taxonomy")
    counters = {"nonexact": 0, "selected": 0, "skipped": 0}
    with (
        tempfile.TemporaryDirectory(prefix="probe-v2-dp-index-") as temp,
        sqlite3.connect(Path(temp) / "index.sqlite3") as db,
    ):
        db.execute(
            "CREATE TABLE candidate (release TEXT,target TEXT,rid INTEGER,"
            "scope TEXT,accession TEXT,ox TEXT,taxid TEXT,length TEXT,"
            "channel TEXT,PRIMARY KEY(release,target,rid))"
        )
        _source_index(db, primary, audit, releases)
        _verify_job_coverage(db, full_jobs or jobs, required, releases)
        wanted_pairs = {}
        for row in _rows(jobs, required):
            if row["exact_sequence"] != "0":
                continue
            key = row["release"], row["target_id"], row["rid"]
            target = facts[key[:2]]
            record = db.execute(
                "SELECT taxid FROM candidate WHERE release=? AND target=? AND rid=?",
                key,
            ).fetchone()
            if record is not None and record[0] == target.resolved_taxid:
                wanted_pairs[key] = record[0]
        witnesses = _documentary_witnesses(
            primary=primary,
            documentary_evidence=documentary_evidence,
            lineage_index=lineage_index,
            lineage_paths=lineage_paths,
            releases=releases,
            wanted=wanted_pairs,
        )
        with (
            jobs.open(encoding="utf-8", newline="") as incoming,
            selected.open("x", encoding="utf-8", newline="") as accepted,
            skipped.open("x", encoding="utf-8", newline="") as denied,
        ):
            reader = csv.DictReader(incoming, delimiter="\t")
            if reader.fieldnames is None or not required.issubset(reader.fieldnames):
                raise ValueError("Invalid 06A schema")
            writer = csv.DictWriter(
                accepted,
                fieldnames=reader.fieldnames,
                delimiter="\t",
                lineterminator="\n",
            )
            writer.writeheader()
            denied_writer = csv.writer(denied, delimiter="\t")
            denied_writer.writerow(("release", "target_id", "rid", "reason"))
            seen = set()
            for row in reader:
                if row["exact_sequence"] != "0":
                    continue
                key = row["release"], row["target_id"], _rid(row["rid"])
                if key in seen:
                    raise ValueError("Duplicate NONEXACT alignment job")
                seen.add(key)
                current = db.execute(
                    "SELECT scope,accession,ox,taxid,length,channel "
                    "FROM candidate WHERE release=? AND target=? AND rid=?",
                    key,
                ).fetchone()
                if current is None:
                    raise ValueError("NONEXACT absent from canonical candidates")
                scope, accession, ox, taxid, length, channel = current
                if (
                    scope != row["job_scope"]
                    or accession != row["candidate_primary_accession"]
                    or ox != row["raw_ox"]
                    or channel != row["discovery_channel"]
                    or (scope == "PRIMARY" and length != row["candidate_length"])
                ):
                    raise ValueError("06A/candidate identity disagreement")
                target = facts.get(key[:2])
                if target is None or not target.resolved_taxid or not taxid:
                    raise ValueError("Missing verified TaxID for NONEXACT DP")
                counters["nonexact"] += 1
                reason = None
                if target.resolved_taxid != taxid:
                    reason = "CROSS_TAXID_NONEXACT"
                elif (key[0], key[1], str(key[2])) not in witnesses:
                    reason = NO_DOCUMENTARY_WITNESS
                if reason is None:
                    writer.writerow(row)
                    counters["selected"] += 1
                else:
                    denied_writer.writerow((*key, reason))
                    counters["skipped"] += 1
    return counters


def _save_budget_failure(
    *,
    destination: Path,
    product: Path,
    error: BudgetExceeded,
    input_paths: dict[str, Path],
    pins: dict[str, str],
    limits: dict,
    scratch_root: Path | None = None,
) -> Path:
    """Keep the complete selection and plan outside any scientific publication."""
    diagnostic = Path(
        tempfile.mkdtemp(
            prefix=f"{destination.name}.dp-budget-failure-",
            dir=scratch_root or destination.parent,
        )
    )
    copied = {}
    for name in (NONEXACT, POLICY_SKIP, NONEXACT_SUMMARY, PLAN, ROUTING):
        source = product / name
        if source.is_file() and not source.is_symlink():
            shutil.copyfile(source, diagnostic / name)
            copied[name] = sha256(diagnostic / name)
    current = {name: sha256(path) for name, path in input_paths.items()}
    report = {
        "status": (
            "INCOMPLETE_MEMORY"
            if str(error).startswith("INCOMPLETE_MEMORY")
            else "INCOMPLETE_BUDGET"
        ),
        "phase": (
            "EXACT_DP_RESOURCE_PLANNING"
            if ROUTING in copied
            else "CERTIFIED_DP_PLANNING"
        ),
        "scientific_output_published": False,
        "dp_executed": False,
        "error": str(error),
        "limits": limits,
        "certified_plan": error.report,
        "input_sha256": pins,
        "input_paths": {name: str(path) for name, path in input_paths.items()},
        "input_sha256_after": current,
        "inputs_unchanged": current == pins,
        "diagnostic_sha256": copied,
    }
    path = diagnostic / "canonical_dp_preflight_failure.json"
    path.write_text(
        json.dumps(report, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    return path


def execute_alignment_v2(
    *,
    workload: Path,
    targets: Path,
    taxonomy: dict[str, Path],
    nonexact_script: Path,
    full_dp_binary: Path,
    finalize_script: Path,
    destination: Path,
    threads: int = 1,
    lineage_index: Path | None = None,
    lineage_paths: dict[str, Path] | None = None,
    dp_backend: str = "full_reference_v1",
    max_pair_cells: int = 50_000_000,
    max_total_cells: int = 500_000_000,
    max_chunk_cells: int | None = None,
    linear_dp_binary: Path | None = None,
    max_dp_memory_bytes: int = 536_870_912,
    dp_checkpoint_rows: int = 256,
    dp_scratch_root: Path | None = None,
) -> dict:
    """Compute canonical 06C with reference or certified-region exact DP."""
    if dp_backend not in {"full_reference_v1", BACKEND}:
        raise ValueError("Unknown canonical DP backend")
    if linear_dp_binary is not None and dp_backend != BACKEND:
        raise ValueError("Automatic large DP requires certified geometry")
    if any(
        not isinstance(value, int) or isinstance(value, bool) or value < 1
        for value in (threads, max_pair_cells, max_total_cells)
    ):
        raise ValueError("Invalid DP resources")
    if max_chunk_cells is not None and max_chunk_cells < 1:
        raise ValueError("Invalid DP chunk budget")
    summary, paths = _preview(workload)
    if summary.get("canonical_contract") != "probe-canonical-v2-complete-workload-v1":
        raise ValueError("Not a complete canonical V2 workload")
    releases = summary["releases"]
    if not targets.is_file() or targets.is_symlink():
        raise ValueError("Missing or unsafe target source")
    if set(taxonomy) != {"START", "END"}:
        raise ValueError("Canonical taxonomy roles are incomplete")
    for role in ("START", "END"):
        path = taxonomy[role]
        if not path.is_file() or path.is_symlink():
            raise ValueError("Missing or unsafe target taxonomy")
    for path in (nonexact_script, full_dp_binary, finalize_script):
        if not path.is_file() or path.is_symlink():
            raise ValueError("Missing or unsafe scientific executable")
    if not os.access(full_dp_binary, os.X_OK):
        raise ValueError("DP implementation is not executable")
    if destination.resolve().is_relative_to(workload.resolve()):
        raise ValueError("Canonical output cannot modify source workload")
    if destination.exists() or destination.is_symlink():
        raise FileExistsError("Canonical DP output already exists")
    if not destination.parent.is_dir() or destination.parent.is_symlink():
        raise ValueError("Canonical DP output parent unsafe")
    scratch = destination.parent
    checkpoint_root = destination.parent / f"{destination.name}.dp-checkpoints"
    environment = os.environ.copy()
    if linear_dp_binary is not None:
        if (
            not linear_dp_binary.is_file()
            or linear_dp_binary.is_symlink()
            or not os.access(linear_dp_binary, os.X_OK)
        ):
            raise ValueError("Missing or unsafe exact rolling executable")
        for value in (max_dp_memory_bytes, dp_checkpoint_rows):
            if type(value) is not int or value < 1:
                raise ValueError("Invalid exact rolling resources")
        scratch = dp_scratch_root or Path("/data/PROGENY/tmp/probe-v2-scratch")
        if (
            not scratch.is_absolute()
            or ".." in scratch.parts
            or scratch.is_symlink()
            or scratch.resolve() != scratch
        ):
            raise ValueError("Unsafe exact rolling scratch root")
        scratch.mkdir(parents=True, exist_ok=True)
        if scratch.stat().st_dev != destination.parent.stat().st_dev:
            raise ValueError("DP scratch and publication require one filesystem")
        identity = hashlib.sha256(str(destination.resolve()).encode()).hexdigest()
        checkpoint_root = scratch / "checkpoints" / identity
        environment.update(dict.fromkeys(("TMPDIR", "TMP", "TEMP"), str(scratch)))
    input_paths = {
        "workload_summary": workload / "2C_alignment_preview.summary.json",
        "targets": targets,
        "full_dp_binary": full_dp_binary,
        "nonexact_script": nonexact_script,
        "finalize_script": finalize_script,
        **{role: taxonomy[role] for role in ("START", "END")},
        **{f"workload_{name}": path for name, path in paths.items()},
    }
    documentary_sources = {}
    if linear_dp_binary is not None:
        input_paths["linear_dp_binary"] = linear_dp_binary
    evidence = workload / EVIDENCE
    if evidence.exists() or EVIDENCE in summary.get("output_sha256", {}):
        if (
            not evidence.is_file()
            or evidence.is_symlink()
            or summary.get("output_sha256", {}).get(EVIDENCE) != sha256(evidence)
        ):
            raise ValueError("Unverified workload documentary evidence")
        documentary_sources["documentary_evidence"] = evidence
    if lineage_index is not None:
        documentary_sources["lineage_index"] = lineage_index
    if lineage_paths is not None:
        documentary_sources.update(
            {f"lineage_{release}": path for release, path in lineage_paths.items()}
        )
    for name, path in documentary_sources.items():
        if not path.is_file() or path.is_symlink():
            raise ValueError("Missing or unsafe documentary DP input")
        input_paths[name] = path
    pins = {name: sha256(path) for name, path in input_paths.items()}
    with tempfile.TemporaryDirectory(
        prefix="probe-canonical-v2-dp-", dir=scratch
    ) as temporary:
        product = Path(temporary) / "product"
        product.mkdir()
        args = [
            "--start-release-id",
            releases["START"],
            "--end-release-id",
            releases["END"],
        ]
        subprocess.run(
            [
                sys.executable,
                str(nonexact_script),
                "--alignment-jobs",
                str(paths[JOBS_OUT]),
                *args,
                "--output",
                str(product / ".nonexact.tsv"),
                "--summary",
                str(product / ".nonexact.json"),
            ],
            check=True,
            env=environment,
        )
        counts = select_nonexact_dp(
            jobs=product / ".nonexact.tsv",
            primary=paths[UNION_OUT],
            audit=paths[AUDIT_OUT],
            taxonomy=taxonomy,
            releases=releases,
            selected=product / NONEXACT,
            skipped=product / POLICY_SKIP,
            full_jobs=paths[JOBS_OUT],
            documentary_evidence=evidence,
            lineage_index=lineage_index,
            lineage_paths=lineage_paths,
        )
        selector = json.loads((product / ".nonexact.json").read_text())
        if (
            selector.get("stage") != "NONEXACT"
            or selector.get("nonexact_job_count") != counts["nonexact"]
        ):
            raise ValueError("NONEXACT selector count disagreement")
        selector["canonical_dp_gate"] = counts
        selector["canonical_dp_gate_contract"] = DP_GATE_CONTRACT
        (product / NONEXACT_SUMMARY).write_text(
            json.dumps(selector, sort_keys=True, indent=2) + "\n",
            encoding="utf-8",
        )
        (product / ".nonexact.tsv").unlink()
        (product / ".nonexact.json").unlink()
        plan_info = None
        execution_info = None
        if dp_backend == BACKEND:
            try:
                planner = plan_bounded_dp if linear_dp_binary is None else plan_large_dp
                automatic_options = (
                    {}
                    if linear_dp_binary is None
                    else {
                        "linear_binary": linear_dp_binary,
                        "routing": product / ROUTING,
                        "max_dp_memory_bytes": max_dp_memory_bytes,
                        "checkpoint_rows": dp_checkpoint_rows,
                        "threads": threads,
                        "scratch_root": scratch,
                    }
                )
                plan_info = planner(
                    binary=full_dp_binary,
                    jobs=product / NONEXACT,
                    targets=targets,
                    start_store=paths[START_STORE],
                    end_store=paths[END_STORE],
                    releases=releases,
                    plan=product / PLAN,
                    max_pair_cells=max_pair_cells,
                    max_total_cells=max_total_cells,
                    max_chunk_cells=max_chunk_cells,
                    **automatic_options,
                )
            except BudgetExceeded as error:
                diagnostic = _save_budget_failure(
                    destination=destination,
                    product=product,
                    error=error,
                    input_paths=input_paths,
                    pins=pins,
                    limits={
                        "max_pair_cells": max_pair_cells,
                        "max_total_cells": max_total_cells,
                        "max_chunk_cells": max_chunk_cells,
                        **(
                            {"max_dp_memory_bytes": max_dp_memory_bytes}
                            if linear_dp_binary is not None
                            else {}
                        ),
                    },
                    scratch_root=scratch if linear_dp_binary is not None else None,
                )
                raise ValueError(f"{error}; diagnostic={diagnostic}") from error
            pairs = plan_info["selected_pairs"]
            cells = plan_info["directional_cells"]
        else:
            pairs, cells = _dp_budget(
                product / NONEXACT,
                max_pair_cells=max_pair_cells,
                max_total_cells=max_total_cells,
            )
        if pairs != counts["selected"]:
            raise ValueError("DP budget count mismatch")
        if pairs and linear_dp_binary is not None:
            execution_info = execute_large_dp(
                binary=full_dp_binary,
                linear_binary=linear_dp_binary,
                jobs=product / NONEXACT,
                targets=targets,
                start_store=paths[START_STORE],
                end_store=paths[END_STORE],
                releases=releases,
                plan=product / PLAN,
                routing=product / ROUTING,
                output=product / DP_OUTPUT,
                summary=product / DP_SUMMARY,
                threads=threads,
                max_pair_cells=max_pair_cells,
                max_total_cells=max_total_cells,
                max_chunk_cells=max_chunk_cells,
                expected_input_sha256=plan_info["input_sha256"],
                checkpoint_root=checkpoint_root,
                max_dp_memory_bytes=max_dp_memory_bytes,
                checkpoint_rows=dp_checkpoint_rows,
                scratch_root=scratch,
            )
            chunks = execution_info["chunks"]
        elif pairs and dp_backend == BACKEND:
            chunks = execute_bounded_dp(
                binary=full_dp_binary,
                jobs=product / NONEXACT,
                targets=targets,
                start_store=paths[START_STORE],
                end_store=paths[END_STORE],
                releases=releases,
                plan=product / PLAN,
                output=product / DP_OUTPUT,
                summary=product / DP_SUMMARY,
                threads=threads,
                max_pair_cells=max_pair_cells,
                max_total_cells=max_total_cells,
                max_chunk_cells=max_chunk_cells,
                expected_input_sha256=plan_info["input_sha256"],
                checkpoint_root=checkpoint_root,
            )
        elif pairs and max_chunk_cells is not None:
            chunks = _run_dp_chunks(
                jobs=product / NONEXACT,
                product=product,
                binary=full_dp_binary,
                targets=targets,
                start_store=paths[START_STORE],
                end_store=paths[END_STORE],
                release_args=args,
                threads=threads,
                max_chunk_cells=max_chunk_cells,
            )
        elif pairs:
            subprocess.run(
                [
                    str(full_dp_binary),
                    "--targets",
                    str(targets),
                    "--alignment-jobs",
                    str(product / NONEXACT),
                    "--start-sequence-store",
                    str(paths[START_STORE]),
                    "--end-sequence-store",
                    str(paths[END_STORE]),
                    *args,
                    "--output",
                    str(product / DP_OUTPUT),
                    "--summary",
                    str(product / DP_SUMMARY),
                    "--threads",
                    str(threads),
                ],
                check=True,
            )
            chunks = 1
        else:
            _write_empty_dp(product / DP_OUTPUT, product / DP_SUMMARY)
            chunks = 0
        measured = _metric_summary(product / DP_SUMMARY)
        if (
            measured["job_count"] != pairs
            or measured["completed_count"] != pairs
            or measured["directional_alignment_count"] != 2 * pairs
            or measured["total_dp_cells_evaluated"] != cells
            or measured["status_OK"] != 2 * pairs
        ):
            raise ValueError("Full-DP scientific metrics mismatch")
        finalizer_plan = (
            ["--dp-cell-plan", str(product / PLAN)] if dp_backend == BACKEND else []
        )
        subprocess.run(
            [
                sys.executable,
                str(finalize_script),
                "--alignment-jobs",
                str(paths[JOBS_OUT]),
                "--nonexact-alignments",
                str(product / DP_OUTPUT),
                "--policy-skipped",
                str(product / POLICY_SKIP),
                "--output",
                str(product / FINAL_OUTPUT),
                "--summary",
                str(product / FINAL_SUMMARY),
                *args,
                *finalizer_plan,
            ],
            check=True,
            env=environment,
        )
        final_report = json.loads((product / FINAL_SUMMARY).read_text())
        if (
            final_report.get("stage") != "06C1"
            or final_report.get("releases") != releases
            or final_report.get("aligned_nonexact_pair_count") != counts["selected"]
            or final_report.get("policy_skipped_nonexact_count") != counts["skipped"]
            or final_report.get("nonexact_directional_full_dp_cell_count") != cells
        ):
            raise ValueError("06C1 output violates canonical DP coverage")
        if pins != {name: sha256(path) for name, path in input_paths.items()}:
            raise ValueError("Canonical DP input changed during execution")
        if (
            plan_info is not None
            and sha256(product / PLAN) != (plan_info["plan_sha256"])
        ):
            raise ValueError("Certified DP plan changed during execution")
        if plan_info is not None and plan_info["input_sha256"] != {
            name: sha256(Path(name)) for name in plan_info["input_sha256"]
        }:
            raise ValueError("Certified DP input changed after planning")
        output_names = (*OUTPUTS, PLAN) if dp_backend == BACKEND else OUTPUTS
        if linear_dp_binary is not None:
            output_names = (*output_names, ROUTING)
        report = {
            "schema": BOUNDED_SCHEMA if dp_backend == BACKEND else SCHEMA,
            "dp_backend": dp_backend,
            "releases": releases,
            "dp_gate": counts,
            "dp_gate_contract": DP_GATE_CONTRACT,
            "dp_chunks": chunks,
            "dp_cells": cells,
            "go_donor_decisions": 0,
            "inputs_sha256": pins,
            "output_sha256": {name: sha256(product / name) for name in output_names},
        }
        if plan_info is not None:
            report["certified_plan"] = plan_info
        if linear_dp_binary is not None:
            report["dp_execution_backend"] = EXECUTION_BACKEND
            report["dp_execution"] = execution_info or {
                "chunks": 0,
                "invocations": 0,
                "completed_pairs": 0,
            }
        (product / SUMMARY).write_text(
            json.dumps(report, sort_keys=True, indent=2) + "\n",
            encoding="utf-8",
        )
        if destination.exists() or destination.is_symlink():
            raise FileExistsError("Concurrent DP output publication")
        if linear_dp_binary is not None:
            for path in product.iterdir():
                with path.open("rb") as stream:
                    os.fsync(stream.fileno())
            descriptor = os.open(product, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
        os.rename(product, destination)
        if linear_dp_binary is not None:
            descriptor = os.open(destination.parent, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
    return report
