"""Snakemake M12 from a resolver-specific M10c; no canonical writes."""

import subprocess
import sys
from pathlib import Path

from probe.stage07_variants import read_spec

context = globals()["snakemake"]
spec = read_spec(Path(context.params.spec_path))
exp = Path(spec["run"]) / "experiments" / spec["experiment_id"]
marker = exp / "FULL_SCAN_AUTHORIZED"
if marker.is_symlink() or not marker.is_file():
    raise RuntimeError("M12 variant requires full-run authorization")
if marker.read_text(encoding="utf-8") != spec["experiment_id"] + "\n":
    raise RuntimeError("M12 variant authorization mismatch")
dest = Path(spec["paths"]["benchmark"])
if Path(context.output[0]) != dest / "manifest.json":
    raise ValueError("Unexpected M12 variant output")
if dest.is_symlink() or (dest.exists() and not dest.is_dir()):
    raise FileExistsError("Unsafe M12 destination")
if dest.is_dir():
    try:
        dest.rmdir()
    except OSError as exc:
        raise FileExistsError("Refusing nonempty M12 destination") from exc

repo = Path(spec["repo"])
run = Path(spec["run"])
command = [
    sys.executable,
    str(repo / "scripts/pipeline/08_build_benchmark_v2.py"),
    "--m10c-bundle",
    spec["paths"]["m10c"],
    "--m8-summary",
    str(Path(spec["paths"]["stage08"]) / "08_collection_summary.json"),
    "--targets-input",
    spec["target_file"],
    "--stage01-targets",
    spec["stage01_targets"],
    "--evidence-registry",
    spec["registry"],
    "--policy",
    spec["evidence_policy"],
    "--ontology",
    spec["ontology"],
    "--start-release",
    spec["releases"]["START"],
    "--end-release",
    spec["releases"]["END"],
    "--destination",
    str(dest),
    "--execute",
]
with Path(context.log[0]).open("w", encoding="utf-8") as log:
    subprocess.run(command, cwd=repo, stdout=log, stderr=log, check=True)
if not (dest / "manifest.json").is_file():
    raise RuntimeError("M12 variant did not publish")
