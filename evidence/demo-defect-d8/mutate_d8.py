"""D8 mutations: each must make a test fail. Usage: python mutate_d8.py <repo root>"""
import subprocess
import sys
from pathlib import Path

ROOT = Path(sys.argv[1])
TESTS = ["tests/test_d8_terminal_toolchain_authority.py", "tests/test_prd020_requirement_lineage.py",
         "tests/test_prd030_terminal_services.py"]
W, T, TI = "kriya/workflow/workflow.py", "kriya/workflow/terminal_gate_service.py", "kriya/tools/toolchain_identity.py"
M = [
 ("unconditional-validator-construction", W, "        return []  # D8: nothing to close, so no validator and no toolchain resolution\n", "        pass\n"),
 ("terminal-default-authority-false", T, "                    toolchain_declaration_mutable=toolchain_declaration_mutable(\n                        WriteScopeMode.DENY_ALL, (), request.plan,\n                    ),\n",
  "                    toolchain_declaration_mutable=False,\n"),
 ("authority-globally-true", W, "        toolchain_declaration_mutable=toolchain_declaration_mutable,\n    )\n    validator.java_home_override",
  "        toolchain_declaration_mutable=True,\n    )\n    validator.java_home_override"),
 ("terminal-authority-globally-true", T, "                    toolchain_declaration_mutable=toolchain_declaration_mutable(\n                        WriteScopeMode.DENY_ALL, (), request.plan,\n                    ),\n",
  "                    toolchain_declaration_mutable=True,\n"),
 ("bypass-earlier-unauthorized-toolchain-gate", TI, "        if not _same_toolchain(baseline, target):\n            if not declaration_mutable:\n                raise ToolchainRequirementConflictError(\n                    f\"the candidate changes the Java toolchain",
  "        if not _same_toolchain(baseline, target):\n            if False:\n                raise ToolchainRequirementConflictError(\n                    f\"the candidate changes the Java toolchain"),
 ("skip-named-tests-when-they-exist", W, "    if not any(outcomes.get(requirement.id) is RequirementOutcome.UNVERIFIED\n",
  "    if True or not any(outcomes.get(requirement.id) is RequirementOutcome.UNVERIFIED\n"),
 ("pre-scan-ignores-verdicts", W, "    if not any(outcomes.get(requirement.id) is RequirementOutcome.UNVERIFIED\n               and named_existing_tests",
  "    if not any(named_existing_tests"),
]
surv = 0
for label, rel, old, new in M:
    p = ROOT / rel
    t = p.read_text()
    if t.count(old) != 1:
        print(label, "ANCHOR", t.count(old))
        surv += 1
        continue
    p.write_text(t.replace(old, new))
    try:
        r = subprocess.run([str(ROOT / ".venv/bin/pytest"), "-q", "-x", "-p", "no:randomly", *TESTS], cwd=ROOT,
                           capture_output=True, text=True, timeout=1800)
    finally:
        p.write_text(t)
    k = r.returncode != 0
    surv += not k
    fail = [line for line in r.stdout.splitlines() if line.startswith("FAILED")][:1]
    print(label, "KILLED" if k else "SURVIVED", fail[0][7:120] if fail else "", flush=True)
print(f"{len(M)-surv}/{len(M)} killed")
