"""Project a lossless B1 ledger into one primary candidate fact table.

The result is deliberately NOT an alignment-ready 05A2 publication: RID-based
FULL metadata, literal sequences, and the independent 05C2 audit are pending.
No donor, locus admissibility, or dynamic-programming decision is made here.
"""

from __future__ import annotations

import csv
import hashlib
import json
import os
import tempfile
from collections import Counter
from collections.abc import Iterator
from pathlib import Path

from probe.canonical_v2.contracts import (
    OUTPUT_FIELDS as LEDGER_FIELDS,
)
from probe.canonical_v2.contracts import (
    OUTPUT_NAME as LEDGER_NAME,
)
from probe.canonical_v2.contracts import (
    SCHEMA as LEDGER_SCHEMA,
)
from probe.canonical_v2.contracts import (
    SUMMARY_NAME as LEDGER_SUMMARY,
)
from probe.stage2c_evidence_bridge import GO_SEQ

SCHEMA = "probe-canonical-v2-primary-facts-v1"
OUTPUT_NAME = "primary_candidate_facts.tsv"
SUMMARY_NAME = "primary_candidate_facts.summary.json"

# Order follows Stage 05A source keys. The original 04E row remains in JSON.
LINEAGE_FIELDS = (
    "lineage_evidence_row_count",
    "distinct_lineage_key_count",
    "lineage_fact_signature",
    "record_match_roles",
    "source_roles",
    "target_accession_match",
    "from_start_anchor",
    "from_end_anchor",
    "has_accession_primary_match",
    "has_accession_secondary_match",
    "has_ordered_locus_match",
    "has_orf_match",
    "key_collision_present",
    "collision_key_count",
    "max_key_target_multiplicity",
    "matched_typed_keys",
    "target_accession_keys",
    "accession_primary_keys",
    "accession_secondary_keys",
    "ordered_locus_keys",
    "orf_keys",
    "collision_keys",
)
META_FIELDS = (
    "entry",
    "sequence_length",
    "pe",
    "sv",
    "sec",
    "gene",
)
SOURCE_NAMES = ("go", "full", "lineage", "rescue")
OUTPUT_FIELDS = (
    "release",
    "target_id",
    "rid",
    "discovery_channel",
    "sequence_discovered",
    "go_positive_sequence_discovered",
    "full_v2_rescue_sequence_discovered",
    "lineage_discovered",
    "full_exact_species_wide",
    "sequence_search_record_number",
    "full_v2_record_number",
    "full_ordinal_state",
    "lineage_full_record_number",
    "rescue_full_record_number",
    "full_exact_record_number",
    "candidate_primary_accession",
    "raw_ox",
    "resolved_taxid",
    "species_taxid",
    "frag",
    "target_primary_bucket",
    *META_FIELDS,
    *(f"seq_{field}" for field in GO_SEQ),
    *(f"lin_{field}" for field in LINEAGE_FIELDS),
    "metadata_source",
    "sequence_evidence_state",
    "requires_full_sequence_materialization",
    "strict_gate",
    "same_species_exact_gate",
    "go_source_json",
    "full_source_json",
    "lineage_source_json",
    "rescue_source_json",
)


def _digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def _rows(path: Path) -> Iterator[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as stream:
        reader = csv.DictReader(stream, delimiter="\t")
        fields = reader.fieldnames
        if fields is None or len(fields) != len(set(fields)):
            raise ValueError("Malformed candidate ledger header")
        if set(fields) != set(LEDGER_FIELDS):
            raise ValueError("Unexpected candidate ledger columns")
        for line, row in enumerate(reader, start=2):
            if None in row or any(value is None for value in row.values()):
                raise ValueError(f"Malformed ledger row at line {line}")
            yield row


def _positive(value: str) -> int:
    if not value.isascii() or not value.isdecimal():
        raise ValueError(f"Invalid positive ordinal: {value!r}")
    number = int(value)
    if number < 1 or str(number) != value:
        raise ValueError(f"Noncanonical positive ordinal: {value!r}")
    return number


def _sources(row: dict[str, str]) -> dict[str, dict[str, str]]:
    result: dict[str, dict[str, str]] = {}
    for name, flag in (
        ("go", "go_positive_species_wide"),
        ("full", "full_exact_species_wide"),
        ("lineage", "lineage_discovered"),
        ("rescue", "full_v2_rescue_discovered"),
    ):
        enabled = row[flag]
        if enabled not in {"0", "1"}:
            raise ValueError(f"Invalid source flag: {flag}")
        raw = row[f"{name}_source_json"]
        if (enabled == "1") != bool(raw):
            raise ValueError(f"Flag/payload disagreement: {name}")
        if not raw:
            continue
        source = json.loads(raw)
        if not isinstance(source, dict) or not all(
            isinstance(value, str) for value in source.values()
        ):
            raise ValueError(f"Invalid {name} source payload")
        if (
            source.get("target_id") != row["target_id"]
            or source.get("rid") != row["rid"]
        ):
            raise ValueError(f"Source key disagreement: {name}")
        if "release" in source and source["release"] != row["release"]:
            raise ValueError(f"Source release disagreement: {name}")
        accession_field = (
            "candidate_primary_accession" if name == "lineage" else "accession"
        )
        if source.get(accession_field) != row["candidate_accession"]:
            raise ValueError(f"Source accession disagreement: {name}")
        if source.get("raw_ox") != row["candidate_raw_ox"]:
            raise ValueError(f"Source OX disagreement: {name}")
        tax_field = "candidate_resolved_taxid" if name == "full" else "resolved_taxid"
        if source.get(tax_field) != row["candidate_resolved_taxid"]:
            raise ValueError(f"Source candidate TaxID disagreement: {name}")
        if source.get("frag") != row["frag"]:
            raise ValueError(f"Source fragment flag disagreement: {name}")
        if source.get("species_taxid") != row["species_taxid"]:
            raise ValueError(f"Source species disagreement: {name}")
        result[name] = source
    if not result:
        raise ValueError("Candidate with no provenance")
    go = result.get("go")
    if go is not None and (
        go.get("exact_sequence") != row["go_positive_exact"]
        or go.get("record_number") != row["go_positive_record_number"]
    ):
        raise ValueError("GO scanner exactness/ordinal disagreement")
    full = result.get("full")
    if full is not None and (
        full.get("exact_full_sequence") != "1"
        or full.get("discovery_channel") != "FULL_V2_EXACT"
        or full.get("record_number") != row["full_record_number"]
    ):
        raise ValueError("FULL scanner evidence/ordinal disagreement")
    return result


def _metadata(
    sources: dict[str, dict[str, str]],
) -> tuple[dict[str, str], str, str]:
    """Choose real observed metadata; never synthesize missing FULL fields."""
    views: dict[str, dict[str, str]] = {}
    for name in ("go", "rescue", "lineage"):
        source = sources.get(name)
        if source is None:
            continue
        length_field = "sequence_length" if name == "lineage" else "candidate_length"
        views[name] = {
            "entry": source["entry"],
            "sequence_length": source[length_field],
            "pe": source["pe"],
            "sv": source["sv"],
            "sec": source["sec"],
            "gene": source["gene"],
            "target_primary_bucket": source[
                "target_primary_bucket" if name == "lineage" else "target_bucket"
            ],
        }
    if not views:
        full = sources["full"]
        return (
            {
                "entry": "",
                "sequence_length": full["length_aa"],
                "pe": "",
                "sv": "",
                "sec": "",
                "gene": "",
                "target_primary_bucket": "",
            },
            "FULL_ORDINAL_ONLY",
            "FULL_EXACT_UNMATERIALIZED",
        )
    # A reference bucket can legitimately differ between scanner policies.
    # Only intrinsic UniProt record metadata must agree across channels.
    for field in META_FIELDS:
        observed = {record[field] for record in views.values()}
        if len(observed) != 1:
            raise ValueError(f"Conflicting source metadata: {field}")
    if (
        "full" in sources
        and sources["full"]["length_aa"]
        != next(iter(views.values()))["sequence_length"]
    ):
        raise ValueError("FULL exact length contradicts source metadata")
    choice = next(name for name in ("go", "rescue", "lineage") if name in views)
    return views[choice], choice.upper(), "SCANNER_OR_LINEAGE_FACTS"


def _project(row: dict[str, str]) -> dict[str, str]:
    if not row["release"] or not row["target_id"]:
        raise ValueError("Missing candidate key")
    _positive(row["rid"])
    if not row["candidate_accession"] or row["frag"] not in {"0", "1"}:
        raise ValueError("Missing accession or invalid fragment flag")
    sources = _sources(row)
    names = set(sources)
    if row["frag"] == "1" and (
        ("go" in names or "rescue" in names)
        or ("full" in names and sources["full"]["exact_full_sequence"] != "1")
        or ("full" not in names and names != {"lineage"})
    ):
        raise ValueError("Fragment requires FULL literal or lineage evidence")
    metadata, source_name, state = _metadata(sources)
    sequence = bool(names & {"go", "full", "rescue"})
    lineage = "lineage" in names
    if not sequence and not lineage:
        raise ValueError("Candidate without primary evidence")
    if sequence and lineage:
        channel = "BOTH"
    elif sequence:
        channel = "SEQUENCE_ONLY"
    else:
        channel = "LINEAGE_ONLY"
    evidence = sources.get("go") or sources.get("rescue")
    seq = {f"seq_{name}": "" for name in GO_SEQ}
    if evidence:
        seq.update({f"seq_{name}": evidence[name] for name in GO_SEQ})
        if (
            "go" in sources
            and "rescue" in sources
            and (sources["go"]["exact_sequence"] != sources["rescue"]["exact_sequence"])
        ):
            raise ValueError("Conflicting GO and rescue exactness")
    elif "full" in sources:
        if sources["full"]["exact_full_sequence"] != "1":
            raise ValueError("FULL exact evidence is not EXACT")
        seq["seq_exact_sequence"] = "1"
    if "full" in sources and seq["seq_exact_sequence"] != "1":
        raise ValueError("FULL exact conflicts with scanner exactness")
    lineage_row = sources.get("lineage", {})
    lin = {f"lin_{name}": lineage_row.get(name, "") for name in LINEAGE_FIELDS}
    ordinal_fields = {
        name: sources.get(name, {}).get("record_number", "")
        for name in ("lineage", "rescue", "full")
    }
    ordinals = {value for value in ordinal_fields.values() if value}
    for ordinal in ordinals:
        _positive(ordinal)
    # Different producers may record different FULL ordinals. Never collapse
    # this ambiguity silently or discard the underlying candidate pair.
    ordinal = next(iter(ordinals)) if len(ordinals) == 1 else ""
    ordinal_state = (
        "CONFLICT" if len(ordinals) > 1 else "CONSISTENT" if ordinals else "ABSENT"
    )
    if "rescue" in sources and (sources["rescue"]["record_number"] != row["rid"]):
        raise ValueError("Rescue ordinal disagrees with RID")
    return {
        "release": row["release"],
        "target_id": row["target_id"],
        "rid": row["rid"],
        "discovery_channel": channel,
        "sequence_discovered": str(int(sequence)),
        "go_positive_sequence_discovered": str(int("go" in sources)),
        "full_v2_rescue_sequence_discovered": str(int("rescue" in sources)),
        "lineage_discovered": str(int(lineage)),
        "full_exact_species_wide": str(int("full" in sources)),
        "sequence_search_record_number": (
            sources["go"]["record_number"] if "go" in sources else ""
        ),
        "full_v2_record_number": ordinal,
        "full_ordinal_state": ordinal_state,
        "lineage_full_record_number": ordinal_fields["lineage"],
        "rescue_full_record_number": ordinal_fields["rescue"],
        "full_exact_record_number": ordinal_fields["full"],
        "candidate_primary_accession": row["candidate_accession"],
        "raw_ox": row["candidate_raw_ox"],
        "resolved_taxid": row["candidate_resolved_taxid"],
        "species_taxid": row["species_taxid"],
        "frag": row["frag"],
        **metadata,
        **seq,
        **lin,
        "metadata_source": source_name,
        "sequence_evidence_state": state,
        "requires_full_sequence_materialization": "1",
        "strict_gate": row["strict_gate"],
        "same_species_exact_gate": row["same_species_exact_gate"],
        **{f"{name}_source_json": row[f"{name}_source_json"] for name in SOURCE_NAMES},
    }


def project_primary_candidate_facts(
    *, release: str, ledger_dir: Path, output_dir: Path
) -> dict:
    """Write a fact-only primary union; does not prepare 06A jobs or donors."""
    if not release or not ledger_dir.is_dir() or not output_dir.parent.is_dir():
        raise ValueError("Missing release, ledger, or output parent")
    if output_dir.exists() or output_dir.is_symlink():
        raise FileExistsError("Primary publication destination exists")
    ledger = ledger_dir / LEDGER_NAME
    summary_file = ledger_dir / LEDGER_SUMMARY
    if any(
        not path.is_file() or path.is_symlink()
        for path in (
            ledger,
            summary_file,
        )
    ):
        raise ValueError("Missing or unsafe candidate ledger publication")
    if output_dir == ledger_dir or output_dir in ledger_dir.parents:
        raise ValueError("Output directory contains an input directory")
    if ledger_dir == output_dir or ledger_dir in output_dir.parents:
        raise ValueError("Input directory contains output destination")
    source_summary = json.loads(summary_file.read_text(encoding="utf-8"))
    if (
        source_summary.get("schema") != LEDGER_SCHEMA
        or source_summary.get("release") != release
        or source_summary.get("output_sha256", {}).get(LEDGER_NAME) != _digest(ledger)
    ):
        raise ValueError("B1 ledger checksum or schema mismatch")
    counts: Counter[str] = Counter()
    with tempfile.TemporaryDirectory(
        prefix="probe-canonical-b2c-", dir=output_dir.parent
    ) as temporary:
        staged = Path(temporary)
        with (staged / OUTPUT_NAME).open("x", encoding="utf-8", newline="") as stream:
            writer = csv.DictWriter(
                stream,
                fieldnames=OUTPUT_FIELDS,
                delimiter="\t",
                lineterminator="\n",
                extrasaction="raise",
            )
            writer.writeheader()
            last_key: tuple[str, int] | None = None
            for row in _rows(ledger):
                if row["release"] != release:
                    raise ValueError("Ledger release mismatch")
                key = row["target_id"], _positive(row["rid"])
                if last_key is not None and key <= last_key:
                    raise ValueError("Ledger is not sorted and unique")
                last_key = key
                projected = _project(row)
                writer.writerow(projected)
                counts["unique_pairs"] += 1
                counts[projected["discovery_channel"]] += 1
                counts["full_metadata_pending"] += (
                    projected["metadata_source"] == "FULL_ORDINAL_ONLY"
                )
                counts["full_ordinal_conflicts"] += (
                    projected["full_ordinal_state"] == "CONFLICT"
                )
                counts["lineage_pairs"] += projected["lineage_discovered"] == "1"
                counts["rescue_pairs"] += (
                    projected["full_v2_rescue_sequence_discovered"] == "1"
                )
                counts["full_exact_pairs"] += (
                    projected["full_exact_species_wide"] == "1"
                )
        original_counts = source_summary.get("counts", {})
        if counts["unique_pairs"] != original_counts.get("unique_pairs", 0):
            raise ValueError("B1 ledger summary count mismatch")
        for projected_count, b1_count in (
            ("lineage_pairs", "lineage_discovered"),
            ("rescue_pairs", "full_v2_rescue_discovered"),
            ("full_exact_pairs", "full_exact_species_wide"),
        ):
            if counts[projected_count] != original_counts.get(b1_count, 0):
                raise ValueError("B1 ledger source count mismatch")
        summary = {
            "schema": SCHEMA,
            "release": release,
            "ready_for_06a": False,
            "donor_decisions": 0,
            "dp_decisions": 0,
            "audit_promotions": 0,
            "counts": dict(sorted(counts.items())),
            "input_sha256": {
                LEDGER_NAME: _digest(ledger),
                LEDGER_SUMMARY: _digest(summary_file),
            },
            "output_sha256": {OUTPUT_NAME: _digest(staged / OUTPUT_NAME)},
        }
        with (staged / SUMMARY_NAME).open(
            "x", encoding="utf-8", newline="\n"
        ) as stream:
            json.dump(summary, stream, indent=2, sort_keys=True)
            stream.write("\n")
        if output_dir.exists() or output_dir.is_symlink():
            raise FileExistsError("Primary publication created concurrently")
        os.rename(staged, output_dir)
    return summary
