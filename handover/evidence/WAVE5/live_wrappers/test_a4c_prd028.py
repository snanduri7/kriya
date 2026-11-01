"""PRD-028 signed live test, stock body UNCHANGED. Attempt 1 (evidence/a4b_live, preserved) was refused before any model call
(membership_authority.unavailable, model_calls 0): the stock test writes ledger.py into a plain non-git directory and today's
repository-membership authority refuses a non-empty non-git workspace. This wrapper does exactly what that refusal prescribes -
git init + one baseline commit of the fixture - immediately before the engine run starts."""
import importlib.util, subprocess
from pathlib import Path
import pytest
from _common import qualified_llm, identity_check, _per_test_state  # noqa: F401
STOCK = Path("/Users/sriramnanduri/WorkingDirectory/AI/ClaudeCode/Kriya-rebuild-v2/tests/test_live_prd025_029_batch6.py")
spec = importlib.util.spec_from_file_location("stock_live_batch6c", STOCK)
stock = importlib.util.module_from_spec(spec); spec.loader.exec_module(stock)
test_live_prd028_member_authority_is_revision_bound_and_in_scope = stock.test_live_prd028_member_authority_is_revision_bound_and_in_scope

@pytest.fixture(autouse=True)
def _workspace_is_a_git_baseline(monkeypatch):
    from kriya.workflow.workflow import WorkflowEngine
    original = WorkflowEngine.run_generation_workflow
    async def with_baseline(self, *args, **kwargs):
        root = kwargs.get("workspace_path")
        assert root, "workspace_path not passed by keyword"
        if not Path(root, ".git").is_dir():
            for cmd in (["init", "-q"], ["add", "-A"], ["-c", "user.name=fixture", "-c", "user.email=fixture@example.invalid", "commit", "-q", "-m", "baseline fixture"]):
                subprocess.run(["git", "-C", root, *cmd], check=True, capture_output=True)
        return await original(self, *args, **kwargs)
    monkeypatch.setattr(WorkflowEngine, "run_generation_workflow", with_baseline)

@pytest.fixture
def cfg(tmp_path):
    from kriya.config.config import load_config
    from kriya.core.model_runtime import clear_model_runtime_cache
    operator = tmp_path / "operator.yaml"; operator.write_text("{}\n", encoding="utf-8")
    config = load_config(str(operator)); config.llm = qualified_llm(); config.llm_chain = []
    clear_model_runtime_cache()
    identity_check(config, stock, "preflight_identity_batch6c.json")
    return config
