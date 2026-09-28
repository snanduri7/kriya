# Platform Architecture (ARCH-PLATFORM-001)

Date: 2026-09-28. Evidence: `PLATFORM_DEPENDENCY_AUDIT.md` (findings PLAT-nnn). Sequencing: `PLATFORM_MIGRATION_PLAN.md`.

## 1. Principle

Kriya core owns policy: workflow, authority, evidence, verification, recovery and commit semantics. Platform code owns mechanism only.

- **Port per concern.** Each mechanism the operating system decides sits behind one small, cohesive port. There is no single `PlatformAdapter` god-object.
- **Adapters grant nothing.** An adapter reports what it can do and how it did it. It never grants authority, and never decides whether something is allowed.
- **Capability-first.** Core code asks "is `PROCESS_TREE_TERMINATION` available?", never "is this Linux?".
- **One place for OS detection.** The OS is detected once, in the composition root.
- **Fail closed.** A required capability that is absent is a typed `UNAVAILABLE` outcome, never a silent downgrade and never a host fallback.
- **Wrap, don't duplicate.** Portable Python (`pathlib`, `os.path.join`, `tempfile`, `shutil.which`, `subprocess` argv lists) is used directly and not wrapped. A port exists only where Python's portable API is insufficient or its semantics differ by platform.

## 2. Current dependency map (as found)

```
workflow / policy / control / config / core      (policy + orchestration)
   │ direct: fcntl (control/run_ownership)                      PLAT-003
   │ direct: sys.platform=="darwin" (workflow/toolchain)        PLAT-015
   │ inline: ~17 lexical path-containment copies                PLAT-001/002/017/018
   │ inline: 9 copies of ~/.kriya resolution                    PLAT-028
   ▼
tools/process.py ── os.name branches ×3, killpg              PLAT-008
tools/sandbox.py ── resource.setrlimit, sys.platform ×2       PLAT-005/006/007
tools/containment*.py ── os.getuid, -v host:ctr (OCI)        PLAT-012/013
mcp/lifecycle.py ── os.name branch, rlimits                  PLAT-008
mcp/containment_adapter.py ── os.getuid (2nd copy of SEC-008) PLAT-012
plugins/core_tools ── /bin/sh -c on host                     PLAT-010
core/execution_environment.py ── platform.system() probe    PLAT-016
```

Two ports already exist and are reused, not duplicated:

- **`ContainmentBackend`** (`kriya/tools/containment.py`: `prepare(profile, command) -> PreparedContainment`, with `resolve_containment_backend`) **is** the `ContainmentRuntimePort`.
- **`ProcessController`** (`kriya/tools/process.py`) is the single spawn boundary. It becomes the only consumer of `ProcessControlPort` and `ResourceLimitPort`.

`InferenceRuntimePort` (INF-001) is the house style for a port: a Protocol, a registry, a tripwire test that no other module names a provider, and a contract suite.

## 3. Target component diagram

```
 Presentation (never touches OS primitives)
   CLI (kriya/cli.py) · REPL · future GUI / API / IDE / MCP front ends
        │
        ▼
 Application services (future; CLI semantics migrate here progressively — not in this scope)
        │
        ▼
 Kriya core (policy + orchestration; platform-neutral; no OS modules, no OS branches)
   workflow · policy · control · config · requirements · evidence · metrics · static_analysis.service
        │ depends on Protocols only
        ▼
 kriya/platform/                          ← the ONLY place OS detection + OS modules live
   services.py      PlatformServices (composition root, detect once, frozen)
   capabilities.py  PlatformCapability enum, CapabilityStatus, PlatformCapabilityUnavailable
   locking.py       WorkspaceLockPort      ─ PosixFlockLock │ (future) WindowsLockFileLock
   process_control.py ProcessControlPort   ─ PosixSessionControl │ (future) WindowsJobControl
   resource_limits.py ResourceLimitPort    ─ PosixRlimit │ (future) WindowsJobLimits
   host_identity.py HostIdentityPort       ─ PosixUidGid │ (future) WindowsAccountIdentity
   filesystem_semantics.py FilesystemSemanticsPort ─ one impl + probed capabilities
   shell.py         CommandShellPort       ─ PosixShell │ (future) WindowsShell
   home.py          KriyaHome resolver (portable default + existing overrides)
   host_properties.py  hardware probe (moved from core/execution_environment)
        │
        ▼
 Mechanism consumers below core (tools/containment layer)
   ProcessController ─ uses ProcessControlPort + ResourceLimitPort
   ContainmentBackend (OCI / Null) ─ uses HostIdentityPort; reports runtime capabilities
```

**`kriya.platform` and the standard library.** A package named `kriya.platform` does not shadow the stdlib `platform` module, because every import is absolute. Modules inside the package import the stdlib module as `import platform as _stdlib_platform`, which makes the difference explicit. If the name is judged confusing, `kriya/host/` is the alternative. The layout is the same either way.

## 4. Ports

Every port is a `typing.Protocol`. Every provider declares `capabilities() -> Mapping[PlatformCapability, CapabilityStatus]` and `identity() -> AdapterIdentity`, where `AdapterIdentity` holds the name, version and OS/runtime facts used as evidence.

| Port | Operations | Capabilities it reports | POSIX provider (macOS and Linux share it) | Future Windows provider |
|---|---|---|---|---|
| `WorkspaceLockPort` | `acquire(path, nonblocking=True) -> LockHandle`, `release`, `probe(path) -> Optional[owner]` | `FILE_LOCK_CRASH_SAFE` | `flock` (LOCK_EX/LOCK_SH, NB) | `msvcrt.locking` / `LockFileEx` on a handle (released on process exit) |
| `ProcessControlPort` | `spawn_options() -> dict` (e.g. `start_new_session` / `creationflags`), `attach(process)`, `terminate_tree(process)` | `PROCESS_TREE_TERMINATION`, `PROCESS_GROUP_ISOLATION` | a session plus `killpg(SIGKILL)` | a Job Object with `KILL_ON_JOB_CLOSE`, `CREATE_NEW_PROCESS_GROUP` |
| `ResourceLimitPort` | `limits_for(plan) -> LimitApplication` (preexec and/or post-spawn attach), per-control `enforcement` | `POSIX_RLIMIT_CPU`, `POSIX_RLIMIT_AS` (ENFORCED on Linux, **ADVISORY** on macOS), `WINDOWS_JOB_OBJECT` | `setrlimit` in `preexec_fn` | Job Object CPU time plus committed memory |
| `HostIdentityPort` | `current() -> HostIdentity(kind, uid, gid, sid, is_privileged)`, `owner_of(path)` | `UID_GID_IDENTITY`, `WINDOWS_ACCOUNT_IDENTITY` | `os.getuid/getgid`, `os.stat().st_uid` | SID via Win32; `uid`/`gid` = None |
| `FilesystemSemanticsPort` | `path_within(root, target) -> bool`, `same_file(a, b)`, `canonical_identity(path)`, `policy_path(root, target) -> posix relpath`, `file_mode`/`apply_mode`, `atomic_replace`, `fsync_directory` | `CASE_INSENSITIVE_PATHS` (probed **per root**, not per OS), `UNICODE_NORMALIZATION_INSENSITIVE`, `SYMLINK`, `ATOMIC_REPLACE`, `POSIX_MODE_BITS`, `DIRECTORY_FSYNC` | one implementation; capabilities probed on the actual filesystem | the same implementation plus Windows-specific capability answers |
| `CommandShellPort` | `shell_argv(script)`, `split(command)`, `wrapper_executables()`, `executable_stem(argv0)` | `POSIX_SHELL`, `WINDOWS_SHELL` | `/bin/sh -c`, `shlex` | `cmd.exe /d /s /c`, PowerShell; stems strip `.exe`/`.cmd`/`.bat` |
| `ContainmentBackend` (existing) | unchanged `prepare()` | new runtime capabilities: `OCI_HOST_IDENTITY_MAPPING` (native 1:1 / desktop-translated / unavailable), `LINUX_CONTAINERS`, `BIND_MOUNT_SYNTAX_MOUNT_FLAG` | OCI (Docker, native or Desktop) | OCI on Docker Desktop in WSL2 Linux-containers mode |

**Filesystem semantics are per filesystem, not per OS.** macOS may mount case-sensitive APFS. Linux may have ext4 casefold directories. `path_within` therefore never decides from the OS name. Its algorithm (fixed in slice 1b, not here) is:

1. realpath both paths;
2. walk the target up to its nearest existing ancestor;
3. compare file identity (`os.path.samestat`) against the root, and against every ancestor of the target;
4. compare the non-existent tail lexically, using case-folding only where the probed capability says so.

That design removes the PLAT-001/002 fail-open class structurally.

## 5. Policy / mechanism split for the landed Linux fixes

- **JVM resources (LINUX-JVM-RLIMIT-AS-001).**
  - `resource_plan()` (strategy: address_space / jvm_heap / unbounded, chosen from the typed toolchain or argv[0]) is **Kriya policy**. It moves to `ExecutionResourcePolicy` unchanged.
  - Applying it is **mechanism**: `setrlimit` becomes `ResourceLimitPort`, and `JAVA_TOOL_OPTIONS` stays an environment transform in the policy.
  - The policy asks the port "is `POSIX_RLIMIT_AS` ENFORCED here?" only to report honest evidence (PLAT-006). It never uses the answer to relax a limit.
- **OCI writer identity (LINUX-OCI-WORKSPACE-IDENTITY-001).**
  - `HostIdentityPort.current()` supplies the trusted host identity.
  - The SEC-008 rule (refuse root, and require every write mount to be owned by that identity) stays one Kriya-owned function, deduplicated from `containment_oci` and `mcp/containment_adapter`.
  - The OCI backend decides the mapping (`--user uid:gid`, the owned tmpfs) from its `OCI_HOST_IDENTITY_MAPPING` capability.
  - Windows reports `WINDOWS_ACCOUNT_IDENTITY` without `UID_GID_IDENTITY`. A write mount then fails closed with a typed error until a Windows mapping is designed.

Neither migration changes behaviour on macOS or Linux. The existing Linux regression tests (`tests/test_linux_jvm_resource_strategy.py`, `tests/test_linux_oci_workspace_identity.py`, `tests/test_linux_oci_venv_interpreter.py`) are the safety net.

## 6. Startup / composition flow

```
kriya entry (CLI/REPL/MCP server/tests)
  → kriya.platform.services.platform_services()      # lazy, process-wide, cached, frozen
       detect host once: sys.platform → provider set (posix | windows)
       import provider modules lazily (fcntl/resource only inside posix providers)
       probe static capabilities (cheap); per-path capabilities probed on demand, cached per root
  → core modules call platform_services().<port>      # never import a provider directly
  → tests: platform_services.override(fake_services) context manager (strict fakes, tests only)
```

- There is exactly one composition point, `PlatformServices`, a frozen dataclass of ports.
- A missing provider for the host (for example Windows before P5) still composes. Each port that has no provider is a `MissingProvider` that reports every capability UNAVAILABLE and raises the typed error on use. Importing Kriya core therefore always succeeds, and a Windows run reports clearly what it lacks.

## 7. Capability negotiation and outcome semantics

- `CapabilityStatus` is one of `ENFORCED`, `ADVISORY` or `UNAVAILABLE`, and carries evidence: an adapter name and a reason.
- A caller that **requires** a capability calls `require(capability)`. On UNAVAILABLE that raises `PlatformCapabilityUnavailable` (reason code `PLATFORM_CAPABILITY_UNAVAILABLE`, naming the capability and adapter). The existing typed errors wrap it where they already exist: `BackendUnavailableError`, `ContainmentSetupError`, `WorkspaceLockHeldError`'s sibling. The failure is deterministic and never retried.
- ADVISORY is allowed only where Kriya policy already documents it. Today that is exactly macOS RLIMIT_AS (the "memory stays advisory on macOS" decision). It is recorded in evidence as ADVISORY, never as ENFORCED.
- Adapter output (identity, capability, modes) is **untrusted mechanism evidence**. Only the policy that owns the decision can promote it, as SEC-008 does for identity.

## 8. Adapter identity and evidence

- Each execution's evidence gains `platform: {provider, version, capabilities_used: {cap: status}}` next to the existing `resources`/`egress` in `ProcessResult` and `validate.execution_evidence()`. The addition is purely additive, and no existing key changes.
- The provider identity joins the execution-environment digest (PRD-014) **only** if a later decision says so. Changing a digest invalidates qualification records, so it is out of scope here and noted as a decision point.

## 9. Configuration

- There is no new user-facing configuration in the first phases. The provider is selected by host detection.
- Test-only overrides use `platform_services.override()`. Kriya never registers a fake provider (the same rule as `_fake_inference_runtime`).
- Home resolution:
  - `KriyaHome` keeps `~/.kriya` and every existing override (`KRIYA_STATE_DIR`, `KRIYA_LOG_DIR`, `KRIYA_AUTHORITY_HOME`, `KRIYA_MCP_APPROVAL_HOME`, `KRIYA_QUALIFICATION_HOME`, `KRIYA_CERTIFICATION_HOME`, `KRIYA_STATIC_ANALYSIS_HOME`, the adjudication home), byte-for-byte.
  - A native Windows default (`%LOCALAPPDATA%\Kriya`) is a P5 decision, and must keep existing stores readable.

## 10. Testing and CI matrix

| Suite | macOS (local M1 Max / optional `macos-latest`) | Linux (`ubuntu-latest`, hosted) | Windows (`windows-latest`) |
|---|---|---|---|
| Architecture guard (AST: forbidden modules and OS branches outside the allowlist) | ✔ | ✔ | ✔ (slice 1) |
| Import safety (`import kriya.cli` + core with POSIX modules blocked; on Windows natively) | ✔ (simulated) | ✔ (simulated) | ✔ native (slice 1) |
| Host-independent path fixtures: POSIX absolute/relative, `C:\`, `C:rel`, UNC `\\s\sh`, `\\?\`, case variants, NFC/NFD, backslashes, reserved names, alternate data streams | ✔ | ✔ | ✔ |
| Port contract suites (lock contention and crash release, tree kill of a grandchild, CPU/AS limit, identity, `path_within` on the real filesystem) | ✔ (POSIX provider) | ✔ (POSIX provider) | P5 (Windows providers) |
| Full deterministic suite | ✔ (operator) | ✔ | P6 (curated subset) |
| Docker/OCI tiers, `live_static_analysis`, production certification | ✔ (Desktop, operator) | ✔ (blocking) | not required unless a Windows containment provider is selected |
| Live model tier | ✔ (operator, PRD-035) | ✔ (smoke contract) | not required |

The Windows job in slice 1 installs Kriya, imports core, and runs the architecture guard plus the path fixtures. It is **non-blocking** until the guard is green on it, then blocking. A macOS deterministic-suite job is recommended, but optional: macOS certification already runs on the target machine.

## 11. Architecture guard

`tests/test_platform_architecture_guard.py` (AST-based, no execution) enforces three rules.

**Forbidden modules.** These modules may not be imported anywhere in `kriya/` or `plugins/` outside the allowlist: `fcntl`, `resource`, `pwd`, `grp`, `termios`, `tty`, `pty`, `msvcrt`, `winreg`, `_winapi`.

**Forbidden OS branches.** These expressions may not appear outside the allowlist: `sys.platform`, `os.name`, `platform.system`/`platform.mac_ver`/`platform.win32_ver`, `os.killpg`, `os.getuid`/`os.getgid`/`os.geteuid`, and `start_new_session=` / `preexec_fn=` built from an OS test.

**Allowlist** (explicit, reviewed; each entry says why):
- `kriya/platform/**`;
- until migrated (each entry is removed by the slice that moves it):
  - `kriya/tools/process.py`
  - `kriya/tools/sandbox.py`
  - `kriya/mcp/lifecycle.py`
  - `kriya/tools/containment_oci.py`
  - `kriya/mcp/containment_adapter.py`
  - `kriya/workflow/toolchain.py`
  - `kriya/core/execution_environment.py`
  - `kriya/production_doctor.py`

The allowlist may only shrink. Adding an entry fails a companion assertion unless the entry carries a reason. `kriya/workflow`, `kriya/policy`, `kriya/control`, `kriya/config`, `kriya/requirements*`, `kriya/metrics` and `kriya/static_analysis/service.py` can never be allowlisted.

**Raw containment idiom.** A later slice adds a rule forbidding the lexical containment idiom (`startswith(<x> + os.sep)`, `commonpath(`) outside `kriya/platform`, once `path_within` exists and the call sites have moved.

## 12. Two design constraints from the directive

- **No presentation coupling.** No platform port depends on a presentation layer (CLI, REPL, GUI or API), and no presentation layer calls OS primitives directly. `cli.py`'s `/dev/tty` read (PLAT-029) is the one current presentation-level OS touch. It is P3, and routes through an approval-prompt service when application services exist.
- **CLI semantics move behind application services, progressively.** Business and workflow semantics in the CLI should move behind reusable application services, so future GUI, API, IDE and MCP front ends reuse the same authorization and execution paths. **This scope does not refactor the CLI.** It only avoids making that harder: the platform package is importable without Click, and `PlatformServices` is a process-wide service, not a CLI object.

Plugins are a separate axis from platform adapters. Extension points (StaticAnalysisProvider, InferenceRuntimeProvider, ToolProvider and others) consume platform capabilities through services and never see `fcntl`, RLIMIT, Job Objects or uid/gid.

## 13. Platform support policy

| Platform | Level | Meaning |
|---|---|---|
| Linux | SUPPORTED / PRODUCTION_CERTIFIED | Hosted production certification is green (run 36384847918). It is re-certified after each platform slice. |
| macOS | SUPPORTED / DEVELOPMENT_AND_TARGET_CERTIFIED | The M1 Max target certification is maintained. PLAT-001/002 must be fixed to keep this claim honest. |
| Windows | ARCHITECTURALLY_SUPPORTED / NOT_YET_CERTIFIED, reached at the end of Phase P1/P2 | Core imports; there is no unconditional POSIX import; `PlatformServices` resolves Windows providers or reports each missing capability as UNAVAILABLE; adapters can be added without changing core. **Kriya makes no runtime-support claim for Windows until P6 Windows certification exists.** |

## 14. Backward compatibility

- Public entry points keep their signatures: `acquire_run_lock`, `probe_run_lock`, `WorkspaceLockHeldError`, `terminate_process_tree`, `resource_plan`, `ResourcePlan`, `resolve_host_writer_identity`, `posix_resource_limits_preexec_fn` (a deprecated shim) and `build_restricted_env`. They delegate to the ports. Test patch points that use these names keep working.
- **Persisted identities do not change silently.** `workspace_identity` digests, approval stores, RunRecords and qualification digests stay byte-identical wherever the workspace was addressed by its on-disk spelling (the normal case). The PLAT-017 fix canonicalizes to the on-disk spelling, so a workspace previously addressed by a **non-canonical** spelling gets a new id. That change is unavoidable, so the fix includes an explicit migration: readers accept both the legacy (as-typed) id and the canonical id, and writers write only the canonical id. The migration is a separate, reviewed decision inside PLAT-PATH-CONTAINMENT-001.
- There is no evidence schema break: `platform` is an additive key.
