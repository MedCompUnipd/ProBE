"""Snakemake runner: create an immutable evidence-policy M12 benchmark."""

import subprocess
import sys
from pathlib import Path

from probe.stage08_variants import read_spec

context = globals()["snakemake"]
spec = read_spec(Path(context.params.spec_path))
repo = Path(spec["repo"])
sources = spec["sources"]
dest = Path(spec["benchmark"])
if Path(context.output.benchmark_manifest) != dest / "manifest.json":
    raise ValueError("Unexpected benchmark manifest location")
# Snakemake may precreate the directory containing a manifest output.
# Only an empty directory is a harmless placeholder.
if dest.is_symlink() or (dest.exists() and not dest.is_dir()):
    raise FileExistsError("Non-directory or symlink destination")
if dest.is_dir():
    try:
        dest.rmdir()  # Atomic; cannot remove nonempty directories.
    except OSError as exc:
        raise FileExistsError(
            "Refusing to overwrite a nonempty variant directory"
        ) from exc

command = [
    sys.executable,
    str(repo / "scripts/pipeline/08_build_benchmark_v2.py"),
    "--m10c-bundle",
    str(Path(sources["m10c_manifest"]).parent),
    "--m8-summary",
    sources["m8_summary"],
    "--targets-input",
    sources["targets"],
    "--stage01-targets",
    sources["stage01"],
    "--evidence-registry",
    spec["registry"],
    "--policy",
    spec["policy"],
    "--ontology",
    sources["ontology"],
    "--start-release",
    spec["releases"]["START"],
    "--end-release",
    spec["releases"]["END"],
    "--destination",
    str(dest),
    "--execute",
]
with Path(context.log[0]).open("w", encoding="utf-8") as handle:
    subprocess.run(command, cwd=repo, check=True, stdout=handle, stderr=handle)
if not (dest / "manifest.json").is_file():
    raise RuntimeError("M12 did not publish its manifest")
