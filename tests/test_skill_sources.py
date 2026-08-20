from pathlib import Path

from kriya.config import AppConfig
from kriya.skills.skill import SkillEngine


def _write_skill(root: Path, folder: str, name: str) -> None:
    path = root / folder
    path.mkdir(parents=True)
    (path / "skill.yaml").write_text(
        f"name: {name}\ndescription: test\nverified: true\n", encoding="utf-8",
    )
    (path / "rules.txt").write_text("Use the tested API.\n", encoding="utf-8")


def test_plain_kriya_can_disable_implicit_skill_sources(tmp_path, monkeypatch):
    explicit = tmp_path / "explicit"
    cwd = tmp_path / "project"
    cwd.mkdir()
    _write_skill(explicit, "only-explicit", "only-explicit")
    _write_skill(cwd / "skills", "implicit-cwd", "implicit-cwd")
    monkeypatch.chdir(cwd)
    cfg = AppConfig()
    cfg.paths.skills = str(explicit)
    cfg.skills.load_global = False
    cfg.skills.load_cwd = False

    engine = SkillEngine.from_config(cfg)
    engine.discover_and_load()

    assert [skill.name for skill in engine.list_skills()] == ["only-explicit"]


def test_active_skill_manifest_is_stable_and_source_grounded(tmp_path):
    _write_skill(tmp_path, "ignite", "ignite")
    engine = SkillEngine(str(tmp_path), load_global=False, load_cwd=False)
    engine.discover_and_load()

    first = engine.manifest_for(["ignite"])
    second = engine.manifest_for(["ignite"])

    assert first == second
    assert first[0]["source_path"] == str(tmp_path / "ignite")
    assert len(first[0]["content_hash"]) == 64
