"""Attested preliminary EXACT metadata for independent NONEXACT discovery.

This register is *not* a lineage approval, donor decision, or DP admission.
Exact equality is reverified from the canonical literal FULL sequence. All
lexical comparisons are diagnostic suggestions, never documentary matches.
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

from probe.cross_strain import LITERAL_EXACT_POLICY
from probe.resolver import is_known_nonidentifying_locus
from probe.stage2c_full_metadata_recovery import _parse_header
from probe.workflow.artifacts import validate_bundle

SCHEMA = "probe-canonical-v2-exact-documentary-register-v2"
OUTPUT = "exact_documentary_keys.tsv"
SUMMARY = "exact_documentary_keys.summary.json"
KEY_FIELDS = (
    "release",
    "target_id",
    "exact_candidate_rid",
    "candidate_accession",
    "target_resolved_taxid",
    "candidate_resolved_taxid",
    "species_taxid",
    "tax_scope",
    "key_type",
    "key_value",
    "metadata_field",
    "lexical_hint",
    "same_taxid_context",
    "eligible_as_existing_04c_key",
    "key_target_candidate_multiplicity",
    "exact_candidate_collision",
    "key_status",
    "source_full_sequence_sha256",
)
UNION_FIELDS = frozenset(
    {
        "release",
        "target_id",
        "rid",
        "candidate_accession",
        "candidate_raw_ox",
        "target_resolved_taxid",
        "candidate_resolved_taxid",
        "species_taxid",
        "go_positive_exact",
        "full_exact_species_wide",
        "strict_gate",
        "same_species_exact_gate",
    }
)
TARGET_FIELDS = frozenset({"target_id", "normalized_sequence"})
VALID_AA = frozenset(b"ACDEFGHIKLMNPQRSTVWYUOBJXZ")
AMBIGUOUS_AA = frozenset(b"BJXZ")


def _sha(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(8 * 1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def _rows(path: Path, required: frozenset[str]):
    with path.open(encoding="utf-8", newline="") as stream:
        reader = csv.DictReader(stream, delimiter="\t")
        fields = reader.fieldnames
        if fields is None or len(fields) != len(set(fields)):
            raise ValueError(f"Invalid TSV header: {path}")
        if not required.issubset(fields):
            raise ValueError(f"Missing TSV fields: {sorted(required - set(fields))}")
        for line, row in enumerate(reader, 2):
            if None in row or any(value is None for value in row.values()):
                raise ValueError(f"Malformed TSV row at {path}:{line}")
            yield row


def _positive(value: str) -> int:
    if not value or not value.isascii() or not value.isdecimal():
        raise ValueError(f"Invalid positive identifier: {value!r}")
    number = int(value)
    if number == 0 or str(number) != value:
        raise ValueError(f"Noncanonical identifier: {value!r}")
    return number


def _load_targets(path: Path) -> dict[str, bytes]:
    result: dict[str, bytes] = {}
    for row in _rows(path, TARGET_FIELDS):
        target = row["target_id"]
        sequence = row["normalized_sequence"].encode("ascii")
        if not target or target in result or not sequence:
            raise ValueError("Missing, duplicated or empty target")
        result[target] = sequence
    return result


def _request_exact(
    db: sqlite3.Connection,
    path: Path,
    release: str,
    targets: dict[str, bytes],
) -> Counter[str]:
    counts: Counter[str] = Counter()
    for row in _rows(path, UNION_FIELDS):
        target, rid = row["target_id"], row["rid"]
        if row["release"] != release or target not in targets:
            raise ValueError("Discovery release/target mismatch")
        _positive(rid)
        try:
            db.execute("INSERT INTO seen VALUES (?, ?)", (target, _positive(rid)))
        except sqlite3.IntegrityError as exc:
            raise ValueError("Duplicate discovery target/RID") from exc
        for field in ("go_positive_exact", "full_exact_species_wide"):
            if row[field] not in {"0", "1"}:
                raise ValueError(f"Invalid discovery exact flag: {field}")
        if row["go_positive_exact"] != "1" and (row["full_exact_species_wide"] != "1"):
            counts["nonexact_discovery_rows"] += 1
            continue
        candidate_tax = row["candidate_resolved_taxid"]
        target_tax = row["target_resolved_taxid"]
        species = row["species_taxid"]
        for value in (candidate_tax, target_tax, species):
            _positive(value)
        same_taxid = candidate_tax == target_tax
        if not same_taxid and (
            row["strict_gate"] != "DENIED"
            or row["same_species_exact_gate"] != "ELIGIBLE"
        ):
            raise ValueError("Cross-TaxID EXACT lacks verified species gate")
        if not row["candidate_accession"] or not row["candidate_raw_ox"]:
            raise ValueError("Missing EXACT accession or OX")
        db.execute(
            "INSERT INTO candidate VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                target,
                _positive(rid),
                row["candidate_accession"],
                row["candidate_raw_ox"],
                target_tax,
                candidate_tax,
                species,
                "SAME_TAXID" if same_taxid else "SAME_SPECIES",
            ),
        )
        counts["exact_discovery_rows"] += 1
        counts["same_taxid" if same_taxid else "cross_subtaxon"] += 1
    return counts


def _gene_keys(gene: str, accession: str, sec: str):
    """Emit *observations*; strings are never normalized for approval."""
    yield "ACCESSION", accession, "PRIMARY_ACCESSION"
    for value in sec.split(",") if sec else ():
        if value:
            yield "ACCESSION", value, "SECONDARY_ACCESSION"
    types = {
        "N": "GENE_NAME",
        "S": "GENE_SYNONYM",
        "L": "ORDERED_LOCUS",
        "O": "ORF",
    }
    for group in gene.split("|") if gene else ():
        for item in group.split(";"):
            if len(item) < 3 or item[1] != ":":
                continue
            if item[0] not in types:
                continue
            for value in item[2:].split(","):
                if value:
                    yield types[item[0]], value, f"GENE_{item[0]}"


def _hint(value: str) -> str:
    """Diagnostic lookup ONLY; never use as documentary equivalence."""
    return "".join(char for char in value.casefold() if char.isalnum())


def _scan_full(
    db: sqlite3.Connection,
    fasta: Path,
    expected_sha: str,
    targets: dict[str, bytes],
    counts: Counter[str],
) -> None:
    query = db.execute("SELECT DISTINCT rid FROM candidate ORDER BY rid")
    pending = query.fetchone()
    digest = hashlib.sha256()
    ordinal = 0
    with fasta.open("rb", buffering=8 * 1024 * 1024) as stream:
        while header := stream.readline():
            sequence = stream.readline()
            if not sequence or not sequence.endswith(b"\n"):
                raise ValueError("Truncated FULL V2 FASTA")
            digest.update(header)
            digest.update(sequence)
            ordinal += 1
            if pending is None or ordinal != pending[0]:
                continue
            facts = _parse_header(header.strip(), ordinal)
            protein = sequence.rstrip(b"\r\n")
            if not protein or int(facts["LEN"]) != len(protein):
                raise ValueError("Invalid FULL candidate sequence length")
            for row in db.execute(
                "SELECT target, accession, raw_ox FROM candidate WHERE rid=?",
                (ordinal,),
            ):
                target, accession, raw_ox = row
                if (
                    facts["primary_accession"] != accession
                    or facts["OX"] != raw_ox
                    or facts["FRAG"] not in {"0", "1"}
                ):
                    raise ValueError("FULL EXACT metadata contradiction")
                if protein != targets[target]:
                    raise ValueError("Discovery EXACT conflicts with FULL sequence")
                if any(residue not in VALID_AA for residue in protein):
                    counts["invalid_alphabet_full_exact"] += 1
                    continue
                if any(residue in AMBIGUOUS_AA for residue in protein):
                    counts["literal_verified_ambiguous_exact"] += 1
                for key_type, key_value, origin in _gene_keys(
                    facts.get("GENE", ""), accession, facts.get("SEC", "")
                ):
                    if not key_value or "\t" in key_value or "\n" in key_value:
                        raise ValueError("Unsafe documentary token")
                    db.execute(
                        "INSERT OR IGNORE INTO observed VALUES (?, ?, ?, ?, ?)",
                        (target, ordinal, key_type, key_value, origin),
                    )
                db.execute(
                    "INSERT INTO verified VALUES (?, ?, ?)",
                    (target, ordinal, hashlib.sha256(protein).hexdigest()),
                )
                counts["literal_verified_exact"] += 1
                if facts["FRAG"] == "1":
                    counts["literal_verified_fragment_exact"] += 1
            pending = query.fetchone()
    if pending is not None or digest.hexdigest() != expected_sha:
        raise ValueError("FULL FASTA checksum or RID coverage mismatch")
    counts["full_records_scanned"] = ordinal


def _write_register(
    db: sqlite3.Connection,
    path: Path,
    release: str,
    counts: Counter[str],
) -> None:
    db.execute("""
        CREATE TEMP TABLE multiplicity AS
        SELECT o.target, o.kind, o.value, c.candidate_tax,
               COUNT(DISTINCT o.rid) AS count
        FROM observed o JOIN candidate c
          ON c.target=o.target AND c.rid=o.rid
        GROUP BY o.target, o.kind, o.value, c.candidate_tax
    """)
    db.execute("""
        CREATE INDEX multiplicity_key ON multiplicity
        (target, kind, value, candidate_tax)
    """)
    query = """
        SELECT c.target, c.rid, c.accession, c.target_tax,
               c.candidate_tax, c.species, c.scope, o.kind,
               o.value, o.origin, v.sha,
               m.count
        FROM observed o
        JOIN candidate c ON c.target=o.target AND c.rid=o.rid
        JOIN verified v ON v.target=o.target AND v.rid=o.rid
        JOIN multiplicity m ON m.target=o.target AND m.kind=o.kind
          AND m.value=o.value AND m.candidate_tax=c.candidate_tax
        ORDER BY c.target, c.rid, o.kind, o.value, o.origin
    """
    with path.open("x", encoding="utf-8", newline="") as stream:
        writer = csv.writer(
            stream,
            delimiter="\t",
            lineterminator="\n",
            quoting=csv.QUOTE_NONE,
        )
        writer.writerow(KEY_FIELDS)
        for row in db.execute(query):
            (
                target,
                rid,
                accession,
                target_tax,
                candidate_tax,
                species,
                scope,
                kind,
                value,
                origin,
                sha,
                multiplicity,
            ) = row
            valid_locus = kind in {"ORDERED_LOCUS", "ORF"} and not (
                is_known_nonidentifying_locus(kind, value)
            )
            if kind in {"GENE_NAME", "GENE_SYNONYM"}:
                status = "GENE_LABEL_NOT_LINEAGE_KEY"
            elif kind == "ACCESSION":
                status = "UNVERIFIED_AS_TARGET_ANCHOR"
            elif not valid_locus:
                status = "NONIDENTIFYING_TYPED_KEY"
            else:
                status = "TYPED_LOCUS_OBSERVATION_NOT_APPROVED"
            # Existing 04C never accepts keys donated by sequence candidates.
            writer.writerow(
                (
                    release,
                    target,
                    rid,
                    accession,
                    target_tax,
                    candidate_tax,
                    species,
                    scope,
                    kind,
                    value,
                    origin,
                    _hint(value),
                    int(scope == "SAME_TAXID"),
                    0,
                    multiplicity,
                    int(multiplicity > 1),
                    status,
                    sha,
                )
            )
            counts["observed_keys"] += 1
            counts[f"kind_{kind}"] += 1
            counts["exact_candidate_key_collisions"] += int(multiplicity > 1)
            counts[f"status_{status}"] += 1


def build_exact_documentary_register(
    *,
    release: str,
    discovery: Path,
    targets: Path,
    full_manifest: Path,
    output_dir: Path,
) -> dict:
    """Stage-independent observations before lineage; NO approvals or DP."""
    if not release or not output_dir.parent.is_dir():
        raise ValueError("Missing release or output parent")
    if output_dir.exists() or output_dir.is_symlink():
        raise FileExistsError("EXACT register destination occupied")
    if any(
        not path.is_file() or path.is_symlink()
        for path in (
            discovery,
            targets,
            full_manifest,
        )
    ):
        raise ValueError("Missing or symlinked register input")
    if output_dir == full_manifest.parent or full_manifest.parent in output_dir.parents:
        raise ValueError("EXACT register output nested in FULL bundle")
    manifest = validate_bundle(full_manifest.parent, deep=False)
    if manifest.stage != "01A_FULL" or manifest.release_id != release:
        raise ValueError("Wrong FULL manifest release or stage")
    sources = [
        item
        for item in manifest.outputs
        if item.path == "01A_full_v2.fasta" and item.sha256
    ]
    if len(sources) != 1:
        raise ValueError("Missing pinned FULL FASTA digest")
    fasta = full_manifest.parent / sources[0].path
    if not fasta.is_file() or fasta.is_symlink():
        raise ValueError("Unsafe FULL FASTA path")
    target_rows = _load_targets(targets)
    with tempfile.TemporaryDirectory(
        prefix="probe-exact-keys-",
        dir=output_dir.parent,
    ) as temporary:
        staging = Path(temporary)
        db = sqlite3.connect(str(staging / "keys.sqlite3"))
        try:
            db.execute(
                "CREATE TABLE seen (target TEXT, rid INTEGER, "
                "PRIMARY KEY (target, rid))"
            )
            db.execute(
                "CREATE TABLE candidate (target TEXT, rid INTEGER, "
                "accession TEXT, raw_ox TEXT, target_tax TEXT, "
                "candidate_tax TEXT, species TEXT, scope TEXT, "
                "PRIMARY KEY (target, rid))"
            )
            db.execute("CREATE INDEX candidate_rid ON candidate (rid)")
            db.execute(
                "CREATE TABLE observed (target TEXT, rid INTEGER, "
                "kind TEXT, value TEXT, origin TEXT, "
                "PRIMARY KEY (target, rid, kind, value, origin))"
            )
            db.execute(
                "CREATE TABLE verified (target TEXT, rid INTEGER, "
                "sha TEXT, PRIMARY KEY (target, rid))"
            )
            counts = _request_exact(db, discovery, release, target_rows)
            _scan_full(db, fasta, sources[0].sha256, target_rows, counts)
            _write_register(db, staging / OUTPUT, release, counts)
        finally:
            db.close()
        summary = {
            "schema": SCHEMA,
            "sequence_identity_policy": LITERAL_EXACT_POLICY,
            "literal_sequence_alphabet": "ACDEFGHIKLMNPQRSTVWYUOBJXZ",
            "ambiguous_symbols_are_wildcards": False,
            "release": release,
            "documentary_approval_decisions": 0,
            "go_donor_decisions": 0,
            "dp_jobs_authorized": 0,
            "lexical_hints_authorize_matches": False,
            "exact_keys_authorize_04c_lineage": False,
            "counts": dict(sorted(counts.items())),
            "input_sha256": {
                "discovery": _sha(discovery),
                "targets": _sha(targets),
                "full_manifest": _sha(full_manifest),
                "full_fasta": sources[0].sha256,
            },
            "output_sha256": {OUTPUT: _sha(staging / OUTPUT)},
        }
        (staging / SUMMARY).write_text(
            json.dumps(summary, sort_keys=True, indent=2) + "\n",
            encoding="utf-8",
        )
        (staging / "keys.sqlite3").unlink()
        if output_dir.exists() or output_dir.is_symlink():
            raise FileExistsError("Concurrent EXACT register publication")
        os.rename(staging, output_dir)
    return summary
