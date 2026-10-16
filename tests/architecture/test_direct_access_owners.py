"""Direct mechanism access only in its owning module (full modularization,
slice A2; handover/MODULARIZATION/DEPENDENCY-RULES.md §2, §4.4).

One closed table per mechanism, in the style of
tests/test_platform_architecture_guard.py. ``OWNERS`` is permanent: the
logical modules (``module_map.MODULES``) and the named files that own the
mechanism by design (DEPENDENCY-RULES §2). ``ALLOWLIST`` is MEASURED and
shrink-only: ``dotted module -> (site count, removal slice, reason)``; the
count must equal the measured count EXACTLY, so a new site fails and a
removed site fails until the table is edited down (an entry that reaches 0
is deleted). Sites are counted by the AST finders in ``access_sites.py``
(code only; never prose, generated-source strings or annotations).

Gate (A2, MEASURED at 5981965): the CURRENT-DEPENDENCY-MAP.md §5 line-regex
counts were reproduced exactly with the Wave 1 script; the tables below are
the AST counts of the same mechanisms. Where they differ, the regex counted
docstrings/comments (e.g. `workflow.static_checks`, `edit_safety`,
`failure_grounding`, `file_resolution` subprocess; `mcp.mcp` os.environ;
`subtask_executor`/`workflow_controller`/`kup.execution` "git"), a generated
source string (`acceptance_oracle` os.environ/getcwd), an annotation
(`mcp.mcp` asyncio.subprocess.Process) or a non-argv "git" (tool-name tuples
in `platform.toolchain_locator`, `shutil.which`). The slice report lists each
delta. The `requests`/`httpx` table coexists with
tests/test_prd012_network_inventory.py, which stays authoritative for the
outbound-client inventory; this one only forbids a client library outside
its owners.

Removal slices (SLICE-PLAN.md, by the slice whose goal names the site; P2
where no slice claims it): subprocess L2 (ProcessPort/ExecutionPort), M1
(snapshot/commit mechanism), M2 (worktree, checkpoint git helpers,
git_metadata), M3 (RepositoryPort for every remaining git call); env/home D3
(one environment reader); cwd D3/J1; sqlite D2 (history store), O1 (the B2 site moved to kup.inspect);
http I2; provider words I3; skill directory reads J2; tool registry K1/K2;
`.kriya` paths D2/D3/M2.
"""
from __future__ import annotations

import ast
from typing import Dict, FrozenSet, Mapping, NamedTuple, Optional, Tuple

import pytest

from . import access_sites as sites
from . import module_map as mm
from .test_import_direction import DONE_SLICES, SLICES


class Table(NamedTuple):
    finder: sites.Finder
    owner_modules: FrozenSet[str]      # logical modules owning the mechanism
    owner_files: FrozenSet[str]        # dotted modules owning it by design (DEPENDENCY-RULES §2)
    owner_prefixes: Tuple[str, ...]    # dotted package prefixes owning it by design


_D3 = "read through config.environment (one environment reader; ConfigService)"
_L2 = "spawn through ProcessPort / ExecutionPort.run_command"
_M1 = "moves with the snapshot/commit mechanism to kriya/repository (owner)"
_M2 = "moves to kriya/repository (worktree, git helpers, git_metadata)"
_M3 = "git through RepositoryPort (GitRepository)"
_P2 = "no owning slice yet: decided in P2 (allowlists empty)"

TABLES: Dict[str, Table] = {
    "subprocess": Table(sites.subprocess_sites, frozenset({"EXECUTION", "REPOSITORY", "PLATFORM"}),
                        frozenset({"kriya.mcp.lifecycle"}), ()),
    "git_argv": Table(sites.git_argv_sites, frozenset({"REPOSITORY"}), frozenset({"kriya.policy.execution"}), ()),
    "os_environ": Table(sites.environ_sites, frozenset(),
                        # tools.sandbox and mcp.mcp build CHILD environments (not Kriya homes)
                        frozenset({"kriya.config.environment", "kriya.tools.sandbox", "kriya.mcp.mcp"}), ()),
    "home": Table(sites.home_sites, frozenset(), frozenset({"kriya.config.environment"}), ()),
    "cwd": Table(sites.cwd_sites, frozenset({"ADAPTER-CLI", "ADAPTER-KUP", "CONTEXT"}), frozenset(), ()),
    "sqlite": Table(sites.sqlite_sites, frozenset({"STATE", "EVIDENCE", "RETRIEVAL", "KNOWLEDGE"}),
                    frozenset({"kriya.core.db"}), ()),
    "http_client": Table(sites.http_sites, frozenset({"PROVIDER", "TOOLBOX", "KNOWLEDGE", "ADAPTER-KUP",
                                                      "ADAPTER-CLI"}), frozenset(), ()),
    # adapters, config defaults (config.config) and CLI help (ADAPTER-CLI)
    "provider_words": Table(sites.provider_word_sites, frozenset({"ADAPTER-CLI"}),
                            frozenset({"kriya.core.model_runtime", "kriya.memory.embedding", "kriya.config.config"}),
                            ("kriya.static_analysis.adapters.",)),
    "skill_dir_reads": Table(sites.skill_dir_read_sites, frozenset({"SKILLS"}), frozenset(), ()),
    "tool_registry": Table(sites.tool_registry_sites, frozenset({"TOOLBOX"}),
                           frozenset({"kriya.workflow.subtask_executor"}), ()),
    "kriya_dir": Table(sites.kriya_dir_sites, frozenset({"STATE", "REPOSITORY"}), frozenset(), ()),
}

ALLOWLIST: Dict[str, Dict[str, Tuple[int, str, str]]] = {
    "subprocess": {
        "kriya.analyzer.analyzer": (5, "L2", _L2),
        "kriya.cli": (1, "M3", _M3 + " (git status of a file's repository)"),
        "kriya.code_intel.service": (1, "L2", _L2),
        "kriya.control.run_coordinator": (1, "M3", _M3 + " (_git_revision)"),
        "kriya.core.release_identity": (2, "L2", _L2),
        "kriya.distribution": (2, "L2", _L2),
        "kriya.production_doctor": (5, "L2", _L2),
        "kriya.skills.skill": (3, "L2", _L2 + " (git_commit_if_tracked via RepositoryPort.commit_tracked)"),
        "kriya.workflow.checkpoint": (8, "M2", _M2),
        "kriya.workflow.file_integrity": (2, "M1", _M1 + " (untracked_repository_paths -> repository/snapshot.py)"),
        "kriya.workflow.milestone_completion": (1, "M3", _M3),
        "kriya.workflow.named_test_oracle": (6, "L2", _L2),
        "kriya.workflow.worktree": (20, "M2", _M2),
    },
    "git_argv": {
        "kriya.analyzer.analyzer": (5, "M3", _M3),
        "kriya.cli": (1, "M3", _M3 + " (not in M3's file list: added by A2)"),
        "kriya.code_intel.service": (1, "M3", _M3),
        "kriya.control.run_coordinator": (1, "M3", _M3),
        "kriya.core.release_identity": (2, "M3", _M3 + " (not in M3's file list: added by A2)"),
        "kriya.distribution": (1, "M3", _M3 + " (not in M3's file list: added by A2)"),
        "kriya.static_analysis.operator_scan": (1, "M3", _M3 + " (not in M3's file list: added by A2)"),
        "kriya.skills.skill": (3, "M3", _M3),
        "kriya.tools.git_metadata": (1, "M2", _M2),
        "kriya.workflow.checkpoint": (8, "M2", _M2 + " (compute_tree_hash/compute_base_commit/git_read_lines)"),
        "kriya.workflow.file_integrity": (2, "M1", _M1),
        "kriya.workflow.milestone_completion": (4, "M3", _M3),
        "kriya.workflow.named_test_oracle": (3, "M3", _M3),
        "kriya.workflow.worktree": (20, "M2", _M2),
    },
    "os_environ": {
        # D3: every other env read goes through kriya.config.environment. PLATFORM is L0 and cannot import CONFIG, and this read is the
        # PATH of the toolchain probe ``tool_visibility()`` - a zero-argument callable the preflight is handed and three tests patch
        # (kriya.kup.execution.tool_visibility) - so the PATH arrives with the probe when ExecutionPort owns it (L1, the slice that removes
        # the ``tool_visibility`` LOCAL_ONLY parameter of the client contract).
        "kriya.platform.toolchain_locator": (1, "L1", "the toolchain probe's PATH: ExecutionPort's probe receives it from CONFIG's environment (re-tagged in D3)"),
    },
    "home": {},
    "cwd": {
        "kriya.core.release_candidate": (1, "P2", _P2),
        "kriya.mcp.mcp": (4, "P2", _P2),
        "kriya.skills.skill": (2, "J1", "skill scopes (ScopeRef) instead of load_cwd's process cwd"),
    },
    "sqlite": {
        "kriya.core.role_metrics": (1, "P2", _P2),
        "kriya.kup.inspect": (1, "O1", "the capabilities description reports sqlite3.sqlite_version (the 12 snapshot queries + the batched run_events query moved to STATE, kriya/control/history, in D2)"),
        "kriya.metrics.evidence": (2, "P2", _P2),
        "kriya.production_doctor": (2, "P2", _P2),
        "kriya.workflow.context_certification": (2, "P2", _P2),
    },
    "http_client": {
        # NEW in D2, unavoidable (count +1, row +1): the snapshot store's ``urllib.request.pathname2url`` (the read-only SQLite URI of a
        # snapshot path - a path-to-URI conversion, never an HTTP client) moved with kup/store.py from ADAPTER-KUP (an http owner) to STATE;
        # ``urllib.parse.quote`` is not equivalent across the supported Pythons (MEASURED on the venv's 3.14: pathname2url('/a/b') ==
        # '///a/b', an explicit empty authority for EVERY absolute path, where 3.10-3.12 return '/a/b'), so the import stays and the
        # URI stays whatever the running Python's pathname2url gives, as before the move. Owner STATE (control.history); P2 decides the rule.
        "kriya.control.history": (1, "P2", _P2 + " (urllib.request.pathname2url: a file-URI conversion moved from kup.store in D2)"),
        "kriya.memory.embedding": (9, "P2", _P2 + " (the embedding client is provider-shaped)"),
        "kriya.tools.resolver": (1, "P2", _P2),
        "kriya.tools.service_runtime": (2, "P2", _P2 + " (runtime HTTP probe of the app under test)"),
        "kriya.workflow.workflow": (1, "I2", "httpx.HTTPError in the kernel's backend-error tuple -> typed ProviderFailure"),
    },
    "provider_words": {
        "kriya.analyzer.analyzer": (1, "P2", _P2 + " (\"openai\" in a framework-detection package list)"),
        "kriya.core.inference_runtime": (1, "I3", "openai SDK import outside the adapter module: ONE selection site"),
        "kriya.core.llm": (1, "I3", "openai SDK import outside the adapter module: ONE selection site"),
    },
    "skill_dir_reads": {
        "kriya.tools.knowledge": (1, "J2", "skills directory read through the SkillRegistry when the guard moves"),
    },
    "tool_registry": {
        "kriya.cli": (5, "K2", "ToolRegistry.execute / catalog (cli tools list/execute)"),
        "kriya.workflow.workflow": (1, "K1", "available tool names through ToolRegistry"),
        "kriya.workflow.workflow_controller": (2, "K1", "available tool names through ToolRegistry"),
    },
    "kriya_dir": {
        "kriya.analyzer.analyzer": (1, "P2", _P2),
        "kriya.capabilities.java": (2, "P2", _P2),
        "kriya.capabilities.python": (1, "P2", _P2),
        "kriya.cli": (4, "P2", _P2),
        "kriya.config.authority_approval": (1, "P2", _P2 + " (error-message prose naming the default ~/.kriya home; no path is built from it since D3: the home is kriya.config.environment's; P2 refines the finder or the message)"),
        "kriya.config.environment": (1, "P2", "NEW in D3, unavoidable (count +1, rows -5 net -4): KRIYA_DIRECTORY_NAME, the one '.kriya' literal the ~/.kriya homes (authority, MCP approvals, waivers, qualifications, certifications, adjudications, state, logs: 8 joins in 7 modules before D3) and CONFIG's paths.state rule are built from; P2 decides whether CONFIG's environment reader is declared an owner of the user-home ~/.kriya (DEPENDENCY-RULES §2 lists only STATE/REPOSITORY for workspace .kriya paths)"),
        "kriya.core.attempt_evidence.leak_check": (1, "P2", _P2 + " (re-tagged in D3: '.kriya/authority' is a leak-detection MARKER string searched for in model-facing blobs, not a path construction; P2 refines the finder)"),
        "kriya.mcp.invocation_approval": (1, "P2", _P2 + " (error-message prose naming the default ~/.kriya home; no path is built from it since D3: the home is kriya.config.environment's; P2 refines the finder or the message)"),
        "kriya.policy.approved_sources": (1, "P2", _P2),
        "kriya.policy.filesystem": (1, "P2", _P2),
        "kriya.production_doctor": (4, "P2", _P2),
        "kriya.static_analysis.doctor": (1, "P2", _P2),
        "kriya.static_analysis.operator_scan": (1, "P2", _P2),
        "kriya.static_analysis.scope": (1, "P2", _P2),
        "kriya.tools.lsp": (1, "P2", _P2),
        "kriya.tools.test_execution": (1, "P2", _P2),
        "kriya.tools.validate": (5, "P2", _P2),
        "kriya.workflow.acceptance_jvm": (1, "P2", _P2),
        "kriya.workflow.acceptance_oracle": (2, "P2", _P2),
        "kriya.workflow.attempt": (2, "P2", _P2),
        "kriya.workflow.authority_bundle": (2, "P2", _P2),
        "kriya.workflow.baseline_policy": (1, "P2", _P2),
        "kriya.workflow.checkpoint": (1, "M2", "the `git rm --cached -- .kriya` argv of compute_workspace_content_hash (re-tagged in D2: the checkpoint store and its CHECKPOINT_DIR moved to control.checkpoint, STATE; the git helper moves to kriya/repository)"),
        "kriya.workflow.compile_regression": (1, "P2", _P2),
        "kriya.workflow.contract_lifecycle": (1, "P2", _P2),
        "kriya.workflow.deterministic_failure_diagnostic": (1, "P2", _P2),
        "kriya.workflow.edit_safety": (1, "P2", _P2),
        "kriya.workflow.example_oracle_java": (3, "P2", _P2),
        "kriya.workflow.failure_grounding": (1, "P2", _P2),
        "kriya.workflow.file_integrity": (1, "P2", _P2),
        "kriya.workflow.file_resolution": (9, "P2", _P2),
        "kriya.workflow.goal_facts": (1, "P2", _P2),
        "kriya.workflow.migration": (1, "P2", _P2),
        "kriya.workflow.milestones": (1, "P2", _P2),
        "kriya.workflow.named_test_oracle": (1, "P2", _P2),
        "kriya.workflow.planning_diagnostics": (1, "P2", _P2),
        "kriya.workflow.proposal_store": (1, "P2", _P2),
        "kriya.workflow.repository_topology": (1, "P2", _P2),
        "kriya.workflow.retry_strategy": (1, "P2", _P2),
        "kriya.workflow.review_context": (1, "P2", _P2),
        "kriya.workflow.triage": (1, "P2", _P2),
        "kriya.workflow.workflow": (4, "P2", _P2),
        "kriya.workflow.workflow_controller": (3, "P2", _P2),
        "kriya.workflow.worktree": (11, "M2", "moves to kriya/repository (owner of the worktree path)"),
    },
}

# DEPENDENCY-RULES §4.4: the only module-level registries. A module-level dict
# that a function writes into anywhere else fails, unless it is a declared
# cache (CURRENT-DEPENDENCY-MAP.md §4: process-wide memoization, not a
# lookup of components). Both tables are closed: an entry that is no longer
# found fails too.
REGISTRY_MODULES: FrozenSet[str] = frozenset({
    "kriya.core.inference_runtime", "kriya.toolbox.registry", "kriya.skills.registry",
    "kriya.static_analysis.registry", "kriya.capabilities", "kriya.platform.services"})
REGISTRIES: Dict[str, FrozenSet[str]] = {
    "kriya.core.inference_runtime": frozenset({"_REGISTRY"}),
    "kriya.static_analysis.registry": frozenset({"_REGISTRY"}),
}
DECLARED_CACHES: Dict[str, Tuple[FrozenSet[str], str]] = {
    "kriya.analyzer.analyzer": (frozenset({"_ANALYZE_CACHE"}), "analysis cache keyed by workspace"),
    "kriya.core.execution_environment": (frozenset({"_HOST_CACHE"}), "host fingerprint cache"),
    "kriya.core.model_qualification": (frozenset({"_RECORD_CACHE"}), "qualification record cache"),
    "kriya.core.model_runtime": (frozenset({"_CACHE"}), "runtime fingerprint cache, bounded by bindings"),
    "kriya.core.token_budget": (frozenset({"_EXACT_COUNTERS"}), "tokenizer cache"),
    "kriya.memory.vector": (frozenset({"_MATRIX_CACHE"}), "embedding matrix cache"),
    "kriya.tools.containment_oci": (frozenset({"_ATTESTED_TOOLCHAINS"}), "process-wide toolchain attestation cache"),
    "kriya.workflow.goal_facts": (frozenset({"_DEVELOPER_FACTS_CACHE"}), "goal fact extraction cache"),
    "kriya.workflow.semantic_region_authority": (frozenset({"_IDENTIFIER_RE_CACHE"}), "compiled regex cache"),
}


def is_owner(table: Table, module: str, logical_table: Mapping[str, Optional[str]]) -> bool:
    return (mm.logical(module, logical_table) in table.owner_modules or module in table.owner_files
            or module.startswith(table.owner_prefixes))


def outside_owners(table: Table, trees: Mapping[str, ast.Module], logical_table) -> Dict[str, int]:
    return {module: count for module, count in sites.counts(table.finder, trees).items()
            if not is_owner(table, module, logical_table)}


@pytest.fixture(scope="module")
def measured() -> Dict[str, Dict[str, int]]:
    table = mm.owner_table()
    return {name: outside_owners(spec, mm.trees(), table) for name, spec in TABLES.items()}


@pytest.mark.parametrize("mechanism", sorted(TABLES))
def test_no_direct_mechanism_outside_its_owner_beyond_the_measured_allowlist(mechanism, measured):
    expected = {module: entry[0] for module, entry in ALLOWLIST[mechanism].items()}
    assert measured[mechanism] == expected, (
        f"{mechanism}: a new site outside the owner ({sorted(TABLES[mechanism].owner_modules)} + "
        f"{sorted(TABLES[mechanism].owner_files)}) fails; a removed site must be deleted from ALLOWLIST "
        "(the count is exact, the list only shrinks)")


def test_every_allowlist_entry_names_an_open_removal_slice_and_never_an_owner():
    table = mm.owner_table()
    assert set(ALLOWLIST) == set(TABLES)
    for mechanism, entries in ALLOWLIST.items():
        for module, (count, slice_id, reason) in entries.items():
            assert count > 0 and reason, f"{mechanism}/{module}"
            assert slice_id in SLICES and slice_id not in DONE_SLICES, f"{mechanism}/{module}: slice {slice_id}"
            assert table.get(module) is not None, f"{mechanism}/{module} is not a classified kriya module"
            assert not is_owner(TABLES[mechanism], module, table), f"{mechanism}/{module} owns the mechanism"
            assert mm.logical(module, table) != "FOUNDATION", f"FOUNDATION is never allowlisted: {module}"


def test_module_level_registries_only_in_the_registry_modules():
    found = {mm.module_name(path): frozenset(names) for path, tree in mm.trees().items()
             for names in [sites.mutated_module_dicts(tree)] if names}
    registries = {module: names for module, names in found.items() if module not in DECLARED_CACHES}
    assert registries == REGISTRIES, "a new module-level registry belongs in one of REGISTRY_MODULES (DEPENDENCY-RULES §4.4)"
    assert set(REGISTRIES) <= REGISTRY_MODULES
    assert {module: names for module, names in found.items() if module in DECLARED_CACHES} \
        == {module: names for module, (names, _why) in DECLARED_CACHES.items()}, "DECLARED_CACHES is closed"


# --------------------------------------------------------------- self-checks

_PLANTED = {
    "kriya/__init__.py": "",
    "kriya/workflow/__init__.py": "",
    "kriya/workflow/planted.py": (
        '"""Docstring prose never counts: subprocess.run, os.environ, "git", ollama, .kriya/x."""\n'
        "import asyncio, os, sqlite3, subprocess, httpx\n"
        "from pathlib import Path\n"
        "from subprocess import Popen\n"
        "# a comment never counts: subprocess.run(['git'])\n"
        "_REG = {}\n"
        "TEMPLATE = 'os.environ[\"X\"] = 1'\n"
        "def f(kernel, skills_dir):\n"
        "    subprocess.run(['git', 'status'])\n"
        "    asyncio.create_subprocess_exec('git', 'log')\n"
        "    Popen(['ls'])\n"
        "    os.environ.get('KRIYA_STATE_DIR'); os.getenv('HOME')\n"
        "    Path.home(); os.path.expanduser('~')\n"
        "    os.getcwd(); Path.cwd()\n"
        "    sqlite3.connect(':memory:')\n"
        "    httpx.get('http://x')\n"
        "    model = 'ollama/qwen'\n"
        "    os.listdir(skills_dir)\n"
        "    kernel.registry.get('tool', 'shell')\n"
        "    state = Path('.kriya') / 'control'\n"
        "    _REG['k'] = 1\n"
        "    return ('python3', 'git'), model, state\n"),
}


def test_the_finders_detect_every_planted_form_and_ignore_prose():
    trees = mm.parse_sources(_PLANTED)
    table = mm.owner_table_for(_PLANTED)
    planted = "kriya.workflow.planted"
    found = {name: outside_owners(spec, trees, table).get(planted, 0) for name, spec in TABLES.items()}
    assert found == {"subprocess": 4, "git_argv": 2, "os_environ": 1, "home": 1, "cwd": 1, "sqlite": 1,
                     "http_client": 1, "provider_words": 1, "skill_dir_reads": 1, "tool_registry": 1,
                     "kriya_dir": 1}
    assert sites.mutated_module_dicts(trees["kriya/workflow/planted.py"]) == {"_REG"}


def test_a_removed_allowlist_entry_or_a_grown_count_fails(measured):
    mechanism, module = "subprocess", "kriya.workflow.worktree"
    reduced = {name: entry[0] for name, entry in ALLOWLIST[mechanism].items() if name != module}
    assert measured[mechanism] != reduced and measured[mechanism][module] == ALLOWLIST[mechanism][module][0]
    grown = dict(_PLANTED)
    grown["kriya/workflow/planted.py"] += "def g():\n    subprocess.call(['x'])\n"
    trees = mm.parse_sources(grown)
    assert outside_owners(TABLES["subprocess"], trees, mm.owner_table_for(grown))["kriya.workflow.planted"] == 5
