from unittest.mock import AsyncMock, patch

from click.testing import CliRunner

from kriya.cli import main
from kriya.config import AppConfig


def test_learn_url_ignores_egress_policy_by_design(tmp_path):
    """Deliberate design decision, confirmed with the user (2026-08-02) rather than
    assumed: unlike LLMClient.complete (which strictly enforces autonomy.egress_policy
    == "local_only" via is_local_url/EgressViolationError), `learn -u` is NOT gated by
    egress_policy at all - even though local_only is Kriya's own default. A user
    typing a specific external URL directly into `learn -u` is itself the explicit,
    deliberate authorization; egress_policy exists to guard against automatic,
    potentially-surprising network calls a pipeline makes on its own, not an
    explicit one-shot command the user just typed. This test locks in that decision
    so a future "fix" doesn't silently turn it into a regression - `learn -u` must
    keep working against a genuinely external URL even under the strictest
    (default) egress_policy."""
    cfg = AppConfig()
    cfg.paths.memory = str(tmp_path / "memory")
    assert cfg.autonomy.egress_policy == "local_only"  # the actual default, not assumed

    mock_fetch = AsyncMock(return_value="Some external documentation content.")

    async def mock_get_embeddings(texts):
        return [[0.1] * 384 for _ in texts]

    mock_embeddings = AsyncMock(side_effect=mock_get_embeddings)
    runner = CliRunner()

    with patch("kriya.tools.web.fetch_url_text", new=mock_fetch), \
         patch("kriya.memory.vector.OllamaEmbeddingClient.get_embeddings", new=mock_embeddings), \
         patch("kriya.cli.load_config", return_value=cfg):
        res = runner.invoke(main, ["learn", "-u", "https://genuinely-external-site.example.com/docs"])

    assert res.exit_code == 0, f"Exception: {res.exception}, Output: {res.output}"
    assert "Successfully indexed" in res.output
    mock_fetch.assert_called_once_with("https://genuinely-external-site.example.com/docs")
