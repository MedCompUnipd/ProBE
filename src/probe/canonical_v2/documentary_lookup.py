"""Discover release-local NONEXACT with START/END EXACT search anchors.

EXACT attributes from either release are lookup keys only. Candidate
sequences, resolved TaxIDs and GO eligibility remain release-specific.
No GO donation or DP eligibility is authorized by this discovery.
"""

from __future__ import annotations

import csv
import hashlib
import json
import os
import sqlite3
import tempfile
from collections import Counter
from pathlib import Path

from probe.canonical_v2 import exact_documentary_registry
from probe.resolver import is_known_nonidentifying_locus
from probe.stage2c_full_metadata_recovery import _parse_header
from probe.workflow.artifacts import validate_bundle

SCHEMA = "probe-canonical-v2-documentary-lookup-v3"
OUTPUT = "documentary_nonexact_matches.tsv"
SUMMARY = "documentary_nonexact_matches.summary.json"
FIELDS = (
    "release",
    "target_id",
    "candidate_rid",
    "candidate_accession",
    "candidate_raw_ox",
    "candidate_species_taxid",
    "candidate_entry",
    "candidate_sequence_length",
    "candidate_pe",
    "candidate_sv",
    "candidate_sec",
    "candidate_gene",
    "target_resolved_taxid",
    "candidate_resolved_taxid",
    "candidate_frag",
    "source_kind",
    "exact_source_rid",
    "exact_source_tax_scope",
    "source_roles",
    "source_key_type",
    "source_key_value",
    "candidate_key_type",
    "candidate_key_value",
    "match_class",
    "source_policy_strict",
    "source_policy_same_species_exact",
    "target_key_multiplicity",
    "candidate_key_multiplicity",
    "source_key_origin",
    "candidate_key_origin",
    "candidate_sequence_sha256",
    "exact_origin_release",
    "exact_origin_target_taxid",
)
KEY_KINDS = frozenset(
    {
        "ACCESSION",
        "GENE_NAME",
        "GENE_SYNONYM",
        "ORDERED_LOCUS",
        "ORF",
    }
)
INDEPENDENT = frozenset({"ACCESSION", "ORDERED_LOCUS", "ORF"})
SOURCE_KEY_ORIGINS = {
    "EXACT": frozenset(
        {
            "PRIMARY_ACCESSION",
            "SECONDARY_ACCESSION",
            "GENE_N",
            "GENE_S",
            "GENE_L",
            "GENE_O",
        }
    ),
    "INDEPENDENT_04D1": frozenset({"ORIGINAL_DOCUMENTARY_KEY"}),
}
_ALLOWED_AA = frozenset(b"ACDEFGHIKLMNPQRSTVWYUOBJXZ")
_AMBIGUOUS_AA = frozenset(b"BJXZ")


def _sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _rows(path: Path, required: set[str]):
    with path.open(encoding="utf-8", newline="") as stream:
        reader = csv.DictReader(stream, delimiter="\t")
        fields = reader.fieldnames
        if fields is None or len(set(fields)) != len(fields):
            raise ValueError(f"Missing/duplicate TSV header: {path}")
        if not required.issubset(fields):
            raise ValueError(f"Missing columns: {sorted(required - set(fields))}")
        for number, row in enumerate(reader, 2):
            if None in row or any(value is None for value in row.values()):
                raise ValueError(f"Malformed TSV: {path}:{number}")
            yield row


def _positive(value: str) -> str:
    if not value or not value.isascii() or not value.isdecimal():
        raise ValueError(f"Invalid TaxID/RID: {value!r}")
    if int(value) < 1 or str(int(value)) != value:
        raise ValueError(f"Noncanonical TaxID/RID: {value!r}")
    return value


def _group(key_type: str) -> str:
    if key_type not in KEY_KINDS:
        raise ValueError(f"Unexpected documentary key type: {key_type}")
    if key_type in {"GENE_NAME", "GENE_SYNONYM"}:
        return "GENE_LABEL"
    return key_type


def _normalize(value: str) -> str:
    return "".join(c for c in value.casefold() if c.isalnum())


def _usable(kind: str, value: str) -> bool:
    if not value or any(char in value for char in "\t\r\n"):
        return False
    if kind in {"ORDERED_LOCUS", "ORF"}:
        return not is_known_nonidentifying_locus(kind, value)
    return True


def _targets(
    path: Path,
    taxonomy_path: Path,
) -> dict[str, tuple[str, bytes]]:
    taxonomy: dict[str, str] = {}
    for row in _rows(taxonomy_path, {"target_id", "resolved_taxid"}):
        target = row["target_id"]
        if not target or target in taxonomy:
            raise ValueError("Missing or duplicated target taxonomy")
        taxid = row["resolved_taxid"]
        taxonomy[target] = _positive(taxid) if taxid else ""
    result: dict[str, tuple[str, bytes]] = {}
    for row in _rows(path, {"target_id", "normalized_sequence"}):
        target = row["target_id"]
        if not target or target in result:
            raise ValueError("Missing or duplicated target ID")
        sequence = row["normalized_sequence"].encode("ascii")
        if not sequence:
            raise ValueError("Target sequence is empty")
        if target not in taxonomy:
            raise ValueError("Target missing from 02B taxonomy")
        result[target] = taxonomy[target], sequence
    if set(result) != set(taxonomy):
        raise ValueError("01/02B target sets disagree")
    return result


def _ox_taxonomy(path: Path) -> dict[str, tuple[str, str]]:
    mapping: dict[str, tuple[str, str]] = {}
    required = {"raw_ox", "resolved_taxid", "species_taxid"}
    for row in _rows(path, required):
        raw = row["raw_ox"]
        resolved = row["resolved_taxid"]
        species = row["species_taxid"]
        if not raw or raw in mapping:
            raise ValueError("Duplicate or missing OX taxonomy mapping")
        if resolved:
            _positive(resolved)
        if species:
            _positive(species)
        mapping[raw] = resolved, species
    return mapping


def _insert(
    db: sqlite3.Connection,
    target: str,
    target_tax: str,
    kind: str,
    value: str,
    source: str,
    exact_rid: str,
    scope: str,
    roles: str,
    origin: str,
    origin_release: str,
    origin_target_taxid: str,
) -> None:
    if not _usable(kind, value):
        return
    db.execute(
        "INSERT OR IGNORE INTO posting VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            target,
            target_tax,
            _group(kind),
            kind,
            value,
            _normalize(value),
            source,
            exact_rid,
            scope,
            roles + ":" + origin,
            origin_release,
            origin_target_taxid,
        ),
    )


def _load_exact(
    db: sqlite3.Connection,
    register: Path,
    origin_release: str,
    evaluation_release: str,
    targets: dict[str, tuple[str, bytes]],
    targets_sha256: str,
) -> int:
    info = register / exact_documentary_registry.SUMMARY
    data = register / exact_documentary_registry.OUTPUT
    if any(not path.is_file() or path.is_symlink() for path in (info, data)):
        raise ValueError("Missing or symlinked EXACT registry payload")
    summary = json.loads(info.read_text("utf-8"))
    if (
        summary.get("schema") != exact_documentary_registry.SCHEMA
        or summary.get("release") != origin_release
        or summary.get("input_sha256", {}).get("targets") != targets_sha256
        or summary.get("output_sha256", {}).get(exact_documentary_registry.OUTPUT)
        != _sha(data)
        or summary.get("dp_jobs_authorized") != 0
    ):
        raise ValueError("EXACT register has invalid SHA or scientific contract")
    required = {
        "release",
        "target_id",
        "exact_candidate_rid",
        "tax_scope",
        "key_type",
        "key_value",
        "metadata_field",
        "target_resolved_taxid",
        "candidate_resolved_taxid",
        "source_full_sequence_sha256",
    }
    count = 0
    for row in _rows(data, required):
        target = row["target_id"]
        if row["release"] != origin_release or target not in targets:
            raise ValueError("EXACT register release/target mismatch")
        if not targets[target][0]:
            raise ValueError("Unresolved target found in EXACT register")
        origin_target_tax = _positive(row["target_resolved_taxid"])
        if (
            origin_release == evaluation_release
            and origin_target_tax != targets[target][0]
        ):
            raise ValueError("EXACT register target taxonomy mismatch")
        scope = row["tax_scope"]
        exact_tax = row["candidate_resolved_taxid"]
        if scope not in {"SAME_TAXID", "SAME_SPECIES"}:
            raise ValueError("Invalid inherited taxonomy scope")
        if (exact_tax == origin_target_tax) != (scope == "SAME_TAXID"):
            raise ValueError("Inherited scope contradicts resolved TaxID")
        _positive(exact_tax)
        rid = _positive(row["exact_candidate_rid"])
        key_type = row["key_type"]
        if key_type not in KEY_KINDS:
            raise ValueError("Unexpected EXACT metadata key type")
        _insert(
            db,
            target,
            targets[target][0],
            key_type,
            row["key_value"],
            "EXACT",
            rid,
            scope,
            "EXACT_VERIFIED",
            row["metadata_field"],
            origin_release,
            origin_target_tax,
        )
        count += 1
    return count


def _load_independent(
    db: sqlite3.Connection,
    index: Path,
    release: str,
    targets: dict[str, tuple[str, bytes]],
) -> int:
    required = {
        "release",
        "target_id",
        "key_type",
        "key_value",
        "source_roles",
        "primary_bucket",
        "key_target_multiplicity",
    }
    count = 0
    for row in _rows(index, required):
        if row["release"] != release:
            continue
        target = row["target_id"]
        if target not in targets or row["key_type"] not in INDEPENDENT:
            raise ValueError("Invalid independently documented key")
        if not targets[target][0]:
            continue
        if not row["primary_bucket"]:
            raise ValueError("Independent key lacks taxonomy bucket")
        if int(_positive(row["key_target_multiplicity"])) < 1:
            raise ValueError("Invalid independent key multiplicity")
        _insert(
            db,
            target,
            targets[target][0],
            row["key_type"],
            row["key_value"],
            "INDEPENDENT_04D1",
            "",
            "INDEPENDENT",
            row["source_roles"],
            "ORIGINAL_DOCUMENTARY_KEY",
            release,
            targets[target][0],
        )
        count += 1
    return count


def _scan(
    db: sqlite3.Connection,
    fasta: Path,
    fasta_sha: str,
    release: str,
    ox_map: dict[str, tuple[str, str]],
    targets: dict[str, tuple[str, bytes]],
    counts: Counter[str],
) -> None:
    digest = hashlib.sha256()
    rid = 0
    allowed_aa = bytes(sorted(_ALLOWED_AA))
    active_taxids = {
        row[0] for row in db.execute("SELECT DISTINCT target_tax FROM posting")
    }
    query = """
        SELECT p.target, p.kind, p.value, p.source, p.exact_rid,
               p.scope, p.roles, p.origin_release, p.origin_target_tax
        FROM posting p
        WHERE p.target_tax=? AND p.key_group=? AND p.value=?
    """
    norm_query = """
        SELECT p.target, p.kind, p.value, p.source, p.exact_rid,
               p.scope, p.roles, p.origin_release, p.origin_target_tax
        FROM posting p
        WHERE p.target_tax=? AND p.key_group=?
          AND p.normalized=? AND p.value!=?
    """
    with fasta.open("rb", buffering=8 * 1024 * 1024) as stream:
        while header := stream.readline():
            sequence = stream.readline()
            if not sequence or not sequence.endswith(b"\n"):
                raise ValueError("Truncated FULL V2 FASTA")
            digest.update(header)
            digest.update(sequence)
            rid += 1
            facts = _parse_header(header.strip(), rid)
            peptide = sequence.rstrip(b"\r\n")
            if not peptide or int(facts["LEN"]) != len(peptide):
                raise ValueError("Invalid FULL sequence length")
            candidate_tax, candidate_species = ox_map.get(
                facts["OX"],
                ("", ""),
            )
            if not candidate_tax or not candidate_species:
                continue
            if peptide.translate(None, allowed_aa):
                counts["invalid_alphabet_candidate_records"] += 1
                continue
            if any(symbol in peptide for symbol in _AMBIGUOUS_AA):
                counts["ambiguous_candidate_records"] += 1
            if candidate_tax not in active_taxids:
                continue
            candidate_sha = hashlib.sha256(peptide).hexdigest()
            keys = set(
                exact_documentary_registry._gene_keys(
                    facts.get("GENE", ""),
                    facts["primary_accession"],
                    facts.get("SEC", ""),
                )
            )
            for kind, value, origin in sorted(keys):
                if not _usable(kind, value):
                    continue
                group = _group(kind)
                postings = [
                    (*row, "LITERAL")
                    for row in db.execute(
                        query,
                        (candidate_tax, group, value),
                    )
                ]
                normalized = _normalize(value)
                if len(normalized) >= 4:
                    postings.extend(
                        (*row, "NORMALIZED_HINT")
                        for row in db.execute(
                            norm_query,
                            (candidate_tax, group, normalized, value),
                        )
                    )
                for (
                    target,
                    source_kind,
                    source_value,
                    source,
                    exact_rid,
                    scope,
                    roles,
                    origin_release,
                    origin_target_tax,
                    match_class,
                ) in postings:
                    target_tax, target_seq = targets[target]
                    if candidate_tax != target_tax or peptide == target_seq:
                        continue
                    if (
                        source == "EXACT"
                        and origin_release == release
                        and exact_rid == str(rid)
                    ):
                        raise ValueError("EXACT registry conflicts with FULL scan")
                    db.execute(
                        "INSERT OR IGNORE INTO matched VALUES "
                        "(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        (
                            target,
                            rid,
                            facts["primary_accession"],
                            facts["OX"],
                            candidate_tax,
                            candidate_species,
                            facts["entry"],
                            facts["LEN"],
                            facts["PE"],
                            facts["SV"],
                            facts.get("SEC", ""),
                            facts.get("GENE", ""),
                            facts["FRAG"],
                            source,
                            exact_rid,
                            scope,
                            roles,
                            source_kind,
                            source_value,
                            kind,
                            value,
                            match_class,
                            origin,
                            candidate_sha,
                            origin_release,
                            origin_target_tax,
                        ),
                    )
                    counts["raw_match_observations"] += 1
    if digest.hexdigest() != fasta_sha:
        raise ValueError("FULL V2 SHA256 changed during documentary scan")
    counts["full_records_scanned"] = rid


def _publish(
    db: sqlite3.Connection,
    path: Path,
    release: str,
    targets: dict[str, tuple[str, bytes]],
    counts: Counter[str],
) -> None:
    # Count ambiguity across the entire inherited + independent target set.
    db.execute("""
        CREATE TEMP TABLE target_multiplicity AS
        SELECT key_group, value, COUNT(DISTINCT target) AS n
        FROM posting GROUP BY key_group, value
    """)
    db.execute("""
        CREATE INDEX target_mult_idx ON target_multiplicity(key_group, value)
    """)
    db.execute("""
        CREATE TEMP TABLE candidate_multiplicity AS
        SELECT target, source_kind, source_value,
               COUNT(DISTINCT rid) AS n
        FROM matched GROUP BY target, source_kind, source_value
    """)
    db.execute("""
        CREATE INDEX candidate_mult_idx ON candidate_multiplicity
        (target, source_kind, source_value)
    """)
    query = """
        SELECT m.target, m.rid, m.accession, m.raw_ox, m.candidate_tax,
               m.species_tax, m.entry, m.seq_len, m.pe, m.sv,
               m.sec, m.gene, m.frag, m.source, m.exact_rid, m.scope, m.roles,
               m.source_kind, m.source_value, m.candidate_kind,
               m.candidate_value, m.match_class, m.origin, m.candidate_sha,
               tm.n, cm.n, m.origin_release, m.origin_target_tax
        FROM matched m
        JOIN target_multiplicity tm ON tm.key_group=(
            CASE WHEN m.source_kind IN ('GENE_NAME','GENE_SYNONYM')
                THEN 'GENE_LABEL' ELSE m.source_kind END
        ) AND tm.value=m.source_value
        JOIN candidate_multiplicity cm ON cm.target=m.target
          AND cm.source_kind=m.source_kind
          AND cm.source_value=m.source_value
        ORDER BY m.target, m.rid, m.source, m.exact_rid,
                 m.source_kind, m.source_value, m.match_class,
                 m.candidate_kind, m.candidate_value, m.roles
    """
    with path.open("x", encoding="utf-8", newline="") as stream:
        writer = csv.writer(stream, delimiter="\t", lineterminator="\n")
        writer.writerow(FIELDS)
        for row in db.execute(query):
            (
                target,
                rid,
                accession,
                raw_ox,
                candidate_tax,
                candidate_species,
                entry,
                seq_len,
                pe,
                sv,
                sec,
                gene,
                frag,
                source,
                exact_rid,
                scope,
                roles,
                source_kind,
                source_value,
                candidate_kind,
                candidate_value,
                match_class,
                origin,
                candidate_sha,
                target_count,
                candidate_count,
                origin_release,
                origin_target_tax,
            ) = row
            strict = source == "INDEPENDENT_04D1" or scope == "SAME_TAXID"
            extended = strict or scope == "SAME_SPECIES"
            # 04D1 roles themselves contain colons, e.g. START:ORDERED_LOCUS.
            # _insert appends one colon-free origin; keep it in its own field.
            source_roles, separator, source_origin = roles.rpartition(":")
            if (
                not separator
                or not source_roles
                or source_origin not in SOURCE_KEY_ORIGINS[source]
            ):
                raise ValueError("Invalid documentary source roles or origin")
            writer.writerow(
                (
                    release,
                    target,
                    rid,
                    accession,
                    raw_ox,
                    candidate_species,
                    entry,
                    seq_len,
                    pe,
                    sv,
                    sec,
                    gene,
                    targets[target][0],
                    candidate_tax,
                    frag,
                    source,
                    exact_rid,
                    scope,
                    source_roles,
                    source_kind,
                    source_value,
                    candidate_kind,
                    candidate_value,
                    match_class,
                    int(strict),
                    int(extended),
                    target_count,
                    candidate_count,
                    source_origin,
                    origin,
                    candidate_sha,
                    origin_release,
                    origin_target_tax,
                )
            )
            counts["matched_rows"] += 1
            counts[
                "matched_literal"
                if match_class == "LITERAL"
                else "matched_normalized_hint"
            ] += 1
            counts["source_" + source] += 1


def discover_documentary_nonexact(
    *,
    release: str,
    targets: Path,
    target_taxonomy: Path,
    exact_register: Path,
    independent_index: Path,
    ox_taxonomy: Path,
    full_manifest: Path,
    other_exact_register: Path | None = None,
    other_release: str | None = None,
    destination: Path,
) -> dict:
    """Read pinned inputs; publish traceable matches without DP or GO."""
    if not release or not destination.parent.is_dir():
        raise ValueError("Invalid release or destination parent")
    if destination.exists() or destination.is_symlink():
        raise FileExistsError("Documentary lookup output already exists")
    inputs = (
        targets,
        target_taxonomy,
        independent_index,
        ox_taxonomy,
        full_manifest,
    )
    if any(not path.is_file() or path.is_symlink() for path in inputs):
        raise ValueError("Missing or symlinked documentary input")
    if not exact_register.is_dir() or exact_register.is_symlink():
        raise ValueError("Missing or symlinked EXACT evidence register")
    if (other_exact_register is None) != (other_release is None):
        raise ValueError("Other EXACT register and release must be paired")
    if other_exact_register is not None:
        if other_release == release or not other_release:
            raise ValueError("Two distinct release identities required")
        if not other_exact_register.is_dir() or other_exact_register.is_symlink():
            raise ValueError("Missing other-release EXACT register")
        if other_exact_register == exact_register:
            raise ValueError("Identical EXACT register directories")
    if (
        exact_register == destination
        or exact_register in destination.parents
        or destination in exact_register.parents
        or full_manifest.parent == destination
        or full_manifest.parent in destination.parents
        or (
            other_exact_register is not None
            and (
                other_exact_register == destination
                or other_exact_register in destination.parents
                or destination in other_exact_register.parents
            )
        )
    ):
        raise ValueError("Documentary output cannot overlap source bundles")
    manifest = validate_bundle(full_manifest.parent, deep=False)
    if manifest.stage != "01A_FULL" or manifest.release_id != release:
        raise ValueError("FULL V2 release or stage mismatch")
    entries = [
        item
        for item in manifest.outputs
        if item.path == "01A_full_v2.fasta" and item.sha256
    ]
    if len(entries) != 1:
        raise ValueError("Missing unique pinned FULL V2 FASTA")
    fasta = full_manifest.parent / entries[0].path
    if not fasta.is_file() or fasta.is_symlink():
        raise ValueError("Missing FULL FASTA source")
    target_rows = _targets(targets, target_taxonomy)
    ox_map = _ox_taxonomy(ox_taxonomy)
    counts: Counter[str] = Counter()
    with tempfile.TemporaryDirectory(
        prefix="probe-v2-doc-",
        dir=destination.parent,
    ) as directory:
        root = Path(directory)
        db = sqlite3.connect(str(root / "lookup.sqlite3"))
        try:
            db.execute("""
                CREATE TABLE posting (
                    target TEXT, target_tax TEXT,
                    key_group TEXT, kind TEXT, value TEXT,
                    normalized TEXT, source TEXT, exact_rid TEXT,
                    scope TEXT, roles TEXT,
                    origin_release TEXT, origin_target_tax TEXT,
                    PRIMARY KEY(target, target_tax, key_group, kind,
                                value, source, exact_rid, scope, roles,
                                origin_release, origin_target_tax)
                )
            """)
            db.execute("""
                CREATE TABLE matched (
                    target TEXT, rid INTEGER, accession TEXT,
                    raw_ox TEXT, candidate_tax TEXT, species_tax TEXT,
                    entry TEXT, seq_len TEXT, pe TEXT, sv TEXT,
                    sec TEXT, gene TEXT, frag TEXT, source TEXT,
                    exact_rid TEXT, scope TEXT, roles TEXT,
                    source_kind TEXT, source_value TEXT,
                    candidate_kind TEXT, candidate_value TEXT,
                    match_class TEXT, origin TEXT, candidate_sha TEXT,
                    origin_release TEXT, origin_target_tax TEXT,
                    PRIMARY KEY (target, rid, source, exact_rid,
                                 origin_release, source_kind,
                                 source_value, candidate_kind,
                                 candidate_value, match_class)
                )
            """)
            counts["exact_source_rows"] = _load_exact(
                db,
                exact_register,
                release,
                release,
                target_rows,
                _sha(targets),
            )
            if other_exact_register is not None:
                counts["other_release_exact_source_rows"] = _load_exact(
                    db,
                    other_exact_register,
                    other_release,
                    release,
                    target_rows,
                    _sha(targets),
                )
            counts["independent_source_rows"] = _load_independent(
                db,
                independent_index,
                release,
                target_rows,
            )
            db.execute(
                "CREATE INDEX posting_value ON posting(target_tax,key_group,value)"
            )
            db.execute(
                "CREATE INDEX posting_normalized "
                "ON posting(target_tax,key_group,normalized)"
            )
            _scan(
                db,
                fasta,
                entries[0].sha256,
                release,
                ox_map,
                target_rows,
                counts,
            )
            _publish(db, root / OUTPUT, release, target_rows, counts)
        finally:
            db.close()
        summary = {
            "schema": SCHEMA,
            "release": release,
            "sequence_alphabet": "ACDEFGHIKLMNPQRSTVWYUOBJXZ",
            "ambiguous_symbols_are_wildcards": False,
            "ambiguous_record_counter": "observed_not_rejected",
            "nonexact_taxonomy_policy": "same_resolved_taxid",
            "count": dict(sorted(counts.items())),
            "nonexact_dp_authorized": 0,
            "go_donor_decisions": 0,
            "matched_key_is_not_unique_locus_proof": True,
            "normalized_hint_alone_authorizes_donation": False,
            "source_policy_is_assigned_per_key": True,
            "documentary_query_mode": (
                "RETROSPECTIVE_START_END_UNION"
                if other_exact_register is not None
                else "SINGLE_RELEASE"
            ),
            "other_exact_release": other_release,
            "input_sha256": {
                "targets": _sha(targets),
                "target_taxonomy": _sha(target_taxonomy),
                "independent_index": _sha(independent_index),
                "ox_taxonomy": _sha(ox_taxonomy),
                "full_manifest": _sha(full_manifest),
                "full_fasta": entries[0].sha256,
                "exact_register_summary": _sha(
                    exact_register / exact_documentary_registry.SUMMARY
                ),
                "other_exact_register_summary": (
                    _sha(other_exact_register / exact_documentary_registry.SUMMARY)
                    if other_exact_register is not None
                    else None
                ),
            },
            "output_sha256": {OUTPUT: _sha(root / OUTPUT)},
        }
        (root / SUMMARY).write_text(
            json.dumps(summary, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        (root / "lookup.sqlite3").unlink()
        if destination.exists() or destination.is_symlink():
            raise FileExistsError("Concurrent documentary output")
        os.rename(root, destination)
    return summary
