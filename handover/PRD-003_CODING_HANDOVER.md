# PRD-003 Coding Agent Handover

## Status
READY_FOR_PYTEST_VERIFICATION

## Source identity
- Base revision: 084fe7d (PRD-002).
- Final revision: the commit containing this handover; obtain with `git log -1`.
- Kriya version: 0.1.0.

## Scope implemented
- Added one callback-level `GenerateOutput` boundary. Once `generate --json`
  starts, it owns stdout restoration and emits exactly one terminal object.
- Preserved successful and failed WorkflowEngine and milestone result dictionaries
  without translating them into another result schema.
- Added fail-closed structured results for callback setup exceptions, workspace
  lock refusal, malformed milestone input, unreadable goal files, interrupted
  prompts, and other controlled/unexpected callback exits.
- Preserved human-readable mode and existing exit-code semantics: verified success
  0, ordinary/strict/environment failures 1, declined knowledge risk 3, interrupt
  130. Click parsing and parent configuration loading occur before the generate
  callback establishes JSON mode and are outside this task's boundary.

## Files changed
- Production: `kriya/cli.py`, `kriya/cli_output.py`.
- Tests: `tests/test_generate_json_contract.py`,
  `tests/test_live_generate_json.py`.
- Handover/evidence: this file, tracker row, `handover/evidence/PRD-003/*`.

## Pre-change reproduction
`tests/test_generate_json_contract.py` initially produced 5 failures and 4
passes. Strict and declined knowledge-gap exits, provider construction failure,
unreadable goal input, and a controlled workflow exception failed the one-JSON
contract. Evidence: `handover/evidence/PRD-003/reproduction-summary.txt`.

## Coding-agent tests
- `pytest -q tests/test_generate_json_contract.py tests/test_cli_smoke.py tests/test_file_goal.py tests/test_milestones.py`: 155 passed, zero skipped in 2.05s.
- `pytest -q tests/test_generate_json_contract.py tests/test_cli_smoke.py tests/test_knowledge.py tests/test_knowledge_extraction.py`: 117 passed, zero skipped in 245.40s.
- `pytest -q tests/test_generate_json_contract.py tests/test_file_goal.py tests/test_milestones.py`: 91 passed, zero skipped in 1.16s during an earlier adjacent run.
- Ruff configured checks passed for the new focused production and test modules.
  F821 passed for all touched files. Full-file Ruff on `kriya/cli.py` reports the
  same six pre-existing findings as the base revision; this change adds none.
- `git diff --check` passed. The live test collected successfully but was not
  executed by the coding agent.
- Full project pytest: not run; user owns that verification.

## Live-model verification setup
Required: YES. The test uses a disposable Git repository and trust store. It
requires a local Ollama-compatible endpoint and records provider version, actual
model artifact digests, source revision, effective redacted configuration and
digest, stdout/stderr, and exit code under pytest's temporary directory. It
requires evidence of at least one recorded LLM call, so a setup-only JSON error
cannot pass. It never accepts public endpoints.

Run from the target checkout using the environment that passed full pytest:

```bash
KRIYA_LIVE_BASE_URL=http://localhost:11434/v1 \
KRIYA_LIVE_LLM_MODEL=qwen2.5-coder:1.5b \
KRIYA_LIVE_EMBED_MODEL=all-minilm \
.newvenv/bin/python -m pytest -m live_model -ra -s tests/test_live_generate_json.py
```

Replace model names with the exact local runtimes you intend to verify. Preserve
the printed `PRD-003 live evidence:` directory before pytest removes temporary
files; using `--basetemp` is recommended:

```bash
mkdir -p handover/evidence/PRD-003/user-live
KRIYA_LIVE_BASE_URL=http://localhost:11434/v1 \
KRIYA_LIVE_LLM_MODEL=qwen2.5-coder:1.5b \
KRIYA_LIVE_EMBED_MODEL=all-minilm \
.newvenv/bin/python -m pytest -m live_model -ra -s \
  --basetemp=handover/evidence/PRD-003/user-live \
  tests/test_live_generate_json.py
```

## Independent user verification
Run:

```bash
.newvenv/bin/python -m pytest -ra tests/test_generate_json_contract.py \
  tests/test_cli_smoke.py tests/test_knowledge.py tests/test_knowledge_extraction.py \
  tests/test_file_goal.py tests/test_milestones.py
.newvenv/bin/python -m pytest -m 'not live_model' -ra \
  --junitxml=handover/evidence/PRD-003/user-full.xml
```

Return complete counts and every skip reason. PRD-003 must remain
`READY_FOR_PYTEST_VERIFICATION` until full pytest passes, then
`READY_FOR_LIVE_VERIFICATION` until the real-model evidence passes. Do not mark
`VERIFIED` from coding-agent tests alone.

## Known limits and decisions
- This task controls only `generate` callback output after `--json` is parsed.
  Click option errors and configuration-load errors happen before the callback
  knows JSON was selected and retain Click's existing output contract.
- Unexpected provider exceptions are represented without traceback or secrets in
  stdout; diagnostic narrative remains on stderr.
- No `.eie/DECISIONS.md` exists. No durable authority rule changed.

Production acceptance: implementation and setup prepared; full pytest and live
verification remain pending with the user.
