import importlib.util
from pathlib import Path
import pytest
from _common import qualified_llm, identity_check, _per_test_state  # noqa: F401
STOCK = Path("/Users/sriramnanduri/WorkingDirectory/AI/ClaudeCode/Kriya-rebuild-v2/tests/test_live_prd025_029_batch6.py")
spec = importlib.util.spec_from_file_location("stock_live_batch6b", STOCK)
stock = importlib.util.module_from_spec(spec); spec.loader.exec_module(stock)
test_live_prd026_ineffective_repair_terminates = stock.test_live_prd026_ineffective_repair_terminates
test_live_prd028_member_authority_is_revision_bound_and_in_scope = stock.test_live_prd028_member_authority_is_revision_bound_and_in_scope

@pytest.fixture
def cfg(tmp_path):
    from kriya.config.config import load_config
    from kriya.core.model_runtime import clear_model_runtime_cache
    operator = tmp_path / "operator.yaml"; operator.write_text("{}\n", encoding="utf-8")
    config = load_config(str(operator))
    config.llm = qualified_llm()
    config.llm_chain = []
    clear_model_runtime_cache()
    identity_check(config, stock, "preflight_identity_batch6b.json")
    return config
