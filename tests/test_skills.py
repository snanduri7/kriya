import os
from unittest.mock import patch

from click.testing import CliRunner

from kriya.cli import main
from kriya.skills.skill import Skill, SkillEngine


def test_skills_lifecycle(tmp_path):
    skills_dir = tmp_path / "skills"
    skills_dir.mkdir()
    
    se = SkillEngine(str(skills_dir), load_global=False)
    
    # 1. Create skill skeleton
    path = se.create_skill_skeleton("FastAPI App")
    assert os.path.exists(path)
    assert os.path.exists(os.path.join(path, "skill.yaml"))
    assert os.path.exists(os.path.join(path, "instructions.md"))
    
    # Let's add rules and examples to the created skeleton folder
    with open(os.path.join(path, "rules.txt"), "w") as f:
        f.write("Always use async def\nNo sync dependencies")
    
    with open(os.path.join(path, "examples", "main.py"), "w") as f:
        f.write("# FastAPI example")
        
    # 2. Discover and Load
    se_load = SkillEngine(str(skills_dir), load_global=False)
    se_load.discover_and_load()
    
    skills = se_load.list_skills()
    assert len(skills) == 1
    
    skill = se_load.get_skill("FastAPI App")
    assert isinstance(skill, Skill)
    assert skill.name == "FastAPI App"
    assert "FastAPI App" in skill.description
    assert skill.rules == ["Always use async def", "No sync dependencies"]
    assert skill.examples == {"main.py": "# FastAPI example"}
    assert "fastapi-app" in skill.tags
    
    # Test find by tag
    tagged = se_load.find_skills_by_tag("fastapi-app")
    assert len(tagged) == 1
    assert tagged[0].name == "FastAPI App"

def test_is_version_supported():
    from kriya.skills.skill import is_version_supported
    
    # 1. Base cases
    assert is_version_supported("2.18.0", "*") is True
    assert is_version_supported("2.18.0", "") is True
    
    # 2. Operators
    assert is_version_supported("2.18.0", "==2.18.0") is True
    assert is_version_supported("2.18.0", "!=2.15.0") is True
    assert is_version_supported("2.18.0", ">=2.15.0") is True
    assert is_version_supported("2.18.0", "<=2.20.0") is True
    assert is_version_supported("2.18.0", ">2.17") is True
    assert is_version_supported("2.18.0", "<3.0") is True
    
    # 3. Mismatches
    assert is_version_supported("2.18.0", "==2.15.0") is False
    assert is_version_supported("2.18.0", "<2.15.0") is False
    assert is_version_supported("2.18.0", ">3.0.0") is False
    
    # 4. Range combinations
    assert is_version_supported("2.18.0", ">=2.15.0 <3.0.0") is True
    assert is_version_supported("3.1.0", ">=2.15.0 <3.0.0") is False


def _make_promote_project(tmp_path):
    """Sets up a project-local skills dir with an already-approved auto-<repo> lesson,
    and a separate 'global' skills dir (standing in for the real shared library, always
    accessed only via a patched _get_global_skills_dir so tests never touch the real
    product skill library) with a pre-existing target skill."""
    project_skills = tmp_path / "project_skills"
    (project_skills / "auto-myrepo").mkdir(parents=True)
    (project_skills / "auto-myrepo" / "rules.txt").write_text(
        "Cache name arguments must be a String literal.\nUse SLF4J for logging.\n"
    )

    global_skills = tmp_path / "global_skills"
    (global_skills / "qpid").mkdir(parents=True)
    (global_skills / "qpid" / "rules.txt").write_text("Use qpid-broker-core 9.2.1.\n")

    config_file = tmp_path / "kriya.yaml"
    config_file.write_text(f"paths:\n  skills: {project_skills}\n")
    return str(config_file), project_skills, global_skills


def test_skills_promote_single_rule(tmp_path):
    config_file, _project_skills, global_skills = _make_promote_project(tmp_path)
    runner = CliRunner()

    with patch("kriya.cli._get_global_skills_dir", return_value=str(global_skills)):
        result = runner.invoke(
            main,
            ["--config", config_file, "skills", "promote", "auto-myrepo", "qpid",
             "--rule", "Use SLF4J for logging."],
            input="y\n"
        )

    assert result.exit_code == 0, result.output
    target_rules = (global_skills / "qpid" / "rules.txt").read_text()
    assert "Use SLF4J for logging." in target_rules
    # The unrelated rule must NOT have been promoted.
    assert "Cache name arguments" not in target_rules

def test_skills_promote_declined_confirmation_does_not_write(tmp_path):
    config_file, _project_skills, global_skills = _make_promote_project(tmp_path)
    runner = CliRunner()

    with patch("kriya.cli._get_global_skills_dir", return_value=str(global_skills)):
        result = runner.invoke(
            main,
            ["--config", config_file, "skills", "promote", "auto-myrepo", "qpid", "--all"],
            input="n\n"
        )

    assert result.exit_code == 0
    target_rules = (global_skills / "qpid" / "rules.txt").read_text()
    assert "Cache name arguments" not in target_rules
    assert "Use SLF4J for logging." not in target_rules

def test_skills_promote_all_skips_already_present_rules(tmp_path):
    config_file, project_skills, global_skills = _make_promote_project(tmp_path)
    # Pre-seed the target with one of the two source rules already present.
    (global_skills / "qpid" / "rules.txt").write_text(
        "Use qpid-broker-core 9.2.1.\nUse SLF4J for logging.\n"
    )
    runner = CliRunner()

    with patch("kriya.cli._get_global_skills_dir", return_value=str(global_skills)):
        result = runner.invoke(
            main,
            ["--config", config_file, "skills", "promote", "auto-myrepo", "qpid", "--all"],
            input="y\n"
        )

    assert result.exit_code == 0, result.output
    target_rules = (global_skills / "qpid" / "rules.txt").read_text()
    assert target_rules.count("Use SLF4J for logging.") == 1
    assert "Cache name arguments must be a String literal." in target_rules

def test_skills_promote_unknown_target_skill_fails(tmp_path):
    config_file, _project_skills, global_skills = _make_promote_project(tmp_path)
    runner = CliRunner()

    with patch("kriya.cli._get_global_skills_dir", return_value=str(global_skills)):
        result = runner.invoke(
            main,
            ["--config", config_file, "skills", "promote", "auto-myrepo", "no-such-skill", "--all"],
        )

    assert result.exit_code != 0
    assert "does not exist" in result.output

def test_skills_promote_requires_rule_or_all(tmp_path):
    config_file, _project_skills, global_skills = _make_promote_project(tmp_path)
    runner = CliRunner()

    with patch("kriya.cli._get_global_skills_dir", return_value=str(global_skills)):
        result = runner.invoke(
            main,
            ["--config", config_file, "skills", "promote", "auto-myrepo", "qpid"],
        )

    assert result.exit_code != 0

def test_skills_promote_marks_target_skill_verified(tmp_path):
    config_file, _project_skills, global_skills = _make_promote_project(tmp_path)
    # _make_promote_project's target skill has no skill.yaml - give it one so
    # verification-marking has something real to update.
    (global_skills / "qpid" / "skill.yaml").write_text("name: qpid\ndescription: Test\n")
    runner = CliRunner()

    with patch("kriya.cli._get_global_skills_dir", return_value=str(global_skills)):
        result = runner.invoke(
            main,
            ["--config", config_file, "skills", "promote", "auto-myrepo", "qpid",
             "--rule", "Use SLF4J for logging."],
            input="y\n"
        )

    assert result.exit_code == 0, result.output
    se = SkillEngine(str(global_skills), load_global=False)
    se.discover_and_load()
    assert se.get_skill("qpid").verified is True

