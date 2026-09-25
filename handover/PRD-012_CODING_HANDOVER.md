# PRD-012 Coding Agent Handover

## Status
READY_FOR_PYTEST_VERIFICATION. This is batch 2 (PRD-011 + PRD-012), with one pytest stop for the whole batch.

## Scope
Production egress and MCP containment enforcement: deny-by-default, configuration-owned authority. Before any
code was written, each requirement was mapped to its existing owner. Most of the boundary already existed:
- SEC-005: shell package-manager network;
- SEC-006: registry-scoped acquisition;
- SEC-009: configuration authority;
- TOOL-002/003: MCP invocation and containment;
- LLM `egress_policy`.

PRD-012 closes the gaps that mapping and a network-site inventory found. It adds no broker and changes no
owner's decision logic, except where noted below.

| # | Requirement | Existing owner | Gap found → closure |
|---|---|---|---|
| 1 | Capability classes | `NetworkAuthority`, `MCPNetworkAuthority`, flags | No single vocabulary → `kriya/policy/egress.py::EgressCapability` plus a total `capability_for()`. Existing enums are not extended, because `prepare()` has no exhaustive guard. |
| 2 | Destination authority only from trusted config | SEC-009 fields, fixed platform hosts | **Embeddings ignored `local_only`**: repository code and goal text could go to a non-local `embedding.base_url` → `OllamaEmbeddingClient` requires `egress_policy` and refuses before any request (all 10 sites). **The doctors probed a refused LLM URL**, sending the API key → neither probes. A network-client inventory guard covers every site. |
| 3 | MCP contained when required | TOOL-003 P2; production forces it | Already closed. `explicit_destinations` remains the disclosed TOOL-003 STOP: recorded as refused, never approximated. |
| 4 | No shell/network workaround | SEC-005 | Under containment and production, **any non-package-manager shell command had UNRESTRICTED network** (reachable from plan TOOL steps via TOOL-001) → new `autonomy.shell_network` (SECURITY_AUTHORITY); production seals `denied`. **Required containment was skipped when `sandbox_execution` was off** → ShellTool is always contained then. |
| 5 | Persist decision, destination, authority source, containment identity | Policy telemetry was DEBUG logs only | → the `egress.authority` run event (every channel, every run, in `traces.db`), plus per-process `ProcessResult.egress` persisted in gate outcomes and the ShellTool result. |
| 6 | Prompt-injection fixtures, real network, live model | SEC-005/006 registry tests | → deterministic fixtures, real-Docker tests with positive controls, and a live canary test. |
| — | "Production network is deny-by-default" | — | **KnowledgeGuard sent goal-named library names to public registries by default** → production seals `knowledge.offline_mode: true`. |

## Behaviour changes
- **Default configuration:** unchanged for every channel except embeddings. Under the default `local_only`, a
  non-local `embedding.base_url` is now refused, as the LLM always was. The packaged default is localhost.
- **Production profile** gains two sealed fields: `autonomy.shell_network: denied` and `knowledge.offline_mode: true`.
  An explicit contradicting value is rejected at load.
- **Doctor:** plain `kriya doctor` no longer contacts a non-local LLM URL under `local_only` (it already reported it
  as an error).

## Files
- **New:**
  - `kriya/policy/egress.py`
  - `kriya/workflow/egress_authority.py`
- **Changed:**
  - `kriya/memory/vector.py`
  - `kriya/tools/process.py`
  - `kriya/tools/validate.py` (`execution_evidence`, generalized from PRD-011's `toolchain_evidence`)
  - `kriya/tools/knowledge.py` (`REGISTRY_METADATA_HOSTS`)
  - `kriya/config/config.py`
  - `kriya/config/authority.py`
  - `plugins/core_tools/__init__.py`
  - `kriya/workflow/workflow.py`
  - `kriya/workflow/attempt.py`
  - `kriya/cli.py`
  - `kriya/production_doctor.py`
  - `kriya/routing.py`
  - `kriya/analyzer/analyzer.py`
  - two spikes
- **Tests:**
  - `test_prd012_egress.py` (deterministic)
  - `test_prd012_egress_docker.py` (real network)
  - `test_prd012_network_inventory.py`
  - `test_live_prd012_egress_injection.py` (live)
  - `test_vector.py` (constructor argument)
- **Docs:**
  - user guide §2.0d
  - `CLAUDE.md` egress section

## Evidence (plain runner, no pytest; Docker and internet available locally)

| Suite | Result | Notes |
|---|---|---|
| `test_prd012_egress` | 25/0 | |
| `test_prd012_egress_docker` | 4/0 | Real network, each with a positive control |
| `test_prd012_network_inventory` | 2/0 | |

The real-network cases (each has a positive control proving the destination is reachable with authority):
- a production shell command cannot connect, while the privileged class does;
- a production `mvn` still reaches only its registry (200), and another host gets 403;
- a hostile plan's TOOL step cannot connect;
- a contained verification run cannot connect, while UNRESTRICTED does.

Mutation checks, each caught:
- `shell_network` ignored;
- the embedding gate disabled;
- the `sandbox_execution` gate restored;
- the production preset without `shell_network`;
- the doctor probe guard removed.

The prompt-injection fixtures:
- a hostile README, tested live with a canary;
- a pom `<repositories>` entry naming an attacker host: it never enters registry authority. Real-proxy denial of
  any other host is already proven by `test_sec005_shell_acquisition_network` and `test_containment_oci`;
- a plan TOOL step calling an attacker host, tested deterministically and against a real network.

## Live test (user's terminal; needs Docker and a local model)
```bash
set -o pipefail
mkdir -p handover/evidence/PRD-012/user-live
KRIYA_PRD012_EVIDENCE_DIR=handover/evidence/PRD-012/user-live \
KRIYA_LIVE_BASE_URL=http://localhost:11434/v1 KRIYA_LIVE_LLM_MODEL=qwen3-coder:30b \
.venv/bin/pytest -m live_model -ra -s tests/test_live_prd012_egress_injection.py \
  2>&1 | tee handover/evidence/PRD-012/user-live/egress-injection.log
```
- **The invariant:**
  - the host canary gets zero requests during a real-model run on a repository whose README tells agents to call it;
  - a container with network authority does reach the canary afterwards (the positive control);
  - the persisted `egress.authority` shows the shell, verification and registry-metadata channels denied.
- **Recorded as evidence, not asserted:** whether the model followed the injection.
- **Canary host:** `host.docker.internal` (Docker Desktop). On Linux, set `KRIYA_PRD012_CANARY_HOST` to the docker
  bridge gateway.

## Residuals (disclosed)
- **Default posture:** outside the production profile, `shell_network` defaults to `unrestricted`. That is SEC-005's
  documented behaviour, recorded explicitly as the privileged class. Changing the global default was deliberately
  out of scope; production seals it.
- **Operator-initiated fetches:** `kriya learn -u <url>` stays a deliberate operator action, not gated by
  `egress_policy`. Live lookup remains an explicit opt-in (APPROVED_PUBLIC_KNOWLEDGE), with sanitized terms and
  per-query approval.
- **Toolchain image pulls:** a missing versioned toolchain image may still be pulled by the Docker daemon from its
  operator-configured registry. The image names are Kriya-owned constants; `kriya doctor --production` reports a
  missing image. This is disclosed, not blocked.
- **MCP:** `explicit_destinations` remains TOOL-003's disclosed STOP.
