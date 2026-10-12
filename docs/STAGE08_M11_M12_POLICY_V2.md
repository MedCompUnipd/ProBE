# ProBE Stage 08 M11/M12 — configurable evidence classes (V2)

Status: **versioned implementation for validation, not approved production
policy**. The historical V1 pilot and frozen stages 01–08/M10c are immutable.
The PROGENY rename is deferred.

## Separate policy dimensions

Each of the 26 registered codes belongs to exactly one class:

- `SCORE`: allowed to establish positive ground truth at END.
- `NON_SCORE`: informative but insufficient on its own to establish truth.
- `EXCLUDED`: ignored in deciding truth, masks and conflicts. Retained in M10c.

Membership is a configurable partition of codes/groupings in M9b; no
implicit hardcoded list of "experimental" codes. The example policy puts
traditional and high-throughput experimental groups in SCORE; other curated,
phylogenetic, computational and electronic codes in NON_SCORE, and NAS/ND in
EXCLUDED. Users may reassign codes, e.g. IBA to SCORE, by adjusting the
explicit disjoint selections. The example is **not scientifically approved**.

## Synthetic benchmark states

`benchmark_terms.tsv`: `query_id,aspect,go_id,status`, where status is exactly
one of `EVAL`, `SKIP`, `NEUTRAL`. `NEUTRAL` is excluded from TP/FP/FN, just as
SKIP is, but its scientific reason is kept separate. `EVAL` derives from
positive END SCORE that did not have positive START SCORE, unless a retained
NOT conflict supersedes it. END NON_SCORE never vetoes a positive END SCORE.
An EVAL is `NOVEL` if there was no admitted positive START NON_SCORE support,
and `CONFIRMATION` otherwise. These are **scientific reasons** in `term_audit.tsv`,
not additional synthetic statuses.

`SKIP` covers previously SCORE-supported START functions, isolated NON_SCORE
positive annotations, or other already known terms. Negatives resolved as
negative are **not** SKIP/NEUTRAL: they stay outside the synthetic table,
operationally FP when predicted for an assessable target/aspect.

## NOT conflicts and temporal reversals

- END NOT NON_SCORE versus END positive SCORE: positive wins.
- END NOT SCORE versus positive NON_SCORE: negative wins, predicted term FP.
- END NOT SCORE versus END positive SCORE: `NEUTRAL`, reason
  `CONFLICT_SCORE_END`.
- START NOT SCORE versus END positive SCORE, even without END NOT:
  `NEUTRAL`, `TEMPORAL_REVERSAL_NOT_TO_POSITIVE`.
- START positive SCORE versus END NOT SCORE: `NEUTRAL`, reverse reversal.
- IKR/IRD NOT (SCORE or NON_SCORE) versus END positive SCORE: `NEUTRAL`,
  `CONFLICT_IKR_IRD`; these codes are not downgraded automatically to IEA.
- Positive and negative NON_SCORE at END without SCORE: `NEUTRAL`.
- START NOT NON_SCORE alone does not veto a subsequent END positive SCORE.

All these collisions are **operational conflicts of propagated supports**, not
yet proof of biologically contradictory experiments: M10c keeps GAF relations,
original donors and GAF line provenance. `term_audit.tsv` and
`negative_terms.tsv` capture per-term decision reasons for later targeted
provenance review. No global evidence-strength ranking is introduced.

Out-of-DAG predictions remain ignored by the eventual evaluator. Within an
assessable target/aspect, valid GO terms not in EVAL/SKIP/NEUTRAL count as FP.
The three ontology roots are omitted. The assessment universe contains only
(target, aspect) pairs with at least one EVAL; NEUTRAL is audited even when
its target/aspect is outside that universe.

## Output and version isolation

New files: `benchmark_terms.tsv`, `target_aspects.tsv`, `term_audit.tsv`,
`negative_terms.tsv`, `temporal_events.tsv`, `duplicate_targets.tsv`,
`manifest.json`. The audit lists EVAL and NEUTRAL with their reason and class
presence, plus resolved negatives (as `NEGATIVE`, not a synthetic status).
`temporal_events.tsv` reports direct-score changes only and is descriptive.
All original accessions are sourced from `inputs/targets.tsv`, and the sole
proven-identical duplicate target is merged with provenance preserved.

Default mode is read-only metadata preflight. `--execute` builds a new
immutable destination and verifies the multi-GB M10c inputs at SHA256 level
before publication. No GAF re-scan or upstream modifications.

This version is not yet a finalized GO evaluator; no CAFA metric is implied.
