# ProBE M11/M12: first temporal benchmark (novel-acquisition profile)

Status: **new implementation for server validation; not yet canonical run**.
The PROGENY rename remains deferred. Stage 01-08 and the M10c bundle
remain immutable. No original normalized GAF is read.

## Input contracts

- `08_interpret_compact/direct_provenance.sqlite3` M10c, especially the
  `term_code_support` table indexed by release/target/aspect/GO/polarity/code.
- M10c manifest, referenced TSV and immutable SHA256 fingerprints.
- `inputs/targets.tsv`, containing the original `uniprot_accession` for each
  internal `target_id`. The accession is **not** the donor accession.
- `work/pipeline-v2/common/01/01_targets.tsv`, independent complete
  target-ID/`optional_accession` cross-check. Missing target IDs are fatal. Duplicate accessions are accepted only when
  Stage 01 proves equal taxonomy, normalized sequence SHA256, and
  normalization policy, with sequence bytes rehashed. Non-identical
  collisions are fatal; no heuristic fallback.
- Exact pinned OWL and versioned evidence registry, plus an **explicit**
  benchmark policy with four independent evidence selections (`score`, `prior`,
  `neutral`, `not`) and START/END NOT roles.

The policy in `config/stage08_temporal_policy.example.json` is **illustrative**,
not a scientifically approved preset. Do not mistake preflight success for
approval of its selection rules.

## Operational semantics

For each `(target, aspect, GO)` in the pinned GO graph, the M10c-derived
positive/NOT GO closures are already available. M11/M12 apply:

- `K0`: positive START support selected by `prior` evidence.
- `G`: positive END support selected by `score` evidence.
- `X1`: positive END support selected by `neutral` evidence.
- `N`: START and/or END negative GO supports admitted by the `not` profile.
- Initial main benchmark: `EVAL = G - (K0 U X1 U N)`.
- `SKIP = (K0 U X1) - N`. Both EVAL and SKIP exclude the three aspect roots.

Any GO within a selected NOT subtree is **neither EVAL nor SKIP**; a prediction
of it scores as FP **if that target/aspect belongs to the assessment**. The
explicit `target_aspects.tsv` file defines that universe: at least one EVAL
term in the aspect. A GO not in either EVAL or SKIP is an FP when predicted
and valid in the frozen GO DAG; outside-DAG predictions are ignored by the
future evaluator. A missing GO is an *operational benchmark FP*, not proof of
biological absence.

No new normalized GO annotations are invented. Score and prior selections
apply to inherited supports in the same way as to direct supports, while
individual direct-support provenance and GO routes remain in M10c.

## Outputs (only after explicit `--execute`)

- `benchmark_terms.tsv`: `query_id,aspect,go_id,status` with EVAL or SKIP.
  One row per assessable target/aspect/GO. The query_id is the **original**
  accession from `inputs/targets.tsv`, not `T000...` or a donor accession.
- `target_aspects.tsv`: one row per assessable target/aspect, including counts
  and START prior-knowledge flags; one score universe per F, P and C.
- `negative_terms.tsv`: diagnostic NOT closure for selected NOT codes. The
  file includes internal IDs for traceability, and may include target/aspects
  not assessed in the main benchmark. It is NOT a second scoring ground truth.
- `temporal_events.tsv`: direct END support acquisition, branch/refinement,
  evidence confirmation of a direct GO (only where START lacks a matching
  score-profile direct annotation), maintained direct score-profile GO,
  previously implied direct GO, and disappearing direct START support. Direct events
  are **descriptive**; inherited EVAL GO remain in benchmark_terms.tsv.
- `duplicate_targets.tsv`: one row for each verified identical duplicate,
  recording both Stage 01 internal target IDs, taxon, and sequence digest.
  The benchmark merges their GO-support flags before applying EVAL/SKIP,
  preventing double weight from the same query sequence. Both original
  M10c target IDs and their donor support remain recoverable upstream.
- `manifest.json`: original source hashes, policy/registry fingerprints, code
  hashes, count summaries and output checksums. Publication is atomic.

## Boundaries and remaining work

1. **EVIDENCE_CONFIRMATION** is reported as a temporal direct event, but is not
   yet an independent EVAL scoring profile (the first benchmark profile is
   novel acquisition). Any future confirmation scoring needs a separate
   explicit mask/truth profile, not an implicit tier comparison.
2. `DIRECT_NEW_GLOBAL` means no *selected K0* in any aspect. This does not
   mean no GO was ever observed; all START observations are retained in M10c.
3. Direct losses are observational losses; they do not imply biology is lost.
   No missing GO in normalised GOA can be reconstructed without upstream data.
4. Full PK masks, CAFA scoring thresholds, predictions propagation and
   Snakemake integration remain after the first validated temporal release.
5. The example profile selects experimental traditional/high-throughput for
   END score, all listed non-NAS/ND evidence groups for K0, curated/electronic
   for END neutral, and both-release NOT in the same selected groups.
   This **must be explicitly approved or replaced** before running a
   production benchmark.
6. The NOT/positive conflict is a deliberate conservative precedence rule;
   report both original supports, never discard provenance.

## Efficiency and reproducibility

Processing uses one sequential SQLite term-code scan ordered by the M10c PK,
followed by a disk-backed keyed merge across releases. Only the current
(target, three aspects) terms are buffered in memory. Full M10c payload
SHA256 is rechecked before execution, while stamps and implementation hashes
are verified again before atomic output publication.

Preflight reads metadata and target-ID maps, but not M10c's multi-GB term
support table. An early preflight PASS does not certify production outputs.
