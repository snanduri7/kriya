# PRD-001 Coding Agent Handover

## Status
READY_FOR_PYTEST_VERIFICATION

## Source identity
- Base revision: af6220ef9c683651912d1f13966ebd5d73124b6b
- Final revision: the commit containing this handover (obtain with git log -1).
- Kriya version: 0.1.0.
- Prepared in a writable checkout; target repository is unchanged because session filesystem permissions prohibit writes there.

## Scope implemented
- Added missing typing.Any import; no authority behavior changes.
- Corrected packaged audit/enforce documentation and reconciled historical risk-register claims without rewriting their history.
- Added eleven bootstrap/configuration proof tests, including fresh-process imports of all production modules, CLI dispatch, annotation evaluation on Python 3.14, undefined-name detection in configuration, and accepted/rejected modes.
- Existing audit default and disabled WorkflowController remain unchanged.

## Files changed
- Production: kriya/config/authority.py; kriya/config/default_config.yaml.
- Tests: tests/test_bootstrap_contract.py.
- Documentation: docs/assurance/KRIYA_PRODUCTION_RISK_REGISTER.md; this handover; handover/TASK_STATUS_TRACKER.csv.
- Verification setup: handover/run_PRD-001_verification.sh and handover/evidence/PRD-001/ logs.

## Pre-change reproduction
- Python 3.13 runpy.run_path('kriya/config/authority.py') raised NameError for Any.
- Python 3.14 focused regression: 1 failed, 9 passed in 3.16s before the fix (the static test was added subsequently).
- Python 3.14 direct imports/CLI already succeeded because annotations are lazy; get_type_hints reproduced the defect. Do not claim a CLI NameError was reproduced on this runtime.
- Evidence: before.txt and before-python313.txt.

## Tests run by coding agent
- Command: python -m pytest -q tests/test_bootstrap_contract.py tests/test_config.py tests/test_sec009_config_authority.py tests/test_cli_smoke.py
- Result: 132 passed, 0 failed, 4 skipped in 7.44s (Python 3.14.6, pytest 9.1.1).
- Skips: four existing SEC-009 subprocess CLI tests require .venv/bin/kriya, absent in the preparation checkout. These are not accepted as verified security coverage.
- Ruff 0.16.0: configured checks on authority.py, config.py and the new test all passed; F821 checks on those files passed.
- Python 3.13 authority module execution passed after the fix.
- Full project pytest: NOT RUN; user will execute it.
- No live model test required for PRD-001.

## Known limitations
- A broader exploratory ruff F821 scan found pre-existing unresolved type references in workflow/attempt.py, workflow/context_budget.py, and workflow/workflow_controller.py. These are outside this configuration slice and are not silently suppressed or reported as a clean project-wide lint result.
- Complete imports were tested on Python 3.14; Python 3.13 verification here was limited to the standalone authority module because the available project dependency environment uses 3.14.
- No production acceptance or independent verification is claimed until the user returns test evidence.
- .eie/DECISIONS.md is absent. No new architecture decision was introduced.

## User verification handoff
The user explicitly owns full-project pytest and all live verification, overriding the original independent-agent execution assignments.
1. Apply the supplied patch to codex/fix-demo1-attribution, then install the editable dev environment if needed: python3 -m venv .venv; .venv/bin/python -m pip install -e '.[dev]'. Use the repository's approved dependency setup if already configured.
2. Run bash handover/run_PRD-001_verification.sh focused. Confirm the four CLI tests actually execute.
3. Run bash handover/run_PRD-001_verification.sh full when performing full-project verification. The original program requires a full non-live Wave-0 gate after PRD-003 as well.
4. Return pytest.txt, pytest.xml, lint.txt and environment/revision records from the printed evidence directory. Review every skip; exit zero alone is not production certification.
5. No live command is needed for PRD-001. Later live-required tasks will have task-specific setup and handovers.

Production acceptance criteria: implementation and focused evidence prepared; final acceptance PENDING user verification. Later tasks remain NOT_STARTED.

## Reopening addendum (2026-09-24, independent review)
- Reopened because 11 F821 undefined names remained in production code and the guard covered 2 files only.
  All 11 were string/`__future__` annotations (lint + `get_type_hints` failures, not runtime import failures).
- Fix: `TYPE_CHECKING` imports in `kriya/workflow/attempt.py` (`ContextItem`, `EngineeringPlan`,
  `PolymorphicValidator`) and `kriya/workflow/context_budget.py` (`SourceDerivationCache`); `Iterable` added to
  `kriya/workflow/workflow_controller.py`'s typing import. No runtime behavior change.
- Guard: `test_config_has_no_undefined_names` replaced by `test_production_code_has_no_undefined_names`, which
  runs ruff F821 over every tracked `kriya/` and `plugins/` Python file.
- Evidence: `ruff check --select F821` over tracked production files -> "All checks passed!"; per-file total
  ruff findings did not increase (attempt.py 12->6, context_budget.py 15->12, workflow_controller.py 2->1).
- Out of scope, unchanged: ~430 pre-existing repo-wide ruff findings (mostly I001/F401) that keep the CI
  `ruff check .` job red - owned by PRD-034.
- User verification command:
  `.venv/bin/pytest tests/test_bootstrap_contract.py tests/test_config.py tests/test_sec009_config_authority.py tests/test_cli_smoke.py -rs`
  (confirm the four SEC-009 subprocess CLI tests execute, not skip).
