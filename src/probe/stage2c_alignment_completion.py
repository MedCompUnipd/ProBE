"""Isolated NONEXACT -> full-DP 06C0 -> directional 06C1 completion.

Reuse frozen production implementations. Never change canonical bundles or
make a GO donor decision. Full-DP work has an explicit cell-budget guard.
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

from probe.stage2c_alignment_preview import (
    AUDIT_OUT,
    END_STORE,
    JOBS_OUT,
    START_STORE,
    UNION_OUT,
)
from probe.stage2c_alignment_preview import (
    SUMMARY_OUT as PREVIEW_SUMMARY,
)

SCHEMA = "stage2c-alignment-completion-v1"
NONEXACT = "2C_NONEXACT_alignment_jobs.tsv"
NONEXACT_SUMMARY = "2C_NONEXACT_summary.json"
DP_OUTPUT = "2C_06C0_nonexact_directional_glocal.tsv"
DP_SUMMARY = "2C_06C0_summary.tsv"
FINAL_OUTPUT = "2C_06C1_canonical_directional_glocal.tsv"
POLICY_SKIP = "2C_policy_skipped_nonexact.tsv"
FINAL_SUMMARY = "2C_06C1_summary.json"
SUMMARY = "2C_alignment_completion.summary.json"
OUTPUTS = (
    NONEXACT,
    NONEXACT_SUMMARY,
    POLICY_SKIP,
    DP_OUTPUT,
    DP_SUMMARY,
    FINAL_OUTPUT,
    FINAL_SUMMARY,
)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _required_file(path: Path) -> Path:
    if not path.is_file():
        raise ValueError(f"Missing input file: {path}")
    return path


def _preview(preview: Path) -> tuple[dict, dict[str, Path]]:
    if not preview.is_dir():
        raise ValueError("Preview directory missing")
    summary_path = _required_file(preview / PREVIEW_SUMMARY)
    metadata = json.loads(summary_path.read_text(encoding="utf-8"))
    if (
        metadata.get("schema") != "stage2c-alignment-preview-v1"
        or metadata.get("alignment_executed") is not False
        or metadata.get("donor_decisions") != 0
        or metadata.get("production_stages_replaced") is not False
    ):
        raise ValueError("Unrecognized alignment preview contract")
    names = (JOBS_OUT, START_STORE, END_STORE, UNION_OUT, AUDIT_OUT)
    files = {name: _required_file(preview / name) for name in names}
    hashes = metadata.get("output_sha256", {})
    if not isinstance(hashes, dict):
        raise ValueError("Preview summary does not declare file hashes")
    for name, path in files.items():
        if hashes.get(name) != sha256(path):
            raise ValueError(f"Preview SHA256 mismatch: {name}")
    releases = metadata.get("releases")
    if not isinstance(releases, dict):
        raise ValueError("Preview release map missing")
    if (
        not all(
            isinstance(releases.get(k), str) and releases[k] for k in ("START", "END")
        )
        or releases["START"] == releases["END"]
    ):
        raise ValueError("Invalid preview release identifiers")
    return metadata, files


def _jobs(path: Path, releases: set[str]) -> tuple[int, int, int]:
    total = 0
    exact = 0
    nonexact = 0
    previous = None
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle, delimiter="\t")
        required = {
            "release",
            "target_id",
            "rid",
            "exact_sequence",
            "target_length",
            "candidate_length",
            "job_scope",
            "discovery_channel",
        }
        if (
            reader.fieldnames is None
            or len(reader.fieldnames) != len(set(reader.fieldnames))
            or not required.issubset(reader.fieldnames)
        ):
            raise ValueError("Invalid preview alignment job schema")
        for row in reader:
            if None in row or any(v is None for v in row.values()):
                raise ValueError("Malformed 06A alignment row")
            rid = row["rid"]
            if not rid.isascii() or not rid.isdecimal():
                raise ValueError("Invalid RID")
            if int(rid) < 1 or str(int(rid)) != rid:
                raise ValueError("Noncanonical RID")
            key = row["release"], row["target_id"], int(rid)
            if (
                key[0] not in releases
                or not key[1]
                or (previous is not None and key <= previous)
            ):
                raise ValueError("Unsorted, duplicate or foreign 06A job")
            previous = key
            if row["exact_sequence"] not in {"0", "1"}:
                raise ValueError("Invalid 06A exactness flag")
            for field in ("target_length", "candidate_length"):
                raw = row[field]
                if (
                    not raw.isascii()
                    or not raw.isdecimal()
                    or int(raw) < 1
                    or str(int(raw)) != raw
                ):
                    raise ValueError(f"Invalid {field}")
            total += 1
            exact += row["exact_sequence"] == "1"
            nonexact += row["exact_sequence"] == "0"
    return total, exact, nonexact


def _dp_budget(
    path: Path, *, max_pair_cells: int, max_total_cells: int
) -> tuple[int, int]:
    count = 0
    total = 0
    with path.open("r", encoding="utf-8", newline="") as stream:
        reader = csv.DictReader(stream, delimiter="\t")
        for row in reader:
            pair_cells = (
                2 * (int(row["target_length"]) + 1) * (int(row["candidate_length"]) + 1)
            )
            if pair_cells > max_pair_cells:
                raise ValueError(
                    "INCOMPLETE_BUDGET: individual-pair DP cell budget exceeded"
                )
            total += pair_cells
            count += 1
            if total > max_total_cells:
                raise ValueError(
                    "INCOMPLETE_BUDGET: full-DP total cell budget exceeded"
                )
    return count, total


def _run(command: list[str]) -> None:
    subprocess.run(command, check=True, capture_output=True, text=True)


def _metric_summary(path: Path) -> dict[str, int]:
    required = {
        "job_count",
        "completed_count",
        "threads",
        "directional_alignment_count",
        "total_dp_cells_evaluated",
        "status_OK",
    }
    with path.open("r", encoding="utf-8", newline="") as stream:
        reader = csv.DictReader(stream, delimiter="\t")
        if reader.fieldnames != ["metric", "value"]:
            raise ValueError("Invalid 06C0 metrics schema")
        result = {}
        for row in reader:
            if row["metric"] in result or row["metric"] not in required:
                raise ValueError("Invalid or duplicate 06C0 metric")
            raw = row["value"]
            if not raw or not raw.isascii() or not raw.isdecimal():
                raise ValueError("Invalid 06C0 metric value")
            result[row["metric"]] = int(raw)
    if set(result) != required:
        raise ValueError("Missing 06C0 metrics")
    return result


def _run_dp_chunks(
    *,
    jobs: Path,
    product: Path,
    binary: Path,
    targets: Path,
    start_store: Path,
    end_store: Path,
    release_args: list[str],
    threads: int,
    max_chunk_cells: int,
) -> int:
    """Bound each 06C0 invocation without splitting a biological sequence.

    The caller first enforces the independent aggregate and pair budgets.
    The temporary chunk files are *not* published; canonical 06C1 validates
    the single reassembled stream before atomic publication.
    """
    chunk_dir = product / ".dp-chunk-work"
    chunk_dir.mkdir()
    part_jobs = chunk_dir / "jobs.tsv"
    part_output = chunk_dir / "alignments.tsv"
    part_summary = chunk_dir / "metrics.tsv"
    final = product / DP_OUTPUT
    counts = {
        "job_count": 0,
        "completed_count": 0,
        "threads": threads,
        "directional_alignment_count": 0,
        "total_dp_cells_evaluated": 0,
        "status_OK": 0,
    }
    chunks = 0
    expected_header = ("\t".join(_dp_header()) + "\n").encode("utf-8")

    with (
        jobs.open("r", encoding="utf-8", newline="") as source,
        final.open("xb") as merged,
    ):
        reader = csv.DictReader(source, delimiter="\t")
        if reader.fieldnames is None or len(reader.fieldnames) != len(
            set(reader.fieldnames)
        ):
            raise ValueError("Invalid NONEXACT chunk input header")
        writer = None
        part_stream = None
        chunk_count = 0
        chunk_cells = 0

        def finish_chunk() -> None:
            nonlocal part_stream, writer, chunk_count, chunk_cells, chunks
            if part_stream is None:
                return
            part_stream.close()
            part_stream = None
            writer = None
            _run(
                [
                    str(binary),
                    "--targets",
                    str(targets),
                    "--alignment-jobs",
                    str(part_jobs),
                    "--start-sequence-store",
                    str(start_store),
                    "--end-sequence-store",
                    str(end_store),
                    *release_args,
                    "--output",
                    str(part_output),
                    "--summary",
                    str(part_summary),
                    "--threads",
                    str(threads),
                ]
            )
            metrics = _metric_summary(part_summary)
            expected = {
                "job_count": chunk_count,
                "completed_count": chunk_count,
                "threads": threads,
                "directional_alignment_count": 2 * chunk_count,
                "total_dp_cells_evaluated": chunk_cells,
                "status_OK": 2 * chunk_count,
            }
            if metrics != expected:
                raise ValueError("Chunk 06C0 metrics disagree with its job list")
            with part_output.open("rb") as stream:
                if stream.readline() != expected_header:
                    raise ValueError("Chunk 06C0 output schema mismatch")
                rows = 0
                for line in stream:
                    if not line.strip():
                        raise ValueError("Blank row in chunk 06C0 result")
                    merged.write(line)
                    rows += 1
                if rows != chunk_count:
                    raise ValueError("Chunk 06C0 row count mismatch")
            for name in (
                "job_count",
                "completed_count",
                "directional_alignment_count",
                "total_dp_cells_evaluated",
                "status_OK",
            ):
                counts[name] += metrics[name]
            chunks += 1
            for file in (part_jobs, part_output, part_summary):
                file.unlink()
            chunk_count = 0
            chunk_cells = 0

        merged.write(expected_header)
        try:
            for row in reader:
                pair_cells = (
                    2
                    * (int(row["target_length"]) + 1)
                    * (int(row["candidate_length"]) + 1)
                )
                if pair_cells > max_chunk_cells:
                    raise ValueError(
                        "Single DP pair exceeds the configured chunk budget"
                    )
                if chunk_count and chunk_cells + pair_cells > max_chunk_cells:
                    finish_chunk()
                if part_stream is None:
                    part_stream = part_jobs.open("x", encoding="utf-8", newline="")
                    writer = csv.DictWriter(
                        part_stream,
                        fieldnames=reader.fieldnames,
                        delimiter="\t",
                        lineterminator="\n",
                    )
                    writer.writeheader()
                writer.writerow(row)
                chunk_count += 1
                chunk_cells += pair_cells
            finish_chunk()
        finally:
            if part_stream is not None:
                part_stream.close()

    with (product / DP_SUMMARY).open("x", encoding="utf-8", newline="") as stream:
        writer = csv.writer(stream, delimiter="\t", lineterminator="\n")
        writer.writerow(("metric", "value"))
        for name, value in counts.items():
            writer.writerow((name, value))
    chunk_dir.rmdir()
    return chunks


def _gate_rows(path: Path, required: set[str]):
    """Read pinned candidate tables, allowing legacy duplicate `entry` only."""
    with path.open("r", encoding="utf-8", newline="") as stream:
        reader = csv.DictReader(stream, delimiter="\t")
        fields = reader.fieldnames or []
        duplicates = {field for field in fields if fields.count(field) > 1}
        if not required.issubset(fields) or duplicates - {"entry"}:
            raise ValueError(f"Invalid candidate gate schema: {path}")
        for number, row in enumerate(reader, start=2):
            if None in row or any(item is None for item in row.values()):
                raise ValueError(f"Malformed candidate gate TSV: {path}:{number}")
            yield row


def _select_dp_jobs(
    *,
    nonexact_in: Path,
    primary: Path,
    audit: Path,
    canonical: Path,
    taxonomy: dict[str, Path],
    output: Path,
    excluded: Path,
    database: Path,
) -> tuple[int, int, int, int]:
    """Gate only DP execution; leave the 06A candidate universe untouched.

    DP requires identical verified resolved TaxIDs, even for 05A2/05C2.
    Canonical provenance is verified but never overrides this requirement.
    Neither a species bucket nor a discovery-channel label determines TaxID.
    """
    from probe.resolver_anchors import _load_target_taxonomy, _taxid

    legacy_cols = {
        "release", "target_id", "rid", "candidate_primary_accession",
        "raw_ox", "resolved_taxid", "sequence_length",
    }
    primary_cols = legacy_cols | {"species_taxid", "discovery_channel"}
    audit_cols = {
        "release", "target_id", "rid", "candidate_primary_accession",
        "raw_ox", "target_resolved_taxid", "anchor_resolved_taxid",
        "anchor_species_taxid", "audit_alignment_allowed",
        "key_source_reason", "audit_class",
        "record_already_in_primary_union_for_target",
    }
    wanted: set[tuple[str, str]] = set()
    for row in _gate_rows(
        nonexact_in,
        {"release", "target_id", "rid", "exact_sequence"},
    ):
        if row["exact_sequence"] != "0" or row["release"] not in taxonomy:
            raise ValueError("Unexpected Stage 2C NONEXACT input")
        wanted.add((row["release"], row["target_id"]))
    targets = _load_target_taxonomy(taxonomy, wanted)

    with sqlite3.connect(database) as db:
        db.execute("PRAGMA journal_mode=DELETE")
        db.execute(
            "CREATE TABLE legacy (release TEXT, target TEXT, rid TEXT, "
            "accession TEXT, ox TEXT, taxid TEXT, length TEXT, "
            "PRIMARY KEY(release,target,rid))"
        )
        db.execute(
            "CREATE TABLE candidates (release TEXT, target TEXT, rid TEXT, "
            "scope TEXT, accession TEXT, ox TEXT, taxid TEXT, species TEXT, "
            "length TEXT, channel TEXT, audit_taxid TEXT, "
            "PRIMARY KEY(release,target,rid))"
        )
        for row in _gate_rows(canonical, legacy_cols):
            db.execute(
                "INSERT INTO legacy VALUES (?,?,?,?,?,?,?)",
                (
                    row["release"], row["target_id"], row["rid"],
                    row["candidate_primary_accession"], row["raw_ox"],
                    row["resolved_taxid"], row["sequence_length"],
                ),
            )
        for row in _gate_rows(primary, primary_cols):
            db.execute(
                "INSERT INTO candidates VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (
                    row["release"], row["target_id"], row["rid"],
                    "PRIMARY", row["candidate_primary_accession"],
                    row["raw_ox"], row["resolved_taxid"],
                    row["species_taxid"], row["sequence_length"],
                    row["discovery_channel"], "",
                ),
            )
        for row in _gate_rows(audit, audit_cols):
            key = (row["release"], row["target_id"], row["rid"])
            overlapping = db.execute(
                "SELECT accession,ox,taxid,species FROM candidates "
                "WHERE release=? AND target=? AND rid=?", key,
            ).fetchone()
            marked_overlap = row["record_already_in_primary_union_for_target"]
            if marked_overlap not in {"0", "1"} or (overlapping is not None) != (
                marked_overlap == "1"
            ):
                raise ValueError("Contradictory 05C2 primary overlap flag")
            if overlapping is not None:
                if overlapping != (
                    row["candidate_primary_accession"], row["raw_ox"],
                    row["anchor_resolved_taxid"], row["anchor_species_taxid"],
                ):
                    raise ValueError("05C2 overlap/PRIMARY identity conflict")
                # One candidate with two provenances, not two DP jobs.
                continue
            if (
                row["audit_alignment_allowed"] != "1"
                or row["key_source_reason"] != "SAME_SPECIES_WRONG_SUBTAXON"
                or row["audit_class"] != "TAXONOMY_RESCUE_AUDIT"
            ):
                # 05C2 includes audit-only rows not emitted by 06A.
                continue
            db.execute(
                "INSERT INTO candidates VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (
                    row["release"], row["target_id"], row["rid"],
                    "TAXONOMY_AUDIT", row["candidate_primary_accession"],
                    row["raw_ox"], row["anchor_resolved_taxid"],
                    row["anchor_species_taxid"], "", "TAXONOMY_AUDIT",
                    row["target_resolved_taxid"],
                ),
            )
        bad_legacy = db.execute(
            "SELECT COUNT(*) FROM legacy l LEFT JOIN candidates c "
            "ON c.release=l.release AND c.target=l.target AND c.rid=l.rid "
            "WHERE c.scope IS NULL OR c.scope!='PRIMARY' "
            "OR c.accession!=l.accession OR c.ox!=l.ox "
            "OR c.taxid!=l.taxid OR c.length!=l.length"
        ).fetchone()[0]
        if bad_legacy:
            raise ValueError("Canonical 05A2 keys/identities changed in preview")
        selected = skipped = audit_skipped = legacy_cross_skipped = 0
        with (
            nonexact_in.open("r", encoding="utf-8", newline="") as stream,
            output.open("x", encoding="utf-8", newline="") as out,
            excluded.open("x", encoding="utf-8", newline="") as sidecar,
        ):
            reader = csv.DictReader(stream, delimiter="\t")
            writer = csv.DictWriter(
                out, fieldnames=reader.fieldnames, delimiter="\t",
                lineterminator="\n",
            )
            skips = csv.writer(sidecar, delimiter="\t", lineterminator="\n")
            writer.writeheader()
            skips.writerow(("release", "target_id", "rid", "reason"))
            previous = None
            for row in reader:
                key = (row["release"], row["target_id"], row["rid"])
                ordered = (key[0], key[1], int(key[2]))
                if previous is not None and ordered <= previous:
                    raise ValueError("Unsorted Stage 2C NONEXACT keys")
                previous = ordered
                candidate = db.execute(
                    "SELECT scope,accession,ox,taxid,species,length,channel,"
                    "audit_taxid FROM candidates "
                    "WHERE release=? AND target=? AND rid=?",
                    key,
                ).fetchone()
                if candidate is None:
                    raise ValueError(f"NONEXACT candidate provenance absent: {key}")
                scope, accession, ox, taxid, species, length, channel, audit_tax = (
                    candidate
                )
                target = targets[key[:2]]
                target_tax = target.resolved_taxid
                resolved = _taxid(taxid, str(key))
                if target_tax is None or resolved is None:
                    raise ValueError(
                        f"Unverified resolved TaxID; DP gate closed: {key}"
                    )
                if (
                    scope != row["job_scope"]
                    or accession != row["candidate_primary_accession"]
                    or ox != row["raw_ox"]
                    or scope == "PRIMARY" and (
                        channel != row["discovery_channel"]
                        or length != row["candidate_length"]
                    )
                ):
                    raise ValueError(f"06A/05A2/05C2 identity conflict: {key}")
                legacy = db.execute(
                    "SELECT 1 FROM legacy WHERE release=? AND target=? AND rid=?",
                    key,
                ).fetchone() is not None
                if scope == "TAXONOMY_AUDIT":
                    if (
                        audit_tax != target_tax or target_tax == resolved
                        or species != target.species_taxid
                    ):
                        raise ValueError(f"Invalid canonical taxonomy audit: {key}")
                    audit_skipped += 1
                elif target_tax != resolved and legacy:
                    legacy_cross_skipped += 1
                if target_tax == resolved:
                    writer.writerow(row)
                    selected += 1
                else:
                    skips.writerow((*key, "CROSS_TAXID_NONEXACT"))
                    skipped += 1
    return selected, skipped, audit_skipped, legacy_cross_skipped


def _provenance_input(manifest: dict, path: Path, digest: str) -> None:
    matches = [
        row for row in manifest.get("inputs", [])
        if row.get("path") == str(path)
    ]
    if len(matches) != 1 or matches[0].get("sha256") != digest:
        raise ValueError(f"Canonical 06C1 provenance mismatch: {path}")


def _provenance_output(manifest: dict, filename: str) -> str:
    matches = [
        row for row in manifest.get("outputs", [])
        if row.get("path") == filename
    ]
    if len(matches) != 1 or not isinstance(matches[0].get("sha256"), str):
        raise ValueError(f"Canonical 06C1 output not pinned: {filename}")
    return matches[0]["sha256"]


def _verified_reuse(
    directory: Path,
    work_root: Path,
    preview_meta: dict,
    targets: Path,
    binary: Path,
    releases: dict[str, str],
) -> tuple[Path, dict[str, str]]:
    """Require pinned production inputs before borrowing any DP geometry.

    Historical files are read only. The original 05D bytes were pinned by
    Stage 2C preview; its store builder preserves those canonical sequences.
    """
    run = work_root.parent.parent
    expected = run / "results/pipeline-v2/common/06C1"
    if directory != expected or directory.is_symlink():
        raise ValueError("Noncanonical 06C1 reuse directory")
    paths = {
        "06A": work_root / "common/06A/manifest.json",
        "NONEXACT": work_root / "common/NONEXACT/manifest.json",
        "06C0": work_root / "common/06C0/manifest.json",
        "06C1": directory / "manifest.json",
        "01": work_root / "common/01/manifest.json",
        "START": work_root / "START/05D/manifest.json",
        "END": work_root / "END/05D/manifest.json",
    }
    manifests = {}
    pins = {}
    for name, path in paths.items():
        if not path.is_file() or path.is_symlink():
            raise ValueError(f"Missing canonical reuse manifest: {path}")
        pins[str(path)] = sha256(path)
        manifest = json.loads(path.read_text(encoding="utf-8"))
        if manifest.get("stage") != ("05D" if name in releases else name):
            raise ValueError(f"Wrong canonical reuse manifest stage: {path}")
        if manifest.get("status") != "complete":
            raise ValueError(f"Incomplete canonical reuse manifest: {path}")
        manifests[name] = manifest
    a, c, final = (manifests[n] for n in ("06A", "06C0", "06C1"))
    _provenance_input(final, paths["06A"], pins[str(paths["06A"])])
    _provenance_input(final, paths["06C0"], pins[str(paths["06C0"])])
    _provenance_input(
        c, paths["NONEXACT"], pins[str(paths["NONEXACT"])]
    )
    for name in ("START", "END"):
        manifest_path = paths[name]
        digest = pins[str(manifest_path)]
        _provenance_input(a, manifest_path, digest)
        _provenance_input(c, manifest_path, digest)
        store = work_root / name / "05D/05D_candidate_sequences.tsv"
        old_store_sha = _provenance_output(
            manifests[name], "05D_candidate_sequences.tsv"
        )
        if preview_meta.get("input_sha256", {}).get(str(store)) != old_store_sha:
            raise ValueError(f"Historical/preview 05D mismatch: {name}")
    _provenance_input(a, paths["01"], pins[str(paths["01"])])
    _provenance_input(c, paths["01"], pins[str(paths["01"])])
    if sha256(targets) != _provenance_output(
        manifests["01"], "01_targets.tsv"
    ):
        raise ValueError("Historical/preview target sequences differ")
    expected_binary = c.get("implementation", {}).get("binary_sha256")
    policy = c.get("parameters", {}).get("alignment_policy", {})
    if (
        expected_binary != sha256(binary)
        or policy.get("algorithm") != "AFFINE_FULL_DP"
        or policy.get("banding") is not False
        or policy.get("modes") != ["TARGET_GLOBAL", "CANDIDATE_GLOBAL"]
        or tuple(policy.get(k) for k in (
            "match", "mismatch", "gap_open", "gap_extend"
        )) != (2, -1, -3, -1)
        or final.get("parameters", {}).get("start_release_id")
        != releases["START"]
        or final.get("parameters", {}).get("end_release_id")
        != releases["END"]
    ):
        raise ValueError("Historical DP implementation or releases differ")
    table = directory / "06C1_canonical_directional_glocal.tsv"
    if not table.is_file() or table.is_symlink():
        raise ValueError("Missing canonical 06C1 reuse table")
    expected_hash = _provenance_output(
        final, "06C1_canonical_directional_glocal.tsv"
    )
    if sha256(table) != expected_hash:
        raise ValueError("Historical 06C1 SHA256 mismatch")
    pins[str(table)] = expected_hash
    summary = directory / "06C1_summary.json"
    if not summary.is_file() or summary.is_symlink():
        raise ValueError("Missing canonical 06C1 summary")
    if sha256(summary) != _provenance_output(final, summary.name):
        raise ValueError("Historical 06C1 summary SHA256 mismatch")
    return table, pins


def _key(row: dict[str, str]) -> tuple[str, str, int]:
    raw = row["rid"]
    if not raw.isascii() or not raw.isdecimal() or int(raw) < 1:
        raise ValueError("Noncanonical alignment RID")
    if str(int(raw)) != raw:
        raise ValueError("Noncanonical alignment RID")
    return row["release"], row["target_id"], int(raw)


def _partition_reuse(
    jobs: Path, historic: Path, pending: Path, reused: Path
) -> tuple[int, int, int]:
    """Partition *already TaxID-gated* jobs using verified historical facts."""
    header = _dp_header()
    reused_count = new_count = reused_cells = 0
    with (
        jobs.open("r", encoding="utf-8", newline="") as jh,
        historic.open("r", encoding="utf-8", newline="") as hh,
        pending.open("x", encoding="utf-8", newline="") as ph,
        reused.open("x", encoding="utf-8", newline="") as rh,
    ):
        job_reader = csv.DictReader(jh, delimiter="\t")
        old_reader = csv.DictReader(hh, delimiter="\t")
        if old_reader.fieldnames != header or job_reader.fieldnames is None:
            raise ValueError("Unrecognized historical 06C1 or selected job schema")
        pending_writer = csv.DictWriter(
            ph, fieldnames=job_reader.fieldnames, delimiter="\t",
            lineterminator="\n",
        )
        reuse_writer = csv.DictWriter(
            rh, fieldnames=header, delimiter="\t", lineterminator="\n"
        )
        pending_writer.writeheader()
        reuse_writer.writeheader()
        previous_old = None
        old = None

        def advance():
            nonlocal previous_old
            record = next(old_reader, None)
            if record is not None:
                if None in record or any(v is None for v in record.values()):
                    raise ValueError("Malformed canonical 06C1 row")
                key = _key(record)
                if previous_old is not None and key <= previous_old:
                    raise ValueError("Unsorted canonical 06C1 row")
                previous_old = key
            return record

        old = advance()
        for job in job_reader:
            key = _key(job)
            if job["exact_sequence"] != "0":
                raise ValueError("EXACT row in DP reuse request")
            while old is not None and _key(old) < key:
                old = advance()
            can_reuse = old is not None and _key(old) == key
            if can_reuse:
                can_reuse = all(
                    old[name] == job[name]
                    for name in (
                        "target_optional_accession",
                        "candidate_primary_accession",
                        "target_length", "candidate_length",
                    )
                ) and all(
                    old[f"{prefix}_status"] == "OK"
                    for prefix in ("target_global", "candidate_global")
                )
            if not can_reuse:
                pending_writer.writerow(job)
                new_count += 1
                continue
            cells = (
                (int(job["target_length"]) + 1)
                * (int(job["candidate_length"]) + 1)
            )
            if any(
                old[f"{prefix}_cells_evaluated"] != str(cells)
                for prefix in ("target_global", "candidate_global")
            ):
                raise ValueError(f"Invalid reused directional cells: {key}")
            reuse_writer.writerow({
                **old,
                **{field: job[field] for field in header[:9]},
            })
            reused_count += 1
            reused_cells += 2 * cells
    return new_count, reused_count, reused_cells


def _merge_dp(
    jobs: Path, computed: Path, reused: Path, merged: Path
) -> None:
    """Restore the full TaxID-permitted order for the unmodified 06C1 CLI."""
    header = _dp_header()
    with (
        jobs.open("r", encoding="utf-8", newline="") as jh,
        computed.open("r", encoding="utf-8", newline="") as ch,
        reused.open("r", encoding="utf-8", newline="") as rh,
        merged.open("x", encoding="utf-8", newline="") as mh,
    ):
        jobs_reader = csv.DictReader(jh, delimiter="\t")
        computed_reader = csv.DictReader(ch, delimiter="\t")
        reused_reader = csv.DictReader(rh, delimiter="\t")
        if computed_reader.fieldnames != header or reused_reader.fieldnames != header:
            raise ValueError("Combined DP table schema mismatch")
        writer = csv.DictWriter(
            mh, fieldnames=header, delimiter="\t", lineterminator="\n"
        )
        writer.writeheader()
        new = next(computed_reader, None)
        old = next(reused_reader, None)
        for job in jobs_reader:
            key = _key(job)
            use_old = old is not None and _key(old) == key
            use_new = new is not None and _key(new) == key
            if use_old == use_new:
                raise ValueError(f"Missing or duplicated DP result: {key}")
            row = old if use_old else new
            if any(row[name] != job[name] for name in header[:9]):
                raise ValueError(f"DP metadata mismatch: {key}")
            writer.writerow(row)
            if use_old:
                old = next(reused_reader, None)
            else:
                new = next(computed_reader, None)
        if new is not None or old is not None:
            raise ValueError("Extraneous DP alignment after merge")


def complete_alignment_preview(
    *,
    preview: Path,
    targets: Path,
    work_root: Path,
    canonical_primary: Path,
    nonexact_script: Path,
    full_dp_binary: Path,
    finalize_script: Path,
    output_dir: Path,
    threads: int = 1,
    max_pair_cells: int = 50_000_000,
    max_total_cells: int = 500_000_000,
    max_chunk_cells: int | None = None,
    reuse_canonical: Path | None = None,
) -> dict:
    """Publish verified 06C1 output without changing canonical evidence.

    ``max_total_cells`` always bounds the complete invocation. With
    ``max_chunk_cells`` set, independent 06C0 jobs additionally observe a
    smaller per-invocation ceiling; pairs are never split into windows.
    """
    metadata, files = _preview(preview)
    source_files = [
        targets, canonical_primary, nonexact_script, full_dp_binary,
        finalize_script,
        work_root / "START/02B/02B_target_taxonomy.tsv",
        work_root / "END/02B/02B_target_taxonomy.tsv",
    ]
    for path in source_files:
        _required_file(path)
    if not os.access(full_dp_binary, os.X_OK):
        raise ValueError("Full-DP binary is not executable")
    budget_values = (threads, max_pair_cells, max_total_cells)
    if max_chunk_cells is not None:
        budget_values = (*budget_values, max_chunk_cells)
    if any(
        not isinstance(v, int) or isinstance(v, bool) or v < 1 for v in budget_values
    ):
        raise ValueError("Invalid alignment threads or cell budget")
    if output_dir.exists() or output_dir.is_symlink():
        raise FileExistsError("Alignment destination already exists")
    if not output_dir.parent.is_dir() or output_dir.parent.is_symlink():
        raise ValueError("Unsafe alignment output parent")
    source_dirs = [preview, *(path.parent for path in source_files)]
    if any(output_dir.resolve().is_relative_to(p.resolve()) for p in source_dirs):
        raise ValueError("Cannot publish inside an input directory")
    releases = metadata["releases"]
    pinned = metadata.get("input_sha256", {}).get(str(canonical_primary))
    if pinned != sha256(canonical_primary):
        raise ValueError("Unpinned or changed canonical 05A2 provenance")
    total, exact, nonexact = _jobs(files[JOBS_OUT], set(releases.values()))
    historic = None
    historic_pins: dict[str, str] = {}
    if reuse_canonical is not None:
        historic, historic_pins = _verified_reuse(
            reuse_canonical, work_root, metadata, targets, full_dp_binary,
            releases,
        )
    lock = output_dir.with_name(f".{output_dir.name}.publish-lock")
    lock.mkdir()
    try:
        with tempfile.TemporaryDirectory(
            prefix=".probe-2c-align-completion-", dir=output_dir.parent
        ) as tmp:
            product = Path(tmp) / "publication"
            product.mkdir()
            args = [
                "--start-release-id",
                releases["START"],
                "--end-release-id",
                releases["END"],
            ]
            _run(
                [
                    sys.executable,
                    str(nonexact_script),
                    "--alignment-jobs",
                    str(files[JOBS_OUT]),
                    *args,
                    "--output",
                    str(product / ".all-nonexact.tsv"),
                    "--summary",
                    str(product / ".all-nonexact.json"),
                ]
            )
            selection = json.loads(
                (product / ".all-nonexact.json").read_text(encoding="utf-8")
            )
            if (
                selection.get("stage") != "NONEXACT"
                or selection.get("input_alignment_job_count") != total
                or selection.get("exact_job_count") != exact
                or selection.get("nonexact_job_count") != nonexact
                or selection.get("releases") != releases
            ):
                raise ValueError("NONEXACT selection count/policy mismatch")
            selected, skipped, audit_skipped, legacy_skipped = _select_dp_jobs(
                nonexact_in=product / ".all-nonexact.tsv",
                primary=files[UNION_OUT],
                audit=files[AUDIT_OUT],
                canonical=canonical_primary,
                taxonomy={
                    releases["START"]: source_files[-2],
                    releases["END"]: source_files[-1],
                },
                output=product / NONEXACT,
                excluded=product / POLICY_SKIP,
                database=Path(tmp) / "candidate-gate.sqlite3",
            )
            if selected + skipped != nonexact:
                raise ValueError("DP gate lost NONEXACT candidate coverage")
            selection.update({
                "nonexact_input_count": nonexact,
                "nonexact_job_count": selected,
                "policy_skipped_nonexact_count": skipped,
                "canonical_audit_policy_skipped_count": audit_skipped,
                "canonical_legacy_cross_taxid_policy_skipped_count": (
                    legacy_skipped
                ),
                "policy": {
                    **selection["policy"],
                    "dp_requires_matching_verified_resolved_taxids": True,
                    "policy_skips_are_not_failed_alignments": True,
                },
            })
            (product / NONEXACT_SUMMARY).write_text(
                json.dumps(selection, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            (product / ".all-nonexact.tsv").unlink()
            (product / ".all-nonexact.json").unlink()
            dp_required = selected
            pending = product / ".pending-dp.tsv"
            reused = product / ".reused-dp.tsv"
            reused_count = reused_cells = 0
            if historic is None:
                shutil.copyfile(product / NONEXACT, pending)
                with reused.open("x", encoding="utf-8", newline="") as stream:
                    stream.write("\t".join(_dp_header()) + "\n")
                new_count = selected
            else:
                new_count, reused_count, reused_cells = _partition_reuse(
                    product / NONEXACT, historic, pending, reused
                )
            if new_count + reused_count != dp_required:
                raise ValueError("Reused DP partition lost selected jobs")
            budget_count, cells = _dp_budget(
                pending,
                max_pair_cells=max_pair_cells,
                max_total_cells=max_total_cells,
            )
            if budget_count != new_count:
                raise ValueError("Pending DP job count mismatch")
            chunks = 0
            if new_count:
                if max_chunk_cells is None:
                    _run(
                        [
                            str(full_dp_binary),
                            "--targets",
                            str(targets),
                            "--alignment-jobs",
                            str(pending),
                            "--start-sequence-store",
                            str(files[START_STORE]),
                            "--end-sequence-store",
                            str(files[END_STORE]),
                            *args,
                            "--output",
                            str(product / DP_OUTPUT),
                            "--summary",
                            str(product / DP_SUMMARY),
                            "--threads",
                            str(threads),
                        ]
                    )
                    chunks = 1
                else:
                    chunks = _run_dp_chunks(
                        jobs=pending,
                        product=product,
                        binary=full_dp_binary,
                        targets=targets,
                        start_store=files[START_STORE],
                        end_store=files[END_STORE],
                        release_args=args,
                        threads=threads,
                        max_chunk_cells=max_chunk_cells,
                    )
            else:
                # The canonical 06C0 runner produces a header and zero metrics.
                _write_empty_dp(product / DP_OUTPUT, product / DP_SUMMARY)
            observed = _metric_summary(product / DP_SUMMARY)
            expected = {
                "job_count": new_count,
                "completed_count": new_count,
                "threads": threads if new_count else 0,
                "directional_alignment_count": 2 * new_count,
                "total_dp_cells_evaluated": cells,
                "status_OK": 2 * new_count,
            }
            if observed != expected:
                raise ValueError("06C0 full-DP result counters disagree")
            combined = product / ".combined-dp.tsv"
            _merge_dp(product / NONEXACT, product / DP_OUTPUT, reused, combined)
            _run(
                [
                    sys.executable,
                    str(finalize_script),
                    "--alignment-jobs",
                    str(files[JOBS_OUT]),
                    "--nonexact-alignments",
                    str(combined),
                    "--policy-skipped",
                    str(product / POLICY_SKIP),
                    "--output",
                    str(product / FINAL_OUTPUT),
                    "--summary",
                    str(product / FINAL_SUMMARY),
                    *args,
                ]
            )
            finalized = json.loads(
                (product / FINAL_SUMMARY).read_text(encoding="utf-8")
            )
            if (
                finalized.get("stage") != "06C1"
                or finalized.get("releases") != releases
                or finalized.get("canonical_pair_count") != total
                or finalized.get("exact_pair_count") != exact
                or finalized.get("nonexact_pair_count") != nonexact
                or finalized.get("aligned_nonexact_pair_count") != selected
                or finalized.get("policy_skipped_nonexact_count") != skipped
                or finalized.get("nonexact_directional_full_dp_cell_count")
                != cells + reused_cells
            ):
                raise ValueError("06C1 finalization disagrees with 06A/06C0")
            summary = {
                "schema": SCHEMA,
                "releases": releases,
                "canonical_production_modified": False,
                "donor_decisions": 0,
                "same_entity_decisions": 0,
                "counts": {
                    "alignment_jobs": total,
                    "exact_pairs": exact,
                    "nonexact_pairs": nonexact,
                    "dp_required_nonexact_pairs": selected,
                    "policy_skipped_nonexact_pairs": skipped,
                    "canonical_audit_policy_skipped_pairs": audit_skipped,
                    "canonical_legacy_cross_taxid_policy_skipped_pairs": (
                        legacy_skipped
                    ),
                    "full_dp_directional_cells": cells,
                    "reused_nonexact_pairs": reused_count,
                    "computed_nonexact_pairs": new_count,
                    "reused_directional_cells": reused_cells,
                },
                "policy_skipped_nonexact_count": skipped,
                "canonical_06c1_reuse_sha256": historic_pins,
                "input_sha256": {
                    str(path): sha256(path)
                    for path in [
                        preview / PREVIEW_SUMMARY,
                        *files.values(),
                        *source_files,
                    ]
                },
                "output_sha256": {name: sha256(product / name) for name in OUTPUTS},
            }
            if max_chunk_cells is not None:
                summary["dp_chunks"] = chunks
                summary["dp_chunk_cells_limit"] = max_chunk_cells
                summary["dp_total_cells_limit"] = max_total_cells
            for scratch in (pending, reused, combined):
                scratch.unlink()
            (product / SUMMARY).write_text(
                json.dumps(summary, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            if output_dir.exists() or output_dir.is_symlink():
                raise FileExistsError("Output appeared before publication")
            os.replace(product, output_dir)
            return summary
    finally:
        lock.rmdir()


def _write_empty_dp(table: Path, summary: Path) -> None:
    with table.open("x", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, delimiter="\t", lineterminator="\n")
        writer.writerow(_dp_header())
    with summary.open("x", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, delimiter="\t", lineterminator="\n")
        writer.writerow(("metric", "value"))
        for key in (
            "job_count",
            "completed_count",
            "threads",
            "directional_alignment_count",
            "total_dp_cells_evaluated",
            "status_OK",
        ):
            writer.writerow((key, "0"))


def _dp_header() -> list[str]:
    base = [
        "release",
        "target_id",
        "rid",
        "job_scope",
        "discovery_channel",
        "target_optional_accession",
        "candidate_primary_accession",
        "target_length",
        "candidate_length",
    ]
    suffixes = [
        "status",
        "score",
        "target_start0",
        "target_end0_exclusive",
        "candidate_start0",
        "candidate_end0_exclusive",
        "target_aligned_span",
        "candidate_aligned_span",
        "target_coverage",
        "candidate_coverage",
        "matches",
        "mismatches",
        "gap_in_target_residues",
        "gap_in_candidate_residues",
        "gap_openings",
        "aligned_residue_pairs",
        "alignment_columns",
        "identity_aligned_pairs",
        "identity_alignment_columns",
        "cells_evaluated",
    ]
    return base + [
        f"{prefix}_{suffix}"
        for prefix in ("target_global", "candidate_global")
        for suffix in suffixes
    ]
