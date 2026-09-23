from __future__ import annotations

import pytest

from probe.target_mapping import (
    SequenceNormalizationPolicy,
    TerminalStopPolicy,
    map_target_sequences,
    normalize_protein_sequence,
)


def test_target_mapping_assigns_ids_and_matches_exact_sequences(tmp_path):
    targets = tmp_path / "targets.fasta"
    uniprot = tmp_path / "uniprot.fasta"
    mapping = tmp_path / "mapping.tsv"
    internal = tmp_path / "targets_internal.fasta"
    targets.write_text(
        '>CAFA target\talpha "quoted"\nacdxu*\n'
        ">second heterogeneous header\nMBOZJ\n"
        ">unmatched\nAAAA\n",
        encoding="utf-8",
    )
    uniprot.write_text(
        ">tr|P11111|FIRST entry\nACDXU\n"
        ">sp|P00001|SECOND entry\nacdxu\n"
        ">sp|P22222|THIRD entry\nMBOZJ\n"
        ">sp|P99999|OTHER entry\nCCCC\n",
        encoding="utf-8",
    )

    result = map_target_sequences(
        target_fasta=targets,
        uniprot_fasta=uniprot,
        mapping_path=mapping,
        internal_fasta_path=internal,
        normalization_policy=SequenceNormalizationPolicy(TerminalStopPolicy.STRIP),
    )

    assert [record.internal_id for record in result.records] == [
        "T000000001",
        "T000000002",
        "T000000003",
    ]
    assert mapping.read_text(encoding="utf-8") == (
        '"CAFA target alpha ""quoted"""\tT000000001\tP00001,P11111\t5\n'
        '"second heterogeneous header"\tT000000002\tP22222\t5\n'
        '"unmatched"\tT000000003\t\t4\n'
    )
    assert internal.read_text(encoding="utf-8") == (
        ">T000000001\nACDXU\n>T000000002\nMBOZJ\n>T000000003\nAAAA\n"
    )


def test_terminal_stop_policy_is_symmetric_and_explicit():
    stripped = normalize_protein_sequence(
        "acdx*",
        policy=SequenceNormalizationPolicy(TerminalStopPolicy.STRIP),
    )
    preserved = normalize_protein_sequence(
        "acdx*",
        policy=SequenceNormalizationPolicy(TerminalStopPolicy.PRESERVE),
    )

    assert stripped.normalized_sequence == "ACDX"
    assert preserved.normalized_sequence == "ACDX*"
    assert stripped.ambiguous_symbols == {"X"}
    with pytest.raises(ValueError, match="internal '\\*'"):
        normalize_protein_sequence(
            "AC*DX",
            policy=SequenceNormalizationPolicy(TerminalStopPolicy.STRIP),
        )
