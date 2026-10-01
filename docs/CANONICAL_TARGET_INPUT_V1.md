# Canonical external target input v1

## Scope

This is ProBE's frozen core contract for externally supplied benchmark targets.
It validates input for later strict exact UniProtKB sequence matching; it does
not identify proteins, resolve taxonomy, repair sequences, or perform
similarity search. Source-specific adapters, including CAFA-format adapters,
must produce this contract before invoking the core preparer.

## Coordinated inputs

The input is exactly two files with a one-to-one `target_id` relationship.

- Canonical FASTA records occupy exactly two physical lines: `>target_id` and
  one sequence line. The header contains no description or whitespace;
  `target_id` is non-empty and unique.
- Metadata is tab-separated with exactly this header and order:
  `target_id`, `ncbi_taxid`, `uniprot_accession`. All columns are mandatory.
  The accession value may be empty, but it is audit/provenance evidence only;
  it never establishes protein identity or supplies a TaxID.

FASTA-only IDs, metadata-only IDs, duplicate IDs, missing TaxIDs, and invalid
FASTA layout are retained in deterministic rejected/audit output. No target is
automatically deduplicated: equal sequences with different TaxIDs, or equal
sequences and TaxIDs with different target IDs, remain separate records.

## `protein-sequence-v1`

Accepted sequences are one continuous, ASCII uppercase literal string using
only `ACDEFGHIKLMNPQRSTVWYBJOUXZ`. `B`, `J`, `X`, and `Z` are retained
literally and are never wildcards. `U` and `O` are also literal residues.
Exact matching later requires the same literal symbols; no character is
substituted with `X` and no missing residue is inferred.

The strict core FASTA boundary does not repair layout, whitespace, case, gaps,
or punctuation. Symbols such as `*`, `-`, `.`, `?`, and `_`, including a
terminal `*`, are rejected. Their audit rows have severity `ALERT`, identify
the target and source line, list offending symbols, and list one-based
`symbol@position` coordinates. The reason states that the supplied form cannot
be verified by strict exact UniProtKB identity matching; ProBE never guesses a
replacement.

## Prepared records

Each accepted record writes `target_id`, raw TaxID, optional accession,
normalized sequence, sequence length, ASCII SHA-256, and normalization policy
version `protein-sequence-v1`. The sequence is already canonical, so its
normalized value is its literal submitted value. SHA-256 plus length is an
efficient exact-identity component only; later matching must retain literal
sequence equality as the final guard.

The production UniProt release FASTA v2 converter similarly emits each valid
record as one uppercase, whitespace-free sequence line. Its fragment and
metadata policies are independent of this target-input contract and are not
changed here.
