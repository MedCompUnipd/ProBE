"""Guard the resolver variant DAG's explicit M8 summary contract."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _rule(text: str, current: str, following: str) -> str:
    start = text.index(f"rule {current}:")
    end = text.index(f"rule {following}:", start)
    return text[start:end]


def test_m8_summary_is_declared_output_before_m12_input() -> None:
    snake = (ROOT / "workflow/Snakefile").read_text(encoding="utf-8")
    collector = _rule(
        snake,
        "stage_08_resolver_variant",
        "stage_08_compact_resolver_variant",
    )
    outputs = collector.split("            output:", 1)[1]
    outputs = outputs.split("            log:", 1)[0]
    assert outputs.index("stage08_manifest=") < outputs.index("stage08_summary=")
    assert 'Path(RV_PATH["stage08"]) / "manifest.json"' in outputs
    assert 'Path(RV_PATH["stage08"]) / "08_collection_summary.json"' in outputs
    benchmark = _rule(
        snake,
        "stage_08_benchmark_resolver_variant",
        "stage_08_diagnostics_resolver_variant",
    )
    inputs = benchmark.split("            input:", 1)[1]
    inputs = inputs.split("            output:", 1)[0]
    assert 'Path(RV_PATH["stage08"]) / "08_collection_summary.json"' in inputs


def test_m8_publisher_writes_summary_inside_atomic_staging() -> None:
    source = ROOT / "src/probe/stage08_collection_publish.py"
    text = source.read_text(encoding="utf-8")
    assert '(staging / "08_collection_summary.json").write_text(' in text
