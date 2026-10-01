# Kriya v1 Deployment Envelope

**Status: APPROVED (2026-09-08).**
Drafted 2026-09-08 from product-owner decisions on DE-01 through DE-06.
This document states *target* product scope, not current implementation
status. A capability listed as REQUIRED here may be entirely unimplemented
today — that gap becomes a REQUIRED row in the Production Risk Register,
not a reason to narrow this envelope.

## 1. Purpose

This document is the single authoritative statement of what "Kriya v1
production-ready" means. It exists so that Risk Register population,
P-series scenario selection, and future KRP/architecture work all answer
the same question — "production-ready *for what*" — instead of each
implicitly assuming a different, undeclared scope. Nothing in the Risk
Register may set `Deployment relevance` without tracing back to a specific
decision in this document.

## 2. v1 supported technology scope (DE-01)

Kriya's core workflow semantics are intended to be language-neutral
long-term; this envelope does **not** authorize refactoring toward that
target now (see §11.9 architectural note below). For v1 *certification*
purposes, two language families are REQUIRED:

**Java** — REQUIRED
- Build systems: Maven REQUIRED, Gradle REQUIRED.
- Framework: Spring / Spring Boot REQUIRED as an explicitly certified
  framework family.
- Framework-neutral (non-Spring) Java repositories are a separate
  evidence question from Spring-specific evidence — do not assume one
  proves the other.

**Python** — REQUIRED
- Mainstream Python repository/package structures REQUIRED.
- Exact supported build/package/test/runtime tooling (pip/poetry/uv,
  pytest vs. unittest, setuptools vs. hatchling, supported Python
  versions, etc.) is **not yet determined** — must come from a source
  capability analysis of Kriya's actual `PolymorphicValidator` and
  related mechanisms during Risk Register population, then be recorded
  explicitly in a future revision of this document. Not inferred here.

**Explicitly OUT_OF_SCOPE for v1 certification** (not a statement that
Kriya can never support these — only that they are not required for v1):
JavaScript/TypeScript, Go, Rust, C/C++, C#, Ruby, other language families.

**Evidence independence rule:** Java evidence never transfers to Python
automatically, and vice versa. Every production capability (repository
discovery, build/project detection, dependency analysis, symbol
extraction, context construction, planning, architecture preservation,
write-scope authorization, generation, static/syntax validation,
compile/build validation, test discovery/execution, regression detection,
recovery attribution, repair, runtime verification, brownfield
preservation, dependency changes, single- and multi-module topology,
terminal correctness, safe termination) gets an **independent** E0–E5
evidence assignment per language in the Risk Register. A mechanism is
never marked E4 for Python merely because its Java path has E4 evidence.

## 3. Repository/topology scope (DE-02)

REQUIRED: single-module and multi-module/multi-package repositories, for
both certified language families.

OUT_OF_SCOPE unless explicitly added later: arbitrary monorepos, generated-
source pipelines.

Existing P7 Maven-reactor evidence is *candidate* evidence toward the
multi-module requirement, not an automatic satisfaction of it — needs its
own re-evaluation during Risk Register population, and covers Java only.

## 4. Autonomy model (DE-03)

REQUIRED: Kriya v1 must eventually qualify for **unattended autonomous
repository modification** — this is a target requirement, not deferred
hardening layered on top of a permanently-supervised product. Human
review/approval remains a real, necessary *operating mode* Kriya must
continue to support, but is not the permanent ceiling of v1 ambition.

Consequence: risks involving hostile generated code, authoritative
execution-policy enforcement, sandboxing, resource containment, tool
authority, repository concurrency, fail-closed degradation, and crash
recovery are REQUIRED for v1, not OPTIONAL or DEFERRED, regardless of
today's implementation state.

## 5. Agent tool capability (DE-04)

REQUIRED: authoritative, policy-mediated TOOL/MCP execution capability is
part of v1 — not MODEL-path-only.

This does **not** mean unrestricted package installation, shell execution,
network access, or environment access. Each of those remains a separately
governed capability, evaluated on its own in the Risk Register. Selecting
this option makes ToolBroker/ActionBroker/policy/MCP/sandbox design work
part of the REQUIRED v1 evaluation surface (KRP-014, 015, 016, 017, 018,
019, 020, 021 all become relevant, not optional roadmap items).

## 6. Deployment and concurrency model (DE-05)

REQUIRED: correct operation both interactively on a developer workstation
and non-interactively in CI/automation.

OUT_OF_SCOPE for v1: multi-user/server deployment, distributed
coordination, multiple concurrent authoritative mutation runs against the
same repository.

**Required v1 invariant regardless of the above:** Kriya must **safely
reject** a conflicting second authoritative mutation run against the same
repository/workspace. Supporting concurrent writers is out of scope;
*safely rejecting* them is REQUIRED. At minimum this covers: no silent
concurrent modification of the same workspace; deterministic rejection of
a second conflicting writer; deterministic handling of stale ownership
after abnormal termination; workspace/repository identity robust enough to
resist trivial path-alias bypass; ownership acquisition happens before any
unsafe mutation; ownership cleanup is crash-aware; unrelated repositories
are never unnecessarily blocked from concurrent operation. No mechanism
(pidfile, flock, or otherwise) is assumed sufficient — the Risk Register
must evaluate the actual risk and current implementation before a
mechanism is chosen. Not implemented under this document.

CI-specific requirements (non-interactive operation, deterministic exit
status, machine-readable result/reporting, environment/configuration
handling, artifact/log persistence, secret handling, bounded execution,
clean cancellation/termination) are REQUIRED evaluation targets — not
implemented under this document.

## 7. Security/threat model (DE-06)

REQUIRED: **hostile-code containment.** Code and executable artifacts
influenced by the LLM or by the target repository — generated source,
generated tests, pre-existing repository tests, build scripts, Maven/
Gradle plugins, Python package/build hooks, package-manager operations,
shell/process execution, TOOL subtasks, MCP servers/tools, runtime
verification targets — must be treated as potentially hostile, not merely
untrusted-but-probably-fine.

Because DE-03 requires unattended autonomy and DE-04 requires
authoritative TOOL/MCP capability, containment is REQUIRED (not optional
hardening) for: filesystem access, process creation and subprocess trees,
network access, environment variables, credentials/secrets, package
installation, tool/MCP authority, CPU/memory/execution-time/disk/host
resource limits, and crash-aware cleanup after termination. Fail-closed
behavior under policy or sandbox failure is itself REQUIRED.

No existing mechanism — subprocess execution, working-directory isolation,
environment filtering, containers, or the current policy objects,
individually or combined — is assumed sufficient by this document. Each
must be audited against this threat model during Risk Register
population, not assumed adequate and not assumed absent.

## 8. Explicitly OUT_OF_SCOPE for v1

- Languages beyond Java and Python (§2).
- Arbitrary monorepos and generated-source pipelines (§3).
- Multi-user/server deployment, distributed coordination, concurrent
  *supported* writers against the same repository (§6) — note the
  distinct REQUIRED safe-rejection invariant above.
- Any capability not named REQUIRED above is, by default, not yet in
  scope for v1 and should be marked DEFERRED in the Risk Register with an
  explicit reason, not silently omitted.

## 9. Unresolved product decisions (carried forward, not resolved here)

- Exact Python build/package/test/runtime tooling matrix (§2) — pending
  source capability analysis.
- Exact concurrency-rejection mechanism (§6) — pending Risk Register
  evaluation.
- Exact CI operating requirements (§6) — acknowledged REQUIRED,
  not yet detailed.
- Exact sandbox/containment mechanism and deployment-platform assumptions
  (§7) — pending Risk Register evaluation of current mechanisms against
  this threat model.
- Whether/when generated-source pipelines or monorepos might be added to
  a future revision of this envelope.

## 10. Definition of "Kriya v1 production-ready" within this envelope

Kriya v1 is production-ready when, for both Java (Maven + Gradle + Spring/
Spring Boot, and framework-neutral Java evaluated separately) and Python
(tooling matrix to be defined), for single- and multi-module/package
repositories, running on a developer workstation or in CI:

1. Every REQUIRED risk in the Production Risk Register (§4–7 above) is
   `CLOSED` — required mechanism present AND required evidence level met
   — or explicitly `DEFERRED` with product-owner sign-off narrowing this
   envelope, not silently left open.
2. Unattended autonomous modification is safe under the hostile-code
   threat model in §7, with evidence, not merely with a policy that
   claims it.
3. TOOL/MCP execution is policy-mediated and capability-scoped, with
   evidence, per §5.
4. Concurrent-writer rejection is proven, per §6.
5. No REQUIRED risk rests on evidence weaker than the level the Risk
   Register determines it needs (E-level requirements per risk, not a
   single blanket bar).

This definition supersedes any earlier informal "production-ready" framing
used before this document existed (including the ~7/10 supervised /
~4.5/10 unattended split discussed earlier in this project) — those were
estimates made without a declared envelope and should not be treated as
calibrated against it.
