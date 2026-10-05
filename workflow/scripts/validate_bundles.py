"""Explicit validation target; never part of ordinary restart checks."""

from pathlib import Path

from probe.workflow.artifacts import validate_bundle

context = globals()["snakemake"]
log = Path(context.log[0])
log.parent.mkdir(parents=True, exist_ok=True)
with log.open("w", encoding="utf-8") as handle:
    for manifest_path in context.input:
        bundle = Path(manifest_path).parent
        result = validate_bundle(bundle, deep=context.params.deep)
        handle.write(
            f"{bundle}\t{result.stage}\tOK\tdeep={context.params.deep}\n"
        )
        handle.flush()
