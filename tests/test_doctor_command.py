import urllib.error
from unittest.mock import MagicMock, patch

from click.testing import CliRunner

from kriya.cli import main
from kriya.config import AppConfig


def _cfg(tmp_path):
    cfg = AppConfig()
    cfg.paths.memory = str(tmp_path / "memory")
    cfg.paths.logs = str(tmp_path / "logs")
    cfg.paths.skills = str(tmp_path / "skills")
    cfg.plugins.directory = str(tmp_path / "plugins")
    return cfg


def test_doctor_tests_llm_connectivity_regardless_of_provider_string(tmp_path):
    """Regression test for a real bug found via code review: doctor() only ran the
    LLM connectivity check when llm.provider == "openai" literally, but
    kriya.core.llm.LLMClient never reads that field at all - it always talks to
    base_url via the OpenAI-compatible protocol. user_guide.md documents provider
    as "OpenAI-compatible client; works against Ollama, LM Studio, etc." - so a
    user who reasonably sets provider to their actual backend's name (e.g.
    "ollama") got the platform's core health check silently skipped with zero
    indication it never ran. Verified live against a real local Ollama server
    before fixing: the [SUCCESS]/[ERROR] line for the LLM section was entirely
    absent with provider="ollama", even though the embedding check (which never
    gated on provider) succeeded against the same real server."""
    cfg = _cfg(tmp_path)
    cfg.llm.provider = "ollama"

    mock_response = MagicMock()
    mock_response.getcode.return_value = 200
    mock_response.read.return_value = b'{"data": [{"id": "qwen3-coder:30b"}]}'
    mock_response.__enter__.return_value = mock_response
    mock_response.__exit__.return_value = False

    runner = CliRunner()
    with patch("kriya.cli.load_config", return_value=cfg), \
         patch("urllib.request.urlopen", return_value=mock_response), \
         patch("kriya.memory.vector.OllamaEmbeddingClient.get_embedding", return_value=[0.1] * 384):
        res = runner.invoke(main, ["doctor"])

    assert "Checking LLM provider connectivity" in res.output
    assert "Testing connection..." in res.output.split("Checking LLM provider connectivity")[1].split("Checking Embedding")[0]
    assert "[SUCCESS] Connected to local LLM server" in res.output


def test_doctor_exits_nonzero_when_llm_connection_fails(tmp_path):
    """Regression test for a real bug found via code review: doctor() never
    exited non-zero regardless of how many [ERROR]-level checks failed, which
    undermines its own README framing ("Ensure local Ollama models and server
    links are connected") for any scripted/CI use (`kriya doctor && ...`)."""
    cfg = _cfg(tmp_path)

    runner = CliRunner()
    with patch("kriya.cli.load_config", return_value=cfg), \
         patch("urllib.request.urlopen", side_effect=urllib.error.URLError("Connection refused")), \
         patch("kriya.memory.vector.OllamaEmbeddingClient.get_embedding", return_value=[0.1] * 384):
        res = runner.invoke(main, ["doctor"])

    assert "[ERROR]" in res.output
    assert res.exit_code != 0


def test_doctor_exits_zero_when_all_checks_pass(tmp_path):
    cfg = _cfg(tmp_path)

    mock_response = MagicMock()
    mock_response.getcode.return_value = 200
    mock_response.read.return_value = b'{"data": [{"id": "qwen3-coder:30b"}]}'
    mock_response.__enter__.return_value = mock_response
    mock_response.__exit__.return_value = False

    runner = CliRunner()
    with patch("kriya.cli.load_config", return_value=cfg), \
         patch("urllib.request.urlopen", return_value=mock_response), \
         patch("kriya.memory.vector.OllamaEmbeddingClient.get_embedding", return_value=[0.1] * 384):
        res = runner.invoke(main, ["doctor"])

    assert res.exit_code == 0


def test_doctor_exits_nonzero_when_embedding_connection_fails(tmp_path):
    cfg = _cfg(tmp_path)

    mock_response = MagicMock()
    mock_response.getcode.return_value = 200
    mock_response.read.return_value = b'{"data": [{"id": "qwen3-coder:30b"}]}'
    mock_response.__enter__.return_value = mock_response
    mock_response.__exit__.return_value = False

    runner = CliRunner()
    with patch("kriya.cli.load_config", return_value=cfg), \
         patch("urllib.request.urlopen", return_value=mock_response), \
         patch("kriya.memory.vector.OllamaEmbeddingClient.get_embedding", side_effect=ConnectionError("refused")):
        res = runner.invoke(main, ["doctor"])

    assert "[ERROR]" in res.output
    assert res.exit_code != 0


def test_doctor_warns_on_java_mvn_version_mismatch_without_failing(tmp_path):
    """Regression test: 'mvn' can silently resolve a different JDK than plain
    'java' (e.g. a Homebrew mvn install defaulting JAVA_HOME to its own,
    possibly newer, openjdk) - confirmed as a real, silent failure mode during
    golden-use-case validation (a JVM startup flag correct for the JDK 'java'
    resolved was fatal under the JDK 'mvn' actually built/ran against). This
    must surface as a [WARNING] (not silently ignored), but must NOT fail
    `doctor`'s own exit code - same severity convention as the existing
    configured-model-not-on-server warning."""
    cfg = _cfg(tmp_path)

    mock_response = MagicMock()
    mock_response.getcode.return_value = 200
    mock_response.read.return_value = b'{"data": [{"id": "qwen3-coder:30b"}]}'
    mock_response.__enter__.return_value = mock_response
    mock_response.__exit__.return_value = False

    runner = CliRunner()
    with patch("kriya.cli.load_config", return_value=cfg), \
         patch("urllib.request.urlopen", return_value=mock_response), \
         patch("kriya.memory.vector.OllamaEmbeddingClient.get_embedding", return_value=[0.1] * 384), \
         patch("kriya.tools.validate.check_java_toolchain", return_value={
             "java_found": True, "java_version": "17",
             "mvn_found": True, "mvn_java_version": "26",
             "mismatch": True,
         }):
        res = runner.invoke(main, ["doctor"])

    assert "[WARNING]" in res.output
    assert "DIFFERENT major" in res.output
    assert res.exit_code == 0


def test_doctor_skips_toolchain_section_cleanly_when_neither_found(tmp_path):
    """Not every Kriya project is Java-based - finding neither 'java' nor 'mvn'
    on PATH must be a silent skip, not a warning or error."""
    cfg = _cfg(tmp_path)

    mock_response = MagicMock()
    mock_response.getcode.return_value = 200
    mock_response.read.return_value = b'{"data": [{"id": "qwen3-coder:30b"}]}'
    mock_response.__enter__.return_value = mock_response
    mock_response.__exit__.return_value = False

    runner = CliRunner()
    with patch("kriya.cli.load_config", return_value=cfg), \
         patch("urllib.request.urlopen", return_value=mock_response), \
         patch("kriya.memory.vector.OllamaEmbeddingClient.get_embedding", return_value=[0.1] * 384), \
         patch("kriya.tools.validate.check_java_toolchain", return_value={
             "java_found": False, "java_version": None,
             "mvn_found": False, "mvn_java_version": None,
             "mismatch": False,
         }):
        res = runner.invoke(main, ["doctor"])

    assert "Checking Java/Maven toolchain" not in res.output
    assert res.exit_code == 0
