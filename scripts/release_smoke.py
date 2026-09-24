"""Run with the clean venv's Python, from outside the source checkout."""

import json
import os
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from click.testing import CliRunner

import kriya
from kriya.cli import main
from kriya.config import load_config

source_root = Path(__file__).resolve().parents[1]
assert not Path(kriya.__file__).resolve().is_relative_to(source_root), "Source checkout masks installed wheel"

with tempfile.TemporaryDirectory(prefix="kriya-release-smoke-") as work:
    os.chdir(work)
    cfg = load_config()
    assert Path(cfg.plugins.directory, "core_tools", "__init__.py").is_file()
    # The bundled skill library ships in the wheel, at the installed global
    # skills location, and loads through the real SkillEngine.
    from kriya.skills.skill import SkillEngine, get_global_skills_dir
    assert Path(cfg.paths.skills).resolve() == Path(get_global_skills_dir()).resolve()
    engine = SkillEngine(cfg.paths.skills, load_global=True, load_cwd=False, workspace_path=work)
    engine.discover_and_load()
    bundled = sorted(skill.name for skill in engine.list_skills())
    print(f"BUNDLED SKILLS: {bundled}")
    assert bundled == ["activemq-artemis", "binary-wire-protocol", "ignite-java17", "qpid"], bundled
    assert Path(cfg.paths.skills, "qpid", "examples", "pom.xml").is_file()
    cfg.paths.memory = str(Path(work, "memory"))
    cfg.paths.logs = str(Path(work, "logs"))
    cfg.logging.file = None
    cfg.mcp = {}
    response = MagicMock()
    response.__enter__.return_value = response
    response.getcode.return_value = 200
    response.read.return_value = json.dumps({"data": [{"id": cfg.llm.model}]}).encode()
    runner = CliRunner()
    for command in ("version", "config", "plugins", "doctor"):
        with patch("kriya.cli.load_config", return_value=cfg), \
             patch("socket.socket.connect", side_effect=AssertionError("Unexpected network access")), \
             patch("urllib.request.urlopen", return_value=response), \
             patch("kriya.memory.vector.OllamaEmbeddingClient.get_embedding", new=AsyncMock(return_value=[0.1])):
            result = runner.invoke(main, [command])
        print(f"COMMAND {command}: exit={result.exit_code}\n{result.output}")
        assert result.exit_code == 0, repr(result.exception)
        if command == "config":
            assert json.loads(result.output)["execution_policy"]["mode"] == "audit"
        if command == "plugins":
            assert "core_tools" in result.output and "INITIALIZED" in result.output
            assert "FAILED" not in result.output
print("Release CLI smoke: PASS (doctor endpoints mocked; no live qualification claimed)")
