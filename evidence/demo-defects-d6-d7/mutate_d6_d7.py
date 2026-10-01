"""D6/D7 mutations: each must make a test fail. Usage: python mutate_d6_d7.py <repo root>"""
import subprocess
import sys
from pathlib import Path

ROOT = Path(sys.argv[1])
TESTS = ["tests/test_d6_runtime_artifacts.py", "tests/test_d7_diagnosis_removal.py"]
V, F, A = "kriya/tools/validate.py", "kriya/workflow/file_integrity.py", "kriya/workflow/attribution.py"
M = [
 # D6: the owner's list
 ("D6 global-allowance-every-gate", V, "    ephemeral = name == RUNTIME_VERIFICATION_GATE\n", "    ephemeral = True\n"),
 ("D6 allowance-during-tests-and-builds", V, "    ephemeral = name == RUNTIME_VERIFICATION_GATE\n",
  "    ephemeral = name in (RUNTIME_VERIFICATION_GATE, \"tests\", \"compile\")\n"),
 ("D6 ignore-tracked-modifications", F, "        changed = self.changes()\n        if changed:\n",
  "        changed = self.changes()\n        if changed and not ephemeral_untracked:\n"),
 ("D6 runtime-files-leak-into-candidate", F, "            self._discard(created)\n", "            pass\n"),
 ("D6 leak-and-not-fail-closed", F, "            if remaining:\n", "            if False:\n"),
 ("D6 runtime-files-become-recovery-targets", F, "        if created and ephemeral_untracked:\n", "        if False:\n"),
 ("D6 directory-name-exemption", F, "        if created and ephemeral_untracked:\n            self._discard(created)\n",
  "        if created and ephemeral_untracked and all(p.startswith(\"ignite/\") for p in created):\n            self._discard(created)\n"),
 # D6: evidence
 ("D6 artifacts-not-recorded", V, "                    result[\"runtime_artifacts\"] = artifacts\n", "                    result[\"runtime_artifacts\"] = []\n"),
 ("D6 artifacts-not-persisted-to-gate-outcome", V, "    \"runtime_artifacts\",\n", ""),
 ("D6 exception-path-keeps-artifacts", V, "                    self.tree_binding.check(name, ephemeral_untracked=ephemeral)\n                raise\n",
  "                    self.tree_binding.check(name)\n                raise\n"),
 # D7: the exact decision branch
 ("D7 helper-disabled", A, "        if _construct_removed(q, pairs, orig_text):\n            return None\n", ""),
 ("D7 construct-need-not-exist-pre-edit", A, "    return (present > 0\n", "    return (True\n"),
 ("D7 partial-removal-accepted", A, "            and sum(len(token.findall(search)) for search, _ in pairs) == present\n",
  "            and sum(len(token.findall(search)) for search, _ in pairs) > 0\n"),
 ("D7 moved-call-accepted", A, "            and not any(token.search(replace) for _, replace in pairs))", ")"),
 ("D7 fuzzy-left-boundary", A, "    token = re.compile(r\"(?<![\\w$.])\" + re.escape(match.group(1)) + r\"\\s*\\(\")",
  "    token = re.compile(re.escape(match.group(1)) + r\"\\s*\\(\")"),
 ("D7 any-signature-quote-accepted", A, "    if match is None:\n        return False\n", "    if match is not None:\n        return True\n"),
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
