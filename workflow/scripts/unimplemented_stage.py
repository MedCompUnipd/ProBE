"""Fail closed until a stage is migrated and validated in a later block."""

from pathlib import Path

context = globals()["snakemake"]
message = (
    f"Stage {context.params.stage} is declared but not implemented in Block 1. "
    "No scientific outputs were produced. See docs/PIPELINE_V2_STAGE_REGISTRY.md."
)
log = Path(context.log[0])
log.parent.mkdir(parents=True, exist_ok=True)
log.write_text(message + "\n", encoding="utf-8")
raise NotImplementedError(message)
