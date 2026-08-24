import os
from unittest.mock import AsyncMock, patch

import yaml
from click.testing import CliRunner

from kriya.cli import main
from kriya.config import AppConfig
from kriya.skills.skill import (
    SkillEngine,
    get_global_skills_dir,
    is_accidental_shared_skill_write,
    is_accidental_shared_skills_write,
)


def test_is_accidental_shared_skills_write_flags_shared_dir_for_other_project(tmp_path):
    """Core regression case: a project whose config doesn't override paths.skills
    (so it resolves to Kriya's own shared install skills/ dir) is exactly the
    accidental-fallback signature that caused a real incident live during this
    validation pass."""
    assert is_accidental_shared_skills_write(get_global_skills_dir(), str(tmp_path)) is True


def test_is_accidental_shared_skills_write_allows_kriya_working_on_itself():
    kriya_install_dir = os.path.dirname(get_global_skills_dir())
    assert is_accidental_shared_skills_write(get_global_skills_dir(), kriya_install_dir) is False
    # A subdirectory of the Kriya install itself (e.g. running from kriya/ or tests/) is fine too.
    assert is_accidental_shared_skills_write(get_global_skills_dir(), os.path.join(kriya_install_dir, "tests")) is False


def test_is_accidental_shared_skills_write_allows_project_local_dir(tmp_path):
    project_skills = str(tmp_path / "skills")
    assert is_accidental_shared_skills_write(project_skills, str(tmp_path)) is False


def test_analyze_warns_when_skills_dir_resolves_to_shared_install(tmp_path):
    """Regression test for a real incident caught live during this validation pass:
    running `kriya analyze` against a scratch project whose config didn't override
    paths.skills silently wrote a new auto-generated skill into the REAL Kriya
    repo's own shared skills/ directory - the exact same class of incident already
    documented multiple times in this project's history.

    Uses a FAKE global-skills-dir (patched via get_global_skills_dir, which is what
    the guard itself calls) rather than the real one, so this test - which is
    deliberately exercising the "resolves to the shared dir" scenario - never
    itself risks writing into the real repo's actual skills/ directory, matching
    the exact hygiene lesson this whole guard exists because of."""
    project_dir = tmp_path / "project"
    project_dir.mkdir()
    (project_dir / "app.py").write_text("def foo():\n    pass\n")

    fake_install_skills_dir = tmp_path / "fake_kriya_install" / "skills"
    fake_install_skills_dir.mkdir(parents=True)

    cfg = AppConfig()
    cfg.paths.skills = str(fake_install_skills_dir)

    async def mock_extract(*args, **kwargs):
        return {"description": "test", "instructions": "# test", "rules": ["rule"]}

    runner = CliRunner()
    with patch("kriya.cli.load_config", return_value=cfg), \
         patch("kriya.skills.skill.get_global_skills_dir", return_value=str(fake_install_skills_dir)), \
         patch("kriya.agents.extractor.ConventionsExtractorAgent.extract_conventions", new=AsyncMock(side_effect=mock_extract)), \
         patch("kriya.core.llm.LLMClient.__init__", return_value=None):
        res = runner.invoke(main, ["analyze", str(project_dir)])

    assert "SHARED install skills directory" in res.output
    # Confirms the warning fired for the RIGHT reason (the guard's real
    # comparison logic), not a coincidence - the skill still gets written to
    # the fake dir, never the real repo's own skills/.
    assert (fake_install_skills_dir / f"auto-{project_dir.name}").exists()


def _write_fake_skill(skills_dir, skill_name, verified_context="original-real-value"):
    skill_dir = skills_dir / skill_name
    skill_dir.mkdir(parents=True)
    (skill_dir / "skill.yaml").write_text(yaml.safe_dump({
        "name": skill_name, "description": "test skill",
        "verified": True, "verified_at": "2026-01-01", "verified_context": verified_context,
    }))
    return skill_dir


# --- MA7.4: is_accidental_shared_skill_write - the actual root-cause predicate ---

def test_is_accidental_shared_skill_write_flags_a_skill_inside_the_shared_dir(tmp_path):
    fake_global = tmp_path / "fake_kriya_install" / "skills"
    skill_dir = _write_fake_skill(fake_global, "auto-core")
    other_workspace = tmp_path / "unrelated_project"
    other_workspace.mkdir()
    with patch("kriya.skills.skill.get_global_skills_dir", return_value=str(fake_global)):
        assert is_accidental_shared_skill_write(str(skill_dir), str(other_workspace)) is True


def test_is_accidental_shared_skill_write_allows_kriya_working_on_itself(tmp_path):
    fake_kriya_install = tmp_path / "fake_kriya_install"
    fake_global = fake_kriya_install / "skills"
    skill_dir = _write_fake_skill(fake_global, "auto-core")
    with patch("kriya.skills.skill.get_global_skills_dir", return_value=str(fake_global)):
        assert is_accidental_shared_skill_write(str(skill_dir), str(fake_kriya_install)) is False


def test_is_accidental_shared_skill_write_allows_a_project_local_skill(tmp_path):
    fake_global = tmp_path / "fake_kriya_install" / "skills"
    fake_global.mkdir(parents=True)
    project_dir = tmp_path / "some_project"
    local_skill_dir = project_dir / "skills" / "auto-someproject"
    local_skill_dir.mkdir(parents=True)
    with patch("kriya.skills.skill.get_global_skills_dir", return_value=str(fake_global)):
        assert is_accidental_shared_skill_write(str(local_skill_dir), str(project_dir)) is False


# --- MA7.4: SkillEngine.mark_verified() really refuses the cross-project write ---

def test_mark_verified_refuses_to_overwrite_a_shared_skill_from_an_unrelated_workspace(tmp_path):
    """The actual durable-lesson-#4 incident, reproduced and now blocked:
    a run for one workspace must never overwrite a DIFFERENT, shared skill's
    real verification record."""
    fake_global = tmp_path / "fake_kriya_install" / "skills"
    skill_dir = _write_fake_skill(fake_global, "auto-core", verified_context="org.apache.ignite:ignite-core 2.18.0")
    other_workspace = tmp_path / "unrelated_python_project"
    other_workspace.mkdir()

    with patch("kriya.skills.skill.get_global_skills_dir", return_value=str(fake_global)):
        engine = SkillEngine(str(fake_global), load_global=False, workspace_path=str(other_workspace))
        engine.discover_and_load()
        result = engine.mark_verified("auto-core", context="unrelated-run-value")

    assert result is False
    on_disk = yaml.safe_load((skill_dir / "skill.yaml").read_text())
    assert on_disk["verified_context"] == "org.apache.ignite:ignite-core 2.18.0"


def test_mark_verified_still_works_for_the_kriya_install_workspace_itself(tmp_path):
    """The exact kriya skills promote/approve case - explicitly targeting the
    shared directory - must keep working, not be swept up by the new guard.
    No workspace_path is supplied here on purpose (matching the real
    SkillEngine(global_skills_dir, load_global=False) construction at
    cli.py's skills_promote) - __init__'s own default logic must recognize
    "skills_dir IS the shared dir" as intentional, regardless of cwd."""
    fake_kriya_install = tmp_path / "fake_kriya_install"
    fake_global = fake_kriya_install / "skills"
    _write_fake_skill(fake_global, "auto-core")

    with patch("kriya.skills.skill.get_global_skills_dir", return_value=str(fake_global)):
        engine = SkillEngine(str(fake_global), load_global=False)  # no workspace_path supplied
        assert engine.workspace_path == str(fake_kriya_install)
        engine.discover_and_load()
        result = engine.mark_verified("auto-core", context="promoted-value")

    assert result is True
    on_disk = yaml.safe_load((fake_global / "auto-core" / "skill.yaml").read_text())
    assert on_disk["verified_context"] == "promoted-value"


def test_mark_verified_unaffected_for_an_ordinary_project_local_skill(tmp_path):
    """The overwhelming common case (a project verifying its OWN local skill)
    must be completely unaffected by this guard."""
    project_dir = tmp_path / "some_project"
    local_skills = project_dir / "skills"
    skill_dir = _write_fake_skill(local_skills, "auto-someproject")
    fake_global = tmp_path / "fake_kriya_install" / "skills"
    fake_global.mkdir(parents=True)

    with patch("kriya.skills.skill.get_global_skills_dir", return_value=str(fake_global)):
        engine = SkillEngine(str(local_skills), load_global=False, workspace_path=str(project_dir))
        engine.discover_and_load()
        result = engine.mark_verified("auto-someproject", context="real-local-value")

    assert result is True
    on_disk = yaml.safe_load((skill_dir / "skill.yaml").read_text())
    assert on_disk["verified_context"] == "real-local-value"


def test_skills_create_warns_when_skills_dir_resolves_to_shared_install(tmp_path):
    fake_install_skills_dir = tmp_path / "fake_kriya_install" / "skills"
    fake_install_skills_dir.mkdir(parents=True)
    other_project_dir = tmp_path / "some_other_project"
    other_project_dir.mkdir()

    cfg = AppConfig()
    cfg.paths.skills = str(fake_install_skills_dir)
    runner = CliRunner()

    with patch("os.getcwd", return_value=str(other_project_dir)), \
         patch("kriya.cli.load_config", return_value=cfg), \
         patch("kriya.skills.skill.get_global_skills_dir", return_value=str(fake_install_skills_dir)):
        res = runner.invoke(main, ["skills", "create", "my-test-skill-xyz"])

    assert "SHARED install skills directory" in res.output
    assert (fake_install_skills_dir / "my-test-skill-xyz").exists()
