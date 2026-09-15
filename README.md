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
ontology = GeneOntology.from_owl("data/pinned-go-plus.owl")
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
