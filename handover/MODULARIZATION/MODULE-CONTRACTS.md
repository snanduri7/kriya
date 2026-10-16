# MODULE-CONTRACTS

Signature-level public contracts. Conventions: `Protocol` for ports implemented by adapters, `ABC` only where the existing seam is an ABC (`InferenceRuntimePort`), `@dataclass(frozen=True)` for every request/decision/evidence value, closed `Enum`/`Literal` for every reason code (tripwire-tested tables), `Optional` never used to mean "unknown" when a typed reason exists. Every contract carries the context it needs explicitly (owner decision 14); nothing reads `Path.home()`, `os.environ` or `os.getcwd()` behind a contract except CONFIG's `environment.py`. All names below are the names the slices create; where a name already exists the slice evolves it (no parallel type).

## 1. FOUNDATION (`kriya/foundation/`)

```python
class KriyaError(Exception):            # base of every typed error; subclasses carry .reason_code: str
    reason_code: str
NewType RunId(str); LaunchId(str); WorkspaceId(str); RequestId(str); FindingId(str); TransactionId(str)

class Clock(Protocol):
    def monotonic(self) -> float: ...
    def now_iso(self) -> str: ...
@dataclass(frozen=True)
class Deadline:                          # absolute monotonic instant + the reason it exists
    at_monotonic: float; label: str
    def remaining(self, clock: Clock) -> float: ...
    def expired(self, clock: Clock) -> bool: ...
class CancellationToken(Protocol):
    def cancelled(self) -> bool: ...
    def reason(self) -> Optional[str]: ...
def sha256_json(obj: Any) -> str        # the one canonical-JSON digest helper (today copied in model_runtime._sha256_json, execution_plan._sha256, ...)
```
Rule: FOUNDATION imports nothing under `kriya.` (architecture test).

## 2. CONTEXT (`kriya/context/model.py`)

```python
@dataclass(frozen=True)
class PrincipalContext:                 # local mode: PrincipalContext.local() == PrincipalContext(id="local", display="local operator", security_domain=SecurityDomain.LOCAL)
    id: str; display: str; security_domain: "SecurityDomain"
class SecurityDomain(str, Enum): LOCAL = "local"      # future: per-user/org domains; provider cache-isolation keys on it
@dataclass(frozen=True)
class WorkspaceContext:
    workspace_id: WorkspaceId              # context.workspace_identity.workspace_identity_key(root) (moved from control)
    root: str                              # realpath
    config_dir: str
    state_dir: str                         # resolved ONCE by ConfigService (trace_db lives here)
@dataclass(frozen=True)
class RunScope:                            # the typed identity of one run; NOT the mutating lease (control.run_coordinator.RunContext keeps that role)
    run_id: RunId; launch_id: Optional[LaunchId]; workspace: WorkspaceContext; principal: PrincipalContext
    config_fingerprint: str; started_iso: str
@dataclass(frozen=True)
class ScopeRef:                            # where a scoped thing (config/skill/tool/evidence/state) comes from
    kind: Literal["system", "organization", "user", "project", "run"]; id: str
@dataclass(frozen=True)
class RequestContext:                      # built by the client/adapter boundary for every operation
    request_id: RequestId; principal: PrincipalContext; workspace: Optional[WorkspaceContext]
    run: Optional[RunScope]; deadline: Optional[Deadline]; cancellation: Optional[CancellationToken]
    config_scope: Tuple[ScopeRef, ...]; skills_scope: Tuple[ScopeRef, ...]; tools_scope: Tuple[ScopeRef, ...]
    evidence_scope: ScopeRef; state_scope: ScopeRef
    @staticmethod
    def local(workspace_root: Optional[str], *, request_id: Optional[str] = None) -> "RequestContext": ...
```

## 3. CONFIG (`kriya/config/`)

```python
class Environment(Protocol):              # config/environment.py: THE only reader of os.environ / Path.home() for Kriya homes
    def state_dir(self) -> Optional[str]            # KRIYA_STATE_DIR
    def log_dir(self) -> Optional[str]              # KRIYA_LOG_DIR
    def authority_home(self) -> str                 # KRIYA_AUTHORITY_HOME | ~/.kriya/authority
    def mcp_approval_home(self) -> str              # KRIYA_MCP_APPROVAL_HOME
    def static_analysis_home(self) -> str           # KRIYA_STATIC_ANALYSIS_HOME
    def qualification_home(self) -> str; def certification_home(self) -> str
    def trust_file(self) -> Optional[str]           # KRIYA_TRUST_FILE
    def toolchain(self, name: Literal["JAVA_HOME", "MAVEN_HOME", "GRADLE_HOME", "PATH"]) -> Optional[str]
    def model_runtime_probe_enabled(self) -> bool   # KRIYA_MODEL_RUNTIME_PROBE
def process_environment() -> Environment  # the real one; tests inject a fake through ConfigService

@dataclass(frozen=True)
class ResolvedConfiguration:
    app: AppConfig; config_dir: str; config_path: Optional[str]; fingerprint: str
    authority: "ConfigAuthorityReport"    # SEC-009 outcome (fields, approval status)
    state_dir: str; log_dir: str
class ConfigService(Protocol):
    def resolve(self, ctx: RequestContext, *, config_path: Optional[str] = None,
                trust_file: Optional[str] = None, scopes: Tuple[ScopeRef, ...] = ()) -> ResolvedConfiguration  # today: load_config() + resolve_config_state(); scopes beyond project are refused (NOT_SUPPORTED_SCOPE), never ignored
    def classify(self, field: Tuple[str, ...], value: Any) -> FieldClassification                                # config.authority.classify_field
# New AppConfig fields (H4) and their SEC-009 classification (REPOSITORY_SAFE unless noted):
#   CORRECTED 2026-10-16 (primary-agent validation; the design draft aliased the old field to the liveness ceiling, which leaves the
#   Scenario-04 symptom unfixed: 673 s elapsed + a 300 s fallback pass > 900 s wall clock, so the LLM deadline would kill the admitted pass):
#   generation_time_budget_seconds KEEPS its present meaning = the TOTAL ACTIVE inference budget (planner+developer+repair+fallback model/gate time),
#     HUMAN_WAIT excluded. It is the cap llm.py::_deadline and attempt.py::_ensure_generation_time_budget enforce today, minus human wait.
#   autonomy.budgets.run_liveness_seconds = NEW separate wall-clock hang ceiling (includes human wait). Unset -> derived =
#     generation_time_budget_seconds + autonomy.budgets.human_wait_allowance_seconds (default 1800). Hitting it stops the run with a typed
#     liveness reason code. Invariant (tested): liveness ceiling > total active budget, so a human wait can never starve an admitted pass.
#   autonomy.budgets.planner_inference_seconds, developer_inference_seconds, repair_inference_seconds,
#   autonomy.budgets.verification_seconds, fallback_reserve_seconds, human_wait_counts_against_inference: bool (default False)
#   autonomy.budgets.per_file_estimate_fallback_seconds (replaces generation_seconds_per_file_estimate; old name accepted, same value)
```
Policy stays separate: `AppConfig` is data; every decision over it lives in `policy/`.

## 4. AUTHORITY (`kriya/policy/`)

```python
class AuthorityDecision(str, Enum): ALLOW = "allow"; DENY = "deny"; REQUIRE_APPROVAL = "require_approval"
@dataclass(frozen=True)
class AuthorityResult: decision: AuthorityDecision; reason_code: str; detail: str; identity_digest: str
@dataclass(frozen=True)
class CandidateIdentity:                   # binds reviewed == built == tested == runtime == applied
    files: Tuple[Tuple[str, str], ...]     # (relpath, raw sha256) sorted
    digest: str                            # launch_approval.candidate_digest(...) today
@dataclass(frozen=True)
class SourceApprovalRequest: launch_id: LaunchId; review_id: str; candidate: CandidateIdentity; diff_counts: Tuple[int, int]
@dataclass(frozen=True)
class RuntimeCommandApprovalRequest: launch_id: LaunchId; review_id: str; command_fingerprint: str; commands: Tuple[str, ...]
class AuthorityPort(Protocol):             # policy/authority.py: the ONE facade; implementations are today's functions
    def evaluate_action(self, request: "ActionRequest") -> "PolicyDecision"                 # policy.execution.ExecutionPolicy.evaluate
    def file_writer(self, ctx: RequestContext, scope: "FilesystemScope") -> "AuthorizedFileWriter"
    def file_reader(self, ctx: RequestContext, scope: "FilesystemScope") -> "AuthorizedFileReader"
    async def await_source_decision(self, req: SourceApprovalRequest, deadline: Deadline) -> "LaunchReviewDecision"   # control.launch_approval.await_decision
    async def await_runtime_decision(self, req: RuntimeCommandApprovalRequest, deadline: Deadline) -> "RuntimeCommandDecision"
    def stale(self, approved: CandidateIdentity, current: CandidateIdentity) -> AuthorityResult   # attempt.assert_candidate_unchanged_since_source_review
    def bind_verified_candidate(self, writes) -> "VerifiedCandidateBinding"                    # verification_binding.bind_candidate
    def config_authority(self, cfg_state) -> "ConfigAuthorityReport"                            # config.authority + authority_approval
    def mcp_invocation(self, identity: "MCPToolIdentity", profile: "MCPCapabilityProfileIdentity") -> AuthorityResult
```
Human-wait accounting hook: `await_source_decision`/`await_runtime_decision` return the elapsed wait in the decision (`waited_seconds: float`), which `BudgetLedger.record_human_wait` consumes (H4). No approval semantic changes.

## 5. EVIDENCE (`kriya/core/attempt_evidence/`)

```python
class DecisionRecord(Protocol):            # decisions.py; every record is a frozen dataclass with:
    kind: str                              # member of model.KINDS
    decision: str                          # closed per-kind vocabulary
    reason_code: str; inputs_digest: str; attempt: int; unit: Optional[str]
    def payload(self) -> Dict[str, Any]: ...
def emit_decision(record: DecisionRecord) -> None     # scope.emit(record.kind, record.payload()); never raises
class EvidenceReader(Protocol):            # reader.py today
    def manifest(self, run_id: RunId) -> Dict[str, Any]
    def records(self, run_id: RunId, *, kind: Optional[Tuple[str, ...]] = None, cursor=None, limit: int = 200) -> "RecordPage"
    def content(self, run_id: RunId, seq: int, part: str) -> bytes
    def verify(self, run_id: RunId) -> "VerificationReport"
# new KINDS: "attribution.decision", "retry_target.decision", "candidate_transaction.decision", "budget.decision" (existing: "fallback.decision")
```
Rule: no renderer/adapter reads the store directly; `EvidenceService` (APPLICATION) wraps `EvidenceReader` with the visibility policy (`kup/policy.py` content_visibility stays the policy source).

## 6. STATE / HISTORY (`kriya/control/`)

```python
class RunStateStore(Protocol):             # persistence.py today
    def save_run_record(self, ws: WorkspaceContext, record: RunRecord) -> None
    def load_run_record(self, ws: WorkspaceContext, run_id: RunId) -> Optional[RunRecord]
    def list_run_records(self, ws: WorkspaceContext) -> RunRecordScan
    def save_launch_record / load_launch_record / save_launch_review_decision / load_launch_review_decision ...
class ControlStore(Protocol): def write_control_file(self, ws: WorkspaceContext, relpath: str, data: bytes) -> None   # the only .kriya/ writer
class CheckpointStore(Protocol):           # control/checkpoint.py (moved)
    def save(self, ws, checkpoint) -> str; def load(self, ws, run_id) -> Optional[Checkpoint]; def delete(...); def latest(...)
class HistoryStore(Protocol):              # control/history.py (from kup/store.py + acquire.py)
    def snapshot(self, ws: WorkspaceContext, state_dir: str) -> SnapshotRef    # acquire_snapshot
    def list(self, snapshot: SnapshotRef, *, limit: int, cursor) -> HistoryPage
    def detail(self, snapshot: SnapshotRef, run_id: RunId, *, include_prompt: bool) -> RunDetail
class RunCoordinator(Protocol):            # run_coordinator.py: the mutating-run lease (names unchanged)
    def begin_mutating_run(...) -> RunContext; def current_run_context() -> Optional[RunContext]; def mark_run_stage(...); ...
    # claim_run_generation_clock is REMOVED in H2: the lease carries `budget: BudgetLedger` instead (see §12)
```
Invariant kept: history is scoped to the active Kriya state directory (`trace_db_path(cfg)`), never reconstructed from `<project>/.kriya/control/runs`.

## 7. PROVIDER (`kriya/core/inference_runtime.py`, evolved; owner decision 8)

```python
@dataclass(frozen=True)
class ProviderIdentity: provider: str; adapter_version: str; endpoint_identity: str   # endpoint_identity = model_runtime.endpoint_identity(base_url)
@dataclass(frozen=True)
class ModelIdentity: model: str; fingerprint_digest: Optional[str]                     # ModelRuntimeFingerprint.digest when probed
@dataclass(frozen=True)
class RuntimeCapabilities:                 # EXISTING, extended
    per_request_context_window: bool; native_identity_probe: bool; stream_usage: bool = False; json_schema_output: bool = False
    streaming: bool = True; cancellation: bool = True; tool_calls: bool = False; structured_output: bool = False
    health_probe: bool = False; timing_metadata: bool = False
@dataclass(frozen=True)
class InferenceRequest:                    # EVOLVED ChatRequest (ChatRequest keeps its name as an alias until P1; same fields + the new ones)
    model: str; messages: List[Dict[str, Any]]; temperature: Optional[float]; max_tokens: int
    extra_body: Optional[Dict[str, Any]] = None; response_format: Optional[Dict[str, Any]] = None
    stream_callback: Optional[Callable[[str], None]] = None; tools: Optional[List[Dict[str, Any]]] = None; timeout: Any = None
    request_id: Optional[RequestId] = None; run_id: Optional[RunId] = None
    principal: Optional[str] = None; security_domain: Optional[str] = None; cache_isolation_key: Optional[str] = None
    context_budget_tokens: Optional[int] = None; output_budget_tokens: Optional[int] = None
    deadline: Optional[Deadline] = None; priority_hint: Optional[str] = None; cancellation: Optional[CancellationToken] = None
@dataclass(frozen=True)
class TimingObservation: provider: str; model: str; role: str; seconds: float; prompt_tokens: int; completion_tokens: int; file_count: Optional[int]
class RuntimeErrorKind(str, Enum): TIMEOUT, CONNECTION, AUTHENTICATION, RATE_LIMITED, SERVER, REQUEST, CANCELLED (new), CONTEXT_EXCEEDED (new)
class ProviderFailure(KriyaError): kind: RuntimeErrorKind; provider: str; model: str; retryable: bool
@dataclass(frozen=True)
class HealthReport: provider: str; endpoint_identity: str; reachable: bool; serves_model: Optional[bool]; served_context: Optional[int]; reason_code: str
class InferenceRuntimePort(abc.ABC):       # EXISTING methods unchanged (wire_payload, configured/with/without_context_window, request_plan,
                                           # observe_served_context(_state), pin_served_configuration, probe, complete, complete_with_tools, classify_error, list_models)
    name: str; capabilities: RuntimeCapabilities
    def identity(self, *, base_url: str) -> ProviderIdentity: ...                                   # new
    def health(self, *, base_url: str, model: str, api_key: str, fetch_json) -> HealthReport: ...   # new (default: via list_models)
    def timing_hint(self, model: str, observations: Sequence[TimingObservation]) -> Optional[float]  # new: seconds/file estimate for THIS provider+model; None = no evidence
    def typed_failure(self, error: BaseException, *, model: str) -> ProviderFailure                  # new: wraps classify_error
# registry (EXISTING functions keep their names): register_runtime_adapter(name, adapter), unregister_runtime_adapter(name), runtime_adapter(name), runtime_for_binding(binding)
def registered_runtimes() -> Tuple[str, ...]                                                       # new
def select_runtime(cfg: ResolvedConfiguration, binding: Any) -> Tuple[InferenceRuntimePort, ProviderIdentity]   # new: the ONLY provider-selection site (today spread in llm.py/_runtime, model_runtime.resolve_configured_model_runtime)
class TimingStore(Protocol):               # core/model_runtime.timing (new): provider/model-scoped observations for budgets (H4)
    def record(self, obs: TimingObservation) -> None; def observations(self, provider: str, model: str, role: str) -> Tuple[TimingObservation, ...]
```
`LLMClient` keeps `complete`, `complete_result`, `complete_with_tools`, `_request_once`, `_recorded_dispatch` (patch points). It gains `ctx: Optional[RequestContext]` and reads its deadline from `request.deadline` (H2), never from `control.run_coordinator`.

## 8. SKILLS (`kriya/skills/registry.py`)

```python
@dataclass(frozen=True)
class SkillRef: id: str; version: str; scope: ScopeRef; source_path: str; digest: str
class SkillState(str, Enum): ENABLED, DISABLED, UNVERIFIED, INCOMPATIBLE, ERROR
@dataclass(frozen=True)
class SkillRecord: ref: SkillRef; descriptor: SkillDescriptor; state: SkillState; compatibility: str; health: str
@dataclass(frozen=True)
class EffectiveSkills: run: RunScope; selected: Tuple[SkillRecord, ...]; rejected: Tuple[Tuple[SkillRef, str], ...]; resolution_digest: str
class SkillRegistry(Protocol):
    def discover(self, ctx: RequestContext) -> Tuple[SkillRecord, ...]        # SkillEngine.discover_and_load + SkillService.list
    def load(self, ref: SkillRef) -> Skill
    def validate(self, ref: SkillRef) -> Tuple[str, ...]                      # reason codes
    def resolve_for_run(self, ctx: RequestContext, repo_model: RepositoryModel, goal: str) -> EffectiveSkills   # once per run
    def set_state(self, ref: SkillRef, state: SkillState, *, by: PrincipalContext) -> SkillRecord
    def stage_rule(self, ref: SkillRef, rule: str, source: str) -> None       # skills/extraction.py (lesson extraction)
```
`ignite-java17` loads through `LocalSkillRegistry(SkillEngine)` (J1 test).

## 9. TOOLBOX (`kriya/toolbox/`)

```python
@dataclass(frozen=True)
class ToolMetadata: id: str; provider: Literal["plugin", "mcp"]; name: str; capabilities: DeclaredCapabilities; requires_approval: bool
                    network: str; permission_requirements: Tuple[str, ...]; health: str; schema_digest: Optional[str]
@dataclass(frozen=True)
class ToolExecutionRequest: ctx: RequestContext; tool_id: str; arguments: Dict[str, Any]; confirmation: Optional[str]
@dataclass(frozen=True)
class ToolExecutionResult: tool_id: str; ok: bool; output: Any; decision: "PolicyDecision"; reason_code: str; evidence_seq: Optional[int]
class ToolRegistry(Protocol):
    def list(self, ctx: RequestContext) -> Tuple[ToolMetadata, ...]           # ToolService.list + Kernel.registry
    def describe(self, ctx: RequestContext, tool_id: str) -> Optional[ToolMetadata]
    def approval_state(self, ctx: RequestContext, tool_id: str) -> ApprovalState
    def resolve(self, tool_id: str) -> BaseTool                                 # exact match, never fuzzy (tool001 test)
    async def execute(self, req: ToolExecutionRequest) -> ToolExecutionResult   # the ONE path into BaseTool.execute (TOOL-001/002 preserved)
```

## 10. EXECUTION (`kriya/tools/execution_port.py`)

```python
@dataclass(frozen=True)
class ExecutionRequest: ctx: RequestContext; workspace_root: str; kind: Literal["compile", "tests", "runtime", "pom_validate", "command"]
                        targets: Tuple[str, ...]; command: Optional[Tuple[str, ...]]; deadline: Optional[Deadline]; containment: Optional[ContainmentProfile]
@dataclass(frozen=True)
class ExecutionResult: ok: bool; reason_code: str; stdout_tail: str; stderr_tail: str; exit_code: Optional[int]; duration_seconds: float
                       findings: Tuple["StructuredFinding", ...]; evidence: Dict[str, Any]; gate_result_seq: Optional[int]
@dataclass(frozen=True)
class StructuredFinding:                   # machine-readable gate finding (SCENARIO-04 §5: file, line, symbol owner, kind)
    kind: str; file: Optional[str]; line: Optional[int]; column: Optional[int]; symbol: Optional[str]; owner_type: Optional[str]; message: str; raw: str
class ExecutionPort(Protocol):
    def compile(self, req: ExecutionRequest) -> ExecutionResult; def run_tests(...); def run_runtime(...); def pom_validate(...); def run_command(...)
    def prepare_runtime(self, req: ExecutionRequest) -> ExecutionResult
class ProcessPort(Protocol): run / run_async / start_managed (= tools.process.ProcessController, unchanged)
```
`PolymorphicValidator` implements `ExecutionPort` by delegating to its existing methods (A-type), so `patch("kriya.tools.validate.PolymorphicValidator.run_compile_check")` keeps intercepting.

## 11. REPOSITORY (`kriya/repository/port.py`)

```python
@dataclass(frozen=True)
class WorktreeRef: workspace_root: str; path: str; base_revision: Optional[str]; scoped: bool
class RepositoryPort(Protocol):
    def read(self, ws: WorkspaceContext, relpath: str) -> Optional[bytes]; def snapshot(self, ws, relpath) -> FileSnapshot   # repository.snapshot
    def digest(self, data: bytes) -> str                                         # raw_digest
    def atomic_write(self, path: str, data: bytes) -> None                       # repository.atomic_write (Kriya-owned files only; candidate writes go through AuthorizedFileWriter)
    def commit_grounded(self, ws, writes: Sequence[StagedFileWrite], *, expected_revisions) -> BatchCommitResult   # repository.commit
    def head(self, ws) -> Optional[str]; def status(self, ws) -> Tuple[StatusEntry, ...]; def tree_hash(self, ws) -> str
    def create_worktree(self, ws, *, name: Optional[str] = None, scoped_paths=None) -> WorktreeRef; def remove_worktree(self, ref: WorktreeRef) -> None
    def diff(self, ws, base: str, head: str) -> str; def untracked(self, ws) -> Tuple[str, ...]
    def describe_checkout(self, ws) -> CheckoutFacts; def export_git_metadata(...)   # git_metadata
```
All `"git"` argv construction lives in `repository/git.py` (architecture test: `"git"` argv + subprocess outside REPOSITORY/EXECUTION/PLATFORM fails).

## 12. KERNEL domain types for Scenario-04 (owner decisions 3-5)

```python
class EvidenceClass(str, Enum):            # precedence order, highest first (owner decision 3)
    DERIVED_DETERMINISTIC = "derived_deterministic"      # compiler/test/validator locator, structured Kriya finding
    AUTHORITATIVE_STRUCTURAL = "authoritative_structural" # parsed manifest, AuthorizedFileWriter denial, plan ownership
    OTHER_DETERMINISTIC = "other_deterministic"           # test-fixture precondition, judge evidence with file:line
    MODEL_CLAIMED = "model_claimed"                       # self-diagnosis, typed redirect, triage: advisory, lowest
PRECEDENCE: Tuple[EvidenceClass, ...] = (DERIVED_DETERMINISTIC, AUTHORITATIVE_STRUCTURAL, OTHER_DETERMINISTIC, MODEL_CLAIMED)
class FindingState(str, Enum): OPEN, TARGETED, RESOLVED, SUPERSEDED, REFUSED
@dataclass(frozen=True)
class Locator: file: str; line: Optional[int]; symbol: Optional[str]; evidence_class: EvidenceClass; source: str
@dataclass(frozen=True)
class Finding:                             # workflow/finding.py (evolves open_findings.OpenFinding: failure_type, reason_code, files, source, evidence, first_attempt)
    id: FindingId; type: str; reason_code: Optional[str]; source: Literal["gate", "validator", "orchestrator", "model", "authority"]
    evidence: str; evidence_digest: str; locators: Tuple[Locator, ...]; affected_files: Tuple[str, ...]; unit: Optional[str]
    repair_scope: Tuple[str, ...]; state: FindingState; precedence: EvidenceClass; depends_on: Tuple[FindingId, ...]; first_attempt: int
@dataclass(frozen=True)
class Provenance(str, Enum):               # OWNER DIRECTIVE 2 #5: authority comes from PROVENANCE, never from which optional field is populated
    DETERMINISTIC_EXACT_LOCATOR = "deterministic_exact_locator"          # file:line from compiler/test/validator output (highest)
    STRUCTURED_AUTHORITATIVE_FILE_SET = "structured_authoritative_file_set"  # validator/manifest file set (e.g. migration manifest); high
    STRUCTURAL_DEPENDENCY = "structural_dependency"                      # code_intel symbol/type edge, plan ownership; medium/high by source
    MODEL_SELF_DIAGNOSIS = "model_self_diagnosis"                        # advisory
    MODEL_TRIAGE = "model_triage"                                        # advisory/fallback
# F1 MUST first classify every current producer of failure.authoritative_files into a Provenance (MEASURED from the producers, not
# assumed from the field name); the conflict rule when both an exact locator and a file set occur is derived from that table and pinned
# by a regression test built from the real recorded case. A rule such as "authoritative_files > locator" is forbidden unless every producer proves it.
@dataclass(frozen=True)
class AttributionSource:                   # callers read authority from these fields, never infer it from which optional field was set
    tier: str; evidence_class: EvidenceClass; provenance: Provenance; authority_level: int; files: Tuple[str, ...]
    confidence: Literal["high","medium","low"]; reasoning: str; evidence_ref: str; exhausted: bool
@dataclass(frozen=True)
class AttributionDecision:                 # evolves attribution.AttributionResult (tier, files, confidence, reasoning stay as properties)
    finding_id: FindingId; selected: AttributionSource; considered: Tuple[AttributionSource, ...]; ignored_advisory: Tuple[AttributionSource, ...]
    precedence_rule: str; reason_code: str; inputs_digest: str
    @property tier / files / confidence / reasoning
class TriageEligibility(str, Enum): ELIGIBLE, NOT_SOURCE_GROUNDED, SINGLE_CANDIDATE, ORCHESTRATOR_NOTICE, NO_MODEL_BUDGET
def triage_eligibility(finding: Finding, known_files: Sequence[str], budget: "BudgetView") -> TriageEligibility   # E: only ELIGIBLE invokes a model
@dataclass(frozen=True)
class RedirectSignal: from_file: str; to_file: str; statement: str                   # typed "the defect is in Y" from response_protocol (F4), never a mention
@dataclass(frozen=True)
class RetryTargetDecision:
    attempt: int; findings: Tuple[FindingId, ...]; attribution: Tuple[AttributionDecision, ...]
    selected_files: Tuple[str, ...]; read_only_context: Tuple[str, ...]; ignored_advisory: Tuple[Tuple[str, str], ...]   # (file, why)
    rejected_candidates: Tuple[Tuple[str, str], ...]                 # (file, reason): out of scope, contradicted by stronger evidence, unevaluated A, ...
    origin: Literal["original", "expansion", "fallback"]            # OWNER DIRECTIVE 2 #6: ONE recorded decision answers: unresolved findings,
    authoritative_locator: Optional[Locator]; advisory_evidence: Tuple[AttributionSource, ...]   # locator, advisory evidence, rule applied,
    scope_filter: str; precedence_rule: str; reason_code: str; inputs_digest: str                 # selected, rejected + why, original/expansion/fallback
# EXPANSION rule (owner directive 2 #4; F4): advisory file B may be ADDED (origin="expansion") only when ALL hold: deterministic locator file A was
# staged AND its change evaluated by the authoritative gate; the same NORMALIZED failure signature remains; B is inside the legal work-unit/write scope;
# no stronger deterministic attribution contradicts B; structural dependency evidence A<->B is used when it exists (model prose alone never
# establishes dependency). B never replaces A's authoritative attribution. A bare mention, a quoted compiler message naming another file, or a
# SUCCESSFUL deterministic repair of A can never produce an expansion.
@dataclass(frozen=True)
class RepairPackage:                       # evolves retry_package.RetryPackage; the ONE source of truth rendered by retry_prompts
    findings: Tuple[Finding, ...]; targets: RetryTargetDecision; allowed_edit_scope: Tuple[str, ...]; authoritative_locators: Tuple[Locator, ...]
    target_projections: Tuple[FileProjection, ...]; reference_projections: Tuple[FileProjection, ...]; advisory_context: str
    def validate(self) -> Tuple[str, ...]  # reason codes; non-empty -> RepairPackageContradiction BEFORE any model call (F)
class RepairPackageContradiction(KriyaError): ...   # e.g. TARGET_OUTSIDE_EDIT_SCOPE, LOCATOR_FILE_NOT_TARGET, FINDING_WITHOUT_TARGET
@dataclass(frozen=True)
class FileProposal: path: str; response_seq: int; parse: Literal["ok","invalid"]; reason_code: Optional[str]; edit_result: Optional[EditResult]
                    staged_digest: Optional[str]; findings_addressed: Tuple[FindingId, ...]; redirect: Optional[RedirectSignal]
@dataclass(frozen=True)
class IndependenceProof: pairs: Tuple[Tuple[str, str, Literal["independent","dependent"], str], ...]   # (a, b, verdict, basis: no shared symbol/type edge in code_intel + no finding dependency)
@dataclass(frozen=True)
class CandidateTransaction:                # workflow/candidate_transaction.py
    id: TransactionId; attempt: int; proposals: Tuple[FileProposal, ...]; proof: IndependenceProof
    staged: Tuple[str, ...]; refused: Tuple[Tuple[str, str], ...]; atomic_groups: Tuple[Tuple[str, ...], ...]
    staged_identity: CandidateIdentity     # == what source review and gates later see (G4 test: reviewed bytes == staged bytes)
    decision: Literal["staged", "partially_staged", "refused"]; reason_code: str
class BudgetPhase(str, Enum): PLANNER, DEVELOPER, REPAIR, VERIFICATION, FALLBACK_RESERVE, HUMAN_WAIT, LIVENESS
# OWNER DIRECTIVE 2 #3: five quantities, NEVER conflated internally: ACTIVE inference budget (generation_time_budget_seconds), HUMAN WAIT elapsed,
# TOTAL RUN WALL-CLOCK, RUN LIVENESS CEILING (run_liveness_seconds), GATE/FALLBACK RESERVES. The liveness default is
# `active + HUMAN_WAIT_ALLOWANCE_SECONDS` where HUMAN_WAIT_ALLOWANCE_SECONDS = 1800 is ONE documented constant owned by workflow/budget_policy.py
# (surfaced as autonomy.budgets.human_wait_allowance_seconds in config); no other module contains the literal. Liveness termination has its own
# typed reason code and BudgetDecision/evidence, distinct from the generation-budget-exhausted reason.
@dataclass(frozen=True)
class BudgetView: phase: BudgetPhase; allowed_seconds: Optional[float]; consumed_seconds: float; remaining_seconds: Optional[float]; liveness_remaining: Optional[float]
@dataclass(frozen=True)
class BudgetDecision: phase: BudgetPhase; action: Literal["admit","refuse","warn"]; estimate_seconds: float; estimate_source: Literal["provider_model_timing","configured_fallback"]
                      provider: str; model: str; file_count: int; view: BudgetView; reason_code: str; inputs_digest: str
class BudgetLedger(Protocol):              # workflow/budget_policy.py; ONE owner, carried on the run lease
    def start_phase(self, phase: BudgetPhase, *, clock: Clock) -> None; def end_phase(self, phase: BudgetPhase, *, clock: Clock) -> float
    def record_human_wait(self, seconds: float) -> None
    def view(self, phase: BudgetPhase, *, clock: Clock) -> BudgetView
    def admit(self, phase: BudgetPhase, *, estimate: Callable[[], Tuple[float, str]], gate_reserve: float, clock: Clock) -> BudgetDecision
    def deadline_for(self, phase: BudgetPhase, *, clock: Clock) -> Optional[Deadline]       # what LLMClient / ExecutionPort receive
@dataclass(frozen=True)
class FallbackDecision: attempt: int; role: str; candidates: Tuple[str, ...]; selected: Optional[str]; skipped: Tuple[Tuple[str, str], ...]
                        eligibility_rule: str; timing_estimate: Optional[BudgetDecision]; reason_code: str; inputs_digest: str   # policy unchanged (G)
```

## 13. QUALIFICATION (`kriya/qualification/`)

```python
class QualificationService(Protocol):
    def qualify_model(self, ctx: RequestContext, binding, *, cases: QualificationCasesConfig) -> QualificationRecord   # model_qualification.assess
    def status(self, ctx, binding) -> QualificationStatus; def certify(self, ctx, candidate: ReleaseCandidate) -> CertificationRecord
    def doctor(self, ctx, *, production: bool) -> DoctorReport            # production_doctor probes
```
Consumes PROVIDER (`InferenceRuntimePort`), EXECUTION (`ExecutionPort`), KERNEL (`DeveloperAgent` through `agents` public surface), never `workflow.attempt` internals.

## 14. KNOWLEDGE (`kriya/knowledge/`)

```python
class KnowledgeGuardPort(Protocol): def check_goal(self, ctx: RequestContext, goal: str, *, cutoff: str, policy: str) -> GapReport
class LookupPolicy(Protocol): def allows(self, ctx: RequestContext, url: str) -> AuthorityResult    # consults policy.egress; never widens
class LearnedKnowledgeReader(Protocol): def retrieve(self, ctx, query: str, *, embedding_model: str) -> Tuple[ReferenceChunk, ...]   # memory.learned_knowledge
```

## 15. APPLICATION (`kriya/application/`)

```python
@dataclass(frozen=True)
class ApplicationServices:                 # composed once per process by application.compose(config_service, environment, ...)
    projects: "ProjectService"; runs: "RunService"; approvals: "ApprovalService"; reviews: "ReviewService"
    configuration: "ConfigurationService"; models: "ModelService"; skills: "SkillCatalogService"; tools: "ToolCatalogService"
    evidence: "EvidenceService"; history: "HistoryService"; health: "HealthService"
class ProjectService(Protocol):   def status(ctx, root) -> WorkspaceStatus; def describe(ctx, root) -> WorkspaceDescription; def create(ctx, parent, name) -> CreatedWorkspace; def initialize(ctx, root) -> InitResult
class RunService(Protocol):       def preflight(ctx, root) -> Preflight; def start(ctx, root, goal: str, options: RunOptions) -> RunHandle; def status(ctx, launch_id) -> LaunchStatus; def recover(ctx, root) -> RecoveryAssessment
class ApprovalService(Protocol):  def pending(ctx, launch_id) -> Tuple[PendingReview, ...]; def decide(ctx, decision: ReviewDecisionRequest) -> DecisionResult   # cli launch-decision
class ReviewService(Protocol):    def change_review(ctx, launch_id, review_id) -> ChangeReview; def runtime_command_review(ctx, launch_id, review_id) -> RuntimeCommandReview
class ConfigurationService(Protocol): def approval_status(ctx, root) -> ConfigApprovalStatus; def approve(ctx, root, fingerprint) -> ConfigApprovalResult
class ModelService(Protocol):     def context(ctx, root) -> ExecutionContextView; def probe(ctx, root) -> ModelProbe; def routes(ctx, root) -> RoutingView
class SkillCatalogService(Protocol): def list(ctx, root); def describe(ctx, root, skill_id)
class ToolCatalogService(Protocol):  def list(ctx, root); def describe(ctx, root, tool_id); def approval_state(ctx, root, tool_id)
class EvidenceService(Protocol):  def manifest(ctx, run_id, ...); def verify(ctx, run_id); def records(ctx, run_id, filters); def change_content(...); def call_content(...)
class HistoryService(Protocol):   def snapshot(ctx, root); def list(ctx, snapshot_id, limit, cursor); def detail(ctx, snapshot_id, run_id, include_prompt); def prune(ctx, keep); def verify(ctx, snapshot_id)
class HealthService(Protocol):    def doctor(ctx, *, production: bool) -> DoctorReport; def capabilities(ctx) -> Capabilities
```
Every service method takes `RequestContext` first and returns a frozen result; refusals are typed (`ApplicationRefusal(reason_code)` mapped 1:1 onto today's KUP error vocabulary in `kup/policy.py`).

## 16. CLIENT (`kriya/client/`)

```python
class KriyaClient(Protocol):               # client/contract.py: the semantic boundary a future RemoteKriyaClient implements over a transport
    projects: ProjectService; runs: RunService; approvals: ApprovalService; reviews: ReviewService; configuration: ConfigurationService
    models: ModelService; skills: SkillCatalogService; tools: ToolCatalogService; evidence: EvidenceService; history: HistoryService; health: HealthService
    def context(self, *, workspace_root: Optional[str], request_id: Optional[str] = None) -> RequestContext   # local: RequestContext.local(...)
class LocalKriyaClient(KriyaClient):       # client/local.py: in-process; constructed by application.compose(); the only implementation now
    def __init__(self, services: ApplicationServices, *, principal: PrincipalContext = PrincipalContext.local()) -> None
```
Relation to the desktop: `Electron renderer -> typed IPC (ipc_contract.ts) -> Electron main/host (kriya_service.ts) -> child "kriya kup-stdio" (kup/serve.py) -> run_kup_operation (kup/cli_ops.py: request -> LocalKriyaClient method -> envelope) -> ApplicationServices -> modules`. Run start (`kriya generate --launch-id`) and approvals (`kriya launch-decision`) are CLI entry points of the same `LocalKriyaClient` (`runs.start`, `approvals.decide`). `serve.py` keeps reloading configuration per request (`ConfigService.resolve` inside the service call), so SECURITY_AUTHORITY is never cached (TRACED invariant, serve.py:5-6).
