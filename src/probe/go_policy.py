"""Compile the Gene Ontology normalization policy used by ProBE."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

from probe.ontology import GO_ROOTS, GeneOntology, TermStatus
from probe.snapshot import ASPECT_NAMESPACES

POLICY_VERSION = "probe-goa-normalization-policy-v1"

POLICY_FILENAME = "01B0_go_policy.tsv"
STATS_FILENAME = "01B0_go_policy.stats.json"
SCIENTIFIC_MANIFEST_FILENAME = "01B0_go_policy.manifest.json"


@dataclass(frozen=True, slots=True)
class GoPolicyResult:
    policy_path: Path
    stats_path: Path
    scientific_manifest_path: Path
    policy_row_count: int
    usable_resolution_count: int
    remapped_resolution_count: int


def sha256_file(path: Path, chunk_size: int = 8 * 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(chunk_size), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _atomic_temp_path(final: Path) -> Path:
    final.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(
        prefix=f".{final.name}.",
        suffix=".tmp",
        dir=final.parent,
    )
    os.close(fd)
    return Path(name)


def _reject_reason(status: TermStatus) -> str:
    if status is TermStatus.OBSOLETE:
        return "OBSOLETE_GO"
    if status is TermStatus.DEPRECATED:
        return "DEPRECATED_GO"
    return "UNKNOWN_GO"


def compile_go_policy(
    *,
    owl_path: Path,
    output_directory: Path,
    expected_owl_sha256: str | None = None,
    overwrite: bool = False,
) -> GoPolicyResult:
    """Compile deterministic GO-ID normalization policy artifacts."""

    owl_path = Path(owl_path)
    output_directory = Path(output_directory)

    if not owl_path.is_file():
        raise FileNotFoundError(owl_path)

    output_directory.mkdir(parents=True, exist_ok=True)

    policy_path = output_directory / POLICY_FILENAME
    stats_path = output_directory / STATS_FILENAME
    manifest_path = output_directory / SCIENTIFIC_MANIFEST_FILENAME

    finals = (policy_path, stats_path, manifest_path)

    if not overwrite:
        existing = [str(path) for path in finals if path.exists()]
        if existing:
            raise FileExistsError(
                "output(s) already exist; use overwrite only after review:\n  "
                + "\n  ".join(existing)
            )

    owl_sha256 = sha256_file(owl_path)

    if expected_owl_sha256 is not None:
        if len(expected_owl_sha256) != 64 or any(
            character not in "0123456789abcdefABCDEF"
            for character in expected_owl_sha256
        ):
            raise ValueError(
                "expected_owl_sha256 must be a 64-digit hexadecimal SHA-256"
            )

        if owl_sha256.lower() != expected_owl_sha256.lower():
            raise ValueError(
                "GO SHA-256 mismatch: "
                f"got {owl_sha256}, expected {expected_owl_sha256}"
            )

    ontology = GeneOntology.from_owl(owl_path)

    input_ids = set(ontology.terms)
    for term in ontology.terms.values():
        input_ids.update(term.alternate_ids)

    policy_tmp = _atomic_temp_path(policy_path)
    stats_tmp = _atomic_temp_path(stats_path)
    manifest_tmp = _atomic_temp_path(manifest_path)

    status_counts: Counter[str] = Counter()
    reject_counts: Counter[str] = Counter()
    usable_count = 0
    remapped_count = 0
    root_count = 0

    success = False

    try:
        with policy_tmp.open("w", encoding="utf-8", newline="\n") as out:
            out.write(
                "input_go_id\tcanonical_go_id\tresolution_status\t"
                "namespace\tis_root\treject_reason\n"
            )

            for input_go_id in sorted(input_ids):
                resolution = ontology.resolve(input_go_id)
                status = resolution.status
                status_counts[status.value] += 1

                canonical = resolution.canonical_id
                namespace = ""
                is_root = False
                rejection = ""

                if canonical is None:
                    rejection = _reject_reason(status)
                    reject_counts[rejection] += 1
                else:
                    term = ontology.term(canonical)
                    if term is None:
                        raise RuntimeError(
                            "usable resolution has no canonical term: "
                            f"{input_go_id} -> {canonical}"
                        )

                    namespace = term.namespace
                    is_root = canonical in GO_ROOTS
                    usable_count += 1

                    if canonical != input_go_id:
                        remapped_count += 1
                    if is_root:
                        root_count += 1

                out.write(
                    f"{input_go_id}\t"
                    f"{canonical or ''}\t"
                    f"{status.value}\t"
                    f"{namespace}\t"
                    f"{1 if is_root else 0}\t"
                    f"{rejection}\n"
                )

        policy_sha256 = sha256_file(policy_tmp)

        stats = {
            "stage": "01B0",
            "policy_version": POLICY_VERSION,
            "ontology_sha256": owl_sha256,
            "policy_row_count": len(input_ids),
            "usable_resolution_count": usable_count,
            "remapped_resolution_count": remapped_count,
            "root_resolution_count": root_count,
            "resolution_status_counts": dict(sorted(status_counts.items())),
            "reject_reason_counts": dict(sorted(reject_counts.items())),
        }

        with stats_tmp.open("w", encoding="utf-8", newline="\n") as out:
            json.dump(stats, out, indent=2, sort_keys=True)
            out.write("\n")

        manifest = {
            "stage": "01B0",
            "policy_version": POLICY_VERSION,
            "ontology_sha256": owl_sha256,
            "policy_sha256": policy_sha256,
            "policy_row_count": len(input_ids),
            "go_roots": sorted(GO_ROOTS),
            "aspect_namespaces": dict(sorted(ASPECT_NAMESPACES.items())),
        }

        with manifest_tmp.open("w", encoding="utf-8", newline="\n") as out:
            json.dump(manifest, out, indent=2, sort_keys=True)
            out.write("\n")

        os.replace(policy_tmp, policy_path)
        os.replace(stats_tmp, stats_path)
        os.replace(manifest_tmp, manifest_path)

        success = True

    finally:
        if not success:
            policy_tmp.unlink(missing_ok=True)
            stats_tmp.unlink(missing_ok=True)
            manifest_tmp.unlink(missing_ok=True)

    return GoPolicyResult(
        policy_path=policy_path,
        stats_path=stats_path,
        scientific_manifest_path=manifest_path,
        policy_row_count=len(input_ids),
        usable_resolution_count=usable_count,
        remapped_resolution_count=remapped_count,
    )
