import importlib.util
from pathlib import Path
import pytest
from _common import qualified_llm, identity_check, _per_test_state  # noqa: F401
STOCK = Path("/Users/sriramnanduri/WorkingDirectory/AI/ClaudeCode/Kriya-rebuild-v2/tests/test_live_prd025_029_batch6.py")
spec = importlib.util.spec_from_file_location("stock_live_batch6", STOCK)
stock = importlib.util.module_from_spec(spec); spec.loader.exec_module(stock)
test_live_prd025_bounded_package_keeps_middle_failure = stock.test_live_prd025_bounded_package_keeps_middle_failure
test_live_prd025_capture_loss_can_never_pass = stock.test_live_prd025_capture_loss_can_never_pass
test_live_prd029_authorized_api_change_is_bound_to_its_commit = stock.test_live_prd029_authorized_api_change_is_bound_to_its_commit
test_live_prd029_targeted_required_contract_change_is_committed_and_bound = stock.test_live_prd029_targeted_required_contract_change_is_committed_and_bound


@pytest.fixture
def cfg(tmp_path):
    from kriya.config.config import load_config
    from kriya.core.model_runtime import clear_model_runtime_cache
    operator = tmp_path / "operator.yaml"; operator.write_text("{}\n", encoding="utf-8")
    config = load_config(str(operator))                       # identical to the stock fixture
    config.llm = qualified_llm()                              # the ONLY difference
    config.llm_chain = []
    clear_model_runtime_cache()
    identity_check(config, stock, "preflight_identity_batch6.json")
    return config
