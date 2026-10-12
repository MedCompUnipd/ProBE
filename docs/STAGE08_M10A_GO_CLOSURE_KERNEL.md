# Stage 08 / M10a — conservative GO closure kernel

Status: **additive, library-only, not in the production Snakemake DAG**.

M9b and the canonical M8 source collection remain unchanged. M10a introduces
an immutable GO graph index, built from the existing, independently audited
`GeneOntology.from_owl` implementation. The pinned OWL SHA256 remains
`42d7869b3a47cd4a9751d7f2f373acf737b64a1435e4b02b169d134b240dc666`.

## Explicit scientific contract

- Only direct GO `is_a` and existential `part_of` edges are admitted, provided
  that both terms are active and in the **same GO aspect**. `regulates`,
  `negatively_regulates`, `positively_regulates`, `has_part` and `occurs_in`
  are not equivalent to functional inheritance; they are rejected as graph
  inputs, not silently followed.
- The adapter to the existing ProBE ontology loader *observes and counts*
  cross-aspect edges, but never traverses them. A manually supplied graph
  rejects cross-aspect edges unless ignoring them is explicitly requested.
- Positive closure follows child → ancestor. Negative `NOT` closure follows
  parent → descendant over **the same vetted hierarchical edges**. Both
  closures include the directly asserted GO term; this supports direct and
  inherited annotations without confusing them.
- Term-missing errors and cyclic inheritance graphs fail closed.
- Direct relation types and an inspectable shortest witness path are exposed
  for later per-donor provenance. No source evidence codes are changed.
- The index does not interpret GAF qualifier/relation field, combine multiple
  donors, resolve simultaneous positive/NOT evidence, apply `SKIP`, classify
  events, or decide TP/FP. Those belong to subsequent M10b/M11/M12 work.
- Root terms are deliberately retained in the closure; exclusion from scoring
  is an independent evaluation policy.

## Scaling notes

The graph is validated once in O(nodes + direct edges), with closures computed
iteratively and cached on demand (bounded to 1024 terms per direction).
No full 40,267-by-40,267 closure matrix is materialized. The data model is
immutable once constructed, and no GOA source files are touched by this module.

## Real pinned-ontology acceptance gate

The independent M9 OWL projection audit reported 40,267 active terms and 78,639
unique eligible same-aspect direct edges, with an exact set match against the
existing ProBE ontology loader. The M10a server validator rebuilds the graph
from that loader and requires these two counts. It also verifies that the
result does not contain a cycle and that the positive/NOT inverse directions
are coherent on audited sample terms.

No production interpretation artifact is produced. The bundle M8 SHA256s are
checked before and after the validation; no Git staging, commit or push.
