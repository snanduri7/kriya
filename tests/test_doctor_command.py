import urllib.error
from unittest.mock import MagicMock, patch

from click.testing import CliRunner

from kriya.cli import main
from kriya.config import AppConfig


def _cfg(tmp_path):
    cfg = AppConfig()
    cfg.paths.memory = str(tmp_path / "memory")
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
    """The HOST's 'mvn' can run on a different JDK than the host's plain 'java' (a Homebrew mvn defaults JAVA_HOME to its own, possibly newer,
    openjdk). `doctor` reports that as a [WARNING] without failing its exit code - and, since TOOLCHAIN-HOST-PROBE-AUTHORITY-001 (owner ruling
    2026-11-12, Scenario-A run 7: host java 21, host mvn 26, every gate in the JDK 17 image), it says plainly that these are HOST tools which a
    contained Kriya run does not use. The old advice ("Pin JAVA_HOME ... before running generate/fix") claimed the opposite and is gone."""
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
             "java_found": True, "java_version": "21",
             "mvn_found": True, "mvn_java_version": "26",
             "mismatch": True,
         }):
        res = runner.invoke(main, ["doctor"])

    out = " ".join(res.output.split())
    assert "Checking HOST Java/Maven tools (host diagnostic; contained Kriya runs do not use them):" in out
    assert "HOST 'java' resolves to JDK 21" in out and "HOST 'mvn' builds/runs against JDK 26" in out
    assert "[WARNING] HOST 'java' (JDK 21) and HOST 'mvn' (JDK 26) resolve to DIFFERENT major versions" in out
    assert "It does NOT change a Kriya run" in out
    assert "Pin JAVA_HOME" not in out
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

    assert "Checking HOST Java/Maven tools" not in res.output and "HOST" not in res.output
    assert res.exit_code == 0


def test_doctor_reports_jdtls_found(tmp_path):
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
         patch("kriya.tools.lsp.find_jdtls", return_value="/opt/homebrew/bin/jdtls"):
        res = runner.invoke(main, ["doctor"])

    assert "[FOUND] jdtls at /opt/homebrew/bin/jdtls" in res.output
    assert "JDK 21+ to RUN" in res.output
    assert res.exit_code == 0


def test_doctor_reports_jdtls_not_found_without_failing(tmp_path):
    """jdtls is entirely optional - not finding it must never fail doctor's
    exit code, only note that LSP grounding is unavailable."""
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
         patch("kriya.tools.lsp.find_jdtls", return_value=None):
        res = runner.invoke(main, ["doctor"])

    assert "Not found on PATH" in res.output
    assert "brew install jdtls" in res.output
    assert res.exit_code == 0
