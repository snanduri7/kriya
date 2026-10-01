# PRD-002 Coding Agent Handover

## Status
READY_FOR_PYTEST_VERIFICATION

## Source identity
- Base target revision: e24ba04 (PRD-001).
- Final revision: commit containing this handover; obtain with git log -1.
- Kriya version: 0.1.0.
- PRD-001 user full-suite pass is recorded separately; its 13 skip reasons remain pending. This is not release acceptance of that dependency.

## Scope and reproduction
The canonical checkout already includes plugins/core_tools, pyproject.toml,
requirements.txt and .github/workflows/ci.yml. No duplicate subsystem was needed.
A real pre-change wheel omitted core_tools and default_config.yaml. The new
integrity checker rejects that wheel (before-integrity.json). The initial new
unit test collection also failed because no distribution verifier existed.

## Implementation
- Setuptools includes the existing plugins.core_tools namespace package and packaged default YAML. Plugin discovery/config authority are unchanged.
- MANIFEST.in includes source release build/lock/CI inputs, core tools, tests, skills and verification scripts.
- python -m kriya.distribution PATH checks a source directory, sdist or wheel without extracting/executing it; missing content yields nonzero and JSON evidence.
- scripts/verify_release.sh builds an sdist, builds its wheel, checks both, installs the wheel with the existing pinned dependencies in a fresh venv, runs pip check, then runs offline CLI smoke outside the checkout.
- CI executes that same release command on Python 3.14, matching the existing lock. Supported-version range CI is preserved. No dependency or alternate lock mechanism added.
- Smoke runs version/config/plugins/doctor with real plugin initialization. Doctor's network dependencies are mocked, with unexpected socket connections rejected.

## Changed files
Production/build: pyproject.toml, MANIFEST.in, kriya/distribution.py.
Tests/setup: tests/test_distribution_integrity.py, tests/_plugin_test_support.py,
scripts/release_smoke.py, scripts/verify_release.sh, .github/workflows/ci.yml.
Docs: README.md, handover/TASK_STATUS_TRACKER.csv, this handover,
handover/PRD-001_USER_TEST_REPORT.md, evidence/PRD-002 files.

## Coding-agent evidence
- Focused command: python -m pytest -q tests/test_distribution_integrity.py tests/test_plugins.py tests/test_plugins_command.py tests/test_cli_smoke.py
- Result: 84 passed, 0 failed, 0 skipped in 1.48s (Python 3.14.6).
- Configured Ruff checks passed for the new production, smoke and test modules.
- Build: python -m build --no-isolation; sdist and wheel integrity passed.
- Installed-wheel CLI smoke passed outside source checkout using an isolated installation target and the existing dependency interpreter. This is not claimed as a clean-dependency-venv test.
- Shell syntax and Git diff whitespace checks passed.
- Full project pytest and clean-venv dependency installation: handed to user, not run by coding agent. Hosted CI was not executed locally.
- Live test required: NO.

## User commands (from the target repository)
Use the environment in which your existing suite passes; the report identifies .newvenv.

```bash
.newvenv/bin/python -m pytest -ra tests/test_distribution_integrity.py tests/test_plugins.py tests/test_plugins_command.py tests/test_cli_smoke.py
KRIYA_PYTHON="$PWD/.newvenv/bin/python" bash scripts/verify_release.sh
.newvenv/bin/python -m pytest -m 'not live_model' -ra --junitxml=handover/PRD-002_full_pytest.xml
```

The build interpreter needs setuptools and build (build already occurs in the
existing requirements.txt lock). Clean-venv installation needs package-index
access. The release script prints its temporary evidence directory; return its
build.txt, integrity.jsonl, smoke.txt and pytest summary, including skip reasons.
Do not substitute an editable install for the wheel verification.

## Limitations and acceptance
Integrity checks prove required file presence, not cryptographic provenance or
complete runtime qualification. Doctor mocks do not prove model readiness.
No durable architecture rule changed; .eie/DECISIONS.md remains absent.
Implementation is prepared; production acceptance remains PENDING user evidence.

## Reopening addendum (2026-09-24, independent review)
Findings closed:
- **Unpinned build backend (req 4).** CI ran `pip install -r requirements.txt setuptools`. `setuptools==83.0.0`
  (the version local builds already use) is now pinned inside the existing pip-compile lock, in pip-compile's own
  `--allow-unsafe` format, and the lock header command now includes `--allow-unsafe` so a regeneration keeps it.
  CI installs from the lock only. No second lock mechanism.
- **Incomplete sdist (req 3, AC1/AC4).** `MANIFEST.in` used per-extension includes and silently dropped tracked
  test fixtures (`tests/incidents/fixtures/*.json`) and skill content (`rules.txt`, `*.java`, `*.xml`,
  `*.properties`). It now grafts `plugins/core_tools`, `scripts`, `skills`, `tests` whole.
  `kriya.distribution --source-root <checkout>` fails an sdist missing any git-tracked file under those trees
  (fails closed with a JSON error when the root is not a git checkout); `scripts/verify_release.sh` uses it for
  the sdist.
- Tests added: `test_sdist_missing_tracked_release_file_fails` (json/txt/java, parametrized),
  `test_sdist_with_every_tracked_release_file_passes`, `test_source_root_that_is_not_a_git_checkout_fails_closed`,
  `test_source_root_rejected_for_wheel`, `test_manifest_grafts_every_release_tree`.

Accepted / known limitations:
- The wheel installs `plugins/` as a namespace package in site-packages; another distribution shipping a top-level
  `plugins` package would share that directory. Renaming would change the user-visible `plugins.directory` default
  and the spec's non-goal forbids relocating core tools just for packaging; documented in README instead.
- **Lock regeneration is currently broken locally**: `pip-compile` (pip-tools 7.6.0, locked) crashes with
  `ImportError: cannot import name 'stdlib_pkgs'` under the venv's pip 26.2.1. The lock itself is valid (it installs
  and `pip check`s); only regenerating it needs a compatible pip-tools/pip pair. Owned by PRD-034 (CI environment).
- A dirty local checkout's untracked files under the grafted trees would enter a locally built sdist; the CI build
  runs from a clean checkout. The check proves nothing tracked is missing, not that nothing extra is present.

User verification:
- `.venv/bin/pytest tests/test_distribution_integrity.py tests/test_plugins.py tests/test_plugins_command.py tests/test_cli_smoke.py -rs`
- Release smoke (builds sdist+wheel, clean venv install, needs package-index access):
  `KRIYA_PYTHON=.venv/bin/python bash scripts/verify_release.sh` - expect every `integrity.jsonl` line
  `"passed": true` and the smoke log to finish without error.
