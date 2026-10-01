# PRD-031A — Pluggable Static Analysis, Policy and Risk-Acceptance Gate

**Phase:** Wave 6 (roadmap correction, 2026-09-27: before PRD-032)
**Priority:** P1 (approved 2026-09-27)
**Dependencies:** PRD-030 (TerminalGateService), PRD-031
**Live test:** REQUIRED. This means the real, pinned-scanner tier `live_static_analysis` (§18). No live-model test is required (approved 2026-09-27).
**Status:** SPEC_APPROVED (2026-09-27, with the corrections in §20). Implementation has not started and begins only on the user's instruction.

Requirement source: the user's PRD-031A specification directive (2026-09-27), summarized in `handover/PRD-031A_DIRECTIVES.md`. This document is the task file. The instructions package has none for PRD-031A.

---

## 1. Problem

Kriya proves a candidate compiles, passes its tests, keeps the original requirements and stays inside its authority. It never asks whether the candidate introduced a security or quality defect that a static analyzer would catch.

Production brownfield work needs that signal. It also needs three properties that a "run semgrep" step would not give:

1. **Honest coverage.** A scanner being configured is not a repository being analyzed. A Java rule pack says nothing about a C++ file.
2. **Brownfield attribution.** Debt that already existed must not be charged to the candidate. A finding the candidate introduced must never hide among it.
3. **Governed risk acceptance.** A finding that would block can continue only through an explicit, trusted, auditable waiver. The model can never create one, and an inline comment the model writes must not act as one either (§8.4).

## 2. Objective

Add a provider-neutral static-analysis gate with these properties:
- It sits at every terminal commit boundary.
- It can be disabled, but disabled is never reported as PASS.
- It checks the provider's capability against the candidate's languages before claiming any coverage.
- It compares PRE and POST findings over the minimum scan scope the provider needs for trustworthy analysis (§5.4). That can be the changed files, their module, the repository or the build graph. POST is exactly the bytes the commit will write, laid over the unchanged base.
- Its policy is configurable and owned by Kriya, not by the adapter.
- A blocking finding can be released only by an operator-created waiver, and a run that relies on one reports ACCEPTED_RISK, never PASS.
- The evidence it produces is bound to all of the following: the candidate, the base/workspace identity of the scope, the scope itself, the provider runtime, the rule packs and the effective settings. A commit refuses evidence that is missing or stale (§10.4).

Semgrep (local CLI, Community Edition engine, local rule packs) is the first adapter. It is not the architecture: no Semgrep type crosses the port.

## 3. Current-source anchors (read first)

| Anchor | Why |
|---|---|
| `kriya/workflow/terminal_gate_service.py` | Enforce terminal gates: `TerminalGateService.run`, `TerminalGateReport.commit_eligible`, `global_gaps()` |
| `kriya/workflow/commit_service.py` (`terminal_writes`, `commit_verified_candidate`) | Enforce commit entry |
| `kriya/workflow/terminal_commit.py` (`CandidateFile`, `materialize_candidate`, `commit_terminal_candidate(writes, ...)`) | **The one commit implementation both paths share.** This is where static-analysis evidence is bound (§10.4). |
| `kriya/workflow/workflow.py` ~3709-3810 (pre-apply boundary, `REQUIREMENTS_UNRESOLVED` block) and ~4920 (`commit_terminal_candidate` call) | Direct and milestone terminal boundary |
| `kriya/workflow/workflow_controller.py` `_run_structured_enforce` (`_emit_gate_outcome`, `TerminalGateValidators` binding) | Enforce terminal orchestration |
| `kriya/cli.py` `_dispatch_generation` / `_dispatch_milestones` | Production routing (see §10.1) |
| `kriya/workflow/validation_baseline.py` (`classify_baseline_delta`, NOT_COMPARABLE) and `baseline_policy.py` (`baseline_environment_identity`) | PRE/POST precedent (PRD-024) |
| `kriya/policy/egress.py` (`EgressCapability`, `capability_for`), `kriya/workflow/egress_authority.py`, `tests/test_prd012_network_inventory.py` | Egress vocabulary and inventory |
| `kriya/tools/containment.py`, `kriya/tools/containment_oci.py`, `kriya/tools/process.py` | The only way a subprocess runs (OCI in production) |
| `kriya/config/config.py` (`AppConfig`, `runtime_profile_preset_fields("production")`), `kriya/config/authority.py` | Config, authority classification, production seal |
| `kriya/config/authority_approval.py`, `kriya/mcp/invocation_approval.py` | Pattern for an operator-authority store kept outside the workspace (waivers follow it) |
| `kriya/production_doctor.py` (`CheckStatus`, `DoctorCheck`, `_report`, schema_version 1) | Doctor integration |
| `kriya/analyzer/analyzer.py` `EXTENSION_MAP` | Existing extension→language table (display names) |
| `kriya/workflow/resume_fingerprints.py`, `kriya/workflow/checkpoint.py` | Resume identity |

`kriya/workflow/language_adapters.py` (PRD-028) is deliberately **not** reused for coverage. It describes what Kriya itself can do per language, not what a scanner can analyze. The two must never be conflated.

## 4. Architecture

### 4.1 Components and layering

```
workflow.py pre-apply boundary ─┐              (direct + milestone units)
TerminalGateService (enforce)  ─┴─> StaticAnalysisService.evaluate(request) -> StaticAnalysisGateResult
                                        │
                                        ├─ ProviderRegistry.create(cfg)     -> StaticAnalysisPort | ProviderUnavailable
                                        ├─ port.probe()                     -> ProviderIdentity + ProviderCapability
                                        ├─ CoverageEvaluator                -> CoverageReport (before any scan)
                                        ├─ EgressAdmission                  -> permitted | EGRESS_NOT_PERMITTED
                                        ├─ port.plan_scope(...)             -> ScopePlan (provider-required scope, §5.4)
                                        ├─ SnapshotBuilder (PRE, POST)      -> Kriya-owned, isolated scope snapshots
                                        ├─ port.scan(ScanRequest) x2        -> ScanResult (normalized findings)
                                        ├─ BaselineDiff                     -> ClassifiedFinding[]
                                        ├─ WaiverRegistry.load() (fresh)    -> Waiver[] (+ rejections)
                                        └─ StaticAnalysisPolicy.decide(...) -> StaticAnalysisOutcome + decisions
commit_terminal_candidate(writes, static_analysis=<StaticAnalysisAuthorization>)   (both paths)
production_doctor: static_analysis.* checks -> same Registry/probe/Coverage/Waiver code
kriya static-analysis {status|scan|waive|revoke|waivers}                (operator CLI)
```

New modules, all under `kriya/static_analysis/`, a new package that sits below `kriya/workflow` in the import order:

| Module | Contents |
|---|---|
| `model.py` | Every provider-neutral type (§6, §7, §9). No provider names. |
| `port.py` | `StaticAnalysisPort` (Protocol), `ScanRequest`, `ScanResult`, `ProviderIdentity`, `ProviderCapability` |
| `registry.py` | `register_provider(name, factory)`, `create_provider(cfg) -> StaticAnalysisPort` |
| `coverage.py` | `LANGUAGE_BY_EXTENSION` (Kriya-owned, provider-neutral language ids), `evaluate_coverage(capability, scope_plan, prerequisites)`, `confirm_coverage(scope_plan, scan_result)` |
| `scope.py` | `ScanScope`, `ScopePlan`, provider-neutral module resolution (nearest build descriptor), `SnapshotBuilder`, `scope_digest()` |
| `baseline.py` | fingerprinting, the `diff_findings(pre, post)` multiset diff, NOT_COMPARABLE |
| `policy.py` | `decide(...)`, pure and deterministic |
| `waivers.py` | store (outside the workspace), `load_waivers()` read fresh every time, `match_waiver()`, `write_waiver()` (CLI only) |
| `service.py` | `StaticAnalysisService`, `StaticAnalysisRequest`, `StaticAnalysisGateResult`, `StaticAnalysisAuthorization` |
| `adapters/semgrep.py` | `SemgrepAdapter`, the **only** module that names Semgrep, its argv, its JSON or its severities |
| `doctor.py` | the check functions `production_doctor` registers |

### 4.2 Layering invariants (structural tests)

1. No module outside `kriya/static_analysis/adapters/` contains the string `semgrep`, case-insensitive. Config and docs are the exceptions: config holds the provider name as data.
2. `kriya/workflow/**` and `kriya/workflow_controller` import only `kriya.static_analysis.service` and `kriya.static_analysis.model`: never `adapters`, never `registry`.
3. `kriya/static_analysis/**` never imports `kriya.workflow.workflow`, `workflow_controller`, `attempt` or `retry_strategy`.
4. No provider branching (`if provider ==`) anywhere outside `registry.py`.
5. `write_waiver` has exactly one production caller: the `kriya static-analysis waive` CLI command (§9.5).
6. The service never writes the real workspace. Its only writes are its own scan roots (temp, outside the workspace and the candidate) and raw evidence under the state dir.

### 4.3 End-to-end flow at a terminal boundary

1. **Inputs.** The boundary hands the service:
   - the exact batch it is about to commit, as `List[StagedFileWrite]` (`materialize_candidate` output). For each path this gives the POST bytes or a delete, plus `expected_base_revision` and `expected_base_exists`;
   - `workspace_path`, `run_id`, a `unit_id` (the WorkUnit, or the enforce milestone id) and the config.

   The batch is the *change set*. It is not the scan scope.
2. **DISABLED.** If `enabled: false`, the service returns DISABLED with no probe and no scan (§7.2).
3. **Provider.** `create_provider` either returns a port or UNAVAILABLE (`PROVIDER_NOT_REGISTERED`).
4. **Probe.** `port.probe()` returns `ProviderIdentity` + `ProviderCapability`. A probe failure → UNAVAILABLE (`PROVIDER_PROBE_FAILED`).
5. **Egress.** Admission checks the provider's declared network and source-upload needs against `autonomy.egress_policy` (§12). If refused → UNAVAILABLE (`EGRESS_NOT_PERMITTED`). This happens **before** any execution.
6. **Scope and coverage.** `port.plan_scope(capability, change_set, workspace, configured_scope)` returns the `ScopePlan` (§5.4). The coverage evaluator then classifies every intended target (§6) and checks prerequisites. PREREQUISITES_MISSING or UNSUPPORTED (and PARTIAL, per policy) are decided **before** scanning. Nothing that was not analyzed is ever reported as analyzed.
7. **Snapshots.** The snapshot builder materializes two Kriya-owned roots under a temp dir, outside both the workspace and the candidate. They are equivalent snapshots of the **whole scope**, not just of the changed files:
   - **PRE** holds every scope file as it is in the real workspace. The real workspace is untouched until commit. For a changed path, `expected_base_revision` is a content hash (`edit_safety.content_revision`: sha256 of the UTF-8 text), not a revision bytes can be read back from. So the real workspace file is read now and accepted only if `content_revision(text) == expected_base_revision`. On the direct path `state.all_original_contents` is that same text and may be used directly. A mismatch gives UNKNOWN (`BASELINE_IDENTITY_MISMATCH`), and the revision-grounded commit would refuse that batch anyway.
   - **POST** is PRE with the batch laid over it: every write replaced and every delete removed. Unchanged scope files are byte-identical in the two roots.
   - Both roots use the same relative paths, and both hold only files Kriya put there. Repository and candidate scanner-control files are never copied (§8.4).
   - The builder records `base_scope_digest` over PRE and `post_scope_digest` over POST (§10.4).
8. **Scans.** The two scans run through the port, POST first. Both must succeed under one `scan_identity` (§8.3), or the result is UNKNOWN or NOT_COMPARABLE.

   Coverage is then **confirmed** against what the scanner reports it actually analyzed (§6).
9. **Diff.** The PRE/POST diff classifies every finding in the scope as introduced, unchanged, worsened or resolved (§8.2), including findings in files the candidate did not change.
10. **Waivers.** The waiver store is read fresh from disk, and each waiver is matched against the findings (§9).
11. **Policy.** The policy turns the findings, coverage and waivers into one `StaticAnalysisOutcome`, a list of per-finding decisions, and `permits_commit` (§7).
12. **Result.** The service returns `StaticAnalysisGateResult`, which carries the evidence (§11) and a `StaticAnalysisAuthorization` bound to the full evidence identity (§10.4).

**Why both snapshots are built at the terminal boundary.**

PRD-024 captures its full-suite baseline before the first mutation. It has to, because the real workspace is where tests run. Here the real workspace is never mutated before commit: the candidate lives in its sandbox. So the scope as it is in the real workspace at the terminal boundary *is* the pristine base. For every changed path this is proven by `content_revision` (`BASELINE_IDENTITY_MISMATCH` otherwise). For every other scope file it is bound by `base_scope_digest`, which the commit re-checks (§10.4).

Building both snapshots and running both scans in one step gives three things:
- the same scanner binary, rule packs and flags, by construction. That removes PRD-024's main NOT_COMPARABLE source, environment drift between PRE and POST;
- no cost when the run fails before commit;
- an exact scope, because the scope depends on the change set, which is known only at the terminal boundary.

Scanning more than the changed files is not assumed away. The provider declares the minimum trustworthy scope, and findings in unchanged files are diffed like any other (§5.4, §8.2).

## 5. Port and adapter contract

```python
class StaticAnalysisPort(Protocol):
    name: str                                    # registry key, e.g. "semgrep"
    def probe(self) -> ProviderProbe: ...        # identity + capability; never scans; bounded time
    def plan_scope(self, capability: ProviderCapability, change_set: ChangeSet, workspace: str,
                   configured: ScanScopeSetting) -> ScopePlan: ...   # §5.4; never narrower than capability.minimum_scope
    def check_prerequisites(self, scope: ScopePlan, workspace: str) -> Sequence[PrerequisiteResult]: ...
    def scan(self, request: ScanRequest) -> ScanResult: ...   # never raises for a scanner failure (§5.3)

@dataclass(frozen=True)
class ProviderIdentity:
    provider: str                 # "semgrep"
    version: str                  # exact tool version as reported
    edition: str                  # e.g. "community" (engine actually used, not licensed)
    executable_digest: str        # sha256 of the resolved executable, or of the pinned image digest under OCI
    execution_location: str       # "local_process" | "container" | "remote_service"
    rule_packs: Tuple[RulePackIdentity, ...]   # ref, sha256 over canonical rule files, rule_count, languages
    effective_options_digest: str # sha256 over the exact adapter-owned flag set (argv minus target paths)
    identity_digest: str          # sha256 over all of the above (the scan_identity)

@dataclass(frozen=True)
class ProviderCapability:
    languages: Mapping[str, LanguageSupport]   # Kriya language id -> maturity ("ga"|"beta"|"experimental"),
                                               #   language_versions (tuple or None = unknown), rules_available (int)
    analysis_scope: str           # "file_local" | "cross_file"
    supported_scopes: FrozenSet[ScanScope]   # CHANGED_FILES | MODULE | REPOSITORY | BUILD_GRAPH
    minimum_scope: ScanScope      # the narrowest scope for which this provider's evidence is trustworthy
    control_files: Tuple[str, ...]           # scanner-control file names never copied into a snapshot (e.g. ignore files)
    network_requirement: str      # "none" | "rule_download" | "service"
    source_upload: bool
    prerequisites: Mapping[str, Tuple[str, ...]]   # language -> prerequisite ids (e.g. "compiled_classes")
```

### 5.1 Scan request and result

- `ScanRequest(root, scope_plan, timeout_seconds, per_file_timeout_seconds, max_target_bytes)`:
  - `root` is a Kriya-owned snapshot root;
  - the intended targets are `scope_plan.targets`, which already exclude the trusted exclusions (§8.4);
  - every target passed must exist in that snapshot. A deleted path is a target of the PRE scan only, because one missing target aborts the whole Semgrep run (§5.5).
- `ScanResult` holds:
  - `status`: `COMPLETE`, `INCOMPLETE`, `FAILED`, `TIMEOUT` or `MALFORMED_OUTPUT`;
  - `findings: Tuple[Finding, ...]`;
  - `analyzed: FrozenSet[path]`;
  - `skipped: Mapping[path, reason]`;
  - `errors: Tuple[ScanError, ...]`;
  - `raw_ref`: the location and sha256 of the raw provider output;
  - `duration_ms`.

  `analyzed` is derived from what the provider reports it scanned, never assumed from the targets passed in.

### 5.4 Scan scope (provider-required, not changed-files-only)

`ScanScope` is a closed, provider-neutral enum:

| Scope | Meaning |
|---|---|
| `CHANGED_FILES` | the change set only; trustworthy only for a `file_local` provider, where a finding depends on the bytes of one file |
| `MODULE` | every source file in each build module that contains a changed path. The module is resolved provider-neutrally from the nearest build descriptor, reusing the markers `PolymorphicValidator` already detects (pom.xml, build.gradle, package.json, pyproject.toml, ...). No descriptor → the repository. |
| `REPOSITORY` | every source file in the workspace, excluding `.kriya/`, VCS metadata and trusted exclusions |
| `BUILD_GRAPH` | the changed modules plus the modules they depend on, as the provider resolves them. It is for providers that analyze compiled or linked artifacts, and it carries prerequisites. |

- **Minimum scope.** The provider declares `minimum_scope`. Config `scope: auto` (the default) uses it. An explicit `scope` may be broader, never narrower: narrower → `StaticAnalysisConfigError` at load when the adapter's minimum is static, or UNAVAILABLE `SCOPE_BELOW_PROVIDER_MINIMUM` at probe when it depends on the version.
- **The plan.** `ScopePlan(kind, targets, change_set, module_roots, trusted_exclusions, plan_digest)`. `targets` is the full intended target list: POST paths, plus PRE-only paths for deletions. It is built by Kriya's `scope.py` for the first three kinds, and by the provider for `BUILD_GRAPH`, which Kriya then validates stays inside the workspace.
- **Findings anywhere in the scope count.** A candidate can cause a finding in a file it did not change: a cross-file taint path, or a changed callee signature. The diff (§8.2) therefore runs over every finding in the scope, keyed by fingerprint. A finding that appears in an unchanged file is `introduced`, and one that disappears there is `resolved`.
- **Semgrep CE.** It declares `analysis_scope: file_local`, `minimum_scope: CHANGED_FILES` and `supported_scopes: {CHANGED_FILES, MODULE, REPOSITORY}`. That minimum is justified because the CE engine (`--oss-only`) analyzes within one file: the Pro engine is needed for inter-file analysis, per Semgrep's CLI reference. An operator may still configure a broader scope. The fake adapter declares `cross_file` with `minimum_scope: MODULE`, so the unchanged-file path is exercised in the deterministic tier.

### 5.2 Semgrep adapter (first production adapter)

- **Engine.**
  - It runs `semgrep scan` with `--oss-only`, so the edition is `community` and `analysis_scope` is `file_local`.
  - It never runs `semgrep ci`, `semgrep login` or `--pro`.
  - Cross-file analysis needs the Pro engine (per Semgrep's CLI reference), so the adapter does not claim it.
- **Flags, fixed and adapter-owned, verified against docs.semgrep.dev/cli-reference on 2026-09-27:**
  - `--metrics=off`: metrics are otherwise sent under `auto` when rules come from the registry or the user is logged in.
  - `--disable-version-check`, which removes a network call.
  - `--disable-nosem`: inline `nosemgrep` comments do not suppress a finding (§8.4).
  - `--no-git-ignore`.
  - `--json`: structured output; SARIF is not needed, and the JSON carries `paths.scanned`, `paths.skipped` and `errors`, which coverage needs.
  - `--verbose`: without it, 1.178.0 omits `paths.skipped` entirely (§5.5). It adds only stderr noise, and the adapter reads stdout JSON only.
  - `--timeout <per_file>`, `--timeout-threshold <n>` and `--max-target-bytes <n>`: files skipped under these limits are recorded as uncovered, never as clean.
  - `--config <pack>` repeated, each pack a **local** file or directory only.
  - `--scan-unknown-extensions` is **not** passed: detection stays with the provider, and Kriya's own coverage evaluator decides what counts.
  - **Environment.** The adapter sets `SEMGREP_SEND_METRICS=off` and `SEMGREP_ENABLE_VERSION_CHECK=0` (both env vars appear in the documented list), plus a scratch `HOME` inside the scan temp dir.
  - The rest of the environment is the SEC-001 `build_restricted_env()` set. No other `SEMGREP_*` variable is ever passed; the documented `SEMGREP_RULES` and `SEMGREP_BASELINE_COMMIT` would otherwise inject rules or change the baseline. A test asserts the exact environment.
  - Verified (§5.5 V16): Semgrep writes `$HOME/.semgrep/{settings.yml,semgrep.log}`, and a scratch `HOME` redirects both.
  - The flag set is part of `effective_options_digest`. `--help` is broken in 1.178.0 (§5.5 V1), so the real tier verifies every flag by use. A flag that is missing or renamed in the pinned version is a probe failure, never silently dropped.
- **Rejected at config validation (typed `StaticAnalysisConfigError`), never at scan time:**
  - rule-pack refs that are registry names (`p/...`, `r/...`, `auto`, `s/...`);
  - URLs;
  - any pack path that does not exist.
- **Version.**
  - `semgrep --version` is recorded exactly.
  - An operator configures an exact version (`providers.semgrep.version: "<x.y.z>"`), and a probe reporting a different version → UNAVAILABLE (`PROVIDER_VERSION_MISMATCH`).
  - `latest` and version ranges are rejected at config load, for the image tag as well. An image is referenced by digest.
  - The image digest is used under OCI.
- **Languages.**
  - The capability comes from a version-pinned, adapter-owned table. Its key is the semgrep version range; its value is `language → maturity`, taken from docs.semgrep.dev/supported-languages and re-checked at implementation.
  - It is intersected with the languages the configured rule packs actually contain rules for, parsed from the packs' `languages:` keys. A language with zero rules is `rules_available: 0` and counts as **not covered**.
  - An unknown semgrep version (outside the table) makes the capability UNKNOWN, which gives UNAVAILABLE (`CAPABILITY_UNKNOWN`) rather than guessing.
- **Severity map (adapter-owned, versioned as `SEVERITY_MAP_VERSION`, part of `identity_digest`).**
  - A security rule whose `metadata.impact` is `HIGH`/`MEDIUM`/`LOW` maps that value to high/medium/low.
  - A rule whose `metadata.security-severity` is `Critical` maps to critical.
  - Otherwise `ERROR`→high, `WARNING`→medium, `INFO`→low.
  - An unknown severity token → `unknown`, which policy treats as `high` (fail safe).
  - CWE and OWASP come from `metadata.cwe`/`metadata.owasp` when present.
- **Prerequisites.** Semgrep CE needs no build, so it declares no language prerequisites. A later adapter that does (CodeQL, SpotBugs: compiled classes, a build database) declares them here, and the evaluator enforces them. The contract is exercised in v1 by the fake adapter.

### 5.3 Process result, findings and errors (never PASS)

The adapter keeps three things separate. Process status never stands in for either of the other two.

1. **Findings.** Whenever stdout is valid JSON matching the pinned schema, the adapter parses and normalizes every entry in `results`, **whatever the exit code**. Findings are evidence, even from a run that then counts as failed.
2. **Scanner and config errors.** Each `errors[]` entry is classified by its kind, not by the exit code. The kind wins over the exit code. `type` is either a string or a `[kind, spans]` list (1.178.0 `PartialParsing`), and both shapes are parsed:
   - a rule or config error (invalid pattern, invalid YAML, invalid rule, unknown language in a rule) → `RULE_PACK_INVALID`, which makes the result UNAVAILABLE;
   - a target-level error (a parse error, including `PartialParsing` at level `warn`, or a timeout on a file) → that target is `not_analyzed`. A partially parsed file still produces findings in 1.178.0, and those findings are kept, but the file is **not** confirmed analyzed;
   - anything unrecognized → `SCAN_FAILED`, which makes the result UNKNOWN.
3. **Process status.** It decides only whether the run completed:

| Observation | `ScanResult.status` | Gate effect |
|---|---|---|
| exit 0, valid JSON, no rule/config error, and every intended target is confirmed analyzed (§6) | COMPLETE | findings decided by policy |
| exit 0, valid JSON, but at least one intended target is not confirmed (skipped, errored, or simply absent from `paths.scanned`) | INCOMPLETE | §6: those targets are not analyzed |
| exit 0 **and** a rule/config error in `errors[]` | FAILED (config) | UNAVAILABLE `RULE_PACK_INVALID`; findings kept as evidence only |
| exit 4, 5 or 7 (documented rule/config failures; 1.178.0 actually exits **7** for invalid YAML and for an invalid rule schema, carrying codes 4/5 only inside `errors[]`) | FAILED (config) | UNAVAILABLE `RULE_PACK_INVALID` |
| exit 2 with a rule error in `errors[]` (1.178.0: an invalid **pattern** exits 2 with `type: "Rule parse error"`, and still returns the valid rules' findings) | FAILED (config) | UNAVAILABLE `RULE_PACK_INVALID`; the partial findings are evidence only |
| exit 8 (language not understood) | FAILED (config) | UNAVAILABLE `CAPABILITY_UNKNOWN` |
| exit 13 (invalid API key; impossible without login) | FAILED (config) | UNAVAILABLE `PROVIDER_PROBE_FAILED` |
| exit 2, 14, 1 (never expected, since `--error` is not passed), any undocumented code, crash or signal | FAILED | UNKNOWN `SCAN_FAILED`; any parsed findings kept as evidence only |
| whole-scan timeout | TIMEOUT | UNKNOWN `SCAN_TIMEOUT` |
| stdout not valid JSON, schema mismatch, or a missing `results`/`paths`/`errors` key | MALFORMED_OUTPUT | UNKNOWN `SCAN_OUTPUT_MALFORMED` |
| executable or image missing, or a version mismatch | probe failure | UNAVAILABLE |

**Exit 0 is not "clean".** Semgrep exits 0 whether or not it found anything, because `--error` is not passed (docs.semgrep.dev/cli-reference, 2026-09-27). Clean means exactly this: a COMPLETE scan whose normalized findings, after diff and policy, leave nothing to warn or block on.

There is no path from an empty or missing output to "zero findings".

### 5.5 Verified behaviour of the pinned scanner (Semgrep 1.178.0, 2026-09-27)

**Pinned version: Semgrep 1.178.0**, installed by the user via pipx (`~/.local/pipx/venvs/semgrep`). This is the exact version of the real tier. For OCI (test 29) the user pulls `semgrep/semgrep:1.178.0` and records its digest; no such image is present yet.

Everything below was observed in a scratch fixture with local rules, `--metrics=off` and a scratch `HOME`. Nothing was fetched from the registry. The real tier re-asserts each item.

| # | Observation | Consequence for the design |
|---|---|---|
| V1 | `semgrep scan --help` crashes (`cmdliner error: Illegal escape char`), so the help text is truncated | Flags cannot be verified from `--help`. The real tier verifies them by use (an unknown flag makes Semgrep reject the invocation) |
| V2 | Every flag in §5.2 is accepted; exit 0 | — |
| V3 | 5 findings, exit 0 | **Exit 0 is not clean** (§5.3), confirmed |
| V4 | `nosemgrep` suppresses the finding without `--disable-nosem`, and is reported with it | `--disable-nosem` is required, confirmed |
| V5 | A **directory** scan with no `.semgrepignore` silently drops `tests/`, `build/` and `src/test/` (the default ignores). `paths.skipped` is absent, even for those files | Silent omission is real. Coverage must be intended vs `scanned` (§6); targets are always passed explicitly |
| V6 | **Explicitly passed** targets under `tests/`, `build/`, `src/test/` are scanned, and a project `.semgrepignore` naming them does not skip them | Explicit targets bypass ignore files. Still not relied on: confirmation decides |
| V7 | A project `.semgrepignore`, even empty, replaces the defaults for directory scans | Recorded only. Not relied on (user directive), because the adapter never scans directories |
| V8 | `--exclude` does **not** apply to explicitly passed targets | Trusted exclusions are applied by Kriya's scope planner (§8.4), not by `--exclude` |
| V9 | `paths.skipped` is omitted unless `--verbose` is passed | `--verbose` added to the fixed flags (§5.2) |
| V10 | A partially parsed Java file: findings are still reported; the file is in `scanned` **and** in `skipped` (`analysis_failed_parser_or_internal_error`); `errors[]` has `type: ["PartialParsing", spans]`, level `warn` | Confirmed = scanned − skipped − errored (§6). The list-shaped `type` is parsed (§5.3) |
| V11 | A Python file with a syntax error was parsed by error recovery: no error, and findings reported | Semgrep's own report is the authority. Kriya does not second-guess parse quality it cannot observe. Disclosed |
| V12 | A 2 MB explicit target with `--max-target-bytes 1000000` appears in `scanned` | The size limit is not observed to skip explicit targets. Confirmation still decides whatever the limit does |
| V13 | A file with an unknown extension (`.xyz`) is silently absent from `scanned` and `skipped` | It is unclassified in Kriya. A classified source file that is silently absent is `not_analyzed` |
| V14 | An invalid pattern exits **2** (`errors[].type` `"Rule parse error"`), and the valid rules' findings are still returned. Invalid YAML exits **7** (`errors[]` codes 5 and 7). Invalid rule schema exits **7** (`InvalidRuleSchemaError`). Unknown language exits **8** | The docs' exit numbers differ. Classification by `errors[]` kind first (§5.3) |
| V15 | One nonexistent target aborts the whole scan: exit 2, empty `scanned` | Every target must exist in its snapshot (§5.1). This is UNKNOWN if it happens |
| V16 | With a scratch `HOME`, Semgrep writes `$HOME/.semgrep/settings.yml` (containing `anonymous_user_id`) and `semgrep.log` there; stderr: "Not sending pseudonymous metrics since metrics are configured to OFF" | The scratch `HOME` redirects Semgrep's state (the §5.2 to-verify item is resolved), and metrics are confirmed off |

## 6. Capability and coverage contract

- **Intended targets.** These are `ScopePlan.targets` (§5.4). For `CHANGED_FILES` that is the batch's paths; for broader scopes it is every classified source file in the scope. A deleted path is absent in POST, so it is scanned PRE only (for "resolved").
- **Scanner-confirmed targets.** These are the paths the provider's structured output reports as analyzed (Semgrep: `paths.scanned`), **minus** any path in `paths.skipped` and any path with a target-level error. In 1.178.0 a partially parsed file appears in `scanned` **and** in `skipped` (`analysis_failed_parser_or_internal_error`), so `scanned` alone overstates coverage (§5.5). Every intended target of a supported language that is not confirmed is `not_analyzed`, whatever the reason: an explicit skip, a scanner default exclusion, size or timeout limits, a parse error, or silent omission. The adapter never infers "analyzed" from "passed as a target".
- **Language of a target.** It comes from `coverage.LANGUAGE_BY_EXTENSION`, a Kriya-owned, provider-neutral table with language ids such as `java`, `python`, `javascript`, `typescript`, `go`, `ruby`, `kotlin`, `c`, `cpp`, `csharp`, `rust`, `php`, `swift` and `scala`. It is derived from, but separate from, `analyzer.EXTENSION_MAP`, whose values are display names.
- **Extensions the table does not list** are `unclassified`. They are reported in the evidence, but they do not count toward coverage: text, data, docs and build files are not claimed as analyzed or unanalyzed source.

Per-target coverage status:

| Status | Meaning |
|---|---|
| `covered` | language supported at an allowed maturity (`providers.<p>.min_language_maturity`, default `ga`), `rules_available > 0`, prerequisites met, **and** the provider reported the file analyzed |
| `unsupported_language` | the provider does not support the language, or only below the maturity bar |
| `no_rules` | the language is supported, but no configured rule targets it |
| `prerequisite_missing` | the language is supported, but a declared prerequisite is not met |
| `not_analyzed` | supported, with rules and prerequisites met, but the scanner did not confirm analyzing it (INCOMPLETE) |

Aggregate `CoverageReport.status`:

| Status | Condition |
|---|---|
| `FULL` | every classified target is covered |
| `PARTIAL` | at least one is covered and at least one is not |
| `UNSUPPORTED` | none is covered because of language or rules |
| `PREREQUISITES_MISSING` | at least one is uncovered **only** because of a prerequisite |
| `PROVIDER_UNAVAILABLE` | no probe |
| `NOT_APPLICABLE` | the batch has no classified source targets, e.g. a docs-only candidate |

The report names every uncovered file with its reason, and gives a count per language.

**Pre-scan gating.** Coverage is computed from the capability before scanning, then confirmed after scanning, which adds `not_analyzed`. Policy is applied to the confirmed report.

**Normalized consequence of `not_analyzed`.** A required file that was not actually analyzed can never yield PASS.
- Under `policy.analysis_errors: block` it gives UNKNOWN (`SCAN_INCOMPLETE`). That is the default, and it is sealed in production when enabled.
- Under `warn` it gives coverage PARTIAL (`COVERAGE_PARTIAL`), which `policy.partial_coverage` then decides.

Either way, the file appears in `coverage.uncovered` with its reason.

**The honesty invariant.** No outcome, result field, CLI line or doctor row may say "analyzed", "clean" or PASS for a file whose status is not `covered`. A test asserts this for every row of the matrix in §16.

## 7. Outcomes, truth table and policy

### 7.1 Outcome vocabulary

`StaticAnalysisOutcome` (a closed enum with a tripwire test):
- `PASS`: coverage FULL (or NOT_APPLICABLE with nothing to analyze), and no finding the policy warns or blocks on.
- `PASS_WITH_WARNINGS`: nothing blocks. At least one finding or coverage gap is `warn`, and no waiver was needed.
- `ACCEPTED_RISK`: at least one finding would **block** and was released by a valid waiver. Nothing else blocks. **This is never PASS.**
- `BLOCKED`: at least one finding or coverage condition blocks, with no valid waiver.
- `UNKNOWN`: the scanner ran but the evidence is incomplete or untrustworthy: TIMEOUT, FAILED, MALFORMED_OUTPUT, NOT_COMPARABLE, a base-identity mismatch, or an INCOMPLETE scan under `analysis_errors: block`. **When analysis is enabled, UNKNOWN always blocks** (approved decision 4).
- `UNAVAILABLE`: the scanner could not run: provider not registered, probe failed, unknown capability, egress refused, or prerequisites missing where policy blocks.
- `DISABLED`: `enabled: false`. The evidence records whether that is the packaged default or operator-set, from provenance (`configured_by`).

Each outcome carries `reason_codes`, a closed table with a tripwire test, for example:
- `NEW_FINDING_BLOCKED`
- `WORSENED_FINDING_BLOCKED`
- `EXISTING_FINDING_BLOCKED`
- `COVERAGE_PARTIAL`
- `COVERAGE_UNSUPPORTED`
- `PREREQUISITES_MISSING`
- `PROVIDER_NOT_REGISTERED`
- `PROVIDER_PROBE_FAILED`
- `CAPABILITY_UNKNOWN`
- `EGRESS_NOT_PERMITTED`
- `SCAN_TIMEOUT`
- `SCAN_FAILED`
- `SCAN_OUTPUT_MALFORMED`
- `SCAN_INCOMPLETE`
- `BASELINE_NOT_COMPARABLE`
- `WAIVER_APPLIED`
- `WAIVER_EXPIRED`
- `WAIVER_SCOPE_MISMATCH`
- `WAIVER_STORE_INVALID`
- `STATIC_ANALYSIS_DISABLED`
- `STATIC_ANALYSIS_EVIDENCE_STALE`
- `RULE_PACK_INVALID`
- `RULE_PACK_DIGEST_MISMATCH`
- `BASELINE_IDENTITY_MISMATCH`
- `SCOPE_BELOW_PROVIDER_MINIMUM`
- `PROVIDER_VERSION_MISMATCH`
- `STATIC_ANALYSIS_EVIDENCE_MISSING`

### 7.2 Truth table (requirement × outcome)

| Outcome | `requirement: optional` → `permits_commit` | `requirement: required` → `permits_commit` | gate event `status` | result `static_analysis.outcome` | top-level `accepted_risk` |
|---|---|---|---|---|---|
| PASS | yes | yes | `passed` | PASS | false |
| PASS_WITH_WARNINGS | yes | yes | `passed_with_warnings` | PASS_WITH_WARNINGS | false |
| ACCEPTED_RISK | yes | yes | `accepted_risk` | ACCEPTED_RISK | **true** |
| BLOCKED | **no** | **no** | `failed` | BLOCKED | false |
| UNKNOWN | **no** | **no** | `unknown` | UNKNOWN | false |
| UNAVAILABLE | per `policy.when_unavailable` (default `warn`); never PASS; not reachable under the production profile (see note) | **no** | `unavailable` | UNAVAILABLE | false |
| DISABLED | yes | not reachable: `required` + `enabled: false` is rejected at config load | `disabled` | DISABLED | false |

- **BLOCKED blocks under either requirement.** `optional` means "a scanner that cannot run does not stop the run". It does not mean "blocking findings are advisory". Softening findings belongs in the severity policy (`warn`/`allow`), where it is explicit per class.
- **UNKNOWN always blocks when analysis is enabled** (approved decision 4). A scan that ran and produced untrustworthy evidence is not the same as "no scanner". The earlier `when_unknown` setting is removed.
- **UNAVAILABLE under `optional`.** Outside production, `policy.when_unavailable` decides it, and the result still says UNAVAILABLE, never PASS.
- **Production** (approved decision 2): the profile does not force `enabled: true`. When analysis *is* enabled, a sealed-profile validator requires `requirement: required` and `policy.analysis_errors: block`. Trustworthy evidence is therefore mandatory, and UNAVAILABLE and UNKNOWN both block.
- **DISABLED** never emits `passed`. The disabled gate is still emitted as a `terminal_gate_outcome` with status `disabled`, so the sequence of gate events is identical whether the gate is on or off.

### 7.3 Policy (Kriya-owned, `kriya/static_analysis/policy.py`, pure)

The policy's inputs are the classified findings, the `CoverageReport`, the scan status, the waiver matches, and the `StaticAnalysisPolicyConfig`. Its output is the outcome, the reason codes, a per-finding `decision ∈ {allow, warn, block}`, and a `basis ∈ {policy, waiver}` for each decision.

- **Findings.** Each finding is decided by `policy.<classification>.<severity>`, where classification is `introduced`, `worsened` or `existing` (`unchanged`), and severity is critical, high, medium, low, info or unknown (unknown is decided as high). `resolved` is always `allow`: it is reported, and it is credit.
- **Coverage.**
  - PARTIAL → `policy.partial_coverage`;
  - UNSUPPORTED → `policy.unsupported_language`;
  - PREREQUISITES_MISSING → `policy.prerequisites_missing`;
  - INCOMPLETE → `policy.analysis_errors`.

  Each value is `warn` or `block`.
- **Adapters decide nothing.** They never see the policy. A structural test asserts that `adapters/` does not import `policy`.

## 8. Findings, fingerprints and PRE/POST

### 8.1 Normalized finding

| Field | Type / notes |
|---|---|
| `provider` | registry name |
| `rule_id` | `<provider>:<provider rule id>`, namespaced so two providers never collide |
| `severity` | critical, high, medium, low, info or unknown (normalized by the adapter's versioned map) |
| `category` | optional (security, correctness, performance, maintainability, other) |
| `cwe`, `owasp` | tuples, possibly empty |
| `path` | workspace-relative, POSIX |
| `range` | `start_line`, `start_col`, `end_line`, `end_col` |
| `message` | provider text. **Untrusted** (§12.4). Length-bounded (2 KB). |
| `fingerprint` | §8.2 |
| `raw_ref` | `{scan_id, index}` into the stored raw output |
| `pre_state` / `post_state` | `present` or `absent` |
| `classification` | introduced, unchanged, worsened or resolved |

### 8.2 Fingerprint and diff

**Fingerprint.**
- `location_key = sha256(rule_id, path, normalized_snippet)`, where `normalized_snippet` is the matched source text with its whitespace runs collapsed and its line numbers dropped. The fingerprint therefore survives line shifts and re-indentation.
- The snippet is taken from the scan root file at the reported range, not from the provider's `lines` field.
- `fingerprint = sha256(location_key, occurrence_index)`. The occurrence index is the rank among findings with the same `location_key` in one scan, ordered by position.

**Diff.** For each `location_key`, let m be the PRE count and n the POST count:
- m = n > 0 → all n are `unchanged`;
- m = 0, n > 0 → all n are `introduced`;
- m > 0, n = 0 → the m PRE findings are `resolved`;
- n > m > 0 → m are `unchanged` and the extra n − m are **`worsened`**: the same defect in the same place, multiplied;
- m > n > 0 → n are `unchanged` and m − n are `resolved`.

A finding whose `location_key` is unchanged but whose severity is higher in POST is also `worsened`. With one scan identity that can only happen through severity metadata that depends on the target, so it is rare, but it is defined.

The diff covers every file in the scope, not only changed ones (§5.4). Paths are snapshot-relative, which equals workspace-relative, so an unchanged file keeps its keys across PRE and POST, and a new finding in it is `introduced`.

A renamed or moved file is `introduced` at its new path and `resolved` at its old one. That is conservative, and it is disclosed. Rename tracking is a non-goal (§19).

### 8.3 Comparability

PRE and POST are comparable only when their `ProviderIdentity.identity_digest` values are equal. That digest covers the version, the executable or image digest, every rule-pack digest, the effective options and the severity-map version. The two scans also share one `ScopePlan` (same `plan_digest`). Because both scans run in one service call, drift is only possible if the adapter misbehaves. A mismatch is still handled explicitly, mirroring PRD-024's `classify_baseline_delta(post_environment=)`:
- the result is `BASELINE_NOT_COMPARABLE`;
- every POST finding is treated as `introduced`, so no failure is excused;
- the outcome is UNKNOWN unless the POST scan alone is clean and complete, in which case PASS stands, exactly as a green suite passes in PRD-024.

### 8.4 Isolation and suppression bypasses (the likeliest real hole)

A candidate is written by a model. Anything in the candidate or the repository that can switch the scanner off is an LLM-created or repository-created waiver, so the design closes each route:

1. **Inline suppression** (`nosemgrep`, or any provider equivalent): the adapter disables it (`--disable-nosem`). A test writes a `nosemgrep` comment on a known finding in the candidate and asserts that the finding is still reported and still blocks.
2. **Ignore files and scanner defaults.**
   - The snapshots contain only scope files. The provider's `control_files` are never copied: for Semgrep that means repository and candidate `.semgrepignore`, and `.gitignore` is irrelevant because there is no `.git` and `--no-git-ignore` is passed. A repository or candidate ignore file therefore cannot remove a target.
   - Trusted exclusions come only from operator config. They are applied by **Kriya's scope planner**: an excluded path is never an intended target and is never passed to the scanner. They are recorded in the evidence and included in the scope-plan digest, so they are legitimate and auditable.

    The scanner's own `--exclude` is not relied on. In 1.178.0 it does not apply to explicitly passed targets (§5.5).
   - **No assumption is made about scanner defaults.** Semgrep documents a built-in default ignore list (including `test/`, `tests/`, `build/`, `vendor/`, `node_modules/`). The spec does **not** assume that any `.semgrepignore`, empty or not, disables it, and it relies on no internal flag (`--x-ignore-semgrepignore-files` is documented `[INTERNAL]` and is not used).
   - Correctness comes from confirmation instead (§6). Every intended target that the pinned scanner does not report as analyzed is `not_analyzed`, and a required file that was not analyzed is PARTIAL or UNKNOWN, never PASS.
   - The real tier records which intended targets the pinned version actually analyzes, including files under `src/test/`. If defaults do exclude them, the result is honest partial coverage, visible to the operator, until trusted configuration addresses it.
3. **Rule selection**: rule packs come only from trusted config (§13), are digest-bound, and are never read from the candidate root. A pack inside the workspace is allowed only with an explicit `sha256` pin in config. It is read from the real workspace (the candidate lives in its sandbox, and the real workspace is untouched until commit), never from the candidate root, and verified against the pin (mismatch → UNAVAILABLE `RULE_PACK_DIGEST_MISMATCH`), so a candidate that edits the rules cannot change what judges it.
4. **Provider config files in the repository** (for example a repository-level scanner settings file): these are never in the scan roots, and the adapter's settings location is a scratch dir.
5. **Target and scope selection**: the scope comes from the provider's declared minimum and the operator's config, and the targets come from Kriya's `scope.py` (or the provider, for `BUILD_GRAPH`, validated by Kriya). Neither the candidate nor the model chooses or narrows them.
6. **Environment**: the adapter's environment is fixed (§5.2). No inherited `SEMGREP_*` variable, repository file or candidate file can add rules, change the baseline, or re-enable inline suppression.

### 8.5 Scan execution

The scan runs through the existing process layer (`kriya/tools/process.py` plus the `ContainmentBackend`), never through a bare `subprocess`.

- **Containment required** (`autonomy.contained_execution_required: true`, sealed by the production profile). The scan runs in OCI with `NetworkAuthority.DENIED`. The scan roots are mounted read-only. The rule packs are mounted read-only at fixed paths. The image is `providers.semgrep.image`, which must be pinned by digest (`@sha256:`). A tag alone, including `latest`, is rejected at config load. There is **no host fallback**: a missing or unpinned image, or no backend, gives UNAVAILABLE.
- **Containment not required.** The scan runs as a host process with the restricted environment. The network is not enforced at the OS level. The honest statement is that network discipline in this mode comes from the fixed flag set (§5.2), not from containment. Evidence records `execution_location: local_process` and `network_enforced: false`.

## 9. Waiver / risk-acceptance model

### 9.1 Waiver record

```json
{
  "schema_version": 1,
  "waiver_id": "SAW-2026-0001",
  "disposition": "accepted_risk",
  "provider": "semgrep",
  "rule_id": "semgrep:java.lang.security.audit.xxe.documentbuilderfactory-disallow-doctype-decl-missing",
  "scope": {
    "paths": ["src/main/java/com/acme/legacy/XmlImport.java"],
    "fingerprint": null,
    "package": null,
    "version_range": null,
    "classifications": ["existing"]
  },
  "max_severity": "high",
  "rule_pack_digest": null,
  "reason": "Legacy importer; input is operator-supplied and signed; replacement tracked.",
  "owner": "platform-security",
  "tracking_ref": "SEC-1234",
  "expires_at": "2026-12-31T00:00:00Z",
  "provenance": {
    "created_at": "2026-09-27T10:00:00Z",
    "created_by": "<os user>",
    "created_via": "cli:kriya static-analysis waive",
    "workspace_id": "<workspace id>",
    "kriya_version": "<version>"
  },
  "record_digest": "<sha256 over all fields above>"
}
```

- `scope.paths`: exact paths or glob patterns; at least one is required.
- `scope.fingerprint`: optional. When set, only that exact finding matches.
- `scope.package` and `scope.version_range`: reserved for dependency (SCA) providers. They are validated as a unit, and they are not applied by v1's SAST adapter.
- `scope.classifications` defaults to `["existing"]`. Waiving an `introduced` finding must be explicit.
- `max_severity`: a finding more severe than this does not match.
- `rule_pack_digest`: optional. When set, the waiver applies only under that exact rule pack.
- `tracking_ref` and `expires_at` are optional.

### 9.2 Matching

A waiver applies to a finding only if **all** of the following hold:
- the `provider` and `rule_id` are equal (exact; no rule-id wildcards in v1);
- the path matches the scope;
- the fingerprint matches, when one is set;
- the classification is in scope;
- the finding's severity is at most `max_severity`;
- the rule-pack digest matches, when one is set;
- the waiver has not expired at evaluation time (UTC);
- the `workspace_id` is equal;
- `record_digest` verifies.

Anything else is a non-match. A waiver that nearly matches, i.e. same rule and path but another condition fails, is reported in `waiver_rejections` with its reason (`WAIVER_EXPIRED`, `WAIVER_SCOPE_MISMATCH`, `WAIVER_SEVERITY_EXCEEDED`, `WAIVER_RULE_PACK_MISMATCH`, `WAIVER_DIGEST_INVALID`). The finding then stays at its policy decision. Nothing applies silently and nothing fails silently.

Waivers release findings only. A coverage gap (partial or unsupported, missing prerequisites), UNKNOWN or UNAVAILABLE is never waivable: the policy decides those, and only the operator config can change that policy.

### 9.3 Store

- **Location.** `~/.kriya/static_analysis/waivers/<workspace_id>.json`, overridable with `KRIYA_STATIC_ANALYSIS_HOME`, or with an explicit `static_analysis.waivers.store` path. The explicit path is SECURITY_AUTHORITY and must lie **outside the workspace** (`validate_trust_path_outside_workspace()` is reused). A repository therefore cannot ship its own risk acceptance. This is the same reasoning as SEC-009 P2 and TOOL-002 P2.
- **Reads.** The store is read fresh on every evaluation, with no cache, so a revoke takes effect on the next gate.
- **Writes.** Writes are atomic: a temp file followed by `os.replace`.
- **Corruption.** A corrupt store, an unknown `schema_version`, or a record digest that fails → `WAIVER_STORE_INVALID`. No waiver from that store applies, and the gate continues with policy-only decisions. It fails closed for acceptance, but it does not crash the run.

### 9.4 Authority

- **No model path.** Only an operator creates or revokes a waiver, through the CLI. No agent, tool, MCP tool, plan field, Planner, Developer or Reviewer output, scanner message or repository file reaches `write_waiver`.
- **No influence on existing waivers.** LLM output may *describe* or *remediate* a finding, but nothing it produces is an input to `match_waiver`, which reads only the store and the normalized finding.
- **Test obligations (§16):**
  - a structural single-caller test;
  - an adversarial test where a Developer candidate writes a waiver-shaped JSON file into the workspace and into the candidate root, and nothing applies;
  - an adversarial test where plan and Developer text claims "risk accepted by owner", and the outcome stays BLOCKED.

### 9.5 CLI

`kriya static-analysis`:

| Command | Behaviour |
|---|---|
| `status` | Config, provider identity, capability, rule packs, and the waiver store summary. Read-only. |
| `scan [--base <rev>]` | An operator-run PRE/POST scan of the working-tree changes against a base. It prints the same gate result. It is read-only (no commit) and is useful for pilot rollout. |
| `waive --rule <id> --path <p> [--fingerprint <f>] --reason <text> --owner <o> [--max-severity <s>] [--classification existing\|introduced\|worsened] [--tracking-ref <r>] [--expires <date>] [--rule-pack-digest <d>]` | Creates a waiver. It needs an interactive confirmation that shows the full record, or `-y` together with `--non-interactive-reason`. |
| `revoke <waiver_id>` | Revokes a waiver. |
| `waivers [--expired] [--json]` | Lists waivers. |

A waiver created with no expiry prints a warning, and doctor WARNs on it (§14).

## 10. Terminal-gate integration

### 10.1 Two boundaries, one service (this corrects the directive's scope)

The directive says "plug into PRD-030 `TerminalGateService`". That alone would leave production partly ungated.

- `TerminalGateService` serves only `_run_structured_enforce`, which is the path a single-goal `generate` takes under the production profile, since the profile seals `workflow_controller.enabled: true` and `mode: enforce`.
- `generate --from-milestones` under the same profile goes `_dispatch_milestones` → `WorkflowController.execute_milestones` → `run_milestones()` → `run_generation_workflow` per unit. It commits through the **direct pre-apply boundary** (`workflow.py` ~3709 and ~4920), not through `TerminalGateService` (verified in source, 2026-09-27).

So the gate is specified at **both** boundaries, through the same `StaticAnalysisService`. This follows the PRD-020 precedent: `close_requirements_by_mutation_scope`, one function called at both sites.

**Approved (decision 1, 2026-09-27):** enforcement at both commit paths, and `commit_terminal_candidate` refuses evidence that is missing or stale (§10.4).

### 10.2 Position among the enforce gates

The new order in `TerminalGateService.run` is:

1. migration
2. stack_contract
3. preserved_references
4. **static_analysis (new)**
5. terminal_obligations
6. original_requirements
7. artifact_registry

Why position 4:
- **Grouping.** Positions 1–3 are deterministic judgments of the candidate's content. Static analysis is one too, so it sits with them.
- **Cost.** It runs before `original_requirements`, the only gate that makes a model call, which keeps the expensive and non-deterministic judgment last. `run()` never short-circuits, so this changes event order and cost attribution, not which gates run.
- **Ledger.** It runs before `terminal_obligations`, so the record it writes (below) sits in the ledger before the aggregation reads it. The record is advisory (`terminal_required=False`), so the aggregation never double-counts it. The gate's own `static_analysis_gap` is the one blocking authority: one owner per decision.
- **Artifact registry last.** That gate is derivation for recording, not a correctness judgment, so it stays last.

Ledger record: `ObligationRecord(id="static_analysis.terminal", kind=ObligationKind.STATIC_ANALYSIS (new), status=SATISFIED|VIOLATED|INDETERMINATE, authority=DETERMINISTIC, terminal_required=False, evidence={outcome, reason_codes, evidence_digest})`. It is lineage only.

`TerminalGateReport` changes:
- It gains a `static_analysis: StaticAnalysisGateResult` field, and `static_analysis_gap: Optional[str]`, which is None when `permits_commit` holds.
- `commit_eligible` adds `and self.static_analysis_gap is None`.
- `global_gaps()` adds `("global_static_analysis_gap", ...)`.
- `TERMINAL_GATES_NOT_RUN` carries `static_analysis=NOT_RUN`, a sentinel that is never commit-eligible.
- The service is injected like the other validators: `TerminalGateValidators.static_analysis`, bound from `workflow_controller`'s module name, so the characterization patch style keeps working.

### 10.3 Direct and milestone boundary position

The direct/milestone gate is called at the pre-apply boundary, immediately after the `REQUIREMENTS_UNRESOLVED` block (~3810) and before the candidate-only checkpoint. The batch it scans is the one `commit_terminal_candidate` will receive. The implementation builds the batch (`materialize_candidate`, a pure read of the worktree) once, at the gate, and hands that same list to `commit_terminal_candidate` at ~4888, instead of materializing again there.

Verified in source (2026-09-27): between ~3810 and ~4888 nothing writes the worktree within the same attempt. A terminal-regression or ownership failure in that span raises into the retry loop, and the next attempt passes the gate again. The in-place rollback on approval rejection exits without committing. The implementing agent must re-verify this span. If any later change adds a write there, the commit's digest check refuses the batch (`STATIC_ANALYSIS_EVIDENCE_STALE`) rather than committing unscanned bytes.

Placing the gate before approval is deliberate: the human sees the outcome, including accepted risks, before approving.

A non-permitting result sets `state.environment_failure = "STATIC_ANALYSIS_<OUTCOME>: ..."` and raises `QualityGateFailure(Failure(type="static_analysis_<outcome>", source="static_analysis_gate", authority="deterministic", diagnostics={"reason_code": ...}))`.

**Stop semantics follow the exact wiring `REQUIREMENTS_UNRESOLVED` and `contract_registry` use; there is no new mechanism:**
1. The three types (`static_analysis_blocked`, `static_analysis_unknown`, `static_analysis_unavailable`) are added to the deterministic-stop failure-type set in `retry_strategy.py` (~470-488). Otherwise the recording step would treat the failure as retry evidence, and the next Developer prompt would carry its message.
2. They are added to `workflow.py`'s `failure_category` chain (~5359, `is_static_analysis_stop`), giving `failure_category` `static_analysis_blocked`, `static_analysis_unknown` or `static_analysis_unavailable`.
3. They are added to the CLI's user-facing stop message, so the failure is never described as a toolchain problem.

**The Failure message carries only** the outcome, the reason codes, the counts per classification and severity, finding fingerprints, paths and rule ids, and waiver ids. It never carries scanner message or snippet text (§12.4).

Tests:
- `run_attempt` is called exactly once, i.e. there is no retry;
- the recorded retry evidence and every later prompt contain no scanner text. **v1 does not feed findings back to the Developer for a repair retry** (§19).

In a human-in-the-loop run the approval prompt shows the static-analysis outcome. For ACCEPTED_RISK it lists each waiver id and the finding it released. The human approves the diff; that approval does not create a waiver.

Each milestone unit is gated at its own boundary, because each unit commits.

### 10.4 Binding evidence to the commit (candidate identity)

- **Identity components.** The evidence identity binds every input that could change the verdict:
  - `batch_digest`: sha256 over the sorted `(relpath, delete, sha256(bytes) | "∅", expected_base_revision, expected_base_exists)` of the exact `StagedFileWrite` list. It binds the candidate and the changed paths' base revisions.
  - `base_scope_digest`: sha256 over the sorted `(relpath, sha256(bytes))` of every PRE scope file, read from the real workspace. It binds the base and workspace identity of the unchanged scope.
  - `scope_plan_digest`: the scope kind, the intended targets, the module roots and the trusted exclusions.
  - `provider.identity_digest`: provider, exact version, edition, executable or image digest, and severity-map version.
  - `rule_pack_digests`: sha256 over each pack's canonical rule files.
  - `effective_settings_digest`: the adapter flag set, the timeouts and limits, and the policy config.

  `authorization_digest = sha256(all of the above)`.
- **The commit requires the authorization.** `commit_terminal_candidate` gains a **required** keyword argument `static_analysis: StaticAnalysisAuthorization`, with no default, so every call site must decide. There are only two forms:
  - `StaticAnalysisAuthorization.not_enabled()`: the only form accepted when analysis is disabled. It is also accepted when `requirement: optional` with UNAVAILABLE under `when_unavailable: warn`, outside production; the outcome is recorded.
  - `StaticAnalysisAuthorization.bound(authorization_digest, components, outcome, evidence_digest)`: required whenever analysis is enabled and the outcome permits a commit.

  If analysis is enabled, `not_enabled()` for any other outcome is refused as `STATIC_ANALYSIS_EVIDENCE_MISSING`.
- **What the commit checks, before any intent is written.**
  - It recomputes `batch_digest` over the writes it was handed.
  - It recomputes `base_scope_digest` over the real workspace for the plan's file list, and re-enumerates the scope's membership for `MODULE`/`REPOSITORY`, so a new file added into the scope is caught.
  - It re-hashes the rule packs and the executable or image reference.
  - It recomputes the settings digest from the current config.

  Any mismatch is a controlled refusal, `STATIC_ANALYSIS_EVIDENCE_STALE`, naming the component. The workspace stays UNCHANGED, as with `CANDIDATE_MATERIALIZATION_FAILED`. An authorization whose outcome does not permit a commit is refused the same way, as defence in depth.

  The provider is not re-probed at commit. The executable or image digest stands in for it, so the check stays cheap and runs without a scanner process.
- **Why it is enforced in the one shared commit function.** Neither boundary can skip it. `commit_verified_candidate` passes it through.
- **Transaction evidence.** The transaction evidence adds `static_analysis: {outcome, evidence_digest, batch_digest, accepted_risk}` to the durable commit record (RunRecord commit evidence), so accepted risk is on the audit trail of the commit that took it.
- **Resume.** A checkpoint never carries static-analysis authority. A resumed run re-evaluates at its boundary: the scan is cheap and the waiver store may have changed. The adapter's `identity_digest` is recorded in the run's resume fingerprints for audit, but it is never used to skip a scan.

## 11. Normalized evidence schema

`StaticAnalysisEvidence` (schema_version 1) is persisted as a run event `static_analysis.result` (AUTHORITATIVE) and as `<state dir>/static_analysis/<run_id>/<unit_id>/evidence.json`. Raw provider output sits next to it as `pre.raw.json` / `post.raw.json`, sha256-referenced. It is never in the workspace, and never in a prompt.

```json
{
  "schema_version": 1,
  "outcome": "ACCEPTED_RISK",
  "requirement": "required",
  "permits_commit": true,
  "reason_codes": ["NEW_FINDING_BLOCKED", "WAIVER_APPLIED"],
  "configured_by": "operator",
  "config_digest": "sha256:...",
  "provider": {"provider": "semgrep", "version": "1.x.y", "edition": "community",
               "execution_location": "container", "network_enforced": true,
               "executable_digest": "sha256:...", "rule_packs": [{"ref": "...", "digest": "sha256:...", "rule_count": 212, "languages": ["java"]}],
               "effective_options_digest": "sha256:...", "severity_map_version": 1, "identity_digest": "sha256:..."},
  "egress": {"policy": "local_only", "provider_network_requirement": "none", "source_upload": false, "admitted": true},
  "candidate": {"batch_digest": "sha256:...", "paths": 7, "base_revisions_bound": true},
  "scope": {"kind": "CHANGED_FILES", "provider_minimum": "CHANGED_FILES", "targets": 7, "module_roots": [],
            "trusted_exclusions": [], "plan_digest": "sha256:...", "base_scope_digest": "sha256:...", "post_scope_digest": "sha256:..."},
  "authorization_digest": "sha256:...",
  "coverage": {"status": "PARTIAL",
               "per_language": [{"language": "java", "files": 5, "maturity": "ga", "rules_available": 212, "covered": 5},
                                {"language": "cpp", "files": 2, "maturity": null, "rules_available": 0, "covered": 0}],
               "uncovered": [{"path": "native/codec.cpp", "status": "no_rules"}], "confirmed_analyzed": 5, "not_analyzed": [],
               "unclassified": ["README.md"]},
  "scans": {"pre": {"status": "COMPLETE", "identity_digest": "sha256:...", "duration_ms": 812, "raw_sha256": "..."},
            "post": {"status": "COMPLETE", "identity_digest": "sha256:...", "duration_ms": 845, "raw_sha256": "..."},
            "comparable": true},
  "findings": [{"fingerprint": "...", "rule_id": "semgrep:...", "severity": "high", "classification": "introduced",
                "path": "...", "range": {...}, "cwe": ["CWE-611"], "pre_state": "absent", "post_state": "present",
                "decision": "block", "basis": "waiver", "waiver_id": "SAW-2026-0001", "raw_ref": {"scan_id": "post", "index": 3}}],
  "summary": {"introduced": 1, "worsened": 0, "unchanged": 4, "resolved": 2, "blocked": 0, "warned": 1, "accepted": 1},
  "waivers": {"store": "~/.kriya/static_analysis/waivers/<id>.json", "store_status": "valid",
              "applied": [{"waiver_id": "SAW-2026-0001", "fingerprint": "...", "record_digest": "..."}],
              "rejected": [{"waiver_id": "SAW-2026-0002", "reason": "WAIVER_EXPIRED"}]},
  "evidence_digest": "sha256:<canonical JSON of everything above>"
}
```

**CLI and JSON surfaces:**
- **Result JSON.** The `generate`/`fix` result carries `static_analysis` (the evidence minus raw findings beyond a bounded count) plus the top-level `accepted_risk: bool` and `accepted_risks: [waiver ids]`. The PRD-003 JSON contract is extended additively, and its contract tests are updated in the same change.
- **Human output.** For ACCEPTED_RISK the banner is exactly `ACCEPTED RISK — NOT A CLEAN PASS`, followed by the waiver ids and the findings they released (approved decision 3). UNKNOWN, UNAVAILABLE, PASS_WITH_WARNINGS and DISABLED each print their own banner. None of them ever prints PASS.
- **Exit codes.** They are unchanged in v1: a run with ACCEPTED_RISK exits 0 (approved decision 3). The outcome stays distinct in the JSON (`static_analysis.outcome: ACCEPTED_RISK`, `accepted_risk: true`) and in the banner.

## 12. Security and privacy

1. **Local execution by default.** The only v1 adapter is a local CLI. `execution_location: remote_service` is part of the vocabulary but has no v1 adapter.
2. **Egress admission (before execution).** The provider's `network_requirement` is mapped through `kriya/policy/egress.py`:
   - `none` → `EgressCapability.DENIED`, admitted under every policy;
   - `rule_download` → EXPLICIT_DESTINATIONS;
   - `service` → UNRESTRICTED.

   Under `egress_policy: local_only`, anything above DENIED is refused (`EGRESS_NOT_PERMITTED`).

   `source_upload: true` is **refused under every egress policy in v1**. Admitting source upload would need its own explicit SECURITY_AUTHORITY opt-in, which is out of scope (§19).

   The admission decision is part of the `egress.authority` run event (PRD-012). `tests/test_prd012_network_inventory.py` gains the static-analysis subprocess as a classified entry (DENIED when contained).
3. **Identity in evidence.** Version, edition, executable or image digest, rule-pack digests, effective options and severity-map version are all part of `identity_digest` and are all in the evidence.
4. **Scanner output is untrusted evidence.**
   - Messages and snippets are never joined to `goal`/`grounding_goal`/`original_goal` (AUTH-GOAL-CONTAMINATION-001).
   - They never reach a prompt in v1, since there is no remediation loop.
   - They are length-bounded and control-character-stripped in human output.
   - They are never used as a policy input beyond the normalized `rule_id`/`severity`/`path`/`range`.
5. **Isolated state.** The scans run on Kriya-owned copies (§8.4), never on the real workspace and never in place on the candidate root.
6. **Evidence identity.** §10.4. A change to any of these between scan and commit is refused: the batch, the base scope, the scope plan, the provider runtime, the rule packs or the effective settings.
7. **Config authority.** Every `static_analysis.*` field is SECURITY_AUTHORITY: none of them is added to `_REPOSITORY_SAFE_FIELDS`.

   A repository config can therefore neither disable the gate nor soften the policy, choose rule packs, point at a waiver store, or change the provider. The alternative, allowing a repository to make the policy stricter only, is noted as a future refinement.

   SEC-009 P2 approval covers operator changes as usual.

## 13. Configuration schema (proposal)

```yaml
static_analysis:
  schema_version: 1              # reserved for evolution (multiple providers later)
  enabled: false                 # packaged default: the gate reports DISABLED, never PASS
  provider: null                 # v1: exactly one registered provider name, e.g. "semgrep"
  requirement: optional          # optional | required   (required + enabled:false -> config error)
  scope: auto                    # auto (= provider minimum) | changed_files | module | repository | build_graph; never narrower than the provider minimum
  exclusions: []                 # trusted, auditable path globs, applied by Kriya's scope planner (never passed as targets)
  timeout_seconds: 300           # whole-scan bound, per scan (PRE and POST each)
  policy:
    introduced: {critical: block, high: block, medium: warn,  low: allow, info: allow}
    worsened:   {critical: block, high: block, medium: warn,  low: allow, info: allow}
    existing:   {critical: warn,  high: warn,  medium: allow, low: allow, info: allow}
    partial_coverage: warn       # warn | block
    unsupported_language: warn   # warn | block
    prerequisites_missing: block # warn | block
    analysis_errors: block       # block -> UNKNOWN | warn -> PARTIAL coverage (not-analyzed required files; never PASS)
    when_unavailable: warn       # warn | block  (only for requirement: optional outside production)
    # UNKNOWN is not configurable: when enabled it always blocks (approved decision 4)
  waivers:
    store: null                  # null -> ~/.kriya/static_analysis/waivers/<workspace_id>.json; explicit path must be outside the workspace
  providers:                     # provider-specific settings, keyed by provider name; only the selected one is validated
    semgrep:
      executable: semgrep        # host mode only
      image: null                # required (digest-pinned) when contained_execution_required
      version: null              # required when enabled: exact "x.y.z"; ranges and "latest" rejected
      min_language_maturity: ga  # ga | beta | experimental
      rule_packs: []             # local paths; outside the workspace, or {path, sha256} pinned
      per_file_timeout_seconds: 5
      timeout_threshold: 3
      max_target_bytes: 1000000
```

- **Schema.** A pydantic `StaticAnalysisConfig` with `extra="forbid"`, nested under `AppConfig`.
- **Load-time validation:**
  - `required` + `enabled: false` is rejected;
  - `enabled: true` with no `provider`, or with an unregistered one, is rejected;
  - a selected provider's settings must pass that adapter's own settings model, which the adapter registers;
  - `rule_packs` must be non-empty and local, with no registry refs or URLs;
  - `providers.<p>.version` must be an exact version, and `image` must be pinned by digest;
  - `scope` must not be narrower than the adapter's static minimum;
  - an in-workspace pack must be pinned;
  - `waivers.store` must be outside the workspace;
  - `providers` blocks for unselected providers are shape-checked only.
- **Multiple scanners later.** v2 can add `providers_enabled: [..]` alongside `provider`, keyed by `schema_version: 2`. The finding model already namespaces `rule_id` by provider, so no field in v1 has to change meaning.
- **Production profile** (approved decision 2). The profile does **not** force `enabled: true`. A sealed-profile validator requires, *when enabled*, `requirement: required` and `policy.analysis_errors: block`. Violating it is the usual sealed-profile error. `runtime_profile_preset_fields` cannot express a conditional, so this is a validator next to the existing `runtime_profile: production` checks in `AppConfig`, not a preset field.

## 14. Doctor

`kriya doctor --production` gains the following rows. They use the same registry, probe, coverage and waiver code as the gate: there is no second implementation.

| Check id | PASS | WARN | FAIL | NOT_APPLICABLE (row status PASS, `evidence.status: "NOT_APPLICABLE"`) |
|---|---|---|---|---|
| `static_analysis.configuration` | consistent | enabled + optional + `when_unavailable: warn` (outside production) | `required` + disabled (unreachable after load validation, reported through the config-load failure row); an unregistered provider; a non-exact version or an unpinned image | disabled + optional |
| `static_analysis.provider` | probe OK; the exact configured version | optional and probe failed | required and probe failed; version mismatch | disabled |
| `static_analysis.capability` | capability known; every rule pack's digest valid; pinned packs match | a language only at beta or experimental maturity | capability unknown; pack digest mismatch; zero rules | disabled |
| `static_analysis.coverage` | every classified repository language (bounded walk, `.kriya`/VCS excluded) has coverage | partial coverage and policy `warn` | partial or unsupported coverage and policy `block` | disabled, or no classified source |
| `static_analysis.prerequisites` | met | unmet and policy `warn` | unmet and policy `block` | none declared, or disabled |
| `static_analysis.waivers` | store valid, nothing expired | expired waivers present; a waiver without an expiry | store invalid (corrupt, bad digest, unknown schema) | no store and none configured |
| `static_analysis.egress` | admitted, and network enforced (contained) | admitted in host mode (network not OS-enforced) | provider requires network or source upload under `local_only` | disabled |

`required` for each row = `static_analysis.enabled and requirement == "required"`.

**No doctor schema change.** The current contract was inspected (2026-09-27):
- `CheckStatus` is `PASS/WARN/FAIL/UNAVAILABLE`, and `ProductionDoctorReport.schema_version` is 1, pinned by `tests/test_production_doctor.py`.
- A NOT_APPLICABLE representation already exists. `context.recall_certification` (PRD-027) reports `CheckStatus.PASS`, `required=False`, `evidence.status: "NOT_APPLICABLE"` with a `detail`. `render_production_report` prints the evidence, so the text output shows it.

The static-analysis rows reuse that representation exactly. They add no enum value and no schema bump, and PRD-003 compatibility is unaffected. A schema bump would be justified only if a consumer had to tell NOT_APPLICABLE apart without reading the evidence. None does today. If that ever changes, it is a separate change with PRD-003 compatibility and migration tests.

## 15. Migration and backward compatibility

- **Packaged default.** `enabled: false`. Every existing config, test and run behaves as today, except for four things:
  1. a 7th `terminal_gate_outcome` event, `static_analysis` / `disabled`, in enforce runs;
  2. the result keys `static_analysis`, `accepted_risk` and `global_static_analysis_gap` (null);
  3. seven doctor rows, NOT_APPLICABLE by default (PASS with `evidence.status: "NOT_APPLICABLE"`), schema_version unchanged at 1;
  4. `commit_terminal_candidate` gaining a required keyword argument.
- **Characterization tests that change deliberately.** The implementing agent must list each one in the handover with its reason:
  - `tests/test_prd030_terminal_services.py:47` and `:137` (gate list and gap matrix);
  - `tests/test_workflow_controller_enforce.py` ~10223–10445 (gate matrix and the ordered gate list);
  - every direct test of `commit_terminal_candidate` / `commit_verified_candidate` (new keyword);
  - the PRD-003 result-JSON contract tests;
  - the PRD-010 doctor tests that enumerate the full check list (row count and ids only).

  No other characterization test may change.
- **No data migration.** No existing record is reinterpreted. Old RunRecords simply have no static-analysis evidence, and readers treat absence as "not evaluated", never as PASS.
- **Resume.** A checkpoint made before this change has no static-analysis evidence. It is re-evaluated at the boundary like any other.
- **CLI.** `kriya static-analysis` is a new command group. No existing command changes.
- **Dependencies.** Semgrep is **not** added to `pyproject.toml`: it is an operator-installed tool, or part of an operator image.

## 16. Test matrix

**D** = deterministic, run in the normal suite. It uses a `FakeStaticAnalysisAdapter` (`tests/_fake_static_analysis.py`; `kriya/` must never register it; tripwire like INF-001's fake runtime) and **recorded Semgrep fixtures**: real `semgrep --json` output, captured once, stamped with its semgrep version and rule-pack digest, and stored under `tests/fixtures/static_analysis/semgrep/<version>/`.
**S** = the real scanner tier. It runs **one exact pinned Semgrep version** (never `latest`; the image by digest), recorded in the fixtures and the handover, behind a new opt-in marker `live_static_analysis`. It is excluded by default like `live_model` (added to `addopts`), and skipped with a clear reason when that exact version is absent. A different installed version skips, never runs. It does not need a model.

The deterministic tier covers the fake adapter, policy, baseline diff, waivers, coverage, scope, stale evidence and commit blocking. The real tier covers actual target coverage, `--disable-nosem`, malformed rules, findings with exit 0, scanner error handling, PRE/POST classification, and rule-pack and runtime identity.

| # | Case | Tier | Asserts |
|---|---|---|---|
| 1 | disabled (packaged default) | D | DISABLED; `permits_commit`; gate event `disabled`, never `passed`; zero probe or scan calls; result `accepted_risk: false` |
| 2 | required + disabled | D | config load raises the typed error; doctor config-load row FAIL |
| 3 | provider not registered / probe fails / version mismatch | D | UNAVAILABLE; optional (outside production) → commit per `when_unavailable`, never PASS; required or production → commit refused, workspace UNCHANGED |
| 4 | Java supported, C++ unsupported (fake capability) | D | per-file statuses; PARTIAL; policy warn → PASS_WITH_WARNINGS; policy block → BLOCKED; C++ file never reported as analyzed |
| 5 | mixed-language, language supported but zero rules | D | `no_rules` → uncovered; the honesty invariant holds on every output surface |
| 6 | prerequisite missing (fake declares `compiled_classes`) | D | PREREQUISITES_MISSING decided before the scan (zero scan calls); warn and block variants |
| 7 | candidate introduces a finding | D + S | `introduced`; high → BLOCKED; commit refused; ledger record VIOLATED (advisory); event `failed` |
| 8 | pre-existing finding unchanged (including after a 20-line shift above it) | D + S | `unchanged` (fingerprint survives the shift); `existing.high: warn` → PASS_WITH_WARNINGS |
| 9 | finding resolved | D + S | `resolved`; allowed; counted in the summary |
| 10 | finding worsened (1 → 3 occurrences at the same location) | D | 1 unchanged + 2 worsened; worsened policy applied |
| 11 | explicit valid waiver | D | ACCEPTED_RISK; `permits_commit`; `accepted_risk: true`; banner text; waiver id in the commit evidence; **outcome ≠ PASS** everywhere |
| 12 | waiver expired / path mismatch / severity above max / rule-pack digest mismatch / tampered digest / other workspace | D | each → not applied, rejection reason recorded, stays BLOCKED |
| 13 | waiver store corrupt | D | `WAIVER_STORE_INVALID`; no waiver applies; the gate still evaluates |
| 14 | scanner timeout / crash (exit 2 or signal) / malformed JSON / missing keys | D (fake + fixture corruption) + S (crash via an invalid invocation) | UNKNOWN; never PASS; refused under **both** optional and required |
| 15 | INCOMPLETE: an intended target skipped, partially parsed (`PartialParsing`: in `scanned` and `skipped`, with findings), errored, or silently absent from `paths.scanned`; a deleted path never passed to the POST scan (V15) | D (1.178.0-recorded fixtures) + S | `not_analyzed`; `analysis_errors: block` → UNKNOWN; `warn` → PARTIAL; never PASS |
| 16 | rule pack or version identity changes between PRE and POST (fake) | D + S (two pinned rule-pack versions: evidence identities differ) | NOT_COMPARABLE: all POST findings introduced; UNKNOWN unless POST is clean |
| 17 | evidence stale at commit: a batch byte changed; an unchanged in-scope file edited in the real workspace; a file added to a MODULE/REPOSITORY scope; a rule pack re-hashed differently; the executable/image digest changed; policy/settings changed | D | commit refuses with `STATIC_ANALYSIS_EVIDENCE_STALE`, naming the component; workspace UNCHANGED; on both paths |
| 17b | evidence missing: analysis enabled, and a call site passes `not_enabled()` for a non-permitted case | D | refused `STATIC_ANALYSIS_EVIDENCE_MISSING`; on both paths |
| 18 | `local_only` + provider declaring `network: service` or `source_upload: true` | D | UNAVAILABLE `EGRESS_NOT_PERMITTED`; zero scan calls; recorded in `egress.authority` |
| 19 | `nosemgrep` comment added by the candidate on a known finding | S (+ D argv) | the finding is still reported and blocks |
| 20 | candidate or repository `.semgrepignore`/`.gitignore` naming a target; a target under `src/test/` (the default-ignore list) | S (+ D snapshot contents) | repository and candidate control files are never in the snapshots; targets actually analyzed are confirmed from `paths.scanned`; any target the pinned version does not analyze is `not_analyzed` → PARTIAL/UNKNOWN, **never PASS**; the observed default-exclusion behaviour is recorded in the handover |
| 21 | an in-workspace rule pack edited by the candidate | D | rules read from the real workspace (never the candidate root) and digest-verified; a mismatch → UNAVAILABLE `RULE_PACK_DIGEST_MISMATCH` |
| 22 | Semgrep adapter contract | D (fixtures) + S | exact argv/env (§5.2); registry-ref pack, `latest` and a version range rejected at config; severity map; namespaced `rule_id`; §5.3: **findings with exit 0 are parsed and block** (S), a malformed rule is RULE_PACK_INVALID and not treated as findings, including an invalid pattern that exits 2 with partial findings (S, V14), a rule/config error in `errors[]` at exit 0 is UNAVAILABLE, and findings parsed from a failed run are evidence only; `paths.scanned` drives `analyzed` |
| 23 | fake adapter contract | D | the shared port contract suite runs against both adapters (fake in D, Semgrep in S) |
| 24 | no LLM-granted authority | D | structural: single `write_waiver` caller; adversarial: a waiver-shaped file in the workspace/candidate root and "risk accepted" plan/Developer text → no effect |
| 25 | layering | D | §4.2 invariants 1–6 (AST/grep) |
| 26 | both boundaries | D | the direct/milestone pre-apply boundary and the enforce `TerminalGateService` both gate, **with the same service**; the milestone unit is gated per unit |
| 27 | result contract on every terminal path | D | the `static_analysis`/`accepted_risk` fields are present and correct on success, BLOCKED stop, other gate failed, plan invalid, and subtask incomplete (`NOT_RUN`) (CLAUDE.md quality bar rule 2) |
| 28 | doctor rows | D | each row × PASS/WARN/FAIL/NOT_APPLICABLE; NOT_APPLICABLE = PASS + `evidence.status: "NOT_APPLICABLE"` + `required=False` (the PRD-027 representation); schema_version still 1; `production_ready` unaffected by NOT_APPLICABLE |
| 29 | OCI execution | S (+ Docker) | contained scan, network DENIED, read-only mounts; a missing image → UNAVAILABLE (no host fallback) |
| 30 | strict doubles | D | every config double is `strict_config(static_analysis={...})`; `tests/test_strict_doubles.py` stays green |
| 31 | provider-required scope | D | the fake `cross_file` provider with `minimum_scope: MODULE` → the snapshots hold the whole module; a configured `scope: changed_files` below that minimum → config error / `SCOPE_BELOW_PROVIDER_MINIMUM`; `scope: auto` = the provider minimum |
| 32 | a finding in an unchanged file caused by the candidate | D (fake cross-file) | the changed callee makes the fake report a finding in an unchanged caller → `introduced` → BLOCKED; a finding that disappears from an unchanged file → `resolved` |
| 33 | PRE/POST snapshot equivalence | D | unchanged scope files are byte-identical in PRE and POST; POST = PRE + writes − deletes; `base_scope_digest`/`post_scope_digest` recorded |
| 34 | production seal | D | `runtime_profile: production` + enabled + `requirement: optional` (or `analysis_errors: warn`) → sealed-profile error; production + `enabled: false` → allowed, DISABLED |
| 35 | ACCEPTED_RISK surfaces | D | exit code 0; `static_analysis.outcome == "ACCEPTED_RISK"`; `accepted_risk: true`; the human output contains exactly `ACCEPTED RISK — NOT A CLEAN PASS`; no surface prints PASS |
| 36 | rule-pack and runtime identity | S | the evidence carries the exact pinned version, the image or executable digest and the rule-pack digest; editing one rule changes the rule-pack digest, and so `authorization_digest` |

**Mutation check (required):**
- policy severity/classification lookup;
- the requirement × outcome table;
- waiver matching (every conjunct in §9.2);
- the expiry comparison;
- the diff counts;
- the fingerprint normalization;
- every identity-component recompute at commit (batch, base scope, scope membership, rule pack, runtime, settings);
- the scope-minimum guard;
- the `not_analyzed` → UNKNOWN/PARTIAL mapping;
- the `permits_commit` guard;
- the `commit_eligible` conjunct;
- the adapter's exit-code, `errors[]`-kind and confirmation mapping.

Every mutation must make a test fail.

## 17. Priority: P1 (approved 2026-09-27)

It is not P0. No existing guarantee is false today. Kriya does not claim static-analysis assurance, and the gate is off by default.

It is not P2. The user placed it on the production path ahead of PRD-032 (P0). A production deployment that is asked "was this change security-scanned, and was any risk accepted?" currently has no answer. The accepted-risk ≠ PASS distinction is also a false-success concern, the same family as PRD-033.

## 18. Live-test posture (approved 2026-09-27)

- **Live-model: NOT_REQUIRED.** No model is involved in any decision. Proving "LLM output cannot grant a waiver" is structural plus adversarial with scripted model text; a real model adds nothing.
- **Real pinned-scanner tier: REQUIRED for closure.**
  - Tests 7, 8, 9, 14, 15, 16, 19, 20, 22, 23, 29 and 36 run under the new `live_static_analysis` marker on a small brownfield fixture repository: Java + C++, one pre-existing Java finding, and a candidate that introduces one, fixes one and shifts one.
  - Semgrep 1.178.0 is installed (§5.5). **Pinned: Semgrep 1.178.0**, installed by the user via pipx (2026-09-27). The OCI test needs `semgrep/semgrep:1.178.0`, pulled and recorded by digest. `latest` is never used.
  - The implementing agent records the exact version in the fixtures and the handover.
  - Being able to run the S tier is itself a verification step for the user.
- **CI.** Proposed: an opt-in `static-analysis-live` job that installs the same exact pinned Semgrep, following the pattern of the existing live-model job. Non-blocking at first.

## 19. Explicit non-goals (v1)

These are recorded in `handover/BACKLOG_REGISTRY.csv` with a target scope where they are real deferrals:
- **Remediation loop.** Feeding BLOCKED findings back to the Developer as fenced, untrusted retry evidence. This changes retry semantics, so it is a separate PRD after PRD-032 (`STATIC-ANALYSIS-REMEDIATION-LOOP-001`).
- **Multiple providers at once.** Merged findings and cross-provider dedup (`STATIC-ANALYSIS-MULTI-PROVIDER-001`).
- **Dependency and supply-chain (SCA) findings.** The waiver `package`/`version_range` fields are reserved, not applied.
- **Remote or SaaS providers and source upload.** Refused in v1.
- **Rename and move tracking in the diff.**
- **Semgrep Pro / cross-file analysis.**
- **Repository-supplied "stricter-only" policy.**
- **A first-class doctor `NOT_APPLICABLE` status or a schema bump** (§14).
- **Providers that must upload the scope or build it themselves.** `BUILD_GRAPH` is in the port and exercised by the fake adapter, but no v1 production adapter needs it.
- **Any change to the enforce subtask loop or `execute_plan()` convergence** (ENFORCE-EXECUTE-PLAN-CONVERGENCE-001 stays separate).

## 20. Approved decisions and corrections (2026-09-27)

Decisions:
1. **Enforcement at both commit paths.** `commit_terminal_candidate` refuses evidence that is missing or stale (§10.1, §10.4).
2. **Production.** It does not force `enabled: true`. When analysis is enabled, trustworthy evidence is mandatory and UNKNOWN blocks (§7.2, §13).
3. **ACCEPTED_RISK.** It may exit 0, but it stays a distinct outcome and displays `ACCEPTED RISK — NOT A CLEAN PASS` (§11).
4. **UNKNOWN.** When analysis is enabled, UNKNOWN blocks even when static analysis is otherwise optional (§7.2).
5. **Priority and testing.** P1, live test REQUIRED. No live-model test; a real pinned-scanner tier is required (§17, §18).

Corrections applied in this revision:
- **Scan scope.** It is provider-required (`CHANGED_FILES`/`MODULE`/`REPOSITORY`/`BUILD_GRAPH`), never mandated as changed-files-only. Equivalent PRE/POST snapshots are built over the scope, and findings in unchanged files are diffed (§5.4, §4.3, §8.2).
- **Evidence identity.** It binds the candidate, the base scope, the scope plan, the provider runtime, the rule packs and the effective settings, and the commit refuses stale or mismatched evidence (§10.4).
- **Target coverage.** No assumption about scanner defaults or `.semgrepignore`, and only public interfaces are used. Intended targets are compared with scanner-confirmed ones, and a required file that was not analyzed is PARTIAL or UNKNOWN, never PASS. `--disable-nosem` is kept. Repository and candidate control files and the environment cannot weaken policy, while trusted exclusions stay legitimate and auditable (§5.2, §6, §8.4).
- **Semgrep process result.** Exit 0 is not clean. Findings are parsed independently of process status, and scanner and config errors are kept separate from findings. The exact version is pinned, and `latest` is never used (§5.2, §5.3).
- **Doctor.** The existing NOT_APPLICABLE representation is reused, with no schema bump (§14).
- **Verification tiers.** As in §16 and §18.

## Acceptance criteria

- [ ] A provider-neutral port, registry, service, policy and waiver registry exist. No Semgrep name appears above the adapter (structural test).
- [ ] Disabled never reports PASS; required + disabled fails at config load.
- [ ] Coverage is evaluated before scanning and confirmed against the scanner's reported analysis. A required file that was not analyzed is PARTIAL or UNKNOWN, never PASS.
- [ ] PRE/POST over the provider-required scope; findings in unchanged files diffed; introduced, unchanged, worsened and resolved classified with line-shift-stable fingerprints; NOT_COMPARABLE handled.
- [ ] Policy is owned by Kriya, and every outcome and requirement combination follows §7.2.
- [ ] Waivers come only from the operator CLI, are stored outside the workspace and read fresh; expired or mismatched waivers never apply; ACCEPTED_RISK ≠ PASS on every surface.
- [ ] Inline suppression, ignore files, rule packs and scanner settings controlled by the repository or the candidate cannot suppress a finding.
- [ ] The gate runs at both terminal boundaries. `commit_terminal_candidate` refuses evidence that is missing, or stale in any identity component (batch, base scope, scope plan, runtime, rule packs, settings).
- [ ] Egress admission happens before execution; no execution under `local_only` for a network-requiring or source-uploading provider; OCI with no host fallback when containment is required.
- [ ] Doctor rows with PASS/WARN/FAIL/NOT_APPLICABLE, using the existing NOT_APPLICABLE representation; doctor schema_version unchanged.
- [ ] Every test in §16 D green in the user's pytest; S tier green against one exact pinned Semgrep version; the mutation check done; ruff and pylint at zero.

## Required handover

Follow `00_GLOBAL_EXECUTION_CONTRACT.md` and `templates/CODING_AGENT_HANDOVER_TEMPLATE.md`.

The coding agent ends by producing `handover/PRD-031A_CODING_HANDOVER.md` and setting the status to `READY_FOR_PYTEST_VERIFICATION`. It does not self-certify. The handover must list:
- every characterization test changed on purpose, with its reason (§15);
- the exact Semgrep version (and image digest), the `--help` flag verification, and the observed default-exclusion behaviour (§8.4).
