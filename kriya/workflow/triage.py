"""Engineering triage domain model - kind/risk/weight classification for a
generation request, per the Milestone Agent control-plane design (MA1 of the
control-plane implementation plan; see session history / docs/design.md for
the full MA1-MA6 sequencing).

MA1.1 (domain types + the two pure classification functions +
escalate_risk) and MA1.2 (EngineeringTriageService.classify() - goal-text
signal detection plus best-effort repository signals) both live here.
Deliberately NOT included yet: wiring into
WorkflowEngine.run_generation_workflow() in shadow mode (MA1.3);
trace/telemetry integration (MA1.4). The MA1.1 types/functions remain pure
(no LLM, no filesystem, no config); classify() below is the first thing in
this module that touches a workspace and, optionally, the dependency graph -
still zero LLM calls, per MA1.2's own scope ("do not add an LLM call yet
except for the true ambiguous kind case") - the genuinely-ambiguous/
conflicting-signals case that the full design routes to an LLM Router pass
instead falls back to the design's own documented deterministic `floor`
here (see _floor_kind below), leaving `router_used=False`. Wiring a real
router LLM call is deferred to whenever WorkflowEngine's kernel/llm_client
are actually available to this service (MA1.3 or later) - classify() already
accepts an optional `kernel` at construction so that wiring is additive,
not a signature change.

Not to be confused with kriya/routing.py's Router, which answers a
completely different question (which CLI command does this natural-language
repl line map to: generate/fix/review/ask/...). EngineeringRoute answers
"how much process does this generation request deserve," a question that
doesn't exist yet for `kriya repl` command routing and never will - the two
are unrelated classifiers over unrelated label sets, kept in separate
modules on purpose.
"""

import logging
import os
import re
from dataclasses import asdict, dataclass, field
from enum import Enum, IntEnum
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class ChangeKind(str, Enum):
    """What SHAPE the requested change is - never how risky it is. Settled
    by request text/repo-state signals (MA1.2), independently of risk_class
    below. A `task` can be `risk_class: high` (e.g. a one-line auth-bypass
    fix) without ever becoming a different kind - shape and danger are
    orthogonal axes by design, not a single score."""

    TASK = "task"
    ENHANCEMENT = "enhancement"
    MILESTONE = "milestone"
    REFACTOR = "refactor"


class RiskClass(IntEnum):
    """Ordered so `max()` over a sequence of RiskClass values does the right
    thing directly - this is what makes max_observed_risk_class's monotonic
    "never automatically lowered" invariant a one-line max() rather than a
    hand-written comparison table."""

    LOW = 1
    MEDIUM = 2
    HIGH = 3


class ExecutionWeight(str, Enum):
    """How much of the pipeline actually runs for this request - the thing
    every downstream stage (context breadth, planning rigor, verification
    tier, approval gating) is meant to read, per kind x risk_class, never
    kind alone. See determine_execution_weight() below."""

    LIGHT = "light"
    STANDARD = "standard"
    HEAVY = "heavy"


@dataclass(frozen=True)
class ImpactVector:
    """Ten deterministic, tool-checkable components risk_class is computed
    from - never file count alone, and never asked of a model. Every field
    here is meant to come from real signals (the local index, the artifact/
    dependency graph, git, pattern matching against the request and touched
    paths) computed by EngineeringTriageService (MA1.2) - this dataclass
    itself has no opinion on how its fields get populated, only on what they
    mean once they are."""

    files_touched: int = 0
    symbols_impacted: int = 0
    downstream_references: int = 0

    public_contract_change: bool = False
    persistence_change: bool = False
    security_boundary_change: bool = False
    dependency_change: bool = False
    configuration_change: bool = False
    build_system_change: bool = False
    shared_entrypoint_change: bool = False


@dataclass(frozen=True)
class EngineeringRoute:
    """The full classification result for one generation request. Frozen
    and immutable by construction - escalate_risk() below returns a NEW
    EngineeringRoute rather than mutating one in place, so a caller can
    never accidentally lose track of which route object a prior decision
    (approval, context assembly, verification tier) was actually made
    against.

    initial_risk_class is set once, at first classification, and never
    changes. current_risk_class is the most recent recomputation.
    max_observed_risk_class is the running maximum across every
    recomputation this run has done - the only value anything downstream is
    meant to reason from (see escalate_risk's own docstring)."""

    kind: ChangeKind
    impact: ImpactVector

    initial_risk_class: RiskClass
    current_risk_class: RiskClass
    max_observed_risk_class: RiskClass

    execution_weight: ExecutionWeight

    deterministic_signals: Dict[str, Any] = field(default_factory=dict)
    reason_codes: List[str] = field(default_factory=list)

    router_used: bool = False
    router_confidence: Optional[float] = None

    # MA2.7 - set ONCE at first classification (classify()) and never
    # changed thereafter, same "set once, never changes" contract as
    # initial_risk_class above - what MA2.10's telemetry shape calls
    # impact_initial/initial_execution_weight, kept as real fields
    # (mirroring initial_risk_class's own precedent) rather than derived
    # after the fact, so no caller can construct a route missing this
    # history. Optional/defaulted only so a construction site that
    # predates MA2.7 (an existing test fixture, say) doesn't hard-fail -
    # to_dict() below falls back to impact/execution_weight when unset,
    # which is exactly correct for a route that has never been recomputed.
    initial_impact: Optional[ImpactVector] = None
    initial_execution_weight: Optional[ExecutionWeight] = None

    def with_recomputed_risk(
        self,
        risk: "RiskClass",
        impact: "ImpactVector",
        reason_codes: List[str],
    ) -> "EngineeringRoute":
        """MA2.2 - the one place the monotonic max_observed_risk_class
        invariant is enforced when a FRESH ImpactVector is available (not
        just a bare risk value) - MA2.4's post-Architect recomputation is
        the first real caller: Architect's real touched-file list produces
        a genuinely new ImpactVector, not just a new risk number, and that
        new impact is worth keeping (e.g. for telemetry's `impact_final`)
        rather than discarded once risk is derived from it.

        Returns a NEW EngineeringRoute (this object is frozen) with:
          - impact REPLACED by the new, more complete ImpactVector
          - current_risk_class set to `risk`
          - max_observed_risk_class = max(previous max, risk) - can only
            move up, exactly like escalate_risk's own invariant (this
            method is now that invariant's one real implementation -
            escalate_risk below delegates here)
          - execution_weight recomputed from (kind, the NEW max) - never
            from current_risk_class alone, so a later, narrower
            recomputation can never quietly undo an earlier escalation's
            process weight
          - reason_codes EXTENDED (never replaced), so the full audit
            trail across every recomputation survives, not just the latest
            one

        kind, initial_risk_class, initial_impact, initial_execution_weight,
        deterministic_signals, router_used, and router_confidence are
        carried over unchanged - only the fields listed above ever move
        after first classification."""
        new_max = max(self.max_observed_risk_class, risk)
        return EngineeringRoute(
            kind=self.kind,
            impact=impact,
            initial_risk_class=self.initial_risk_class,
            current_risk_class=risk,
            max_observed_risk_class=new_max,
            execution_weight=determine_execution_weight(self.kind, new_max),
            deterministic_signals=self.deterministic_signals,
            reason_codes=[*self.reason_codes, *reason_codes],
            router_used=self.router_used,
            router_confidence=self.router_confidence,
            initial_impact=self.initial_impact,
            initial_execution_weight=self.initial_execution_weight,
        )

    def to_dict(self) -> Dict[str, Any]:
        """Content-free operational telemetry shape (MA1.4, extended MA2.7) -
        matches GenerationState.generation_metrics()'s own "safe to persist
        in local traces" convention: only the classification and why it
        fired, never goal text or file content. Enum members serialize to
        their plain string/name form so this round-trips cleanly through
        json.dumps (kriya/core/trace.py::TraceLogger.log_run).

        MA2.7 adds the initial-vs-final view that actually makes an
        escalation legible without cross-referencing log lines:
        initial_execution_weight/impact_initial (falling back to the
        current values for a route that predates MA2.7 or was never
        recomputed - identical to current for such a route anyway, so the
        fallback is exactly correct, not a guess), `escalated` (a simple
        risk-class comparison), and `escalation_stage` - "post_architect"
        when the evidence is a reason code MA2.4's recompute_from_files
        actually tagged that way, "none" when nothing escalated,
        "unspecified" for an escalation with no traceable stage tag (e.g.
        a bare escalate_risk() call outside the post-Architect path)."""
        escalated = self.max_observed_risk_class > self.initial_risk_class
        if any(c.startswith("post_architect:") for c in self.reason_codes):
            escalation_stage = "post_architect"
        elif escalated:
            escalation_stage = "unspecified"
        else:
            escalation_stage = "none"
        return {
            "kind": self.kind.value,
            "initial_risk_class": self.initial_risk_class.name,
            "current_risk_class": self.current_risk_class.name,
            "max_observed_risk_class": self.max_observed_risk_class.name,
            "execution_weight": self.execution_weight.value,
            "initial_execution_weight": (self.initial_execution_weight or self.execution_weight).value,
            "final_execution_weight": self.execution_weight.value,
            "impact": asdict(self.impact),
            "impact_initial": asdict(self.initial_impact or self.impact),
            "impact_final": asdict(self.impact),
            "escalated": escalated,
            "escalation_stage": escalation_stage,
            "reason_codes": list(self.reason_codes),
            "deterministic_signals": dict(self.deterministic_signals),
            "router_used": self.router_used,
            "router_confidence": self.router_confidence,
        }


def risk_reason_codes(impact: ImpactVector) -> List[str]:
    """Names every ImpactVector component that actually fired, in the same
    fixed order determine_risk_class checks them - the audit trail behind
    "every classification traces back to the one rule that actually fired,"
    not a blend nobody can explain afterward. Kept as its own function
    (rather than folded into determine_risk_class's return value) so that
    function's signature stays exactly `ImpactVector -> RiskClass`, trivial
    to unit test without also asserting on reason-code text."""
    reasons: List[str] = []
    if impact.security_boundary_change:
        reasons.append("security_boundary_change")
    if impact.build_system_change:
        reasons.append("build_system_change")
    if impact.persistence_change and impact.downstream_references > 0:
        reasons.append("persistence_change+downstream_references")
    if impact.public_contract_change and impact.downstream_references > 0:
        reasons.append("public_contract_change+downstream_references")
    if impact.persistence_change:
        reasons.append("persistence_change")
    if impact.public_contract_change:
        reasons.append("public_contract_change")
    if impact.configuration_change:
        reasons.append("configuration_change")
    if impact.dependency_change:
        reasons.append("dependency_change")
    if impact.files_touched >= 5:
        reasons.append("files_touched>=5")
    if impact.downstream_references >= 5:
        reasons.append("downstream_references>=5")
    return reasons


def determine_risk_class(impact: ImpactVector) -> RiskClass:
    """Fixed rule, not a weighted sum - a point total is hard to audit and
    easy for a borderline case to land anywhere; an ordered set of rules
    means every classification traces back to the one rule that actually
    fired (risk_reason_codes above). Pure: no config, no LLM, no
    filesystem - the caller (EngineeringTriageService, MA1.2) is entirely
    responsible for populating `impact` from real signals first."""
    if (
        impact.security_boundary_change
        or impact.build_system_change
        or (impact.persistence_change and impact.downstream_references > 0)
        or (impact.public_contract_change and impact.downstream_references > 0)
    ):
        return RiskClass.HIGH

    if (
        impact.persistence_change
        or impact.public_contract_change
        or impact.configuration_change
        or impact.dependency_change
        or impact.files_touched >= 5
        or impact.downstream_references >= 5
    ):
        return RiskClass.MEDIUM

    return RiskClass.LOW


# kind x risk_class -> execution_weight. `milestone` never lands on `light`
# even at low risk - a milestone-shaped request always gets at least
# `standard` depth, per the design's own process-profile table; every other
# kind scales light/standard/heavy directly with risk_class.
_EXECUTION_WEIGHT_TABLE: Dict[ChangeKind, Dict[RiskClass, ExecutionWeight]] = {
    ChangeKind.TASK: {
        RiskClass.LOW: ExecutionWeight.LIGHT,
        RiskClass.MEDIUM: ExecutionWeight.STANDARD,
        RiskClass.HIGH: ExecutionWeight.HEAVY,
    },
    ChangeKind.ENHANCEMENT: {
        RiskClass.LOW: ExecutionWeight.LIGHT,
        RiskClass.MEDIUM: ExecutionWeight.STANDARD,
        RiskClass.HIGH: ExecutionWeight.HEAVY,
    },
    ChangeKind.REFACTOR: {
        RiskClass.LOW: ExecutionWeight.LIGHT,
        RiskClass.MEDIUM: ExecutionWeight.STANDARD,
        RiskClass.HIGH: ExecutionWeight.HEAVY,
    },
    ChangeKind.MILESTONE: {
        RiskClass.LOW: ExecutionWeight.STANDARD,
        RiskClass.MEDIUM: ExecutionWeight.STANDARD,
        RiskClass.HIGH: ExecutionWeight.HEAVY,
    },
}


def determine_execution_weight(kind: ChangeKind, risk: RiskClass) -> ExecutionWeight:
    """A function of BOTH kind and risk_class, never kind alone - the whole
    point of separating the two axes. A `heavy task` (e.g. a one-line
    expired-JWT auth-bypass fix) gets `heavy` depth applied to `task`
    sequencing; it never silently falls back to "tasks get the light
    treatment" just because it reads like a bug report. Pure, same
    contract as determine_risk_class above."""
    return _EXECUTION_WEIGHT_TABLE[kind][risk]


def escalate_risk(
    route: EngineeringRoute,
    new_risk: RiskClass,
    new_reason_codes: Optional[List[str]] = None,
) -> EngineeringRoute:
    """Recomputes current_risk_class and, monotonically, max_observed_risk_class
    and execution_weight - returns a NEW EngineeringRoute, never mutates
    `route` in place. max_observed_risk_class can only move up
    (max(previous_max, new_risk)); a later, narrower view finding LOWER risk
    than an earlier one found is recorded in current_risk_class but never
    erases what max_observed_risk_class already holds. execution_weight is
    recomputed from (route.kind, the new max_observed_risk_class) every
    time this fires, since nothing downstream is allowed to reason from
    anything but the running maximum - a plan or implementation discovering
    real risk the initial classification missed must not silently keep
    running at the original, now-stale execution_weight.

    new_reason_codes (optional) is appended to route.reason_codes so the
    audit trail shows WHY this particular escalation (or non-escalation)
    happened, not just the before/after risk values.

    MA2.2: this is now a thin convenience wrapper over
    EngineeringRoute.with_recomputed_risk for the common case where only
    the risk value changed, not the impact vector itself - route.impact is
    passed through unchanged. with_recomputed_risk is the one real
    implementation of the monotonic invariant; kept here, unchanged in
    signature/behavior, since MA1.1 callers already depend on this exact
    shape."""
    return route.with_recomputed_risk(new_risk, route.impact, new_reason_codes or [])


# ============================================================================
# MA1.2 - EngineeringTriageService: assembles a fresh EngineeringRoute from a
# real goal + workspace. Everything below is a deterministic ESTIMATE from
# goal text plus whatever `known_files` the caller already knows about (an
# existing milestone's established files, or files a caller has already
# pinned down) - initial triage necessarily runs before Planner/Architect
# have produced a real touched-file list, so this is deliberately an
# estimate, sharpened later by MA2's re-triage once Architect's real
# `architect_files` exists (see triage.py's own module docstring and
# escalate_risk above). Getting THIS call slightly wrong is cheap - it's one
# input to execution_weight, not a one-shot final verdict - getting the
# re-triage wrong after real files are known would matter far more.
# ============================================================================

# --- goal-text signal detectors --------------------------------------------
# Each is a small, independently testable pure function over the raw goal
# text - no filesystem, no config, no LLM. Mirrors kriya/workflow/acceptance.py's
# established style (module-level compiled regexes, IGNORECASE where the
# request text's casing shouldn't matter, a short "why this pattern" comment
# only where the shape isn't obvious from the regex itself).

_REPRO_SHAPE_RE = re.compile(
    r"\b(?:traceback|stack\s*trace|exception|throws?\b|"
    r"crashes?\s+when|fails?\s+when|breaks?\s+when|"
    r"expected\s+.{1,60}?\bgot\b|returns?\s+(?:a\s+)?\d{3}\b|"
    r"\bbug\b|\bdefect\b|\bregression\b|\bbroken\b)",
    re.IGNORECASE,
)
# A file:line reference (a real stack-trace frame, or a reproduction quoting
# one) is strong, unambiguous repro-shape evidence on its own.
_STACK_LOCATION_RE = re.compile(r"\b[\w./-]+\.(?:py|java|rb|js|ts|go|c|cpp|h|cs):\d+\b")


def _detect_repro_shape(goal: str) -> bool:
    """Strong pull toward `task`; can short-circuit the rest of the decision
    tree (see EngineeringTriageService.classify below) - matches the
    design's own signal table: an error message, a stack trace, "expected X,
    got Y," "crashes when…"."""
    text = goal or ""
    return bool(_REPRO_SHAPE_RE.search(text) or _STACK_LOCATION_RE.search(text))


_MIGRATION_LANGUAGE_RE = re.compile(
    r"\b(?:replace|migrate|migrating|migration|swap\s+out|switch\s+(?:from|to)|"
    r"upgrade\s+to|port\s+(?:it\s+)?(?:to|from)|move\s+(?:off|away\s+from)|"
    r"deprecat\w*)\b",
    re.IGNORECASE,
)


def _detect_migration_language(goal: str) -> bool:
    """Strong pull toward `refactor`; can short-circuit. The design's full
    signal also includes "a library named that isn't in the current
    manifest" - deliberately not attempted here (cross-referencing a free-
    text library mention against real manifest content is its own, more
    fragile piece of NLP); keyword language alone is the cheaper, more
    reliable half of the signal and is what this checks."""
    return bool(_MIGRATION_LANGUAGE_RE.search(goal or ""))


_NUMBERED_LIST_RE = re.compile(r"(?:^|\n)\s*(?:\d+[.)]|[-*•])\s+\S", re.MULTILINE)
_SEQUENCE_KEYWORD_RE = re.compile(r"\b(?:first|then|next|finally|afterwards?)\b", re.IGNORECASE)
_ADDITIVE_LANGUAGE_RE = re.compile(r"\b(?:and\s+also|as\s+well\s+as|in\s+addition\s+to)\b", re.IGNORECASE)
_INDEPENDENT_DELIVERABLE_RE = re.compile(
    r"\b(?:independent\s+(?:deliverable|stage|service|module|artifact)|"
    r"separate\s+(?:application|service|module|artifact|entrypoint)|"
    r"multiple\s+(?:applications|services|modules|artifacts|entrypoints)|"
    r"milestones?|phase\s+\d+)\b",
    re.IGNORECASE,
)


def _detect_multi_part_structure(goal: str) -> bool:
    """Detect independent delivery topology, not acceptance-criteria length.

    Lists and sequencing language count only when the goal also names
    independent stages/artifacts. A production edit plus its supporting test,
    or several bullets describing one defect, remains one task.
    """
    text = goal or ""
    topology = bool(_INDEPENDENT_DELIVERABLE_RE.search(text))
    if not topology:
        return False
    return bool(
        _NUMBERED_LIST_RE.search(text)
        or _ADDITIVE_LANGUAGE_RE.search(text)
        or len(_SEQUENCE_KEYWORD_RE.findall(text)) >= 2
    )


_NARROW_SCOPE_RE = re.compile(r"\b(?:just|only|quick|small|simple|tiny|minor)\b", re.IGNORECASE)
_BROAD_SCOPE_RE = re.compile(
    r"\b(?:build|implement\s+full|complete|comprehensive|entire|full\s+support|end[- ]to[- ]end)\b",
    re.IGNORECASE,
)


def _detect_scope_language(goal: str) -> str:
    """Weak - a tie-breaker only, never decisive alone (per the design's own
    signal table). Returns "quick" | "full" | "none", matching the design's
    router input-template shape directly so this can feed that prompt
    unchanged once the router pass is wired in. Both patterns matching is
    treated as "none" (contradictory wording is not a real signal either
    way), not arbitrarily resolved toward one side."""
    text = goal or ""
    narrow = bool(_NARROW_SCOPE_RE.search(text))
    broad = bool(_BROAD_SCOPE_RE.search(text))
    if narrow and not broad:
        return "quick"
    if broad and not narrow:
        return "full"
    return "none"


_SECURITY_TERMS_RE = re.compile(
    r"\b(?:auth\w*|session\w*|token\w*|permission\w*|credential\w*|password\w*|"
    r"encrypt\w*|decrypt\w*|crypto\w*|jwt|oauth|csrf|xss|sql\s*injection|"
    r"access\s*control|privilege\w*|role[- ]based)\b",
    re.IGNORECASE,
)


def _detect_security_terms(goal: str) -> bool:
    """Text half of security_boundary_change - the design's own signal is
    "touches auth/session/token/permission/crypto-pattern files or symbols,
    OR the request itself names them"; this is the "names them" half.
    _security_file_signal below (over known_files) is the "touches…files"
    half - classify() ORs the two, matching the design's own OR."""
    return bool(_SECURITY_TERMS_RE.search(goal or ""))


_DEPENDENCY_TERMS_RE = re.compile(
    r"\b(?:add\s+(?:a\s+)?(?:new\s+)?dependenc\w*|install\s+\S+|"
    r"npm\s+install|pip\s+install|bundle\s+add|"
    r"add\s+.{1,40}?\b(?:library|package|gem|module)\b)\b",
    re.IGNORECASE,
)


def _detect_dependency_terms(goal: str) -> bool:
    """Text evidence of a new dependency being requested. The design's own
    dependency_change signal is "migration_language_detected, or
    adds_dependencies present" - `adds_dependencies` is a Milestone v2
    field (MA3, not built yet), so this text detector stands in for it
    until then; classify() ORs it with migration-language detection,
    matching the design's own OR shape."""
    return bool(_DEPENDENCY_TERMS_RE.search(goal or ""))


_CREATION_LANGUAGE_RE = re.compile(
    r"\b(?:add\s+(?:a\s+)?new|create\s+a\s+new|expose\s+(?:a\s+)?new|introduce\s+a\s+new|"
    r"new\s+(?:endpoint|api|interface|public))\b",
    re.IGNORECASE,
)


def _detect_creation_language(goal: str) -> bool:
    """Text half of creates_new_public_surface. The design's full signal
    also requires an LSP workspace-symbol search confirming NO existing
    match exists for what's being asked for - deliberately not attempted
    here (a live LSP server call is a meaningfully heavier dependency than
    anything else in this first cut); text-only creation language is a
    weaker but real proxy, documented as a known simplification rather than
    silently treated as the full signal."""
    return bool(_CREATION_LANGUAGE_RE.search(goal or ""))


# --- repository-file-pattern signals ----------------------------------------
# Applied against `known_files` (files the CALLER already knows are relevant
# to this request - an established-file list from milestone context, or
# files the caller has otherwise already pinned down) - NOT a full workspace
# listing, which answers a different question (see _workspace_appears_empty
# below). A request with no known_files gets file-pattern signals of False
# from this half; the text-based detectors above still apply regardless.

_PERSISTENCE_FILE_RE = re.compile(
    r"(?:migrations?[/\\]|schema\.\w+$|.*repository\.\w+$|.*dao\.\w+$|\.sql$)",
    re.IGNORECASE,
)
_SECURITY_FILE_RE = re.compile(
    r".*(?:auth|session|security|permission|credential)\w*\.\w+$",
    re.IGNORECASE,
)
_CONFIG_FILE_RE = re.compile(
    r"(?:\.ya?ml$|\.properties$|applicationContext.*\.xml$|\.env(?:\.|$))",
    re.IGNORECASE,
)
_BUILD_SYSTEM_FILE_RE = re.compile(
    r"(?:^pom\.xml$|^package\.json$|^build\.gradle\w*$|^Dockerfile\w*$|"
    r"^\.github/workflows/|^Gemfile$|^pyproject\.toml$|^setup\.(?:py|cfg)$|"
    r"^Makefile$|^CMakeLists\.txt$)",
    re.IGNORECASE,
)


def _file_pattern_signal(files: List[str], pattern: "re.Pattern") -> bool:
    return any(pattern.search(f) for f in files)


def _workspace_appears_empty(workspace_path: str) -> bool:
    """Cheap existence check for the "repo empty / brand new" triage
    short-circuit (see EngineeringTriageService.classify) - stops at the
    first file found rather than building a full listing the way
    milestones.py::_list_workspace_files does for a different consumer
    (the Milestone Planner's prompt, which genuinely needs the names).

    Two real bugs fixed 2026-08-24 (live-validation, protocol_encoder_java),
    both the same shape: this function counted Kriya's OWN files as
    "established project content."

    1. "logs"/"memory" are Kriya's own PACKAGED DEFAULT paths.logs/
       paths.memory basenames (kriya/config/default_config.yaml) - project-
       local, sibling to the real workspace root, same as this function's
       existing .kriya exclusion. Without excluding them, the mere act of
       Kriya running once (even a run that writes zero real source files -
       just its own log/trace state) made every SUBSEQUENT call to this
       function report "not empty."
    2. kriya.yaml/kriya.yml (this project's own config file) and a bare
       goal-text file passed via `kriya generate -f <file>` both sit
       directly in the workspace root by convention (see
       reference_kriya_live_validation.md's own documented shape) - NEITHER
       is established project code an extension point could ever attach
       to, but a plain "any file present" check counted both. Since a
       goal file's real name is caller-chosen (no fixed basename to
       exclude), excluded by SHAPE instead: any bare `.md` file at the
       workspace root - documentation/goal-text, never compiled/executed
       project content, the same reasoning that makes a README.md not
       count as "established code" either.

    Both bugs silently affected this function's ORIGINAL caller too
    (EngineeringTriageService.classify's own "repo empty" signal, wrong
    since before this session, never previously noticed) - not just
    plan_validation.py's newer extension_points exemption (MA7.8) that
    surfaced them. Not excluding "skills" or non-.md top-level files in
    general - unlike logs/memory/a bare .md file, those can legitimately
    be real, human-authored project content."""
    ignored_dirs = {".git", ".kriya", "__pycache__", "node_modules", "target", ".venv", "venv", "logs", "memory"}
    ignored_root_files = {"kriya.yaml", "kriya.yml"}
    if not os.path.isdir(workspace_path):
        return True
    for root, dirs, filenames in os.walk(workspace_path):
        dirs[:] = [d for d in dirs if d not in ignored_dirs]
        is_root = os.path.abspath(root) == os.path.abspath(workspace_path)
        for name in filenames:
            if is_root and (name in ignored_root_files or name.lower().endswith(".md")):
                continue
            return False
    return True


# --- best-effort dependency-graph signals -----------------------------------
# "Use existing Kriya machinery where possible" (MA1.2's own scope) - but
# kriya/config/config.py's own auto_index_missing_dependency_graph comment
# documents a real, confirmed gap: index_repository() only ever runs from
# the explicit `kriya analyze` CLI command, so a workspace's
# dependency_graph.db is frequently empty. DependencyGraph.has_indexed_files()
# is checked FIRST, every time, so an empty/missing graph degrades honestly
# to "signal unavailable" (recorded in deterministic_signals) rather than
# silently reporting 0 downstream references as if that were a confirmed
# answer - same "say plainly nothing was actually checked" principle
# CLAUDE.md documents for PolymorphicValidator's unknown-stack case.

_SYMBOL_TOKEN_RE = re.compile(r"`([A-Za-z_][\w.]*)`")
_CAMEL_CASE_RE = re.compile(r"\b[A-Z][a-zA-Z0-9]*[a-z][A-Za-z0-9]*\b")
_SHARED_ENTRYPOINT_REFERENCE_THRESHOLD = 3


def _extract_named_symbols(goal: str, max_symbols: int = 8) -> List[str]:
    """Heuristic candidate-identifier extraction from free text - backtick-
    quoted tokens first (explicit, high-confidence: `` `RecipeRepository` ``),
    then bare CamelCase words as a weaker fallback. Not NLP, just enough to
    give the dependency-graph lookup below something concrete to check;
    false negatives here just mean a request's real impact is undercounted,
    never a fabricated positive."""
    text = goal or ""
    quoted = _SYMBOL_TOKEN_RE.findall(text)
    if quoted:
        return quoted[:max_symbols]
    return _CAMEL_CASE_RE.findall(text)[:max_symbols]


def _basenames_as_symbols(planned_files: List[str], max_symbols: int = 8) -> List[str]:
    """Cheap proxy for "what symbol might this planned file define," for
    MA2.4's post-Architect recomputation - the file doesn't exist on disk
    yet at Architect time (Developer writes real content later), so there
    is nothing to parse a class/function name out of. A file's basename
    (the Java/most-OOP-language convention: a filename matches its primary
    class name) is the best available signal without guessing at content
    that doesn't exist yet - a false negative here (a language/convention
    where this doesn't hold) just means an undercounted downstream-
    reference signal, never a fabricated positive."""
    names: List[str] = []
    for path in planned_files[:max_symbols]:
        base = os.path.basename(path)
        stem = base.split(".")[0] if "." in base else base
        if stem:
            names.append(stem)
    return names


def _dependency_graph_signals(kernel: Optional[Any], symbols: List[str]) -> Dict[str, Any]:
    """Returns {"available": bool, "downstream_references": int,
    "symbols_impacted": int, "shared_entrypoint": bool}. `available` is
    False (with the numeric fields at 0 / shared_entrypoint False) whenever
    no kernel was supplied, the graph can't be opened, or it has never been
    indexed for this workspace - never raises, since a triage classifier
    failing an entire generation run over a missing/absent index would be a
    bad trade for what is meant to be a lightweight, best-effort signal.

    `symbols` is caller-supplied candidate identifiers to check - classify()
    passes goal-text-extracted candidates (_extract_named_symbols);
    recompute_from_files (MA2.4) passes planned-file basenames
    (_basenames_as_symbols) instead, since that call has real file paths
    but no goal text to re-derive symbols from."""
    result: Dict[str, Any] = {
        "available": False,
        "downstream_references": 0,
        "symbols_impacted": 0,
        "shared_entrypoint": False,
    }
    if kernel is None:
        return result

    try:
        memory_path = kernel.config.paths.memory
        db_path = os.path.join(memory_path, "dependency_graph.db")
        if not os.path.exists(db_path):
            return result

        from kriya.analyzer.graph import DependencyGraph

        graph = DependencyGraph(db_path)
        try:
            if not graph.has_indexed_files():
                return result

            total_callers = 0
            symbols_found = 0
            max_callers_for_one_symbol = 0
            for symbol in symbols:
                callers = graph.get_callers(symbol)
                if callers:
                    symbols_found += 1
                    total_callers += len(callers)
                    max_callers_for_one_symbol = max(max_callers_for_one_symbol, len(callers))

            result["available"] = True
            result["downstream_references"] = total_callers
            result["symbols_impacted"] = symbols_found
            result["shared_entrypoint"] = max_callers_for_one_symbol >= _SHARED_ENTRYPOINT_REFERENCE_THRESHOLD
            return result
        finally:
            graph.close()
    except Exception as e:
        logger.debug(f"Dependency-graph triage signals unavailable, degrading honestly: {e}")
        return result


def _floor_kind(multi_part_structure: bool, touches_shared_entrypoint: bool, creates_new_public_surface: bool) -> ChangeKind:
    """The design's own deterministic floor - what a genuinely conflicting-
    signals case falls back to when no LLM Router pass is available (see
    this module's docstring). `milestone` takes priority over `enhancement`
    over `task`, matching the design's stated ordering exactly."""
    if multi_part_structure:
        return ChangeKind.MILESTONE
    if touches_shared_entrypoint or creates_new_public_surface:
        return ChangeKind.ENHANCEMENT
    return ChangeKind.TASK


_ENHANCEMENT_LANGUAGE_RE = re.compile(
    r"\b(?:enhance|extend|augment|add|include|expose|return|support)\w*\b"
    r"[\s\S]{0,120}\b(?:existing|current|already|endpoint|response|service|system|behavior|feature)\b"
    r"|\b(?:existing|current|already)\b[\s\S]{0,120}"
    r"\b(?:enhance|extend|augment|add|include|expose|return|support)\w*\b",
    re.IGNORECASE,
)


def _detect_enhancement_language(goal: str) -> bool:
    """Detect additive behavior requested against an existing system."""
    return bool(_ENHANCEMENT_LANGUAGE_RE.search(goal or ""))


@dataclass
class EngineeringTriageService:
    """Assembles a fresh EngineeringRoute for one generation request.
    `kernel` is optional and only used for the best-effort dependency-graph
    signals above - a bare `EngineeringTriageService()` works standalone for
    unit testing every other signal without a live kernel/config/DB. Not
    itself an "agent" (kriya/agents/agent.py) - no LLM call happens in this
    slice; see the module docstring for why and what changes once one is
    wired in."""

    kernel: Optional[Any] = None

    async def classify(
        self,
        goal: str,
        workspace_path: str,
        *,
        known_files: Optional[List[str]] = None,
    ) -> EngineeringRoute:
        text = goal or ""
        files = list(known_files or [])
        reason_codes: List[str] = []

        repro_shape = _detect_repro_shape(text)
        migration_language = _detect_migration_language(text)
        multi_part_structure = _detect_multi_part_structure(text)
        scope_language = _detect_scope_language(text)
        security_terms = _detect_security_terms(text)
        dependency_terms = _detect_dependency_terms(text)
        creation_language = _detect_creation_language(text)
        enhancement_language = _detect_enhancement_language(text)
        repo_appears_empty = _workspace_appears_empty(workspace_path)

        graph_signals = _dependency_graph_signals(self.kernel, _extract_named_symbols(text))

        security_file_signal = _file_pattern_signal(files, _SECURITY_FILE_RE)
        persistence_file_signal = _file_pattern_signal(files, _PERSISTENCE_FILE_RE)
        config_file_signal = _file_pattern_signal(files, _CONFIG_FILE_RE)
        build_system_file_signal = _file_pattern_signal(files, _BUILD_SYSTEM_FILE_RE)

        touches_shared_entrypoint = bool(graph_signals["shared_entrypoint"])
        creates_new_public_surface = creation_language

        # --- kind: fixed decision order, short-circuits before anything else ---
        if repo_appears_empty:
            kind = ChangeKind.MILESTONE
            reason_codes.append("repo_appears_empty")
        elif repro_shape:
            kind = ChangeKind.TASK
            reason_codes.append("repro_shape_detected")
        elif migration_language:
            kind = ChangeKind.REFACTOR
            reason_codes.append("migration_language_detected")
        elif enhancement_language:
            kind = ChangeKind.ENHANCEMENT
            reason_codes.append("existing_system_enhancement_detected")
        else:
            kind = _floor_kind(multi_part_structure, touches_shared_entrypoint, creates_new_public_surface)
            if multi_part_structure:
                reason_codes.append("multi_part_structure_detected")
            if touches_shared_entrypoint:
                reason_codes.append("touches_shared_entrypoint")
            if creates_new_public_surface:
                reason_codes.append("creates_new_public_surface")
            if kind == ChangeKind.TASK and not (multi_part_structure or touches_shared_entrypoint or creates_new_public_surface):
                reason_codes.append("no_signals_fired_defaulted_to_task")

        # --- impact vector: repo signals OR'd with text signals per-component ---
        impact = ImpactVector(
            files_touched=len(files),
            symbols_impacted=int(graph_signals["symbols_impacted"]),
            downstream_references=int(graph_signals["downstream_references"]),
            public_contract_change=creates_new_public_surface,
            persistence_change=persistence_file_signal,
            security_boundary_change=security_terms or security_file_signal,
            dependency_change=migration_language or dependency_terms,
            configuration_change=config_file_signal,
            build_system_change=build_system_file_signal,
            shared_entrypoint_change=touches_shared_entrypoint,
        )

        risk = determine_risk_class(impact)
        reason_codes.extend(risk_reason_codes(impact))
        weight = determine_execution_weight(kind, risk)

        deterministic_signals: Dict[str, Any] = {
            "repro_shape": repro_shape,
            "migration_language": migration_language,
            "multi_part_structure": multi_part_structure,
            "scope_language": scope_language,
            "security_terms": security_terms,
            "dependency_terms": dependency_terms,
            "enhancement_language": enhancement_language,
            "creation_language": creation_language,
            "repo_appears_empty": repo_appears_empty,
            "known_files_count": len(files),
            "dependency_graph_available": graph_signals["available"],
        }

        return EngineeringRoute(
            kind=kind,
            impact=impact,
            initial_risk_class=risk,
            current_risk_class=risk,
            max_observed_risk_class=risk,
            execution_weight=weight,
            deterministic_signals=deterministic_signals,
            reason_codes=reason_codes,
            router_used=False,
            router_confidence=None,
            initial_impact=impact,
            initial_execution_weight=weight,
        )

    async def recompute_from_files(
        self,
        *,
        route: EngineeringRoute,
        workspace_path: str,
        planned_files: List[str],
    ) -> EngineeringRoute:
        """MA2.4 - recomputes risk from Architect's REAL planned file list,
        the first point in the pipeline where Kriya knows more than the
        original request's text/known_files could tell classify() above.
        Reuses the EXACT SAME deterministic file-pattern machinery
        classify() already uses (MA1.2) - no new heuristics, no LLM call,
        matching this codebase's established "never trust an LLM's guess
        when Kriya can compute deterministically" pattern.

        Detects, directly against planned_files:
          - pom.xml/build.gradle/package.json/etc -> build_system_change
            AND dependency_change (touching a build manifest at all is
            itself evidence of a dependency change, per the design's own
            "pom.xml/build.gradle/package.json -> build_system_change /
            dependency_change" signal mapping)
          - application*.yml/.properties/XML config -> configuration_change
          - auth/session/security/permission/credential-pattern filenames
            -> security_boundary_change
          - repository/DAO/schema/migration-pattern filenames ->
            persistence_change
          - a planned file's basename clearing the shared-reference
            threshold in the dependency graph (best-effort, same honest
            degradation as classify()'s own graph signals) ->
            shared_entrypoint_change

        public_contract_change is carried over from route.impact rather
        than re-derived - there is no fresh goal text here to re-check for
        creation language, and silently resetting a true signal already
        found at initial classification to False would be a false
        negative, not a more accurate answer.

        Always returns via EngineeringRoute.with_recomputed_risk (MA2.2),
        so max_observed_risk_class can only move up regardless of what
        this recomputation finds, and every new reason code is tagged
        `post_architect:` so the audit trail can distinguish initial
        triage evidence from this later recomputation's own findings."""
        security_file_signal = _file_pattern_signal(planned_files, _SECURITY_FILE_RE)
        persistence_file_signal = _file_pattern_signal(planned_files, _PERSISTENCE_FILE_RE)
        config_file_signal = _file_pattern_signal(planned_files, _CONFIG_FILE_RE)
        build_system_file_signal = _file_pattern_signal(planned_files, _BUILD_SYSTEM_FILE_RE)

        graph_signals = _dependency_graph_signals(self.kernel, _basenames_as_symbols(planned_files))

        new_impact = ImpactVector(
            files_touched=len(planned_files),
            symbols_impacted=int(graph_signals["symbols_impacted"]),
            downstream_references=int(graph_signals["downstream_references"]),
            public_contract_change=route.impact.public_contract_change,
            persistence_change=persistence_file_signal,
            security_boundary_change=security_file_signal,
            dependency_change=build_system_file_signal,
            configuration_change=config_file_signal,
            build_system_change=build_system_file_signal,
            shared_entrypoint_change=bool(graph_signals["shared_entrypoint"]),
        )

        new_risk = determine_risk_class(new_impact)
        new_reason_codes = [f"post_architect:{code}" for code in risk_reason_codes(new_impact)]
        return route.with_recomputed_risk(new_risk, new_impact, new_reason_codes)
