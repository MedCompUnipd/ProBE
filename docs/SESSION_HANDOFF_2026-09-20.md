# ProBE session history and handoff — 2026-09-20

## Purpose

This file records the logical history of the Codex session performed in the
shared remote working copy. It is the handoff source for another desktop. It is
not a substitute for `AGENTS.md` or the scientific specifications; those remain
authoritative together with the later explicit decisions recorded here.

The repository is a single server-side working copy used over SSH. There are no
independent desktop clones to synchronize. Do not reset, restore, clean, or
overwrite the current uncommitted work when resuming from another workstation.

## Chronological history

### 1. Initial read-only inspection

The user first requested only `pwd`, `git status`, the current branch, and the
last five commits. The branch was `codex/modifiche-main`, aligned with its
remote, with an already dirty working tree containing the ontology-preflight
implementation and documentation changes.

No files were changed during that inspection.

### 2. Project reconstruction

The user requested a full reading of:

- `docs/BENCHMARK_IMPLEMENTATION_GAP_ANALYSIS.md`;
- `docs/BENCHMARK_SELECTION_AND_METRIC_SPEC.md`;
- `docs/ONTOLOGY_PREFLIGHT_PROVISIONAL.md`;
- `docs/SCIENTIFIC_DECISIONS.md`;
- `docs/TRUTH_MASK_IMPLEMENTATION_PLAN.md`.

The code, recent commits, tests, and documentation were compared. The main
conclusions were:

- the direct canonical event milestone was implemented;
- a substantial, uncommitted ontology-preflight milestone was present;
- truth/mask construction (`Q`, `K0`, `X1`, `M`, `T`) was not implemented;
- prediction ingestion, information accretion, metrics, and export remained
  future work;
- the working tree had to be preserved.

At that point 43 tests passed in the local Pixi environment. No project file was
changed during the reconstruction.

### 3. Ontology policy changed from go-plus.owl to go.owl

The user explicitly decided that `go.owl` contains the information required by
ProBE and that `go-plus.owl` is unsuitable for node navigation.

This decision supersedes every earlier requirement to use `go-plus.owl`:

- `go.owl` is the master ontology for normalization, navigation, propagation,
  benchmark construction, and future evaluation;
- the exact local file must still be immutable and pinned by SHA-256;
- `go-plus.owl` must not be used for the navigation or scoring graph;
- `NON_GO_PLUS_ONTOLOGY` is obsolete;
- the conservative propagation relations remain `is_a` and `part_of`.

The decision was applied to `AGENTS.md`, scientific specifications, the
preflight policy, tests, README, and the development ledger.

### 4. Requirement 1 — stand-alone release preprocessing

The user requested a stand-alone tool taking `go.owl`, one release GOA, and the
matching UniProt FASTA.

Implemented in `src/probe/preprocessing.py`:

- validate GO IDs against `go.owl`;
- canonicalize alternate IDs and unique active `replaced_by` chains;
- remove unknown and unresolved obsolete/deprecated terms;
- remove aspect-incompatible GOA rows;
- remove accessions whose only valid annotations are GO roots;
- retain root rows when the same accession also has a non-root annotation;
- preserve all GAF evidence codes, qualifiers, and remaining columns;
- create `Uniprot_filtered.fasta` with only retained GOA accessions;
- write a deterministic debug TSV for retained GOA accessions absent from the
  FASTA;
- stream the GOA in two passes and stream the FASTA;
- support plain and gzip-compressed inputs;
- reject output paths that overwrite an input.

The command is:

```bash
python -m probe.preprocessing GO_OWL GOA UNIPROT_FASTA \
  --goa-output GOA_filtered.gaf \
  --fasta-output Uniprot_filtered.fasta \
  --debug-log preprocess_debug.tsv
```

Synthetic tests are in `tests/test_preprocessing.py`. No real release was
processed.

### 5. Requirement 2 — main t0 versus t1 pipeline

The user requested code that:

1. reads target proteins in FASTA;
2. finds exact UniProt matches and GOA assertions at t0;
3. repeats the operation independently at t1;
4. compares t0 and t1 to obtain the benchmark ground truth.

Implemented in `src/probe/pipeline.py`:

- typed `OntologyInput` and `ReleaseInput` records;
- checksum-verified loading and preflight of one `go.owl` context;
- independent exact-sequence indexing for t0 and t1;
- release-specific aliases retained in separate `IdentityMap` objects;
- deterministic merge by target ID and exact sequence ID;
- release-filtered `AnnotationSnapshot` objects;
- comparison through the existing canonical direct-event layer;
- explicit evidence policy, defaulting to `experimental_strict` only when none
  is supplied;
- `DirectGroundTruthCandidate` records preserving old/new evidence, contexts,
  normalization provenance, and source assertions.

The same accession with a different sequence in t0 and t1 is interpreted
release-specifically: only the release whose sequence exactly matches the target
contributes aliases and annotations.

The pipeline intentionally does **not** claim that direct candidates are final
evaluable ground truth. `BenchmarkPipelineResult.final_ground_truth` remains
`None`, and `require_final_ground_truth()` raises `GroundTruthNotFinalError`.
This protects the scientific invariant that final truth still requires the
truth/mask milestone:

```text
D_score
G_candidate = C(D_score)
K0 = C(D0_positive)
X1 = C(D_neutral1) - C(D_score)
M = Q - K0 - X1
T = G_candidate intersect M
```

Synthetic tests are in `tests/test_pipeline.py`. They cover aliases changing
between releases, complete evidence preservation, evidence upgrades, branch
acquisition, unmatched targets, and one accession changing sequence across
releases. No real release was processed.

## Current scientific state

Stable decisions:

- exact normalized sequence identity, never similarity, is the primary match;
- aliases are release-specific provenance;
- one pinned `go.owl` is used throughout;
- direct membership is keyed by sequence, aspect, and canonical GO term;
- assertion context never creates a term acquisition;
- evidence codes are retained as complete sets;
- `NAS`, `ND`, and `NOT` do not create positive strict truth;
- direct events and propagated truth are distinct;
- only `is_a` and `part_of` propagate annotations;
- evidence confirmation remains separate from novel-function evaluation.

Still pending:

- approval/implementation of the truth and evaluation-mask milestone;
- closure provenance from every direct term;
- construction of `Q`, `D_score`, `K0`, `D_neutral1`, `X1`, `M`, and `T`;
- final decisions for K0 evidence scope, new t1 IEA, event profile, and
  evidence-confirmation unmasking;
- information accretion and informativeness filters;
- prediction ingestion and metrics.

## Advancement rationale

The work advanced in dependency order rather than jumping directly to a final
benchmark table:

1. **Normalize each release first.** GOA IDs and UniProt accessions must be
   synchronized against one ontology and one release FASTA before temporal
   comparison. Otherwise ontology drift or missing sequences can be mistaken
   for biological novelty.
2. **Match t0 and t1 independently.** An accession may disappear, migrate, or
   refer to a different sequence. Exact matching per release prevents the same
   accession string from overriding sequence identity.
3. **Merge only through exact sequence identity.** Release aliases are combined
   only after both searches have independently matched the same target sequence.
4. **Compare canonical direct terms.** Evidence and assertion contexts are
   retained, but only sequence/aspect/canonical-term membership defines a
   biological annotation event.
5. **Stop before unsupported finalization.** Direct qualifying events provide
   the correct input to truth construction, but they are not yet the evaluable
   truth. Publishing them as final would ignore prior-accessible knowledge,
   neutral later truth, roots, global ontology scope, and closure provenance.

This sequence makes Requirement 2 operational without silently resolving the
remaining truth/mask policy decisions. The explicit final-truth gate is therefore
a scientific safeguard, not an unfinished placeholder in the orchestration.

## Verification status at handoff

The completed project gate is:

```text
pixi run check
47 tests passed
Ruff passed
```

`git diff --check` also passed, and the modified Python files passed Ruff's
format check. The 47 tests include all preprocessing and t0/t1 pipeline tests.

No real ontology/GOA/UniProt benchmark run has been performed for Requirements
1 or 2. No commit or push was made during this session.

## Final implementation state

- Requirement 1 is implemented and synthetically tested.
- Requirement 2 is implemented through canonical direct ground-truth
  candidates and synthetically tested.
- `go.owl` is the authoritative master ontology throughout the code and current
  documentation.
- The final evaluable ground truth remains intentionally blocked until the
  truth/mask milestone constructs and tests `Q`, `D_score`, `G_candidate`, `K0`,
  `D_neutral1`, `X1`, `M`, and `T`.
- The working tree remains uncommitted and contains both work that predated this
  session and the changes described in this handoff. It must be preserved as a
  single shared unit unless the user explicitly requests a reviewed commit.

## Safe continuation from another desktop

Because every workstation accesses this same working copy:

1. run `git status --short --branch` before doing anything;
2. do not pull, reset, restore, clean, or switch branches over the current
   uncommitted work;
3. read `AGENTS.md` and this handoff completely;
4. inspect `src/probe/preprocessing.py`, `src/probe/pipeline.py`, and their tests;
5. run `pixi run check` before changing scientific behavior;
6. continue with synthetic fixtures only unless the user explicitly authorizes
   a real release run.

## Suggested resume prompt

Use the following prompt in the next Codex session:

> Riprendi ProBE dalla working copy remota condivisa sul branch
> `codex/modifiche-main`. Prima di modificare file, esegui `git status` e leggi
> integralmente `AGENTS.md` e `docs/SESSION_HANDOFF_2026-09-20.md`. Non usare
> `go-plus.owl`: il master è `go.owl`. Verifica con `pixi run check` il
> preprocessore del Requisito 1 e la pipeline t0/t1 del Requisito 2. Non usare
> file reali. Poi ricostruisci esattamente ciò che manca per implementare il
> milestone truth/mask (`Q`, `D_score`, `K0`, `D_neutral1`, `X1`, `M`, `T`),
> evidenziando prima le decisioni scientifiche ancora da approvare e senza
> trattare `direct_candidates` come ground truth finale.

## Continuation — preprocessing CLI contract confirmed 2026-09-21

The release preprocessor now uses the required flags `--owl`, `--uniprot`,
`--goa`, `--new_goa`, and `--uni_with_go`. It restricts the filtered GOA to
ACCIDs present in the supplied FASTA, removes every GO root row, and retains an
accession only when at least one valid non-root assertion remains. `NOT` rows
and all other GAF provenance remain available for later positive/negative
comparison. Missing FASTA ACCIDs are excluded from the GOA and written alone,
one per line, to `NEW_GOA.missing_accids.log` unless an override is supplied.
For release-scale inputs, accession membership is held in a temporary SQLite
index under the optional `--work_dir`; FASTA and GOA records are streamed and
the large release files are never materialized in RAM.

## Continuation — target identity mapping 2026-09-23

`src/probe/target_mapping.py` now assigns deterministic `T` plus nine-digit
internal IDs to target records, preserves the full original target header in a
quoted TSV mapping, writes a normalized internal-ID FASTA, and streams the
preprocessed UniProt FASTA to find exact hash-and-sequence matches. UniProt
ACCIDs are extracted from `db|ACCID|entry_name` headers and emitted sorted and
comma-separated. Normalization is symmetric and policy-versioned; terminal
`*` handling must be selected explicitly as `strip` or `preserve`, while
internal `*` is rejected and supported ambiguous/IUPAC symbols are retained.
