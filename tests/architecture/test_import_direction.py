"""Import direction between logical modules (full modularization, slice A1;
handover/MODULARIZATION/DEPENDENCY-RULES.md §1-§3).

Every kriya import edge, top-level AND function-local, is resolved to the
logical modules of ``module_map.MODULES`` and checked against the one-way
matrix (``ALLOWED`` + the footnote restrictions ``RESTRICTED``); a
cross-module edge must also land on one of the target's ``public`` contract
modules. A violation not in ``ALLOWLIST`` fails. ``ALLOWLIST`` is MEASURED
(every entry is an edge present when it was written) and may only shrink:
an entry that is no longer a violation fails as stale, every entry names the
slice that removes it (SLICE-PLAN.md), and FOUNDATION is never listed.

Removal slices were assigned by rule, target first: workspace identity B1;
triage/process_profile C2; edit_safety/file_integrity/terminal_commit M1;
checkpoint D2 (kup.execution B3); runtime command approval from kup B3;
verification_authority from kup B2; run_coordinator from provider/execution
H2; the DEPENDENCY-RULES §3 rows by their named slice; importers of the
build_info/tomlcompat/__version__ facades P1; any other kup edge O1; any
other CLI edge O2 (production_doctor N1). Everything else is a public-
surface or declared-owner edge that DEPENDENCY-RULES §3 does not enumerate:
P2 ("allowlists empty"), until a slice that owns it claims it.
"""
from __future__ import annotations

import os
from typing import Dict, Iterable, Mapping, Optional, Tuple

import pytest

from . import module_map as mm

SLICES = frozenset(
    [f"A{i}" for i in range(1, 4)] + [f"B{i}" for i in range(1, 5)] + [f"C{i}" for i in range(1, 4)]
    + [f"D{i}" for i in range(1, 4)] + [f"E{i}" for i in range(1, 15)] + [f"F{i}" for i in range(1, 6)]
    + [f"G{i}" for i in range(1, 6)] + [f"H{i}" for i in range(1, 6)] + [f"I{i}" for i in range(1, 5)]
    + ["J1", "J2", "K1", "K2", "L1", "L2", "M1", "M2", "M3", "N1", "N2", "O1", "O2", "O3", "P1", "P2", "Q1", "Q2"])
DONE_SLICES = frozenset({"A1", "A2", "A3", "B1", "B2"})

# CURRENT-DEPENDENCY-MAP.md §3, the back-edges into kriya/workflow that close
# the big cycle (TRACED to the import line there; MEASURED here): (source,
# target, lazy). While an edge exists it must be intra-module by declaration
# or allowlisted; a removed edge must be deleted from this table (shrink-only).
BACK_EDGES: Tuple[Tuple[str, str, bool], ...] = (
    ("kriya.control.persistence", "kriya.workflow.edit_safety", False),
    ("kriya.control.persistence", "kriya.workflow.file_integrity", False),
    ("kriya.control.control_store", "kriya.workflow.edit_safety", False),
    ("kriya.control.decisions", "kriya.workflow.edit_safety", False),
    ("kriya.control.commit_state", "kriya.workflow.edit_safety", False),
    ("kriya.control.recovery", "kriya.workflow.edit_safety", False),
    ("kriya.control.recovery", "kriya.workflow.contract_lifecycle", True),
    ("kriya.control.retention", "kriya.workflow.checkpoint", False),
    ("kriya.control.retention", "kriya.workflow.edit_safety", False),
    ("kriya.control.retention", "kriya.workflow.milestone_completion", False),
    ("kriya.control.launch_approval", "kriya.workflow.edit_safety", False),
    ("kriya.control.launch_approval", "kriya.workflow.runtime_command_approval", True),
    ("kriya.control.state", "kriya.workflow.process_profile", False),
    ("kriya.control.state", "kriya.workflow.triage", False),
    ("kriya.control.telemetry", "kriya.workflow.checkpoint", False),
    ("kriya.control.telemetry", "kriya.workflow.context_package", False),
    ("kriya.control.run_coordinator", "kriya.workflow.checkpoint", True),
    ("kriya.policy.filesystem", "kriya.workflow.edit_safety", False),
    ("kriya.policy.model", "kriya.workflow.process_profile", False),
    ("kriya.policy.model", "kriya.workflow.triage", False),
    ("kriya.policy.execution", "kriya.workflow.triage", False),
    ("kriya.static_analysis.operator_scan", "kriya.workflow.edit_safety", False),
    ("kriya.static_analysis.operator_scan", "kriya.workflow.file_integrity", False),
    ("kriya.static_analysis.scope", "kriya.workflow.file_integrity", False),
    ("kriya.static_analysis.service", "kriya.workflow.terminal_commit", True),
    ("kriya.kup.runtime_command_ops", "kriya.workflow.runtime_command_approval", False),
    ("kriya.kup.execution", "kriya.workflow.checkpoint", True),
    ("kriya.knowledge.staging", "kriya.workflow.skill_extraction", False),
    ("kriya.core.model_qualification", "kriya.workflow.file_integrity", True),
    ("kriya.analyzer.analyzer", "kriya.workflow.checkpoint", True),
    ("kriya.capabilities.gradle", "kriya.workflow.jvm_module_ownership", True),
)

# (source module, target module) -> (removal slice, reason). Generated from
# the measured violations at slice A1 and edited only by deleting entries.
_C2 = "risk/route vocabulary moves to policy.risk"
_C3 = "approval helpers move to policy.approval / egress takes the client identity"
_M1 = "file write/commit mechanism moves to REPOSITORY"
_D2 = "checkpoint/trace/history owned by STATE; kernel inputs passed as parameters"
_D3 = "env/home/state-path resolution through config.environment / ConfigService"
_B2_KUP = ("B2 moved the read use case into an APPLICATION service; until the KriyaClient boundary exists the KUP adapter calls the "
           "service directly (B4: run_kup_operation dispatches through LocalKriyaClient; O1: every kup/*_ops.py maps over the client)")
_B2_MOVED = "moved with the B2 use case from kup into APPLICATION (count-neutral); the target is not on its module's public surface"
_B3 = "run/approval/review use case moves into an APPLICATION service"
_H2 = "deadline arrives in the request, not read from the run lease"
_J1 = "skill extraction moves to SKILLS (public registry names)"
_N1 = "qualification moves to kriya/qualification (L4)"
_L1 = "jvm module ownership is EXECUTION-owned (moves to capabilities/)"
_K1 = "tool registry composition moves to TOOLBOX"
_P1 = "import facade; the importer switches to kriya.foundation when facades are removed"
_O1 = "KUP adapter reaches the product through KriyaClient only"
_O2 = "CLI keeps composition names only; command bodies go through APPLICATION"
_P2 = "not enumerated in DEPENDENCY-RULES §3: public-surface or declared-owner edge, no owning slice yet"
ALLOWLIST: Dict[Tuple[str, str], Tuple[str, str]] = {
    ("kriya.agents.agent", "kriya.core.completion"): ("P2", _P2),  # non-public PROVIDER module
    ("kriya.agents.agent", "kriya.core.model_capabilities"): ("P2", _P2),  # non-public PROVIDER module
    ("kriya.agents.agent", "kriya.core.model_runtime"): ("P2", _P2),  # non-public PROVIDER module
    ("kriya.agents.agent", "kriya.core.role_metrics"): ("P2", _P2),  # non-public PROVIDER module
    ("kriya.agents.agent", "kriya.core.token_budget"): ("P2", _P2),  # non-public PROVIDER module
    ("kriya.analyzer.analyzer", "kriya.agents.extractor"): ("P2", _P2),  # direction RETRIEVAL -> KERNEL
    ("kriya.analyzer.analyzer", "kriya.skills.skill"): ("P2", _P2),  # direction RETRIEVAL -> SKILLS
    ("kriya.analyzer.analyzer", "kriya.workflow.checkpoint"): ("D2", _D2),  # direction RETRIEVAL -> KERNEL
    ("kriya.analyzer.graph", "kriya.core.db"): ("P2", _P2),  # non-public FOUNDATION module
    ("kriya.application.catalog", "kriya.tools.service"): ("K1", _B2_MOVED + " (K1: tools/service.py becomes toolbox.catalog)"),  # non-public TOOLBOX module (was kup.catalog_ops, O1)
    ("kriya.application.history", "kriya.policy.telemetry"): ("P2", _B2_MOVED),  # non-public AUTHORITY module (was kup.application_outcome, O1)
    ("kriya.application.history", "kriya.workflow.verification_authority"): ("P2", _B2_MOVED),  # non-public KERNEL module (was kup.application_outcome, B2)
    ("kriya.application.project", "kriya.control.recovery"): ("P2", _B2_MOVED),  # non-public STATE module (was kup.cli_ops + kup.workspace_ops, O1)
    ("kriya.application.project", "kriya.tools.git_metadata"): ("M2", _B2_MOVED + " (M2: git_metadata moves to kriya/repository)"),  # non-public EXECUTION module (was kup.workspace_ops, O1)
    ("kriya.capabilities.gradle", "kriya.workflow.jvm_module_ownership"): ("L1", _L1),  # direction EXECUTION -> KERNEL
    ("kriya.cli", "kriya.agents"): ("O2", _O2),  # direction ADAPTER-CLI -> KERNEL
    ("kriya.cli", "kriya.agents.response_protocol"): ("O2", _O2),  # direction ADAPTER-CLI -> KERNEL
    ("kriya.cli", "kriya.analyzer"): ("O2", _O2),  # direction ADAPTER-CLI -> RETRIEVAL
    ("kriya.cli", "kriya.analyzer.analyzer"): ("O2", _O2),  # direction ADAPTER-CLI -> RETRIEVAL
    ("kriya.cli", "kriya.analyzer.java_members"): ("O2", _O2),  # direction ADAPTER-CLI -> RETRIEVAL
    ("kriya.cli", "kriya.build_info"): ("P1", _P1),  # non-public FOUNDATION module
    ("kriya.cli", "kriya.config.authority"): ("O2", _O2),  # direction ADAPTER-CLI -> AUTHORITY
    ("kriya.cli", "kriya.config.authority_approval"): ("O2", _O2),  # direction ADAPTER-CLI -> AUTHORITY
    ("kriya.cli", "kriya.control.commit_state"): ("O2", _O2),  # direction ADAPTER-CLI -> STATE
    ("kriya.cli", "kriya.control.launch"): ("O2", _O2),  # direction ADAPTER-CLI -> STATE
    ("kriya.cli", "kriya.control.launch_approval"): ("O2", _O2),  # direction ADAPTER-CLI -> AUTHORITY
    ("kriya.cli", "kriya.control.persistence"): ("O2", _O2),  # direction ADAPTER-CLI -> STATE
    ("kriya.cli", "kriya.control.recovery"): ("O2", _O2),  # direction ADAPTER-CLI -> STATE
    ("kriya.cli", "kriya.control.retention"): ("O2", _O2),  # direction ADAPTER-CLI -> STATE
    ("kriya.cli", "kriya.control.run_coordinator"): ("O2", _O2),  # direction ADAPTER-CLI -> STATE
    ("kriya.cli", "kriya.control.run_ownership"): ("O2", _O2),  # direction ADAPTER-CLI -> STATE
    ("kriya.cli", "kriya.control.run_record"): ("O2", _O2),  # direction ADAPTER-CLI -> STATE
    ("kriya.cli", "kriya.core"): ("O2", _O2),  # direction ADAPTER-CLI -> KERNEL
    ("kriya.cli", "kriya.core.attempt_evidence.candidate_export"): ("O2", _O2),  # direction ADAPTER-CLI -> EVIDENCE
    ("kriya.cli", "kriya.core.attempt_evidence.explain"): ("O2", _O2),  # direction ADAPTER-CLI -> EVIDENCE
    ("kriya.cli", "kriya.core.attempt_evidence.leak_check"): ("O2", _O2),  # direction ADAPTER-CLI -> EVIDENCE
    ("kriya.cli", "kriya.core.attempt_evidence.reader"): ("O2", _O2),  # direction ADAPTER-CLI -> EVIDENCE
    ("kriya.cli", "kriya.core.attempt_evidence.retention"): ("O2", _O2),  # direction ADAPTER-CLI -> EVIDENCE
    ("kriya.cli", "kriya.core.db"): ("O2", _O2),  # non-public FOUNDATION module
    ("kriya.cli", "kriya.core.execution_environment"): ("O2", _O2),  # direction ADAPTER-CLI -> PROVIDER
    ("kriya.cli", "kriya.core.inference_runtime"): ("O2", _O2),  # direction ADAPTER-CLI -> PROVIDER
    ("kriya.cli", "kriya.core.inference_settings"): ("O2", _O2),  # direction ADAPTER-CLI -> PROVIDER
    ("kriya.cli", "kriya.core.logging_setup"): ("O2", _O2),  # non-public CONFIG module
    ("kriya.cli", "kriya.core.model_certification"): ("O2", _O2),  # direction ADAPTER-CLI -> QUALIFICATION
    ("kriya.cli", "kriya.core.model_qualification"): ("O2", _O2),  # direction ADAPTER-CLI -> QUALIFICATION
    ("kriya.cli", "kriya.core.model_routing"): ("O2", _O2),  # direction ADAPTER-CLI -> PROVIDER
    ("kriya.cli", "kriya.core.model_runtime"): ("O2", _O2),  # direction ADAPTER-CLI -> PROVIDER
    ("kriya.cli", "kriya.core.provider_contract"): ("O2", _O2),  # direction ADAPTER-CLI -> PROVIDER
    ("kriya.cli", "kriya.core.role_metrics"): ("O2", _O2),  # direction ADAPTER-CLI -> PROVIDER
    ("kriya.cli", "kriya.core.state_paths"): ("O2", _O2),  # direction ADAPTER-CLI -> STATE
    ("kriya.cli", "kriya.core.trace"): ("O2", _O2),  # direction ADAPTER-CLI -> STATE
    ("kriya.cli", "kriya.knowledge.rubric"): ("O2", _O2),  # direction ADAPTER-CLI -> KNOWLEDGE
    ("kriya.cli", "kriya.knowledge.scaffold"): ("O2", _O2),  # direction ADAPTER-CLI -> KNOWLEDGE
    ("kriya.cli", "kriya.knowledge.staging"): ("O2", _O2),  # direction ADAPTER-CLI -> KNOWLEDGE
    ("kriya.cli", "kriya.kup.execution"): ("O2", _O2),  # non-public ADAPTER-KUP module
    ("kriya.cli", "kriya.mcp.invocation_approval"): ("O2", _O2),  # direction ADAPTER-CLI -> AUTHORITY
    ("kriya.cli", "kriya.mcp.mcp"): ("O2", _O2),  # direction ADAPTER-CLI -> TOOLBOX
    ("kriya.cli", "kriya.memory.embedding"): ("O2", _O2),  # direction ADAPTER-CLI -> RETRIEVAL
    ("kriya.cli", "kriya.memory.learned_knowledge"): ("O2", _O2),  # direction ADAPTER-CLI -> KNOWLEDGE
    ("kriya.cli", "kriya.memory.vector"): ("O2", _O2),  # direction ADAPTER-CLI -> RETRIEVAL
    ("kriya.cli", "kriya.metrics.adjudication"): ("O2", _O2),  # non-public METRICS module
    ("kriya.cli", "kriya.metrics.evidence"): ("O2", _O2),  # non-public METRICS module
    ("kriya.cli", "kriya.metrics.thresholds"): ("O2", _O2),  # non-public METRICS module
    ("kriya.cli", "kriya.plugins.plugin"): ("O2", _O2),  # direction ADAPTER-CLI -> TOOLBOX
    ("kriya.cli", "kriya.prompt"): ("O2", _O2),  # direction ADAPTER-CLI -> KERNEL
    ("kriya.cli", "kriya.skills"): ("O2", _O2),  # direction ADAPTER-CLI -> SKILLS
    ("kriya.cli", "kriya.skills.skill"): ("O2", _O2),  # direction ADAPTER-CLI -> SKILLS
    ("kriya.cli", "kriya.static_analysis.doctor"): ("O2", _O2),  # direction ADAPTER-CLI -> EXECUTION
    ("kriya.cli", "kriya.static_analysis.operator_scan"): ("O2", _O2),  # direction ADAPTER-CLI -> EXECUTION
    ("kriya.cli", "kriya.static_analysis.service"): ("O2", _O2),  # direction ADAPTER-CLI -> EXECUTION
    ("kriya.cli", "kriya.static_analysis.waivers"): ("O2", _O2),  # direction ADAPTER-CLI -> EXECUTION
    ("kriya.cli", "kriya.tools.knowledge"): ("O2", _O2),  # direction ADAPTER-CLI -> KNOWLEDGE
    ("kriya.cli", "kriya.tools.lsp"): ("O2", _O2),  # direction ADAPTER-CLI -> EXECUTION
    ("kriya.cli", "kriya.tools.validate"): ("O2", _O2),  # direction ADAPTER-CLI -> EXECUTION
    ("kriya.cli", "kriya.tools.web"): ("O2", _O2),  # direction ADAPTER-CLI -> TOOLBOX
    ("kriya.cli", "kriya.workflow"): ("O2", _O2),  # direction ADAPTER-CLI -> KERNEL
    ("kriya.cli", "kriya.workflow.acceptance_approval"): ("O2", _O2),  # direction ADAPTER-CLI -> AUTHORITY
    ("kriya.cli", "kriya.workflow.acceptance_oracle"): ("O2", _O2),  # direction ADAPTER-CLI -> KERNEL
    ("kriya.cli", "kriya.workflow.authority_bundle"): ("O2", _O2),  # direction ADAPTER-CLI -> AUTHORITY
    ("kriya.cli", "kriya.workflow.checkpoint"): ("D2", _D2),  # direction ADAPTER-CLI -> KERNEL
    ("kriya.cli", "kriya.workflow.context_budget"): ("O2", _O2),  # direction ADAPTER-CLI -> KERNEL
    ("kriya.cli", "kriya.workflow.context_certification"): ("O2", _O2),  # direction ADAPTER-CLI -> QUALIFICATION
    ("kriya.cli", "kriya.workflow.failure_reporting"): ("O2", _O2),  # direction ADAPTER-CLI -> KERNEL
    ("kriya.cli", "kriya.workflow.milestones"): ("O2", _O2),  # direction ADAPTER-CLI -> KERNEL
    ("kriya.cli", "kriya.workflow.proposal_promotion"): ("O2", _O2),  # direction ADAPTER-CLI -> KERNEL
    ("kriya.cli", "kriya.workflow.proposal_store"): ("O2", _O2),  # direction ADAPTER-CLI -> KERNEL
    ("kriya.cli", "kriya.workflow.requirement_contract"): ("O2", _O2),  # direction ADAPTER-CLI -> KERNEL
    ("kriya.cli", "kriya.workflow.requirement_disposition"): ("O2", _O2),  # direction ADAPTER-CLI -> KERNEL
    ("kriya.cli", "kriya.workflow.review_context"): ("O2", _O2),  # direction ADAPTER-CLI -> KERNEL
    ("kriya.cli", "kriya.workflow.runtime_command_approval"): ("C3", _C3),  # direction ADAPTER-CLI -> AUTHORITY
    ("kriya.cli", "kriya.workflow.untrusted_context"): ("O2", _O2),  # direction ADAPTER-CLI -> KERNEL
    ("kriya.cli", "kriya.workflow.workflow_controller"): ("O2", _O2),  # direction ADAPTER-CLI -> KERNEL
    ("kriya.cli", "kriya.workflow.worktree"): ("O2", _O2),  # direction ADAPTER-CLI -> KERNEL
    ("kriya.code_intel.store", "kriya.core.db"): ("P2", _P2),  # non-public FOUNDATION module
    ("kriya.config.config", "kriya.config.authority_approval"): ("P2", _P2),  # non-public AUTHORITY module
    ("kriya.config.config", "kriya.core.state_paths"): ("D3", _D3),  # direction CONFIG -> STATE
    ("kriya.config.config", "kriya.static_analysis.registry"): ("D3", _D3),  # direction CONFIG -> EXECUTION
    ("kriya.control.artifacts", "kriya.core.tomlcompat"): ("P1", _P1),  # non-public FOUNDATION module
    ("kriya.control.commit_state", "kriya.workflow.edit_safety"): ("M1", _M1),  # direction STATE -> KERNEL
    ("kriya.control.control_store", "kriya.workflow.edit_safety"): ("M1", _M1),  # direction STATE -> KERNEL
    ("kriya.control.decisions", "kriya.workflow.edit_safety"): ("M1", _M1),  # direction STATE -> KERNEL
    ("kriya.control.launch", "kriya.control.launch_approval"): ("P2", _P2),  # non-public AUTHORITY module
    ("kriya.control.launch_approval", "kriya.control.launch"): ("P2", _P2),  # direction AUTHORITY -> STATE
    ("kriya.control.launch_approval", "kriya.control.run_ownership"): ("P2", _P2),  # direction AUTHORITY -> STATE
    ("kriya.control.launch_approval", "kriya.workflow.edit_safety"): ("M1", _M1),  # direction AUTHORITY -> KERNEL
    ("kriya.control.persistence", "kriya.workflow.edit_safety"): ("M1", _M1),  # direction STATE -> KERNEL
    ("kriya.control.persistence", "kriya.workflow.file_integrity"): ("M1", _M1),  # direction STATE -> KERNEL
    ("kriya.control.recovery", "kriya"): ("P1", _P1),  # non-public FOUNDATION module
    ("kriya.control.recovery", "kriya.workflow.contract_lifecycle"): ("D2", _D2),  # direction STATE -> KERNEL
    ("kriya.control.recovery", "kriya.workflow.edit_safety"): ("M1", _M1),  # direction STATE -> KERNEL
    ("kriya.control.retention", "kriya.workflow.checkpoint"): ("D2", _D2),  # direction STATE -> KERNEL
    ("kriya.control.retention", "kriya.workflow.edit_safety"): ("M1", _M1),  # direction STATE -> KERNEL
    ("kriya.control.retention", "kriya.workflow.milestone_completion"): ("D2", _D2),  # direction STATE -> KERNEL
    ("kriya.control.run_coordinator", "kriya.core.logging_setup"): ("P2", _P2),  # non-public CONFIG module
    ("kriya.control.run_coordinator", "kriya.workflow.checkpoint"): ("D2", _D2),  # direction STATE -> KERNEL
    ("kriya.control.state", "kriya.workflow.process_profile"): ("C2", _C2),  # direction STATE -> KERNEL
    ("kriya.control.state", "kriya.workflow.triage"): ("C2", _C2),  # direction STATE -> KERNEL
    ("kriya.control.telemetry", "kriya.workflow.checkpoint"): ("D2", _D2),  # direction STATE -> KERNEL
    ("kriya.control.telemetry", "kriya.workflow.context_package"): ("D2", _D2),  # direction STATE -> KERNEL
    ("kriya.core", "kriya.core.registry"): ("P2", _P2),  # non-public TOOLBOX module
    ("kriya.core.execution_environment", "kriya.platform.host_properties"): ("P2", _P2),  # non-public PLATFORM module
    ("kriya.core.kernel", "kriya.core.registry"): ("P2", _P2),  # non-public TOOLBOX module
    ("kriya.core.kernel", "kriya.mcp"): ("K1", _K1),  # non-public TOOLBOX module
    ("kriya.core.llm", "kriya.control.run_coordinator"): ("H2", _H2),  # direction PROVIDER -> STATE
    ("kriya.core.llm", "kriya.core.model_qualification"): ("P2", _P2),  # direction PROVIDER -> QUALIFICATION
    ("kriya.core.llm", "kriya.policy.execution"): ("P2", _P2),  # direction PROVIDER -> AUTHORITY
    ("kriya.core.llm", "kriya.policy.model"): ("P2", _P2),  # direction PROVIDER -> AUTHORITY
    ("kriya.core.logging_setup", "kriya.control.persistence"): ("D3", _D3),  # direction CONFIG -> STATE
    ("kriya.core.model_capabilities", "kriya.core.model_qualification"): ("P2", _P2),  # direction PROVIDER -> QUALIFICATION
    ("kriya.core.model_qualification", "kriya"): ("N1", _N1),  # non-public FOUNDATION module
    ("kriya.core.model_qualification", "kriya.agents.response_protocol"): ("N1", _N1),  # non-public KERNEL module
    ("kriya.core.model_qualification", "kriya.core.completion"): ("N1", _N1),  # non-public PROVIDER module
    ("kriya.core.model_qualification", "kriya.core.execution_environment"): ("N1", _N1),  # non-public PROVIDER module
    ("kriya.core.model_qualification", "kriya.core.file_stamp"): ("N1", _N1),  # non-public FOUNDATION module
    ("kriya.core.model_qualification", "kriya.core.inference_settings"): ("N1", _N1),  # non-public PROVIDER module
    ("kriya.core.model_qualification", "kriya.core.model_capabilities"): ("N1", _N1),  # non-public PROVIDER module
    ("kriya.core.model_qualification", "kriya.core.model_runtime"): ("N1", _N1),  # non-public PROVIDER module
    ("kriya.core.model_qualification", "kriya.core.token_budget"): ("N1", _N1),  # non-public PROVIDER module
    ("kriya.core.model_qualification", "kriya.workflow.file_integrity"): ("N1", _N1),  # non-public KERNEL module
    ("kriya.core.model_routing", "kriya.core.model_qualification"): ("P2", _P2),  # direction PROVIDER -> QUALIFICATION
    ("kriya.core.model_routing", "kriya.core.state_paths"): ("P2", _P2),  # direction PROVIDER -> STATE
    ("kriya.core.model_runtime", "kriya.core.state_paths"): ("P2", _P2),  # direction PROVIDER -> STATE
    ("kriya.core.qualification_identity", "kriya.core.model_runtime"): ("P2", _P2),  # non-public PROVIDER module
    ("kriya.core.release_candidate", "kriya.config.authority_approval"): ("P2", _P2),  # non-public AUTHORITY module
    ("kriya.core.release_candidate", "kriya.core.execution_environment"): ("P2", _P2),  # non-public PROVIDER module
    ("kriya.core.release_candidate", "kriya.core.inference_settings"): ("P2", _P2),  # non-public PROVIDER module
    ("kriya.core.release_candidate", "kriya.core.model_runtime"): ("P2", _P2),  # non-public PROVIDER module
    ("kriya.core.release_identity", "kriya.platform.host_properties"): ("P2", _P2),  # non-public PLATFORM module
    ("kriya.core.role_metrics", "kriya.core.model_qualification"): ("P2", _P2),  # direction PROVIDER -> QUALIFICATION
    ("kriya.core.trace", "kriya.core.db"): ("P2", _P2),  # non-public FOUNDATION module
    ("kriya.knowledge.channels.repo_manifest", "kriya.skills.skill"): ("P2", _P2),  # non-public SKILLS module
    ("kriya.knowledge.staging", "kriya.skills.skill"): ("P2", _P2),  # non-public SKILLS module
    ("kriya.knowledge.staging", "kriya.workflow.skill_extraction"): ("J1", _J1),  # direction KNOWLEDGE -> KERNEL
    ("kriya.kup.authority_ops", "kriya.config.authority_approval"): ("O1", _O1),  # direction ADAPTER-KUP -> AUTHORITY
    ("kriya.kup.catalog_ops", "kriya.application"): ("O1", _B2_KUP),  # direction ADAPTER-KUP -> APPLICATION (new in B2)
    ("kriya.kup.cli_ops", "kriya.application"): ("B4", _B2_KUP),  # direction ADAPTER-KUP -> APPLICATION (new in B2)
    ("kriya.kup.cli_ops", "kriya.core.state_paths"): ("O1", _O1),  # direction ADAPTER-KUP -> STATE
    ("kriya.kup.context_ops", "kriya.application"): ("O1", _B2_KUP),  # direction ADAPTER-KUP -> APPLICATION (new in B2)
    ("kriya.kup.context_ops", "kriya.core.llm"): ("O1", _O1),  # direction ADAPTER-KUP -> PROVIDER
    ("kriya.kup.evidence_ops", "kriya.application"): ("O1", _B2_KUP),  # direction ADAPTER-KUP -> APPLICATION (new in B2)
    ("kriya.kup.execution", "kriya.config.authority"): ("O1", _O1),  # direction ADAPTER-KUP -> AUTHORITY
    ("kriya.kup.execution", "kriya.control.launch"): ("O1", _O1),  # direction ADAPTER-KUP -> STATE
    ("kriya.kup.execution", "kriya.control.persistence"): ("O1", _O1),  # direction ADAPTER-KUP -> STATE
    ("kriya.kup.execution", "kriya.control.recovery"): ("O1", _O1),  # direction ADAPTER-KUP -> STATE
    ("kriya.kup.execution", "kriya.control.run_ownership"): ("O1", _O1),  # direction ADAPTER-KUP -> STATE
    ("kriya.kup.execution", "kriya.core.state_paths"): ("O1", _O1),  # direction ADAPTER-KUP -> STATE
    ("kriya.kup.execution", "kriya.platform.toolchain_locator"): ("O1", _O1),  # non-public PLATFORM module
    ("kriya.kup.execution", "kriya.workflow.checkpoint"): ("B3", _B3),  # direction ADAPTER-KUP -> KERNEL
    ("kriya.kup.project_init_ops", "kriya.core.model_runtime"): ("O1", _O1),  # direction ADAPTER-KUP -> PROVIDER
    ("kriya.kup.review_ops", "kriya.control.launch"): ("O1", _O1),  # direction ADAPTER-KUP -> STATE
    ("kriya.kup.review_ops", "kriya.control.launch_approval"): ("O1", _O1),  # direction ADAPTER-KUP -> AUTHORITY
    ("kriya.kup.review_ops", "kriya.control.persistence"): ("O1", _O1),  # direction ADAPTER-KUP -> STATE
    ("kriya.kup.runtime_command_ops", "kriya.control.launch"): ("O1", _O1),  # direction ADAPTER-KUP -> STATE
    ("kriya.kup.runtime_command_ops", "kriya.control.launch_approval"): ("O1", _O1),  # direction ADAPTER-KUP -> AUTHORITY
    ("kriya.kup.runtime_command_ops", "kriya.control.persistence"): ("O1", _O1),  # direction ADAPTER-KUP -> STATE
    ("kriya.kup.runtime_command_ops", "kriya.workflow.runtime_command_approval"): ("B3", _B3),  # direction ADAPTER-KUP -> AUTHORITY
    ("kriya.kup.serve", "kriya.config.authority"): ("O1", _O1),  # direction ADAPTER-KUP -> AUTHORITY
    ("kriya.kup.store", "kriya.core.db"): ("O1", _O1),  # non-public FOUNDATION module
    ("kriya.kup.store", "kriya.core.state_paths"): ("O1", _O1),  # direction ADAPTER-KUP -> STATE
    ("kriya.kup.workspace_ops", "kriya.application"): ("O1", _B2_KUP),  # direction ADAPTER-KUP -> APPLICATION (new in B2)
    ("kriya.mcp.containment_adapter", "kriya.mcp.capability"): ("P2", _P2),  # non-public AUTHORITY module
    ("kriya.mcp.lifecycle", "kriya.tools.sandbox"): ("P2", _P2),  # non-public EXECUTION module
    ("kriya.mcp.mcp", "kriya.core.kernel"): ("P2", _P2),  # direction TOOLBOX -> KERNEL
    ("kriya.mcp.mcp", "kriya.mcp.capability"): ("P2", _P2),  # non-public AUTHORITY module
    ("kriya.mcp.mcp", "kriya.mcp.invocation_approval"): ("P2", _P2),  # non-public AUTHORITY module
    ("kriya.mcp.mcp", "kriya.policy.errors"): ("P2", _P2),  # non-public AUTHORITY module
    ("kriya.mcp.mcp", "kriya.policy.model"): ("P2", _P2),  # non-public AUTHORITY module
    ("kriya.mcp.mcp", "kriya.policy.telemetry"): ("P2", _P2),  # non-public AUTHORITY module
    ("kriya.mcp.mcp", "kriya.tools.sandbox"): ("P2", _P2),  # non-public EXECUTION module
    ("kriya.memory.embedding", "kriya.control.run_coordinator"): ("H2", _H2),  # direction RETRIEVAL -> STATE
    ("kriya.memory.vector", "kriya.core.db"): ("P2", _P2),  # non-public FOUNDATION module
    ("kriya.metrics.thresholds", "kriya.config.authority_approval"): ("P2", _P2),  # direction METRICS -> AUTHORITY
    ("kriya.plugins.plugin", "kriya.core.kernel"): ("P2", _P2),  # direction TOOLBOX -> KERNEL
    ("kriya.policy.execution", "kriya.core.llm"): ("C3", _C3),  # direction AUTHORITY -> PROVIDER
    ("kriya.policy.execution", "kriya.workflow.triage"): ("C2", _C2),  # direction AUTHORITY -> KERNEL
    ("kriya.policy.filesystem", "kriya.workflow.edit_safety"): ("M1", _M1),  # direction AUTHORITY -> KERNEL
    ("kriya.policy.model", "kriya.workflow.process_profile"): ("C2", _C2),  # direction AUTHORITY -> KERNEL
    ("kriya.policy.model", "kriya.workflow.triage"): ("C2", _C2),  # direction AUTHORITY -> KERNEL
    ("kriya.production_doctor", "kriya.agents.response_protocol"): ("N1", _N1),  # direction ADAPTER-CLI -> KERNEL
    ("kriya.production_doctor", "kriya.analyzer.analyzer"): ("N1", _N1),  # direction ADAPTER-CLI -> RETRIEVAL
    ("kriya.production_doctor", "kriya.control.run_ownership"): ("N1", _N1),  # direction ADAPTER-CLI -> STATE
    ("kriya.production_doctor", "kriya.core.attempt_evidence.reader"): ("N1", _N1),  # direction ADAPTER-CLI -> EVIDENCE
    ("kriya.production_doctor", "kriya.core.attempt_evidence.writer"): ("N1", _N1),  # direction ADAPTER-CLI -> EVIDENCE
    ("kriya.production_doctor", "kriya.core.inference_runtime"): ("N1", _N1),  # direction ADAPTER-CLI -> PROVIDER
    ("kriya.production_doctor", "kriya.core.inference_settings"): ("N1", _N1),  # direction ADAPTER-CLI -> PROVIDER
    ("kriya.production_doctor", "kriya.core.logging_setup"): ("N1", _N1),  # non-public CONFIG module
    ("kriya.production_doctor", "kriya.core.model_capabilities"): ("N1", _N1),  # direction ADAPTER-CLI -> PROVIDER
    ("kriya.production_doctor", "kriya.core.model_qualification"): ("N1", _N1),  # direction ADAPTER-CLI -> QUALIFICATION
    ("kriya.production_doctor", "kriya.core.model_runtime"): ("N1", _N1),  # direction ADAPTER-CLI -> PROVIDER
    ("kriya.production_doctor", "kriya.core.provider_contract"): ("N1", _N1),  # direction ADAPTER-CLI -> PROVIDER
    ("kriya.production_doctor", "kriya.core.role_metrics"): ("N1", _N1),  # direction ADAPTER-CLI -> PROVIDER
    ("kriya.production_doctor", "kriya.core.state_paths"): ("N1", _N1),  # direction ADAPTER-CLI -> STATE
    ("kriya.production_doctor", "kriya.memory.embedding"): ("N1", _N1),  # direction ADAPTER-CLI -> RETRIEVAL
    ("kriya.production_doctor", "kriya.memory.vector"): ("N1", _N1),  # direction ADAPTER-CLI -> RETRIEVAL
    ("kriya.production_doctor", "kriya.static_analysis.doctor"): ("N1", _N1),  # direction ADAPTER-CLI -> EXECUTION
    ("kriya.production_doctor", "kriya.tools.containment"): ("N1", _N1),  # direction ADAPTER-CLI -> EXECUTION
    ("kriya.production_doctor", "kriya.tools.containment_oci"): ("N1", _N1),  # direction ADAPTER-CLI -> EXECUTION
    ("kriya.production_doctor", "kriya.tools.lsp"): ("N1", _N1),  # direction ADAPTER-CLI -> EXECUTION
    ("kriya.production_doctor", "kriya.tools.process"): ("N1", _N1),  # direction ADAPTER-CLI -> EXECUTION
    ("kriya.production_doctor", "kriya.tools.toolchain_identity"): ("N1", _N1),  # direction ADAPTER-CLI -> EXECUTION
    ("kriya.production_doctor", "kriya.tools.validate"): ("N1", _N1),  # direction ADAPTER-CLI -> EXECUTION
    ("kriya.production_doctor", "kriya.workflow.checkpoint"): ("D2", _D2),  # direction ADAPTER-CLI -> KERNEL
    ("kriya.production_doctor", "kriya.workflow.context_certification"): ("N1", _N1),  # direction ADAPTER-CLI -> QUALIFICATION
    ("kriya.production_doctor", "kriya.workflow.language_adapters"): ("N1", _N1),  # direction ADAPTER-CLI -> KERNEL
    ("kriya.production_doctor", "kriya.workflow.semantic_region_authority"): ("N1", _N1),  # direction ADAPTER-CLI -> KERNEL
    ("kriya.production_doctor", "kriya.workflow.worktree"): ("N1", _N1),  # direction ADAPTER-CLI -> KERNEL
    ("kriya.repl", "kriya"): ("P1", _P1),  # non-public FOUNDATION module
    ("kriya.routing", "kriya.memory.embedding"): ("O2", _O2),  # direction ADAPTER-CLI -> RETRIEVAL
    ("kriya.routing", "kriya.memory.vector"): ("O2", _O2),  # direction ADAPTER-CLI -> RETRIEVAL
    ("kriya.static_analysis.operator_scan", "kriya.workflow.edit_safety"): ("M1", _M1),  # direction EXECUTION -> KERNEL
    ("kriya.static_analysis.operator_scan", "kriya.workflow.file_integrity"): ("M1", _M1),  # direction EXECUTION -> KERNEL
    ("kriya.static_analysis.scope", "kriya.workflow.file_integrity"): ("M1", _M1),  # direction EXECUTION -> KERNEL
    ("kriya.static_analysis.service", "kriya.core.state_paths"): ("P2", _P2),  # direction EXECUTION -> STATE
    ("kriya.static_analysis.service", "kriya.workflow.terminal_commit"): ("M1", _M1),  # direction EXECUTION -> KERNEL
    ("kriya.static_analysis.waivers", "kriya.config.authority_approval"): ("P2", _P2),  # non-public AUTHORITY module
    ("kriya.tools", "kriya.tools.tool"): ("P2", _P2),  # direction EXECUTION -> TOOLBOX
    ("kriya.tools.service", "kriya.mcp.invocation_approval"): ("P2", _P2),  # non-public AUTHORITY module
    ("kriya.tools.toolchain_identity", "kriya.core.tomlcompat"): ("P1", _P1),  # non-public FOUNDATION module
    ("kriya.tools.validate", "kriya.control.run_coordinator"): ("H2", _H2),  # direction EXECUTION -> STATE
    ("kriya.tools.validate", "kriya.core.state_paths"): ("P2", _P2),  # direction EXECUTION -> STATE
    ("kriya.tools.validate", "kriya.core.tomlcompat"): ("P1", _P1),  # non-public FOUNDATION module
    ("kriya.tools.validate", "kriya.policy.enforcement"): ("P2", _P2),  # non-public AUTHORITY module
    ("kriya.tools.validate", "kriya.policy.errors"): ("P2", _P2),  # non-public AUTHORITY module
    ("kriya.tools.validate", "kriya.policy.model"): ("P2", _P2),  # non-public AUTHORITY module
    ("kriya.tools.web", "kriya.policy.model"): ("P2", _P2),  # non-public AUTHORITY module
    ("kriya.workflow.acceptance_approval", "kriya.workflow.acceptance_jvm"): ("P2", _P2),  # direction AUTHORITY -> KERNEL
    ("kriya.workflow.acceptance_approval", "kriya.workflow.acceptance_oracle"): ("P2", _P2),  # direction AUTHORITY -> KERNEL
    ("kriya.workflow.acceptance_approval", "kriya.workflow.requirements"): ("P2", _P2),  # direction AUTHORITY -> KERNEL
    ("kriya.workflow.acceptance_jvm", "kriya.tools.test_execution"): ("P2", _P2),  # non-public EXECUTION module
    ("kriya.workflow.acceptance_oracle", "kriya.tools.test_execution"): ("P2", _P2),  # non-public EXECUTION module
    ("kriya.workflow.acceptance_oracle", "kriya.workflow.acceptance_approval"): ("P2", _P2),  # non-public AUTHORITY module
    ("kriya.workflow.api_preservation", "kriya.code_intel.model"): ("P2", _P2),  # non-public RETRIEVAL module
    ("kriya.workflow.api_preservation", "kriya.code_intel.parsing"): ("P2", _P2),  # non-public RETRIEVAL module
    ("kriya.workflow.attempt", "kriya.analyzer.graph"): ("P2", _P2),  # non-public RETRIEVAL module
    ("kriya.workflow.attempt", "kriya.core.inference_settings"): ("P2", _P2),  # non-public PROVIDER module
    ("kriya.workflow.attempt", "kriya.core.model_capabilities"): ("P2", _P2),  # non-public PROVIDER module
    ("kriya.workflow.attempt", "kriya.core.model_qualification"): ("P2", _P2),  # direction KERNEL -> QUALIFICATION
    ("kriya.workflow.attempt", "kriya.core.model_runtime"): ("P2", _P2),  # non-public PROVIDER module
    ("kriya.workflow.attempt", "kriya.core.role_metrics"): ("P2", _P2),  # non-public PROVIDER module
    ("kriya.workflow.attempt", "kriya.core.token_budget"): ("P2", _P2),  # non-public PROVIDER module
    ("kriya.workflow.attempt", "kriya.policy.errors"): ("P2", _P2),  # non-public AUTHORITY module
    ("kriya.workflow.attempt", "kriya.policy.model"): ("P2", _P2),  # non-public AUTHORITY module
    ("kriya.workflow.attempt", "kriya.skills.skill"): ("P2", _P2),  # non-public SKILLS module
    ("kriya.workflow.attempt", "kriya.tools.service_runtime"): ("P2", _P2),  # non-public EXECUTION module
    ("kriya.workflow.attempt", "kriya.tools.test_execution"): ("P2", _P2),  # non-public EXECUTION module
    ("kriya.workflow.attempt", "kriya.workflow.authority_escalation"): ("P2", _P2),  # non-public AUTHORITY module
    ("kriya.workflow.attempt", "kriya.workflow.runtime_command_approval"): ("C3", _C3),  # non-public AUTHORITY module
    ("kriya.workflow.authority_bundle", "kriya.tools.containment"): ("P2", _P2),  # direction AUTHORITY -> EXECUTION
    ("kriya.workflow.authority_bundle", "kriya.workflow.contract_compilation"): ("P2", _P2),  # direction AUTHORITY -> KERNEL
    ("kriya.workflow.authority_bundle", "kriya.workflow.obligations"): ("P2", _P2),  # direction AUTHORITY -> KERNEL
    ("kriya.workflow.authority_bundle", "kriya.workflow.requirements"): ("P2", _P2),  # direction AUTHORITY -> KERNEL
    ("kriya.workflow.authority_escalation", "kriya.workflow.context_source"): ("P2", _P2),  # direction AUTHORITY -> KERNEL
    ("kriya.workflow.authority_escalation", "kriya.workflow.edit_safety"): ("M1", _M1),  # direction AUTHORITY -> KERNEL
    ("kriya.workflow.authority_escalation", "kriya.workflow.language_adapters"): ("P2", _P2),  # direction AUTHORITY -> KERNEL
    ("kriya.workflow.authority_request", "kriya.core.state_paths"): ("P2", _P2),  # direction AUTHORITY -> STATE
    ("kriya.workflow.authority_request", "kriya.workflow.contract_compilation"): ("P2", _P2),  # direction AUTHORITY -> KERNEL
    ("kriya.workflow.authority_request", "kriya.workflow.requirement_scopes"): ("P2", _P2),  # direction AUTHORITY -> KERNEL
    ("kriya.workflow.authority_request", "kriya.workflow.requirements"): ("P2", _P2),  # direction AUTHORITY -> KERNEL
    ("kriya.workflow.baseline_policy", "kriya.tools.toolchain_identity"): ("P2", _P2),  # non-public EXECUTION module
    ("kriya.workflow.commit_service", "kriya.static_analysis.service"): ("P2", _P2),  # non-public EXECUTION module
    ("kriya.workflow.context_budget", "kriya.code_intel.parsing"): ("P2", _P2),  # non-public RETRIEVAL module
    ("kriya.workflow.context_budget", "kriya.core.inference_settings"): ("P2", _P2),  # non-public PROVIDER module
    ("kriya.workflow.context_budget", "kriya.core.model_qualification"): ("P2", _P2),  # direction KERNEL -> QUALIFICATION
    ("kriya.workflow.context_budget", "kriya.core.model_runtime"): ("P2", _P2),  # non-public PROVIDER module
    ("kriya.workflow.context_budget", "kriya.core.token_budget"): ("P2", _P2),  # non-public PROVIDER module
    ("kriya.workflow.context_budget", "kriya.policy.trust"): ("P2", _P2),  # non-public AUTHORITY module
    ("kriya.workflow.context_certification", "kriya.analyzer.graph"): ("P2", _P2),  # non-public RETRIEVAL module
    ("kriya.workflow.context_certification", "kriya.core.model_runtime"): ("P2", _P2),  # non-public PROVIDER module
    ("kriya.workflow.context_certification", "kriya.workflow.context_budget"): ("P2", _P2),  # non-public KERNEL module
    ("kriya.workflow.context_certification", "kriya.workflow.context_recall_fixtures"): ("P2", _P2),  # non-public KERNEL module
    ("kriya.workflow.context_certification", "kriya.workflow.graph_retrieval"): ("P2", _P2),  # non-public KERNEL module
    ("kriya.workflow.context_orchestrator", "kriya.control.state"): ("P2", _P2),  # non-public STATE module
    ("kriya.workflow.context_orchestrator", "kriya.policy.trust"): ("P2", _P2),  # non-public AUTHORITY module
    ("kriya.workflow.context_package", "kriya.control.artifacts"): ("P2", _P2),  # non-public STATE module
    ("kriya.workflow.context_package", "kriya.control.contracts"): ("P2", _P2),  # non-public STATE module
    ("kriya.workflow.context_package", "kriya.policy.trust"): ("P2", _P2),  # non-public AUTHORITY module
    ("kriya.workflow.context_projection", "kriya.control.artifacts"): ("P2", _P2),  # non-public STATE module
    ("kriya.workflow.context_source", "kriya.code_intel.model"): ("P2", _P2),  # non-public RETRIEVAL module
    ("kriya.workflow.context_source", "kriya.code_intel.parsing"): ("P2", _P2),  # non-public RETRIEVAL module
    ("kriya.workflow.context_source", "kriya.core.file_stamp"): ("P2", _P2),  # non-public FOUNDATION module
    ("kriya.workflow.contract_baseline", "kriya.tools.test_execution"): ("P2", _P2),  # non-public EXECUTION module
    ("kriya.workflow.contract_baseline", "kriya.workflow.authority_bundle"): ("P2", _P2),  # non-public AUTHORITY module
    ("kriya.workflow.contract_compilation", "kriya.workflow.authority_request"): ("P2", _P2),  # non-public AUTHORITY module
    ("kriya.workflow.contract_lifecycle", "kriya.control.contracts"): ("P2", _P2),  # non-public STATE module
    ("kriya.workflow.contract_relevance", "kriya.code_intel.delegation"): ("P2", _P2),  # non-public RETRIEVAL module
    ("kriya.workflow.contract_relevance", "kriya.code_intel.model"): ("P2", _P2),  # non-public RETRIEVAL module
    ("kriya.workflow.contract_relevance", "kriya.code_intel.parsing"): ("P2", _P2),  # non-public RETRIEVAL module
    ("kriya.workflow.edit_safety", "kriya.policy.model"): ("P2", _P2),  # non-public AUTHORITY module
    ("kriya.workflow.egress_authority", "kriya.tools.knowledge"): ("P2", _P2),  # non-public KNOWLEDGE module
    ("kriya.workflow.goal_facts", "kriya.code_intel.delegation"): ("P2", _P2),  # non-public RETRIEVAL module
    ("kriya.workflow.goal_facts", "kriya.code_intel.locate"): ("P2", _P2),  # non-public RETRIEVAL module
    ("kriya.workflow.graph_retrieval", "kriya.analyzer.graph"): ("P2", _P2),  # non-public RETRIEVAL module
    ("kriya.workflow.graph_retrieval", "kriya.code_intel.locate"): ("P2", _P2),  # non-public RETRIEVAL module
    ("kriya.workflow.graph_retrieval", "kriya.code_intel.model"): ("P2", _P2),  # non-public RETRIEVAL module
    ("kriya.workflow.insertion_locus", "kriya.code_intel.model"): ("P2", _P2),  # non-public RETRIEVAL module
    ("kriya.workflow.insertion_locus", "kriya.code_intel.parsing"): ("P2", _P2),  # non-public RETRIEVAL module
    ("kriya.workflow.investigation", "kriya.analyzer.graph"): ("P2", _P2),  # non-public RETRIEVAL module
    ("kriya.workflow.investigation", "kriya.policy.errors"): ("P2", _P2),  # non-public AUTHORITY module
    ("kriya.workflow.live_lookup", "kriya.tools.search"): ("P2", _P2),  # non-public TOOLBOX module
    ("kriya.workflow.live_lookup", "kriya.tools.web"): ("P2", _P2),  # non-public TOOLBOX module
    ("kriya.workflow.localization_decision", "kriya.core.role_metrics"): ("P2", _P2),  # non-public PROVIDER module
    ("kriya.workflow.localization_decision", "kriya.core.token_budget"): ("P2", _P2),  # non-public PROVIDER module
    ("kriya.workflow.lsp_integration", "kriya.tools.lsp"): ("P2", _P2),  # non-public EXECUTION module
    ("kriya.workflow.milestone_completion", "kriya.control.recovery"): ("P2", _P2),  # non-public STATE module
    ("kriya.workflow.milestones", "kriya.control.contracts"): ("P2", _P2),  # non-public STATE module
    ("kriya.workflow.milestones", "kriya.core.trace"): ("P2", _P2),  # non-public STATE module
    ("kriya.workflow.model_transition", "kriya.core.inference_settings"): ("P2", _P2),  # non-public PROVIDER module
    ("kriya.workflow.model_transition", "kriya.core.model_capabilities"): ("P2", _P2),  # non-public PROVIDER module
    ("kriya.workflow.model_transition", "kriya.core.model_qualification"): ("P2", _P2),  # direction KERNEL -> QUALIFICATION
    ("kriya.workflow.model_transition", "kriya.core.model_runtime"): ("P2", _P2),  # non-public PROVIDER module
    ("kriya.workflow.named_test_oracle", "kriya.core.tomlcompat"): ("P1", _P1),  # non-public FOUNDATION module
    ("kriya.workflow.named_test_oracle", "kriya.tools.test_execution"): ("P2", _P2),  # non-public EXECUTION module
    ("kriya.workflow.ownership_findings", "kriya.analyzer.graph"): ("P2", _P2),  # non-public RETRIEVAL module
    ("kriya.workflow.planner_repair", "kriya.core.role_metrics"): ("P2", _P2),  # non-public PROVIDER module
    ("kriya.workflow.proposal_binding", "kriya.analyzer.java_members"): ("P2", _P2),  # non-public RETRIEVAL module
    ("kriya.workflow.recovery_coordinator", "kriya.core.token_budget"): ("P2", _P2),  # non-public PROVIDER module
    ("kriya.workflow.recovery_coordinator", "kriya.policy.errors"): ("P2", _P2),  # non-public AUTHORITY module
    ("kriya.workflow.relocalization", "kriya.code_intel.locate"): ("P2", _P2),  # non-public RETRIEVAL module
    ("kriya.workflow.requirements", "kriya.tools.test_execution"): ("P2", _P2),  # non-public EXECUTION module
    ("kriya.workflow.resume_fingerprints", "kriya"): ("P1", _P1),  # non-public FOUNDATION module
    ("kriya.workflow.resume_fingerprints", "kriya.control.contracts"): ("P2", _P2),  # non-public STATE module
    ("kriya.workflow.resume_fingerprints", "kriya.core.inference_settings"): ("P2", _P2),  # non-public PROVIDER module
    ("kriya.workflow.resume_fingerprints", "kriya.core.model_qualification"): ("P2", _P2),  # direction KERNEL -> QUALIFICATION
    ("kriya.workflow.resume_fingerprints", "kriya.core.model_runtime"): ("P2", _P2),  # non-public PROVIDER module
    ("kriya.workflow.resume_fingerprints", "kriya.skills.skill"): ("P2", _P2),  # non-public SKILLS module
    ("kriya.workflow.resume_fingerprints", "kriya.tools.containment_oci"): ("P2", _P2),  # non-public EXECUTION module
    ("kriya.workflow.resume_fingerprints", "kriya.tools.toolchain_identity"): ("P2", _P2),  # non-public EXECUTION module
    ("kriya.workflow.retry_strategy", "kriya.policy.errors"): ("P2", _P2),  # non-public AUTHORITY module
    ("kriya.workflow.retry_strategy", "kriya.tools.lsp"): ("P2", _P2),  # non-public EXECUTION module
    ("kriya.workflow.review_context", "kriya.analyzer.graph"): ("P2", _P2),  # non-public RETRIEVAL module
    ("kriya.workflow.review_context", "kriya.analyzer.java_members"): ("P2", _P2),  # non-public RETRIEVAL module
    ("kriya.workflow.run_trace", "kriya.core.role_metrics"): ("P2", _P2),  # non-public PROVIDER module
    ("kriya.workflow.run_trace", "kriya.core.trace"): ("P2", _P2),  # non-public STATE module
    ("kriya.workflow.self_correction", "kriya.policy.errors"): ("P2", _P2),  # non-public AUTHORITY module
    ("kriya.workflow.self_correction", "kriya.tools.resolver"): ("P2", _P2),  # non-public EXECUTION module
    ("kriya.workflow.semantic_region_authority", "kriya.analyzer.java_members"): ("P2", _P2),  # non-public RETRIEVAL module
    ("kriya.workflow.semantic_scope_derivation", "kriya.analyzer.java_members"): ("P2", _P2),  # non-public RETRIEVAL module
    ("kriya.workflow.skill_extraction", "kriya.skills.skill"): ("P2", _P2),  # non-public SKILLS module
    ("kriya.workflow.skill_extraction", "kriya.tools.knowledge"): ("P2", _P2),  # non-public KNOWLEDGE module
    ("kriya.workflow.state", "kriya.core.role_metrics"): ("P2", _P2),  # non-public PROVIDER module
    ("kriya.workflow.subtask_executor", "kriya.core.registry"): ("P2", _P2),  # non-public TOOLBOX module
    ("kriya.workflow.subtask_executor", "kriya.policy.errors"): ("P2", _P2),  # non-public AUTHORITY module
    ("kriya.workflow.subtask_executor", "kriya.tools.tool"): ("P2", _P2),  # non-public TOOLBOX module
    ("kriya.workflow.subtask_telemetry", "kriya.control.decisions"): ("P2", _P2),  # non-public STATE module
    ("kriya.workflow.subtask_telemetry", "kriya.control.telemetry"): ("P2", _P2),  # non-public STATE module
    ("kriya.workflow.suite_attribution", "kriya.tools.toolchain_identity"): ("P2", _P2),  # non-public EXECUTION module
    ("kriya.workflow.terminal_commit", "kriya.control.contracts"): ("P2", _P2),  # non-public STATE module
    ("kriya.workflow.terminal_commit", "kriya.static_analysis.service"): ("P2", _P2),  # non-public EXECUTION module
    ("kriya.workflow.terminal_commit", "kriya.workflow.verification_binding"): ("P2", _P2),  # non-public AUTHORITY module
    ("kriya.workflow.terminal_gate_service", "kriya.control.artifacts"): ("P2", _P2),  # non-public STATE module
    ("kriya.workflow.terminal_gate_service", "kriya.static_analysis.service"): ("P2", _P2),  # non-public EXECUTION module
    ("kriya.workflow.terminal_gate_service", "kriya.workflow.verification_binding"): ("P2", _P2),  # non-public AUTHORITY module
    ("kriya.workflow.test_delta", "kriya.code_intel.model"): ("P2", _P2),  # non-public RETRIEVAL module
    ("kriya.workflow.test_delta", "kriya.code_intel.parsing"): ("P2", _P2),  # non-public RETRIEVAL module
    ("kriya.workflow.test_delta", "kriya.tools.test_execution"): ("P2", _P2),  # non-public EXECUTION module
    ("kriya.workflow.toolchain", "kriya.capabilities"): ("P2", _P2),  # non-public EXECUTION module
    ("kriya.workflow.toolchain", "kriya.platform.toolchain_locator"): ("P2", _P2),  # non-public PLATFORM module
    ("kriya.workflow.toolchain", "kriya.tools.toolchain_identity"): ("P2", _P2),  # non-public EXECUTION module
    ("kriya.workflow.triage", "kriya.analyzer.graph"): ("P2", _P2),  # non-public RETRIEVAL module
    ("kriya.workflow.validation_baseline", "kriya.tools.test_execution"): ("P2", _P2),  # non-public EXECUTION module
    ("kriya.workflow.workflow", "kriya.analyzer.graph"): ("P2", _P2),  # non-public RETRIEVAL module
    ("kriya.workflow.workflow", "kriya.code_intel.model"): ("P2", _P2),  # non-public RETRIEVAL module
    ("kriya.workflow.workflow", "kriya.control.launch_approval"): ("P2", _P2),  # non-public AUTHORITY module
    ("kriya.workflow.workflow", "kriya.core.completion"): ("P2", _P2),  # non-public PROVIDER module
    ("kriya.workflow.workflow", "kriya.core.inference_settings"): ("P2", _P2),  # non-public PROVIDER module
    ("kriya.workflow.workflow", "kriya.core.model_capabilities"): ("P2", _P2),  # non-public PROVIDER module
    ("kriya.workflow.workflow", "kriya.core.model_qualification"): ("P2", _P2),  # direction KERNEL -> QUALIFICATION
    ("kriya.workflow.workflow", "kriya.core.model_routing"): ("P2", _P2),  # non-public PROVIDER module
    ("kriya.workflow.workflow", "kriya.core.model_runtime"): ("P2", _P2),  # non-public PROVIDER module
    ("kriya.workflow.workflow", "kriya.core.role_metrics"): ("P2", _P2),  # non-public PROVIDER module
    ("kriya.workflow.workflow", "kriya.core.token_budget"): ("P2", _P2),  # non-public PROVIDER module
    ("kriya.workflow.workflow", "kriya.core.trace"): ("P2", _P2),  # non-public STATE module
    ("kriya.workflow.workflow", "kriya.knowledge.channels.live_failure"): ("P2", _P2),  # non-public KNOWLEDGE module
    ("kriya.workflow.workflow", "kriya.knowledge.staging"): ("P2", _P2),  # non-public KNOWLEDGE module
    ("kriya.workflow.workflow", "kriya.policy.errors"): ("P2", _P2),  # non-public AUTHORITY module
    ("kriya.workflow.workflow", "kriya.policy.model"): ("P2", _P2),  # non-public AUTHORITY module
    ("kriya.workflow.workflow", "kriya.policy.telemetry"): ("P2", _P2),  # non-public AUTHORITY module
    ("kriya.workflow.workflow", "kriya.skills.skill"): ("P2", _P2),  # non-public SKILLS module
    ("kriya.workflow.workflow", "kriya.static_analysis.service"): ("P2", _P2),  # non-public EXECUTION module
    ("kriya.workflow.workflow", "kriya.tools.knowledge"): ("P2", _P2),  # non-public KNOWLEDGE module
    ("kriya.workflow.workflow", "kriya.tools.test_execution"): ("P2", _P2),  # non-public EXECUTION module
    ("kriya.workflow.workflow", "kriya.tools.web"): ("P2", _P2),  # non-public TOOLBOX module
    ("kriya.workflow.workflow", "kriya.workflow.acceptance_approval"): ("P2", _P2),  # non-public AUTHORITY module
    ("kriya.workflow.workflow", "kriya.workflow.authority_bundle"): ("P2", _P2),  # non-public AUTHORITY module
    ("kriya.workflow.workflow", "kriya.workflow.authority_request"): ("P2", _P2),  # non-public AUTHORITY module
    ("kriya.workflow.workflow", "kriya.workflow.verification_binding"): ("P2", _P2),  # non-public AUTHORITY module
    ("kriya.workflow.workflow_controller", "kriya.analyzer.graph"): ("P2", _P2),  # non-public RETRIEVAL module
    ("kriya.workflow.workflow_controller", "kriya.control.artifacts"): ("P2", _P2),  # non-public STATE module
    ("kriya.workflow.workflow_controller", "kriya.control.commit_state"): ("P2", _P2),  # non-public STATE module
    ("kriya.workflow.workflow_controller", "kriya.control.contracts"): ("P2", _P2),  # non-public STATE module
    ("kriya.workflow.workflow_controller", "kriya.control.decisions"): ("P2", _P2),  # non-public STATE module
    ("kriya.workflow.workflow_controller", "kriya.control.state"): ("P2", _P2),  # non-public STATE module
    ("kriya.workflow.workflow_controller", "kriya.skills.skill"): ("P2", _P2),  # non-public SKILLS module
    ("kriya.workflow.workflow_controller", "kriya.static_analysis.service"): ("P2", _P2),  # non-public EXECUTION module
    ("kriya.workflow.workflow_controller", "kriya.tools.knowledge"): ("P2", _P2),  # non-public KNOWLEDGE module
    ("kriya.workflow.workflow_controller", "kriya.workflow.acceptance_approval"): ("P2", _P2),  # non-public AUTHORITY module
    ("kriya.workflow.workflow_controller", "kriya.workflow.authority_request"): ("P2", _P2),  # non-public AUTHORITY module
    ("kriya.workflow.workflow_types", "kriya.control.decisions"): ("P2", _P2),  # non-public STATE module
    ("kriya.workflow.worktree", "kriya.policy.enforcement"): ("P2", _P2),  # non-public AUTHORITY module
    ("kriya.workflow.worktree", "kriya.policy.errors"): ("P2", _P2),  # non-public AUTHORITY module
    ("kriya.workflow.worktree", "kriya.policy.model"): ("P2", _P2),  # non-public AUTHORITY module
}


def violations(edges: Iterable[mm.Edge], table: Mapping[str, Optional[str]],
               modules: Mapping[str, mm.Module] = mm.MODULES) -> Dict[Tuple[str, str], str]:
    """(source, target) -> why the edge breaks the matrix or a public contract."""
    found: Dict[Tuple[str, str], str] = {}
    for edge in edges:
        source, target = mm.logical(edge.source, table), mm.logical(edge.target, table)
        if source is None or target is None:
            found[(edge.source, edge.target)] = f"unowned {edge.source if source is None else edge.target}"
            continue
        if source == target:
            continue
        restricted = mm.RESTRICTED.get((source, target))
        if target not in mm.ALLOWED[source] or (restricted is not None and edge.target not in restricted):
            found[(edge.source, edge.target)] = f"direction {source} -> {target}"
        elif edge.target not in modules[target].public:
            found[(edge.source, edge.target)] = f"non-public {target} module"
    return found


@pytest.fixture(scope="module")
def current() -> Dict[Tuple[str, str], str]:
    return violations(mm.scan(), mm.owner_table())


# ------------------------------------------------------------------ the map

def test_every_kriya_file_has_exactly_one_owner():
    claims = {path: mm.owners(path) for path in mm.python_files()}
    assert {path: owners for path, owners in claims.items() if len(owners) != 1} == {}, (
        "classify every new kriya/ file in tests/architecture/module_map.py MODULES (fail closed)")


def test_every_exact_owner_entry_and_existing_public_module_belongs_to_its_module():
    files = set(mm.python_files())
    table = mm.owner_table()
    for name, module in mm.MODULES.items():
        for entry in module.owner_paths:
            assert entry.endswith("/") or entry in files, f"{name}: {entry} is not a kriya file"
        for public in module.public:
            assert table.get(public, name) == name, f"{name} declares {public}, owned by {table.get(public)}"
    assert set(mm.ALLOWED) == set(mm.MODULES)
    assert all(target in mm.MODULES for targets in mm.ALLOWED.values() for target in targets)
    assert all(pair[1] in mm.ALLOWED[pair[0]] for pair in mm.RESTRICTED), "a restriction narrows an allowed edge"


# ---------------------------------------------------------------- direction

def test_no_import_breaks_the_direction_matrix_or_a_public_contract_outside_the_allowlist(current):
    unlisted = {pair: why for pair, why in current.items() if pair not in ALLOWLIST}
    assert unlisted == {}, (
        "import through the target module's public contract and in the DEPENDENCY-RULES direction "
        f"(tests/architecture/module_map.py): {unlisted}")


def test_the_allowlist_only_shrinks_and_names_the_removal_slice(current):
    stale = sorted(pair for pair in ALLOWLIST if pair not in current)
    assert stale == [], f"no longer a violation: delete these ALLOWLIST entries {stale}"
    table = mm.owner_table()
    for (source, target), (slice_id, reason) in ALLOWLIST.items():
        assert slice_id in SLICES and slice_id not in DONE_SLICES, f"{source} -> {target}: slice {slice_id}"
        assert reason, f"{source} -> {target}: no reason"
        assert mm.logical(source, table) != "FOUNDATION", f"FOUNDATION is never allowlisted: {source}"


def test_the_wave1_back_edges_are_measured_and_accounted_for(current):
    edges = {(edge.source, edge.target): edge.lazy for edge in mm.scan()}
    table = mm.owner_table()
    for source, target, lazy in BACK_EDGES:
        assert (source, target) in edges, f"{source} -> {target} is gone: delete it from BACK_EDGES"
        assert edges[(source, target)] is lazy, f"{source} -> {target}: lazy={edges[(source, target)]}"
        intra = mm.logical(source, table) == mm.logical(target, table)
        assert intra or (source, target) in ALLOWLIST, f"{source} -> {target} is neither declared nor allowlisted"
        assert intra or (source, target) in current


def test_foundation_imports_nothing_outside_foundation():
    table = mm.owner_table()
    escaping = sorted((edge.source, edge.target) for edge in mm.scan()
                      if mm.logical(edge.source, table) == "FOUNDATION"
                      and mm.logical(edge.target, table) != "FOUNDATION")
    assert escaping == []


# --------------------------------------------------------------- self-checks

_PLANTED = {
    "kriya/__init__.py": "",
    "kriya/cli.py": "",
    "kriya/foundation/__init__.py": "import kriya.cli\n",
    "kriya/policy/__init__.py": "",
    "kriya/policy/egress.py": "from kriya.workflow import attempt\n",
    "kriya/workflow/__init__.py": "",
    "kriya/workflow/workflow.py": "from kriya.policy import egress\n",
    "kriya/workflow/attempt.py": "def lazy():\n    from ..policy import errors\n",
    "kriya/policy/errors.py": "",
    "kriya/newpkg/x.py": "from kriya import cli\n",
}


def test_the_scanner_resolves_relative_lazy_and_submodule_imports():
    edges = {(edge.source, edge.target): edge.lazy for edge in mm.scan_sources(_PLANTED)}
    assert edges == {
        ("kriya.foundation", "kriya.cli"): False,
        ("kriya.policy.egress", "kriya.workflow.attempt"): False,
        ("kriya.workflow.workflow", "kriya.policy.egress"): False,
        ("kriya.workflow.attempt", "kriya.policy.errors"): True,
        ("kriya.newpkg.x", "kriya.cli"): False,
    }


def test_the_guard_detects_every_forbidden_form():
    found = violations(mm.scan_sources(_PLANTED), mm.owner_table_for(_PLANTED))
    assert found == {
        ("kriya.foundation", "kriya.cli"): "direction FOUNDATION -> ADAPTER-CLI",
        ("kriya.policy.egress", "kriya.workflow.attempt"): "direction AUTHORITY -> KERNEL",
        ("kriya.workflow.attempt", "kriya.policy.errors"): "non-public AUTHORITY module",
        ("kriya.newpkg.x", "kriya.cli"): "unowned kriya.newpkg.x",
    }  # KERNEL -> policy.egress is legal: a public AUTHORITY module


def test_an_unowned_or_doubly_owned_file_is_detected():
    assert mm.owners("kriya/newpkg/x.py") == []
    twice = {"A": mm.Module(("kriya/x.py",), (), 0), "B": mm.Module(("kriya/x.py",), (), 0),
             "C": mm.Module(("kriya/",), (), 0)}
    assert mm.owners("kriya/x.py", twice) == ["A", "B"]
    assert mm.owners("kriya/y.py", twice) == ["C"]
    assert mm.owners("kriya/workflow/context_certification.py") == ["QUALIFICATION"]  # exact file beats the package


def test_a_removed_allowlist_entry_or_a_new_edge_fails():
    pair = next(iter(ALLOWLIST))
    reduced = {key: value for key, value in ALLOWLIST.items() if key != pair}
    current = violations(mm.scan(), mm.owner_table())
    assert [key for key in current if key not in reduced] == [pair]
    planted = dict(_PLANTED, **{"kriya/control/__init__.py": "", "kriya/control/x.py": "import kriya.workflow.workflow\n"})
    assert violations(mm.scan_sources(planted), mm.owner_table_for(planted))[("kriya.control.x", "kriya.workflow.workflow")] \
        == "direction STATE -> KERNEL"


# ------------------------------------------------------------ module tiers

def test_every_module_tier_file_exists():
    import conftest

    tests_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    missing = sorted(f"{marker}: {name}" for marker, names in conftest.MODULE_TIER_FILES.items() for name in names
                     if not os.path.isfile(os.path.join(tests_dir, name)))
    assert missing == []
    assert set(conftest.MODULE_TIER_FILES) | set(conftest.MODULE_TIER_DIRECTORIES.values()) <= _registered_markers()


def _registered_markers() -> set:
    from kriya.foundation.tomlcompat import tomllib

    with open(mm.REPO / "pyproject.toml", "rb") as handle:
        markers = tomllib.load(handle)["tool"]["pytest"]["ini_options"]["markers"]
    return {entry.split(":")[0].split("(")[0] for entry in markers}


# ---------------------------------------------------------- foundation values

def test_foundation_contract_values():
    from kriya.foundation.clock import Deadline
    from kriya.foundation.errors import KriyaError

    class FakeClock:
        def __init__(self, now: float) -> None:
            self.now = now

        def monotonic(self) -> float:
            return self.now

        def now_iso(self) -> str:
            return "1970-01-01T00:00:00Z"

    deadline = Deadline(at_monotonic=100.0, label="run")
    clock = FakeClock(40.0)
    assert deadline.remaining(clock) == 60.0 and not deadline.expired(clock)
    clock.now = 100.0
    assert deadline.expired(clock)
    error = KriyaError("SOME_CODE", "detail")
    assert error.reason_code == "SOME_CODE" and str(error) == "detail"
    with pytest.raises(TypeError):
        KriyaError("")
