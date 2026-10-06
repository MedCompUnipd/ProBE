# Gene Ontology input policy

## Scope

ProBE compares biological knowledge associated with two generic temporal roles:

- `START`
- `END`

The biological datasets assigned to START and END are selected by the user.

For each experiment, ProBE requires one user-selected `go.owl` snapshot.
The same exact `go.owl` snapshot must be used to normalize both START and END.

## Common ontology reference space

Conceptually:

    user-selected go.owl snapshot
                |
          +-----+-----+
          |           |
          v           v
        START         END
      GO annotations GO annotations
          |           |
          v           v
      normalize    normalize
          |           |
          +-----+-----+
                |
                v
        temporal comparison

Using the same `go.owl` snapshot for both temporal roles ensures that START and
END are interpreted in one common ontology reference space.

This prevents ontology evolution itself from being confounded with temporal
changes in biological annotation data.

## What is fixed by ProBE

ProBE fixes the ontology family and the scientific normalization rules:

- the ontology input is `go.owl`;
- `go-plus.owl` is not used for normalization, navigation, propagation, or
  scoring;
- the GO normalization algorithm is defined by ProBE;
- START and END must use the same ontology artifact.

## What is selected by the user

The user selects:

- the START biological snapshot;
- the END biological snapshot;
- the `go.owl` release/snapshot used as the common ontology reference.

ProBE does not impose a predefined:

- `go.owl` release date;
- `go.owl` version IRI;
- `go.owl` SHA-256;
- START database release;
- END database release.

START and END are temporal roles, not hard-coded release identifiers.

The user is responsible for providing internally coherent biological inputs for
each temporal snapshot.

## Ontology provenance and integrity

For every experiment, ProBE computes the SHA-256 digest of the supplied
`go.owl` artifact and records the observed digest in run provenance.

The user may optionally provide an expected SHA-256 value. When supplied, ProBE
must verify that the observed digest matches it before continuing.

An expected checksum is an integrity assertion for the user-selected input. It
does not restrict ProBE to a predefined `go.owl` release.

Additional provenance such as ontology IRI, version IRI, source URL, release
date, and retrieval date may be recorded when available. Unknown provenance
must not be invented.

## Normalization

The single selected `go.owl` snapshot is used to derive one common GO
normalization policy.

That same policy is applied to both START and END data.

The normalization policy includes, as defined by ProBE:

- primary GO identifier resolution;
- alternate identifier resolution;
- replacement handling;
- obsolete and deprecated identifier handling;
- namespace assignment;
- GO root identification.

The normalization algorithm is part of ProBE. The `go.owl` snapshot to which it
is applied is selected by the user.

## Validation datasets

Specific UniProt, GOA, taxonomy, and `go.owl` releases used during ProBE
development and regression testing are validation fixtures only.

They are not production requirements and must not be encoded as scientific
constraints of the workflow.
