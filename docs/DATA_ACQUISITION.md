# Reproducible data acquisition

`probe.data_acquisition` downloads and freezes the upstream inputs selected for
the `start` and `end` benchmark roles. It does not select releases, parse
biological metadata, or align releases by date.

## Storage and configuration

Use an explicit absolute `data_root` on a filesystem sized for the complete
inputs and extraction workspace. Approximately 1 TB or more may be appropriate;
the required capacity depends on the selected releases. Data roots inside the
Git repository are rejected.

Configuration uses TOML from the Python standard library. Both snapshot roles
and every biological release choice are mandatory. Optional `sha256` fields are
verified in addition to ProBE's always-computed local SHA-256.

```toml
data_root = "/large_disk/probe_data"
minimum_free_bytes = 1099511627776

[snapshots.start.uniprot]
release = "UPSTREAM_START_RELEASE"
mode = "archive"
swissprot_member = "optional/exact/uniprot_sprot.dat.gz"
trembl_member = "optional/exact/uniprot_trembl.dat.gz"

[snapshots.start.uniprot.archive]
url = "https://upstream.example/previous_releases/release-label/knowledgebase/archive.tar.gz"
# sha256 = "replace-with-an-upstream-or-otherwise-pinned-64-character-digest"

[snapshots.start.goa]
release = "GOA_START_LABEL"
url = "https://upstream.example/goa/archive/goa_uniprot_all.gaf.gz"

[snapshots.start.taxonomy]
snapshot_date = "TAXONOMY_START_LABEL"
url = "https://upstream.example/taxonomy/archive/new_taxdump.zip"

[snapshots.end.uniprot]
release = "UPSTREAM_END_RELEASE"
mode = "direct"

[snapshots.end.uniprot.swissprot]
url = "https://upstream.example/uniprot_sprot.dat.gz"

[snapshots.end.uniprot.trembl]
url = "https://upstream.example/uniprot_trembl.dat.gz"

[snapshots.end.goa]
release = "GOA_END_LABEL"
url = "https://upstream.example/goa/goa_uniprot_all.gaf.gz"

[snapshots.end.taxonomy]
snapshot_date = "TAXONOMY_END_LABEL"
url = "https://upstream.example/taxonomy/new_taxdump.zip"
```

An optional `[snapshots.<role>.ontology]` table accepts `release`, `url`, and
optional `sha256`. A local pinned source can be supplied with a `file://` URL.
Remote source URLs support HTTP, HTTPS, and FTP.

Archive-mode UniProt configuration may omit the two member names only when the
archive contains exactly one `*sprot*.dat.gz` and one `*trembl*.dat.gz`
candidate. Ambiguous or missing candidates are fatal; ProBE never guesses.

## Commands

Acquire both configured roles:

```console
python -m probe.data_acquisition --config acquisition.toml
```

Acquire one role or validate existing data without network writes:

```console
python -m probe.data_acquisition --config acquisition.toml --snapshot start
python -m probe.data_acquisition --config acquisition.toml --snapshot end --check
```

If an existing raw or extracted artifact conflicts with its recorded checksum,
the normal command stops. `--repair` preserves the conflicting path with a
`.conflict-<timestamp>` suffix and reacquires or re-extracts it. Completed,
checksum-valid artifacts are reused. Downloads use `.part` files and become raw
artifacts only through an atomic rename after successful streaming and checksum
verification. Small resumable acquisition-state records live below
`manifests/.state/`, not in the raw-data directories.

## Resulting layout

```text
<DATA_ROOT>/
  start/
    uniprot/{raw,extracted,derived}/
    goa/raw/
    taxonomy/{raw,extracted}/
    ontology/raw/
    indexes/
  end/
    uniprot/{raw,extracted,derived}/
    goa/raw/
    taxonomy/{raw,extracted}/
    ontology/raw/
    indexes/
  manifests/{start,end}.json
```

`raw` stores unchanged upstream bytes. `extracted` stores safe archive members.
`derived` is reserved for reproducible local products and never replaces raw
inputs. GOA remains gzip-compressed. Taxonomy extraction retains all safe ZIP
members and verifies the presence of `nodes.dmp`, `names.dmp`, `merged.dmp`, and
`delnodes.dmp`.

Each deterministic JSON manifest records role, requested labels, URLs, relative
local paths, retrieval timestamp, sizes, computed checksums, compression,
archive-member names, extraction relationships, warnings, and tool version.
`--check` recomputes local checksums and validates required extraction members.

Combined UniProt FASTA generation is currently deferred. No `.dat`-to-FASTA
implementation exists in the active repository, so this acquisition layer only
prepares and records the required Swiss-Prot and TrEMBL `.dat.gz` inputs.
