# S19 PHASE 1: measured preflight (TYPE C, gate-input integrity; OI-141, OI-142)

**VERDICT.**
- **(a) OI-141, Kriya's own git over repository-controlled configuration.** First wrong owner: NO single git invocation
  owner exists. 56 spawn sites in 16 modules (+ the GitTool plugin) each build and spawn their own argv over the
  repository's configuration; only `git_metadata._probe` (GUI Open Folder) and the hook-capable worktree/skill/GitTool
  writes carry partial hardening. MEASURED (684 probes): repository configuration runs code ON THE HOST through 26 of
  the 38 probed Kriya call sites (never: S04, S05, S13, S14, S16, S20, S26-S29, S32, S33) (core.fsmonitor in 25 sites, also through include.path / includeIf / config.worktree / a
  submodule; post-index-change / post-checkout / reference-transaction hooks; content-filter drivers in 19 sites; diff
  drivers in GitTool diff/blame; gpg.program in the bootstrap and skill commits).
  - **Correct in phase 2 (owner-coherent, golden/digest/baseline-neutral, MEASURED byte-identical outputs on 684
    probes):** one invocation seam in PLATFORM (`kriya.platform.git_argv`) inserting `-c core.fsmonitor=false
    -c core.hooksPath=/dev/null` at spawn time, after the policy audit, at every call site; plus an AST enforcement
    test. Closes every fsmonitor and hook vector measured (incl. include/includeIf/worktree-config/submodule).
  - **Correct in phase 2 as a FAIL-CLOSED CHOICE, owner question Q2/Q3 open:** `-c commit.gpgSign=false` in the same
    seam (closes gpg.program; user-visible only for users who sign), and `--no-ext-diff --no-textconv` / `--no-textconv`
    on GitTool's patch-producing diff/blame (closes diff drivers; user-visible only for users with global drivers).
  - **STOP (would move an evidence-identity digest), Q1:** content-filter drivers (`filter.<x>.clean|smudge|process`,
    from .git/config, includes, `.git/info/attributes` + `.gitattributes`). Neutralising them changes
    `compute_workspace_content_hash` / `workspace_evidence_hash` (MEASURED 4b9c78c1... -> 28da0150...), and with
    `filter.<x>.required=true` turns the content hash into None, the fingerprint `dirty` into `clean` (wrong), and makes
    status and worktree creation fail. This vector stays OPEN (MEASURED host execution, incl. the checkpoint hash every
    run computes) until the owner decides.
- **(b) OI-142, ignored/generated artifacts forging gate evidence.** First wrong owner: the verification-integrity
  contract itself (FILE-INTEGRITY-CONTRACT-001/001B: "ignored build output is never repository content", `.kriya/`
  excluded) together with gates executing candidate code inside the read-write candidate, whose ignored content and
  `.kriya/venv` persist across gates and work units (MEASURED). R63 reproduced through the REAL python test gate
  (`PolymorphicValidator.run_tests`) in 4 forms; `VerificationTreeBinding` detected none.
  - **No phase-2 correction** (Q4): no option is both complete and neutral. A fresh pycache prefix closes only the two
    `__pycache__` forms and costs +0.93 s per pytest gate (0.357 s -> 1.287 s median, 3.6x, MEASURED); the sourceless
    `.pyc` and ignored `conftest.py` forms survive every Python flag and purge; the acceptance runner is bound by
    `RUNNER_CONTRACT_DIGEST` (human approvals bind it), so changing its invocation is an owner decision;
    `.kriya/venv` (the contained gate's own pytest and dependencies) sits in the same read-write mount and is a larger
    channel than bytecode (TRACED + R41/R59 MEASURED in S18).

Base: `arch/full-modularization-v1` @ 267f7a4 (clean). Host: macOS Darwin 27.0.0, git 2.54.0, `.venv` Python 3.14.
Scratch (every reproducer, raw JSON):
`/private/tmp/claude-501/-Users-sriramnanduri-WorkingDirectory-AI-ClaudeCode-Kriya-By-ClaudeCode/bc971a42-dc25-4273-ba41-f653fa8c96f5/scratchpad/s19/`
(`probe_git.py`, `runprobe.sh`, `results/results_<mode>.json`, `probe_filter_stop.py` + `filterstop/filter_stop.json`,
`probe_r63.py` + `r63/r63_results.json`, `perf_prefix.py`, `probe_persist.py`, `perf_git.py`). Every probe runs with
`env -i`, HOME / TMPDIR / KRIYA_* / GIT_CONFIG_GLOBAL pointed at scratch, GIT_CONFIG_NOSYSTEM=1, fixed GIT_*_DATE, stdin
`/dev/null`; every fixture, marker and output is under scratch.

## Undecided owner questions (fail-closed choice in brackets)

- **Q1 (STOP, digest):** content-filter drivers. Options: (A) blank repository-scoped (local / worktree / included)
  `filter.*.clean|smudge|process` and force `required=false` (git_metadata `_filter_overrides` precedent, which blanks
  filters from EVERY scope for `status` only): moves the content/evidence hash for such repositories (MEASURED diff
  below), changes candidate bytes where a smudge transforms; (B) refuse, typed, a workspace whose repository scope
  defines a content-filter command (fail-closed, no digest move, a new refusal); (C) document as operator-trusted (a
  repository's own .git/config is trusted). Recommendation: B for the run (typed refusal before any Kriya git), with A
  kept for read-only GUI probes as today. [Nothing implemented; the vector stays open.]
- **Q2:** Kriya's commits (greenfield init + bootstrap empty commits, skill bookkeeping commit, operator GitTool commit)
  never sign. [Implemented fail-closed: `commit.gpgSign=false` in the seam; a user with global signing gets unsigned
  Kriya bootstrap/skill/GitTool commits.] Alternative: neutralise only repository-scoped `gpg.*` (a blacklist of the
  gpg key family; rejected as incomplete by construction).
- **Q3:** GitTool diff/blame output with drivers. [Implemented fail-closed: `--no-ext-diff --no-textconv` (diff),
  `--no-textconv` (blame); a user's GLOBAL external diff / textconv no longer shapes GitTool output.] Alternative:
  blank only repository-scoped `diff.*` drivers (an extra `git config` spawn per call).
- **Q4 (OI-142):** see section 5 for the option table; recommendation there.

## 1. Git inventory (TRACED; file:line at 267f7a4)

Every site spawns with raw `subprocess` (no ProcessController) except GitTool (ProcessController, `none` backend,
TRUSTED_KRIYA_INFRASTRUCTURE). Every site inherits Kriya's whole environment (GIT_* included) except
`git_metadata._probe` (`_probe_env`: PATH/HOME/GIT_OPTIONAL_LOCKS/GIT_TERMINAL_PROMPT/LC_ALL) and the scratch-index
sites (`child_environment()` + `GIT_INDEX_FILE`). Every site therefore sees system + global + local + worktree config
and everything they include, plus the repository's `.gitattributes` / `.git/info/attributes` and `.git/hooks`.

| site | file:line | purpose | R/W | cwd | probe id |
|---|---|---|---|---|---|
| tracked_release_files | distribution.py:30 | `ls-files -z -- trees` | R | Kriya source root | S23 |
| _check_git_worktree | production_doctor.py:698-699 | `rev-parse`, `worktree list` (argv0 = `shutil.which`) | R | workspace | S26 |
| review | cli.py:3749 | `status --porcelain -z` of a file's repository | R (index refresh) | file's dir | S38 (argv replica) |
| untracked_repository_paths | repository/snapshot.py:233,236 | `rev-parse`, `ls-files --others --exclude-standard` | R | root | S01 |
| git_metadata._git | tools/git_metadata.py:103 | export: `init --bare`, `pack-objects`, `index-pack`, `rev-parse` | W (export repo in state) | owner / export | S27, S29 |
| _filter_overrides / _probe | git_metadata.py:178,188 | GUI Open Folder probes (already: `--no-optional-locks`, fsmonitor off, status filters emptied) | R | folder | S28 |
| kriya_revision | core/release_identity.py:63-64 | `rev-parse HEAD`, `status --untracked-files=no` | R | Kriya source root | S22 |
| operator_scan._git | static_analysis/operator_scan.py:27 | `diff --name-only`, `ls-files`, `worktree add/remove` | R + W (worktree admin, checkout) | workspace | S24, S25 |
| discover_source_files | code_intel/service.py:77 | `ls-files --cached --others` | R | root | S21 |
| checkpoint | workflow/checkpoint.py:63,72,169,174,186,192,245,258 | fingerprint, content hash (scratch index: `read-tree`, `add -A`, `rm --cached`, `write-tree`), tree hash, base commit | R + W (objects) | workspace | S11-S14 |
| workspace_evidence_hash | workflow/milestone_completion.py:372-375 | scratch index `read-tree`, `add -A`, `add -u`, `write-tree` | R + W (objects) | workspace | S15 |
| worktree | workflow/worktree.py:101,123,268,272,295,352,373,399,416,485,492,535,554,572,577,580,592,593,751,752 | worktree list/remove/add/prune, content paths, status, HEAD, greenfield `init` + commit, toplevel, `git_read_lines` (cli:2763, requirement_closers:209,232,658), bootstrap commit, reuse `checkout -f` + `clean -fd` | R + W | owner / candidate | S02-S10 |
| named_test_oracle | workflow/named_test_oracle.py:131(_git:167),152,173 | `ls-tree`, `cat-file --batch`, `archive` | R | base repo | S16, S17 |
| git_commit_if_tracked | skills/skill.py:89,94,101 | `rev-parse`, `add`, `commit` | W | skill dir | S18 |
| analyzer | analyzer/analyzer.py:430,433,984,991,992 | revision + dirty; incremental `diff --name-only`, `diff --cached`, `ls-files --others` | R | root | S19, S36, S37 (replicas) |
| _git_revision | control/run_coordinator.py:91 | `rev-parse` | R | workspace | S20 |
| GitTool | plugins/core_tools/__init__.py:523,555 | `rev-parse --show-toplevel`; status/diff/log/branch/blame/commit | R (commit W) | owner / candidate / cwd | S30-S35 |

56 spawn sites in 16 kriya modules + the plugin. The literal-"git" finder (tests/architecture access_sites) misses the
variable-argv0 sites (`production_doctor`, `git_metadata._probe/_filter_overrides`) and GitTool's appended `cmd`.

## 2. Config-key table (MEASURED, 18 classes x 38 call sites = 684 probes per mode)

Fixture per probe: an owner repository with a committed `a.py`, `.gitattributes` `* filter=kx diff=kx merge=kx`,
`sub/b.txt`, `.gitignore`, then a tracked modification, an untracked `u.py` and `skill.md`; the class's configuration
is planted AFTER setup (setup git is hardened and never patched); one marker file per key. "Sites" lists the probe ids
whose call created the key's marker.

| class (planted) | executes through Kriya's own git today (MEASURED) | seam `-c core.fsmonitor=false -c core.hooksPath=/dev/null` | + `commit.gpgSign=false` | GitTool `--no-ext-diff --no-textconv` |
|---|---|---|---|---|
| `core.fsmonitor` (local) | S01-03, S06-12, S15, S18, S19, S21-25, S30, S31, S34-38 (25 sites) | CLOSED (0) | - | - |
| `core.fsmonitor` via `include.path` | same 25 sites (+ the included `filter.kx.clean` in 17) | fsmonitor CLOSED; included filter OPEN (Q1) | - | - |
| `core.fsmonitor` via `includeIf "gitdir:/"` | same 25 sites | CLOSED (0) | - | - |
| `core.fsmonitor` via `config.worktree` (`extensions.worktreeConfig`) | 24 sites | CLOSED (0) | - | - |
| `core.fsmonitor` in a SUBMODULE's config (status recursion) | S03, S07, S08, S11, S12, S15, S19, S22, S24, S30, S31, S35, S36, S38 | CLOSED (0): `-c` propagates through GIT_CONFIG_PARAMETERS | - | - |
| hooks in `.git/hooks` | post-index-change: S03, S07, S08, S11, S12, S15, S18, S19, S22, S30, S38; post-checkout + reference-transaction + post-index-change: S25 (operator_scan `worktree add`) | CLOSED (0) | - | - |
| hooks via `core.hooksPath` | same (S38 timing-dependent: post-index-change runs only when status refreshes the index) | CLOSED (0) | - | - |
| `filter.kx.clean/smudge` (+ `.gitattributes`) | clean: S03, S07-09, S11, S12, S15, S18, S19, S22, S24, S30, S31, S34-36, S38; smudge: S07-09, S17 (archive), S25 | OPEN (19) | OPEN | OPEN (Q1, STOP) |
| `filter.kx.process` | same 19 sites (a failing process filter makes worktree add / checkout / archive / diff FAIL) | OPEN | OPEN | OPEN (Q1) |
| `.git/info/attributes` + config drivers | same 19 sites + GitTool diff/blame textconv | filter OPEN; textconv see diff | - | textconv CLOSED (see section 2a) |
| `diff.kx.textconv`, `diff.kx.command`, `diff.external` | S31 GitTool diff (`diff.kx.command`), S34 GitTool blame (`textconv`) | OPEN | - | see section 2a |
| `commit.gpgSign` + `gpg.program` | S10 (bootstrap empty commit), S18 (skill commit); GitTool commit TRACED (same commit path; the fixture had nothing staged) | OPEN | CLOSED (0); the commits then SUCCEED (before: failed through the hostile gpg) | - |
| `core.pager`, `pager.*`, `core.editor`, `sequence.editor` | none (no tty: captured output; `-m` messages) | - | - | - |
| `core.sshCommand`, `credential.helper`, `core.askPass`, `core.gitProxy`, `core.alternateRefsCommand` | none (Kriya runs no network git command) | - | - | - |
| `alias.<builtin>` (status, diff, add, commit, ...) | none (git never lets an alias shadow a builtin) | - | - | - |
| `trace2.eventTarget/normalTarget` (local) | none (trace2 is read from system/global only) | - | - | - |
| `safe.directory` (local) | no effect (honoured only from system/global/command line) | - | - | - |

Not executing but behaviour-relevant (TRACED, not changed): `core.worktree` in repository config redirects the work tree
Kriya's calls describe; `core.excludesFile` / global config shape `--exclude-standard` (and so
`untracked_repository_paths` and the binding); `GIT_DIR` / `GIT_WORK_TREE` / `GIT_INDEX_FILE` / `GIT_CONFIG_*` in Kriya's
own environment redirect every inheriting site. These are operator/global scope, not repository-controlled; global and
system scope stay operator-trusted (GIT_CONFIG_GLOBAL=/dev/null or GIT_CONFIG_NOSYSTEM would change outputs callers
depend on: `safe.directory`, `core.excludesFile`, the skill commit's global identity, `core.autocrlf` (M4)).

### 2a. Positive controls (MEASURED, same fixture base path in every mode)
- plain vs seam (fsmonitor + hooksPath): 684/684 outputs byte-identical; the only differences are two error MESSAGES
  that quote the longer argv (filter_process S07, gpg S10), same exception class and failure.
- plain vs seam + gpgSign: identical except the 2 gpg probes, which now succeed (S10 worktree created, S18 commit made).
- plain vs seam + GitTool driver flags: identical except GitTool diff/blame in the two classes that define a diff
  driver (`diff`, `attributes_info`): the driver no longer runs and the output is git's own diff/blame (before: the
  driver's output). `blame --no-textconv` is accepted by git 2.54.0 (no error).
- FULL mode (all of the above + a scoped filter blank) fired 74 markers, all filter-class: the harness's scope parser
  split `--show-scope -z` records on TAB, but `-z` separates the scope with NUL (`od -c` MEASURED), so the scoped blank
  never applied. OWN HARNESS DEFECT (TEST_HARNESS), preserved in `results/results_hardened_full.json`; the filter-blank
  effect is measured separately and correctly in 2b. Every non-filter class: 0 markers in FULL mode.
- The scope enumeration (`git config --show-scope --includes --list -z`) fires no marker and reports keys from an
  included file as `local` (MEASURED, `scopecheck/`).

### 2b. Q1 STOP diff (MEASURED, `probe_filter_stop.py`; a transforming repository-scoped filter: clean = upper-case,
smudge = lower-case; tracked `a.py` modified)

| value | plain (filter runs) | filter blanked | filter blanked, `required=true` |
|---|---|---|---|
| compute_workspace_content_hash | 4b9c78c1f3f0321abcbc770ca12d7492514292a18c180bfe1a0d48402bf56d3b | 28da0150e021afa411f99b97020e9ead68d3a803f1496b2e038a5ce75ba3071d | None (resume unavailable) |
| workspace_evidence_hash | 4b9c78c1...56d3b | 28da0150...3071d | None |
| compute_tree_hash | 4da6efe8030ec59526af08b3c0c1f8814470d205 | same | same |
| compute_workspace_fingerprint | 23b78a30...:dirty | same | 23b78a30...:clean (WRONG: the file is modified) |
| analyzer._repository_revision | 23b78a30...+dirty | same | 23b78a30... (dirty lost) |
| git_status_entries | [(" M", "a.py")] | same | RuntimeError `clean filter 'kx' failed` |
| create_git_worktree | ok | ok | CalledProcessError (worktree not created) |

## 3. The seam (design, for phase 2)

- **Where.** `kriya/platform/__init__.py` defines `git_argv(argv) -> list`: the one owner of how Kriya invokes git.
  PLATFORM (L0) is in every module's allowed set (`_BASE`), `kriya.platform` is already a declared public contract, the
  package imports only `os`, so: no allowlist row, no module_map change, no new cycle (MEASURED by the architecture
  tests at the correction). `kriya.repository.git` (the planned M3 owner) is NOT importable by ADAPTER-CLI
  (`cli`, `distribution`, `production_doctor`: RESTRICTED to the composition modules) without three new rows: STOP shape,
  so not chosen; M3 may move the function, not the rule.
- **What.** `[argv[0], -c core.fsmonitor=false, -c core.hooksPath=/dev/null, -c commit.gpgSign=false, *argv[1:]]`
  (global options before the subcommand); argv0 must be `git` by basename (absolute paths from `shutil.which` /
  `_git_candidates` accepted), anything else raises; a NEW list (the audited argv is never mutated).
- **When.** At spawn, AFTER the policy audit: `_audit_git_write` and the POL-001-P3 bootstrap recognizers
  (`_is_kriya_internal_bootstrap_commit/_init`, exact-prefix matches) keep seeing today's argv, so no policy decision
  changes (TRACED kriya/policy/execution.py:452-510; tests/test_worktree_policy_audit.py pins the audited argv).
- **Counts.** Every `["git", ...]` literal and every `subprocess.*` call stays where it is (the seam wraps the literal
  in place): `test_direct_access_owners` subprocess / git_argv counts unchanged; the seam module holds no git literal.
- **Enforcement.** An AST test over kriya/ and plugins/: every list/tuple literal whose first element is `"git"` or a
  name `git` is either an argument of `git_argv(...)` or bound to a local name that the same function passes to
  `git_argv(...)`; a closed exemption table for non-spawn literals (the plugin's `ActionRequest(command=("git", sub))`);
  no `asyncio.create_subprocess_exec("git", ...)` / string spawn; planted controls for each shape. Plus a dynamic test:
  a recording Popen over the real call sites asserts every spawned git argv carries the options.
- **GitTool.** `diff` gains `--no-ext-diff --no-textconv`, `blame` gains `--no-textconv` (Q3), at the tool's own
  argv construction (the owner of its fixed subcommand shapes).

## 4. OI-60 (per-call cost)
MEASURED (`perf_git.py`, scratch repository with 2,000 tracked files, 60 timed runs after 3 warm-ups, median):
`status` 11.82 ms plain vs 11.21 ms with the three `-c` options; `ls-files --others` 7.99 vs 8.11 ms; `rev-parse` 5.67
vs 5.56 ms: within noise (no extra spawn; the options are parsed in-process). A user whose GLOBAL config enables
`core.fsmonitor` loses fsmonitor acceleration in Kriya's own calls (output unchanged). The rejected scoped-enumeration
alternative (Q1 A / Q3 alternative) costs one extra `git config` spawn (~5 ms) per call.

## 5. OI-142 ignored/generated artifacts

### 5a. R63 through the real gate runner (MEASURED, `probe_r63.py`)

Candidate: a git repository, `.gitignore` = `__pycache__/`, `*.pyc`, `.pytest_cache/`; reviewed `calc.py` returns 1;
tracked `tests/test_calc.py` asserts 2. An honest gate FAILS. Each form is what candidate code executed by an EARLIER
gate can leave behind (all git-ignored). Gate: `PolymorphicValidator(candidate).run_tests()` (host backend, the
validator's own interpreter resolution, `VerificationTreeBinding` bound over the repository content before the gate).
Mitigations are SIMULATED at spawn (the gate's interpreter argv/env rewritten), before any product change.

| form | none | PYTHONDONTWRITEBYTECODE | `-B` | `--check-hash-based-pycs always` | PYTHONPYCACHEPREFIX (fresh, outside) | `-X pycache_prefix` (fresh) | purge `__pycache__` |
|---|---|---|---|---|---|---|---|
| honest (no plant) | FAIL | FAIL | FAIL | FAIL | FAIL | FAIL | FAIL |
| timestamp `.pyc` (R63: forged source compiled, reviewed size + mtime) | PASS forged | forged | forged | forged | FAIL (closed) | closed | closed |
| unchecked-hash `.pyc` (PEP 552: never validated against the source, no size/mtime needed) | forged | forged | forged | closed | closed | closed | closed |
| sourceless ignored `helper.pyc` (reviewed source imports `helper`) | forged | forged | forged | forged | forged | forged | forged |
| ignored `tests/.gitignore` `*` + `tests/conftest.py` patching `calc.f` | forged | forged | forged | forged | forged | forged | forged |

`VerificationTreeBinding`: DETECTED none of the 28 forged runs (it binds tracked + candidate paths only; ignored is out
of contract). `gate_output_roots` authorises `__pycache__/` and `.pytest_cache/` for every python gate, so even an
ignored-inclusive binding could not tell a planted `.pyc` from a legitimate one.

### 5b. Persistence (MEASURED, `probe_persist.py`)
A planted ignored `__pycache__/calc.cpython-314.pyc` and `candidate/.kriya/venv/lib/PLANTED` in the managed worktree
SURVIVE the next `create_git_worktree` (reuse path: `checkout -f HEAD` + `clean -fd`), i.e. the next work unit; an
untracked non-ignored file does not.

### 5c. Gate-runner route table (TRACED unless marked)

| runner | ignored / generated input it reads | bound by VerificationTreeBinding | route for candidate code to write it | closed by |
|---|---|---|---|---|
| python test gate (`PIP.run_tests`: `python -c "...pytest.main"`, no `-B`) | in-tree `__pycache__/*.pyc`, sourceless `*.pyc`, ignored `conftest.py` / modules / packages under the roots it appends (`.`, `src`, `src/main/python`, ...) | no | any earlier gate executing candidate code (tests, app) on `none` (anywhere) or `oci` (rw candidate mount); persists (5b) | prefix: pyc forms only (5a) |
| python compile gate (`py_compile` contained / in-process `compile()`) | writes `__pycache__` from the reviewed source; reads nothing ignored | no | - | n/a |
| acceptance runner (`python -I -B runner`, `--noconftest`, `--import-mode=importlib`, staged in `.kriya/<uuid>`) | candidate modules' in-tree `__pycache__` / sourceless `.pyc` (the staged module is fresh per run) | no | same | prefix would change the runner contract bound by `RUNNER_CONTRACT_DIGEST` (acceptance approvals bind it): OWNER |
| contained python environment | `candidate/.kriya/venv` (pytest + dependencies the gate imports) | no (`.kriya/` excluded by design, R59) | contained gate writes `candidate/.kriya` (R41 MEASURED in S18) | none of the flags; needs a read-only or out-of-tree venv: OWNER |
| python `run_app` / runtime verification | same as the test gate | no | same | prefix (pyc only) |
| Maven (`clean compile`, then `test`) | `target/` | no | test code writing `target/classes`; `clean` before each compile wipes it (INFERRED: a `test` run not preceded by `clean compile` would read it) | partially by `clean` |
| Gradle (`build/`, root `.gradle/`) | incremental build state | no | same | NOT_RUN |
| javac (`-d build`) | `build/` | no | same | NOT_RUN |
| Ruby (`bundle install --path vendor/bundle` -> `.bundle/config`, then `bundle exec`) | `.bundle/config`, `vendor/bundle` | no | same | NOT_RUN |
| named-test oracle | base exported by `git archive` into a Kriya-owned directory OUTSIDE the workspace | n/a | - | already isolated (smudge filters still run: Q1) |
| node | no node gate runner exists | - | - | - |

Model-authored edits (AuthorizedFileWriter) into an ignored path are part of `candidate_paths` (development_loop.py:683,
724: `state.all_files_written`), so they ARE bound (TRACED).

### 5d. Options (Q4) and what each does to evidence identity

| option | closes | cost / identity effect |
|---|---|---|
| fresh pycache prefix per python gate (`sys.pycache_prefix` in Kriya's runner script, or env) | timestamp + unchecked `.pyc` | +0.93 s per pytest gate (3.6x; recompiles pytest and every dependency each gate); pytest runner script not digested; acceptance: changes the digest-bound runner contract (OWNER) |
| per-run prefix outside the candidate | same (cheaper) | writable by host-`none` gate code anyway; under `oci` must be mounted rw -> plantable again |
| purge ignored `__pycache__` before each python gate | timestamp + unchecked `.pyc` | a Kriya delete inside the workspace: needs the authorized-writer / inventory classification; recompiles project modules only |
| ignored-inclusive binding | detects new/changed ignored files between gates | contract change (001B); legitimate gate output is indistinguishable in `__pycache__`/`target`; would change run outcomes |
| read-only candidate mount + out-of-tree venv / caches (oci) | every route under `oci` | contradicts "generated build output is legitimate" (S18 section 4a); new machinery |
| `--check-hash-based-pycs always` | unchecked-hash `.pyc` only | cheap; incomplete |

Recommendation: an owner decision on the contract first (are ignored artifacts written by gate code trusted inputs to a
later gate?). If "no", the coherent owner is the gate-execution environment (fresh per-invocation bytecode prefix plus an
out-of-tree, read-only verification venv under `oci`), with `--noconftest`-style refusal of ignored `conftest.py` and
sourceless `.pyc` (a pre-gate typed refusal when ignored importable files exist under the gate's roots). None is done in
S19.

## 6. NOT_RUN / limits
- Linux host: NOT_RUN (git 2.54.0 on macOS only; key semantics are git's, INFERRED to transfer).
- An older git: NOT_RUN (only 2.54.0 measured; `-c core.fsmonitor=false` is already relied on by `git_metadata._probe`,
  and `core.hooksPath=/dev/null` by worktree/skill/GitTool since SEC-001-P1).
- cli `review` and analyzer `index_repository` were measured through argv replicas (S36-S38), not the full commands.
- GitTool commit with a staged change: TRACED (the probe fixture had nothing staged; the commit path is the same `git
  commit` the skill site measured).
- OI-142: Maven/Gradle/javac/Ruby variants NOT_RUN; OCI variant of R63 NOT_RUN (the container route to the candidate
  mount is S18 R41, MEASURED).
- Lint at this commit: see the report.

## 7. Phase 2 result (appended after the correction; sections 0-6 are the pre-fix evidence, unchanged)

- Commits: 4668ddb characterization; 3fee52b seam; c8f499e REPOSITORY; a24233b KERNEL; 1529824 EXECUTION; 873d290
  SKILLS; 5cb6e0a RETRIEVAL; c0b3800 STATE; a27a65e QUALIFICATION; b2579b7 ADAPTER-CLI; d3a66db PLUGIN; 67c1f60
  enforcement. Each owner commit ran the S19 file, tests/architecture and every test file naming that owner's functions.
- Original reproducer re-run on the fixed tree (`probe_git_post.py`, same fixtures, plain mode, MEASURED): 88 markers,
  all of them either the held filter classes (filter, filter_process, attributes_info: 57; include_fsmonitor's
  included filter: 15 of 18) or the harness's OWN argv replicas S36-S38 (raw `subprocess.run` in the probe, not Kriya
  code: the analyzer `index_repository` and cli `review` sites themselves are wrapped, TRACED + the AST rule + the
  dynamic twin). Every real Kriya call site: 0 fsmonitor / hook / gpg / diff-driver markers. Outputs vs pre-fix: 7
  differences, all intended (gpg S10/S18 now succeed; GitTool diff/blame show git's own output where a driver was set;
  one error message quotes the longer argv).
- Architecture: `test_direct_access_owners` subprocess / git_argv counts, the cycle baseline, the import-direction
  allowlist and module_map are unchanged (no new edge outside `_BASE`).

## 8. Q1 result (appended 2026-10-20; owner decision: REFUSAL, not blanking; sections 0-7 are unchanged evidence)

Origin MEASURED (git 2.54.0, `git config --show-scope --show-origin -z --get-regexp '^filter\..*\.(clean|smudge|process)$'`;
the record is `scope NUL origin NUL key LF value NUL`; the inspection fired no planted driver in any case):

| composition | reported scope / origin | decision |
|---|---|---|
| `.git/config` clean / smudge / process | local / `file:.git/config` | refuse (REPOSITORY_DEFINED) |
| `include.path` (absolute), nested include, `includeIf "gitdir:/"` | local / the included file | refuse |
| `include.path = ../x.gitcfg` (a work-tree file) | local / `file:.git/../x.gitcfg` | refuse |
| `config.worktree` (`extensions.worktreeConfig`), also a reused linked worktree's own | worktree / `file:.git/config.worktree` | refuse |
| repository override of a trusted global filter | global + local records | refuse (origin decides) |
| global only (git-lfs-like) | global / `~/.gitconfig` | trusted: works, a repository may reference it |
| global `include` / `includeIf` of a file INSIDE the repository | global / the repository file | refuse (ORIGIN_IN_REPOSITORY; fail-closed choice, owner question) |
| global `include.path` relative | global / relative to the global file's folder (never the repository) | trusted |
| `GIT_CONFIG_COUNT`, `GIT_CONFIG_PARAMETERS`, `-c` | command / `command line:` | trusted (Kriya's own process) |
| `filter.x.required`, other metadata keys | not listed (rc 1) | not refused |
| empty command | local, value "" | not refused (runs nothing, MEASURED) |
| valueless key / whitespace command | local, value None / "   " | refuse (fail closed) |
| broken config | rc 128 | refuse (GIT_CONFIG_UNINSPECTABLE) |
| submodule / embedded repo (no `.gitmodules`) / gitlink only HEAD has | parent lists nothing (rc 1); the child's local scope | MEASURED firing through parent status/diff/add and the scratch-index `add -A`: inspected recursively, refuse |
| untracked nested repository | - | MEASURED never fires (status/add do not recurse): not inspected |

Correction: kriya/repository/git.py + one inspection immediately before every filter-capable Kriya git operation (and
at `begin_mutating_run`); no memoization; NO digest moved (golden predictor on a filter-free repository unchanged);
details in S19Q1-DECISION-TO-KILLER.md. Not covered (STOP / owner): `kriya review <folder>` (ADAPTER-CLI ->
REPOSITORY needs one new import-direction row), Kriya's own source root (operator installation).

S19 P1c correction to the table above (2026-10-20, the rows stay as measured then): "untracked nested repository -
never fires" was incomplete. MEASURED at 135acfe (relayed F3, re-derived in scratch p1c): workspace_evidence_hash's
scratch-index `add -A` records an untracked embedded repository as a NEW gitlink and the following `add -u` runs that
repository's git and its own clean filter (`status` and `add -A` alone still do not; no `-c` option prevents it). Now
the scratch index is inspected with its own GIT_INDEX_FILE immediately before `add -u` (S19P1c-DECISION-TO-KILLER.md).
Also measured since: `diff --name-only <base>` (mutation_scope_evidence) runs a clean filter; a local
`includeIf "gitdir:**/worktrees/**"` applies only in a linked worktree's context (worktree add's checkout); and the
TRUSTED rows (global only, command scope) are trusted by origin only when the command's executable code is the
operator's too (executable-code provenance, GIT_CONTENT_FILTER_EXECUTABLE_UNPROVEN).
