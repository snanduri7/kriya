# Asset inventory: `~/kriya-live-demo/` to Demo V2

Source: `~/kriya-live-demo/` (1.0 GB, not a git repository; measured 2026-10-14). Nothing was copied recursively.
Classes: **VERSION** (now in this directory), **MATERIALIZE** (rebuilt at prep time from an identity in git), **STALE** (do not copy), **LATER** (versioned when its scenario is selected/built).

| Source | Class | Where it went / why |
|---|---|---|
| `demo-03-brownfield/goal-problem-only.md` | VERSION | `scenarios/01-normal-coding/goal.md` (the maintained goal, which states the problem and does not give away the fix). `goal.md` (the older goal that names the missing call) is STALE for a demo of problem solving. |
| `demo-03-brownfield/config/generate.yaml` | VERSION (adapted) | `scenarios/01-normal-coding/config/kriya.template.yaml`: absolute paths replaced with a placeholder; header comments shortened. Validated against this baseline's `AppConfig` (verify step). |
| `demo-03-brownfield/reset.sh` | VERSION (adapted) | `scripts/reset_demo.sh`: same identity checks (commit, target-file hash), work area outside the repository. |
| `demo-03-brownfield/expected-evidence.md` | VERSION (adapted) | `expected/scenario-01.md`, rewritten to separate checked outcomes from model variance. |
| `demo-03-brownfield/baseline/` (2.2 MB clone) | MATERIALIZE | Snapshot of a third-party public project (`alexmanrique/spring-boot-application-example`) plus local additions; license unverified, so NOT vendored. Identity in `fixtures/brownfield-spring/fixture.env`. |
| `demo-03-brownfield/run-demo.sh` | STALE | Exits 2 ("superseded"); pointed at a mutable development checkout. |
| `demo-03-brownfield/run-demo-v4.sh`, `run-demo-production.sh`, `config/generate-production*.yaml`, `capture-production-evidence.py` | STALE | Bound to the frozen demo runtime (`ad5598d`), to `demo-runtime.sh`, and to a production profile whose requirements (containment, qualification, brownfield baseline) are out of scope for a development-profile demo. |
| `demo-03-brownfield/presenter.md`, `RUNBOOK.md`, `qualification.md` | STALE (reference) | Terminal-driven narration for a different runtime; the GUI flow is described in the scenario contract instead. |
| `demo-03-brownfield/{workspace,run,run-production,model-eval-001,evidence}` | STALE | Run output and a 15 MB working clone; build output and evidence stores are never versioned. |
| `demo-02-knowledge-acquisition/{goal.md,config/*.yaml,step-*.sh,learn-*.sh,presenter.md,RUNBOOK.md}` | LATER | Reference for Scenario 02 once a technology is selected. Not copied: the Artemis candidate failed its offline-reuse step (see Scenario 02 STATUS). |
| `demo-02-knowledge-acquisition/evidence/v4/LEARN-CLASSIFICATION.md` | referenced in place | The measured result that Scenario 02 must not be built on the Artemis narrative. |
| `demo-02-knowledge-acquisition/README.md` | STALE | Says "NOT rehearsed"; contradicted by `evidence/v4`. |
| `demo-01-skills/{skills/ignite-java17,goal.md,config}` | LATER | A pre-authored skill A/B, not acquisition. Versioned only if Scenario 02 adopts it as a fixture. |
| `demo-04-control/` (zero-model policy-enforcement demo, 88 KB) | NOT CARRIED | Outside the three Demo V2 scenarios. Still valid as a separate asset where it is. |
| `runtimes/`, `runtime-artifacts/`, `current`, `demo-runtime*.sh`, `build-demo-runtime.sh`, `promote-demo-runtime.sh`, `DEMO_RUNTIME*.{json,md}`, `*.zip` | STALE | Frozen virtualenvs, wheels and promotion tooling pinned to product `ad5598d` and Ollama 0.34.4. Demo V2 runs the checked-out product, verified against the baseline tag. |
| `demo-prep-v4/`, `model001-campaign/` | STALE | Campaign evidence, not demo assets. |
| `~/WorkingDirectory/.../Kriya-By-ClaudeCode/demo/Kriya_Capabilities_and_Live_Proof_v1.1_v2.pptx` | NOT CARRIED | Slide deck, untracked in a different checkout; the owner decides whether to version it. |

Not versioned anywhere in Demo V2: runtime clones, build outputs, virtualenvs, dependency caches, large logs, model files, evidence stores.
