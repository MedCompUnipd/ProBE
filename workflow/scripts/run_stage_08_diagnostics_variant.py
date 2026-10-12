"""Snakemake runner: create immutable diagnostics for an M12 variant."""

import subprocess
import sys
from pathlib import Path

from probe.stage08_variants import read_spec

context = globals()["snakemake"]
spec = read_spec(Path(context.params.spec_path))
repo = Path(spec["repo"])
sources = spec["sources"]
dest = Path(spec["statistics"])
if Path(context.output.diagnostics_manifest) != (dest / "publication_manifest.json"):
    raise ValueError("Unexpected statistics manifest location")
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
    str(repo / "scripts/pipeline/08_profile_benchmark.py"),
    "--benchmark",
    spec["benchmark"],
    "--targets",
    sources["targets"],
    "--stage01",
    sources["stage01"],
    "--output",
    str(dest),
    "--length-edges",
    spec["filters"]["length_edges"],
]
for key, flag in (
    ("min_length", "--min-length"),
    ("max_length", "--max-length"),
    ("taxids", "--taxids"),
):
    value = spec["filters"][key]
    if value is not None:
        command.extend((flag, str(value)))
with Path(context.log[0]).open("w", encoding="utf-8") as handle:
    subprocess.run(command, cwd=repo, check=True, stdout=handle, stderr=handle)
if not (dest / "publication_manifest.json").is_file():
    raise RuntimeError("Diagnostics did not publish its manifest")
