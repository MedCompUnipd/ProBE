"""Snakemake variant M10c runner, using the canonical interpreter CLI."""

import subprocess
import sys
from pathlib import Path

from probe.stage07_variants import read_spec

context = globals()["snakemake"]
spec = read_spec(Path(context.params.spec_path))
exp = Path(spec["run"]) / "experiments" / spec["experiment_id"]
marker = exp / "FULL_SCAN_AUTHORIZED"
if marker.is_symlink() or not marker.is_file():
    raise RuntimeError("M10c variant requires explicit full-run authorization")
if marker.read_text(encoding="utf-8") != spec["experiment_id"] + "\n":
    raise RuntimeError("M10c variant authorization mismatch")
destination = Path(spec["paths"]["m10c"])
if Path(context.output[0]) != destination / "manifest.json":
    raise ValueError("Unexpected variant M10c output")
if destination.is_symlink() or (destination.exists() and not destination.is_dir()):
    raise FileExistsError("Unsafe variant M10c destination")
if destination.is_dir():
    try:
        destination.rmdir()
    except OSError as exc:
        raise FileExistsError("Refusing nonempty M10c destination") from exc

run = Path(spec["run"])
work = run / "work/pipeline-v2"
repo = Path(spec["repo"])
command = [
    sys.executable,
    str(repo / "scripts/pipeline/08_interpret_compact.py"),
    "--stage07-bundle",
    spec["paths"]["stage07"],
    "--start-goa-bundle",
    str(work / "START/01B1"),
    "--end-goa-bundle",
    str(work / "END/01B1"),
    "--go-policy-bundle",
    str(work / "common/01B0"),
    "--m8-bundle",
    spec["paths"]["stage08"],
    "--ontology",
    spec["ontology"],
    "--destination",
    str(destination),
    "--execute",
]
with Path(context.log[0]).open("w", encoding="utf-8") as log:
    subprocess.run(command, cwd=repo, stdout=log, stderr=log, check=True)
if not (destination / "manifest.json").is_file():
    raise RuntimeError("M10c variant did not publish")
