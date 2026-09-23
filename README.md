# ProBE

ProBE builds time-resolved protein function benchmarks from CAFA target
sequences, historical UniProt releases, Gene Ontology annotations, and matching
Gene Ontology releases.

The project is an API-first rewrite of the original ProBE pipeline. The first
goal is a small, auditable library that can be explored from notebooks. A CLI,
prediction metrics, download automation, and alternative sequence matching are
deliberately deferred until the core scientific model is stable.

## Scientific model

ProBE keeps four inputs independent and versioned:

1. A cohort of target protein sequences, normally from CAFA.
2. One or more historical UniProt FASTA releases used to find accessions with
   exactly identical sequences.
3. Two GO annotation snapshots, called `old` and `new`.
4. One pinned GO ontology snapshot used for both annotation releases.

Exact sequences are assigned an internal SHA-256 identity. UniProt accessions
and CAFA target identifiers are aliases of that identity; they are not treated
as the identity itself. Annotations are compared only after both releases have
been normalized against the same ontology vocabulary. Unknown, obsolete, and
ambiguous replacement terms are excluded with an auditable reason. Evidence
upgrades are decided by an explicit policy rather than an implicit numeric ranking.

## Installation

[Pixi](https://pixi.sh/) owns the development environment:

```bash
pixi install
pixi run test
```

For an editable Python installation outside Pixi:

```bash
python -m pip install -e .
```

Python 3.11 or newer is required.

## Notebook API

The intended workflow is composed from ordinary Python objects:

```python
from probe import (
    AnnotationSnapshot,
    EvidencePolicy,
    GeneOntology,
    SequenceDataset,
    SequenceIndex,
    compare_annotations,
)

targets = SequenceDataset.read("data/cafa_targets.fasta")

uniprot = SequenceIndex.from_fastas(
    {
        "uniprot_old": "data/uniprot_old.fasta.gz",
        "uniprot_new": "data/uniprot_new.fasta.gz",
    }
)
identities = uniprot.match(targets)

subjects = identities.subject_ids
ontology = GeneOntology.from_owl("data/pinned-go.owl")
old = AnnotationSnapshot.read(
    release="2023-01-01",
    annotations="data/goa_old.gaf.gz",
    ontology=ontology,
    subjects=subjects,
)
new = AnnotationSnapshot.read(
    release="2025-01-01",
    annotations="data/goa_new.gaf.gz",
    ontology=ontology,
    subjects=subjects,
)

result = compare_annotations(
    old,
    new,
    identities=identities,
    evidence_policy=EvidencePolicy.experimental(),
)

result.changes
result.exclusions
result.selected_targets
result.validation
```

Reading and analysis do not create output directories or write implicit files.
Serialization will be added as an explicit operation once the result schema is
settled.

## Release preprocessing

The stand-alone preprocessor synchronizes one GOA/UniProt release against the
pinned master `go.owl`. It canonicalizes GO IDs, removes unresolved annotations
and root-only accessions, writes a FASTA containing only accessions retained in
the cleaned GOA, and records retained GOA accessions missing from the FASTA.

```bash
pixi run python -m probe.preprocessing \
  --owl data/go.owl \
  --uniprot data/uniprot_release.fasta.gz \
  --goa data/goa_release.gaf.gz \
  --new_goa output/GOA_filtered.gaf \
  --uni_with_go output/Uniprot_filtered.fasta \
  --statistics output/preprocessing_report.txt \
  --work_dir /large-fast-volume/probe-work
```

The same operation is available from notebooks through `preprocess_release()`.
The inputs may be plain text or gzip-compressed; outputs are deterministic plain
text files. Only GOA ACCIDs present in the input FASTA are eligible. The default
missing-accession log is
`output/GOA_filtered.gaf.missing_accids.log` and contains only the excluded
ACCIDs, one per line; its path can be overridden with
`--missing_accessions_log`.

The FASTA and GOA are never loaded wholesale into memory. A temporary SQLite
accession index is created under `--work_dir` (or beside `--new_goa` when the
option is omitted), the FASTA is streamed to build that disk index, the GOA is
streamed once while the filtered GOA is written, and the FASTA is streamed a
second time to write `--uni_with_go`. The temporary index is removed on normal
completion and on Python exceptions. Choose a local, fast filesystem with ample
free space for `--work_dir`; the index size depends mainly on the number and
length of unique FASTA accessions. The 122 MB ontology is parsed as the pinned
in-memory ontology context, but the 109/178 GB release files are not
materialized in RAM.

All three GO roots are removed from the filtered GOA. A protein is retained only
when it has at least one valid non-root annotation. Evidence codes, `NOT` and
other qualifiers, and all remaining GAF columns are preserved for later
release comparison. Alternate IDs and unique active `replaced_by` chains are
canonicalized; unresolved, `consider`-only, and aspect-incompatible terms are
discarded.

## Target internal IDs and exact UniProt matching

The target-mapping tool accepts heterogeneous target headers, assigns stable
run-local IDs in `T000000001` format, writes a renamed target FASTA, and matches
normalized target sequences exactly against a preprocessed UniProt FASTA.

```bash
pixi run python -m probe.target_mapping \
  --targets data/cafa5_targets.fasta \
  --uniprot output/Uniprot_filtered.fasta \
  --mapping output/target_mapping.tsv \
  --internal_fasta output/targets_internal.fasta \
  --terminal_stop strip
```

`--terminal_stop` is mandatory and accepts `strip` or `preserve`. Both inputs
use the same policy. Case and whitespace are normalized, IUPAC protein symbols
including `B`, `J`, `O`, `U`, `X`, and `Z` are retained unchanged, and internal
`*` characters are rejected. The mapping TSV contains quoted original header,
internal ID, sorted comma-separated UniProt ACCIDs, and normalized protein
length, in target input order.

## t0/t1 benchmark pipeline

After preprocessing both releases, `run_benchmark_pipeline()` matches each
target sequence independently against the t0 and t1 UniProt FASTA files, reads
only the associated GOA assertions, merges release-specific aliases by exact
sequence identity, and classifies the direct annotation events with an explicit
evidence policy.

```python
from hashlib import sha256
from pathlib import Path

from probe import (
    ConfirmationMaskPolicy,
    EvidencePolicy,
    EvaluationUniversePolicy,
    NeutralTruthPolicy,
    PositiveAssertionPolicy,
    TruthMaskConfiguration,
    TruthSelectionProfile,
)
from probe.pipeline import OntologyInput, ReleaseInput, run_benchmark_pipeline

go_owl = Path("data/go.owl")
evidence = EvidencePolicy.experimental_strict()
result = run_benchmark_pipeline(
    targets_fasta="data/targets.fasta",
    ontology=OntologyInput(
        path=go_owl,
        expected_sha256=sha256(go_owl.read_bytes()).hexdigest(),
        source_url="http://purl.obolibrary.org/obo/go.owl",
        retrieval_date="YYYY-MM-DD",
    ),
    t0=ReleaseInput(
        "t0",
        Path("t0/Uniprot_filtered.fasta"),
        Path("t0/GOA_filtered.gaf"),
    ),
    t1=ReleaseInput(
        "t1",
        Path("t1/Uniprot_filtered.fasta"),
        Path("t1/GOA_filtered.gaf"),
    ),
    evidence_policy=evidence,
    truth_mask=TruthMaskConfiguration(
        truth_profile=TruthSelectionProfile.combined(evidence),
        prior_policy=PositiveAssertionPolicy.all_usable_positive_t0(),
        neutral_policy=NeutralTruthPolicy.primary_non_electronic_neutral(),
        confirmation_mask_policy=ConfirmationMaskPolicy.main_knowledge_gain(),
        universe_policy=EvaluationUniversePolicy.is_a_rooted(),
    ),
)
```

`result.direct_candidates` contains qualifying canonical direct events with all
old/new evidence codes and source assertions. It is never the final evaluable
ground truth. When `truth_mask` is supplied, `result.final_ground_truth` contains
separate target/aspect records for `D_score`, `G_candidate`, `K0`,
`D_neutral1`, `X1`, `M`, and final `T`. Omitting `truth_mask` leaves final truth
unset, and `require_final_ground_truth()` raises rather than silently promoting
the direct candidates.

The example makes one run configuration explicit; it does not establish a
permanent headline profile. Alternative named event, neutral-IEA,
evidence-confirmation, and safe-root policies remain separately identifiable.

## Codebase structure

```text
src/probe/
├── __init__.py          Public notebook API
├── source.py            Plain/gzip input handling and checksums
├── validation.py        Structured errors and warnings
├── records.py           Immutable FASTA and GAF records
├── parsing/
│   ├── base.py          Minimal parser protocol
│   ├── fasta.py         Streaming protein FASTA parser
│   ├── gaf.py           Streaming GAF 2.x parser
│   └── owl.py           GO OWL/RDF loader
├── identity.py          Sequence normalization, hashing, and aliases
├── ontology.py          GO graph navigation, propagation, IC, and SimGIC
├── relations.py         Exact-IRI relation policy and edge dispositions
├── ontology_validation.py Deterministic ontology preflight and reachability
├── preprocessing.py      Stand-alone GOA/FASTA release synchronization
├── target_mapping.py     Internal target IDs and exact UniProt matching
├── pipeline.py           Exact t0/t1 matching and direct-event orchestration
├── truth.py              Direct truth records and named scientific policies
├── masking.py            Q, closure provenance, K0, X1, M, and final T
├── snapshot.py          Annotation/ontology release consistency
├── evidence.py          Explicit evidence categories and policies
└── comparison.py        Annotation changes and protein selection
```

The dependency direction is intentionally one-way:

```text
source + validation -> parsing -> domain objects -> analysis -> notebook
```

Parsers do not select benchmark proteins. Ontology code does not render tables.
Evidence policy does not read files. Keeping these boundaries makes scientific
assumptions independently testable.

## Pinned ontology preflight

Benchmark work should load an ontology context with the expected checksum before
using its GO projection:

```python
from probe import OwlLoader, preflight_ontology

context = OwlLoader().load_context(
    "data/go.owl",
    expected_sha256="<sha256 of the exact local bytes>",
    source_url="http://purl.obolibrary.org/obo/go.owl",
    retrieval_date="YYYY-MM-DD",
)
report = preflight_ontology(context)
ontology = context.ontology
```

This performs no network import retrieval and no OWL reasoning. The complete
parsed graph remains available through a read-only query facade, while the GO
projection admits annotation propagation only through same-aspect `is_a` and
`part_of` edges. `GeneOntology.from_owl()` remains a compatibility API for
small exploratory workflows, but it does not supply the explicit provenance
required for a published benchmark.

## Design rules

- Add an abstraction only when it removes duplicated behavior or protects a
  scientific invariant.
- Preserve raw assertion provenance; filtering happens after parsing.
- Keep direct annotations separate from ontology-propagated annotations.
- Do not silently discard unknown, obsolete, negated, or unmappable terms.
- Use the same pinned ontology object for both annotation releases.
- Prefer immutable records and pure transformations.
- Do not add a CLI code path separate from the Python API.
- Avoid pandas as an internal data model. Notebook adapters may be added later.

## Current scope

The initial implementation provides the object model and an executable vertical
slice over small and medium inputs. Full-release persistence and performance
tuning remain roadmap items. See [DEVELOPMENT.md](DEVELOPMENT.md) for current
status, coordination rules, and the implementation plan.

## License

ProBE is licensed under the GNU General Public License v3.0. See
[LICENSE](LICENSE).
