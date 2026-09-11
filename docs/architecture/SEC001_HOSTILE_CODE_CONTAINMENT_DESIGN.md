# SEC-001 — Hostile-Code Containment: Design Investigation

**Status: DESIGN_INVESTIGATION, no implementation.** Drafted 2026-09-11
from the `kriya-v0.9.0-demo-validated` baseline (code state `740ddfd`,
reconciliation commit `8524739`, pushed to `origin/milestone-decomposition`
before this investigation began). Governed by
`KRIYA_PRODUCTION_RISK_REGISTER.md`'s `SEC-001` row (currently
`NEEDS_IMPLEMENTATION`, E1) and `KRIYA_V1_DEPLOYMENT_ENVELOPE.md` §7
(DE-06). No production code changed by this document.

---

## 1. Execution surface inventory

Traced directly from source this session (every row below is a real,
current call site, not inferred). `caller → command source → subprocess
primitive → cwd → env → network requirement → filesystem requirement →
existing policy → containment`.

| Caller | Command source | Primitive | cwd | env today | Network need | FS need | Existing policy | Containment today | Class |
|---|---|---|---|---|---|---|---|---|---|
| `validate.py::_run_cmd_with_timeout` (compile: `mvn clean compile`, `python compile()`) | Kriya-constructed argv (stack detection) | `ProcessController.run()` (process-group session, kill-tree on timeout) | isolated git worktree (`REPO-001`) | allowlist+rlimit if `sandbox_execution` (default **True**) | dependency resolution only (first run / cache miss) | target repo tree | `_audit_run_command` — audit-only except `COMMAND_SUDO_DENIED` hard-deny | env filter + best-effort CPU/mem rlimit; **zero FS/network confinement** | BUILD_TOOL running TARGET_CODE/GENERATED_CODE (Maven plugins, `setup.py`/build hooks execute arbitrary code at this step) |
| `validate.py::_run_cmd_with_timeout` (test: `mvn test`, `pytest`, `bundle exec`) | same | same | same | same | same (transitive test deps) | same | same | same | same — **also directly runs pre-existing and generated test code** |
| `validate.py::_ensure_project_venv` (`pip install -r requirements.txt`) / Ruby `bundle install --path vendor/bundle` | Kriya-constructed | same `ProcessController.run()` path | same | same | REQUIRED (package registry) | writes venv/vendor dir | audit-only `RUN_COMMAND` + separate audit-only `INSTALL_PACKAGE` check (`extract_install_package_target`) — **never enforced** | env filter + rlimit only | PACKAGE_MANAGER — `pip`'s own build backend / `setup.py` can run arbitrary Python at install time; classic supply-chain surface |
| `attempt.py` finite_command call sites (×3, `740ddfd`) → `service_runtime.py::_prepare_required_artifact` (`mvn package`) then `java -jar`/`-cp` | Kriya-constructed, targets the just-generated build artifact | same `ProcessController.run()` path, shared `build_subprocess_env_and_preexec()` | same | same | package build may need deps | writes/reads target artifact | same audit-only `_audit_run_command` | same | **highest-severity single surface**: this is literally executing the generated application's own `main()` with full user privileges |
| `service_runtime.py::start_managed` (managed-service runtime verification, e.g. Spring Boot run) | Kriya-constructed | `ProcessController.start_managed()` (same process-group session; caller owns teardown) | same | env/preexec_fn passed by caller, same profile when threaded | service may open outbound connections at will once running | same | none directly — inherits whatever the caller set | env filter + rlimit if threaded; **no outbound-network restriction on the running service** | GENERATED_CODE / TARGET_CODE, long-lived |
| `plugins/core_tools/__init__.py::ShellTool._run` | **model/caller-supplied string, shell-interpreted** | `asyncio.create_subprocess_shell` — raw, **not** `ProcessController` | `os.getcwd()` | allowlist+rlimit if `sandbox_execution` | unrestricted | unrestricted | `enforce_hard_invariants` (mode-independent, 5 hard-DENY codes) + REQUIRE_APPROVAL gate only under `mode=enforce` | env filter + rlimit only; **no timeout, no process-group kill-tree** (confirmed by reading the method — no `start_new_session`, no wrapped timeout around `communicate()`) | GENERATED_CODE / EXTERNAL_TOOL — **the single most dangerous primitive in the codebase**: full shell metacharacter interpretation of content that can originate from model output |
| `plugins/core_tools/__init__.py::GitTool._run` | fixed subcommand set (`status/diff/log/branch/commit/blame`); only `-m <message>` is variable, passed as one argv token | `asyncio.create_subprocess_exec` (no shell) | `os.getcwd()` | full inherited env, **no allowlist/rlimit applied** | none | repo working tree | `_check_git_destructive` catch-all, audit-only unless `mode=enforce` | none of the sandbox.py controls | TRUSTED_KRIYA_INFRASTRUCTURE-ish, but `commit`/`checkout`-class operations can trigger **repository-defined git hooks** — see finding below |
| `workflow/worktree.py` (worktree add/checkout/commit/clean, ×12 call sites) | fixed Kriya-internal git argv | `subprocess.run` directly (not `ProcessController`) | worktree/repo path | full inherited env | none | worktree | POL-001-P3's `KRIYA_INTERNAL_CONTROL_PLANE` recognizers for the two bootstrap shapes; other calls unaudited | none | TRUSTED_KRIYA_INFRASTRUCTURE — **but see hooks finding**: `git checkout -f`/`git clean -fd`/`git commit` all run without hook suppression |
| `skills/skill.py` (`git add`/`git commit` in the skills repo) | fixed Kriya-internal argv | `subprocess.run` | skills dir | full inherited env | none | skills dir | none | none | TRUSTED_KRIYA_INFRASTRUCTURE, same hooks caveat |
| `analyzer/analyzer.py`, `cli.py:1964`, `workflow/checkpoint.py` (git status/diff/log/rev-parse/ls-files) | fixed, read-only git argv | `subprocess.run` | repo path | full inherited env | none | read-only | none | none | TRUSTED_KRIYA_INFRASTRUCTURE, read-only plumbing — no hook trigger |
| `workflow/toolchain.py` (`java -version`, etc.) | fixed argv | `subprocess.run` | n/a | full inherited env | none | none | none | none | EXTERNAL_TOOL, read-only fact-gathering |
| `mcp/mcp.py::MCPClient.start` | **user-configured** command/args/env from `kriya.yaml` | `asyncio.create_subprocess_exec`, `env={**os.environ, **self.env}` (**full inheritance, confirmed**) | inherited | full inherited + config env | whatever the server does — unrestricted | whatever the server does — unrestricted | none | **none at all** — no allowlist, no rlimit, no process-group session visible | MCP — worse-contained than ShellTool; the binary itself may be a legitimate trusted tool or an arbitrary third-party executable, Kriya cannot tell |
| `tools/lsp.py::JdtlsClient.start` | Kriya-selected binary (`shutil.which("jdtls")`) | `asyncio.create_subprocess_exec`, env = inherited minus `JAVA_HOME` | project root | full inherited (minus JAVA_HOME) | none needed | reads target repo source for indexing | none | none | LSP / TRUSTED_KRIYA_INFRASTRUCTURE (Kriya picks the binary) but parses arbitrary target-repo source — lower priority, not zero |

**Count: 14 distinct caller/primitive combinations across 10 files**, reducible to 3 underlying subprocess primitives (`ProcessController.run`/`start_managed`, raw `subprocess.run`, `asyncio.create_subprocess_exec`/`_shell`) plus one shell-interpreting outlier (`ShellTool`).

**Two findings not previously named as risks, surfaced by this inventory:**

1. **Git hooks are an unaddressed hostile-code execution vector.** `GitTool.commit`, and `worktree.py`'s `git checkout -f`/`git clean -fd`/`git commit` (bootstrap and reset paths), all run against a working tree that can contain repository-defined hooks (`.git/hooks/pre-commit`, `post-checkout`, etc.) — either pre-existing in a compromised/adversarial target repo, or written by a prior hostile generation attempt. None of these call sites set `core.hooksPath=/dev/null`, `--no-verify`, or `GIT_TEMPLATE_DIR`/hook-disabling env. This is in scope for SEC-001 (DE-06 names "build scripts" and "pre-existing repository tests" explicitly; a git hook is the same class of thing) and is cheap to close independent of any sandboxing mechanism — see Task 11, SEC-001-P1.
2. **`ShellTool` has no timeout or process-group isolation**, unlike every `ProcessController`-mediated path. A hung or deliberately-infinite-looping shell command blocks indefinitely with no kill-tree. This is a resource-containment gap (Task 8) layered on top of the FS/network gap, in the single most dangerous execution primitive in the codebase.

---

## 2. Threat model

Evaluated against `KRIYA_V1_DEPLOYMENT_ENVELOPE.md` §7 (DE-06), which
names filesystem, process/subprocess trees, network, environment
variables, credentials/secrets, package installation, tool/MCP authority,
and CPU/memory/time/disk/host resource limits as all REQUIRED — not
optional hardening.

**MUST CONTAIN for v1** (directly named or directly implied by DE-06, and
directly exercised by the execution surfaces above):
- Write outside workspace.
- Read outside workspace (specifically: SSH keys, cloud credentials,
  `~/.aws`, `~/.ssh`, other repositories on the same host, Kriya's own
  source tree).
- Read arbitrary environment variables (the `sandbox_env_allowlist`
  mechanism already exists and handles this for the paths it's wired
  into — not yet MCP, not yet `ShellTool`'s missing timeout companion
  gap, not yet GitTool/worktree).
- Excessive CPU / memory (rlimit exists, already wired for
  `PolymorphicValidator`+`ShellTool`; advisory-only for memory on macOS —
  documented limitation, not a gap to silently paper over).
- Wall-clock timeout + surviving child process (`ProcessController`
  already does this for its own callers; `ShellTool` does not).
- Arbitrary outbound network access during execution/test/runtime-verify
  phases (currently completely unrestricted everywhere).
- Persistence after Kriya exits (a detached/double-forked child, a cron
  entry, a launchd plist) — not currently addressed anywhere.
- Modify Kriya's own source or other repositories on the host.

**SHOULD CONTAIN for v1** (real DE-06 concerns, lower immediate severity
or harder to fully close without broader work):
- Localhost / Ollama / MCP endpoint access from executed target/generated
  code (distinct from Kriya's own process's legitimate Ollama access —
  see Authority Model, network dimension).
- Cloud metadata endpoints (`169.254.169.254` etc.) — relevant mainly in
  CI/cloud-hosted contexts, less so on a developer's own Mac, but cheap to
  include once any network allowlisting exists at all.
- Fork/process-count explosion (rlimit `RLIMIT_NPROC` deliberately NOT set
  today — correctly, per `sandbox.py`'s own docstring: it's a per-EUID
  limit on Linux, shared across unrelated processes for the same user, so
  a low value has real collateral-damage risk; a per-process-group count
  cap needs a different mechanism, e.g. cgroups on Linux or a monitoring
  loop, not a naive rlimit).
- Excessive disk-write volume (distinct from CPU/memory; no existing
  control anywhere).
- Symlink/path-escape from within an otherwise-permitted write target.

**OUT OF SCOPE for v1** (per the envelope's own §8, or genuinely separate
concerns already tracked as their own rows — do not fold these into
SEC-001, per Invariant 13):
- MCP-specific environment isolation as its own hardening pass — tracked
  separately as `SEC-003`; this design's containment primitive should be
  *reusable* by SEC-003's eventual fix, but implementing SEC-003 itself is
  not this task.
- Package-installation/network-containment as a dedicated broker
  abstraction — tracked as `SEC-005`; this design should not require
  building that broker as a prerequisite (see Task 9 — POL-001 already
  proved direct call-site wiring is a viable alternative path).
- Fail-closed behavior under sandbox/policy *failure* specifically as its
  own certification — tracked as `SEC-002`; this design must *produce*
  the deterministic failure signal SEC-002 would consume, not itself
  close SEC-002.
- Multi-tenant / server-deployment isolation between concurrent unrelated
  users — explicitly out of scope per the envelope §8/§6.

---

## 3. Authority model

Not a single `network=true/false` switch — six independent dimensions,
because Kriya legitimately needs different authority for different
phases of the same run:

| Dimension | Values needed | Why not binary |
|---|---|---|
| Filesystem — workspace | read-write (the isolated git worktree, `REPO-001`/`REPO-002`) | already effectively scoped by worktree isolation; containment adds enforcement, not new scope |
| Filesystem — dependency cache (`~/.m2`, `pip` cache, `~/.gradle` eventually) | read-write during a dedicated acquisition phase; **read-only** during execution/test/runtime-verify | a hostile test that can write into the shared Maven/pip cache can poison every future run on the host, not just this one |
| Filesystem — temp | read-write, host-default temp dir or a per-run scoped one | build tools need scratch space; scoping it per-run also bounds disk-growth blast radius |
| Filesystem — everything else | denied | SSH keys, other repos, Kriya's own source, `/etc`, etc. |
| Network — denied | default during execution/test/runtime-verify phases | this is the phase DE-06 actually cares about protecting |
| Network — dependency-registry-only | Maven Central + configured mirrors, PyPI + configured index, during a **separate acquisition phase only** | see Task 7 — this is the load-bearing distinction the task asks for |
| Network — localhost | denied by default even from an otherwise network-enabled phase, **except** the specific port a managed-service verification probe is expected to bind (an explicit, narrow allow, not a blanket localhost pass) | Ollama/MCP endpoints and Kriya's own control-plane sockets must not be reachable from code Kriya is trying to contain |
| Environment | allowlist (exists, `sandbox_env_allowlist`) | already correctly modeled as a distinct dimension from network/filesystem |
| Process creation | allowed (build tools spawn their own children) but the whole tree is Kriya's single unit of containment/termination | matches `ProcessController`'s existing process-group model — extend, don't replace |
| Resources | CPU seconds, memory MB (exist); wall-clock timeout (exists for `ProcessController` callers, missing for `ShellTool`); disk-write cap (new) | |

**Dependency acquisition must be a separate authority from executing
untrusted target code.** This is the single most important authority-model
conclusion: today, a `mvn test` invocation resolves dependencies from the
network AND executes the target repo's tests AND runs its build
plugins/hooks, all under one undifferentiated subprocess with one
undifferentiated (currently absent) network posture. Splitting acquisition
(network-constrained, no target/generated code execution) from execution
(network-denied, dependency cache read-only) is the only way to grant
Maven/pip real dependency resolution without also granting a hostile test
or build hook arbitrary outbound network access — see Task 7.

---

## 4. Candidate mechanisms

| | A. macOS native (`sandbox-exec`) | B. Container (Docker/OCI) | C. Lightweight (rlimit/env/process-group, **existing**) | D. VM | E. Hybrid (host-side acquisition + contained execution) |
|---|---|---|---|---|---|
| Security boundary | Kernel MAC policy (Seatbelt) — real FS/network mediation per-process, no root needed | Full kernel namespace + cgroup isolation (Linux); on macOS, always VM-mediated underneath | Resource limits + env filtering only — **no FS/network boundary at all** | Strongest — separate kernel | Composes whichever backend (A/B/C) for the execution phase; acquisition phase uses plain, unsandboxed but network-scoped host execution |
| macOS support | Present, functional today (confirmed: `sandbox-exec` ships with macOS) but **deprecated since 10.12, no public replacement, private/undocumented SBPL profile language** | Docker Desktop/OrbStack/Podman all run a Linux VM under the hood on macOS (Apple Silicon: Virtualization.framework-backed) — real containers, but never native macOS containers | Full support (`resource` module, POSIX) | Same VM story as containers, heavier if hand-rolled (Lima/UTM) vs. Docker Desktop's managed one | Depends on chosen backend |
| CI/Linux portability | None — macOS-only mechanism | Excellent — identical OCI image runs unmodified on Linux CI runners | Full (rlimit is POSIX) | Good but heavier ops burden | Good, if backend choice is itself portable (container backend is; native backend is not) |
| Filesystem isolation | Real, profile-defined allow/deny | Real, bind-mount defined | **None** | Real | Real, for the execution phase only |
| Network isolation | Real, profile-defined | Real, network mode (`--network none` / custom) | **None** | Real | Real for execution; deliberately open (scoped) for acquisition |
| Process isolation | Per-process MAC, not namespace-based | Full PID namespace | Process-group only (kill-tree, no isolation of what the tree can see) | Full | Same as chosen backend |
| Resource isolation | Limited (Seatbelt isn't primarily a resource limiter) | Strong (cgroups: CPU/mem/pids/IO all real on Linux; macOS host still mediates via the VM) | rlimit CPU real cross-platform; memory (`RLIMIT_AS`) real on Linux, **advisory only on macOS** (documented in `sandbox.py` already) | Strong | Same as chosen backend |
| Environment isolation | Can be combined with existing `build_restricted_env` | Trivial — container env is whatever you pass, nothing inherited by default | Exists (`build_restricted_env`) | Trivial | Same as chosen backend |
| Dependency-cache compatibility | Straightforward — profile allows read-write to cache dir during acquisition, read-only during execution | Straightforward — bind-mount the cache dir with the right mode per phase | N/A (no FS enforcement to configure) | Straightforward | Straightforward, this is the design's whole point |
| Java/Maven compatibility | Real risk: JVM does a lot of dynamic file/socket access (JIT, class loading, GC); a too-strict profile can break legitimate Maven/JVM behavior in ways that are hard to predict without extensive testing | Excellent — this is exactly what CI already does for Java everywhere | Full compatibility (nothing is restricted) | Excellent | Same as chosen backend, mitigated by keeping the profile as permissive-within-workspace as possible |
| Python compatibility | Similar risk — `pip`/setuptools/venv creation touch many paths; profile must allow the venv dir, pip cache, etc. | Excellent | Full compatibility | Excellent | Same as chosen backend |
| Performance | Low overhead — process-level, no VM boot | Real overhead: even Apple Silicon's fast VM backends add real seconds to first-run cold start, plus per-container filesystem overlay cost on every build | None (current state) | Highest overhead | Backend-dependent; acquisition phase adds no VM cost since it isn't sandboxed |
| Operational dependency | None beyond macOS itself (but see deprecation risk) | Docker Desktop, OrbStack, or Podman must be installed and running — a **real new hard dependency** for a tool marketed local-first/zero-extra-install | None | Heaviest dependency | Depends on backend chosen for the execution phase only |
| Bypass risk | A generated Java/Python process that finds an undocumented SBPL escape (these exist historically, e.g. via XPC or certain syscalls) defeats the boundary silently unless profile changes are validated per macOS version | Kernel-namespace escapes are rare and high-severity CVEs when found, well-monitored ecosystem | Trivially bypassed — resource limits do not stop a malicious write/network call, only cap it | Strongest against bypass | As strong as the chosen backend |
| Failure behavior | Must fail closed per Invariant 9: if `sandbox-exec` is missing/incompatible (unsupported macOS version, profile rejected), refuse to run rather than fall back unsandboxed | Fails closed naturally if the daemon isn't running — an explicit, loud error, not a silent unsandboxed run | Already fails closed for the two controls it has (rlimit setrlimit failures are caught and logged, but do NOT block the run today — **this is itself a gap**, see Task 11) | Same as container | Composable |
| Implementation complexity | Medium — one profile-generation module, but real testing burden across macOS versions | Medium-high — image build/maintenance, mount/network mode plumbing, version pinning | Low — already built | High | Medium — orchestration layer over one of A/B/C, plus the two-phase split itself |

**Not evaluated as separately viable: “VM-level isolation” as its own
standalone answer (Task 4.D).** On Apple Silicon, a hand-rolled VM
(Lima/UTM/`vz` framework directly) offers nothing Docker Desktop/OrbStack
doesn't already package better, at higher implementation cost — folded
into "container" above as the practical way any VM boundary would
actually be consumed.

---

## 5. macOS reality check

**Verified live on this exact host** (macOS 26.6.2, build 25G83, M1 Max) —
not inferred from recall, per this project's own "verify at the source"
convention:

```
$ which sandbox-exec
/usr/bin/sandbox-exec

$ echo hi | sandbox-exec -p '(version 1)(deny default)(allow process-exec)(allow file-read*)(allow file-write-data (literal "/dev/stdout"))' /bin/cat
hi                                    # exit 0 — permissive profile accepted, ran normally

$ sandbox-exec -p '(version 1)(deny default)(allow process-exec)' /bin/cat /etc/hosts
[sandbox violation, no output]        # exit 134 — file-read correctly DENIED with no allow rule for it
```

The binary is present, accepts a hand-written profile without rejection,
and a `deny default` profile genuinely blocks an unauthorized file read on
this exact OS version — real, current, empirical evidence of a working
filesystem-containment boundary, not a claim about the mechanism in the
abstract.

**Conclusion: production-strength filesystem+network containment is
available directly on macOS today (`sandbox-exec`, confirmed working
above), but only through a mechanism Apple has publicly deprecated since
10.12 with no supported replacement for this use case, whose profile
language (SBPL) is undocumented/private and has changed across macOS
releases without notice historically.** This is a real, currently-working
primitive on this exact host and OS version — but it fails the "stable,
supported" bar a production-grade v1 certification would ideally want,
and per this task's own Invariant 10/Task 5 instruction, that limitation
must be stated plainly rather than designed around silently. (Network
containment specifically was not separately live-tested this pass — the
filesystem result is the load-bearing proof-of-mechanism; network-rule
SBPL syntax should be verified with the same discipline before
implementation, not assumed to work identically from this filesystem
result alone.)

**STOP-condition reading — since resolved by explicit product decision
(2026-09-11 design-correction pass, see Section 10):** the original draft
of this section surfaced a live ambiguity — the task's own STOP list
includes *"recommendation relies on an unsupported macOS primitive,"*
which a `sandbox-exec`-as-PRIMARY recommendation would trigger — and left
the choice of reading to a future reviewer rather than picking silently.
That choice has now been made explicitly, not inferred: **production-grade
SEC-001 closure relies on a supported OCI/container isolation backend.
`sandbox-exec` remains available only as an experimental/local backend —
real, verified-working (below), useful for local development ergonomics
and as evidence the containment *contract* is implementable — but it is
not, and will not become, the production security guarantee, and cannot
by itself satisfy a CLOSED disposition for `SEC-001`.** Section 10 reflects
this directly; the empirical verification below is retained as real
evidence the mechanism works, not as an argument for using it in
production.

No other macOS-native mechanism is practical for this use case:
- **App Sandbox** requires code-signing + entitlement provisioning
  designed for App Store-distributed apps — not a fit for a CLI tool
  spawning ad hoc subprocesses for arbitrary local projects.
- **Endpoint Security Framework** requires a system extension plus a
  restricted, Apple-approved entitlement — not obtainable for an
  open-source CLI a developer installs via `pip install -e .`.

Containers/VMs on macOS are never native — Docker Desktop, OrbStack, and
Podman on Apple Silicon all run a real Linux VM (Virtualization.framework-
backed) under the hood; "container-based isolation" and "VM-level
isolation" collapse into the same operational reality on this platform.
They provide the strongest, most standardized guarantee and the cleanest
CI/Linux portability story, at the cost of a genuinely new external
dependency this local-first tool does not have today.

**Therefore: a VM/container boundary is not strictly required to achieve
real FS/network containment on macOS** (sandbox-exec can do it), **but it
is required if "production-grade" is defined as resting only on stable,
publicly supported primitives.** Both statements are true; which one
governs is a product decision, not a technical one — surfaced explicitly
in the recommendation below rather than resolved unilaterally here.

---

## 6. Architecture fit

**Where containment belongs:** one new module,
`kriya/tools/containment.py` (name illustrative, not prescribed), owning
a single function analogous to `ProcessController.run()`'s own contract
but with a `ContainmentProfile` parameter — **not** a new orchestration
layer, not a new agent, not a `WorkflowEngine` change. `ProcessController`
is already the sole real chokepoint for the `validate.py`/
`service_runtime.py` execution family (Task 1's inventory confirms this —
every `ProcessController`-mediated call site already funnels through
`run()`/`start_managed()`). The narrowest integration point satisfying
"no untrusted execution bypasses containment" is therefore: **extend
`ProcessController` itself** to accept and apply a `ContainmentProfile`
(composing today's `env`/`preexec_fn` parameters, not replacing them),
and migrate `ShellTool` and `MCPClient.start` onto `ProcessController`
instead of their own raw `asyncio.create_subprocess_*` calls — this alone
closes the "ShellTool has no timeout/kill-tree" and "MCP has zero
sandboxing" gaps found in Task 1, independent of which containment
*backend* (sandbox-exec/container/lightweight-only) is eventually chosen.

**Which existing modules own which piece:**
- `kriya/tools/sandbox.py` — already the right home for the *lightweight*
  controls (env allowlist, rlimit); extend in place, don't replace.
- `kriya/tools/process.py::ProcessController` — becomes the single real
  executor all containment flows through, per Invariant 14 ("prefer one
  execution-control abstraction over scattered sandbox logic").
- `kriya/policy/execution.py::ExecutionPolicy` — stays exactly what it is
  today: the pre-execution DECISION layer (should this command run at
  all, under what mode). It does not become a containment mechanism
  (Invariant 5) and does not need to change shape — a
  `ContainmentProfile` is a *consequence* attached to an ALLOW/
  ALLOW_SANDBOXED decision, not a new `PolicyDecision` value. Concretely:
  `ActionType.RUN_COMMAND` requests already distinguish `ALLOW` from
  `ALLOW_SANDBOXED` (per the existing enum in `kriya/policy/model.py`) —
  **confirmed by direct grep this session, not assumed**: `PolicyResult`
  already carries a `requires_sandbox: bool = False` field
  (`kriya/policy/model.py:98`), and `execution.py:725-733` already returns
  `ALLOW_SANDBOXED`/`requires_sandbox=True` specifically for RUN_COMMAND
  requests matching the MA4.4 starter build/test allowlist — exactly the
  TARGET_CODE/GENERATED_CODE/BUILD_TOOL surface this design targets. The
  one real consumer found (`kriya/policy/filesystem.py:300`) currently
  treats `ALLOW_SANDBOXED` identically to `ALLOW` — the signal is already
  computed and already correctly scoped, just not yet acted on anywhere.
  The natural
  composition: `ExecutionPolicy.evaluate()` continues to decide
  ALLOW/ALLOW_SANDBOXED/REQUIRE_APPROVAL/DENY exactly as now;
  `ALLOW_SANDBOXED`/`requires_sandbox=True` becomes the trigger a caller uses to select a
  non-default (stricter) `ContainmentProfile` from `ProcessController`,
  while a bare `ALLOW` still gets the *default* profile (which, given
  DE-06's "must be treated as potentially hostile" framing, should itself
  already be a meaningfully-contained profile for TARGET_CODE/
  GENERATED_CODE callers — TRUSTED_KRIYA_INFRASTRUCTURE callers, like
  `worktree.py`'s bootstrap operations, opt into an explicit "trusted,
  uncontained" profile instead, the same way `AuthorizedFileWriter`
  already carries its own distinct trust class).
- **Trusted vs. untrusted, concretely:** the Task 1 classification column
  is the authority. `TRUSTED_KRIYA_INFRASTRUCTURE` call sites
  (`worktree.py`, `skills.py`, `analyzer.py`'s read-only git plumbing,
  `checkpoint.py`) get the uncontained/trusted profile explicitly
  (fixed, Kriya-authored argv, no user/model/target-repo content beyond
  what POL-001-P3 already vetted for the two bootstrap shapes) —
  everything else (`validate.py`, `service_runtime.py`, `ShellTool`,
  `MCPClient`) defaults to a contained profile, opt-out only by the same
  kind of explicit, narrow, structurally-verified recognizer POL-001-P3
  already established for git bootstrap, not a blanket flag.
- **Audit vs. enforce interacts with SEC-001 differently than it does with
  POL-001.** POL-001's enforce mode gates *whether a command is permitted
  to start at all* (DENY/REQUIRE_APPROVAL). SEC-001's containment is
  orthogonal — it governs *what a permitted command can do while
  running*, and per Invariant 9 must default to fail-closed independent
  of `execution_policy.mode`: containment is not itself a `POL-001`-style
  audit/enforce toggle, because DE-06 states plainly "fail-closed behavior
  under policy or sandbox failure is itself REQUIRED," not opt-in. A
  config escape hatch may still exist for advanced users (analogous to
  `runtime_profile: legacy`), but the packaged default must contain, not
  merely audit.
- **How containment failure becomes deterministic workflow evidence:** the
  same pattern `kriya/workflow/verification_contract.py`'s
  `ContractVerdictState` already established for VER-006 — a
  `ContainmentResult` (real result, real telemetry, not a boolean) with an
  explicit state distinguishing "ran contained, succeeded/failed
  normally" from "containment mechanism itself unavailable/failed" (the
  latter must propagate as its own `failure_category`, analogous to
  `generation_budget_exhausted`'s own precedent from the post-demo
  reconciliation — never silently collapsed into an ordinary compile/test
  failure, and never silently treated as "ALLOW, proceed unsandboxed").

**Can validator/tool/MCP execution converge on one primitive?** Yes for
the *executor* (`ProcessController`, extended) — no for the *containment
profile itself*, which must differ by classification (TARGET_CODE/
GENERATED_CODE vs. TRUSTED_KRIYA_INFRASTRUCTURE vs. MCP's distinct
"arbitrary third-party binary, unknown trust" case) per Task 1's own
finding that not every subprocess needs identical containment.

---

## 7. Dependency resolution

**This is the design's central practicality question**, per the task's
own framing, and the answer is the two-phase split named in the Authority
Model above.

**Maven.** `mvn` resolves dependencies transitively and can execute
arbitrary plugin code at multiple build phases (not just `test` — a
`maven-antrun-plugin` or a custom plugin bound to `validate`/`compile` can
run before Kriya's own compile/test distinction even applies). This means
a clean acquisition/execution split for Maven cannot simply be "run `mvn
test` twice, once with network and once without" — plugin execution and
dependency resolution are interleaved by Maven's own lifecycle. The
practical pattern: run `mvn dependency:go-offline` (a real, standard Maven
goal designed exactly for this — populate `~/.m2` for every declared
dependency without running the project's own build lifecycle/plugins)
during the network-constrained acquisition phase, then run the real
`compile`/`test`/`package` goals with `mvn -o` (offline mode, already used
elsewhere in this project's own workflow per prior session history) during
the network-denied execution phase, with `~/.m2` mounted/restricted
read-only. `dependency:go-offline` itself still executes as a Maven
plugin (trusted, Apache-maintained, not target-repo-authored) — real but
meaningfully lower risk than executing the target project's own
plugins/tests. Gradle (`TOP-001`, not yet supported) has an analogous
`--offline` mode and network-only dependency resolution should follow the
same pattern once Gradle support exists — noted for future consistency,
not designed further here (out of scope, Gradle isn't in v1 yet).

**Python.** `pip install` for a `requirements.txt`/`pyproject.toml`
dependency set can run arbitrary code via `setup.py`/PEP 517 build
backends (`build_ext`, custom `setup.py` commands) during acquisition
itself — meaning even the ACQUISITION phase for Python is not fully
"trusted metadata download," unlike a simple Maven JAR fetch. Mitigation
within the same two-phase shape: prefer `pip download`/`pip install
--no-deps --only-binary=:all:` where wheels are available (avoids running
arbitrary build backends entirely for most common packages), falling back
to a build-backend invocation only when a source distribution is
unavoidable — and even then, that fallback still belongs to the
network-constrained acquisition phase (contained by the SAME profile as
execution, just with dependency-registry network allowed), not treated as
automatically trusted. This directly answers the task's own instruction:
**dependency installation is not automatically trusted** — it gets the
acquisition phase's network allowance, not a free pass from containment
altogether.

**Package scripts/hooks (both ecosystems):** treated as GENERATED_CODE-
adjacent for containment purposes regardless of which phase they run in —
the phase split controls *network* authority, not whether these scripts
are contained at all.

**Practicality assessment:** real, but non-trivial engineering — this
should be its own implementation slice (see Task 11, SEC-001-P4), not
something the sandboxing mechanism gets "for free" once a `ContainmentProfile`
exists.

---

## 8. Resource containment (v1 minimum)

| Control | macOS | Linux/container runtime | v1 recommendation |
|---|---|---|---|
| Wall-clock timeout | Fully enforceable (already is, for `ProcessController` callers) | Same | Extend to `ShellTool`/`MCPClient` via the `ProcessController` migration in Task 6 |
| Process-tree termination | Real via process-group SIGKILL (`_terminate_tree`, already exists) | Same, plus cgroup-based kill is stronger if a container backend is used | Extend the existing mechanism to every migrated caller — no new primitive needed |
| CPU | Real (`RLIMIT_CPU`, already exists) | Real, plus cgroup CPU shares/quota with a container backend | Keep existing rlimit; make a `setrlimit` failure itself fail-closed rather than logged-and-ignored (see Task 11, SEC-001-P2 — a real, small, currently-missed gap) |
| Memory | **Advisory only** (`RLIMIT_AS` weakly enforced by XNU — already documented in `sandbox.py`) | Real (`RLIMIT_AS` or cgroup memory limit) | Document as a known, accepted macOS limitation for the lightweight backend; a container backend closes this gap for users who opt into it |
| Child-process count | Deliberately not rlimit'd (correct — see Threat Model, SHOULD CONTAIN) | cgroup `pids` controller is a real, correct mechanism | SHOULD CONTAIN, container-backend-only for v1; not closeable cleanly with rlimit alone on either platform |
| File/disk growth | No existing control on either platform | `tmpfs`/quota-backed volume with a container backend; du-based polling loop as a lightweight-only fallback | SHOULD CONTAIN; lightweight-backend version is a real but imperfect polling-based cap, not a hard kernel limit |

---

## 9. SEC-005 reconciliation

**Result: `DIFFERENT_SCOPE_NO_CONFLICT`.**

Read `docs/assurance/KRIYA_V1_RISK_PRIORITIZATION.md` and
`KRIYA_V1_CRITICAL_PATH.md` in full this pass (both dated 2026-09-08,
predating both POL-001's actual closure on 2026-09-10 and the demo). They
are the real source of the `SEC-005 → POL-001 → SEC-001 → ...` chain
quoted in the prior reconciliation task — and at the time they were
written, **both correctly show `SEC-005` as `NI` (NEEDS_IMPLEMENTATION),
never closed.** `KRIYA_V1_CRITICAL_PATH.md`'s own `SEC-005` entry:
*"Current mechanism: none — `tools/process.py`/`tools/web.py`/
package-resolution logic exist but aren't broker-mediated... Blocking
dependencies: none among open risks (implies building `KRP-014`..."*
— i.e. `SEC-005` names a specific, never-built architectural artifact
(`KRP-016`, "Subprocess/package/network broker," built on shared scaffolding
`KRP-014`), and the chain expressed a *planned build order*
("build the broker first, then wire `POL-001` through it"), not a claim
that either was already done.

`POL-001` then actually closed on 2026-09-10 (register §7, read in full
this session) — but by a **different path than planned**: direct,
call-site-by-call-site `ExecutionPolicy` consultation wired into
`GitTool`/`ShellTool`/`AuthorizedFileWriter`/the Stage 2A INSTALL_PACKAGE
gate/`worktree.py`'s two bootstrap shapes, never through a centralized
broker. `SEC-005`'s own defined scope (a dedicated subprocess/package/
network broker) was never built, and closing `POL-001` never required it.

**No register drift, no incorrect prior closure exists to correct.**
Nothing in the authoritative register or its own dated planning
predecessors ever recorded `SEC-005` as closed. The prior reconciliation
task's premise ("previous project state treated SEC-005 as CLOSED") does
not match any artifact found — it appears to have conflated "the chain's
first two links both resolved" with "`POL-001`'s real, narrower-scoped
closure." The two risks are genuinely different scopes (decision-gating
reachability vs. a broker-mediated containment/authority abstraction) that
do not conflict, and `POL-001`'s real path is itself evidence the broker
was not, in fact, a hard prerequisite.

**Relationship to `SEC-001`:** the 2026-09-08 chain places `SEC-001`
downstream of `POL-001`, not directly of `SEC-005` — and no row in the
current register states a technical dependency from `SEC-001` onto
`SEC-005` (confirmed by direct grep, same finding as the prior
reconciliation pass). Since `POL-001` is now genuinely closed, and closed
without the `SEC-005` broker, `SEC-001` is unblocked to proceed
regardless of `SEC-005`'s own status — this design does not require
`SEC-005`'s broker as a prerequisite, consistent with Invariant 13's
instruction not to fold `SEC-005` into `SEC-001`'s implementation.

---

## 10. Recommendation

**AMENDED 2026-09-11, by explicit product decision (design-correction
pass preceding implementation) — this supersedes the original PRIMARY/
FALLBACK framing below in place, not by deletion, so the reasoning that
led here stays visible.**

**Decision:**
1. Production-grade `SEC-001` closure will rely on a supported OCI/
   container isolation backend, not `sandbox-exec`.
2. `sandbox-exec` may remain available as an experimental/local backend —
   it is real and verified-working (Section 5), useful for local
   development ergonomics and as a concrete proof the `ContainmentProfile`
   contract is implementable — but it is explicitly **not sufficient for
   a `SEC-001` `CLOSED` disposition**, because it is deprecated/private
   with no committed future.
3. Containment-required execution must fail closed whenever no qualifying
   backend is available — this was already Invariant 9 in the original
   design and is unchanged; what changes is that `sandbox-exec` no longer
   counts as "qualifying" for production/closure purposes, only for
   experimental/local use explicitly opted into.

**Revised architecture (what this changes about Sections 6/9 above):**
the `ContainmentProfile` abstraction, the `ProcessController` extension,
and the two-phase dependency-acquisition split are all **unchanged** —
they were always backend-agnostic by design (Section 6's own point:
"the actual FS/network containment mechanism" is a pluggable backend
question, not baked into the profile contract). What changes is only
which backend is authoritative for closure: the OCI/container backend
(Section 4.B in the original survey) is now PRIMARY for production;
`sandbox-exec` (Section 4.A) is demoted to an explicitly-labeled
EXPERIMENTAL/LOCAL backend, selectable but never the default answer to
"is `SEC-001` closed."

- **Security properties guaranteed (OCI backend, production):** real
  kernel-level filesystem containment (bind-mount-defined), real network
  isolation (network-mode-defined, composing directly with the two-phase
  acquisition/execution split), real process (PID-namespace) isolation,
  real resource isolation (cgroups: CPU/memory/pids all real, unlike the
  macOS-advisory-memory limitation), environment isolation (trivial —
  nothing inherited by default), git-hook suppression (backend-independent,
  applies regardless).
- **Security properties NOT guaranteed even under the OCI backend:**
  resistance to a kernel-namespace-escape-class vulnerability (rare,
  monitored, but not zero); this design does not attempt seccomp-profile
  or capability-drop hardening beyond a container runtime's own secure
  defaults — a further hardening pass, not scoped here.
- **macOS implementation:** a container runtime (Docker Desktop, OrbStack,
  or Podman — whichever the host has) invoked as the `ContainmentProfile`
  backend; on Apple Silicon this is always VM-mediated under the hood
  (Section 5's own finding, unchanged) — a real, new external dependency,
  not zero-install, which is the explicit trade this product decision
  makes in exchange for resting on a supported primitive.
- **CI/Linux implementation:** the same backend, natively available on
  most CI runners already — no VM-mediation overhead there, strongest
  case for this backend.
- **Required external dependencies:** a container runtime, required for
  any containment-required execution to succeed at all once this ships —
  this is the real cost of the amended decision, explicitly accepted
  here rather than glossed over.
- **Performance implications:** real VM-boot/image-overlay cost on macOS
  (Section 4's own comparison table, unchanged); CI-acceptable elsewhere.
- **Developer UX:** `containment.backend: oci | sandbox-exec (experimental)
  | none` config knob; `oci` is the packaged default target once the
  backend is implemented (not in this design-correction pass — see the
  companion implementation work package), `sandbox-exec` requires
  explicit, clearly-labeled opt-in ("experimental, not sufficient for
  SEC-001 closure" surfaced in its own config docstring/CLI warning, not
  buried), `none` requires the loudest warning of the three (Invariant 9).
- **Migration path:** ship the `ContainmentProfile` contract and
  `ProcessController` extension first, backend-agnostic, with a minimal
  test/dummy backend proving composition and fail-closed semantics (no
  production backend yet — this is the companion implementation work
  package's actual scope); ship the OCI backend as the following,
  separate package; `sandbox-exec` may ship alongside as the
  explicitly-experimental option, never gating `CLOSED`.
- **Fail-closed behavior, restated under the amended decision:**
  containment-required execution with no qualifying (i.e. non-experimental,
  for production) backend configured/available must block execution
  deterministically — this is now the ONLY way `SEC-001`'s fail-closed
  invariant can be satisfied when a container runtime isn't present,
  since silently downgrading to `sandbox-exec` would itself be an
  unauthorized, undisclosed weakening of the closure guarantee.

**CAN SEC-001 BE CLOSED ON MACOS WITHOUT CONTAINERS/VMs? NO, under this
amended decision** — this directly supersedes the original CONDITIONAL
answer below. `sandbox-exec` remains real, working evidence that the
`ContainmentProfile` contract is soundly implementable (Section 5), but
by explicit product decision it is not an acceptable production
closure mechanism. A container/VM backend is therefore required for
`SEC-001 → CLOSED`, on any platform, including macOS.

---

**Original PRIMARY/FALLBACK framing (2026-09-11, pre-correction) — kept
verbatim below for provenance; superseded by the amendment above, not
authoritative:**

*PRIMARY (superseded): Hybrid two-phase execution over an extended
`ProcessController`, with `sandbox-exec` as the macOS enforcement backend
and a pluggable `ContainmentProfile` abstraction so a container backend
can be added later without a second sandboxing implementation — argued on
the basis of zero new local dependency and low overhead. FALLBACK
(now PRIMARY under the amendment): container-only (Docker/OCI) from the
start, argued as the right choice "if the product decision favors 'never
build on an unsupported API' over 'no new required dependency'" — that
product decision has now been made explicitly (see above), so this
FALLBACK is the operative recommendation going forward. Original
CONDITIONAL answer to "can SEC-001 close without containers/VMs":
CONDITIONAL, on the grounds that sandbox-exec genuinely provides the
DE-06-named properties — technically still true as a statement about the
mechanism, but no longer the governing answer once "sufficient for
CLOSED" is defined to require a supported backend, per the amendment.*

---

**Foundation work package IMPLEMENTED 2026-09-11** (this design's own
DESIGN_INVESTIGATION status now applies only to the production
containment BACKEND, not the foundation below it):

- SEC-001-P1 (git-hook suppression): DONE.
- SEC-001-P2 (fail-closed resource-limit setup): DONE, with one real
  correction found during implementation - `RLIMIT_AS` genuinely fails to
  set on macOS (not just weakly enforced once set, confirmed empirically);
  fails closed everywhere except that one platform-specific case.
- SEC-001-P3a (async-compatibility design decision): DONE - a thin
  `run_async()` sibling on `ProcessController` itself, sharing every
  security-relevant helper with `run()`.
- SEC-001-P3b (ShellTool/MCPClient migration): ShellTool DONE. MCPClient
  explicitly NOT migrated this pass (out of scope, preserves SEC-003
  ownership - see the implementation work package's own instruction).
- SEC-001-P4 (two-phase dependency acquisition split): NOT implemented
  this pass - out of this foundation package's scope.
- SEC-001-P5 (`ContainmentProfile` + backend): the CONTRACT and a dummy/
  test backend are DONE (`kriya/tools/containment.py`), proving
  composition and fail-closed semantics. The `sandbox-exec` backend
  itself is NOT implemented - per the amended Section 10 decision, that
  backend is now explicitly experimental/non-closing even once built, and
  building it was not requested by the foundation work package.
- SEC-001-P6 (OCI backend): NOT implemented - explicitly deferred, per
  both this design's own amendment and the implementation work package's
  own "do not implement the production container backend yet" instruction.

`validate.py`/`service_runtime.py` (the compile/test/finite_command
surface) were NOT migrated onto `ContainmentProfile` this pass - they
still call `ProcessController.run()` with the same raw `env`/`preexec_fn`
convention as before (now fail-closed on resource-setup failure, since
that fix is universal to `ProcessController`, but not yet
containment-profile-aware). This is a real, deliberate scope boundary of
the foundation package, not a silent gap - see that package's own
completeness-gate accounting.

## 11. Implementation decomposition (original decomposition, kept for reference)

Deliberately not a monolithic sandbox manager, per Invariant 14 and
Task 11's own instruction — each slice is independently shippable and
independently testable.

**SEC-001-P1 — Git hook suppression for Kriya-internal git operations.**
Objective: no git hook in a target/generated repository can execute as a
side effect of Kriya's own `worktree.py`/`GitTool`/`skills.py` git calls.
Modules affected: `kriya/workflow/worktree.py`, `plugins/core_tools/__init__.py::GitTool`,
`kriya/skills/skill.py` (append `-c core.hooksPath=/dev/null` or
equivalent to every constructed git argv). Invariant: zero behavior
change to any git operation's own result, only hook execution suppressed.
Deterministic tests: a fixture repo with a `post-checkout`/`pre-commit`/
`post-commit` hook that writes a sentinel file; assert the sentinel never
appears after the equivalent Kriya-internal operation, both before (red)
and after (green) the fix. Evidence target: E3 (deterministic, no live
model needed). Stop condition: any existing test asserting current hook
behavior would need to change (none expected — hooks are not currently
exercised by any test per this session's inventory).

**SEC-001-P2 — Fail-closed resource-limit application.** Objective: a
`setrlimit` failure inside `posix_resource_limits_preexec_fn` must
prevent the subprocess from running under `sandbox_execution: True`,
not silently proceed unlimited (today it's caught and logged only).
Modules affected: `kriya/tools/sandbox.py`, `kriya/tools/process.py`.
Invariant: `sandbox_execution: False` behavior is completely unchanged.
Deterministic tests: mock `resource.setrlimit` to raise, assert the
subprocess never starts (or starts and is immediately terminated) rather
than running unbounded. Evidence target: E3. Stop condition: none
expected — narrow, additive.

**SEC-001-P3a — `ProcessController` async-compatibility design decision
(design-only, precedes and gates P3b/P5).** `ProcessController` is
synchronous (`subprocess.Popen`/`communicate()`); `ShellTool` and
`MCPClient` are both async call sites. This is a real open question, not
a mechanical detail — three shapes are plausible (a thin
`asyncio.to_thread`-style wrapper around the existing sync
`ProcessController`; a parallel async-native implementation duplicating
the same timeout/kill-tree contract; or making `ProcessController` itself
async and adapting its two existing sync callers). Objective of this
slice: pick one, with its own small write-up, before any migration code
is written. Whichever shape is chosen becomes the actual integration
point Section 6/P5 assumes is a single chokepoint — **P5 explicitly
depends on this slice landing first**, not on a parallel track, since a
`ContainmentProfile` designed against the wrong shape would need
reworking. Stop condition: if none of the three shapes above turns out
clean (e.g. `preexec_fn`/rlimit composition behaves differently under
`asyncio.create_subprocess_exec` than under `Popen` in some
not-yet-identified way) — stop and treat this as its own review, not an
in-flight improvisation.

**SEC-001-P3b — Migrate `ShellTool`/`MCPClient` onto the P3a-chosen
primitive.** Objective: both callers get real timeout + process-group
kill-tree, closing the "no timeout, no kill-tree" gap found in Task 1.
Modules affected: `plugins/core_tools/__init__.py`, `kriya/mcp/mcp.py`,
and whatever `kriya/tools/process.py` addition P3a specified. Invariant:
existing stdout/stderr/exit-code contract for both tools unchanged from
the caller's perspective. Deterministic tests: a deliberately-hanging
shell command / MCP server, assert termination within the configured
timeout with no surviving process (checked via `os.kill(pid, 0)`/
`psutil`-style liveness check from the test harness, not just "the call
returned"). Evidence target: E3.

**SEC-001-P4 — Two-phase dependency acquisition/execution split (Maven
`dependency:go-offline` + `-o`; Python wheel-preferring `pip
download`/`--no-deps` + offline execution).** Objective: implement the
Task 7 pattern for the existing Java/Python validation paths. Modules
affected: `kriya/tools/validate.py` (compile/test command construction),
possibly `kriya/workflow/attempt.py` (sequencing — acquisition must
complete before the finite_command/managed_service execution phase
begins). Invariant: no change to compile/test *outcomes* for a project
whose dependencies are already cached (offline-mode behavior should be
transparent in the common case, matching this repo's own existing
`./mvnw -o` convention). Deterministic tests: a fixture project with a
dependency NOT already cached; assert acquisition succeeds
network-enabled and the subsequent execution phase then succeeds fully
offline (real, not mocked network state — a live-model-tier-style test,
per this repo's existing `live_model` marker convention). Evidence
target: E2 at minimum (real subprocess behavior), ideally E3+ once a
live/CI run confirms it against a real Maven Central/PyPI fetch. Stop
condition: if a target project's build plugins turn out to require
network access DURING the execution phase in ways `dependency:go-offline`
cannot pre-resolve (a plugin that fetches its own remote resources at
build time, not via the dependency mechanism) — stop and treat that as a
SHOULD-CONTAIN exception list, not silently widen the execution phase's
network posture back open.

**SEC-001-P5 — `ContainmentProfile` abstraction + `sandbox-exec` backend
(depends on P3a's shape being settled — see above).**
Objective: the actual FS/network containment mechanism from Sections 6/10.
Modules affected: new `kriya/tools/containment.py`; `ProcessController`
extended to accept a `ContainmentProfile`; `validate.py`/
`service_runtime.py` construct and pass profiles per Task 1's
classification (contained default for TARGET_CODE/GENERATED_CODE/
BUILD_TOOL/PACKAGE_MANAGER, explicit trusted-uncontained opt-out for
TRUSTED_KRIYA_INFRASTRUCTURE call sites only). Invariant: fail-closed
per Invariant 9 — backend unavailable/profile-rejected must prevent
execution, not degrade to unsandboxed. Deterministic tests: this is
where the Closure Design section's adversarial tests belong (see below).
Evidence target: E1 at implementation, E4 only after real adversarial
production-path validation (see Closure Design). Stop condition: if
`sandbox-exec` profile generation cannot be made compatible with real
Maven/pytest behavior without excessive per-project tuning (a genuine
risk named in Section 4's compatibility row) — stop and escalate to the
FALLBACK (container-only) architecture rather than shipping a profile so
permissive it provides no real boundary.

**SEC-001-P6 — Container backend (opt-in), CI wiring.** Objective:
second `ContainmentProfile` backend implementation, selectable via config,
default for CI. Modules affected: same `containment.py`, plus CI
workflow config (`.github/workflows/ci.yml`). Invariant: identical
`ContainmentProfile` contract as P5's backend — no caller-visible
difference beyond configuration. Deterministic tests: same adversarial
suite as P5, run against the container backend. Evidence target: E3+
(CI itself provides repeatable, portable evidence). Stop condition: none
expected, this is the lower-risk backend.

---

## Closure design — what would eventually permit `SEC-001 → CLOSED`

Per Invariant 2 ("do not change a risk disposition without concrete
evidence") and the established register convention (VER-006's own
closure-bar language is the template), closure requires **adversarial
deterministic tests plus production-path validation**, not helper-only
unit tests of the containment module in isolation. At minimum, a
deliberately hostile fixture (a generated "application" whose only
purpose is to attempt each of the following) run through the *real*
`WorkflowEngine`/`PolymorphicValidator`/`ProcessController` path — not a
standalone script calling `containment.py` directly — must prove:

- A write attempt to a sentinel path outside the authorized workspace
  fails, and the sentinel file does not exist afterward (independently
  confirmed via `os.path.exists()` from OUTSIDE the contained process,
  the same discipline Demo 04 already established for `AuthorizedFileWriter`).
- A read attempt against a sentinel secret file (e.g. a fake
  `~/.ssh/id_ed25519`-shaped fixture, never a real key) fails or returns
  no content — confirmed by asserting the hostile process's own captured
  stdout/stderr never contains the sentinel's known content.
- A sentinel environment variable, present in the real parent environment
  but not on the allowlist, is absent from the child's observed
  environment (the child prints its own `os.environ` for the test to
  inspect).
- An outbound network connection attempt (to a real, controlled test
  endpoint, not a production service) fails during the execution phase,
  and succeeds during the acquisition phase against only the
  registry-scoped destination.
- No child process (spawned by the hostile fixture attempting to
  daemonize/detach) survives after `ProcessController` reports
  termination — checked via PID liveness from the test harness itself,
  not trusted from the terminated process's own self-report.
- CPU/wall-clock/memory bounds are actually enforced — a fixture that
  spins or allocates past the configured limit is terminated within a
  bounded margin of the configured limit, not merely "eventually."

**Production-path requirement, explicitly:** at least one of these must
be exercised through a real `kriya generate`/`fix` run against a real
local model (same discipline as VER-006's own E2 controlled-validation
precedent) — a hostile *Developer-generated* file, not only a
hand-authored fixture — before `SEC-001` can move past `NEEDS_EVIDENCE`
toward `CLOSED`, matching this register's own standing "do not close from
self-tests alone" convention.

---

## Retrieval-relevance follow-up (recorded only, not designed/implemented here)

**Classification: NEW_RISK.**

Rationale, brief and non-distracting from SEC-001: the gap flagged in the
prior reconciliation (`kriya learn`'s untrusted-knowledge relevance —
"safe acquisition != relevant acquisition") is a distinct failure mode
from every existing `CTX-*` row (internal-repository retrieval) and from
the prompt-injection *safety* fencing `CLAUDE.md` already documents — it
concerns whether ingested external content is *correct/applicable* for
the library/version actually in use, a content-quality question with no
existing owner. `EXISTING_RISK_EXTENSION` was considered and rejected:
stretching `CTX-001`/`CTX-002`/`CTX-003`'s scope to cover an entirely
different data source (external, user-ingested `kriya learn` content vs.
internal repository/dependency-graph context) would blur what those rows
actually evidence today. Recommend a dedicated future pass add this as
its own row, following the same explicit-scope-bookkeeping precedent
`CORR-018`/`VER-006` already established — not created here, per the
prior reconciliation's own scope discipline.
