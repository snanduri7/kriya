# PRD-034 — Full Pytest and CI Production Certification: Design Analysis

**Wave:** 7. **Spec:** `tasks/PRD-034_Full_Pytest_and_CI_Production_Certification.md` (P0, Live test NOT REQUIRED, depends on PRD-033), plus the Wave 7 directive §6.

## 1. Existing CI (`.github/workflows/ci.yml`), analysed first

| Job | What it does | Gap against PRD-034 |
|---|---|---|
| `test` (3.10–3.14) | `pip install -e .[dev]`; `pytest -q` | Docker, JDK and Maven are not installed explicitly, so every Docker-dependent test (≈20 `skipif(docker …)` sites) can skip silently, and nobody sees it. No junit, versions or skip audit are archived. |
| `lint` | lock-file ruff | ok |
| `static-check` | lock-file pylint | ok |
| `lock-file` | pinned install, `pytest -q` | same silent-skip gap |
| `release-integrity` | sdist, wheel and clean-install smoke (PRD-002 `scripts/verify_release.sh`) | ok; archived |
| `live-model-smoke` | Ollama + `qwen2.5-coder:1.5b`; `pytest -m live_model` | `-m live_model` also selects every target-identity live tier (PRD-012/013/017/020/025/032/033 files default to `qwen3-coder:30b` and fail on the CI model). CI-scale wiring smoke and target-machine verification are not separated. |
| `nightly-live-model-matrix` | three small models | same selection problem |

Nothing runs the pinned static-analysis tier (STATIC-ANALYSIS-CI-LIVE-JOB-001). No job runs `doctor --production`. Nothing asserts a non-root user.

## 2. Environment semantics finding (evidence at PRD-031A closure)

Some tests exercise Kriya running `pip`/`python` resolved from PATH (`test_validate_policy_audit.py`, the Django run-command test). From a shell without the venv's `bin` on PATH they fail with `FileNotFoundError: 'pip'`. That makes evidence depend on the operator's shell.

**Fix: `tests/conftest.py` puts the directory of the interpreter running pytest (`sys.executable`) first on PATH for the session.** The environment under test is then the one that supplies `python`/`pip`. This makes the semantics explicit; it does not relax a test. The certification script also runs with the venv first.

## 3. Design

1. **Skip audit (conftest plugin).** Every skipped test is recorded with its nodeid and reason. In certification mode (`--certification` or `KRIYA_CERTIFICATION=1`), a skip is a **failure** unless it matches an entry in `tests/certification/skip_allowlist.yaml`. Each entry carries `nodeid` (a pattern), `reason`, `owner`, `expires` and `tier`; an expired entry no longer allows the skip. The allowlist starts with only the genuine platform cases (for example "symlink not supported on this platform"); Docker, JDK, Maven and scanner skips are **not** allowlisted in certification mode. `--certification-report DIR` writes `skips.json`, and pytest's own `--junitxml` gives the results.
2. **`scripts/certify.sh`: one canonical procedure, run locally and by CI.**
   - The venv's `bin` goes first on PATH.
   - A step fails when run as root; `KRIYA_CERT_ALLOW_ROOT` is honoured only for a job that tests root refusal, and none exists.
   - It records exact tool versions (python, pip freeze digest, java, mvn, gradle if present, docker client/server, semgrep, git, OS) to `environment.json`.
   - Then, in order:
     - ruff and pylint;
     - the deterministic suite in certification mode (`-m "not live_model and not live_static_analysis"`: every Docker/JDK test must execute);
     - the pinned-scanner tier `-m live_static_analysis` in certification mode;
     - the release clean-install smoke (`scripts/verify_release.sh`);
     - `kriya doctor --production --json` with a canonical configuration, archived.
   - It writes `certification-summary.json/.md` with the counts per stage, unexpected skips, versions and the doctor summary.
   - A stage that cannot run (missing Docker, scanner or JDK) is **UNAVAILABLE and fails the certification**. It is never silently degraded.
3. **CI (minimal change).**
   - A new `production-certification` job on `ubuntu-latest` (non-root runner user):
     - `actions/setup-java` Temurin 17 plus Maven;
     - Docker (present on the runner);
     - `pip install semgrep==1.178.0`;
     - `docker pull` of the pinned Semgrep digest;
     - `scripts/certify.sh`;
     - it uploads the certification directory.
   - This job closes STATIC-ANALYSIS-CI-LIVE-JOB-001.
   - The existing `test`/`lock-file` matrix jobs are kept, and they archive junit.
   - **Live separation:** a new marker `live_target` marks live tests that need the target production identity; the CI live jobs run `-m "live_model and not live_target"` (wiring smoke on the CI model). The target tiers run on the target machine (PRD-035). Live jobs never gate the deterministic jobs; they already are separate jobs.
4. **Doctor in the canonical environment.** No model is qualified in CI, so the `model.*` rows are expected to FAIL there. The summary records the full doctor JSON and gates only on its being produced and parseable. It never turns a FAIL into PASS; the PRODUCTION_READY value is reported as is.

## 4. Constraints and honest limits

- **Pushing is forbidden in this batch**, so the GitHub-hosted run cannot be executed from here. The canonical local run of `scripts/certify.sh` on the target machine (macOS arm64, Docker 27.5.1, JDK 17, Maven 3.9.16, Semgrep 1.178.0, uid 501) is the executed evidence. The hosted run is `NOT_EXECUTED (pending the user's push)`. This is not a CI service limitation, so it is not a hard stop.
- **Gradle** is not installed locally. Any test that needs it and skips shows up as an unexpected skip in certification mode. It is resolved by installing it or by an owned, dated allowlist entry, and the decision is disclosed.
- **No xfail, no blanket skip, no root, no relaxed assertion.**

## 5. Non-goals

- A hosted multi-OS matrix beyond the existing one.
- Live-model gating of deterministic truth.
- Qualifying models in CI.
