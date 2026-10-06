#!/usr/bin/env python3
"""
Build a fresh authoritative NCBI taxonomy SQLite index and resolve canonical
target TaxIDs plus the distinct raw UniProt OX values extracted upstream.

All taxonomy semantics come from probe.taxonomy.NcbiTaxonomyIndex.
"""

from __future__ import annotations

import argparse
import json
import os
import tempfile
from collections import Counter
from pathlib import Path

from probe.taxonomy import NcbiTaxonomyIndex


def atomic_tmp(final: Path) -> Path:
    final.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(
        prefix=f".{final.name}.",
        suffix=".tmp",
        dir=final.parent,
    )
    os.close(fd)
    p = Path(name)
    p.unlink()
    return p


def context(index: NcbiTaxonomyIndex, raw_taxid: str) -> dict[str, str]:
    anchor = index.species_anchor(raw_taxid)
    res = anchor.resolution
    resolved = res.resolved_taxid or ""
    node = index.node(resolved) if resolved else None
    species = anchor.species_anchor.taxid if anchor.species_anchor is not None else ""

    return {
        "raw_taxid": raw_taxid,
        "resolution_status": res.status.value,
        "resolved_taxid": resolved,
        "resolved_rank": node.rank if node is not None else "",
        "merge_path": ",".join(res.merge_path),
        "lineage_status": anchor.lineage.status.value,
        "species_status": anchor.status.value,
        "species_taxid": species,
        "species_bucket": f"S:{species}" if species else "",
        "resolved_bucket": f"T:{resolved}" if resolved else "",
        "primary_target_bucket": (
            f"S:{species}"
            if resolved and species and resolved == species
            else (f"T:{resolved}" if resolved and species else "")
        ),
    }


def load_targets(path: Path) -> list[tuple[str, str]]:
    with path.open("r", encoding="utf-8") as handle:
        header = handle.readline().rstrip("\n\r").split("\t")
        try:
            i_id = header.index("target_id")
            i_tax = header.index("raw_taxid")
        except ValueError as e:
            raise ValueError("target TSV must contain target_id and raw_taxid") from e

        rows: list[tuple[str, str]] = []
        seen: set[str] = set()
        for line_no, line in enumerate(handle, start=2):
            if not line.strip():
                continue
            f = line.rstrip("\n\r").split("\t")
            if len(f) <= max(i_id, i_tax):
                raise ValueError(f"malformed target row at line {line_no}")
            tid, tax = f[i_id], f[i_tax]
            if tid in seen:
                raise ValueError(f"duplicate target_id: {tid}")
            seen.add(tid)
            rows.append((tid, tax))
        return rows


def load_ox(path: Path) -> list[str]:
    out: list[str] = []
    previous: str | None = None
    with path.open("r", encoding="utf-8") as handle:
        for line_no, line in enumerate(handle, start=1):
            ox = line.rstrip("\n\r")
            if not ox:
                raise ValueError(f"blank OX at line {line_no}")
            if previous is not None and ox <= previous:
                raise ValueError("OX list must be strictly sorted and unique")
            out.append(ox)
            previous = ox
    return out


def write_target_contexts(path: Path, rows: list[tuple[str, dict[str, str]]]) -> None:
    cols = [
        "target_id", "raw_taxid", "resolution_status", "resolved_taxid",
        "resolved_rank", "merge_path", "lineage_status", "species_status",
        "species_taxid", "primary_bucket",
    ]
    with path.open("w", encoding="utf-8", newline="\n") as out:
        out.write("\t".join(cols) + "\n")
        for tid, c in rows:
            values = [
                tid, c["raw_taxid"], c["resolution_status"], c["resolved_taxid"],
                c["resolved_rank"], c["merge_path"], c["lineage_status"],
                c["species_status"], c["species_taxid"], c["primary_target_bucket"],
            ]
            out.write("\t".join(values) + "\n")


def write_ox_contexts(path: Path, rows: list[dict[str, str]]) -> None:
    cols = [
        "raw_ox", "resolution_status", "resolved_taxid", "resolved_rank",
        "merge_path", "lineage_status", "species_status", "species_taxid",
        "species_bucket", "resolved_bucket",
    ]
    with path.open("w", encoding="utf-8", newline="\n") as out:
        out.write("\t".join(cols) + "\n")
        for c in rows:
            values = [
                c["raw_taxid"], c["resolution_status"], c["resolved_taxid"],
                c["resolved_rank"], c["merge_path"], c["lineage_status"],
                c["species_status"], c["species_taxid"], c["species_bucket"],
                c["resolved_bucket"],
            ]
            out.write("\t".join(values) + "\n")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--taxon-dir", type=Path)
    ap.add_argument("--nodes", type=Path)
    ap.add_argument("--names", type=Path)
    ap.add_argument("--merged", type=Path)
    ap.add_argument("--delnodes", type=Path)
    ap.add_argument("--snapshot-id", required=True)
    ap.add_argument("--index", type=Path, required=True)
    ap.add_argument("--targets", type=Path, required=True)
    ap.add_argument("--ox-list", type=Path, required=True)
    ap.add_argument("--target-output", type=Path, required=True)
    ap.add_argument("--ox-output", type=Path, required=True)
    ap.add_argument("--stats", type=Path, required=True)
    args = ap.parse_args()

    explicit_taxonomy = (
        args.nodes,
        args.names,
        args.merged,
        args.delnodes,
    )

    if args.taxon_dir is not None:
        if any(path is not None for path in explicit_taxonomy):
            raise ValueError(
                "--taxon-dir cannot be combined with explicit taxonomy files"
            )
        nodes = args.taxon_dir / "nodes.dmp"
        names = args.taxon_dir / "names.dmp"
        merged = args.taxon_dir / "merged.dmp"
        delnodes = args.taxon_dir / "delnodes.dmp"
    else:
        if any(path is None for path in explicit_taxonomy):
            raise ValueError(
                "provide either --taxon-dir or all of "
                "--nodes --names --merged --delnodes"
            )
        nodes, names, merged, delnodes = explicit_taxonomy

    for source in (nodes, names, merged, delnodes):
        if source is None or not source.is_file():
            raise FileNotFoundError(source)

    targets = load_targets(args.targets)
    ox_values = load_ox(args.ox_list)

    for p in (args.index, args.target_output, args.ox_output, args.stats):
        if p.exists():
            raise FileExistsError(f"refusing to overwrite existing output: {p}")
        p.parent.mkdir(parents=True, exist_ok=True)

    index_tmp = atomic_tmp(args.index)
    target_tmp = atomic_tmp(args.target_output)
    ox_tmp = atomic_tmp(args.ox_output)
    stats_tmp = atomic_tmp(args.stats)

    idx = NcbiTaxonomyIndex.build(
        index_tmp,
        nodes=nodes,
        names=names,
        merged=merged,
        delnodes=delnodes,
        snapshot_id=args.snapshot_id,
    )

    try:
        cache: dict[str, dict[str, str]] = {}

        def get(raw: str) -> dict[str, str]:
            if raw not in cache:
                cache[raw] = context(idx, raw)
            return cache[raw]

        target_rows = [(tid, get(raw)) for tid, raw in targets]
        ox_rows = [get(raw) for raw in ox_values]

        write_target_contexts(target_tmp, target_rows)
        write_ox_contexts(ox_tmp, ox_rows)

        target_resolution = Counter(c["resolution_status"] for _, c in target_rows)
        target_species = Counter(c["species_status"] for _, c in target_rows)
        target_bucket_kind = Counter(
            ("S" if c["primary_target_bucket"].startswith("S:") else
             "T" if c["primary_target_bucket"].startswith("T:") else "UNRESOLVED")
            for _, c in target_rows
        )
        ox_resolution = Counter(c["resolution_status"] for c in ox_rows)
        ox_species = Counter(c["species_status"] for c in ox_rows)

        stats = {
            "stage": "02B",
            "snapshot_id": args.snapshot_id,
            "target_count": len(target_rows),
            "distinct_raw_ox_count": len(ox_rows),
            "target_resolution_status_counts": dict(sorted(target_resolution.items())),
            "target_species_status_counts": dict(sorted(target_species.items())),
            "target_primary_bucket_kind_counts": dict(
                sorted(target_bucket_kind.items())
            ),
            "ox_resolution_status_counts": dict(sorted(ox_resolution.items())),
            "ox_species_status_counts": dict(sorted(ox_species.items())),
            "taxonomy_provenance": {
                "snapshot_id": idx.provenance.snapshot_id,
                "sources": [
                    {"member": s.member, "path": s.path, "sha256": s.sha256}
                    for s in idx.provenance.sources
                ],
            },
        }
        with stats_tmp.open("w", encoding="utf-8", newline="\n") as out:
            json.dump(stats, out, indent=2, sort_keys=True)
            out.write("\n")
    finally:
        idx.close()

    os.replace(index_tmp, args.index)
    os.replace(target_tmp, args.target_output)
    os.replace(ox_tmp, args.ox_output)
    os.replace(stats_tmp, args.stats)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
