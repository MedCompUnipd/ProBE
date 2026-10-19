"""Scientific Stage 07 V2 over verified 06A/06C1 and original documents.

Reuse frozen Stage 07/Stage 2C decision kernels. No legacy Stage 05
publication is required. Evidence from new EXACT aliases is never upgraded
from discovery to an independently attested locus or direct accession.
"""

from __future__ import annotations

import csv
import json
from collections import Counter
from dataclasses import replace
from pathlib import Path

from probe.canonical_v2.alignment_v2 import (
    BOUNDED_SCHEMA,
    NO_DOCUMENTARY_WITNESS,
    POLICY_SKIP_REASONS,
    has_documentary_witness,
)
from probe.canonical_v2.bounded_dp import BACKEND, PLAN, read_plan
from probe.canonical_v2.documentary_science import (
    bind_scientific_provenance,
    merge_documentary_facts,
    read_documentary_facts,
)
from probe.canonical_v2.fragment_literal_policy import (
    promote_canonical_literal_fragments,
)
from probe.canonical_v2.release_workload import EVIDENCE
from probe.resolver import ResolverDecision, apply_accepted_multiplicity
from probe.resolver_alignments import collect_alignments
from probe.resolver_anchors import (
    _load_target_taxonomy,
    collect_direct_anchors,
)
from probe.resolver_assembly import (
    STAGE07_COLUMNS,
    ResolvedCandidate,
    validate_policy_skipped_candidate,
)
from probe.resolver_candidates import (
    CandidateProvenance,
    CandidateRecord,
    _audit,
)
from probe.resolver_lineage import collect_lineage
from probe.stage08_donors import (
    ACCEPTED_COLUMNS,
    DonorAssociation,
    read_accepted_associations,
)
from probe.stage2c_alignment_completion import FINAL_OUTPUT, POLICY_SKIP
from probe.stage2c_alignment_preview import (
    AUDIT_OUT,
    END_STORE,
    JOBS_OUT,
    START_STORE,
    UNION_OUT,
)
from probe.stage2c_resolver_variant import resolve_stage2c_variant
from probe.workflow.artifacts import (
    Artifact,
    atomic_stage_bundle,
    sha256_file,
    validate_bundle,
)

VERSION = "probe-canonical-v2-stage07-science-v2-literal-fragment"
ACCEPTED = "07_accepted_associations.tsv"
SELECTION = "07_selection_metadata.json"
COMPLETE = "07_complete_decisions.tsv"
REPORT = "07_canonical_scientific_summary.json"


def _rows(path: Path, fields: set[str]):
    if not path.is_file() or path.is_symlink():
        raise ValueError(f"Missing or symlinked resolver input: {path}")
    with path.open(encoding="utf-8", newline="") as stream:
        reader = csv.DictReader(stream, delimiter="\t")
        header = reader.fieldnames
        if header is None or len(header) != len(set(header)):
            raise ValueError(f"Malformed resolver input header: {path}")
        if not fields.issubset(header):
            raise ValueError(f"Missing resolver input columns: {path}")
        for number, row in enumerate(reader, 2):
            if None in row or any(value is None for value in row.values()):
                raise ValueError(f"Malformed resolver input row: {path}:{number}")
            yield row


def _positive(raw: str) -> int:
    if not raw.isascii() or not raw.isdecimal():
        raise ValueError("Noncanonical candidate RID")
    number = int(raw)
    if number < 1 or str(number) != raw:
        raise ValueError("Invalid candidate RID")
    return number


def _flag(raw: str) -> bool:
    if raw not in {"0", "1"}:
        raise ValueError("Invalid boolean flag")
    return raw == "1"


def _targets(path: Path) -> dict[str, tuple[str, str]]:
    result = {}
    for row in _rows(path, {"target_id", "raw_taxid", "optional_accession"}):
        target = row["target_id"]
        if not target or target in result or not row["raw_taxid"]:
            raise ValueError("Invalid Stage 01 target identity")
        result[target] = row["raw_taxid"], row["optional_accession"]
    return result


def load_v2_candidate_universe(
    *,
    targets_path: Path,
    primary_path: Path,
    audit_path: Path,
    releases: dict[str, str],
) -> tuple[dict, dict]:
    """Read release-local V2 primary/audit facts without legacy 05A2 schema."""
    if set(releases) != {"START", "END"} or len(set(releases.values())) != 2:
        raise ValueError("Invalid two-release mapping")
    target_info = _targets(targets_path)
    candidates = {}
    original_flags = {}
    required = {
        "release",
        "target_id",
        "rid",
        "candidate_primary_accession",
        "raw_ox",
        "resolved_taxid",
        "species_taxid",
        "frag",
        "target_primary_bucket",
        "discovery_channel",
        "sequence_discovered",
        "go_positive_sequence_discovered",
        "full_v2_rescue_sequence_discovered",
        "lineage_discovered",
        "lin_distinct_lineage_key_count",
        "lin_key_collision_present",
    }
    for row in _rows(primary_path, required):
        key = row["release"], row["target_id"], row["rid"]
        if (
            key[0] not in releases.values()
            or key[1] not in target_info
            or not row["candidate_primary_accession"]
            or not row["raw_ox"]
            or not row["target_primary_bucket"]
        ):
            raise ValueError(f"Invalid V2 primary identity: {key}")
        _positive(key[2])
        if key in candidates:
            raise ValueError("Duplicate canonical primary candidate")
        lineage = _flag(row["lineage_discovered"])
        raw_count = row["lin_distinct_lineage_key_count"]
        if lineage:
            count = _positive(raw_count)
            collision = _flag(row["lin_key_collision_present"])
        else:
            if raw_count not in {"", ".", "0"}:
                raise ValueError("Unexpected nonlineage key count")
            if row["lin_key_collision_present"] not in {"", ".", "0"}:
                raise ValueError("Unexpected nonlineage collision")
            count = 0
            collision = False
        original_flags[key] = (lineage, count, collision)
        target_acc = target_info[key[1]][1]
        candidate = CandidateRecord(
            release=key[0],
            target_id=key[1],
            rid=key[2],
            scope="PRIMARY",
            target_accession=target_acc,
            candidate_accession=row["candidate_primary_accession"],
            candidate_raw_ox=row["raw_ox"],
            candidate_resolved_taxid=row["resolved_taxid"] or None,
            candidate_species_taxid=row["species_taxid"] or None,
            primary_bucket=row["target_primary_bucket"],
            fragment_record=_flag(row["frag"]),
            discovery_channel=row["discovery_channel"],
            sequence_discovered=_flag(row["sequence_discovered"]),
            go_positive_sequence_discovered=_flag(
                row["go_positive_sequence_discovered"]
            ),
            full_v2_rescue_sequence_discovered=_flag(
                row["full_v2_rescue_sequence_discovered"]
            ),
            lineage_discovered=lineage,
            lineage_key_count=count,
            lineage_collision_present=collision,
            audit=None,
        )
        candidates[key] = candidate
    required_audit = {
        "release",
        "target_id",
        "rid",
        "target_raw_taxid",
        "target_optional_accession",
        "record_already_in_primary_union_for_target",
        "candidate_primary_accession",
        "raw_ox",
        "anchor_resolved_taxid",
        "audit_class",
        "audit_alignment_allowed",
        "key_source_reason",
        "direct_match_type",
        "direct_match_accession",
        "sec",
    }
    for row in _rows(audit_path, required_audit):
        key = row["release"], row["target_id"], row["rid"]
        if key[0] not in releases.values() or key[1] not in target_info:
            raise ValueError("Audit references a foreign release or target")
        original = candidates.get(key)
        record = _audit(
            row,
            str(key),
            target_info[key[1]],
            allow_overlap=original is not None,
        )
        if original is None:
            candidates[key] = record
        else:
            if (
                original.candidate_accession != record.candidate_accession
                or original.candidate_raw_ox != record.candidate_raw_ox
                or original.candidate_resolved_taxid != record.candidate_resolved_taxid
                or original.fragment_record != record.fragment_record
            ):
                raise ValueError("Audit overlaps a different V2 candidate")
            candidates[key] = replace(original, overlap_audit=record.audit)
    return candidates, original_flags


def _skip_reasons(path: Path, releases: dict[str, str]) -> dict[tuple, str]:
    found = {}
    for row in _rows(path, {"release", "target_id", "rid", "reason"}):
        key = row["release"], row["target_id"], row["rid"]
        if (
            key[0] not in releases.values()
            or row["reason"] not in POLICY_SKIP_REASONS
            or not key[1]
        ):
            raise ValueError("Invalid policy-skipped candidate")
        _positive(key[2])
        if key in found:
            raise ValueError("Duplicate policy-skipped candidate")
        found[key] = row["reason"]
    return found


def _skip_keys(path: Path, releases: dict[str, str]) -> frozenset[tuple]:
    return frozenset(_skip_reasons(path, releases))


def _resolve_documentary_gated(
    *,
    provenance: dict,
    alignments: dict,
    taxonomy: dict,
    sequences: dict,
    skip_reasons: dict[tuple, str],
    release_order: tuple[str, str],
    strain_policy: str,
    partial_sequence: str,
    documentary_fragment: str,
):
    """Retain scientifically excluded pairs without inventing DP measurements.

    The shared Stage 2C kernel continues to handle cross-TaxID exclusions.
    A canonical pair lacking prior documentary support is UNRESOLVED and
    is never passed to that kernel as a fabricated cross-TaxID exclusion.
    """
    denied = {
        key for key, reason in skip_reasons.items() if reason == NO_DOCUMENTARY_WITNESS
    }
    if not denied.issubset(provenance) or set(provenance) != set(alignments):
        raise ValueError("Documentary gate candidate coverage mismatch")
    deferred = {}
    for key in denied:
        facts = provenance[key]
        alignment = alignments[key]
        validate_policy_skipped_candidate(facts, alignment)
        context = taxonomy.get(key[:2])
        if (
            context is None
            or not context[0]
            or context[0] != facts.candidate.candidate_resolved_taxid
            or context[0] != facts.direct.target_resolved_taxid
            or facts.candidate.scope != "PRIMARY"
            or has_documentary_witness(facts.lineage)
        ):
            raise ValueError("Invalid NO_DOCUMENTARY_WITNESS exclusion")
        decision = ResolverDecision(
            source_scope="PRIMARY",
            evaluation_scope="PRIMARY",
            decision="UNRESOLVED",
            decision_basis=NO_DOCUMENTARY_WITNESS,
            entity_equivalence="NOT_ASSERTED",
            annotation_donor=False,
            audit_outcome=None,
            warnings=("NONEXACT_NO_DOCUMENTARY_DP",),
        )
        deferred[key] = ResolvedCandidate(
            candidate=facts.candidate,
            decision=decision,
            sequence_relation="NOT_ALIGNED_BY_POLICY",
            exact_sequence=None,
        )
    for key, alignment in alignments.items():
        if (
            alignment.aligned
            and alignment.exact_sequence is False
            and not has_documentary_witness(provenance[key].lineage)
        ):
            raise ValueError("NONEXACT DP lacks prior documentary witness")
    retained = set(provenance) - denied
    result = resolve_stage2c_variant(
        provenance={key: provenance[key] for key in retained},
        alignments={key: alignments[key] for key in retained},
        taxonomy=taxonomy,
        sequences=sequences,
        release_order=release_order,
        strain_policy=strain_policy,
        partial_sequence=partial_sequence,
        documentary_fragment=documentary_fragment,
    )
    combined = {**result.rows, **deferred}
    counted = apply_accepted_multiplicity(
        [(*key, item.decision) for key, item in combined.items()]
    )
    for release, target, rid, decision in counted:
        key = release, target, rid
        combined[key] = replace(combined[key], decision=decision)
    if set(combined) != set(provenance):
        raise ValueError("Documentary exclusion lost canonical candidates")
    return replace(result, rows=combined)


def _sequence_proofs(
    *,
    wanted: set[tuple],
    targets_path: Path,
    stores: dict[str, Path],
) -> dict:
    target_ids = {key[1] for key in wanted}
    targets = {}
    for row in _rows(targets_path, {"target_id", "normalized_sequence"}):
        key = row["target_id"]
        if key in target_ids:
            if key in targets or not row["normalized_sequence"]:
                raise ValueError("Duplicated or empty target sequence")
            targets[key] = row["normalized_sequence"]
    if set(targets) != target_ids:
        raise ValueError("Target sequence proof coverage incomplete")
    source_ids = {(release, rid) for release, _, rid in wanted}
    found = {}
    for release, path in stores.items():
        for row in _rows(path, {"release", "rid", "sequence"}):
            key = row["release"], row["rid"]
            if row["release"] != release:
                raise ValueError("FULL sequence store crossed release")
            if key not in source_ids:
                continue
            if key in found or not row["sequence"]:
                raise ValueError("Duplicate or empty FULL candidate sequence")
            found[key] = row["sequence"]
    if set(found) != source_ids:
        raise ValueError("FULL sequence proof coverage incomplete")
    return {key: (targets[key[1]], found[(key[0], key[2])]) for key in wanted}


def _artifact(path: Path) -> Artifact:
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"Unsafe scientific input: {path}")
    return Artifact(str(path), path.stat().st_size, sha256_file(path))


def _write_scientific_tables(staged: Path, sorted_rows: list) -> None:
    with (staged / COMPLETE).open("x", encoding="utf-8", newline="") as output:
        writer = csv.DictWriter(
            output,
            fieldnames=STAGE07_COLUMNS,
            delimiter="\t",
            lineterminator="\n",
        )
        writer.writeheader()
        for item in sorted_rows:
            writer.writerow(item.tsv_row())
    with (staged / ACCEPTED).open("x", encoding="utf-8", newline="") as output:
        writer = csv.writer(output, delimiter="\t", lineterminator="\n")
        writer.writerow(ACCEPTED_COLUMNS)
        for item in sorted_rows:
            if item.decision.decision != "ACCEPTED":
                continue
            record = item.tsv_row()
            association = DonorAssociation(
                *(record[field] for field in ACCEPTED_COLUMNS)
            )
            writer.writerow(association.tsv_values())


def publish_canonical_stage07(
    *,
    workload: Path,
    completion: Path,
    targets: Path,
    taxonomy_paths: dict[str, Path],
    anchor_paths: dict[str, Path],
    lineage_index: Path,
    lineage_paths: dict[str, Path],
    policy: str,
    destination: Path,
    partial_sequence: str = "allow",
    documentary_fragment: str = "allow",
) -> dict:
    """Publish donor decisions through the actual frozen scientific kernels."""
    from probe.canonical_v2.alignment_v2 import SUMMARY as DP_REPORT
    from probe.canonical_v2.release_workload import SCHEMA as WORKLOAD_SCHEMA
    from probe.stage2c_alignment_completion import _preview
    from probe.stage2c_alignment_completion import sha256 as input_sha

    summary, paths = _preview(workload)
    releases = summary["releases"]
    if summary.get("canonical_contract") != WORKLOAD_SCHEMA:
        raise ValueError("Not a complete verified canonical V2 workload")
    if policy not in {"strict", "same_species_exact"}:
        raise ValueError("Unknown canonical donor policy")
    if (
        set(taxonomy_paths) != set(anchor_paths)
        or set(lineage_paths) != set(releases.values())
        or set(taxonomy_paths) != set(releases.values())
    ):
        raise ValueError("Incomplete release-local taxonomy/documentary inputs")
    if destination.exists() or destination.is_symlink():
        raise FileExistsError("Stage 07 publication destination occupied")
    if not destination.parent.is_dir() or destination.parent.is_symlink():
        raise ValueError("Stage 07 output parent is missing or unsafe")
    report = json.loads((completion / DP_REPORT).read_text("utf-8"))
    expected = (completion / FINAL_OUTPUT, completion / POLICY_SKIP)
    expected_cells = None
    if report.get("schema") == BOUNDED_SCHEMA:
        if report.get("dp_backend") != BACKEND:
            raise ValueError("Unknown certified alignment backend")
        expected += (completion / PLAN,)
    elif report.get("schema") != "probe-canonical-v2-alignment-execution-v1":
        raise ValueError("Unknown canonical alignment schema")
    if report.get("releases") != releases or any(
        report.get("output_sha256", {}).get(file.name) != input_sha(file)
        for file in expected
    ):
        raise ValueError("Unverified canonical V2 DP completion")
    if report.get("schema") == BOUNDED_SCHEMA:
        expected_cells = read_plan(completion / PLAN)
        if report.get("dp_gate", {}).get("selected") != len(
            expected_cells
        ) or report.get("dp_cells") != sum(
            sum(cells) for cells in expected_cells.values()
        ):
            raise ValueError("Certified DP plan/completion metrics mismatch")
    candidates, original_flags = load_v2_candidate_universe(
        targets_path=targets,
        primary_path=paths[UNION_OUT],
        audit_path=paths[AUDIT_OUT],
        releases=releases,
    )
    original = collect_lineage(
        index_path=lineage_index,
        evidence_paths=lineage_paths,
        candidates={
            key: record.lineage_reference()
            for key, record in candidates.items()
            if record.scope == "PRIMARY"
        },
    )
    for key, (has_lineage, count, collision) in original_flags.items():
        observed = original.get(key)
        if has_lineage != (observed is not None):
            raise ValueError("Frozen 04D2 lineage discovery coverage changed")
        if observed is not None and (
            len(observed.matched_typed_keys) != count
            or observed.collision_present != collision
        ):
            raise ValueError("Frozen lineage provenance changed")
    evidence_path = workload / EVIDENCE
    if summary.get("output_sha256", {}).get(EVIDENCE) != input_sha(evidence_path):
        raise ValueError("Documentary provenance SHA256 mismatch")
    new = read_documentary_facts(
        path=evidence_path,
        releases=releases,
    )
    merged = merge_documentary_facts(
        candidates=candidates,
        independently_checked=original,
        new_evidence=new,
    )
    direct = collect_direct_anchors(
        target_taxonomy_paths=taxonomy_paths,
        classified_anchor_paths=anchor_paths,
        candidates={key: value.anchor_reference() for key, value in candidates.items()},
    )
    provenance = bind_scientific_provenance(
        candidate_facts={
            key: CandidateProvenance(record, direct[key], original.get(key))
            for key, record in candidates.items()
        },
        documentary_facts=merged,
    )
    skip_reasons = _skip_reasons(completion / POLICY_SKIP, releases)
    skipped = frozenset(skip_reasons)
    alignments = collect_alignments(
        candidates=candidates,
        jobs_path=paths[JOBS_OUT],
        alignments_path=completion / FINAL_OUTPUT,
        policy_skipped_keys=skipped,
        expected_cells=expected_cells,
    )
    contexts = _load_target_taxonomy(
        taxonomy_paths,
        {key[:2] for key in candidates},
    )
    taxonomy = {
        key: (fact.resolved_taxid, fact.species_taxid) for key, fact in contexts.items()
    }
    wanted = {
        key
        for key, row in provenance.items()
        if key not in skipped
        and row.direct.target_resolved_taxid is not None
        and row.candidate.candidate_resolved_taxid is not None
        and (
            (
                row.direct.exact_resolved_taxid
                and alignments[key].aligned
                and alignments[key].exact_sequence is True
            )
            or (
                policy == "same_species_exact"
                and not row.direct.exact_resolved_taxid
                and taxonomy[key[:2]][1] == row.candidate.candidate_species_taxid
            )
        )
    }
    proof = _sequence_proofs(
        wanted=wanted,
        targets_path=targets,
        stores={
            releases[role]: paths[START_STORE if role == "START" else END_STORE]
            for role in releases
        },
    )
    result = _resolve_documentary_gated(
        provenance=provenance,
        alignments=alignments,
        taxonomy=taxonomy,
        sequences=proof,
        skip_reasons=skip_reasons,
        release_order=(releases["START"], releases["END"]),
        strain_policy=policy,
        partial_sequence=partial_sequence,
        documentary_fragment=documentary_fragment,
    )
    # Canonical-only policy: frozen Stage 2C remains untouched.
    result = promote_canonical_literal_fragments(
        result=result,
        provenance=provenance,
        alignments=alignments,
        sequences=proof,
        documentary_fragment=documentary_fragment,
        strain_policy=policy,
        taxonomy=taxonomy,
    )
    inputs = (
        targets,
        *paths.values(),
        *expected,
        workload / EVIDENCE,
        *taxonomy_paths.values(),
        *anchor_paths.values(),
        lineage_index,
        *lineage_paths.values(),
        completion / DP_REPORT,
    )
    artifacts = tuple(_artifact(path) for path in dict.fromkeys(inputs))
    policies = {
        "resolver": {"strain_policy": policy},
        "annotation_donor": {
            "partial_sequence": partial_sequence,
            "documentary_fragment": documentary_fragment,
        },
    }
    metadata = {
        "schema_version": "stage07-selection-metadata-v1",
        "stage": "07",
        "policy_version": VERSION,
        "releases": releases,
        "effective_policies": policies,
        "stage08_contract": {
            "association_source": ACCEPTED,
            "go_donor_criterion": "annotation_donor=YES",
            "preserve_nondonors": True,
            "preserve_audit_origin": True,
        },
        "source": {"type": "canonical V2 documentary + 06C1"},
    }
    sorted_rows = sorted(
        result.rows.values(),
        key=lambda item: (
            (releases["START"], releases["END"]).index(item.key[0]),
            item.key[1],
            int(item.key[2]),
        ),
    )
    decisions = Counter(item.decision.decision for item in sorted_rows)
    donors = sum(item.decision.annotation_donor for item in sorted_rows)
    with atomic_stage_bundle(
        destination,
        stage="07",
        role=None,
        release_id=None,
        parameters={
            "start_release_id": releases["START"],
            "end_release_id": releases["END"],
            "policy_version": VERSION,
            "policies": policies,
            "canonical_v2": True,
        },
        ontology=None,
        implementation={"name": "canonical-stage07-v2", "version": VERSION},
        inputs=artifacts,
        hash_outputs=True,
    ) as staged:
        _write_scientific_tables(staged, sorted_rows)
        (staged / SELECTION).write_text(
            json.dumps(metadata, sort_keys=True, indent=2) + "\n",
            encoding="utf-8",
        )
        (staged / REPORT).write_text(
            json.dumps(
                {
                    "schema": VERSION,
                    "releases": releases,
                    "candidate_count": len(sorted_rows),
                    "decisions": dict(decisions),
                    "annotation_donor_yes": donors,
                    "cross_strain_total": result.cross_strain_total,
                    "cross_strain_donors": result.cross_strain_donors,
                    "policy_skip_reasons": dict(Counter(skip_reasons.values())),
                },
                sort_keys=True,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        if any(_artifact(Path(item.path)).sha256 != item.sha256 for item in artifacts):
            raise ValueError("Scientific input changed during Stage 07 publication")
    validate_bundle(destination, deep=True)
    index = read_accepted_associations(
        destination / ACCEPTED,
        start_release_id=releases["START"],
        end_release_id=releases["END"],
        source_database="UniProtKB",
    )
    if len(index.associations) != decisions["ACCEPTED"]:
        raise ValueError("Stage 08 accepted association count mismatch")
    return {
        "candidate_count": len(sorted_rows),
        "decisions": dict(decisions),
        "annotation_donor_yes": donors,
        "policy": policy,
    }
