"""GREENFIELD-AUTHORITY-BASE-001 (owner ruling 2026-11-18): a sealed verification authority for a project that does not exist yet.

MEASURED before the fix (Stage B probe, canonical main 40064bd, production profile, a fresh folder holding only ``kriya.yaml``, a manifest written with the product's own
``authority_template``): ``[Verification authority refused] AUTHORITY_BASE_MISMATCH: the authority binds base revision 'GREENFIELD_NO_BASE_REVISION', the workspace is at None`` -
zero model calls. ``load_authority_bundle`` refused every manifest when the workspace had no HEAD, and an empty folder has no language to match, so a production-profile
greenfield goal could never bind an external authority.

The contract these tests pin: the manifest DECLARES greenfield with the typed ``GREENFIELD_BASE_REVISION`` in its existing ``base_revision`` field; it binds only a workspace
the repository layer proves is an empty folder with no Git repository; ``None``, an empty string, a missing field or a failed detection is never greenfield; a greenfield
manifest never authorizes a brownfield workspace and a concrete-base manifest never binds a workspace without a HEAD; nothing is executed at baseline; before any closure the
run's base must be an empty parentless commit (Kriya's bootstrap) and the generated candidate's detected language must equal the declared one.
"""
import json
import subprocess
from types import SimpleNamespace

import pytest
from _strict_doubles import strict_config

from kriya.workflow import authority_bundle as ab
from kriya.workflow import requirement_closers as closers
from kriya.workflow import workflow
from kriya.workflow.contract_compilation import CLOSER_EXTERNAL_ACCEPTANCE
from kriya.workflow.obligations import ObligationLedger
from kriya.workflow.requirements import (
    BEHAVIOR,
    RequirementOutcome,
    derive_requirements,
    record_requirement_verdicts,
    requirement_outcomes,
    seed_requirement_obligations,
)

GOAL = "Create a Java 17 Maven application whose Greeter returns a greeting for every name it is given.\n"
OTHER_GOAL = "Create a Python package whose slugify lowercases every title it is given.\n"
IDENTITY = ("-c", "user.email=t@t", "-c", "user.name=t")


def _git(cwd, *args):
    return subprocess.run(["git", "-c", "maintenance.auto=false", "-c", "gc.auto=0", *args], cwd=cwd, capture_output=True, text=True, check=True).stdout.strip()


def _empty_folder(tmp_path, name="ws"):
    """What a greenfield scenario starts from: Kriya's own configuration and state, a desktop shell's metadata file, no repository, no project."""
    root = tmp_path / name
    (root / ".kriya").mkdir(parents=True)
    (root / "kriya.yaml").write_text("workflow_controller:\n  enabled: true\n")
    (root / ".DS_Store").write_bytes(b"\x00")
    return root


def _manifest(tmp_path, base, *, goal=GOAL, name="authority", language="java", build_tool="maven", **overrides):
    bundle = tmp_path / name
    (bundle / "oracle").mkdir(parents=True)
    (bundle / "oracle" / "verify.sh").write_text("#!/bin/sh\nexit 0\n")
    manifest = ab.authority_template(str(bundle), derive_requirements(goal), goal, base_revision=base, language=language, build_tool=build_tool,
                                     verify=["sh", "oracle/verify.sh"], prepare=None,
                                     covers=[("REQ-1", BEHAVIOR, "GENERAL", "the oracle builds the project and checks the greeting")], authority_id="greenfield-oracle")
    manifest.update(overrides)
    (bundle / "manifest.json").write_text(json.dumps(manifest, indent=1))
    return bundle / "manifest.json"


def _load(tmp_path, manifest, workspace, *, base=None, language=None, goal=GOAL):
    return ab.load_authority_bundle(str(manifest), derive_requirements(goal), goal, state_root=str(tmp_path / "state"), workspace=str(workspace),
                                    base_revision=base, project_language=language)


def _refusal(tmp_path, manifest, workspace, **facts):
    with pytest.raises(ab.AuthorityBundleError) as refused:
        _load(tmp_path, manifest, workspace, **facts)
    return refused.value.reason_code


# ---------------------------------------------------------------- binding

def test_a_declared_greenfield_manifest_binds_a_proven_empty_folder_and_seals_the_expected_toolchain(tmp_path):
    workspace = _empty_folder(tmp_path)
    bundle = _load(tmp_path, _manifest(tmp_path, ab.GREENFIELD_BASE_REVISION), workspace)
    assert bundle.is_greenfield is True and bundle.base_revision == ab.GREENFIELD_BASE_REVISION
    assert (bundle.language, bundle.build_tool) == ("java", "maven")            # the EXPECTED identity, from the sealed manifest only
    assert bundle.to_dict()["base_revision"] == ab.GREENFIELD_BASE_REVISION
    assert sorted(bundle.covers) == ["REQ-1"]


def test_the_cli_binder_binds_the_measured_scenario_and_refuses_it_once_the_folder_holds_a_project(tmp_path):
    """The Stage B probe's own path: ``cli._bind_verification_authority`` on a folder holding only Kriya's configuration."""
    from kriya.cli import _bind_verification_authority

    workspace = _empty_folder(tmp_path)
    manifest = _manifest(tmp_path, ab.GREENFIELD_BASE_REVISION)
    cfg = strict_config(paths={"state": str(tmp_path / "state")})
    assert _bind_verification_authority(cfg, str(manifest), GOAL, str(workspace)).is_greenfield is True
    (workspace / "pom.xml").write_text("<project/>\n")
    with pytest.raises(ab.AuthorityBundleError) as refused:                       # a non-Git folder holding project content has no source authority at all
        _bind_verification_authority(cfg, str(manifest), GOAL, str(workspace))
    assert refused.value.reason_code == ab.AUTHORITY_EXECUTION_UNAVAILABLE


def test_the_greenfield_identity_hashes_distinctly_from_any_concrete_base(tmp_path):
    workspace = _empty_folder(tmp_path)
    greenfield = _load(tmp_path, _manifest(tmp_path, ab.GREENFIELD_BASE_REVISION), workspace)
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "pom.xml").write_text("<project/>\n")
    _git(repo, "init", "-q")
    _git(repo, *IDENTITY, "add", "-A")
    _git(repo, *IDENTITY, "commit", "-q", "-m", "base")
    head = _git(repo, "rev-parse", "HEAD")
    concrete = _load(tmp_path, _manifest(tmp_path, head, name="concrete"), repo, base=head, language="java")
    assert concrete.is_greenfield is False and concrete.digest != greenfield.digest
    assert len(ab.GREENFIELD_BASE_REVISION) != 40 and not all(c in "0123456789abcdef" for c in ab.GREENFIELD_BASE_REVISION)   # never a commit id


def test_a_greenfield_manifest_never_binds_a_workspace_with_a_head(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, *IDENTITY, "commit", "-q", "--allow-empty", "-m", "an empty commit is still a base")
    head = _git(repo, "rev-parse", "HEAD")
    manifest = _manifest(tmp_path, ab.GREENFIELD_BASE_REVISION)
    assert _refusal(tmp_path, manifest, repo, base=head) == ab.AUTHORITY_BASE_MISMATCH
    assert _refusal(tmp_path, manifest, repo) == ab.AUTHORITY_BASE_MISMATCH        # the same repository, even if a caller reported no HEAD: the folder is Git-backed


def test_a_greenfield_manifest_never_binds_a_repository_without_a_commit(tmp_path):
    repo = tmp_path / "unborn"
    repo.mkdir()
    _git(repo, "init", "-q")
    assert _refusal(tmp_path, _manifest(tmp_path, ab.GREENFIELD_BASE_REVISION), repo) == ab.AUTHORITY_BASE_MISMATCH


@pytest.mark.parametrize("entry", ["pom.xml", "src", "notes.txt", "kriya.yml"])
def test_a_greenfield_manifest_never_binds_a_folder_that_already_holds_content(tmp_path, entry):
    workspace = _empty_folder(tmp_path)
    if entry == "src":
        (workspace / "src").mkdir()
    else:
        (workspace / entry).write_text("x\n")
    assert _refusal(tmp_path, _manifest(tmp_path, ab.GREENFIELD_BASE_REVISION), workspace) == ab.AUTHORITY_BASE_MISMATCH


def test_a_greenfield_manifest_refuses_when_the_caller_reports_a_base_or_a_detected_language(tmp_path):
    """The loader never trusts the folder alone: a reported revision or a detected project language contradicts "no existing base"."""
    workspace = _empty_folder(tmp_path)
    manifest = _manifest(tmp_path, ab.GREENFIELD_BASE_REVISION)
    assert _refusal(tmp_path, manifest, workspace, base="a" * 40) == ab.AUTHORITY_BASE_MISMATCH
    assert _refusal(tmp_path, manifest, workspace, language="java") == ab.AUTHORITY_BASE_MISMATCH
    assert _load(tmp_path, manifest, workspace).is_greenfield is True              # the control: the same manifest and folder bind without them


def test_a_missing_folder_or_an_unanswerable_repository_question_is_never_greenfield(tmp_path, monkeypatch):
    from kriya.repository import bound_tree

    manifest = _manifest(tmp_path, ab.GREENFIELD_BASE_REVISION)
    assert _refusal(tmp_path, manifest, tmp_path / "absent") == ab.AUTHORITY_BASE_MISMATCH
    workspace = _empty_folder(tmp_path)

    def unavailable(_root):
        raise bound_tree.BoundTreeError(bound_tree.BOUND_TREE_UNAVAILABLE, "git could not answer")
    monkeypatch.setattr(bound_tree, "workspace_root_class", unavailable)
    assert _refusal(tmp_path, manifest, workspace) == ab.AUTHORITY_BASE_MISMATCH


@pytest.mark.parametrize("declared, code", [(None, ab.AUTHORITY_BASE_MISMATCH), ("", ab.AUTHORITY_BASE_MISMATCH), ("greenfield", ab.AUTHORITY_BASE_MISMATCH),
                                            ("a" * 40, ab.AUTHORITY_BASE_MISMATCH)])
def test_greenfield_is_never_implicit_and_a_concrete_base_never_binds_a_folder_without_a_head(tmp_path, declared, code):
    workspace = _empty_folder(tmp_path)
    assert _refusal(tmp_path, _manifest(tmp_path, "placeholder", base_revision=declared), workspace) == code


def test_a_manifest_without_the_base_field_is_invalid(tmp_path):
    workspace = _empty_folder(tmp_path)
    manifest = _manifest(tmp_path, ab.GREENFIELD_BASE_REVISION)
    data = json.loads(manifest.read_text())
    del data["base_revision"]
    manifest.write_text(json.dumps(data))
    assert _refusal(tmp_path, manifest, workspace) == ab.AUTHORITY_INVALID


def test_a_greenfield_manifest_made_for_another_goal_is_refused_by_the_existing_identity_rule(tmp_path):
    workspace = _empty_folder(tmp_path)
    assert _refusal(tmp_path, _manifest(tmp_path, ab.GREENFIELD_BASE_REVISION, goal=OTHER_GOAL, language="python", build_tool="pip"), workspace) == ab.AUTHORITY_GOAL_MISMATCH


def test_a_greenfield_manifest_still_needs_a_valid_sealed_toolchain(tmp_path):
    workspace = _empty_folder(tmp_path)
    manifest = _manifest(tmp_path, ab.GREENFIELD_BASE_REVISION, toolchain={"language": "cobol", "build_tool": "maven"})
    assert _refusal(tmp_path, manifest, workspace) == ab.AUTHORITY_INVALID


# ---------------------------------------------------------------- baseline: nothing exists, nothing is executed

def _engine(tmp_path, bundle):
    cfg = strict_config(paths={"state": str(tmp_path / "state")})
    return SimpleNamespace(kernel=SimpleNamespace(config=cfg), verification_authority_bundle=bundle, verification_authorities=(bundle.authority(),),
                           acceptance=None, acceptance_approval=None, requirement_contract=None, requirement_dispositions=None, derived_examples=None,
                           derived_examples_report=None), cfg


def test_the_baseline_of_a_greenfield_authority_executes_nothing_and_is_not_an_environment_stop(tmp_path, monkeypatch):
    workspace = _empty_folder(tmp_path)
    bundle = _load(tmp_path, _manifest(tmp_path, ab.GREENFIELD_BASE_REVISION), workspace)
    engine, cfg = _engine(tmp_path, bundle)
    requirements = derive_requirements(GOAL)
    contract = workflow.compile_run_contract(engine, GOAL, GOAL, str(workspace), requirements)
    assert [rid for rid, _b in contract.binding_closers(CLOSER_EXTERNAL_ACCEPTANCE)] == ["REQ-1"] and contract.base_revision is None

    def never(*_args, **_kwargs):
        raise AssertionError("a greenfield authority must not be executed at baseline")
    monkeypatch.setattr(ab, "run_authority_bundle", never)
    report = workflow.run_contract_baseline(engine, contract, requirements, str(workspace), autonomy_cfg=cfg.autonomy, config=cfg)
    [entry] = [e for e in report.authorities_run if e["kind"] == "external_acceptance_command"]
    assert entry["verdict"] == "INDETERMINATE" and entry["reason_code"] == ab.AUTHORITY_INDETERMINATE and entry["digest"] == bundle.digest
    assert report.unavailable_authorities() == [] and report.no_mutation_required is False and report.discriminating is False
    assert report.unsatisfied["REQ-1"] == [BEHAVIOR]


def test_control_executing_the_oracle_on_the_empty_folder_is_the_environment_stop_the_baseline_must_not_produce(tmp_path):
    """Why the baseline is typed instead of executed: there is no bound tree to copy from a folder without a repository."""
    workspace = _empty_folder(tmp_path)
    bundle = _load(tmp_path, _manifest(tmp_path, ab.GREENFIELD_BASE_REVISION), workspace)
    run = ab.run_authority_bundle(bundle, str(workspace), state_root=str(tmp_path / "state"), validator_factory=lambda _root: None)
    assert run.reason_code == ab.AUTHORITY_EXECUTION_UNAVAILABLE and run.verdict == ab.VERDICT_INDETERMINATE


def test_the_greenfield_baseline_run_establishes_nothing():
    run = ab.greenfield_baseline_run()
    assert (run.verdict, run.reason_code) == (ab.VERDICT_INDETERMINATE, ab.AUTHORITY_INDETERMINATE)
    assert run.prepare_result is None and run.verify_result is None and run.candidate_digest is None


# ---------------------------------------------------------------- closure: actual project identity before any verdict counts

def _closure(tmp_path):
    workspace = _empty_folder(tmp_path)
    bundle = _load(tmp_path, _manifest(tmp_path, ab.GREENFIELD_BASE_REVISION), workspace)
    engine, cfg = _engine(tmp_path, bundle)
    requirements = derive_requirements(GOAL)
    contract = workflow.compile_run_contract(engine, GOAL, GOAL, str(workspace), requirements)
    ledger = ObligationLedger()
    seed_requirement_obligations(ledger, requirements)
    record_requirement_verdicts(ledger, requirements, {r.id: (RequirementOutcome.UNVERIFIED, "x") for r in requirements.requirements},
                                revision=1, evidence_fingerprint="cand", source="test")
    return SimpleNamespace(workspace=workspace, bundle=bundle, engine=engine, cfg=cfg, requirements=requirements, contract=contract, ledger=ledger)


def _close(case, *, proven, language, verdict=ab.VERDICT_PASS):
    calls = []

    def execute():
        calls.append(1)
        return ab.AuthorityRun(verdict=verdict, reason_code="x", reason="scripted")
    kwargs = {}
    if proven is not None:
        kwargs["greenfield_base_proven"] = proven
    if language is not None:
        kwargs["candidate_language"] = language
    entries = ab.close_requirements_with_authority_bundle(case.ledger, case.requirements, case.bundle, case.contract, execute=execute, source="t", revision=1,
                                                          base_revision="b" * 40, **kwargs)
    return entries, len(calls)


def test_a_greenfield_authority_closes_only_with_a_proven_bootstrap_base_and_the_declared_language(tmp_path):
    case = _closure(tmp_path)
    [entry], executed = _close(case, proven=lambda: True, language=lambda: "java")
    assert executed == 1 and entry["closed"] is True
    assert requirement_outcomes(case.ledger, case.requirements)["REQ-1"] is RequirementOutcome.HUMAN_ACCEPTED


@pytest.mark.parametrize("proven", [None, lambda: False, lambda: None, lambda: "yes"])
def test_a_greenfield_authority_never_runs_when_the_base_is_not_proven_to_be_the_empty_bootstrap(tmp_path, proven):
    case = _closure(tmp_path)
    [entry], executed = _close(case, proven=proven, language=lambda: "java")
    assert executed == 0 and entry["closed"] is False and entry["reason_code"] == ab.AUTHORITY_BASE_MISMATCH
    assert requirement_outcomes(case.ledger, case.requirements)["REQ-1"] is RequirementOutcome.UNVERIFIED


@pytest.mark.parametrize("language", [None, lambda: None, lambda: "python"])
def test_a_greenfield_authority_never_runs_when_the_generated_project_is_not_the_declared_language(tmp_path, language):
    case = _closure(tmp_path)
    [entry], executed = _close(case, proven=lambda: True, language=language)
    assert executed == 0 and entry["closed"] is False and entry["reason_code"] == ab.AUTHORITY_TOOLCHAIN_MISMATCH
    assert requirement_outcomes(case.ledger, case.requirements)["REQ-1"] is RequirementOutcome.UNVERIFIED


def test_a_failing_oracle_on_a_proven_greenfield_candidate_is_counter_evidence(tmp_path):
    case = _closure(tmp_path)
    [entry], executed = _close(case, proven=lambda: True, language=lambda: "java", verdict=ab.VERDICT_FAIL)
    assert executed == 1 and entry["closed"] is False
    assert requirement_outcomes(case.ledger, case.requirements)["REQ-1"] is RequirementOutcome.VIOLATED


# ---------------------------------------------------------------- the repository facts the closer supplies

def _bootstrapped(tmp_path, name="run"):
    """What ``worktree._bootstrap_greenfield_repository`` leaves: a repository whose only commit has no parent and an empty tree."""
    root = tmp_path / name
    root.mkdir()
    _git(root, "init", "-q")
    _git(root, *IDENTITY, "commit", "-q", "--allow-empty", "-m", "bootstrap")
    return root, _git(root, "rev-parse", "HEAD")


def test_the_bootstrap_base_fact_is_read_from_git_lineage_and_tree(tmp_path):
    root, bootstrap = _bootstrapped(tmp_path)
    assert closers._base_is_empty_bootstrap_commit(str(root), bootstrap) is True
    assert closers._base_is_empty_bootstrap_commit(str(root), None) is False
    assert closers._base_is_empty_bootstrap_commit(str(root), "") is False
    assert closers._base_is_empty_bootstrap_commit(str(root), "f" * 40) is False                    # not a commit of this repository
    assert closers._base_is_empty_bootstrap_commit(str(tmp_path), bootstrap) is False               # not a repository
    _git(root, *IDENTITY, "commit", "-q", "--allow-empty", "-m", "a second empty commit has a parent")
    assert closers._base_is_empty_bootstrap_commit(str(root), _git(root, "rev-parse", "HEAD")) is False
    assert closers._base_is_empty_bootstrap_commit(str(root), bootstrap) is True                    # the original bootstrap is still one


def test_a_parentless_commit_that_holds_a_file_is_not_a_greenfield_base(tmp_path):
    root = tmp_path / "brown"
    root.mkdir()
    (root / "pom.xml").write_text("<project/>\n")
    _git(root, "init", "-q")
    _git(root, *IDENTITY, "add", "-A")
    _git(root, *IDENTITY, "commit", "-q", "-m", "a project")
    assert closers._base_is_empty_bootstrap_commit(str(root), _git(root, "rev-parse", "HEAD")) is False


def test_the_candidate_language_comes_from_the_existing_detector_over_the_bound_tree(tmp_path):
    deps = workflow._closer_deps()
    root, _bootstrap = _bootstrapped(tmp_path)
    assert closers._candidate_project_language(deps, str(root)) is None                             # nothing generated yet
    (root / "pom.xml").write_text("<project/>\n")                                                   # a generated, uncommitted candidate file
    assert closers._candidate_project_language(deps, str(root)) == "java"
    assert closers._candidate_project_language(deps, str(_empty_folder(tmp_path))) is None          # no membership authority: unknown
    assert closers.CloserDeps.__dataclass_fields__["project_language"].default(["pom.xml"]) is None  # a CloserDeps built without the detector answers unknown


def _evidence_call(tmp_path, case, candidate, monkeypatch, verdict=ab.VERDICT_PASS):
    calls = []

    def scripted(bundle, candidate_root, **_kwargs):
        calls.append(candidate_root)
        return ab.AuthorityRun(verdict=verdict, reason_code="x", reason="scripted")
    monkeypatch.setattr(ab, "run_authority_bundle", scripted)
    entries = workflow.close_requirements_with_authority_bundle_evidence(
        case.cfg.autonomy, case.ledger, case.requirements, case.contract, str(candidate), str(candidate), engine=case.engine, revision=1,
        toolchain_declaration_mutable=False)
    return entries, calls


def test_the_production_closer_proves_both_facts_from_the_generated_repository(tmp_path, monkeypatch):
    """The real closer path (workflow forwarder -> requirement_closers -> authority_bundle) on what a greenfield run produces: Kriya's empty bootstrap commit plus generated files."""
    case = _closure(tmp_path)
    candidate, _bootstrap = _bootstrapped(tmp_path)
    (candidate / "pom.xml").write_text("<project/>\n")
    [entry], calls = _evidence_call(tmp_path, case, candidate, monkeypatch)
    assert calls == [str(candidate)] and entry["closed"] is True
    assert requirement_outcomes(case.ledger, case.requirements)["REQ-1"] is RequirementOutcome.HUMAN_ACCEPTED


def test_the_production_closer_refuses_a_generated_project_of_another_language(tmp_path, monkeypatch):
    case = _closure(tmp_path)
    candidate, _bootstrap = _bootstrapped(tmp_path)
    (candidate / "setup.py").write_text("from setuptools import setup\n")
    [entry], calls = _evidence_call(tmp_path, case, candidate, monkeypatch)
    assert calls == [] and entry["closed"] is False and entry["reason_code"] == ab.AUTHORITY_TOOLCHAIN_MISMATCH


def test_the_production_closer_refuses_a_base_that_already_held_a_project(tmp_path, monkeypatch):
    case = _closure(tmp_path)
    candidate = tmp_path / "brown"
    candidate.mkdir()
    (candidate / "pom.xml").write_text("<project/>\n")
    _git(candidate, "init", "-q")
    _git(candidate, *IDENTITY, "add", "-A")
    _git(candidate, *IDENTITY, "commit", "-q", "-m", "an existing project")
    [entry], calls = _evidence_call(tmp_path, case, candidate, monkeypatch)
    assert calls == [] and entry["closed"] is False and entry["reason_code"] == ab.AUTHORITY_BASE_MISMATCH


# ---------------------------------------------------------------- unchanged: a concrete-base authority

def test_a_concrete_base_authority_ignores_the_greenfield_facts_and_keeps_its_own_rules(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "pom.xml").write_text("<project/>\n")
    _git(repo, "init", "-q")
    _git(repo, *IDENTITY, "add", "-A")
    _git(repo, *IDENTITY, "commit", "-q", "-m", "base")
    head = _git(repo, "rev-parse", "HEAD")
    manifest = _manifest(tmp_path, head)
    bundle = _load(tmp_path, manifest, repo, base=head, language="java")
    assert _refusal(tmp_path, manifest, repo, base=head, language="python") == ab.AUTHORITY_TOOLCHAIN_MISMATCH   # the brownfield language rule is untouched
    engine, _cfg = _engine(tmp_path, bundle)
    requirements = derive_requirements(GOAL)
    contract = workflow.compile_run_contract(engine, GOAL, GOAL, str(repo), requirements)
    ledger = ObligationLedger()
    seed_requirement_obligations(ledger, requirements)
    record_requirement_verdicts(ledger, requirements, {r.id: (RequirementOutcome.UNVERIFIED, "x") for r in requirements.requirements},
                                revision=1, evidence_fingerprint="cand", source="test")
    run = ab.AuthorityRun(verdict=ab.VERDICT_PASS, reason_code="x", reason="scripted")
    [other] = ab.close_requirements_with_authority_bundle(ledger, requirements, bundle, contract, execute=lambda: run, source="t", revision=1, base_revision="c" * 40,
                                                          greenfield_base_proven=lambda: True, candidate_language=lambda: "java")
    assert other["closed"] is False and other["reason_code"] == ab.AUTHORITY_BASE_MISMATCH          # another base never consumes it, whatever the greenfield facts say
    [entry] = ab.close_requirements_with_authority_bundle(ledger, requirements, bundle, contract, execute=lambda: run, source="t", revision=1, base_revision=head,
                                                          greenfield_base_proven=lambda: False, candidate_language=lambda: "python")
    assert entry["closed"] is True
