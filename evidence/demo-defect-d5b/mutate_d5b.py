"""D5b mutations: each must make a test fail. Usage: python mutate_d5b.py <repo root>"""
import subprocess
import sys
from pathlib import Path

ROOT = Path(sys.argv[1])
TESTS = ["tests/test_d5b_recovery_evidence.py", "tests/test_d5_runtime_resource_grounding.py"]
G, R = "kriya/workflow/failure_grounding.py", "kriya/workflow/retry_strategy.py"
M = [
 ("start-at-the-grounded-frame-again", G, "        if header is not None:\n            start = header\n", ""),
 ("first-exception-in-the-whole-log", G, "        if header is not None:\n            start = header\n",
  "        first = _EXCEPTION_HEADER_LINE.search(raw_output) if False else next(\n"
  "            (m.start() for m in re.finditer(r\"(?m)^.*(?:Exception|Error):\", raw_output)), None)\n"
  "        if first is not None:\n            start = first\n"),
 ("ignore-caused-by", G, "    r\"^\\s*(?:Caused by:\\s+|Exception in thread", "    r\"^\\s*(?:Exception in thread"),
 ("break-the-d5-resource-case", G,
  "    if _STACK_FRAME_LINE.match(raw_output[start:line_end if line_end >= 0 else len(raw_output)]):\n",
  "    if not _STACK_FRAME_LINE.match(raw_output[start:line_end if line_end >= 0 else len(raw_output)]):\n"
  "        return raw_output[:limit]\n    if True:\n"),
 ("remove-the-bound", G, "    return raw_output[start:start + limit]\n", "    return raw_output[start:]\n"),
 ("alter-recovery-file-scope", R, "            outside_scope = sorted(set(implicated) - allowed_scope)",
  "            outside_scope = sorted((set(implicated) | set(known_attribution_files)) - allowed_scope)"),
 ("unbounded-message-reach", G, "    for candidate in range(index - 1, max(-1, index - 1 - _EXCEPTION_MESSAGE_LINES), -1):",
  "    for candidate in range(index - 1, -1, -1):"),
 ("frames-not-skipped", G, "    while index > 0 and _STACK_FRAME_LINE.match(lines[index - 1]):\n        index -= 1\n", ""),
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
