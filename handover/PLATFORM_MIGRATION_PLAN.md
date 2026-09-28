# Platform Migration Plan (ARCH-PLATFORM-001)

Date: 2026-09-28. Inputs: `PLATFORM_DEPENDENCY_AUDIT.md` and `PLATFORM_ARCHITECTURE.md`.
Rules that apply to every slice:
- The quality bar applies in full (pylint/ruff at zero; failure paths tested; strict doubles; mutation checks on new decision logic; a regression test fails without its fix).
- Each slice is its own commits.
- No behaviour change on macOS or Linux unless the slice is a named bug fix.
- Linux hosted certification is re-run at the end of each phase that touches execution.
- No push without approval. PRD-036 does not start until the ARCH-PLATFORM-001 gate (§5) is met.

## 1. Recommendation on the Linux fixes (§19 item 8)

**The Linux fixes stay as landed. They do not need to be redone behind an abstraction first.** They are correct, hosted-green (runs 36380966601 and 36384847918), and contain no OS branching. They decide on typed inputs (the toolchain, argv[0], a trusted identity) in the tools and containment layer, below orchestration.

What remains is that their mechanism (`setrlimit`, `os.getuid`) is called directly. That moves behind `ResourceLimitPort` and `HostIdentityPort` in Phase P2 as a mechanical, no-behaviour-change refactor, with the existing Linux regression tests as the safety net. Reopening them now would risk a certified behaviour for no safety gain, and the directive says not to reopen successful fixes unnecessarily.

## 2. Phases

| Phase | Content | Behaviour change | Exit evidence |
|---|---|---|---|
| **P0-fix** (slice 1A) | Fix PLAT-001 (P0) and PLAT-002 (P1): identity-based path containment and same-file checks for the protected-path and outside-workspace guards, seeding `kriya/platform/filesystem_semantics.py`. | Yes, a **bug fix**: case- or normalization-variant bypasses are refused on macOS and Windows. Linux is unchanged. | Regression tests fail on `ea0627e` and pass after. Mutation-checked. The user's full pytest run is green. |
| **P1** (slice 1B) | The `kriya/platform` composition root, the capability model and `WorkspaceLockPort` (PLAT-003). The architecture guard (allowlist seeded with every current offender). The import-safety test. A non-blocking `windows-latest` import+guard CI job. | None. | `import kriya.cli` succeeds with `fcntl` blocked. The guard is green. The Windows job imports core. |
| **P2** (slices 2-5, one port per slice) | `ProcessControlPort` (PLAT-008/009). `ResourceLimitPort` plus the `ExecutionResourcePolicy` split and honest ADVISORY evidence (PLAT-005/006/007). `HostIdentityPort` plus one SEC-008 rule (PLAT-012). OCI `--mount` syntax (PLAT-013). `path_within` adopted at every PLAT-018 site, and the raw-idiom guard rule. Case-stable `workspace_identity` (PLAT-017, id-preserving). The toolchain locator out of `workflow/` (PLAT-015). Each slice removes its entries from the guard allowlist. | None, except PLAT-006: the evidence now says ADVISORY on macOS instead of overclaiming. | The allowlist is down to `kriya/platform/**`. The macOS suite and Linux hosted certification are green. |
| **P3** | Linux production semantics exposed by hosted CI. **Already done** (Wave 7 Linux closure). Re-applied only if a P2 slice regresses. | — | Hosted production certification is green after P2. |
| **P4** | Linux certification rerun at the P2 head. macOS target regression. | — | Both certifications recorded. **This is the ARCH-PLATFORM-001 gate, which then unblocks PRD-036.** |
| **P5** | Windows providers. Lock (`LockFileEx`). Job Object process/resource control. Account identity. `CommandShellPort` (PLAT-010/011/033), which is a **security prerequisite** so policy recognizers see `cmd`/PowerShell. Windows path policy: separator-normalized policy paths (PLAT-019/020) and the portable relpath validator rejecting reserved names and alternate data streams (PLAT-021). Mode, replace and fsync capabilities (PLAT-022/023/024). The git invocation profile (PLAT-025/026). Symlink capability (PLAT-027). A Python certification driver (PLAT-038). | Windows only. | Windows contract suites are green. |
| **P6** | `windows-latest` CI for install/import, config, path normalization, locking, process execution, workspace commit/recovery and the non-container deterministic core. | — | Windows certified at a declared level. Only then is a Windows runtime claim made. |

P5 and P6 are **after** PRD-036. PRD-036 certifies macOS and Linux, and records Windows as ARCHITECTURALLY_SUPPORTED / NOT_YET_CERTIFIED.

## 3. First implementation slice: exact files

### Slice 1A: PLAT-001 / PLAT-002 bug fix (separate commits; a supported-platform security fix, not portability debt)

Whether 1A goes before 1B, or whether you prefer 1B first, is **your decision**. My recommendation is 1A first, because it is a reproduced P0 on the certified target platform.

| File | Change |
|---|---|
| `kriya/platform/__init__.py` | new: package marker. The docstring states the layering rule. |
| `kriya/platform/filesystem_semantics.py` | new: `same_file(a, b)`, `path_within(root, target)` (realpath, then file identity via `samestat` up the nearest existing ancestor chain, then a probed case- and normalization-insensitive compare of the non-existent tail), `probe_case_insensitive(root)`. No OS branching: it probes the actual filesystem. |
| `kriya/policy/filesystem.py` | `AuthorizedFileWriter.authorize`: the protected check becomes "the target is the same file as, or the same path identity as, any protected path"; the ALLOWLIST compare uses the same identity (false denials removed); `is_within_scope` becomes `path_within`. |
| `kriya/config/authority_approval.py` | `validate_trust_path_outside_workspace` becomes `not path_within(ws, trust_dir)`. |
| `kriya/mcp/invocation_approval.py` | The trust-dir guard uses `path_within`. |
| `kriya/core/model_qualification.py` | `_refuse_inside_workspace` uses `path_within`. |
| `tests/test_platform_path_identity.py` | new: case-variant and NFD-variant protected targets are refused (skipped with a reason when the tmp filesystem is case-sensitive, **plus** a host-independent unit using a fake probe); outside-workspace guards refuse case-variant in-workspace paths; the Linux behaviour is unchanged; sibling-prefix (`/repo` vs `/repo-evil`) is still outside. Mutation: dropping the identity compare, or the tail fold, fails a test. |
| `handover/BACKLOG_REGISTRY.csv` | `PLAT-PATH-IDENTITY-001` row, closed with evidence. |

### Slice 1B: composition root, lock port, guard, Windows import job (no behaviour change)

| File | Change |
|---|---|
| `kriya/platform/capabilities.py` | new: `PlatformCapability` enum, `CapabilityStatus` (ENFORCED/ADVISORY/UNAVAILABLE plus evidence), `PlatformCapabilityUnavailable` (`PLATFORM_CAPABILITY_UNAVAILABLE`), `AdapterIdentity`. |
| `kriya/platform/services.py` | new: `PlatformServices` (frozen), `platform_services()` (detect once, lazy provider import, cached), `override()` (tests), `MissingProvider`. The **only** `sys.platform` read. |
| `kriya/platform/locking.py` | new: the `WorkspaceLockPort` Protocol and `PosixFlockLock` (the `fcntl` import lives here, inside the provider). |
| `kriya/control/run_ownership.py` | Drop `import fcntl`; delegate to `platform_services().workspace_lock`. The public API (`acquire_run_lock`, `probe_run_lock`, `_lock_path`, `WorkspaceLockHeldError`) and the payload are unchanged. |
| `kriya/production_doctor.py` | The store probe's lazy `fcntl` becomes `workspace_lock.probe_file` (PLAT-004). |
| `tests/test_platform_architecture_guard.py` | new: the AST guard (forbidden modules and OS branches outside the allowlist; the allowlist may only shrink and every entry carries a reason); the import-safety test (block `fcntl`/`resource`/`pwd`/`grp`/`termios`, then import `kriya.cli`, `kriya.workflow.workflow`, `kriya.control.recovery`, `kriya.tools.validate`, `kriya.mcp.mcp`, `plugins.core_tools`). |
| `tests/test_platform_workspace_lock_contract.py` | new: the provider contract (contention is nonblocking and typed; release on context exit; release on child-process death (spawn a holder and kill it); the probe never creates the file; a missing provider gives typed UNAVAILABLE). |
| `.github/workflows/ci.yml` | new `platform-windows` job (`windows-latest`, Python 3.12): `pip install -e .`, then `pytest tests/test_platform_architecture_guard.py` plus the host-independent path fixtures. `continue-on-error: true` until it is green once, then blocking (a separate commit). |
| `CLAUDE.md`, `docs/design.md` | A short "Platform services" section: the layering rule, the guard, the support levels. |
| `handover/BACKLOG_REGISTRY.csv` | Rows per §4. |

Mutation checks for 1B: remove the lazy import (the guard fails); make `MissingProvider` return SUPPORTED (the contract fails); swap `LOCK_NB` for blocking (the contention test times out, bounded).

## 4. Proposed backlog changes (CSV-ready; **not yet applied**)

These rows are not written to the registry yet. The directive ends at audit and design. Also, an OPEN P0/P1 row without a fix in the same batch would conflict with the registry rule "an open P0/P1 is worked, never parked" (`tests/test_backlog_registry.py`). On approval they are added in the commit that starts the corresponding slice.

```csv
ARCH-PLATFORM-001,"Cross-platform architecture & dependency closure: platform mechanism behind ports/adapters, no P0/P1 platform leak, core importable without POSIX modules, architecture guard, Linux hosted + macOS certification green",P1,OPEN,ARCH-PLATFORM-001 audit (2026-09-28),Pre-PRD-036 gate (phases P0-fix..P4),PRD-036,,handover/PLATFORM_MIGRATION_PLAN.md
PLAT-PATH-IDENTITY-001,"Case/normalization-variant path bypasses GOAL_SOURCE_FILE_PROTECTED on case-insensitive filesystems (macOS reproduced); outside-workspace trust/approval/qualification guards accept case-variant in-workspace paths",P0,OPEN,ARCH-PLATFORM-001 audit (2026-09-28) PLAT-001/PLAT-002,Slice 1A,ARCH-PLATFORM-001;PRD-036,,handover/PLATFORM_DEPENDENCY_AUDIT.md#PLAT-001
PLAT-IMPORT-SAFETY-001,"kriya core unimportable without fcntl (cli -> run_coordinator -> run_ownership); WorkspaceLockPort + architecture/import guard",P1,OPEN,ARCH-PLATFORM-001 audit PLAT-003,Slice 1B,ARCH-PLATFORM-001,,handover/PLATFORM_DEPENDENCY_AUDIT.md#PLAT-003
PLAT-PROCESS-CONTROL-001,"Process-tree ownership via scattered os.name branches + killpg; ProcessControlPort (POSIX session / Windows Job Object)",P2,OPEN,ARCH-PLATFORM-001 audit PLAT-008/009,Phase P2,ARCH-PLATFORM-001,,handover/PLATFORM_DEPENDENCY_AUDIT.md#PLAT-008
PLAT-RESOURCE-LIMITS-001,"ResourceLimitPort + ExecutionResourcePolicy split; win32 silently unbounded; macOS RLIMIT_AS evidence overclaims (report ADVISORY)",P2,OPEN,ARCH-PLATFORM-001 audit PLAT-005/006/007,Phase P2,ARCH-PLATFORM-001,,handover/PLATFORM_DEPENDENCY_AUDIT.md#PLAT-005
PLAT-HOST-IDENTITY-001,"HostIdentityPort; one SEC-008 identity rule (containment_oci + mcp/containment_adapter duplicate); OCI_HOST_IDENTITY_MAPPING runtime capability; typed error where uid/gid unavailable",P2,OPEN,ARCH-PLATFORM-001 audit PLAT-012,Phase P2,ARCH-PLATFORM-001,,handover/PLATFORM_DEPENDENCY_AUDIT.md#PLAT-012
PLAT-OCI-MOUNT-SYNTAX-001,"OCI -v host:ctr:mode breaks on drive letters and any ':' in a host path; use --mount type=bind",P2,OPEN,ARCH-PLATFORM-001 audit PLAT-013,Phase P2,ARCH-PLATFORM-001,,handover/PLATFORM_DEPENDENCY_AUDIT.md#PLAT-013
PLAT-PATH-CONTAINMENT-001,"~17 hand-written lexical path containment/identity checks (startswith(root+os.sep)/commonpath/normcase) -> FilesystemSemanticsPort.path_within + guard rule; case-stable workspace_identity (id-preserving)",P2,OPEN,ARCH-PLATFORM-001 audit PLAT-017/018,Phase P2,ARCH-PLATFORM-001,,handover/PLATFORM_DEPENDENCY_AUDIT.md#PLAT-018
PLAT-TOOLCHAIN-LOCATOR-001,"workflow/toolchain.py branches on sys.platform=='darwin' for java_home; move to a toolchain locator adapter",P2,OPEN,ARCH-PLATFORM-001 audit PLAT-015,Phase P2,ARCH-PLATFORM-001,,handover/PLATFORM_DEPENDENCY_AUDIT.md#PLAT-015
PLAT-WINDOWS-POLICY-PARITY-001,"Before any Windows claim: separator-normalized sensitive-path matching, ..\\ escape checks, reserved names/ADS/trailing dot in model paths, cmd/PowerShell shell-wrapper recognition, executable stems",P2,DEFERRED,ARCH-PLATFORM-001 audit PLAT-011/019/020/021/033,Phase P5 (blocks Windows certification),Windows certification,,handover/PLATFORM_DEPENDENCY_AUDIT.md#PLAT-019
PLAT-WINDOWS-FS-GIT-001,"Windows filesystem + git fidelity: mode bits, os.replace over open files, directory fsync, symlink privilege, autocrlf/eol/filemode/hooksPath git invocation profile",P2,DEFERRED,ARCH-PLATFORM-001 audit PLAT-022..027,Phase P5,Windows certification,,handover/PLATFORM_DEPENDENCY_AUDIT.md#PLAT-022
PLAT-WINDOWS-PROVIDERS-001,"Windows providers: LockFileEx lock, Job Object process/resource control, account identity, CommandShellPort, host-shell ShellTool, Python certification driver, windows-latest CI certification",P2,DEFERRED,ARCH-PLATFORM-001 audit PLAT-004/005/008/010/038,Phases P5-P6,Windows certification,,handover/PLATFORM_MIGRATION_PLAN.md
PLAT-PORTABILITY-P3-001,"P3 portability debt: KriyaHome resolver (9 copies), host property probe placement, /dev/tty prompt, PowerShell completion, /tmp baseline normalization, lsp tree kill, per-platform lock file, Windows test subset, dead '[::1]' literal",P3,DEFERRED,ARCH-PLATFORM-001 audit PLAT-009/016/028..031/034/036/037,Phase P5 or later,none,,handover/PLATFORM_DEPENDENCY_AUDIT.md
SEC-CONTROL-PATH-WRITE-001,"AuthorizedFileWriter (UNRESTRICTED) allows writes to .git/** and .kriya/** inside the workspace; end-to-end reachability via generate not yet reproduced",P1,OPEN,ARCH-PLATFORM-001 audit PLAT-039 (outside platform scope),Reproduce first; own slice,PRD-036 if reachable,,handover/PLATFORM_DEPENDENCY_AUDIT.md#PLAT-039
```

The tracker (`TASK_STATUS_TRACKER.csv`) gets one task, `ARCH-PLATFORM-001`, pointing at the registry row. PRD-036's `blocking` gains `ARCH-PLATFORM-001`.

## 5. The ARCH-PLATFORM-001 gate (definition of done before PRD-036)

1. The whole-repository audit is complete (this package) and approved.
2. The platform architecture is approved.
3. PLAT-001/002 (P0/P1) are fixed. SEC-CONTROL-PATH-WRITE-001 is reproduced and either fixed or reclassified with evidence.
4. PLAT-003 is fixed. Core imports with POSIX modules blocked, and the Windows CI job imports core.
5. The architecture guard is live. Its allowlist holds only `kriya/platform/**` (P2 complete), **or** the user accepts named residual allowlist entries as P2 debt with a target scope.
6. The Linux fixes sit behind `ResourceLimitPort` / `HostIdentityPort` with no behaviour change.
7. The macOS deterministic suite is green (the user's pytest run) and macOS target certification still holds.
8. Linux hosted production certification is green at the gate head.
9. The docs state the support levels (Linux PRODUCTION_CERTIFIED, macOS DEVELOPMENT_AND_TARGET_CERTIFIED, Windows ARCHITECTURALLY_SUPPORTED / NOT_YET_CERTIFIED).

A decision for you inside item 5: whether all of P2 must finish before PRD-036, or only 1A, 1B and the two Linux-fix ports (PLAT-RESOURCE-LIMITS-001 and PLAT-HOST-IDENTITY-001), with the rest carried as registered P2. My recommendation is the latter. Every remaining P2 item has no effect on macOS or Linux correctness today, and each one is independent.

## 6. Out of scope, stated so it is not implied

- No Windows runtime support or claim in this program before P6.
- No CLI or application-service refactor (PLATFORM_ARCHITECTURE.md §12).
- No change to persisted identities, qualification or environment digests, or evidence schemas beyond the additive `platform` key.
- No weakening of Linux or macOS security for a lowest common denominator. Windows providers must meet or exceed the same contracts; for memory, a Job Object is stronger than RLIMIT_AS.
