import logging
import os
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple, Union

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

logger = logging.getLogger(__name__)


class ModelCapabilities(BaseModel):
    """Measured local-model protocol capabilities, never inferred from API shape."""

    native_tool_calls: bool = Field(default=True)
    json_mode: bool = Field(default=True)
    reliable_multiline_json: bool = Field(default=False)
    streaming: bool = Field(default=True)
    max_tool_argument_chars: int = Field(default=8192, ge=256)
    preferred_edit_protocol: str = Field(default="small_native_tools")

class LLMConfig(BaseModel):
    provider: str = Field(default="openai")
    model: str = Field(default="llama3")
    api_key: str = Field(default="local-key")
    base_url: str = Field(default="http://localhost:11434/v1")
    temperature: float = Field(default=0.2)
    max_tokens: int = Field(default=4096)
    # Planner responses are execution metadata, not implementation bodies.
    # Keep their output budget independent from Developer generation, but large
    # enough for dependency-rich authoritative plans without truncation.
    planner_max_tokens: int = Field(default=8192, ge=256)
    extra_body: Dict[str, Any] = Field(default_factory=dict)
    reasoning: bool = Field(default=False)
    context_window: int = Field(default=32768)
    knowledge_cutoff: str = Field(default="2023-12-01")
    knowledge_cutoff_confidence: str = Field(default="estimated")
    # Applied ONLY to Developer generation calls that are directly responding to a
    # real prior Quality Gate failure (the same scope as prior_error_context's
    # fix-analysis instruction) - None (default) means no override, unchanged
    # behavior. A real, cited finding motivated this as opt-in rather than
    # lowering `temperature` globally: code-gen success rate measured dropping
    # ~25.7% going from 0.0->0.2 temperature for only a ~9.6% diversity gain
    # (AAAI-38 adaptive-temperature-sampling study) - the opposite of "add
    # randomness to shake a stuck retry loose." Deliberately NOT changed as the
    # global default: `temperature` defaults to 0.7 in default_config.yaml with
    # its own documented rationale (avoiding MoE repetition loops on longer,
    # from-scratch attempt-1 generations) that this finding doesn't touch or
    # contradict - a retry's typically-shorter, narrower regeneration is a
    # different case, not evidence the global default should change too.
    retry_temperature: Optional[float] = Field(default=None)
    # Applied ONLY to ReviewerAgent.run() calls (kriya/workflow/workflow.py, both the
    # pre-approval and final Review stages) - None (default) means no override, the
    # Reviewer inherits whatever `temperature` the rest of the run uses, unchanged
    # behavior. Added after a live, root-caused incident (2026-08-18, eval harness
    # batch b-10t, django_healthcheck_gap): a Reviewer call at temperature=0.2 (the
    # eval harness's own hardcoded eval-determinism override, not this field) entered
    # a verbatim degenerate repetition loop - one genuine review, then the identical
    # "### Code Review" / "### Merge Readiness" block repeated 250 times until it hit
    # the full max_tokens ceiling (639s wall-clock for what should have been a few
    # hundred tokens). Confirmed via Ollama's own server.log: a real, steady ~26 tok/s
    # generation the whole time, not a hang. This is the exact MoE-repetition-loop
    # failure class `temperature: 0.7`'s own default_config.yaml comment already
    # documents and defends against for the Developer's generation calls - the
    # Reviewer stage had no equivalent protection. A dedicated field (mirroring
    # retry_temperature's shape) rather than routing through agent_llms.reviewer.llm,
    # which would silently require re-specifying model/base_url/max_tokens/etc. too
    # just to change one sampling parameter.
    reviewer_temperature: Optional[float] = Field(default=None)
    capabilities: ModelCapabilities = Field(default_factory=ModelCapabilities)

class PluginsConfig(BaseModel):
    directory: str = Field(default="./plugins")
    enabled: List[str] = Field(default_factory=list)

class PathsConfig(BaseModel):
    skills: str = Field(default="./skills")
    memory: str = Field(default="./memory")
    logs: str = Field(default="./logs")


class SkillsConfig(BaseModel):
    load_global: bool = Field(default=True)
    load_cwd: bool = Field(default=True)

class LoggingConfig(BaseModel):
    """Log locations are owned by kriya/core/logging_setup.py: the directory is
    KRIYA_LOG_DIR > `directory` (absolute; canonicalized once at load) >
    ~/.kriya/logs, never the process CWD. `file` is deprecated and ignored
    (kept so existing configs load and its SEC-009 classification holds)."""
    level: str = Field(default="INFO")
    directory: Optional[str] = Field(default=None)
    file_enabled: bool = Field(default=True)
    run_file_enabled: bool = Field(default=True)
    file: Optional[str] = Field(default=None)

class MCPCapabilityConfig(BaseModel):
    """TOOL-003 P1 (2026-09-13): the operator-controlled MAXIMUM
    filesystem/network authority a given `mcp.<server>` may ever be
    declared to possess. Defines and governs capability authority only -
    does NOT yet enforce it at the OS/container boundary (that is TOOL-003
    P2, real OCI containment). See kriya/mcp/capability.py's module
    docstring for the full resolved-profile/canonicalization/digest
    machinery this config resolves into, and CLAUDE.md's "MCP capability
    authority" section for the overall mechanism.

    SAFE DEFAULT (every field defaults to the most restrictive value -
    "declares nothing"): with no containment backend wired to this profile
    yet, no default can make an untrusted MCP process ACTUALLY safer today
    - the only meaningful thing a default can do is avoid pre-declaring
    broad authority that a future enforcement pass would then either have
    to honor (defeating the point of finally landing containment) or
    silently downgrade (a false sense of security in the interim). So the
    default declares NOTHING (no filesystem/network authority at all) -
    every existing `mcp.<server>` block with no `capabilities` section
    keeps running exactly as it does today (nothing is enforced yet - see
    the P1 security statement), but the day TOOL-003 P2 wires this profile
    into real containment, an operator who never explicitly widened a
    server's declared authority will find it starts genuinely restricted,
    not grandfathered into broad implicit trust that would then need to be
    manually locked back down.

    `extra="forbid"` (unlike MCPServerConfig itself): an unrecognized
    capability field must be a hard pydantic ValidationError, not silently
    dropped - SEC-009 already denies-without-approval any `mcp.*` field by
    virtue of the whole `mcp.<server>` block being SECURITY_AUTHORITY
    (kriya/config/authority.py's `classify_field()`), but a silently-
    ignored unknown field would still let a future typo'd/renamed field
    pass validation while doing nothing, which is a worse failure mode
    than refusing to load the config at all.

    PROCESS is deliberately NOT a configurable field here at all (Task 8):
    SEC-004's process-group isolation (`start_new_session=True`) is
    LIFECYCLE ownership only (guaranteed cleanup on shutdown/timeout), not
    an execution-capability restriction - it does not, and was never
    claimed to, restrict what a live MCP server's process tree can do
    while running. Adding a boolean like `allow_process_spawn` here would
    be a decorative, non-enforceable policy field (nothing currently
    reads or enforces it) that could mislead a future reader into thinking
    process capability is already governed - it is not, until real OCI
    containment (TOOL-003 P2) exists. See
    kriya/mcp/capability.py::PROCESS_AUTHORITY_STATEMENT for the fixed,
    honest constant every resolved profile carries instead.

    NETWORK reuses `kriya.tools.containment.NetworkAuthority`'s exact
    DENIED/UNRESTRICTED semantics by value-string convention (see
    kriya/mcp/capability.py::MCPNetworkAuthority for why this is a
    dedicated MCP-local enum rather than an added member of the shared
    ContainmentProfile-facing enum - reusing that shared enum's own type
    was evaluated and rejected: kriya/tools/containment_oci.py's
    `OCIContainmentBackend.prepare()` has no exhaustive-match ValueError
    guard for `ContainmentProfile.network` - an unrecognized member
    silently falls through to full UNRESTRICTED networking, so adding a
    third member to that SHARED, ALREADY-IN-PRODUCTION enum risks a live
    SEC-006 regression in the completely unrelated target-code sandboxing
    path if any future code ever constructed a ContainmentProfile with the
    new member by mistake - not a risk worth taking for a value that MCP
    capability profiles never feed into ContainmentBackend in this
    package anyway)."""

    model_config = ConfigDict(extra="forbid")

    workspace_read: bool = Field(default=False)
    workspace_write: bool = Field(default=False)
    temp_read_write: bool = Field(default=False)
    dependency_cache_read: bool = Field(default=False)
    # Resolved to absolute, escape-checked real paths by
    # resolve_config_state() BEFORE SEC-009 authority resolution ever
    # inspects this config (see that function's own "MCP capability path
    # resolution" block) - by the time AppConfig validates this model, and
    # by the time kriya/mcp/capability.py's resolve_mcp_capability_profile()
    # ever sees these values, they are always already-safe absolute paths,
    # never raw operator-supplied relative strings. A relative string that
    # resolves outside the anchor root is rejected at that same resolution
    # point (ValueError), never silently accepted here downstream.
    additional_read_paths: List[str] = Field(default_factory=list)
    additional_write_paths: List[str] = Field(default_factory=list)
    network: str = Field(default="denied")
    # Only meaningful (and required non-empty) when network=="explicit_destinations" -
    # see kriya/mcp/capability.py::resolve_mcp_capability_profile()'s own check.
    network_hosts: List[str] = Field(default_factory=list)

    @field_validator("network")
    @classmethod
    def _network_must_be_recognized(cls, v: str) -> str:
        if v not in ("denied", "explicit_destinations", "unrestricted"):
            raise ValueError(
                f"mcp.<server>.capabilities.network must be one of "
                f"'denied'/'explicit_destinations'/'unrestricted', got {v!r}"
            )
        return v

class MCPServerConfig(BaseModel):
    command: str
    args: List[str] = Field(default_factory=list)
    env: Dict[str, str] = Field(default_factory=dict)
    capabilities: MCPCapabilityConfig = Field(default_factory=MCPCapabilityConfig)

class MCPLifecycleConfig(BaseModel):
    """SEC-004 (2026-09-13): bounded MCP subprocess lifecycle/resource
    controls, applied uniformly to every configured `mcp.<server>` -
    deliberately ONE coherent section rather than per-server overrides
    or scattered module-level constants (this risk's own explicit
    instruction: "do not introduce unnecessary tuning knobs if two
    bounds can share one clearly defined setting"). Every field here is
    SECURITY_AUTHORITY under SEC-009 (kriya/config/authority.py) -
    weakening any bound (a longer timeout, a bigger stdout limit, a
    higher resource ceiling) is a security-relevant capability change, so
    a repository can never set any of these without explicit SEC-009
    approval; only the packaged default (this file) is trusted.

    `startup_timeout_seconds` bounds the ENTIRE launch sequence (process
    spawn + initialize request + initialize response/handshake
    completion) - a child that starts but never completes the handshake
    cannot hang Kriya past this bound.

    `request_timeout_seconds` bounds every individual protocol request
    made through `_send_request()` after startup (tools/list, tools/call,
    any future method) - independent of the startup bound, since a
    server can complete its handshake and then hang on a specific call.

    `shutdown_grace_seconds` is how long `stop()` waits for a SIGTERM'd
    process tree to exit cooperatively before escalating to SIGKILL.
    `force_kill_reap_seconds` is the separate, additional bound on
    reaping the tree AFTER the forced kill is sent - kept distinct from
    the grace period because a killed process's exit is normally
    near-instant, so a much shorter bound is appropriate there than the
    grace period given to a cooperative shutdown attempt.

    `max_stdout_line_bytes` is the hard ceiling on a single stdout
    protocol line/frame (enforced via asyncio's own StreamReader `limit`,
    which raises deterministically rather than allocating unboundedly -
    see kriya/mcp/lifecycle.py). Measured empirically against Kriya's own
    shipped MCP server's real `tools/list` response (1282 bytes) - the
    default here is deliberately generous headroom over that, sized for
    a legitimately tool-rich third-party server, not the smallest value
    that happens to work today.

    `max_stderr_buffer_bytes` bounds the ROLLING (not cumulative) stderr
    buffer kept for diagnostics - a flooding server's stderr is
    continuously drained (never blocking the child's own write()) but
    only the most recent bytes up to this bound are retained.

    `cpu_seconds`/`memory_mb` reuse `kriya/tools/sandbox.py::
    posix_resource_limits_preexec_fn()` (SEC-001) exactly as-is - the
    same primitive already used for target-code sandbox execution.
    IMPORTANT ASYMMETRY, stated honestly rather than flattened: `memory_mb`
    (RLIMIT_AS) is a true ceiling, deterministic/fail-closed on Linux,
    advisory-only on macOS (an already-accepted SEC-001 platform
    limitation, not new here). `cpu_seconds` (RLIMIT_CPU) is a CUMULATIVE
    LIFETIME BUDGET, not a rate limit - appropriate for a finite
    compile/test job, but an MCP server is a long-lived daemon for the
    life of a Kriya session, so this is deliberately generous (a
    legitimately busy server should not be SIGKILL'd mid-session for
    having done a lot of honest work). It still bounds a genuinely
    CPU-spinning/pathological server, which is the property SEC-004
    requires - true CPU RATE limiting (cgroups CPU shares/quota) would
    need real container/cgroup containment and belongs to the
    not-yet-registered MCP invocation/execution authority work (NOT the
    register's existing SEC-005 row, which is an unrelated
    package-installation/network-access broker risk), not attempted
    here (see docs/assurance/KRIYA_PRODUCTION_RISK_REGISTER.md's SEC-004
    entry for the full native-vs-OCI decision record)."""

    startup_timeout_seconds: int = Field(default=30, ge=1)
    request_timeout_seconds: int = Field(default=60, ge=1)
    shutdown_grace_seconds: int = Field(default=5, ge=0)
    force_kill_reap_seconds: int = Field(default=5, ge=0)
    max_stdout_line_bytes: int = Field(default=4_000_000, ge=1024)
    max_stderr_buffer_bytes: int = Field(default=1_000_000, ge=1024)
    cpu_seconds: Optional[int] = Field(default=3600, ge=1)
    memory_mb: Optional[int] = Field(default=2048, ge=1)
    # TOOL-003 P2 (2026-09-13): a containerized MCP server's teardown has a
    # SECOND lifecycle layer beyond the process-group kill terminate_mcp_process()
    # already bounds - the host `docker rm -f <container>` authoritative
    # cleanup (PreparedContainment.cleanup's own docstring: a VM-mediated
    # container runtime like Docker Desktop does not reliably stop on a
    # host-side process-group SIGKILL alone). Bounds THAT separate call,
    # invoked via run_in_executor (never blocking the event loop) after
    # terminate_mcp_process() already ran - additive to, not a replacement
    # for, shutdown_grace_seconds/force_kill_reap_seconds above. Only
    # meaningful when mcp_contained_execution_required is True; ignored
    # entirely for host-side (uncontained) MCP servers.
    container_cleanup_timeout_seconds: int = Field(default=15, ge=1)

class EmbeddingConfig(BaseModel):
    model: str = Field(default="nomic-embed-text:latest")
    base_url: str = Field(default="http://localhost:11434/v1")

class AutonomyConfig(BaseModel):
    mode: str = Field(default="human-in-the-loop")
    egress_policy: str = Field(default="local_only")
    sensitive_paths: List[str] = Field(default_factory=lambda: [
        r".*\.env$", r".*secrets.*", r"\.github/workflows/.*", r"Jenkinsfile",
        r".*credentials.*", r".*password.*"
    ])
    risk_threshold_lines: int = Field(default=100)
    sandbox_execution: bool = Field(default=True)
    sandbox_env_allowlist: List[str] = Field(default_factory=lambda: [
        "HOME", "LANG", "LC_ALL", "USER", "SHELL", "TMPDIR", "TEMP", "TMP",
        "JAVA_HOME", "M2_HOME", "GRADLE_HOME", "VIRTUAL_ENV", "PYTHONPATH"
    ])
    sandbox_cpu_seconds: int = Field(default=240)
    sandbox_memory_mb: int = Field(default=4096)
    # SEC-007 (2026-09-12): dependency/build-tool ACQUISITION resource
    # authority, deliberately separate from sandbox_cpu_seconds/
    # sandbox_memory_mb above - those two bound TARGET/generated code
    # execution and are meant to be tightened aggressively for a
    # suspected-hostile application; acquisition (Maven/pip resolving a
    # real, possibly large dependency/plugin tree with real network
    # access) is trusted-purpose tooling with different, usually larger,
    # resource needs. Confirmed live, 2026-09-11/12: a fixture's own
    # sandbox_memory_mb=128 (chosen to make an application's OWN
    # resource-abuse probe meaningful) OOM-killed Maven's acquisition-
    # phase JVM (real returncode 137) resolving a legitimately large
    # transitive plugin tree - lowering the target cap to test hostile
    # code should never also break legitimate build tooling. Defaults
    # generous enough for ordinary Maven/pip dependency resolution
    # without being unbounded.
    acquisition_cpu_seconds: int = Field(default=300)
    acquisition_memory_mb: int = Field(default=2048)
    # SEC-006 (2026-09-12): the ONLY source of destination authority for
    # NetworkAuthority.DEPENDENCY_REGISTRY_ONLY acquisition - a hostile
    # repository's pom.xml/requirements.txt/settings.xml/pip.conf can never
    # enlarge this set (Invariant: repository content cannot enlarge
    # authority). Exact hostnames only, empirically the minimum needed for
    # real Maven Central + PyPI acquisition (verified live, 2026-09-12):
    # `repo.maven.apache.org` (Maven Central itself), `pypi.org` (the PyPI
    # index), `files.pythonhosted.org` (the Fastly CDN PyPI actually serves
    # wheels from - NOT reachable via pypi.org's own host). Deliberately NO
    # organizational wildcard (`.apache.org`/`.python.org`) even though one
    # would be more convenient - the packaged default must contain only
    # what was empirically required, per this risk's own explicit
    # instruction. A private/internal registry needs an explicit additional
    # entry here; it is never inferred from repository content.
    acquisition_registry_hosts: List[str] = Field(
        default_factory=lambda: ["repo.maven.apache.org", "pypi.org", "files.pythonhosted.org"],
        # validate_default: without this, pydantic v2 does not run
        # field_validator over a DEFAULT value at all (only over an
        # explicitly-supplied one) - the packaged default would then stay
        # in its literal declaration order while any explicit config value
        # gets canonicalized (sorted/deduped/lowercased), a real
        # inconsistency for a field whose canonical form doubles as the
        # authority-identity hash input (kriya/tools/containment_oci.py's
        # compute_authority_id).
        validate_default=True,
    )

    @field_validator("acquisition_registry_hosts")
    @classmethod
    def _validate_acquisition_registry_hosts(cls, hosts: List[str]) -> List[str]:
        """Normalizes to a canonical form (lowercase, no trailing dot,
        deduped, sorted) - this exact canonical form is also the input to
        the per-run authority-identity hash (kriya/tools/containment_oci.py),
        so two configs naming the same hosts in a different order/case
        always produce the same authority identity. Rejects anything that
        isn't a bare exact hostname: no scheme/path (a URL smuggling a
        different real destination than it appears to), no port, no
        wildcard/leading-dot convenience entries (this risk's own explicit
        instruction - a wildcard is a materially larger authority grant
        than the exact host it looks like), no empty/whitespace entries."""
        normalized = []
        for raw in hosts:
            host = raw.strip().lower()
            if not host:
                raise ValueError("autonomy.acquisition_registry_hosts: empty hostname entry is not allowed.")
            if "://" in host or "/" in host:
                raise ValueError(
                    f"autonomy.acquisition_registry_hosts: {raw!r} looks like a URL, not a bare "
                    "hostname - registry authority must be an exact hostname (e.g. 'pypi.org'), "
                    "never a URL/path."
                )
            if ":" in host:
                raise ValueError(
                    f"autonomy.acquisition_registry_hosts: {raw!r} includes a port - registry "
                    "authority must be a bare hostname with no port."
                )
            if host.startswith(".") or host.startswith("*"):
                raise ValueError(
                    f"autonomy.acquisition_registry_hosts: {raw!r} is a wildcard/organizational "
                    "entry - this risk's own instruction forbids convenience wildcards (e.g. "
                    "'.apache.org'). List each exact registry hostname explicitly."
                )
            host = host.rstrip(".")
            if not host:
                raise ValueError("autonomy.acquisition_registry_hosts: empty hostname entry is not allowed.")
            normalized.append(host)
        return sorted(set(normalized))
    # SEC-001 foundation (2026-09-11): which ContainmentBackend
    # (kriya/tools/containment.py) ProcessController composes for a
    # profile that requires one. "none" (NullContainmentBackend) is the
    # ONLY packaged value today - it reproduces the exact env-allowlist +
    # best-effort-rlimit behavior sandbox_execution already provided
    # before this field existed, so this default changes nothing about
    # current behavior. No production (OCI/sandbox-exec) backend name is
    # registered yet - see docs/architecture/SEC001_HOSTILE_CODE_CONTAINMENT_DESIGN.md;
    # an unrecognized value fails closed (BackendUnavailableError), never
    # silently falls back to uncontained execution.
    containment_backend: str = Field(default="none")
    # SEC-001-P6 (2026-09-11): opt-in gate for routing PolymorphicValidator's
    # compile/test commands and service_runtime's application-under-test
    # process through a real ContainmentProfile (network=DENIED, real
    # filesystem/process isolation via `containment_backend`) instead of
    # today's env-allowlist/rlimit-only behavior. Default False preserves
    # "existing behavior must remain compatible when containment is not
    # required by the current execution profile" exactly - flipping this to
    # True with containment_backend still "none" fails CLOSED (Null backend
    # now refuses any non-UNRESTRICTED-network profile), by design: this
    # flag means "these paths must be really contained", not merely "try
    # to". Versioned toolchain selection and image attestation are owned by
    # PolymorphicValidator/OCIContainmentBackend (PRD-011).
    contained_execution_required: bool = Field(default=False)
    # TOOL-003 P2 (2026-09-13): the MCP analogue of contained_execution_required
    # immediately above, deliberately a SEPARATE flag rather than reusing that
    # one - contained_execution_required governs target-code compile/test/
    # managed-service sandboxing (PolymorphicValidator/service_runtime), a
    # different execution surface with a different risk profile and a
    # different operator who might reasonably want one contained without the
    # other. Default False preserves 100% of today's MCP behavior (host-side
    # execution, unchanged) for every existing deployment and the entire
    # existing MCP test corpus - this is a decision made explicitly with the
    # user (2026-09-13), not a default flip: "always require containment for
    # every MCP server" was considered and rejected as this pass's default
    # because it would make Docker a hard MCP dependency and require rewriting
    # ~40 existing tests that spawn real MCP subprocesses via sys.executable
    # with no Docker involved - a strict superset relationship holds instead
    # (flipping this to True later is a one-line config change, not a
    # rearchitecture). Same fail-closed semantics as contained_execution_required:
    # flipping this to True with containment_backend still "none" (or Docker
    # simply unavailable) means every MCP server refuses to start - "no raw-host
    # fallback" is the explicit, non-negotiable requirement (2026-09-13 user
    # instruction) - never a silent downgrade to host-side execution just
    # because containment could not be established. When False, MCP still gets
    # TOOL-002 invocation authorization and a resolved/audited MCPCapabilityProfile
    # (TOOL-003 P1) - only the OS/container enforcement boundary is absent, and
    # every real MCP connection's own telemetry records this fact explicitly
    # (kriya/mcp/mcp.py's own "containment_required"/"containment_active"
    # fields) - a host-side run must never be misread as containment evidence.
    mcp_contained_execution_required: bool = Field(default=False)
    # CORR-018 (2026-09-13): opt-in strict semantic-region enforcement for
    # ordinary generate/fix Java brownfield mutations - the general-case
    # analogue of contained_execution_required/mcp_contained_execution_
    # required immediately above, same shape, same reasoning. Default False
    # preserves 100% of today's ordinary generate/fix behavior: neither
    # CORR-016's own grounding_goal-regex derivation nor a deterministic
    # repository relationship (e.g. interface -> implementer) can name
    # which member a natural bug-fix-shaped goal ("fix the NPE when X is
    # null") needs to touch - that is discovered during generation, not
    # statable up front - so making this unconditional would fail closed
    # on the overwhelming majority of real brownfield usage (a deliberate,
    # explicit decision with the user, 2026-09-13; see kriya/workflow/
    # semantic_scope_derivation.py's own module docstring). When True,
    # every mutation to an EXISTING Java source file must be covered by
    # requirement-grounded semantic-region authority (explicit grounding_
    # goal derivation or deterministic repository-grounded necessity) or
    # the candidate is rejected before write - never a silent downgrade
    # to file-level-only protection. Flipping this to True does not touch
    # A3's own existing authorized_semantic_regions caller (proposal_
    # promotion.py already supplies its own explicit region list, which
    # this flag never overrides or narrows).
    semantic_region_enforcement_required: bool = Field(default=False)
    # ShellTool previously had no wall-clock timeout at all (SEC-001
    # execution-surface inventory finding, 2026-09-11) - every other real
    # command primitive in this codebase (ProcessController.run(), used by
    # PolymorphicValidator/service_runtime) always has one.
    shell_command_timeout_seconds: int = Field(default=300)
    # VAL-001 brownfield validation baselining (2026-09-18, kriya/workflow/
    # validation_baseline.py): both fields default to a complete no-op for
    # every existing caller - zero new subprocess invocation, zero behavior
    # change - deliberately, per this same precedent's own documented
    # run_verification_enabled blast-radius lesson a few lines below (~110
    # explicit test opt-outs needed for THAT default-True flip). Brownfield
    # baselining is opt-in per-campaign, never an unconditional new default.
    #
    # brownfield_baseline_target_test: an explicit PolymorphicValidator.
    # run_tests(target_test=...) value naming the "relevant/targeted" test
    # scope for a brownfield PRE/POST baseline comparison - a single string
    # (one target) or an ORDERED LIST of strings (VAL-001 G1-R3: several
    # specific targets at once, e.g. VAL-001 G1's own two C# test files,
    # represented STRUCTURALLY as a YAML list - never as one shell-joined
    # string; kriya/tools/validate.py's own PolymorphicValidator.run_tests()
    # passes each list entry as its own separate argv entry, no shell
    # involved anywhere in this path). None (default) means no targeted
    # baseline is captured at all - this is deliberately NOT auto-derived
    # from architect_files (that would require inventing a new affected-
    # test-discovery heuristic, explicitly out of scope for this package -
    # "do not create a parallel validation framework").
    brownfield_baseline_target_test: Optional[Union[str, List[str]]] = Field(default=None)
    # brownfield_full_regression_baseline_policy: "auto" | "required" |
    # "disabled". "required": capture a pristine full-suite PRE baseline
    # once (before the first Developer call) and delta-compare the final
    # candidate's own full-regression run against it - NEW_FAILURE/
    # CHANGED_FAILURE block, PRE_EXISTING_FAILURE does not. "disabled":
    # today's exact unmodified behavior (the full suite still runs
    # post-approval as it always has; no PRE baseline, no delta - any
    # failure blocks, matching current behavior byte-for-byte). "auto":
    # reserved for a future risk-based trigger ("follow existing risk/
    # validation policy") - currently behaves identically to "disabled"
    # (no such existing policy signal exists yet to hook into) - stated
    # honestly here rather than silently activating baselining as a new
    # default behavior no compatibility analysis has covered.
    brownfield_full_regression_baseline_policy: str = Field(default="auto")

    @field_validator("brownfield_full_regression_baseline_policy")
    @classmethod
    def _brownfield_policy_must_be_known_value(cls, v: str) -> str:
        if v not in ("auto", "required", "disabled"):
            raise ValueError(
                "autonomy.brownfield_full_regression_baseline_policy must be "
                f"'auto', 'required', or 'disabled', got {v!r}"
            )
        return v
    run_verification_enabled: bool = Field(default=True)
    run_verification_timeout_seconds: int = Field(default=90)
    # Gates on SpecComplianceAgent (kriya/agents/agent.py): unlike compile/test/
    # run-verification, checks whether the goal's LITERALLY named requirements
    # (an exact field/method/class name, exact type, exact constant) actually
    # appear in the generated code - closes a gap compile/test/LSP grounding
    # structurally can't (syntactically valid, semantically non-compliant code
    # passes every other gate). Runs once, only after every other gate already
    # passed. Default False (opt-in), unlike run_verification_enabled's
    # default True - this is a genuinely NEW unconditional agent call, and
    # run_verification_enabled's own introduction required ~110 explicit
    # `cfg.autonomy.run_verification_enabled = False` opt-outs across
    # tests/test_workflow.py just to keep its shared llm.complete mock
    # side_effect sequencing intact; repeating that blast radius for a new,
    # separately-optional gate isn't warranted. Same "new capability, off
    # until proven" default already used for web_lookup_enabled and
    # self_correction_loop_enabled above.
    spec_compliance_enabled: bool = Field(default=False)
    max_consecutive_no_progress_attempts: int = Field(default=2, ge=1)
    web_lookup_enabled: bool = Field(default=False)
    # A live-lookup query's CONTENT is already hard-restricted (bare technology-name
    # strings only, enforced in code, never goal/design/code/error text) - this is a
    # separate, additional gate on WHEN it's allowed to fire at all. False means every
    # outbound query needs real-time confirmation (showing the exact terms and target
    # URL) before it leaves the machine; a non-interactive (-y) run with this still
    # False simply never sends the query, rather than the pre-existing behavior of
    # firing it anyway and silently discarding the result. Set True only if you've
    # accepted unattended outbound search as part of your threat model.
    web_lookup_auto_approve: bool = Field(default=False)
    # Off by default for the same reason as web_lookup_enabled above: this activates
    # a genuinely new capability (the Developer's own model gets a bounded native
    # tool-calling loop against the sandbox worktree on a compile OR run-verification
    # failure, before falling back to today's full-regeneration retry) rather than
    # tuning an existing one. Native tool-calling is confirmed reliable only for
    # SMALL tool-call arguments on local models (spikes/tool_call_developer/README.md)
    # - the loop's toolset (kriya/workflow/self_correction.py) is deliberately
    # restricted to small-argument-only actions (including, since 2026-08-22, 4
    # read-only "ground truth" lookups - a project's real declared dependencies, an
    # external dependency's real public API via javap against the resolved
    # classpath, a Maven Central coordinate lookup, and a real compiled-output
    # listing - closing the gap where a fix needs grounding in something that isn't
    # any one file's content), never full file content and never a new file, so this
    # stays a narrow, additive recovery path, not a parallel generation architecture.
    self_correction_loop_enabled: bool = Field(default=False)
    self_correction_loop_max_turns: int = Field(default=4)
    # DEV-INV-001 (2026-09-19): off by default for the same reason as
    # self_correction_loop_enabled above - a genuinely new capability (the
    # Developer may request bounded, read-only repository evidence -
    # inspect_member/find_symbol/find_callers/search_code, kriya/workflow/
    # investigation.py - mid-attempt, BEFORE proposing any code), not a
    # tuning knob on an existing one. Strictly read-only: every path-backed
    # result passes through kriya/policy/filesystem.py::AuthorizedFileReader
    # (workspace containment + sensitive-path denial) before being shown to
    # the model, and D1's own `_completeness_gated_operation` (kriya/
    # workflow/attempt.py) is completely unchanged - investigation evidence
    # can only ever help authorize a MORE precise patch, never a whole-file
    # replacement it wouldn't otherwise be authorized for.
    developer_investigation_enabled: bool = Field(default=False)
    # Per-attempt budget (shared across every _run_developer_generation call
    # within the SAME attempt_number, including coordinated-repair's several
    # per-participant calls - see kriya/workflow/attempt.py's own
    # investigation_turns_used_by_attempt accounting), not per-call: a
    # coordinated repair with several participants must not get this many
    # turns EACH.
    #
    # Raised from 4 to 10 (2026-09-19, VAL-001 G1 follow-up): safe to raise
    # now that run_investigation_loop() stops itself the moment mutation-
    # readiness is achieved (see that function's own EVIDENCE-DRIVEN
    # PROGRESSION docstring) rather than only on the model's own say-so or
    # this count - this ceiling is now a genuine safety valve for a
    # pathological loop, not the primary stopping mechanism, so a higher
    # default costs nothing in the common (readiness-reached-early) case.
    developer_investigation_max_turns: int = Field(default=10, ge=0)
    # Default 1 = today's exact behavior (a single first attempt, unchanged). A value
    # above 1 tries that many INDEPENDENT full-set candidates for the very first
    # generation attempt only (never on later retries, which already have real error
    # grounding to react to) before falling into the normal retry loop - see
    # kriya/workflow/best_of_n.py. Deliberately sequential, never parallel: a goal
    # binding real fixed resources (an embedded broker's port, an Ignite node's
    # discovery/comm ports) would have two candidates' generated apps port-conflict
    # under real parallel execution, and local model serving typically doesn't
    # meaningfully parallelize multiple requests against one loaded model anyway - so
    # peak resource usage at any moment stays identical to a normal single-attempt
    # run; the only cost is added wall-clock in the bounded worst case. Only takes
    # effect when a real isolated worktree sandbox exists (see best_of_n.py's own
    # guard) - never risks writing a discarded candidate's files into the real project.
    best_of_n_first_attempt: int = Field(default=1)
    # Optional end-to-end generation deadline. None preserves unbounded normal
    # CLI behavior; eval/demo harnesses with an outer timeout should set this to
    # the same or a slightly smaller value so Kriya can stop cleanly instead of
    # starting a model pass the harness will kill mid-generation.
    generation_time_budget_seconds: Optional[int] = Field(default=None, ge=1)
    generation_gate_reserve_seconds: int = Field(default=120, ge=0)
    generation_seconds_per_file_estimate: int = Field(default=90, ge=1)
    # Closes a real, confirmed-in-code gap (2026-08-23): index_repository() -
    # the only thing that ever populates dependency_graph.db's files/symbols
    # tables and vector_index.db's code embeddings - is called EXCLUSIVELY
    # from the `kriya analyze` CLI command, never from run_generation_workflow()
    # itself. A repo never explicitly `kriya analyze`-d therefore has an
    # empty persisted graph for the entire life of every `generate`/`fix`
    # call against it - already known to silently degrade the duplicate-type
    # and cross-package-mismatch Quality Gates to their in-memory-only
    # fallback (see docs/design.md §7.45's follow-up), and, for a genuinely
    # pre-existing repo Kriya never wrote itself, there's no established_files-
    # style fallback covering that gap at all. When True, run_generation_workflow()
    # checks once, before state.generation_started_monotonic starts (i.e. this
    # cost is structurally excluded from generation_time_budget_seconds, not
    # counted against it) whether dependency_graph.db has any indexed files
    # for this workspace; if not, it runs a real, one-time index_repository()
    # pass (never with changed=True - that flag scopes to files `git diff`
    # reports as modified/staged/untracked, which would silently index NOTHING
    # for a fully-committed pre-existing repo, exactly the case this exists to
    # cover) before proceeding. A repeat call against an already-indexed
    # workspace (milestone 2+ in a sequence, or a second `fix` call) is a
    # cheap no-op row check, not a re-index - the same file-level mtime cache
    # index_repository() already has makes this safe to leave enabled across
    # a whole milestone sequence. Any failure (embedding endpoint down, model
    # not pulled) is caught and logged as a warning - generation proceeds
    # exactly as it does today with an empty graph, never blocked by this.
    # Defaults False here (same "new capability, off until proven" rollout
    # already used for spec_compliance_enabled above - this is the first time
    # `generate`/`fix` would trigger real, uncontrolled embedding-endpoint
    # traffic implicitly rather than only on an explicit `kriya analyze`) -
    # candidate to flip True in default_config.yaml once live-validated, same
    # two-step rollout spec_compliance_enabled already went through.
    auto_index_missing_dependency_graph: bool = Field(default=False)

class SearchConfig(BaseModel):
    # Empty by default - live lookup stays fully inert unless a project explicitly
    # points this at a search backend (e.g. a self-hosted SearXNG instance) AND sets
    # autonomy.web_lookup_enabled: true. Two separate switches on purpose - flipping
    # one alone does nothing, so a config merge/copy-paste can't silently enable
    # outbound search.
    base_url: str = Field(default="")
    # How many candidate results to fetch and try per term before giving up on it.
    # A single top-ranked result for a well-known library is often a marketing/landing
    # page with nothing concrete to extract (confirmed via real testing against a real
    # search backend) - trying several in order meaningfully improves the odds of
    # actually finding something usable, which is the whole point of the feature.
    top_k: int = Field(default=3)
    # Extra identifiers the project owner explicitly declares public. Unknown
    # terms never receive unattended auto-approval.
    public_terms: List[str] = Field(default_factory=list)

class FallbackModelConfig(BaseModel):
    model: str
    base_url: str = Field(default="http://localhost:11434/v1")
    api_key: str = Field(default="local-key")
    temperature: float = Field(default=0.2)
    max_tokens: int = Field(default=4096)
    capabilities: ModelCapabilities = Field(default_factory=ModelCapabilities)
    reasoning: bool = Field(default=False)
    # Mirrors LLMConfig.extra_body (below) - a fallback model can need request
    # shape the primary model doesn't (e.g. qwen3.8:27b's reasoning_effort
    # string, distinct from the `reasoning` bool above, which only gates this
    # client's own <think>-stripping/token-floor logic). Before this field
    # existed, every call site that escalates to a fallback model (call_with_
    # escalation, attribution's triage tier, self_correction's tool loop, the
    # Developer retry loop's targeted/fallback-targeted/full-set paths, lesson
    # extraction) still unconditionally used the PRIMARY model's own extra_body
    # regardless of which model was actually being called - harmless when the
    # fallback ignores unknown fields, but silently wrong whenever it doesn't.
    extra_body: Dict[str, Any] = Field(default_factory=dict)
    context_window: int = Field(default=32768)
    knowledge_cutoff: str = Field(default="2023-12-01")
    knowledge_cutoff_confidence: str = Field(default="estimated")

class AgentModelConfig(BaseModel):
    # llm=None means "use the top-level llm config" (today's single-model behavior) -
    # every field here is opt-in, so a project that never touches agent_llms sees zero
    # behavior change. llm_chain is this role's OWN escalation list, tried in order
    # after llm (or the top-level llm if unset) fails - independent of Developer's
    # quality-gate-driven retry loop, which is untouched by this and keeps using the
    # top-level llm/llm_chain exactly as before.
    llm: Optional[LLMConfig] = Field(default=None)
    llm_chain: List[FallbackModelConfig] = Field(default_factory=list)

class AgentRolesConfig(BaseModel):
    # Developer deliberately has no entry here - it stays on the top-level llm/
    # llm_chain, escalated by the existing quality-gate retry loop (a fundamentally
    # different failure signal - "the generated code didn't compile/pass tests" -
    # than the call-level failures the roles below escalate on).
    planner: AgentModelConfig = Field(default_factory=AgentModelConfig)
    architect: AgentModelConfig = Field(default_factory=AgentModelConfig)
    reviewer: AgentModelConfig = Field(default_factory=AgentModelConfig)
    run_verifier: AgentModelConfig = Field(default_factory=AgentModelConfig)
    skill_gap: AgentModelConfig = Field(default_factory=AgentModelConfig)
    spec_compliance: AgentModelConfig = Field(default_factory=AgentModelConfig)

class RoutingConfig(BaseModel):
    # On by default (since 2026-08-02) - a first-time kriya repl user typing a
    # plain-English request instead of an exact command was the single biggest
    # first-contact UX gap found in this platform's validation pass, and this
    # feature's own real-world validation (95.6% effective accuracy on a
    # 136-case held-out test set, ask-when-uncertain fallback for anything
    # genuinely ambiguous) was already strong enough to trust as a default.
    # Explicit commands are completely unaffected either way - routing only
    # ever activates when the typed line's first word doesn't already match a
    # real command name (kriya/repl.py::_route_line). The one real cost of
    # this default is a new hard dependency on routing.embed_model being
    # pulled; RoutingModelUnavailable fails loudly with the exact `ollama
    # pull ...` command needed (or `routing.enabled: false` to opt back out)
    # rather than silently degrading. See spikes/version_b_routing/README.md
    # for the full feasibility validation this config reflects.
    enabled: bool = Field(default=True)
    # Deliberately separate from embedding.model, which stays tuned for the RAG
    # code/doc index - a different task (long-form retrieval vs. short natural-
    # language intent phrases). Validated head-to-head on a 136-case held-out test
    # set: the packaged default RAG embedding model (nomic-embed-text) scored 77.2%
    # effective routing accuracy vs 95.6% for embeddinggemma - not a marginal gap,
    # and not something a silent fallback should paper over (see kriya/routing.py,
    # which fails loudly rather than falling back to embedding.model on this
    # model being unavailable).
    embed_model: str = Field(default="embeddinggemma:latest")
    # Below this cosine similarity to every command's exemplar centroid, treat the
    # input as out of scope even if the LLM gate said otherwise - defense in depth.
    reject_threshold: float = Field(default=0.3)
    # If the best and second-best candidate commands are within this similarity
    # margin of each other, ask which one instead of guessing (see
    # kriya.routing.Router.route). Ported from AskWhenUncertainClassifier in the
    # spike, where this was the single biggest lever separating a wrong guess from
    # a safe outcome.
    ask_margin: float = Field(default=0.05)

class KnowledgeConfig(BaseModel):
    training_cutoff: str = Field(default="2023-12-01")  # ISO date
    check_enabled: bool = Field(default=True)
    offline_mode: bool = Field(default=False)
    release_cache_ttl_days: int = Field(default=30, ge=1)

class EngineeringTriageConfig(BaseModel):
    """MA1 of the control-plane implementation plan (kriya/workflow/triage.py) -
    kind/risk_class/execution_weight classification for a generation request.
    Two independent switches, same "flipping one alone does nothing" pattern
    already used for autonomy.web_lookup_enabled + search.base_url: `enabled`
    turns classification ON (so it actually runs and gets logged), `shadow_mode`
    keeps its result from affecting anything current Kriya does. MA1 requires
    shadow_mode to stay True regardless of `enabled` - nothing reads
    EngineeringRoute for a real decision until MA2. Both default False here at
    the pydantic-model level (the safe bare-AppConfig() fallback, same
    convention as spec_compliance_enabled/auto_index_missing_dependency_graph
    above) - default_config.yaml is what actually turns shadow classification
    on for real usage once MA1.3 wires it in."""

    enabled: bool = Field(default=False)
    shadow_mode: bool = Field(default=True)

class ProcessProfilesConfig(BaseModel):
    """MA2 of the control-plane implementation plan - whether a resolved
    ProcessProfile (kriya/workflow/process_profile.py) actually gets to
    change run_generation_workflow()'s behavior, and which behaviors
    specifically. Deliberately separate from EngineeringTriageConfig above:
    `engineering_triage.enabled` controls whether classification runs and
    is observable at all (MA1's scope, unchanged by this); `enabled` here
    plus each per-capability `enforce_*` flag controls whether MA2's actual
    behavioral changes are live - "safe incremental activation" per the
    control-plane plan, so MA2.5's approval gating can be validated live
    without MA2.6's context/verification changes also being active, and
    vice versa. All default False - a new capability stays off until it's
    been live-validated, same convention as spec_compliance_enabled/
    auto_index_missing_dependency_graph (kriya/config/config.py's
    AutonomyConfig, above)."""

    enabled: bool = Field(default=False)
    enforce_approval: bool = Field(default=False)
    enforce_context_depth: bool = Field(default=False)
    # MA2.6b explicit decision (control-plane implementation plan): "ProcessProfile
    # may increase safety/process cost, but it may not reduce Kriya's existing
    # verification baseline." MA2 ships verification depth as telemetry-only -
    # verification_tier is recorded, but PolymorphicValidator/Quality Gates run
    # IDENTICALLY regardless of execution_weight (the full regression suite stays
    # unconditional for LIGHT too, exactly as it is today). Rejected here, not just
    # left unread, so a misconfiguration can never quietly believe reduced-LIGHT-
    # verification is active when it isn't - see the validator below.
    enforce_verification_depth: bool = Field(default=False)

    @field_validator("enforce_verification_depth")
    @classmethod
    def _not_yet_implemented(cls, v: bool) -> bool:
        if v:
            raise ValueError(
                "process_profiles.enforce_verification_depth is not implemented yet - MA2 "
                "ships verification depth as telemetry-only by explicit design decision "
                "('a triage misclassification cannot reduce regression-test coverage in "
                "MA2'). Setting this to true would silently do nothing rather than actually "
                "changing verification behavior, which is exactly the quiet misconfiguration "
                "this validator exists to prevent. Leave it false until a future milestone "
                "implements real, deterministically-safeguarded behavioral enforcement."
            )
        return v

class ExecutionPolicyConfig(BaseModel):
    """MA4.15 of the control-plane implementation plan - whether
    kriya/policy/execution.py::ExecutionPolicy's real decisions ever get to
    influence Kriya's actual behavior, beyond being computed and logged.

    `enabled` defaults to True, unlike engineering_triage.enabled/
    process_profiles.enabled above (both default False at the pydantic-model
    level - "a new capability stays off until it's been live-validated").
    ExecutionPolicy's audit-only consultation is NOT a new, unvalidated
    capability the way those were when their own config sections were
    introduced: every MA4.3-4.14 real call site (kriya/core/llm.py,
    kriya/tools/validate.py, kriya/workflow/edit_safety.py, kriya/tools/
    web.py, kriya/workflow/worktree.py, kriya/workflow/workflow.py) has
    already been calling ExecutionPolicy.evaluate() unconditionally, safely,
    and exception-guarded for this task's entire duration - defaulting
    `enabled` to False now would be a real regression (telemetry that's
    already flowing today would silently stop), not a safe-activation
    default. `enabled` only gates WorkflowEngine's own two call sites today
    (_audit_approval_rules, _authorize_action's Stage 2A caller) - it is not
    yet threaded through every real call site (see kriya/policy/execution.py
    and kriya/workflow/workflow.py's own comments for the honestly-tracked
    boundary on that).

    `mode` is the actual audit-vs-enforce gate. MA4 rolled out in AUDIT mode
    only, "before any future ENFORCE mode is considered" - not "before it
    is implemented," which MA4.13 already did (WorkflowEngine.
    _authorize_action's enforce=True branch was real, tested code from the
    start). POL-001-P2 (2026-09-10) is the explicit, confirmed-with-the-user
    decision that restriction was always waiting on - mirroring how
    workflow_controller.mode's own analogous "shadow"-only restriction was
    lifted (§8.5 of docs/design.md) only after being asked first, never
    silently. `mode` still defaults to `"audit"` - lifting the rejection
    makes `"enforce"` SELECTABLE, it does not change what a project gets
    without explicitly opting in. Every real call site this now actually
    activates (WorkflowEngine._authorize_action's Stage 2A caller,
    plugins/core_tools/__init__.py::GitTool's commit gate) was already
    built, tested, and dormant specifically so no new architecture would
    need to be invented under pressure once this moment arrived."""

    enabled: bool = Field(default=True)
    mode: str = Field(default="audit")

    @field_validator("mode")
    @classmethod
    def _mode_must_be_audit_or_enforce(cls, v: str) -> str:
        if v not in ("audit", "enforce"):
            raise ValueError(f"execution_policy.mode must be 'audit' or 'enforce', got {v!r}")
        return v

class WorkflowControllerConfig(BaseModel):
    """MA6.13/6.14 of the MA6 structured-execution implementation plan
    (kriya/workflow/workflow_controller.py) - mirrors ExecutionPolicyConfig's
    own audit/enforce precedent immediately above: `enabled` defaults False
    (a new, not-yet-broadly-validated capability stays off, same "ship the
    mechanism, default it off" pattern as engineering_triage/process_profiles
    when THEY were introduced). `mode` is the real gate - "shadow" builds a
    real EngineeringPlan and runs SubtaskExecutor against it for every
    subtask, but never lets the result affect real files or the run's
    actual outcome (kriya/workflow/workflow_controller.py's
    _run_structured_shadow) - the existing, unmodified run_generation_workflow()
    still owns the real outcome unconditionally in this mode.

    TRIED AND REVERTED, 2026-08-24: `enabled` was briefly flipped to
    True-by-default the same day, reasoned as "zero risk" because shadow
    mode is provably non-mutating (never affects real generation output).
    That reasoning covered CORRECTNESS but missed RESOURCE REACHABILITY:
    shadow mode still makes its own real Planner/Architect/SubtaskExecutor
    LLM calls through the real WorkflowEngine, independent of whatever
    run_generation_workflow() itself does. A widespread test pattern in
    this suite (tests/test_file_goal.py and others) mocks ONLY
    WorkflowEngine.run_generation_workflow (not Kernel/LLMClient), which
    was previously sufficient to guarantee zero real network calls end to
    end - once workflow_controller.enabled defaulted True, that same
    pattern silently let shadow's own agent calls reach a REAL, unmocked
    LLMClient, hanging/failing against a live network endpoint the test
    never expected to hit. Reverted same-day (never reached origin).
    Re-attempting this default flip needs a real test-suite audit first
    (every run_generation_workflow-only mock site, not just an explicit
    assertion about the default value) - not something to redo casually.

    "enforce" (MA7.8, 2026-08-24) is now real, allowed code - lifting the
    prior rejection was its own deliberate decision, confirmed with the
    user directly (mirroring how MA7.3 handled the analogous
    execution_policy.mode restriction: asked first, never silently
    lifted). SubtaskExecutor (MA6.5) still deliberately stops at "get file
    content or a tool result" - "enforce" does NOT port compile/test
    verification or approval gating into new machinery; instead
    WorkflowController._run_structured_enforce reuses the existing,
    mature run_generation_workflow() itself, once per subtask, the same
    real pattern kriya/workflow/milestones.py::run_milestones() already
    uses for milestones - see that method's own docstring for exactly
    what it does and its honest remaining scope boundaries (TOOL-tagged
    subtasks are refused outright, not silently skipped). `enabled`/`mode`
    both still default to False/"shadow" - "enforce" only ever runs for a
    project that explicitly opts in."""

    enabled: bool = Field(default=False)
    mode: str = Field(default="shadow")

    @field_validator("mode")
    @classmethod
    def _mode_must_be_valid(cls, v: str) -> str:
        if v not in ("shadow", "enforce"):
            raise ValueError(f"workflow_controller.mode must be 'shadow' or 'enforce', got {v!r}")
        return v

_VALID_RUNTIME_PROFILES = (None, "legacy", "validated", "hardened", "production")

# A production run must have a finite outer deadline.  One hour is deliberately
# generous enough for local-model generation while still preventing an
# unbounded run.  The reserve and per-file estimates remain independently
# configurable only outside the sealed profile.
PRODUCTION_GENERATION_TIME_BUDGET_SECONDS = 3600

# These guarantees have no weakening configuration knob: generation already
# refuses to run in the application workspace when isolated-worktree creation
# fails (workflow.py), and checkpoints/traces are persistent workflow artifacts.
# Declaring them is not evidence that the deployment can honor them:
# `kriya doctor --production` (kriya/production_doctor.py) verifies each one
# against the real environment and derives `runtime.fixed_guarantees` from those
# checks, so these identifiers stay the shared vocabulary, not a PASS.
PRODUCTION_FIXED_RUNTIME_GUARANTEES = frozenset({
    "candidate_isolation_fail_closed",
    "checkpoint_persistence",
    "trace_persistence",
    "no_uncontained_host_fallback",
})


def _production_budget_is_at_least_as_strict(value: Any) -> bool:
    return (
        isinstance(value, int) and not isinstance(value, bool)
        and 0 < value <= PRODUCTION_GENERATION_TIME_BUDGET_SECONDS
    )


# Sealed leaves for which a strictly safer explicit value is accepted in place
# of the preset value. Every other sealed leaf already carries its strictest
# setting, so it must equal the preset exactly.
_PRODUCTION_STRICTER_ACCEPTED = {
    ("autonomy", "generation_time_budget_seconds"): (
        _production_budget_is_at_least_as_strict,
        f"{PRODUCTION_GENERATION_TIME_BUDGET_SECONDS!r} or a smaller positive integer",
    ),
}


def production_sealed_value_satisfied(key: Tuple[str, str], value: Any) -> bool:
    """Whether ``value`` meets the production seal for ``key``: the preset
    value itself, or - for the leaves in _PRODUCTION_STRICTER_ACCEPTED - a
    value at least as strict."""
    stricter = _PRODUCTION_STRICTER_ACCEPTED.get(key)
    if stricter is not None:
        return stricter[0](value)
    return value == runtime_profile_preset_fields("production")[key]


def production_sealed_requirement(key: Tuple[str, str]) -> str:
    stricter = _PRODUCTION_STRICTER_ACCEPTED.get(key)
    return stricter[1] if stricter is not None else repr(runtime_profile_preset_fields("production")[key])


class AppConfig(BaseModel):
    """runtime_profile (2026-08-25, external review P2) - a named
    preset in place of remembering which combination of independent
    toggles (engineering_triage.shadow_mode, process_profiles.enabled,
    workflow_controller.enabled/mode) "hardened" actually means. Deliberately
    NOT a new independent config surface of its own: load_config() applies
    it as a straightforward, unconditional override of those existing
    fields AFTER the normal default+user merge. `legacy`, `validated`, and
    `hardened` are coherent presets; a user config must pick a profile OR
    hand-tune the individual fields, never mix both. None (the
    default) changes nothing - every field keeps behaving exactly as it
    always has, matching every existing kriya.yaml unchanged.

    Deliberately does NOT touch execution_policy.mode - that field's own
    validator has always hard-rejected "enforce" as a distinct, separate,
    later decision (kriya/config/config.py's own ExecutionPolicyConfig
    docstring), and this preset does not silently reach around that
    restriction. The narrow, always-on hard-invariant enforcement
    (kriya/policy/enforcement.py, MA7.3) and control-plane persistence
    already happen unconditionally whenever workflow_controller.enabled is
    true - there is no separate, real toggle for either one to include
    here, despite how the original review phrased the preset's contents.

    `production` is the separately sealed posture. It requires WorkflowController
    and ExecutionPolicy enforcement, local-only model egress, a finite generation
    deadline (3600 seconds, or an explicitly stricter smaller value), OCI
    target-code containment, MCP containment for any configured MCP server, and a
    required brownfield full-regression baseline. Candidate isolation,
    checkpoint/trace persistence, and refusal to fall back to raw-host execution
    are fixed runtime guarantees named in PRODUCTION_FIXED_RUNTIME_GUARANTEES, not
    decorative switches; `kriya doctor --production` verifies each against the
    real deployment (`runtime.fixed_guarantees` is derived from those checks).
    semantic_region_enforcement_required is intentionally not forced: language
    support remains incomplete until PRD-028, and the production doctor reports
    that precision boundary as `semantic.precision_boundary`."""

    llm: LLMConfig = Field(default_factory=LLMConfig)
    llm_chain: List[FallbackModelConfig] = Field(default_factory=list)
    plugins: PluginsConfig = Field(default_factory=PluginsConfig)
    paths: PathsConfig = Field(default_factory=PathsConfig)
    skills: SkillsConfig = Field(default_factory=SkillsConfig)
    logging: LoggingConfig = Field(default_factory=LoggingConfig)
    mcp: Dict[str, MCPServerConfig] = Field(default_factory=dict)
    mcp_lifecycle: MCPLifecycleConfig = Field(default_factory=MCPLifecycleConfig)
    embedding: EmbeddingConfig = Field(default_factory=EmbeddingConfig)
    autonomy: AutonomyConfig = Field(default_factory=AutonomyConfig)
    knowledge: KnowledgeConfig = Field(default_factory=KnowledgeConfig)
    search: SearchConfig = Field(default_factory=SearchConfig)
    agent_llms: AgentRolesConfig = Field(default_factory=AgentRolesConfig)
    routing: RoutingConfig = Field(default_factory=RoutingConfig)
    engineering_triage: EngineeringTriageConfig = Field(default_factory=EngineeringTriageConfig)
    process_profiles: ProcessProfilesConfig = Field(default_factory=ProcessProfilesConfig)
    execution_policy: ExecutionPolicyConfig = Field(default_factory=ExecutionPolicyConfig)
    workflow_controller: WorkflowControllerConfig = Field(default_factory=WorkflowControllerConfig)
    runtime_profile: Optional[str] = Field(default=None)

    @field_validator("runtime_profile")
    @classmethod
    def _runtime_profile_must_be_valid(cls, v: Optional[str]) -> Optional[str]:
        if v not in _VALID_RUNTIME_PROFILES:
            raise ValueError(f"runtime_profile must be one of {_VALID_RUNTIME_PROFILES!r}, got {v!r}")
        return v

    @model_validator(mode="after")
    def _production_profile_must_remain_sealed(self) -> "AppConfig":
        """Reject an incompletely expanded or manually weakened production profile.

        load_config() expands the preset before constructing AppConfig, but callers
        also construct AppConfig directly in integrations and tests. Validating the
        effective object here prevents that second path from treating the profile
        name as evidence while retaining unsafe defaults.
        """
        if self.runtime_profile != "production":
            return self

        required = runtime_profile_preset_fields("production")
        violations = []
        for (top, leaf), expected in required.items():
            actual = getattr(getattr(self, top), leaf)
            # MCP containment is conditional on MCP execution being enabled. The
            # expanded preset still turns it on pre-emptively, so later adding an
            # MCP server cannot weaken the posture by omission.
            if (top, leaf) == ("autonomy", "mcp_contained_execution_required") and not self.mcp:
                continue
            if not production_sealed_value_satisfied((top, leaf), actual):
                violations.append(
                    f"{top}.{leaf} must be {production_sealed_requirement((top, leaf))}, got {actual!r}"
                )

        if violations:
            raise ValueError(
                "runtime_profile 'production' is sealed; unsafe effective settings: "
                + "; ".join(violations)
            )
        return self

def runtime_profile_preset_fields(profile: Optional[str]) -> Dict[Any, Any]:
    """The exact (top_key, leaf_key) -> value mapping a runtime_profile
    preset expands to - see AppConfig's own docstring for what each preset
    means. Extracted as its own function so it can be tested directly
    without going through load_config() (whose SEC-009 P1 authority
    resolution denies a repository-sourced runtime_profile outright,
    independent of what it would have expanded to - see
    tests/test_sec009_config_authority.py) or through AppConfig's
    constructor (which does not itself apply this expansion - only
    load_config() does, deliberately, post-merge/pre-authority)."""
    if profile == "legacy":
        return {
            ("engineering_triage", "shadow_mode"): True,
            ("process_profiles", "enabled"): False,
            ("workflow_controller", "enabled"): False,
            ("workflow_controller", "mode"): "shadow",
        }
    if profile == "validated":
        return {
            ("engineering_triage", "shadow_mode"): False,
            ("process_profiles", "enabled"): True,
            ("workflow_controller", "enabled"): True,
            ("workflow_controller", "mode"): "shadow",
        }
    if profile == "hardened":
        return {
            ("engineering_triage", "shadow_mode"): False,
            ("process_profiles", "enabled"): True,
            ("workflow_controller", "enabled"): True,
            ("workflow_controller", "mode"): "enforce",
        }
    if profile == "production":
        return {
            ("engineering_triage", "shadow_mode"): False,
            ("process_profiles", "enabled"): True,
            ("workflow_controller", "enabled"): True,
            ("workflow_controller", "mode"): "enforce",
            ("execution_policy", "enabled"): True,
            ("execution_policy", "mode"): "enforce",
            ("autonomy", "egress_policy"): "local_only",
            ("autonomy", "generation_time_budget_seconds"): PRODUCTION_GENERATION_TIME_BUDGET_SECONDS,
            ("autonomy", "containment_backend"): "oci",
            ("autonomy", "contained_execution_required"): True,
            ("autonomy", "mcp_contained_execution_required"): True,
            # Until PRD-024 gives `auto` a safe risk-derived meaning, production
            # always captures the pristine full-suite baseline and fails closed
            # if that baseline is indeterminate.
            ("autonomy", "brownfield_full_regression_baseline_policy"): "required",
        }
    return {}


@dataclass
class ConfigResolutionState:
    """Everything load_config() resolves BEFORE authority is checked -
    extracted into its own function (resolve_config_state()) so both
    load_config() and `kriya authority inspect`/`approve` (kriya/cli.py)
    share exactly one code path for what counts as a violation and what the
    live effective value of each field is. approve() in particular must see
    EXACTLY what load_config() would compute - a second, slightly-different
    re-implementation here would be a real correctness risk, not just
    duplication."""
    config_dict: Dict[str, Any]
    workspace_root: str
    violations: List[Any]  # List[ConfigAuthorityViolation] - Any to avoid a module-level authority.py import


def resolve_config_state(config_path: Optional[str] = None) -> ConfigResolutionState:
    """Source -> provenance -> expansion -> field classification -> violation
    list. Never raises for a violation (that's load_config()'s job, after
    consulting SEC-009 P2 approval) and never constructs AppConfig - pure
    resolution, safe to call from `kriya authority inspect`/`approve` even
    when the config would ultimately be denied.
    """
    from kriya.config.authority import (
        ConfigSource,
        FieldClassification,
        FieldPath,
        agent_role_field_classification,
        compute_violations,
        explicit_config_source,
        path_field_classification,
    )

    config_dict: Dict[str, Any] = {}
    user_data: Dict[str, Any] = {}
    # provenance[(top_key, leaf_key)] = ConfigSource that set that field.
    # top_key is None for scalar/list top-level fields (runtime_profile,
    # llm_chain). Granularity mirrors the merge loop below exactly - this is
    # the actual unit a source can independently set.
    provenance: Dict[FieldPath, ConfigSource] = {}
    # Runtime-resolved overrides for fields whose classification depends on a
    # resolved value, not just the field name (paths.* containment check).
    classification_overrides: Dict[FieldPath, FieldClassification] = {}

    workspace_root = os.path.realpath(os.getcwd())

    # Determine Kriya Installation Directory
    KRIYA_INSTALL_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))

    # Try to load default configuration from package path
    default_path = os.path.join(os.path.dirname(__file__), "default_config.yaml")
    if os.path.exists(default_path):
        try:
            with open(default_path, "r") as f:
                default_data = yaml.safe_load(f)
                if default_data:
                    # Resolve relative paths in default config to Kriya Installation root
                    if "paths" in default_data:
                        for k, v in default_data["paths"].items():
                            if isinstance(v, str) and (v.startswith("./") or v.startswith("../")):
                                default_data["paths"][k] = os.path.realpath(os.path.join(KRIYA_INSTALL_DIR, v))
                    if "plugins" in default_data and "directory" in default_data["plugins"]:
                        v = default_data["plugins"]["directory"]
                        if isinstance(v, str) and (v.startswith("./") or v.startswith("../")):
                            default_data["plugins"]["directory"] = os.path.realpath(os.path.join(KRIYA_INSTALL_DIR, v))
                    config_dict.update(default_data)
                    for top_key, val in default_data.items():
                        if isinstance(val, dict):
                            for leaf_key in val:
                                provenance[(top_key, leaf_key)] = ConfigSource.PACKAGED_DEFAULT
                        else:
                            provenance[(None, top_key)] = ConfigSource.PACKAGED_DEFAULT
        except Exception as e:
            logger.warning(f"Failed to load packaged default configuration at '{default_path}', falling back to bare defaults: {e}")

    # If no config_path is explicitly provided, look for 'kriya.yaml' or 'kriya.yml' in current directory
    # If not found, look for it in the Kriya Installation Directory
    config_dir = os.getcwd()
    source: Optional["ConfigSource"] = None
    if not config_path:
        for filename in ["kriya.yaml", "kriya.yml"]:
            path = os.path.join(os.getcwd(), filename)
            if os.path.exists(path):
                config_path = path
                config_dir = os.getcwd()
                source = ConfigSource.AUTO_DISCOVERED_CWD
                break

        if not config_path:
            # Fall back to Kriya installation directory
            for filename in ["kriya.yaml", "kriya.yml"]:
                path = os.path.join(KRIYA_INSTALL_DIR, filename)
                if os.path.exists(path):
                    config_path = path
                    config_dir = KRIYA_INSTALL_DIR
                    source = ConfigSource.AUTO_DISCOVERED_INSTALL_DIR
                    break
    else:
        # realpath (not just abspath) so a symlinked --config path is
        # classified and resolved by its REAL target, not its apparent
        # location - closes the symlinked-config gap (SEC-009 P1).
        config_path = os.path.realpath(config_path)
        config_dir = os.path.dirname(config_path)
        source = explicit_config_source(config_path, workspace_root)

    # Load user config if specified and exists
    if config_path and os.path.exists(config_path):
        try:
            with open(config_path, "r") as f:
                user_data = yaml.safe_load(f) or {}
                if user_data:
                    # Resolve relative paths in user config to config_dir (realpath -
                    # resolves symlinks in the resulting path, not just abspath).
                    if "paths" in user_data:
                        for k, v in user_data["paths"].items():
                            if isinstance(v, str) and (v.startswith("./") or v.startswith("../")):
                                user_data["paths"][k] = os.path.realpath(os.path.join(config_dir, v))
                    if "plugins" in user_data and "directory" in user_data["plugins"]:
                        v = user_data["plugins"]["directory"]
                        if isinstance(v, str) and (v.startswith("./") or v.startswith("../")):
                            user_data["plugins"]["directory"] = os.path.realpath(os.path.join(config_dir, v))

                    # logging.file - SEC-009 bypass-closure fix (2026-09-12).
                    # Canonicalize to ONE resolved absolute realpath (anchored
                    # to config_dir, not process CWD) BEFORE classification
                    # and BEFORE configure_logging() ever sees it, so the
                    # value authority resolution inspects and the value
                    # execution actually opens are always identical - unlike
                    # paths.*/plugins.directory above (which only rewrite a
                    # "./"-or-"../"-prefixed value), this resolves ANY string
                    # value (bare-relative, absolute, or already "./"-
                    # prefixed) uniformly, because configure_logging()'s own
                    # os.path.abspath() would otherwise anchor a bare-relative
                    # value to CWD instead of config_dir - the exact
                    # classify-here/execute-there split this fix must not
                    # introduce. A value of `null`/non-string (explicitly
                    # disabling file logging) is left untouched here and
                    # handled directly in the classification_overrides block
                    # below - disabling a feature grants no authority.
                    if isinstance(user_data.get("logging"), dict) and isinstance(user_data["logging"].get("file"), str):
                        lf = user_data["logging"]["file"]
                        resolved_lf = lf if os.path.isabs(lf) else os.path.join(config_dir, lf)
                        user_data["logging"]["file"] = os.path.realpath(resolved_lf)

                    # logging.directory - canonicalized ONCE here (expand ~,
                    # realpath) so the value SEC-009 digests is exactly the
                    # directory kriya/core/logging_setup.py opens. A relative
                    # value is a typed error: it is never anchored to the CWD
                    # or to config_dir.
                    if isinstance(user_data.get("logging"), dict) and user_data["logging"].get("directory") is not None:
                        from kriya.core.logging_setup import canonical_log_directory
                        user_data["logging"]["directory"] = canonical_log_directory(
                            user_data["logging"]["directory"], "logging.directory"
                        )

                    # TOOL-003 P1: MCP capability filesystem paths - resolved
                    # and escape-checked HERE, anchored to config_dir (same
                    # anchor and same realpath-based idiom as paths.*/
                    # logging.file above, for the identical reason: the value
                    # SEC-009 authority resolution digests below and the
                    # value kriya/mcp/capability.py's resolve_mcp_capability_
                    # profile() eventually binds to a real MCPClient must be
                    # the SAME single resolved value, computed ONCE, never
                    # independently re-resolved later against a possibly-
                    # different CWD (the exact classify-here/execute-there
                    # split the logging.file bypass fix exists to prevent -
                    # see this function's own module-level precedent above).
                    # A `capabilities` key is unconditionally backfilled to
                    # `{}` for every configured server (even one that never
                    # mentions `capabilities` at all) so an omitted key and
                    # an explicit empty `capabilities: {}` always produce the
                    # IDENTICAL effective dict from this point forward -
                    # otherwise the two would digest differently under
                    # SEC-009 P2 (build_security_field_records() digests this
                    # exact dict) even though pydantic resolves both to the
                    # same default MCPCapabilityConfig(), causing a spurious
                    # "approval invalidated" the moment an operator adds a
                    # no-op explicit empty block.
                    if isinstance(user_data.get("mcp"), dict):
                        for _server_name, _server_cfg in user_data["mcp"].items():
                            if not isinstance(_server_cfg, dict):
                                continue
                            caps = _server_cfg.setdefault("capabilities", {})
                            if not isinstance(caps, dict):
                                continue
                            for _path_field in ("additional_read_paths", "additional_write_paths"):
                                raw_paths = caps.get(_path_field)
                                if not isinstance(raw_paths, list):
                                    continue
                                resolved_paths = []
                                for raw in raw_paths:
                                    if not isinstance(raw, str):
                                        resolved_paths.append(raw)
                                        continue
                                    if os.path.isabs(raw):
                                        # Absolute -> an explicit host path.
                                        # Unambiguous by construction - no
                                        # anchor, no escape check applies (an
                                        # absolute path was never workspace-
                                        # relative to begin with).
                                        resolved_paths.append(os.path.realpath(raw))
                                        continue
                                    # Relative -> workspace-relative-only:
                                    # resolved against config_dir and
                                    # canonicalized (realpath follows both
                                    # `..` traversal and any intermediate
                                    # symlink to its real target) - REJECTED
                                    # if the real target does not stay inside
                                    # the real config_dir. A relative-looking
                                    # path must never be able to smuggle an
                                    # outside-anchor target past someone
                                    # reading the config, and must never
                                    # silently receive host-path authority it
                                    # did not explicitly ask for by being
                                    # written as an absolute path.
                                    real_root = os.path.realpath(config_dir)
                                    real_target = os.path.realpath(os.path.join(real_root, raw))
                                    if real_target != real_root and not real_target.startswith(real_root + os.sep):
                                        raise ValueError(
                                            f"mcp.{_server_name}.capabilities.{_path_field} entry "
                                            f"{raw!r} is a relative path that resolves outside "
                                            f"{real_root!r} - a relative capability path must stay "
                                            f"within the config's own directory; use an absolute "
                                            f"path to explicitly authorize a host path outside it."
                                        )
                                    resolved_paths.append(real_target)
                                caps[_path_field] = resolved_paths

                    # paths.{skills,memory,logs} classification depends on the
                    # resolved value (in-workspace vs. escaping) - resolve
                    # non-relative values too (an absolute path or a
                    # symlinked one) so containment is checked against the
                    # real target in every case, not just the "./"-prefixed
                    # relative-path branch above. Anchored to config_dir (the
                    # directory the SETTING config file lives in), not
                    # workspace_root/CWD - see path_field_classification()'s
                    # own docstring for why: an explicit --config living
                    # outside CWD with an ordinary relative `./skills` value
                    # is not an authority escape, just a config that lives
                    # somewhere else.
                    if isinstance(user_data.get("paths"), dict):
                        for k, v in user_data["paths"].items():
                            if k in ("skills", "memory", "logs") and isinstance(v, str):
                                resolved = v if os.path.isabs(v) else os.path.join(config_dir, v)
                                classification_overrides[("paths", k)] = path_field_classification(
                                    k, resolved, config_dir
                                )

                    # agent_llms.<role> is one atomic merge unit (see
                    # agent_role_field_classification()'s docstring) -
                    # REPOSITORY_SAFE unless it redirects a network
                    # destination (base_url) somewhere within it.
                    if isinstance(user_data.get("agent_llms"), dict):
                        for role, role_val in user_data["agent_llms"].items():
                            classification_overrides[("agent_llms", role)] = agent_role_field_classification(role_val)

                    # logging.file classification - same value-sensitive
                    # containment check as paths.* above, now that the value
                    # (if a string) has already been canonicalized to a
                    # single resolved realpath anchored at config_dir. An
                    # explicit `null` (disable file logging entirely) is
                    # REPOSITORY_SAFE outright - it grants no filesystem
                    # authority, it removes a capability.
                    if isinstance(user_data.get("logging"), dict) and "file" in user_data["logging"]:
                        lf = user_data["logging"]["file"]
                        if isinstance(lf, str):
                            classification_overrides[("logging", "file")] = path_field_classification(
                                "file", lf, config_dir
                            )
                        else:
                            classification_overrides[("logging", "file")] = FieldClassification.REPOSITORY_SAFE
                    # logging.directory: null (the canonical default) grants
                    # nothing; any directory is a filesystem write target and
                    # falls to its static SECURITY_AUTHORITY entry.
                    if isinstance(user_data.get("logging"), dict) and "directory" in user_data["logging"]:
                        if user_data["logging"]["directory"] is None:
                            classification_overrides[("logging", "directory")] = FieldClassification.REPOSITORY_SAFE

                    # Simple deep merge of level-1 dicts, tracking provenance
                    # at the exact same granularity the merge itself uses.
                    for key, val in user_data.items():
                        if isinstance(val, dict) and key in config_dict and isinstance(config_dict[key], dict):
                            config_dict[key].update(val)
                            for leaf_key in val:
                                provenance[(key, leaf_key)] = source
                        else:
                            config_dict[key] = val
                            if isinstance(val, dict):
                                for leaf_key in val:
                                    provenance[(key, leaf_key)] = source
                            else:
                                provenance[(None, key)] = source
        except Exception as e:
            from kriya.core.logging_setup import LogDirectoryError
            if isinstance(e, LogDirectoryError):
                raise  # already typed and names the field
            raise ValueError(f"Failed to load configuration at {config_path}: {e}") from e

    # --- runtime_profile expansion (dict-level, BEFORE AppConfig/authority) ---
    # Applied before AppConfig construction so authority resolution below
    # inspects the FULLY EXPANDED effective configuration - a repository
    # setting runtime_profile cannot launder a security-sensitive field
    # change through preset expansion and have it slip past classification
    # simply because the raw `runtime_profile` scalar was the only thing
    # checked. Derived fields inherit RUNTIME_PROFILE_OVERRIDE provenance
    # (itself denied for any repository-equivalent source) whenever the
    # runtime_profile value itself did not come from a trusted source.
    runtime_profile = config_dict.get("runtime_profile")
    if runtime_profile is not None:
        preset_fields = runtime_profile_preset_fields(runtime_profile)
        if runtime_profile == "production":
            contradictory = []
            for (top, leaf), required_value in preset_fields.items():
                explicit_section = user_data.get(top)
                if (
                    isinstance(explicit_section, dict)
                    and leaf in explicit_section
                    and not production_sealed_value_satisfied((top, leaf), explicit_section[leaf])
                ):
                    contradictory.append(
                        f"{top}.{leaf}={explicit_section[leaf]!r} "
                        f"(production requires {production_sealed_requirement((top, leaf))})"
                    )
            if contradictory:
                raise ValueError(
                    "runtime_profile 'production' rejects contradictory overrides: "
                    + "; ".join(sorted(contradictory))
                )
        else:
            # Preserve the original all-or-nothing behavior of the three older
            # presets exactly. Production is more precise because its sealed
            # surface spans only selected leaves in larger sections such as
            # autonomy, where unrelated settings remain legitimate.
            conflicting = sorted(
                key for key in ("engineering_triage", "process_profiles", "workflow_controller")
                if key in user_data
            )
            if conflicting:
                raise ValueError(
                    f"runtime_profile cannot be combined with explicit {conflicting!r}; choose the preset "
                    "or configure the individual subsystems"
                )

        rp_source = provenance.get((None, "runtime_profile"), ConfigSource.PACKAGED_DEFAULT)
        derived_source = (
            ConfigSource.RUNTIME_PROFILE_OVERRIDE if rp_source not in (
                ConfigSource.PACKAGED_DEFAULT, ConfigSource.PLATFORM_FLOOR
            ) else rp_source
        )

        for (top, leaf), value in preset_fields.items():
            if runtime_profile == "production":
                explicit_section = user_data.get(top)
                if (
                    isinstance(explicit_section, dict)
                    and leaf in explicit_section
                    and explicit_section[leaf] != value
                ):
                    # Past the contradiction check above, a differing explicit
                    # value is a stricter one (e.g. a shorter deadline): keep it,
                    # with its own provenance, rather than loosening it back to
                    # the preset.
                    continue
            config_dict.setdefault(top, {})[leaf] = value
            provenance[(top, leaf)] = derived_source

    violations = compute_violations(provenance, classification_overrides)
    return ConfigResolutionState(config_dict=config_dict, workspace_root=workspace_root, violations=violations)


def load_config(config_path: Optional[str] = None, trust_file: Optional[str] = None) -> AppConfig:
    """Resolves config_path via resolve_config_state(), then applies SEC-009
    P1/P2 authority: any violation is checked against a valid, digest-
    matching, currently-valid approval artifact (kriya/config/
    authority_approval.py) - the local per-workspace store, or `trust_file`
    for CI/operator-supplied trust (defaulting to the KRIYA_TRUST_FILE env
    var if not passed explicitly). Only violations that remain uncovered
    after that check raise ConfigAuthorityError, BEFORE AppConfig is ever
    constructed and therefore BEFORE Kernel/PluginManager/MCPManager can see
    this configuration. With no valid approval, behavior is byte-identical
    to P1 (fail closed is the unconditional floor - see
    authority_approval.py's module docstring for why the trust sources
    supported here cannot become blanket repository trust).
    """
    from kriya.config.authority import ConfigAuthorityError
    from kriya.config.authority_approval import resolve_with_approval, validate_trust_path_outside_workspace

    state = resolve_config_state(config_path)

    if state.violations:
        effective_trust_file = trust_file or os.environ.get("KRIYA_TRUST_FILE")
        if effective_trust_file:
            # Refuses a trust-file path resolving inside the workspace root -
            # the load-bearing guard against a checked-out branch shipping
            # both a hostile config and its own matching "approval" (see
            # authority_approval.py's module docstring). Raises before any
            # approval lookup even happens - never silently falls through to
            # the local store when an explicit trust_file is itself unsafe.
            validate_trust_path_outside_workspace(effective_trust_file, state.workspace_root)
        remaining = resolve_with_approval(state.violations, state.config_dict, state.workspace_root, effective_trust_file)
        if remaining:
            raise ConfigAuthorityError(remaining)

    cfg = AppConfig(**state.config_dict)

    # Enforce baseline sensitive paths inheritance
    baseline_sensitive = [
        r".*\.env$", r".*secrets.*", r"\.github/workflows/.*", r"Jenkinsfile",
        r".*credentials.*", r".*password.*"
    ]
    for pattern in baseline_sensitive:
        if pattern not in cfg.autonomy.sensitive_paths:
            cfg.autonomy.sensitive_paths.append(pattern)

    return cfg
