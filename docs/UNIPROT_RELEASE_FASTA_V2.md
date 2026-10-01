# UniProt release FASTA v2

`probe-release-fasta-v2` is the streaming, native preprocessing converter for
authoritative UniProt `.dat` and `.dat.gz` sources. Inputs are processed in the
given order; record IDs (`RID`) are continuous across sources and records remain
in source order. It writes a two-line enriched FASTA, an issues TSV, and summary
JSON under the supplied output prefix. Each completed artifact is installed by
an independent atomic rename; the three-file set is not a single transaction.

Build through Pixi:

```bash
pixi run build-release-fasta
```

Run it with one or more ordered inputs:

```bash
pixi run release-fasta-v2 -- \
  --input path/to/uniprot_sprot.dat.gz \
  --input path/to/uniprot_trembl.dat.gz \
  --output-prefix output/uniprot/derived/combined-v2 \
  --release-id START-2023-01
```

Format v2 retains raw OX TaxIDs without taxonomy resolution. `FRAG=1` only for
a real `DE   Flags:` field containing `Fragment` or `Fragments`; it is not a
containment or target-admission decision. GN metadata retains group boundaries
using compact `N:`, `S:`, `L:`, and `O:` fields, with groups separated by `|`.

The validated experimental full START conversion produced byte-identical Python
and C++ FASTA/issues artifacts. The v2 FASTA SHA-256 was
`17bf4b0e9d99812abbbb400e6443c28f85051b505bdcb991846f2d76ed3d73bb`.
Those large-run values are validation evidence only; CI uses small fixtures.
