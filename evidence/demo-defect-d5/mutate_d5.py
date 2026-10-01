"""D5 mutations: each must make a test fail. Usage: python mutate_d5.py <repo root>"""
import subprocess
import sys
from pathlib import Path

ROOT = Path(sys.argv[1])
TESTS = ["tests/test_d5_runtime_resource_grounding.py", "tests/test_d4_runtime_entrypoint_ownership.py"]
G, A = "kriya/workflow/failure_grounding.py", "kriya/workflow/attribution.py"
R, C = "kriya/workflow/retry_strategy.py", "kriya/workflow/workflow_controller.py"
M = [
 # the owner's list
 ("disable-resource-extraction", G, "            references.append(FailureResourceReference(raw, normalized, kind, depth))\n", ""),
 ("loader-frame-ranked-above-resource", A, '    if failure.type == "run_verification":\n        resource = ground_runtime_resource_failure(',
  '    if failure.type == "run_verification" and not extract_error_source_locations(raw_text):\n        resource = ground_runtime_resource_failure('),
 ("accept-ambiguous-basename", G, "        if len(matches) == 1:\n            return \"resolved\", (matches[0],)",
  "        if len(matches) >= 1:\n            return \"resolved\", (matches[0],)"),
 ("allow-outside-workspace", G, "        ref = os.path.relpath(os.path.realpath(ref), os.path.realpath(workspace_root))",
  "        ref = ref.lstrip(\"/\")"),
 ("allow-absolute-without-workspace", G, "        if not workspace_root:\n            return \"unresolved\", ()\n        ref = os.path.relpath(os.path.realpath(ref), os.path.realpath(workspace_root))",
  "        ref = os.path.relpath(os.path.realpath(ref), os.path.realpath(workspace_root or os.sep))"),
 ("reopen-the-verification-unit", C, "            effective_owner_subtask_id=resolution.owner_subtask_id,\n            owner_resolution_basis=resolution.resolution_basis.value,\n            mutation_reason=mutation_reason,",
  "            effective_owner_subtask_id=failed_subtask.id,\n            owner_resolution_basis=resolution.resolution_basis.value,\n            mutation_reason=mutation_reason,"),
 ("authorize-all-files", R, "            outside_scope = sorted(set(implicated) - allowed_scope)",
  "            outside_scope = sorted((set(implicated) | set(known_attribution_files)) - allowed_scope)"),
 ("remove-recovery-no-progress", C, "                        and recovery_candidate_fingerprints.get(fingerprint_key) == candidate_fingerprint\n",
  "                        and False\n"),
 # D5's own decisions
 ("drop-innermost-candidate-frame-guard", G, "    if innermost - enclosing:\n        return None\n", ""),
 ("shallowest-naming-exception-decides", G, "    deepest = max((ref.segment", "    deepest = min((ref.segment"),
 ("ambiguous-reference-ignored-not-blocking", G, "    resolved = () if ambiguous else tuple(", "    resolved = tuple("),
 ("partial-path-basename-fallback", G, "    if \"/\" not in ref:\n        tiers.append(", "    if True:\n        tiers.append(([f for f in known if os.path.basename(f) == os.path.basename(ref)]) or "),
 ("no-resource-root-tier", G, "        tiers.append([f for f in known if any(f == root + ref or f.endswith(\"/\" + root + ref)",
  "        tiers.append([f for f in known if False and any(f == root + ref or f.endswith(\"/\" + root + ref)"),
 ("resource-evidence-for-every-failure-type", A, '    if failure.type == "run_verification":\n        resource = ground_runtime_resource_failure(',
  '    if True:\n        resource = ground_runtime_resource_failure('),
 ("owner-evidence-head-only", R, "\"raw_evidence\": grounded_evidence_excerpt(failure.raw_output or \"\", outside_scope),",
  "\"raw_evidence\": (failure.raw_output or \"\")[:2000],"),
 ("excerpt-ignores-head", G, "    if pattern is None or pattern.search(raw_output, 0, limit):\n", "    if pattern is None:\n"),
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
