"""OI-120: the analyzer's conventions bootstrap writes a skill ONLY through the Skills owner (``SkillEngine.create_skill``, the canonical atomic
writer), never with a plain ``open()``; the owner's shared-install-directory refusal applies, and an existing skill is never overwritten.

Measured defect (2026-11-06 reconciliation): kriya/analyzer/analyzer.py wrote skill.yaml / instructions.md / rules.txt of extractor-LLM text with plain
``open(..., "w")`` into the skills directory (a second, un-owned Skills writer; with paths.skills unset the shared install library after a warning only).
"""
import ast
import os
from pathlib import Path
from unittest.mock import patch

import pytest

from kriya.analyzer.analyzer import RepositoryAnalyzer
from kriya.config import AppConfig
from kriya.repository import atomic_write
from kriya.skills import skill as skill_module
from kriya.skills.skill import SkillEngine, SkillWriteRefused

EXTRACTED = '{"description":"Test Conventions","instructions":"# Code Styles","rules":["Rule X: Use synch"]}'


def _app(tmp_path):
    src = tmp_path / "myapp"
    src.mkdir()
    (src / "index.py").write_text("class MyService: pass\n")
    cfg = AppConfig()
    cfg.paths.memory = str(tmp_path / "memory")
    cfg.paths.skills = str(tmp_path / "skills")
    cfg.autonomy.run_verification_enabled = False
    return src, cfg


async def _index(analyzer, cfg):
    from _fake_embedding import native_embed_response

    async def complete(self, system_prompt, user_prompt, stream_callback=None, **kwargs):
        return EXTRACTED

    with patch("httpx.AsyncClient.post") as post, patch("kriya.core.llm.LLMClient.complete", new=complete):
        post.side_effect = native_embed_response(384)
        await analyzer.index_repository(cfg)


@pytest.mark.usefixtures("bound_workspace")
@pytest.mark.asyncio
async def test_the_bootstrap_hands_the_skill_to_the_skills_owner_and_every_file_goes_through_the_canonical_writer(tmp_path):
    src, cfg = _app(tmp_path)
    written = []
    real = atomic_write.atomic_write_file

    def spy(full_path, *args, **kwargs):
        written.append(os.path.basename(full_path))
        return real(full_path, *args, **kwargs)

    with patch.object(skill_module, "atomic_write_file", spy):
        await _index(RepositoryAnalyzer(str(src)), cfg)
    assert sorted(written) == ["instructions.md", "rules.txt", "skill.yaml"], "the three skill files must go through the canonical writer"
    skill_dir = Path(cfg.paths.skills) / "auto-myapp"
    assert (skill_dir / "rules.txt").read_text() == "Rule X: Use synch" and (skill_dir / "examples").is_dir()
    assert "Test Conventions" in (skill_dir / "skill.yaml").read_text()


def test_the_analyzer_module_contains_no_plain_write_open():
    """Structural tripwire: no ``open(<path>, "w"...)`` in kriya/analyzer/analyzer.py (reading sample files with "r" is fine)."""
    tree = ast.parse(Path(skill_module.__file__).parents[1].joinpath("analyzer", "analyzer.py").read_text(encoding="utf-8"))
    offenders = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and getattr(node.func, "id", "") == "open":
            modes = [a for a in node.args[1:2]] + [k.value for k in node.keywords if k.arg == "mode"]
            if any(isinstance(m, ast.Constant) and isinstance(m.value, str) and set(m.value) & set("wax+") for m in modes):
                offenders.append(node.lineno)
    assert offenders == [], f"plain write open() in the analyzer at lines {offenders}"


@pytest.mark.usefixtures("bound_workspace")
@pytest.mark.asyncio
async def test_an_existing_skill_is_never_overwritten(tmp_path):
    src, cfg = _app(tmp_path)
    existing = Path(cfg.paths.skills) / "auto-myapp"
    existing.mkdir(parents=True)
    (existing / "rules.txt").write_text("HAND-WRITTEN RULE")
    await _index(RepositoryAnalyzer(str(src)), cfg)
    assert (existing / "rules.txt").read_text() == "HAND-WRITTEN RULE" and not (existing / "skill.yaml").exists()


def test_the_owner_refuses_a_new_skill_in_the_shared_install_directory_for_an_unrelated_workspace(tmp_path, monkeypatch):
    shared = tmp_path / "kriya-install" / "skills"
    shared.mkdir(parents=True)
    monkeypatch.setattr(skill_module, "get_global_skills_dir", lambda: str(shared))
    manager = SkillEngine(str(shared), load_global=False, load_cwd=False, workspace_path=str(tmp_path / "unrelated-project"))
    assert "SHARED install skills directory" in (manager.new_skill_refusal("auto-x") or "")
    with pytest.raises(SkillWriteRefused):
        manager.create_skill("auto-x", {"name": "auto-x"}, "i", "r", commit_message="m")
    assert list(shared.iterdir()) == [], "nothing may be created in the shared directory"
    project_local = SkillEngine(str(tmp_path / "project" / "skills"), load_global=False, load_cwd=False, workspace_path=str(tmp_path / "project"))
    assert project_local.new_skill_refusal("auto-x") is None


@pytest.mark.usefixtures("bound_workspace")
@pytest.mark.asyncio
async def test_a_refused_bootstrap_spends_no_model_call(tmp_path, monkeypatch):
    src, cfg = _app(tmp_path)
    calls = []

    async def complete(self, *args, **kwargs):
        calls.append(1)
        return EXTRACTED

    monkeypatch.setattr(SkillEngine, "new_skill_refusal", lambda self, name: "refused for the test")
    from _fake_embedding import native_embed_response

    with patch("httpx.AsyncClient.post") as post, patch("kriya.core.llm.LLMClient.complete", new=complete):
        post.side_effect = native_embed_response(384)
        await RepositoryAnalyzer(str(src)).index_repository(cfg)
    assert calls == [] and not (Path(cfg.paths.skills) / "auto-myapp").exists()


def test_mutation_a_plain_open_writer_in_the_analyzer_would_be_caught(tmp_path):
    source = 'import yaml\nwith open("skill.yaml", "w", encoding="utf-8") as f:\n    f.write("x")\n'
    tree = ast.parse(source)
    flagged = [n for n in ast.walk(tree) if isinstance(n, ast.Call) and getattr(n.func, "id", "") == "open"
               and any(isinstance(a, ast.Constant) and isinstance(a.value, str) and set(a.value) & set("wax+") for a in n.args[1:2])]
    assert flagged, "the tripwire would not see a plain write open()"
