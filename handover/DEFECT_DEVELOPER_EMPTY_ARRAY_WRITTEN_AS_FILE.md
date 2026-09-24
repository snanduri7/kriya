# Defect: a Developer answer of `[]` is written verbatim as a file's content

## Status
OPEN. Not fixed; kept out of PRD-008 on purpose. Found 2026-09-24 during the PRD-008 S4c zero-write probe.

Proposed route: model-protocol / output-normalization work (likely PRD-015). Promote it to an earlier blocking defect if live testing shows it happens with real models.

## Summary
The file is `calc.py`. The Developer is in `MODE: REPAIR_WITH_FULL_FILE`. It answers with the two characters `[]`, an empty JSON file array that means "no files to change".

Kriya takes that text as the new full content of `calc.py`. The one-line Python module becomes the literal text `[]`, which is still valid Python. So it compiles, and the repository has no tests to fail. The run then commits the destroyed file to the real workspace and reports SUCCESS.

## Reproduction (plain Python, mocked LLM, real workflow and real commit)
```python
import asyncio, subprocess, tempfile, os
from pathlib import Path
from unittest.mock import AsyncMock
from kriya.config.config import AppConfig
from kriya.core import LLMClient
from kriya.core.kernel import Kernel
from kriya.workflow.workflow import WorkflowEngine

tmp = Path(os.path.realpath(tempfile.mkdtemp()))
for a in (["init", "-q"], ["config", "user.email", "t@e.invalid"], ["config", "user.name", "T"]):
    subprocess.run(["git", *a], cwd=tmp, check=True)
(tmp / "calc.py").write_text("def add(a, b):\n    return a + b\n")
subprocess.run(["git", "add", "."], cwd=tmp, check=True)
subprocess.run(["git", "commit", "-qm", "s"], cwd=tmp, check=True)

cfg = AppConfig(); cfg.autonomy.mode = "guardrails"; cfg.autonomy.run_verification_enabled = False
seq = ["Step 1: verify calc.py add", "Design: calc.py already has add", "[]", "Review: Approved"] + ["Review: Approved"] * 30
async def fake(*a, **k):
    return seq.pop(0)
llm = LLMClient(cfg); llm.complete = AsyncMock(side_effect=fake)
result = asyncio.run(WorkflowEngine(Kernel(config=cfg), llm).run_generation_workflow(
    goal="ensure calc.py has add", workspace_path=str(tmp)))
print(repr((tmp / "calc.py").read_text()))
```

## Observed (captured 2026-09-24 at commit 4ad70f2)
**Model calls:**
```
Planner   -> Step 1: verify calc.py add
Architect -> Design: calc.py already has add
Developer (MODE: REPAIR_WITH_FULL_FILE) -> []
Reviewer  -> Review: Approved
```

**Gates, attempt 1:**
- `compile` succeeded: "Python files compiled successfully."
- `regression_test` succeeded with "collected 0 items ... no tests ran". PRD-008 S4c records this as `NO_TESTS_EXECUTED`, which is never positive evidence.

**Commit and result:**
- RunRecord: `SUCCESS`; commit cycles `['COMMITTED']`.
- Commit evidence: `committed`, `calc.py MODIFY`, with before/after sha256 different.
- Final `calc.py` content: `'[]'`. Before the run it was `'def add(a, b):\n    return a + b\n'`.

## Expected
In a full-file mode, an empty JSON array (or any JSON-shaped "no files" answer) should be treated as a protocol-level answer, never as file content. Acceptable readings:
- **No change:** an empty candidate, so no commit for that file; or
- **Protocol violation:** a typed failure and a retry, with the file left untouched.

Replacing the file with `[]` is never acceptable.

## Affected path
- **Where the answer enters:** the Developer response handling in `kriya/workflow/attempt.py` for `CodeOperation.REPAIR_WITH_FULL_FILE` (around the `CREATE_FULL_FILE`/`REPAIR_WITH_FULL_FILE` handling near lines 845-970).
- **Where it could be caught:** `kriya/agents/agent.py::DeveloperAgent`, which already normalizes batch JSON file arrays and the `path`→`filepath` key. The empty-array case falls through to "raw text is the file content".
- **Why no gate stops it:** the damage survives because `[]` compiles as Python, and a repository with no tests has no behavioural gate. In Java or Ruby, compile would likely fail and trigger a retry. The worst case is exactly the no-test Python repository.

## Not in scope of the finding
PRD-008 is not affected. The commit was a real, verified-by-gates COMMITTED cycle, and S4b/S4c prove its bytes correctly. The defect is in what the gates accepted as a candidate.
