"""S4 step 2 (C-04, R-04): mutation transaction authority for skills, and the project-authority path class of the write gate.

Measured on 4bf42c1 before the change (~/kriya-evidence/s4-4bf42c1/reproducers_pre_fix_4bf42c1.txt):
  R1  a run for a FOREIGN workspace appended an extracted rule and its provenance to a skill of the shipped library;
  R2  on the direct shape a skeleton and an extracted rule moved the workspace repository's HEAD during the run;
  R3  a folder a run had left in the installation's skills directory was loaded with trusted rules, like a shipped skill;
  R4  two projects' runs both wrote one explicit skills directory outside both;
  R5  the write gate allowed a model-directed write to the project's kriya.yaml and to a file of its workspace skills root.

The invariant: a persistent skill mutation goes through ONE entry on the Skills owner (`SkillEngine.write_skill_path`), which decides by
path class and actor; a run writes only the project's own roots, inside one transaction that becomes durable (one commit) only when the
run's candidate passed, and never before.
"""
import ast
import inspect
import os
import subprocess
from pathlib import Path

import pytest

import kriya
import kriya.skills.authority as authority
import kriya.skills.skill as skill_module
from kriya.config import resolution
from kriya.knowledge import scaffold, staging
from kriya.knowledge.schema import KnowledgeFact
from kriya.policy import filesystem
from kriya.policy.filesystem import PROJECT_AUTHORITY_PATH_DENIED, AuthorizedFileWriter, project_authority_relpaths
from kriya.policy.model import PolicyDecision
from kriya.skills.authority import SKILL_WRITE_REASON_CODES, SkillPathClass
from kriya.skills.skill import (
    HeldSkillVerification,
    SkillEngine,
    SkillWriteRefused,
    mark_rules_verified,
    record_conflict_resolution,
    record_rule_provenance,
)
from kriya.tools.knowledge import KnowledgeGuard
from kriya.workflow.skill_extraction import _stage_skill_conflicts, _write_skill_extraction

RULE = "Always close the widget client in a finally block before the test exits."
SHIPPED_RULE = "Use the thin client."


def _git(cwd, *args):
    return subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", "-c", "commit.gpgsign=false", *args], cwd=str(cwd),
                          check=True, capture_output=True, text=True).stdout.strip()


def _skill(root, name, rules=SHIPPED_RULE + "\n"):
    folder = Path(root) / name
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "skill.yaml").write_text(f"name: {name}\ndescription: d\ntags: [{name}]\n")
    (folder / "rules.txt").write_text(rules)
    return folder


def _tree(root):
    """Every file under ``root`` with its bytes (git internals left out): what "zero bytes changed" is judged on."""
    found = {}
    for current, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d != ".git"]
        for name in filenames:
            path = os.path.join(current, name)
            found[os.path.relpath(path, root)] = Path(path).read_bytes()
    return found


@pytest.fixture
def install(tmp_path, monkeypatch):
    """A Kriya installation that is a source checkout: its repository's HEAD holds the shipped skill ``widgetlib``."""
    install_dir = tmp_path / "install"
    shipped = install_dir / "skills"
    _skill(shipped, "widgetlib")
    _git(install_dir, "init", "-q")
    _git(install_dir, "add", "-A")
    _git(install_dir, "commit", "-q", "-m", "shipped")
    monkeypatch.setattr(skill_module, "get_global_skills_dir", lambda: str(shipped))
    authority.reset_shipped_identity_cache()
    yield shipped
    authority.reset_shipped_identity_cache()


@pytest.fixture
def workspace(tmp_path):
    """A project repository that tracks its own ./skills (one skill, ``qpid``)."""
    ws = tmp_path / "ws"
    _skill(ws / "skills", "qpid")
    (ws / "README.md").write_text("project\n")
    _git(ws, "init", "-q")
    _git(ws, "add", "-A")
    _git(ws, "commit", "-q", "-m", "base")
    return ws


def _run_engine(root, ws, **kwargs):
    engine = SkillEngine(str(root), load_global=False, load_cwd=False, workspace_path=str(ws), **kwargs)
    engine.discover_and_load()
    return engine


def _fact():
    return KnowledgeFact(category="Dependencies", key="k", value="Uses widgetlib 2.0.", source_channel="live_failure",
                         extraction_confidence="human_supplied", provenance="p")


# ------------------------------------------------------------------ the path class

def test_the_path_classes_of_one_run(install, workspace, tmp_path):
    overlay, explicit = tmp_path / "state" / "projects" / "p1" / "skills", tmp_path / "operator-skills"
    for root in (overlay, explicit):
        root.mkdir(parents=True)
    in_repo = _run_engine(workspace / "skills", workspace)
    assert in_repo.classify_path(str(workspace / "skills" / "qpid" / "rules.txt")) is SkillPathClass.PROJECT_MUTABLE
    assert in_repo.classify_path(str(workspace / "skills")) is SkillPathClass.PROJECT_MUTABLE            # the root itself
    assert in_repo.classify_path(str(install / "widgetlib" / "rules.txt")) is SkillPathClass.PRODUCT_SHIPPED_READ_ONLY
    assert in_repo.classify_path(str(tmp_path / "elsewhere" / "x" / "rules.txt")) is SkillPathClass.FOREIGN_UNKNOWN   # under no root of this engine
    assert in_repo.classify_path(str(workspace / "src" / "rules.txt")) is SkillPathClass.FOREIGN_UNKNOWN            # in the project, not a skills root
    with_overlay = _run_engine(overlay, workspace, project_overlay=str(overlay))
    assert with_overlay.classify_path(str(overlay / "auto-ws" / "rules.txt")) is SkillPathClass.PROJECT_MUTABLE
    assert _run_engine(overlay, workspace).classify_path(str(overlay / "auto-ws" / "rules.txt")) is SkillPathClass.EXPLICIT_SHARED   # the same directory, not named as the overlay
    assert _run_engine(explicit, workspace).classify_path(str(explicit / "lib" / "rules.txt")) is SkillPathClass.EXPLICIT_SHARED
    assert _run_engine(explicit, workspace, project_overlay=str(overlay)).classify_path(str(explicit / "lib")) is SkillPathClass.EXPLICIT_SHARED


def test_the_reason_codes_are_a_closed_table():
    assert SKILL_WRITE_REASON_CODES == {
        "SKILL_WRITE_SHIPPED_READ_ONLY", "SKILL_WRITE_EXPLICIT_ROOT_OPERATOR_ONLY", "SKILL_WRITE_PATH_UNCLASSIFIED",
        "SKILL_WRITE_RUN_TRANSACTION_UNAVAILABLE"}
    assert {member.value for member in SkillPathClass} == {"project_mutable", "product_shipped_read_only", "explicit_shared", "foreign_unknown"}


# ------------------------------------------------------------------ negative 1: a run never mutates a shipped skill (R1)

def _shipped_writers(engine, shipped):
    skill = engine.get_skill("widgetlib")
    folder = str(shipped / "widgetlib")
    return {
        "write_skill_extraction": lambda: _write_skill_extraction(skill, {"rules": [RULE], "examples": {"e.py": "x\n"}}, source="live_lookup:http://x", engine=engine),
        "stage_skill_conflicts": lambda: _stage_skill_conflicts(skill, [{"candidate_rule": RULE, "conflicts_with": SHIPPED_RULE, "reason": "r"}], engine=engine),
        "record_rule_provenance": lambda: record_rule_provenance(folder, RULE, "human_text", engine=engine),
        "mark_rules_verified": lambda: mark_rules_verified(folder, [SHIPPED_RULE], project="p", engine=engine),
        "record_conflict_resolution": lambda: record_conflict_resolution(str(shipped), "widgetlib", RULE, "other", RULE, "prefer_a", engine=engine),
        "stage_facts": lambda: staging.stage_facts(folder, [_fact()], engine=engine),
        "record_direct_fact": lambda: staging.record_direct_fact(folder, _fact(), engine=engine),
        "record_scaffold_answer": lambda: scaffold.record_scaffold_answer(folder, "Dependencies", "Uses widgetlib 2.0.", engine=engine),
        "mark_verified": lambda: engine.mark_verified("widgetlib", context="c"),
        "mark_unverified": lambda: engine.mark_unverified("widgetlib"),
        "mark_gap_acknowledged": lambda: engine.mark_gap_acknowledged("widgetlib"),
        "create_skill_skeleton": lambda: engine.create_skill_skeleton("newlib"),
        "generate_skill_template": lambda: KnowledgeGuard(skills_dir=str(shipped), cutoff_date_str="2023-12-01", offline=True).generate_skill_template(
            "org.example:newlib", "1.0", engine=engine),
        "write_skill_path": lambda: engine.write_skill_path(os.path.join(folder, "rules.txt"), "replaced\n"),
        "remove": lambda: engine.write_skill_path(os.path.join(folder, "rules.txt"), None),
    }


SHIPPED_WRITER_SITES = (
    "create_skill_skeleton", "generate_skill_template", "mark_gap_acknowledged", "mark_rules_verified", "mark_unverified", "mark_verified",
    "record_conflict_resolution", "record_direct_fact", "record_rule_provenance", "record_scaffold_answer", "remove", "stage_facts",
    "stage_skill_conflicts", "write_skill_extraction", "write_skill_path",
)


def test_the_shipped_writer_table_is_complete(install, tmp_path):
    assert sorted(_shipped_writers(_run_engine(install, tmp_path), install)) == sorted(SHIPPED_WRITER_SITES)


@pytest.mark.parametrize("site", SHIPPED_WRITER_SITES)
def test_no_writer_of_a_run_changes_a_byte_of_the_shipped_library(install, tmp_path, site):
    foreign = tmp_path / "foreign"
    foreign.mkdir()
    engine = _run_engine(install, foreign)
    before, head = _tree(install), _git(install.parent, "rev-parse", "HEAD")
    try:
        _shipped_writers(engine, install)[site]()
    except SkillWriteRefused as refusal:
        assert refusal.reason_code == "SKILL_WRITE_SHIPPED_READ_ONLY"
    assert _tree(install) == before and _git(install.parent, "rev-parse", "HEAD") == head
    assert _git(install.parent, "status", "--porcelain") == ""


def test_the_refusal_is_typed_and_says_where_learning_belongs(install, tmp_path):
    engine = _run_engine(install, tmp_path)
    with pytest.raises(SkillWriteRefused) as refusal:
        engine.write_skill_path(str(install / "widgetlib" / "rules.txt"), "x\n")
    assert refusal.value.reason_code == "SKILL_WRITE_SHIPPED_READ_ONLY" and "SHARED install skills directory" in str(refusal.value)
    assert engine.new_skill_refusal("newlib") is not None and not (install / "newlib").exists()


def test_a_run_inside_the_kriya_installation_itself_does_not_write_the_shipped_library_either(install):
    """The workspace IS the installation (Kriya developed with Kriya): the library is still read-only to an ordinary run."""
    engine = _run_engine(install, install.parent)
    with pytest.raises(SkillWriteRefused):
        engine.create_skill_skeleton("newlib")
    assert engine.mark_verified("widgetlib", context="c") is False
    assert _git(install.parent, "status", "--porcelain") == ""


def test_the_operators_promotion_writes_the_shipped_library_and_commits_it(install):
    """`kriya skills promote`: an engine the person points at the library itself (cli.py). The one route into the shipped library."""
    engine = SkillEngine(str(install), load_global=False, operator=True)
    rules = str(install / "widgetlib" / "rules.txt")
    engine.write_skill_path(rules, SHIPPED_RULE + "\n" + RULE + "\n")
    assert engine.commit_skill_change(rules, "Kriya: promote 1 rule(s)") is True
    assert _git(install.parent, "log", "-1", "--format=%s") == "Kriya: promote 1 rule(s)" and _git(install.parent, "status", "--porcelain") == ""


def test_an_operator_command_from_another_projects_directory_still_does_not_write_the_shipped_library(install, tmp_path):
    """MA7.4 is kept: `kriya skills unverify <shipped skill>` typed in some other project is refused."""
    engine = _run_engine(install, tmp_path, operator=True)
    assert engine.mark_unverified("widgetlib") is False
    assert _git(install.parent, "status", "--porcelain") == ""


# ------------------------------------------------------------------ shipped identity is positive, never the location (R3)

def test_only_content_the_product_repository_holds_is_shipped(install, tmp_path):
    _skill(install, "auto-legacy", rules="A rule an old run left here.\n")            # untracked: a run-created folder
    engine = _run_engine(install, tmp_path)
    assert engine.get_skill("widgetlib").trusted_rules == [SHIPPED_RULE]
    assert engine.get_skill("auto-legacy").rules == ["A rule an old run left here."] and engine.get_skill("auto-legacy").trusted_rules == []
    assert engine.covers_library(["widgetlib"], [str(install)]) is True and engine.covers_library(["auto-legacy"], [str(install)]) is False


def test_a_shipped_skill_a_run_appended_to_is_no_longer_shipped_content(install, tmp_path):
    with open(install / "widgetlib" / "rules.txt", "a", encoding="utf-8") as handle:
        handle.write(RULE + "\n")                                                    # what R1 did, by a writer that is gone
    assert _run_engine(install, tmp_path).get_skill("widgetlib").trusted_rules == []


@pytest.mark.parametrize("planted", ["rule_provenance.json", "instructions.md", "examples/Planted.java"])
def test_a_file_the_loader_reads_that_the_product_does_not_ship_makes_the_skill_unshipped(install, tmp_path, planted):
    target = install / "widgetlib" / planted
    target.parent.mkdir(exist_ok=True)
    target.write_text("[]" if planted.endswith(".json") else "planted\n")
    assert _run_engine(install, tmp_path).get_skill("widgetlib").trusted_rules == []


def test_a_file_the_loader_does_not_read_leaves_the_shipped_skill_shipped(install, tmp_path):
    (install / "widgetlib" / "staged_rules.txt").write_text("[CONFLICT] left by an old run\n")
    assert _run_engine(install, tmp_path).get_skill("widgetlib").trusted_rules == [SHIPPED_RULE]


def test_a_verified_claim_in_an_unshipped_install_folder_is_not_believed(install, tmp_path):
    folder = _skill(install, "auto-legacy")
    (folder / "skill.yaml").write_text("name: auto-legacy\ndescription: d\ntags: [auto-legacy]\nverified: true\nverification_gap_acknowledged: true\n")
    loaded = _run_engine(install, tmp_path).get_skill("auto-legacy")
    assert loaded.verified is False and loaded.verified_claim is True and loaded.verification_gap_acknowledged is False


def test_another_installations_distribution_record_does_not_mask_the_checkouts_own_identity(install, tmp_path, monkeypatch):
    """The environment holds the metadata of a Kriya installed somewhere else (an editable install of another checkout, a wheel in
    site-packages beside this checkout): its RECORD describes THAT directory and says nothing about this one."""
    import importlib.metadata

    elsewhere = tmp_path / "other-site-packages" / "skills" / "widgetlib"
    elsewhere.mkdir(parents=True)
    (elsewhere / "rules.txt").write_text("another installation\n")

    class _Hash:
        mode, value = "sha256", "AAAA"

    class _File:
        parts, hash = ("skills", "widgetlib", "rules.txt"), _Hash()

    class _Distribution:
        files = [_File()]

        @staticmethod
        def locate_file(item):
            return elsewhere / "rules.txt"

    monkeypatch.setattr(importlib.metadata, "distribution", lambda name: _Distribution())
    authority.reset_shipped_identity_cache()
    assert _run_engine(install, tmp_path).get_skill("widgetlib").trusted_rules == [SHIPPED_RULE]


def test_an_installation_with_no_canonical_metadata_ships_nothing(tmp_path, monkeypatch):
    """Neither a distribution RECORD nor a product repository (an unpacked source archive): nothing can be shown to be shipped."""
    shipped = tmp_path / "unpacked" / "skills"
    _skill(shipped, "widgetlib")
    monkeypatch.setattr(skill_module, "get_global_skills_dir", lambda: str(shipped))
    authority.reset_shipped_identity_cache()
    assert authority.shipped_identity(str(shipped)) is None
    assert _run_engine(shipped, tmp_path).get_skill("widgetlib").trusted_rules == []
    authority.reset_shipped_identity_cache()


def test_a_site_packages_directory_inside_some_other_repository_proves_nothing(tmp_path, monkeypatch):
    """The installation directory is not the work tree's root: a user's repository that happens to hold the files is not the product's."""
    repo = tmp_path / "user-repo"
    shipped = repo / "venv" / "site-packages" / "skills"
    _skill(shipped, "widgetlib")
    _git(repo, "init", "-q")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "the user committed a venv")
    monkeypatch.setattr(skill_module, "get_global_skills_dir", lambda: str(shipped))
    authority.reset_shipped_identity_cache()
    assert authority.shipped_identity(str(shipped)) is None
    authority.reset_shipped_identity_cache()


def test_a_wheel_installation_is_judged_by_its_distribution_record(tmp_path, monkeypatch):
    import base64
    import hashlib
    import importlib.metadata

    shipped = tmp_path / "site-packages" / "skills"
    folder = _skill(shipped, "widgetlib")
    _skill(shipped, "auto-legacy")

    class _Hash:
        mode = "sha256"

        def __init__(self, data):
            self.value = base64.urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(b"=").decode("ascii")

    class _File:
        def __init__(self, relative):
            self.parts = tuple(relative.split("/"))
            self.relative = relative
            self.hash = _Hash((tmp_path / "site-packages" / relative).read_bytes())

    class _Distribution:
        files = [_File("skills/widgetlib/skill.yaml"), _File("skills/widgetlib/rules.txt")]

        @staticmethod
        def locate_file(item):
            return tmp_path / "site-packages" / item.relative

    monkeypatch.setattr(importlib.metadata, "distribution", lambda name: _Distribution())
    monkeypatch.setattr(skill_module, "get_global_skills_dir", lambda: str(shipped))
    authority.reset_shipped_identity_cache()
    engine = _run_engine(shipped, tmp_path)
    assert engine.get_skill("widgetlib").trusted_rules == [SHIPPED_RULE] and engine.get_skill("auto-legacy").trusted_rules == []
    (folder / "rules.txt").write_text(SHIPPED_RULE + "\n" + RULE + "\n")              # changed after installation
    assert _run_engine(shipped, tmp_path).get_skill("widgetlib").trusted_rules == []
    authority.reset_shipped_identity_cache()


# ------------------------------------------------------------------ negative 2: an explicit root outside the project (R4)

def test_a_configured_path_outside_the_project_gives_a_run_no_write_authority(install, workspace, tmp_path):
    shared = tmp_path / "shared-skills"
    _skill(shared, "sharedlib")
    before = _tree(shared)
    for project in (workspace, tmp_path / "other-project"):
        project.mkdir(exist_ok=True)
        engine = _run_engine(shared, project)
        with pytest.raises(SkillWriteRefused) as refusal:
            engine.create_skill_skeleton("newlib")
        assert refusal.value.reason_code == "SKILL_WRITE_EXPLICIT_ROOT_OPERATOR_ONLY"
        assert _write_skill_extraction(engine.get_skill("sharedlib"), {"rules": [RULE], "examples": {}}, source="human_text", engine=engine) is False
        assert engine.mark_verified("sharedlib", context="c") is False
        assert engine.mark_gap_acknowledged("sharedlib") is True and engine.is_gap_acknowledged(engine.get_skill("sharedlib")) is True   # held for the run only
        assert engine.activate_run_shadow(str(project), str(tmp_path / "state")) is False                                              # nothing of it to transact
    assert _tree(shared) == before


def test_the_operators_own_command_writes_the_explicit_root(install, workspace, tmp_path):
    shared = tmp_path / "shared-skills"
    _skill(shared, "sharedlib")
    engine = _run_engine(shared, workspace, operator=True)
    engine.create_skill_skeleton("newlib")
    assert engine.mark_verified("sharedlib", context="c") is True
    assert (shared / "newlib" / "skill.yaml").is_file() and "verified: true" in (shared / "sharedlib" / "skill.yaml").read_text()


def test_a_writer_handed_no_engine_writes_as_the_narrowest_actor(install, tmp_path):
    """The module-level writers still take a path alone. With no engine they act as a RUN for the process directory's project, so a
    skills directory outside it is refused: there is no engine-less write."""
    shared = tmp_path / "shared-skills"
    folder = _skill(shared, "sharedlib")
    before = _tree(shared)
    record_rule_provenance(str(folder), RULE, "human_text")
    record_conflict_resolution(str(shared), "a", RULE, "b", RULE, "prefer_a")
    assert staging.stage_facts(str(folder), [_fact()]) == []
    with pytest.raises(SkillWriteRefused):
        staging.record_direct_fact(str(folder), _fact())
    guard = KnowledgeGuard(skills_dir=str(shared), cutoff_date_str="2023-12-01", offline=True)
    with pytest.raises(TypeError):   # the scaffold has no engine-less form at all: its caller names the Skills owner (kriya.tools builds no engine)
        guard.generate_skill_template("org.example:lib", "1.0")
    with pytest.raises(SkillWriteRefused):
        guard.generate_skill_template("org.example:lib", "1.0", engine=SkillEngine(str(shared), load_global=False, load_cwd=False))
    assert _tree(shared) == before


def test_a_path_under_no_skills_root_is_refused(install, workspace, tmp_path):
    engine = _run_engine(workspace / "skills", workspace)
    for target in (workspace / "src" / "App.java", tmp_path / "anywhere" / "rules.txt", workspace / "skills" / ".." / "README.md"):
        with pytest.raises(SkillWriteRefused) as refusal:
            engine.write_skill_path(str(target), "x\n")
        assert refusal.value.reason_code == "SKILL_WRITE_PATH_UNCLASSIFIED"
    assert (workspace / "README.md").read_text() == "project\n"
    assert _run_engine(workspace / "skills", workspace, operator=True).classify_path(str(tmp_path / "anywhere")) is SkillPathClass.FOREIGN_UNKNOWN
    with pytest.raises(SkillWriteRefused):
        _run_engine(workspace / "skills", workspace, operator=True).write_skill_path(str(tmp_path / "anywhere" / "rules.txt"), "x\n")   # an operator engine too


# ------------------------------------------------------------------ negatives 3 and 4: one transaction, durable only at a passing end (R2)

def _learn(engine):
    """What the analysis stage does when a person supplies reference material for one existing and one new skill."""
    engine.create_skill_skeleton("newlib")
    engine.discover_and_load()
    assert _write_skill_extraction(engine.get_skill("qpid"), {"rules": [RULE], "examples": {"e.py": "x\n"}}, source="human_text", engine=engine) is True
    _stage_skill_conflicts(engine.get_skill("qpid"), [{"candidate_rule": "c", "conflicts_with": SHIPPED_RULE, "reason": "r"}], engine=engine)
    record_conflict_resolution(engine.effective_skills_dir(engine.canonical_skills_dir), "qpid", RULE, "newlib", RULE, "prefer_a", engine=engine)
    engine.discover_and_load()


def test_a_run_that_does_not_pass_leaves_the_skills_and_the_repository_untouched(install, workspace, tmp_path):
    before, head = _tree(workspace), _git(workspace, "rev-parse", "HEAD")
    engine = SkillEngine(str(workspace / "skills"), load_global=False, load_cwd=False, workspace_path=str(workspace))
    engine.begin_run_learning(str(workspace), str(tmp_path / "state"))
    assert engine.run_shadow_active is True
    _learn(engine)
    assert RULE in engine.get_skill("qpid").rules and engine.get_skill("newlib") is not None      # the run itself sees what it learned ...
    assert _tree(workspace) == before and _git(workspace, "rev-parse", "HEAD") == head            # ... and nothing durable changed meanwhile
    engine.discard_run_learning()
    assert _tree(workspace) == before and _git(workspace, "rev-parse", "HEAD") == head and _git(workspace, "status", "--porcelain") == ""
    assert not os.path.exists(tmp_path / "state" / "run_shadow")


def test_a_run_that_passes_persists_its_learning_once_as_one_commit(install, workspace, tmp_path):
    head = _git(workspace, "rev-parse", "HEAD")
    engine = SkillEngine(str(workspace / "skills"), load_global=False, load_cwd=False, workspace_path=str(workspace))
    engine.begin_run_learning(str(workspace), str(tmp_path / "state"))
    _learn(engine)
    engine.hold_verifications([HeldSkillVerification("qpid", "qpid 9", (RULE,))])
    assert _git(workspace, "rev-parse", "HEAD") == head

    assert engine.persist_run_learning() == []

    assert int(_git(workspace, "rev-list", "--count", f"{head}..HEAD")) == 1                       # exactly one commit ...
    assert _git(workspace, "log", "-1", "--format=%s") == "Kriya: persist the skills this run learned" and _git(workspace, "status", "--porcelain") == ""
    rules = (workspace / "skills" / "qpid" / "rules.txt").read_text()
    assert rules.count(RULE) == 1 and (workspace / "skills" / "newlib" / "skill.yaml").is_file()   # ... holding each write exactly once
    assert engine.persist_run_learning() == [] and int(_git(workspace, "rev-list", "--count", f"{head}..HEAD")) == 1   # nothing is persisted twice


def test_the_projects_overlay_outside_the_repository_is_in_the_same_transaction(install, workspace, tmp_path):
    overlay = tmp_path / "state" / "projects" / "p1" / "skills"
    _skill(overlay, "qpid")
    before = _tree(overlay)
    engine = SkillEngine(str(overlay), load_global=False, load_cwd=False, workspace_path=str(workspace), project_overlay=str(overlay))
    engine.begin_run_learning(str(workspace), str(tmp_path / "state"))
    assert engine.run_shadow_active is True
    _learn(engine)
    assert _tree(overlay) == before
    engine.discard_run_learning()
    assert _tree(overlay) == before

    engine = SkillEngine(str(overlay), load_global=False, load_cwd=False, workspace_path=str(workspace), project_overlay=str(overlay))
    engine.begin_run_learning(str(workspace), str(tmp_path / "state"))
    _learn(engine)
    assert engine.persist_run_learning() == []
    assert RULE in (overlay / "qpid" / "rules.txt").read_text() and (overlay / "newlib" / "skill.yaml").is_file()


def test_a_run_whose_transaction_cannot_start_writes_no_skill_at_all(install, workspace, tmp_path):
    """The state directory lies inside the repository the run binds: the shadow is refused, and with it every skill write of the run."""
    before = _tree(workspace)
    engine = SkillEngine(str(workspace / "skills"), load_global=False, load_cwd=False, workspace_path=str(workspace))
    engine.begin_run_learning(str(workspace), str(workspace / ".state"))
    engine.discover_and_load()
    assert engine.run_shadow_active is False
    with pytest.raises(SkillWriteRefused) as refusal:
        engine.create_skill_skeleton("newlib")
    assert refusal.value.reason_code == "SKILL_WRITE_RUN_TRANSACTION_UNAVAILABLE"
    assert _write_skill_extraction(engine.get_skill("qpid"), {"rules": [RULE], "examples": {}}, source="human_text", engine=engine) is False
    assert {k: v for k, v in _tree(workspace).items() if not k.startswith(".state")} == before


def test_no_writer_of_a_run_commits_and_an_operators_command_does(install, workspace):
    run = _run_engine(workspace / "skills", workspace)
    head = _git(workspace, "rev-parse", "HEAD")
    run.create_skill_skeleton("runlib")
    assert run.commit_skill_change(str(workspace / "skills" / "runlib"), "must not be committed") is True
    assert _git(workspace, "rev-parse", "HEAD") == head
    operator = _run_engine(workspace / "skills", workspace, operator=True)
    operator.create_skill_skeleton("operatorlib")
    assert _git(workspace, "rev-parse", "HEAD") != head and "operatorlib" in _git(workspace, "log", "-1", "--format=%s")


@pytest.mark.asyncio
async def test_a_failed_direct_shape_run_leaves_the_skills_directory_and_head_unchanged(tmp_path, monkeypatch):
    """C-04's acceptance proof, through the real `run_generation_workflow` (the direct shape): the person supplies reference material, the
    run extracts a rule into the skill (this used to be written and committed during analysis), then its model fails. Before: the commit
    and the changed skills directory survived the failed run (BYP-A4-07)."""
    import json

    from test_midrun_workspace_mutations import ALPHA_RULE, _run_standalone
    from test_midrun_workspace_mutations import _git as git

    extraction = json.dumps({"rules": [ALPHA_RULE], "examples": {}, "conflicts": []})
    written = []
    real_write = SkillEngine.write_skill_path

    def spy(self, path, text):
        real_write(self, path, text)
        written.append((os.path.realpath(path), text))

    monkeypatch.setattr(SkillEngine, "write_skill_path", spy)
    outcome = {}
    try:
        result, _timeline = await _run_standalone(
            tmp_path, {"brokeralpha": ("", "")}, [extraction, RuntimeError("the model is gone")],
            "Build something using the brokeralpha skill",
            skill_gap_callback=lambda reason, names: "Reference material establishing the alpha broker's port rule.")
        outcome["passed"] = result.get("quality_gates_passed")
    except Exception as error:  # noqa: BLE001 - however the failed run ends, the invariant below is what is proven
        outcome["raised"] = type(error).__name__
    ws = tmp_path / "ws"
    assert outcome.get("passed") is not True
    # The learning step really ran and really wrote the rule - into the run's transaction, never into the repository's skills.
    rule_writes = [path for path, text in written if path.endswith(os.path.join("brokeralpha", "rules.txt")) and ALPHA_RULE in (text or "")]
    assert rule_writes and all(not path.startswith(os.path.realpath(ws)) for path, _ in written), written
    assert int(git(ws, "rev-list", "--count", "HEAD")) == 1 and git(ws, "status", "--porcelain", "--", "skills") == ""
    assert (ws / "skills" / "brokeralpha" / "rules.txt").read_text() == ""


# ------------------------------------------------------------------ the write gate's project-authority class (R5, A5 P5)

def test_the_configuration_names_are_the_ones_configuration_discovers():
    assert filesystem.PROJECT_AUTHORITY_FILE_NAMES == resolution._CONFIG_FILE_NAMES == ("kriya.yaml", "kriya.yml")


@pytest.mark.parametrize("relative", ["kriya.yaml", "kriya.yml", "KRIYA.YAML", "Kriya.Yml", "service/kriya.yaml", "a/b/kriya.yml"])
def test_a_candidate_write_never_reaches_a_project_configuration_file(tmp_path, relative):
    result = AuthorizedFileWriter(str(tmp_path)).authorize(str(tmp_path / relative))
    assert result.decision is PolicyDecision.DENY and result.reason_code == PROJECT_AUTHORITY_PATH_DENIED


def test_a_candidate_write_never_reaches_the_configuration_file_through_another_name(tmp_path):
    (tmp_path / "kriya.yaml").write_text("paths: {}\n")
    os.symlink(tmp_path / "kriya.yaml", tmp_path / "notes.txt")
    result = AuthorizedFileWriter(str(tmp_path)).authorize(str(tmp_path / "notes.txt"))
    assert result.decision is PolicyDecision.DENY and result.reason_code == PROJECT_AUTHORITY_PATH_DENIED


def test_a_candidate_write_never_reaches_the_workspace_skills_root(tmp_path):
    _skill(tmp_path / "skills", "qpid")
    relpaths = project_authority_relpaths(str(tmp_path), str(tmp_path / "skills"))
    assert relpaths == ("skills",)
    writer = AuthorizedFileWriter(str(tmp_path), project_authority_relpaths=relpaths)
    for relative in ("skills/qpid/rules.txt", "skills/qpid/skill.yaml", "skills/new/skill.yaml", "skills"):
        result = writer.authorize(str(tmp_path / relative))
        assert result.decision is PolicyDecision.DENY and result.reason_code == PROJECT_AUTHORITY_PATH_DENIED, relative
    for relative in ("src/App.java", "skills-of-the-app/model.py", "docs/skills.md", "kriya_yaml.py"):
        assert writer.authorize(str(tmp_path / relative)).decision is PolicyDecision.ALLOW, relative


def test_an_ordinary_directory_named_skills_is_source_when_it_is_not_the_projects_skills_root(tmp_path):
    """The class is the CONFIGURED root, never the name: a project whose skills live elsewhere may have source under ./skills."""
    assert project_authority_relpaths(str(tmp_path), str(tmp_path.parent / "elsewhere" / "skills")) == ()
    assert project_authority_relpaths(str(tmp_path), None) == () and project_authority_relpaths(str(tmp_path), str(tmp_path)) == ()
    assert AuthorizedFileWriter(str(tmp_path)).authorize(str(tmp_path / "skills" / "matcher.py")).decision is PolicyDecision.ALLOW


def test_the_run_hands_the_skills_root_to_the_candidate_commit():
    """The per-run half reaches the two writers of the candidate transaction (the attempt's batch commit and the pom correction)."""
    import kriya.workflow.candidate_transaction as candidate_transaction
    import kriya.workflow.gate_orchestrator as gate_orchestrator
    from kriya.workflow.attempt import AttemptContext

    assert AttemptContext.__dataclass_fields__["project_authority_relpaths"].default == ()
    for module in (candidate_transaction, gate_orchestrator):
        assert "project_authority_relpaths=ctx.project_authority_relpaths" in inspect.getsource(module), module.__name__
    import kriya.workflow.development_loop as development_loop
    import kriya.workflow.workflow as workflow

    assert "project_authority_relpaths=tuple(ctx.project_authority_relpaths or ())" in inspect.getsource(development_loop)
    assert "project_authority_relpaths=project_authority_relpaths," in inspect.getsource(workflow)


# ------------------------------------------------------------------ structure: one entry, and every caller names its owner

PRODUCT = Path(kriya.__file__).resolve().parent
WRITERS = {"record_rule_provenance", "mark_rules_verified", "record_conflict_resolution", "stage_facts", "promote_staged", "record_direct_fact",
           "record_scaffold_answer", "_write_skill_extraction", "_stage_skill_conflicts", "generate_skill_template"}
RAW_WRITES = {"atomic_write_file", "git_commit_if_tracked", "git_commit_paths"}


def _raw_writes(node):
    """Names of the raw write primitives a syntax tree uses: open() in a writing mode, os.remove / os.unlink, shutil writers, and RAW_WRITES."""
    found = []
    for call in (n for n in ast.walk(node) if isinstance(n, ast.Call)):
        name = call.func.id if isinstance(call.func, ast.Name) else call.func.attr if isinstance(call.func, ast.Attribute) else ""
        if name == "open":
            mode = next((k.value for k in call.keywords if k.arg == "mode"), call.args[1] if len(call.args) > 1 else None)
            if isinstance(mode, ast.Constant) and isinstance(mode.value, str) and set(mode.value) & set("wax+"):
                found.append("open:" + mode.value)
        elif name in RAW_WRITES or name in {"write_text", "write_bytes"}:
            found.append(name)
        elif isinstance(call.func, ast.Attribute) and isinstance(call.func.value, ast.Name) and (
                (call.func.value.id == "os" and name in {"remove", "unlink", "rename", "replace"})
                or (call.func.value.id == "shutil" and name in {"rmtree", "copyfile", "copy", "copytree", "move"})):
            found.append(name)
    return sorted(found)


def _function(module_path, name):
    tree = ast.parse(module_path.read_text(encoding="utf-8"))
    return next(n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == name)


@pytest.mark.parametrize("relative", ["workflow/skill_extraction.py", "knowledge/staging.py", "knowledge/scaffold.py"])
def test_the_skill_writers_outside_the_owner_hold_no_raw_write(relative):
    assert _raw_writes(ast.parse((PRODUCT / relative).read_text(encoding="utf-8"))) == []


@pytest.mark.parametrize("relative,name", [("tools/knowledge.py", "generate_skill_template"), ("cli.py", "skills_approve"),
                                           ("cli.py", "skills_promote"), ("cli.py", "skills_gaps"), ("cli.py", "skills_unverify"),
                                           ("analyzer/analyzer.py", "index_repository")])
def test_the_skill_writing_functions_elsewhere_hold_no_raw_write(relative, name):
    assert _raw_writes(_function(PRODUCT / relative, name)) == []


def test_the_owner_itself_writes_only_inside_the_entry_and_commits_only_through_its_own_funnel():
    tree = ast.parse((PRODUCT / "skills" / "skill.py").read_text(encoding="utf-8"))
    by_function = {n.name: _raw_writes(n) for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
    holders = {name: writes for name, writes in by_function.items() if writes}
    assert holders == {
        "write_skill_path": ["atomic_write_file", "remove"],        # THE entry: the one place a byte under a skills root is written or removed
        "commit_skill_change": ["git_commit_if_tracked"],           # the one commit funnel: an operator's command, or the end-of-run persistence
    }


def test_every_production_caller_of_a_skill_writer_names_its_owner():
    missing = []
    for path in sorted(PRODUCT.rglob("*.py")):
        for call in (n for n in ast.walk(ast.parse(path.read_text(encoding="utf-8"))) if isinstance(n, ast.Call)):
            name = call.func.id if isinstance(call.func, ast.Name) else call.func.attr if isinstance(call.func, ast.Attribute) else ""
            if name in WRITERS and not any(keyword.arg == "engine" for keyword in call.keywords):
                missing.append(f"{path.relative_to(PRODUCT)}:{call.lineno} {name}")
    assert missing == []
