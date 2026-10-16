# Optional Stage 2C scientific evidence. No canonical output is replaced.
# A ready receipt is the only Snakemake output for an immutable publication.
# NEVER mark an already-published scientific directory as directory().

S2C_V2_ROOT = Path(S2C_V2_PLAN["root"])
S2C_V2_REPO = Path(S2C_V2_PLAN["repo"])


def s2c_receipt(phase, role):
    if role in ("START", "END"):
        phase_dir = Path(S2C_V2_PLAN["paths"][role][phase])
        return str(phase_dir.parent / (phase + ".ready.json"))
    if role == "common":
        return str(S2C_V2_ROOT / "common" / (phase + ".ready.json"))
    return str(S2C_V2_ROOT / "resolver" / (role + ".ready.json"))


def s2c_evidence_inputs(wildcards):
    role = wildcards.role
    phase = wildcards.phase
    manifest = lambda stage: bundle(stage, role)
    receipt = lambda previous: s2c_receipt(previous, role)
    if phase == "delta":
        return [
            str(Path(S2C_UNION_PLAN["paths"][role]) / "manifest.json"),
            bundle("05A2"),
        ]
    if phase == "bridge":
        return [
            receipt("delta"),
            str(Path(S2C_PLAN["paths"][role]) / "manifest.json"),
            str(Path(S2C_FULL_PLAN["paths"][role]) / "manifest.json"),
        ]
    if phase == "reuse":
        return [receipt("bridge"), manifest("05D")]
    if phase == "full_recovery":
        return [receipt("reuse"), manifest("01A_FULL")]
    if phase == "reconcile":
        return [receipt("full_recovery"), receipt("reuse"), receipt("bridge")]
    if phase == "projection":
        return [
            receipt("reconcile"), receipt("bridge"), bundle("05A2"),
            manifest("02B"), bundle("01"),
        ]
    if phase == "coverage":
        return [
            receipt("projection"), receipt("reconcile"),
            manifest("05D"), bundle("01"),
        ]
    if phase == "go_recovery":
        return [receipt("coverage"), manifest("01C")]
    raise ValueError("Invalid Stage 2C evidence phase")


rule stage_2c_evidence_step:
    input:
        s2c_evidence_inputs
    output:
        str(S2C_V2_ROOT / "{role}" / "{phase}.ready.json")
    params:
        phase=lambda wc: wc.phase,
        role=lambda wc: wc.role,
        plan_path=str(S2C_V2_SPEC_PATH),
        enabled=S2C_V2_EXECUTE
    wildcard_constraints:
        role="START|END",
        phase=(
            "projection|coverage|go_recovery"
            if "inheritance" in S2C_V2_PLAN
            else "delta|bridge|reuse|full_recovery|reconcile|projection|"
            "coverage|go_recovery"
        )
    threads: 1
    resources:
        mem_mb=8192,
        runtime=240
    script:
        "scripts/run_stage_2c_variant.py"


rule stage_2c_shared_preview:
    input:
        [
            s2c_receipt("go_recovery", role)
            for role in ("START", "END")
        ] + [
            bundle("05A2"), bundle("05C2"), bundle("01"),
            bundle("05D", "START"), bundle("05D", "END"),
        ]
    output:
        s2c_receipt("preview", "common")
    params:
        phase="preview",
        role="common",
        plan_path=str(S2C_V2_SPEC_PATH),
        enabled=S2C_V2_EXECUTE
    threads: 1
    resources:
        mem_mb=8192,
        runtime=240
    script:
        "scripts/run_stage_2c_variant.py"


rule stage_2c_shared_completion:
    input:
        preview=s2c_receipt("preview", "common"),
        binary=DIRECTIONAL_GLOCAL_BINARY,
        targets=bundle("01")
    output:
        s2c_receipt("completion", "common")
    params:
        phase="completion",
        role="common",
        plan_path=str(S2C_V2_SPEC_PATH),
        enabled=S2C_V2_EXECUTE
    threads:
        cfg.resources("06C0")["threads"]
    resources:
        mem_mb=cfg.resources("06C0")["mem_mb"],
        runtime=cfg.resources("06C0")["runtime_minutes"]
    script:
        "scripts/run_stage_2c_variant.py"


rule stage_2c_policy_resolver:
    input:
        [
            s2c_receipt("completion", "common"),
            s2c_receipt("preview", "common"),
            bundle("04D1"),
        ] + [
            bundle(stage, role)
            for stage in ("02B", "04B", "04D2")
            for role in ("START", "END")
        ]
    output:
        str(S2C_V2_ROOT / "resolver" / "{policy}.ready.json")
    params:
        phase="resolver",
        role=lambda wc: wc.policy,
        plan_path=str(S2C_V2_SPEC_PATH),
        enabled=S2C_V2_EXECUTE
    wildcard_constraints:
        policy="strict|same_species_exact"
    threads: 1
    resources:
        mem_mb=8192,
        runtime=240
    script:
        "scripts/run_stage_2c_variant.py"


rule stage_2c_variants_both:
    input:
        [
            s2c_receipt("resolver", policy)
            for policy in ("strict", "same_species_exact")
        ]
