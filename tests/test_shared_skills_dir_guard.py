import os
from unittest.mock import AsyncMock, patch

from click.testing import CliRunner

from kriya.cli import main
from kriya.config import AppConfig
from kriya.skills.skill import get_global_skills_dir, is_accidental_shared_skills_write


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
