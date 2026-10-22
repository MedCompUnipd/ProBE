# Exact large-pair DP for canonical V2

Design baseline: `086e7960ef955dac515c1e2d6a8f715ebf5fc2e8` (2026-10-21).

## Scope and compatibility

The validated `certified-geometric-bounds-v2` C++ and Python implementations
remain unchanged. The extension runs after the existing documentary and
same-resolved-TaxID NONEXACT admission gate. It never changes admission,
EXACT discovery, donor selection, Stage 07, GO, NOT, propagation or temporal
benchmark policies. No global target-universe execution is authorized.

The existing eleven-column certified region plan and forty-nine-column
alignment output retain their exact schemas and meanings. In particular,
`cells_evaluated` is the complete certified region size, including boundary
cells, for each direction; it is independent of interruptions and retries.

The existing summary's `dp_backend` continues to identify the certified
geometry/scoring contract consumed by the unchanged downstream validators.
An explicit `dp_execution_backend` and a hash-bound backend plan identify the
actual execution engines. A mixed execution must never be reported as having
used the frozen executable for every pair. Both executable hashes are pinned.

## Numerical contract inspected in the frozen source

Source: `cpp/src/probe_directional_glocal_bounded_dp_v2.cpp`.

- Literal character equality scores +2, including ambiguous IUPAC symbols.
  Every unequal character pair scores -1. Gap opening is -3 and each extension
  is -1: a run of length L costs `-3 - (L - 1)`.
- TARGET_GLOBAL consumes the target and permits free candidate overhangs.
  CANDIDATE_GLOBAL consumes the candidate and permits free target overhangs.
- M predecessors are preferred in order M, X, Y; X prefers X, M, Y; Y prefers
  Y, M, X. `best3` replaces the current choice only for a strictly larger
  score. Metrics are never used as an extra tie-break criterion.
- Endpoint preference is score descending, consumed `i+j` descending,
  state ascending (M=0, X=1, Y=2), i descending, j descending. TARGET_GLOBAL
  examines row n; CANDIDATE_GLOBAL examines column m at rows i >= 1.
- Free leading boundary residues are absent from path statistics. Coordinates
  are zero-based and half-open. Identity over aligned pairs and identity over
  alignment columns have different denominators. Formatting stays `%.10f`.
- The frozen safe sequence length and integer score sentinel remain in force.
  This work changes the space requirement, not the numerical domain.

## Chosen algorithm and equivalence argument

The new `certified-rolling-stats-v1` execution engine uses the same certified
band as the reference. It stores two compact rows of three affine states.
Each state contains its score, starting coordinates and cumulative matches,
mismatches, residues in either gap direction, and gap openings. No CIGAR or
traceback string is required by the existing output contract.

Induction over the frozen row/column traversal establishes equivalence:
boundary states represent exactly the reference's traceback stopping points;
at each interior state the same ordered score comparison selects the same
predecessor; copying that predecessor's statistics and applying the one edge
operation yields the reference path's statistics. Endpoint selection uses
the frozen comparator and copies the winning state's statistics. The two
directions therefore reproduce every serialized scientific field, including
ties and free ends. The band certificate retains every optimal path and tie.

Rows retain the original target/candidate orientation. Transposing the
sequences merely to shorten a row would exchange X/Y preferences and is not
part of this implementation. Buffer width is the maximum certified row width,
not the area of the matrix. Runtime remains proportional to certified cells.

Affine divide-and-conquer would require additional tie-preservation reasoning
and recursive boundary contracts without helping this statistics-only output.
Tiled traceback would retain a quadratic disk product that is unnecessary
here. Rolling path statistics is the smallest exact extension for this API.

The new translation unit includes the frozen source with its main entry point
renamed, so score constants, geometry, ordered comparisons and serializer are
shared without editing the validated source. Its own CLI and rolling kernel
are separate. Both source identities are part of build provenance.

## Resource planner and routing

The complete admitted workload is inventoried before execution. A separate
`2C_06C0_backend_plan.tsv` records pair key, dimensions, both directional cell
counts, band widths, resource estimates, assigned engine and ordered chunk.
The ordinary backend keeps the configured pair threshold (default 50M cells
across both directions). Oversize or memory-constrained pairs use the rolling
engine. Resource failures remain explicit incomplete execution, never a
scientific exclusion or a policy-skipped result.

The existing 500M default total cap remains a limit on each invocation.
The rolling process checkpoints and returns status 75 when its cell quantum
is exhausted. The stage resumes the same pair with the same quantum until
complete; this loop executes one stage's numerical work and is not a second
workflow scheduler. The cursor can stop within a row, so no minimum row-sized
exception silently exceeds the cell cap. Only exit 0 supplies final outputs.

The memory limit controls the accounted DP workspace, with explicit sequence
and checkpoint overhead. It is not an assertion that operating-system RSS is
identical to the estimate. Native allocations are checked before execution;
measured peak RSS and CPU time accompany controlled validation. Ordinary
concurrent workers are accounted for separately from serial large-pair work.

## Checkpoint and publication contract

Completed chunk manifests bind exact ordered keys and payload SHA256 digests.
The new journal is separate from the frozen journal format. A stable active
pair directory survives destruction of a stage's transient publication area.
Its identity binds source hashes, both executable identities, resources,
release identifiers, pair identity and certified plan.

An intra-pair checkpoint contains direction, row/column cursor, both active
frontiers and statistics, current best endpoint and its statistics, and any
already completed direction. It binds sequences and geometry as well as the
caller-provided identity. A completed first direction is reused when the
second direction resumes. Checkpoint bytes are checksummed, written to a
temporary file, fsynced, renamed atomically and followed by directory fsync.

The executor trusts a complete committed checkpoint only. Partial temporary
files are not completion records. A corrupted or incompatible committed state
fails explicitly. Uncheckpointed work may be repeated after an abrupt kill;
valid completed chunks and committed intra-pair work are retained. Logical
scientific cells and physical work in each invocation are reported separately.

On Linux, death of the Python parent delivers SIGTERM to the native worker.
The worker commits a safe checkpoint and returns 75. A new worker waits for
the previous worker's file lock, so an immediate restart neither runs the
same pair concurrently nor fails merely because the old worker is saving.

Merge verifies the exact frozen header, exhaustive key coverage, no duplicate
keys, expected order, statuses and directional certified cell counts before
publication. The existing finalizer remains the scientific metric validator.
An incomplete merge is never a published alignment bundle.

## DAG and operational integration

An optional `large_dp` block in a fresh recipe or canonical specification
supplies `binary`, `max_dp_memory_bytes`, `checkpoint_rows` and `scratch_root`.
Its presence enables automatic execution selection within the existing
certified DP contract. Absence preserves the complete previous behavior and
fingerprints. The block and actual binary content join execution provenance.

On thanos the scratch root is `/data/PROGENY/tmp/probe-v2-scratch`; TMPDIR, TMP
and TEMP are set to it. Incomplete DP work and persistent resume data use that
root. It must remain available until the new run is fully validated. Atomic
publication requires scratch and destination on the same filesystem; this is
checked before numerical work. Tests use an explicitly selected isolated
scratch root in their own environment.

Snakemake retains only dependency ordering, declared resources and restart.
Pair admission, certificate checks, output reconciliation and checkpoint
validation remain inside the stage and its numerical engine. A fresh128 run
uses a new recipe, new output root and the exact same 128 target inputs as
the frozen run. Neither completed baseline output nor main is modified.

## Validation gates

1. Native differential tests compare all 49 fields against the frozen bounded
   executable. The full-matrix reference independently checks all scientific
   fields except its intentionally different cell accounting.
2. Exhaustive short, fixed-seed randomized, tie-rich, ambiguous-alphabet,
   asymmetric and free-overhang cases exercise path selection. Metamorphic
   alphabet relabeling preserves equality and numerical results.
3. A genuinely wide certified region exceeds the real 50M pair threshold.
   Long near-identical sequences alone do not prove this case.
4. Small invocation quanta, first/second-direction restart, abrupt kills,
   corruption, changed inputs and memory refusal exercise checkpoint safety.
5. Mixed routing and merge tests prove admission-independent complete coverage
   and reuse of completed chunks. Config and fingerprint tests protect legacy
   behavior and new source identity.
6. The on-server gate runs Ruff on touched Python, the project test suite, a
   controlled selected-pair integration, then a new isolated fresh128 E2E.
   It compares scientific rows and summaries with the frozen baseline.

Test evidence and limitations are reported separately from this design. This
document is not itself a claim that an on-server run has taken place.
