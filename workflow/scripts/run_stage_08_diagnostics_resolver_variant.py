"""Snakemake immutable M12 diagnostics after resolver changes."""

import subprocess
import sys
from pathlib import Path

from probe.stage07_variants import read_spec

context = globals()["snakemake"]
spec = read_spec(Path(context.params.spec_path))
exp = Path(spec["run"]) / "experiments" / spec["experiment_id"]
marker = exp / "FULL_SCAN_AUTHORIZED"
if marker.is_symlink() or not marker.is_file():
    raise RuntimeError("M12 diagnostics require full-run authorization")
if marker.read_text(encoding="utf-8") != spec["experiment_id"] + "\n":
    raise RuntimeError("M12 diagnostics authorization mismatch")
dest = Path(spec["paths"]["diagnostics"])
if Path(context.output[0]) != dest / "publication_manifest.json":
    raise ValueError("Unexpected diagnostics variant output")
if dest.is_symlink() or (dest.exists() and not dest.is_dir()):
    raise FileExistsError("Unsafe diagnostics destination")
if dest.is_dir():
    try:
        dest.rmdir()
    except OSError as exc:
        raise FileExistsError("Refusing nonempty diagnostics directory") from exc

repo = Path(spec["repo"])
command = [
    sys.executable,
    str(repo / "scripts/pipeline/08_profile_benchmark.py"),
    "--benchmark",
    spec["paths"]["benchmark"],
    "--targets",
    spec["target_file"],
    "--stage01",
    spec["stage01_targets"],
    "--output",
    str(dest),
]
with Path(context.log[0]).open("w", encoding="utf-8") as log:
    subprocess.run(command, cwd=repo, stdout=log, stderr=log, check=True)
if not (dest / "publication_manifest.json").is_file():
    raise RuntimeError("M12 diagnostics variant did not publish")
