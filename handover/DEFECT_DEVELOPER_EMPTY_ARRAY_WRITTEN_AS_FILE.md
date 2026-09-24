# Defect: a Developer answer of `[]` is written verbatim as a file's content

## Status
FIXED, READY_FOR_PYTEST_VERIFICATION (2026-09-24, after PRD-008 was VERIFIED at `6e1a846`). Found 2026-09-24
during the PRD-008 S4c zero-write probe; kept out of PRD-008 on purpose. See "Fix" at the end. PRD-008A does not
start until this fix is independently pytest-verified.

Classification (S4c review decision, 2026-09-24): a false-success-capable model-output/rendering defect.

Schedule: fixed immediately after PRD-008 S5, before PRD-008A. It no longer waits for PRD-015.

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

## Fix (2026-09-24)
**Reproduced on HEAD first** (`3cde9cb`, with a role-aware fake LLM so every Developer call answers `[]`):
the Developer ran in `MODE: REPAIR_WITH_FULL_FILE`, `calc.py` became `'[]'`, and `quality_gates_passed` was
True. The milestone path had the same defect: the milestone reported `success` with the destroyed file.

**Chosen reading: protocol violation, not "no change".** `[]` is not in the vocabulary of any full-file
contract (CREATE_FULL_FILE and REPAIR_WITH_FULL_FILE ask for raw content; the retry REPAIR contract has its own
explicit `NO CHANGE NEEDED:` marker). Reading it as "no change" would turn a malformed answer into a
clean-looking outcome, so it fails closed instead: a typed failure, a retry, and the file left untouched.

**Changes:**
- `kriya/agents/agent.py`:
  - New `DeveloperAgent._file_list_protocol_answer_error(content, filepath)`. On the already-sanitized content
    (fences stripped, any envelope that carries real content already unwrapped), a whole-response JSON array,
    an empty object, or an object with a `files`/`filepath`/`path` key is a file-list protocol answer.
  - Exempt: data-format targets (`.json`, `.jsonc`, `.json5`, `.geojson`, `.yaml`, `.yml`) and known
    extensionless tool-config dotfiles (`.babelrc`, `.eslintrc`, `.prettierrc`, `.watchmanconfig`, ...), where
    `[]` or `{}` is legitimate content. Any other target fails closed. Accepted limit: a JSON config file outside
    those lists cannot be created as `[]`/`{}`. It retries and then fails; it is never silently written.
  - Also closed: a content-less `{"files": [...]}` envelope. `_unwrap_file_content_envelope` leaves it as text,
    and for a `.py` target it is a valid dict literal, the same exposure as `[]`. That function's docstring no
    longer claims a STRUCTURAL CORRUPTION gate catches it.
  - `_fill_missing_content` applies it to every non-edit entry, so it covers CREATE_FULL_FILE,
    REPAIR_WITH_FULL_FILE and a retry's `FILE CONTENT:` block. A match drops the content and sets
    `protocol_error` plus `protocol_reason_code = FILE_LIST_PROTOCOL_ANSWER_AS_CONTENT`.
- `kriya/workflow/attempt.py`: the existing operation-contract check (which already rejects `protocol_error`
  before attribution or any write) now carries `diagnostics.reason_code` from the entry, so the failure is
  typed. It is deliberately not in `_DETERMINISTIC_VERDICT_REASON_CODES`: this is model output, and a resampled
  retry can genuinely return real content.
- `kriya/workflow/operations.py`: `all_results_are_no_change()` never counts an entry with `protocol_error` as
  a no-change assessment. Its only caller already runs after the contract check; this makes that hold whatever
  the call order.
- `docs/design.md`: paragraph after "Repair protocol boundary".

**Not changed:** the batch JSON path in `DeveloperAgent.run_generation`. There, content comes from an entry's
own `content` field, not from raw text standing in for a file. The subtask executor's MODEL path serves only the
non-mutating shadow run and never writes.

**Evidence (plain-Python smoke; pytest is the user's):**
- `tests/test_developer_file_list_answer_as_content.py`: 32 cases pass under a manual runner.
  - The detector: 8 protocol shapes, 8 non-protocol contents, 6 data-format exemptions.
  - The producer: `[]`, fenced `[]` and `{"files": []}` under REPAIR_WITH_FULL_FILE; `[]` under
    CREATE_FULL_FILE; `FILE CONTENT:\n[]` on a retry; an envelope with real content still unwrapped;
    `data.json` = `[]` kept.
  - The contract: typed error; never a no-change assessment.
  - End to end on both paths, with a real workflow, real gates and the real commit seam:
    - `calc.py` bytes unchanged;
    - a typed `operation_contract` failure;
    - no `COMMITTED` cycle;
    - no RunRecord SUCCESS.
    The paths are a direct `generate` (the Developer's first call is `MODE: REPAIR_WITH_FULL_FILE`) and a
    milestone sequence.
- Negative control: with the detector stubbed out, both end-to-end tests fail. The milestone run reports
  `success`, which is exactly the defect.

**Regression checks (grep and replay):**
- The only reader of `Failure.diagnostics["reason_code"]` is the deterministic-verdict gate (`attempt.py`). The
  new code is not in its set, so no routing changes.
- The only existing test whose Developer answers `[]` (`test_predetermined_architect_files_deliberately_empty_...`)
  takes the batch path. Replayed with and without the detector, it gives identical results.
- The `sanitize_generated_content` envelope tests call a function this fix does not change.

**Pytest (user):**
```
.venv/bin/pytest tests/test_developer_file_list_answer_as_content.py tests/test_agents.py tests/test_retry_policy.py tests/test_workflow.py tests/test_workflow_controller_enforce.py tests/test_prd008_s4b_milestone_completion.py tests/test_prd008_s4c_milestone_resume.py tests/test_milestones.py
.venv/bin/pytest
```
