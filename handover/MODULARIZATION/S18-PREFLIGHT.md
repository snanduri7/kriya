# S18 PHASE 1: measured preflight (TYPE C, planned-command authority; OI-127, OI-88 remainder, OI-129, OI-135)

**VERDICT: OPTION_B_RECOMMENDED.** Option A is NOT PROVEN. The OCI boundary itself holds when the candidate is the
mounted root (MEASURED R41/R42). The owner's other requirement fails: unauthorized mutation must be detected BEFORE the
resulting evidence is trusted.
- The existing integrity seam (`VerificationTreeBinding`) catches tracked and untracked source changes (R51/R52/R55/R56/R58).
- It is blind to git-ignored content and to the candidate's `.kriya/` (R53, R54, R57, R59).
- A container rooted at the candidate can write all four (R41).
- Ignored content is an evidence-forgery channel. A planted ignored `__pycache__` file overrode the reviewed source in a
  later pytest gate (R63, CONFIRMED).
- The design treats exactly that output as legitimate gate output (`gate_output_roots`: `__pycache__/`, `target/`,
  `build/`).

Closing this needs one of two pieces of new machinery, and neither is an existing seam:
- an ignored-inclusive snapshot plus a discard/restore of whatever the step changed (unmeasured cost); or
- a read-only candidate mount, which contradicts "generated build output is legitimate".

For Option A, the owner's STOP condition "scope integrity occurs only after evidence is trusted" therefore holds for
ignored content (today there is no check at all: R80). Option B meets every other owner constraint:
- one governance resolution with no competing policy;
- control paths unreachable because nothing planned executes;
- validate_refactor refused.

So the unit does not STOP. It proceeds with Option B, subject to owner sign-off.

Base: `arch/full-modularization-v1` @ 2e40e2b (clean). Phase-1 commits: 25997b2 (characterization pins), abe74f3
(this document), 36fd3b2 (own test weakness, below), plus this revision. No production code was changed. Host: macOS
Darwin 27.0.0, Docker Desktop engine 27.5.1 (linux), git 2.54.0, .venv Python. Scratch, with every reproducer and its raw JSON:
`/private/tmp/claude-501/-Users-sriramnanduri-WorkingDirectory-AI-ClaudeCode-Kriya-By-ClaudeCode/bc971a42-dc25-4273-ba41-f653fa8c96f5/scratchpad/s18/`
(`harness.py`, `test_r80_enforce_probe.py`, `runs/results_*.json`, `runs/R80.json`, `runs/R63/probe.py`).

## 0. Reproducer index (all against the unfixed bytes)

Fixture used by every harness reproducer:
- An owner git repository with a real `.git` directory.
- The PRODUCTION candidate from `create_git_worktree(owner)`, which lives at `owner/.kriya/worktree`.
- Sibling `outside/`, `state/` and `authority/` directories.
- HOME, XDG_CONFIG_HOME, every KRIYA_* home, KRIYA_AUTHORITY_HOME, GIT_CONFIG_GLOBAL and GIT_CONFIG_NOSYSTEM all pointed at
  scratch.
- `chdir(owner)`, as the CLI and desktop launch do. ShellTool and ValidationTool act on `os.getcwd()`.

sha256 of every watched file is taken before and after each probe. Dispatch goes through the real
`subtask_executor.execute()`, i.e. `execute_model_authored()`, with `tool_roots=(owner, candidate)` as the enforce loop
passes them.

| id | what | backend / mode | result (MEASURED) |
|---|---|---|---|
| R01 | shell `printf x > <outside>/ABS.txt` | none / guardrails | COMPLETED exit 0; outside/ABS.txt created |
| R02 | shell `echo '[s18]' >> .git/config` | none / guardrails | COMPLETED; owner .git/config changed |
| R03 | shell `git -C <owner>/.git config ...` | none / guardrails | COMPLETED; owner .git/config changed |
| R04 | shell `cd <candidate> && git config ...` (worktree gitdir indirection) | none / guardrails | COMPLETED; the OWNER's .git/config changed (shared config) |
| R05 | shell `printf x > link/SYM.txt` (owner symlink -> outside) | none / guardrails | COMPLETED; outside/SYM.txt created |
| R06 | shell `printf x > .kriya/S18_CONTROL` | none / guardrails | COMPLETED; owner .kriya written |
| R07 | shell write to Kriya state dir (absolute) | none / guardrails | COMPLETED; state/S18_STATE created |
| R08 | shell write to the authority-approval home (absolute) | none / guardrails | COMPLETED; authority/S18_APPROVAL.json created |
| R09 | shell `>> .kriya/worktree/src.py` (candidate source via owner-relative path) | none / guardrails | COMPLETED; candidate src.py changed |
| R10 | shell `>> src.py` (owner source) | none / guardrails | COMPLETED; owner src.py changed |
| R11 | shell plants `core.fsmonitor` in owner .git/config | none / guardrails | COMPLETED; marker PRESENT after Kriya's own `untracked_repository_paths(candidate)` (`git ls-files`, git 2.54.0). "Absent before Kriya's git" was recorded for R31 and in P1 (the `none` case), not in the R11 run itself = host code execution by Kriya's own git |
| R12 | R01 in human-in-the-loop | none / HITL | NEEDS_REVIEW `TOOL_CONFIRMATION_UNSUPPORTED` (B3); nothing written |
| R21-R31 | R01-R11 with the PRODUCTION profile (`containment_backend: oci`, `contained_execution_required: true`, `shell_network: denied`) | oci / guardrails | R21/R25/R27/R28: exit 2, nothing written (path absent in container). R23: exit 127 (`git` absent in the image; image artifact, not a boundary). R24: exit 2 (candidate absolute path absent). R22: owner .git/config CHANGED. R26: owner .kriya WRITTEN. R29: candidate source CHANGED. R30: owner source CHANGED. R31: fsmonitor planted, then executed ON THE HOST by Kriya's own git (marker absent before, present after). **Every one is reported COMPLETED, even exit 2 and 127.** |
| R40 | validate_refactor, run config `oci` + contained | - / guardrails | COMPLETED; `PolymorphicValidator(".")`, effective root = OWNER folder, backend `none`, contained False, run config says `oci` |
| R41 | OCI with workspace = CANDIDATE (Option-A shape), network DENIED | oci | uid 501:20; `.git` = `/dev/null` char device, `rm` -> EBUSY, `.git/config` not creatable; owner and outside invisible; absolute and relative symlinks to host paths do not resolve; tracked write, new untracked file and `candidate/.kriya/PLANTED` SUCCEED and land on the host candidate; DNS/TCP fail; `/kriya` not writable (permission) |
| R42 | R41 with network UNRESTRICTED | oci | same filesystem results; DNS and TCP succeed (default bridge) |
| R51-R62 | `VerificationTreeBinding(candidate, tracked, ())` bound before a mutation, `check("tool:shell")` after | - | DETECTED: tracked modify R51, new untracked R52, exec bit R55, delete tracked R56, .gitignore edit R58. NOT DETECTED: nested `sub/.gitignore` `*` + `sub/evil.py` R53; ignored symlink to a host path R54; ignored `__pycache__` plant R57; `candidate/.kriya/PLANTED` R59; candidate gitfile rewritten to a non-repo R60 (`untracked_repository_paths` returns empty: fail-open); owner shared .git/config R61; `info/exclude` hiding a new file R62 |
| R63 | planted ignored `__pycache__/src.*.pyc` (same size and mtime as the reviewed source) then a pytest gate | host python | the gate ran the PLANTED bytecode (test of the reviewed source failed) = CONFIRMED evidence-forgery channel through ignored output |
| R70 | `validate_tool_capability_membership` with the registry's names incl. an undeclared plugin tool and `shell` | - | valid=True (membership only) |
| R71 | SIMULATED host without Docker (`_require_docker` raises), production profile | oci | FAILED, `reason_codes=()` (untyped), nothing written |
| R72 / R73 | a plugin BaseTool that DECLARES (allowing) `authorize_model_invocation` and writes outside | none / HITL, guardrails | COMPLETED and written in BOTH modes: a self-declared plugin grants itself authority even in human-in-the-loop |
| R74 | operator `kriya tools execute shell '{"command": "printf x > <outside>/OPERATOR.txt"}' -y` (CliRunner, config HITL) | none | exit 0, outside file written; the operator run takes the run lock and a RunRecord (PRD-006) |
| R75 | operator `kriya tools execute validate_refactor ... -y` (tests patched) | none | exit 0, `VALIDATION SUCCESS`, run lock + RunRecord |
| R76 | operator `kriya tools execute git '{"subcommand": "status"}' -y` | none | exit 0, owner status printed, nothing written |
| R80 | real `WorkflowController` enforce run, guardrails, `none`: planned shell appends to the candidate's controller through `.kriya/worktree/...` and to the owner's controller | none / guardrails | s0 COMPLETED. ALL 5 later compile/test gate calls (nested sub-run candidate) saw the injected source. Nothing detected it. The owner file stays modified (uncommitted, outside any review). Run ended needs_review only because the harness acceptance criterion stays unresolved. `files` = [shop/service.py]: the injection is not in the review list |
| T1 | targeted pytest: S1 MCP / undeclared / validate_refactor pins | - | 7 passed (0.88 s) |
| P1 | `tests/test_s18_planned_command_characterization.py` (25997b2, 36fd3b2) | none | `--runxfail`: 10 failed, each because the planned action ran (R80 pin: owner changed, gates saw the injection, step COMPLETED: all True; fsmonitor pin: fails on the plant itself, marker absent before Kriya's git); normal: 10 xfailed |

Own harness defect (reported, preserved): the first OCI batch ran with HOME redirected to scratch. That hid the Docker
Desktop context, so every probe FAILED "daemon not reachable", nothing was written, and `reason_codes=()`. This is a
TEST_HARNESS artifact, not a product finding. It is preserved as
`runs/results_A_oci_HARNESS_ARTIFACT_no_docker_context.json` and was re-run with an explicit `DOCKER_HOST`.

Own test weakness (fixed in its own commit 36fd3b2): the fsmonitor pin's only failing assertion was the hook execution
by Kriya's own git, which depends on git refreshing through `core.fsmonitor` for `ls-files` (true for 2.54.0 here). On
another git it could XPASS. It now fails first on the plant itself (the shared `.git/config` gained `fsmonitor`).

Rule deviation (disclosed): the first `--runxfail` / targeted pytest runs of P1 (and the R80 pin) used pytest's default
basetemp under `/private/var/folders/...`, outside the scratch directory. Every write was inside that pytest tmp tree
(the probes write only to fixture paths), but the brief says scratch only; later runs pass `--basetemp=<scratch>/s18/pt`.

## 1. THE TABLE (tool x origin x autonomy mode)

Legend:
- **decl** = what the tool declares today (`authorize_model_invocation` override, S1).
- **cat** = Planner catalogue visibility. Today this is `registry.list_components("tool")`, unfiltered (stage_planning.py:106, planner_repair.py:451; TRACED).
- **val** = plan validation, i.e. `validate_tool_capability_membership`, which checks membership only (R70).
- **root** = working root.
- **ctl** = control paths reachable.
- **SI** = scope-integrity check.
- **op** = operator `kriya tools execute` (`BaseTool.execute`; cwd = the operator's own; `requires_confirmation` prompts unless `-y`). It is mode-independent and unchanged by S18 (owner).

| tool / op | origin | mode | decl | cat | val | execution authority (what decides) | backend required / available | root | write boundary | ctl | SI (what, when vs evidence) | failure behaviour | repro |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| shell | planned | HITL | refuse in HITL | listed | passes | B3 `requires_confirmation`+HITL, then S1 `_refuse_unreviewed_execution` | n/a (never runs) | - | nothing runs | none | n/a | NEEDS_REVIEW `TOOL_CONFIRMATION_UNSUPPORTED` | R12, S1 |
| shell | planned | guardrails | refuse in HITL only | listed | passes | POL-001 hard invariants (5 codes) + SEC-005 network; NOTHING about paths | none: none required (default `none`); production: oci required, available here | `os.getcwd()` = OWNER folder (both backends) | none: entire host user; oci: owner folder rw | none: owner .git, owner .kriya, candidate, Kriya state, approvals, any path; oci: owner .git (real dir not masked) and owner .kriya incl. the candidate | NONE at the TOOL step. The later sub-run gate binding binds AFTER it, so injected content is part of the bound baseline and evidence is trusted over it | COMPLETED whatever the exit code (exit 2/127 COMPLETED); backend unavailable = FAILED untyped | R01-R11, R21-R31, R71, R80 |
| shell | op | any | - | - | - | operator + confirmation prompt (`-y` skips) + POL-001/SEC-005; run lock (PRD-006) | as configured | operator cwd | as configured (none: anywhere) | as configured | none | ToolExecutionError | R74 |
| validate_refactor | planned | HITL | refuse in HITL | listed | passes | S1 `TOOL_MUTATION_REVIEW_UNAVAILABLE` | n/a | - | nothing runs | none | n/a | NEEDS_REVIEW | S1 pin |
| validate_refactor | planned | guardrails | named files = AuthorizedFileReader reads | listed | passes | AuthorizedFileReader on `files`; then `PolymorphicValidator(".")` with a DEFAULT `AutonomyConfig()` | always `none` (ignores the run's oci/contained config) | OWNER folder (`"."`) | the build/test toolchain runs on the host in the owner folder | everything the host toolchain touches | none at the step | COMPLETED; compile/test failure returned as COMPLETED text | R40, T1 |
| validate_refactor | op | any | - | - | - | operator; run lock | default `none` | operator cwd | host | host | none | text result | R75 |
| filesystem read/list | planned | HITL / guardrails | AuthorizedFileReader over (owner, candidate) | listed | passes | AuthorizedFileReader (canonical containment, symlinks resolved, credential stores) | none needed (in-process read) | resolved against owner | read only | none (reads only) | n/a | refused typed `PATH_OUTSIDE_AUTHORIZED_READABLE_ROOTS` / `SENSITIVE_PATH_DENIED` | S1 pins (T1 file) |
| filesystem write | planned | HITL / guardrails | AuthorizedFileWriter DENY_ALL | listed | passes | AuthorizedFileWriter DENY_ALL (OI-128 closed: refused in all modes) | n/a | - | nothing written | none | n/a | FAILED `WRITE_SCOPE_DENY_ALL` / `PATH_OUTSIDE...` / `TRUSTED_CONTROL_PATH_DENIED` | S1 pins |
| filesystem * | op | any | - | - | - | operator | in-process | operator cwd | anywhere the user can write | yes | none | ToolExecutionError | S1 `test_the_operator_cli_writes_where_the_operator_says` |
| git status/diff/log/branch | planned | HITL / guardrails | unchanged (read) | listed | passes | none beyond fixed argv; TRUSTED_KRIYA_INFRASTRUCTURE, `none` | none | `os.getcwd()` = owner (OI-129 item 3) | read; BUT git honours repo config (R11/R31: a planted `core.fsmonitor` runs on `git status`) | reads .git | n/a | COMPLETED or ToolExecutionError | S1 pin `test_git_reads_still_run_in_every_mode`; R11 |
| git blame | planned | HITL / guardrails | file = AuthorizedFileReader | listed | passes | AuthorizedFileReader | none | owner | read | reads .git | n/a | typed read refusal | S1 pin |
| git commit | planned | HITL / guardrails | PLAT-039 denial | listed | passes | `trusted_control_path_denial` in every mode | n/a | - | nothing | none | n/a | FAILED `TRUSTED_CONTROL_PATH_DENIED` | S1 pin |
| git * | op | any | - | - | - | operator + POL-001-P2 commit policy (audit/enforce) | none | operator cwd | .git of cwd | yes | none | ToolExecutionError | R76 (status); commit TRACED |
| search / ast | planned | HITL / guardrails | AuthorizedFileReader (search: every walked file) | listed | passes | AuthorizedFileReader | none | owner | read | none | n/a | typed read refusal | S1 pins |
| plugin BaseTool, DECLARED (overrides `authorize_model_invocation`) | planned | HITL / guardrails | its own (self-)declaration | listed | passes | the plugin's own `authorize_model_invocation` + its `_run`: a SELF-declared allow is honoured even in human-in-the-loop | whatever it does | process cwd | anything | anything | none | COMPLETED (writes outside) | R72 (HITL), R73 (guardrails) |
| plugin BaseTool, UNDECLARED | planned | HITL | default: refuse in HITL | listed | passes | `BaseTool.authorize_model_invocation` default | - | - | nothing runs | none | n/a | NEEDS_REVIEW `TOOL_MODEL_AUTHORITY_UNDECLARED` | S1 pin |
| plugin BaseTool, UNDECLARED | planned | guardrails | default: own authority | listed | passes | the plugin's own `_run` (none) | whatever it does | process cwd | anything | anything | none | COMPLETED (writes) | T1 (`test_an_undeclared_tool_in_a_non_hitl_mode_keeps_its_own_authority`), P1 |
| plugin (any) | op | any | - | - | - | operator | - | operator cwd | anything | anything | none | ToolExecutionError | TRACED (same CLI path as R74) |
| MCP tool | planned | HITL / guardrails | TOOL-002/003 (adds nothing) | listed (registered as tools) | passes | TOOL-002 durable identity-bound operator approval in `MCPTool._run` (always enforced) + TOOL-003 capability profile | OCI only when `mcp_contained_execution_required` (default False = host subprocess) | per MCP capability profile (`workspace_read/write`, additional paths) | per profile under OCI; host otherwise | per profile | none | unapproved: zero calls, typed denial; approved: exactly one call | T1 (`test_the_tool002_mcp_scenarios_hold_in_human_in_the_loop` x4, `test_mcp_tools_keep_their_own_tool002_authority_in_every_mode`) |
| MCP tool | op | any | - | - | - | same TOOL-002/003 gate | same | same | same | same | none | same | TRACED (`MCPTool._run` is the one choke point for both origins) |

Cross-cutting findings in the table:
- (MEASURED R70) The catalogue and validation consult nothing but membership.
- (TRACED enforce_loop.py:513-517) If `list_components("tool")` raises, `available_tool_names=None` and the check is SKIPPED, i.e. it fails open.
- (TRACED) In human-in-the-loop the Planner is shown shell and validate_refactor, and validation accepts them; only the executor refuses.

## 2. Host matrix

| host class | real containment | evidence |
|---|---|---|
| this macOS host (Docker Desktop 27.5.1) | `oci` available and working (R21-R31, R41, R42); `none` = no containment | MEASURED |
| Linux host with Docker | `oci` (same argv); container runs as the host uid:gid, refused for root or an owner mismatch (SEC-008, LINUX-OCI-WORKSPACE-IDENTITY-001); native bind mounts (no Desktop file-sharing layer) | NOT_RUN (no Linux host in this session); TRACED from `containment_oci.prepare` / `_writer_identity`; the macOS mount results are INFERRED, not measured, to transfer |
| host without Docker | none: `oci` fails closed before running (`_require_docker` / `_probe_daemon` -> BackendUnavailableError) | SIMULATED R71 (FAILED, nothing written, but `reason_codes=()`); the HOME-redirect artifact run is an unplanned real instance of "daemon unreachable" (same outcome) |

**What `oci` confines** (MEASURED R41/R42/R21-R31; TRACED `OCIContainmentBackend.prepare`):
- **Mounts.** Only `ContainmentProfile.workspace_path` is mounted, rw, at `/kriya/workspace` (cwd). Besides it: the tmpfs `/kriya/tmp` (noexec, nosuid, owned by the run user), optional dependency caches, temp and MCP extra mounts. Host root, home, outside, Kriya state and the approval home are not mounted (R21/R27/R28 exit 2, nothing written).
- **User.** The host uid:gid (501:20 measured), with `--cap-drop ALL`, `no-new-privileges` and a pids limit.
- **Network.** DENIED gives `--network none` (DNS and TCP fail, R41). UNRESTRICTED gives the default bridge (both succeed, R42). The production profile forces `shell_network: denied`.
- **.git.** A gitfile pointing OUTSIDE the mount (a worktree candidate) is masked by a read-only `/dev/null` mount: unremovable (EBUSY), and `.git/config` cannot be created (R41). A REAL `.git` directory inside the mounted workspace is left mounted rw (`dangling_gitfile_mask` handles only gitfiles), so the owner's `.git/config` is writable (R22).
- **Kriya control state.** When the workspace is the owner folder, `<owner>/.kriya/` (run lock, RunRecords, checkpoints AND the candidate worktree itself) is mounted rw (R26, R29). When the workspace is the candidate, `candidate/.kriya/` can be created freely (R41).
- **Symlinks.** They resolve inside the container's namespace, so absolute and relative links to host paths do not reach the host (R41, R25).
- **Root FS.** Not `--read-only` (TRACED, no flag), but `/kriya` is not writable by the run user (R41). Container layer only, discarded with `--rm`.
- **No leftovers.** `docker ps -a --filter name=kriya-oci` was empty after every batch (MEASURED).

**What `none` means in practice** (MEASURED R01-R11; TRACED `NullContainmentBackend`): the host process, with an env allowlist and best-effort rlimits.
- No filesystem, network or process isolation.
- The planned command can write everything the Kriya user can (R01-R10).
- It is refused only for a profile asking for a non-UNRESTRICTED network.

**New finding (TRACED, not investigated further):** `git_metadata_mount_args`' docstring says "The host's own `.git` is never mounted". That holds only when a git-metadata export is bound. With none bound (ShellTool's profile never binds one; `autonomy.git_metadata_export=false` for gates), a real `.git` DIRECTORY inside the mounted workspace is mounted rw (R22). Gates normally bind the export (`git_metadata_export` default True, `PolymorphicValidator._bound_git_metadata`). But the scoped-snapshot sandbox (a workspace nested in an enclosing repository) carries a real Kriya-built `.git` directory (`worktree.py:459`), so with the export off a contained gate could plant a hook that Kriya's host git then runs (R31 channel; INFERRED). Recommended OI row: "OCI mounts a real `.git` directory rw when no git-metadata export is bound; the hook-plant channel (R31) applies to any such profile". Not added to OPEN-ITEMS (orchestrator's decision).

**The production shell is `oci` over the OWNER folder** (`ShellTool._run`: `workspace_path=os.getcwd()`). That is not a candidate boundary. It is also an escape: R31 plants a hook that Kriya's own host git then runs.

## 3. Shell and validate_refactor specifics

- **Detection today** (MEASURED R80; TRACED enforce_loop.py:1794-1859).
  - There is no scope check at the TOOL step. A planned shell result is COMPLETED on any exit code (R21: 2, R23: 127).
  - The first integrity check over the candidate is `VerificationTreeBinding`, inside the later MODEL sub-runs. It binds AFTER the TOOL step, so injected content becomes part of the bound baseline.
  - All gate evidence was produced over the injected source (R80, 5/5 gate calls). Detection happens never, not merely late.
- **What apply-time review sees** (MEASURED R80 + TRACED `commit_service.plan_terminal_writes` / `enforce_loop.py:3232-3262`).
  - The enforce terminal gate service runs over the plan candidate, and the commit materializes ONLY the plan's planned paths (each path's final planned action) from the candidate.
  - An injection into a PLANNED path (R80: the controller is s2's planned file) is therefore part of the commit batch, indistinguishable from model output. In R80 the run stopped needs_review (harness criterion unresolved), so no commit was measured.
  - An injection into an UNPLANNED path is silently left out of the commit, but every gate already ran over it.
  - The result's `files` contained only the MODEL subtask's file (shop/service.py).
  - The owner-folder modification is not in any candidate batch at all. It stays in the owner's working tree (`git status`: ` M shop/controller.py`), unreviewed.
- **The five probes on `none` (all written) and on `oci` (production profile):**
  - `printf x > /abs/outside`: none R01 written; oci R21 not reachable (exit 2).
  - `git -C <gitdir> ...`: none R03/R04 written. On oci, git is absent in the default image (R23 exit 127, an image artifact). The direct-file variant R22 `echo x >> .git/config` WROTE the owner config.
  - `echo x >> .git/config`: none R02 written; oci R22 written.
  - A write through a symlink: none R05 written outside; oci R25 not reachable.
  - Kriya state / approval / control: none R06/R07/R08 all written. oci: R27/R28 (state, approvals: not mounted) not reachable, but R26 owner `.kriya` WRITTEN and R29 candidate source WRITTEN.
  - The hook plant (R11/R31) runs on the host on both backends.
- **validate_refactor root** (MEASURED R40; TRACED `plugins/core_tools/validation_tool.py`).
  - `ValidationTool._run` builds `PolymorphicValidator(".")`, i.e. the process cwd = the OWNER folder.
  - `ValidationTool()` is registered with NO config (`CoreToolsPlugin.initialize`), so its validator always uses a default `AutonomyConfig()`: backend `none`, `contained_execution_required=False`. This holds even when the run requires `oci`.
  - The planned-step root CAN be made explicit through existing seams, so the "untargetable" STOP condition does NOT apply. The seams are `BaseTool._run_model_authored(args, invocation)` (S1; already overridden by SearchTool) with `invocation.roots[1]`, plus passing the run's `AutonomyConfig` at registration as ShellTool already receives it. This is TRACED, not exercised.

## 4. Design

### 4a. Why Option A is not proven (each item labelled)
1. **Boundary** (MEASURED R41/R42). Candidate-root OCI with network DENIED confines the host, the owner, outside paths, symlinks and the candidate's git admin (masked). This half of Option A is proven for this host. On Linux it is NOT_RUN (TRACED only).
2. **Pre-trust scope integrity.**
   - Reusing `VerificationTreeBinding` at the TOOL branch (bind before, `check` after, before the result is recorded) would catch tracked and untracked source changes (MEASURED R51/R52/R55/R56/R58).
   - It is blind to R53 (nested `.gitignore`), R54 (ignored symlink), R57 (ignored bytecode) and R59 (`candidate/.kriya`, excluded by `untracked_repository_paths` by design). The container can write each of them (R41).
   - R63 CONFIRMS that ignored output changes later evidence.
   - INFERRED, not measured: Maven/Gradle incremental builds and pytest `conftest.py` in an ignored directory are the same channel.
   - The design calls this output legitimate (`gate_output_roots`) and never integrity-checks it. "Generated build output is legitimate" and "detected before evidence is trusted" cannot both hold with existing seams. The two ways out are new machinery: (a) an ignored-inclusive snapshot of the whole candidate (including `target/`, `node_modules/`; unmeasured cost) plus restoring whatever the step changed; or (b) a read-only candidate mount (`ContainmentProfile.workspace_write=False` exists), which removes build output entirely.
3. **Control paths under A** (MEASURED R41). The candidate gitfile is masked. `candidate/.kriya` would need an extra mask mount. That is small and is not the blocker.
4. **Later execution must also be contained** (TRACED). A planted artifact otherwise runs on the host in a `none`-backend gate. Option A would be valid only under `contained_execution_required` + `oci` for every later gate, which is the production profile only.

### 4b. Option B (recommended): one declaration, one resolution, typed refusal
- **Declaration**, on the existing tool contract in `kriya/tools/tool.py` (TOOLBOX):
  - A `ToolGovernance` enum `READ_ONLY | GOVERNED_SIDE_EFFECT | OPERATOR_ONLY`.
  - A `BaseTool.planned_governance` property whose default is `None` = undeclared = fail closed. An unknown value is also refused.
  - Shipped declarations: filesystem `READ_ONLY` (writes stay refused per call by AuthorizedFileWriter DENY_ALL, OI-128); search, ast `READ_ONLY`; git `READ_ONLY` (commit stays refused per call by PLAT-039); shell `OPERATOR_ONLY`; validate_refactor `OPERATOR_ONLY`; `MCPTool` `GOVERNED_SIDE_EFFECT` (TOOL-002/003 decide each call).
- **One resolution**, `resolve_planned_tool_governance(tool, *, origin, autonomy_mode, containment_backend) -> ToolGovernanceDecision(plannable, reason_code)`, also in `kriya/tools/tool.py`:
  - Origin OPERATOR is a separate authority. The resolver says so and decides nothing for it; `kriya tools execute` is unchanged.
  - For MODEL_AUTHORED: undeclared or unknown gives `TOOL_GOVERNANCE_UNDECLARED`; `OPERATOR_ONLY` gives `TOOL_OPERATOR_ONLY`. `READ_ONLY` and `GOVERNED_SIDE_EFFECT` are plannable in both modes, with per-call authority unchanged (AuthorizedFileReader / TOOL-002).
  - The mode is read through the OI-135 shared reader. An invalid mode is a typed `AutonomyModeError`.
  - Containment is an input. Under Option B it is recorded, not decisive. It becomes decisive only if Option A is later proven.
  - The two new codes join the closed `MODEL_TOOL_REFUSAL_REASON_CODES` table. No second policy system: per-call authority stays where S1 put it.
- **Consumers.**
  - (1) Planner catalogue: `stage_planning.py:106` and the `planner_repair` prompt list only plannable names.
  - (2) Plan validation: `enforce_loop.py:513` and `shadow_mode.py:187` pass the same plannable set. A registered but non-plannable tool gets its own plan reason code, e.g. `PLANNED_TOOL_NOT_PLANNABLE`, added to `_CODES_WITH_TARGETED_GUIDANCE` (tests/test_workflow_controller_enforce.py). It must not be mislabelled UNREGISTERED. The enforce-loop listing failure becomes `[]` (fail closed), not `None`.
  - (3) Executor recheck: `execute_model_authored` calls the resolver first, then `authorize_model_invocation`. B3 stays first in the executor, so human-in-the-loop shell evidence keeps `TOOL_CONFIRMATION_UNSUPPORTED` (no pin churn).
  - All three get the names through ONE helper, `subtask_executor.plannable_tool_names(registry, config)`.
- **Behaviour after.**
  - Planned shell and validate_refactor are refused (typed) in every mode, and never in the catalogue.
  - An undeclared plugin tool is unplannable everywhere.
  - Filesystem writes and git commit are unchanged (refused).
  - Reads and MCP are unchanged.
  - The operator path is unchanged.
  - The 10 P1 pins flip.
- **Characterization-flip set (what the owner signs away; each encodes a planned shell / validate_refactor / undeclared step running outside human-in-the-loop today; found by an AST scan of the test files):**
  - `tests/test_tool001_autonomous_tool_execution.py`: `test_scenario_c_policy_denial_zero_side_effect`, `test_policy_exception_fails_closed`, `test_scenario_g_shelltool_ordinary_command_unaffected_when_uncontained`, `test_scenario_g_shelltool_maven_registry_scoped_when_contained`, `test_scenario_g_shelltool_unmapped_manager_denied_when_contained`, `test_scenario_g_real_pip_through_autonomous_path_authorized_vs_unauthorized`. These are SEC-005 registry-scoped acquisition THROUGH A PLANNED shell step: Option B removes that capability for planned steps; the operator path and Kriya's own acquisition path keep it.
  - `tests/test_prd012_egress.py`: `test_planner_tool_subtask_contacting_an_attacker_host_runs_with_no_network` (becomes a refusal; the HITL twin is unchanged); `tests/test_prd012_egress_docker.py`: `test_hostile_plan_tool_subtask_cannot_reach_its_attacker_destination`.
  - `tests/test_e12_enforce_characterization.py`: `test_a_tool_subtask_recorded_completed_is_executed_again_on_resume`, `test_a_tool_subtask_that_did_not_complete_stops_the_run` (planned shell as the TOOL vehicle; they need a plannable tool instead). `test_shadow_tool_subtask_stops_the_pass_before_any_later_subtask` is shadow (non-executing) and is INFERRED unaffected.
  - `tests/test_s1_toolbox_authority.py`: `test_an_undeclared_tool_in_a_non_hitl_mode_keeps_its_own_authority`, `test_validate_refactor_outside_hitl_keeps_its_own_authority_and_reads_only_inside`.
  - `tests/test_lr_r1_m1_t6_fixes.py`: `test_d_an_executed_tool_subtask_is_a_unit_with_its_action_result`, `test_d_a_failed_tool_subtask_is_the_terminal_condition` (the t6 echo test tool needs a `planned_governance` declaration).
- **Owner question raised by R72/R73:** a third-party plugin's declaration is a self-claim. Today a plugin that overrides `authorize_model_invocation` to allow runs even in human-in-the-loop. Under the new declaration it could self-declare `READ_ONLY` and still write. Recommendation: a plannable declaration on a non-shipped tool is honoured only when operator-approved (TOOL-002 shape: durable, identity-bound) or the tool is MCP (already TOOL-002-governed). Otherwise it is treated as undeclared.
- **git reads** (OI-129 item 3). Make the root explicit: `GitTool._run_model_authored` with `cwd=invocation.roots[0]`, which preserves behaviour. Whether planned reads should look at the candidate instead is an owner question.

### 4c. Layering consequence (both options)
- **Edges.**
  - `kriya.tools.tool` (TOOLBOX) gains `kriya.config.authority` (AUTHORITY, public contract module). TOOLBOX -> AUTHORITY `pub` is allowed by the matrix, so no allowlist row is needed. `config.authority` imports only foundation and platform, so there is no cycle.
  - KERNEL consumers reach the resolver through `kriya.workflow.subtask_executor` (intra-KERNEL). Its edge to `kriya.tools.tool` already exists (`test_import_direction.py:406`, removal P2).
  - No new KERNEL -> `kriya.tools.tool` edge: stage_planning, enforce_loop, shadow_mode and planner_repair must NOT import `kriya.tools.tool` directly, or new rows appear.
  - `plugins/core_tools` is outside the module map.
- **Baseline.** No baseline change. Allowlist count unchanged (to be MEASURED by `tests/architecture` at the correction).
- **Option A extras, if ever proven.** `kriya.tools.containment` (EXECUTION public) from the plugin tools (outside the map), and `kriya.workflow.file_integrity` from enforce_loop (intra-KERNEL). Also count-neutral.

## 5. Verdict, stale contract statements (OI-129), OI-135

**Verdict:** OPTION_B_RECOMMENDED (deciding evidence: R53/R54/R57/R59 undetected + R41 reachable + R63 CONFIRMED forgery
channel; R80 shows no pre-trust check exists today; R41 shows the boundary itself is fine). STOP not triggered for B.
For Option A the triggered STOP condition is "scope integrity occurs only after evidence is trusted" (for git-ignored
content), with "OCI cannot prove the boundary" true only of today's owner-folder wiring (R22/R26/R29/R30/R31), not of
the candidate-root shape (R41).

**Stale statements (OI-129; files READ-ONLY here, corrected in the S18 correction commit):**
- `kernel-mcp-and-tool-authority.md`:
  - :15 "any future TOOL-tagged `SubtaskExecutor` dispatch - both call `BaseTool.execute(`": the dispatch exists and calls `execute_model_authored`, not `BaseTool.execute`.
  - :72 "converging on the exact same `kernel.registry.get(...) -> BaseTool.execute()` boundary ... `tool.execute(**subtask.tool_arguments...)`": now `execute_model_authored` plus the tool's `authorize_model_invocation`.
  - :74 "only the structured fields ever reach `tool.execute()`".
  - :76 "read off the real exception chain `BaseTool.execute()`'s `raise ... from e`". Also, "a non-COMPLETED result breaks" is true but misleading, because a shell exit 2/127 is COMPLETED (R21/R23).
  - :84 "A TOOL subtask is exactly as safe ... as the identical call made by an operator" and "exactly one `tool.execute()` call site": false since S1 (0 call sites; model-authored differs from operator) and contradicted for shell by R01-R11/R80.
  - :86 (evidence list, still accurate as a list; it should add the S1 pins and the S18 refusal evidence).
- `verification-platform-and-assurance.md`:
  - :41 "Candidate writes (`AuthorizedFileWriter`) never reach `.git/**`/`.kriya/**` ... Kriya's own `.kriya/` state is written only through `control_store`": true for AuthorizedFileWriter, but a planned shell writes both on `none` (R02, R06) and on production `oci` (R22, R26).
  - :8 "OCI identity ... never root": true; it should add that the workspace mounted is the process cwd for ShellTool (owner folder).
- `config-resolution-and-authority.md` :16 (S16a addition, recorded in OI-129): still says a violation is checked against a digest-bound approval. A malformed `autonomy.mode` is now a typed error before classification.
- `generation-pipeline.md` :12 should name the closed mode vocabulary.
- Registry row TOOL-SUBTASK-DESKTOP-REVIEW-001 remaining-text (OI-129 item 2).

**OI-135 shared autonomy-mode reader in this unit:**
- Add `autonomy_mode_of(config) -> str` in `kriya/config/authority.py`, the single owner. It applies `parse_autonomy_mode` to `config.autonomy.mode`, so a model_construct'd or `object.__setattr__` value gets re-checked at read time: `AutonomyModeError`, never permissive.
- The resolver and `ModelToolInvocation` consume it. `subtask_executor._autonomy_mode` maps an `AutonomyModeError` to a typed NEEDS_REVIEW. "No config" keeps meaning the strictest reading.
- Migrate every other consumer in the same unit, each with a mutant. S16a counted 7 literal `human-in-the-loop` comparison sites plus the `tools.tool` constant plus `application.run`: stage_apply (x2), spec_compliance, runtime_verification (x2), `policy.approval.defer_execution_until_source_review`, `subtask_executor._hitl_blocks_tool_without_confirmation`; plus `ModelToolInvocation.reviews_every_change` and `application.run._interaction`.
- `revalidate_instances='always'` only if the hot-path cost, measured at the correction, is negligible.

## 6. NOT_RUN / BLOCKED (kept separate)

- **NOT_RUN: a Linux host with Docker.** None was available in this session. The mount, identity and network rows are TRACED for Linux.
- **NOT_RUN: the Maven/Gradle incremental-build variant of the R63 channel and the pytest ignored-`conftest.py` variant.** Both are INFERRED.
- **NOT_RUN: the ignored-inclusive snapshot cost** (a full candidate walk including `target/`, `node_modules/`).
- **NOT_RUN: `git -C` inside OCI with a git-bearing image.** The default image has no git (R23 exit 127). The equivalent direct file write was measured (R22).
- **NOT_RUN: an operator `kriya tools execute` run in a separate process.** R74-R76 drive the real CLI through CliRunner in-process. Operator git commit and plugin rows are TRACED.
- **NOT_RUN: a real MCP server.** The MCP rows come from the existing TOOL-002 scenario tests (T1).
- **NOT_RUN: the candidate-root `_run_model_authored` seam for validate_refactor.** TRACED only.
- **SIMULATED, not real: the host without Docker (R71).**
- **BLOCKED: none.**
- **Full pytest: NOT_RUN** (phase 1 adds only a strict-xfail test file). Targeted runs: P1 (10 xfailed; `--runxfail` 10 failed), T1 (7 passed), P1 + S1 file together (76 passed, 10 xfailed).
- **NOT_RUN: the declared-plugin and operator probes on `oci`.** R72-R76 ran on `none` only.
- **Lint:** ruff (whole repo) rc 0, pylint rc 0 at 25997b2, abe74f3, 36fd3b2 and at this commit.
