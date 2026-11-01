import importlib.util
from pathlib import Path
import pytest
from _common import qualified_llm, identity_check, _per_test_state  # noqa: F401
STOCK = Path("/Users/sriramnanduri/WorkingDirectory/AI/ClaudeCode/Kriya-rebuild-v2/tests/test_live_prd020_024_batch5.py")
spec = importlib.util.spec_from_file_location("stock_live_batch5", STOCK)
stock = importlib.util.module_from_spec(spec); spec.loader.exec_module(stock)
test_prd020_lineage_survives_model_paraphrase = stock.test_prd020_lineage_survives_model_paraphrase
test_prd021_planner_sees_the_grounded_owner = stock.test_prd021_planner_sees_the_grounded_owner
test_prd022_duplicate_owner_first_and_second_attempt = stock.test_prd022_duplicate_owner_first_and_second_attempt
test_prd024_pre_existing_failure_is_not_a_new_regression = stock.test_prd024_pre_existing_failure_is_not_a_new_regression
test_prd024_auto_does_not_baseline_a_docstring_edit_for_its_test_reference_alone = stock.test_prd024_auto_does_not_baseline_a_docstring_edit_for_its_test_reference_alone


@pytest.fixture
def cfg(tmp_path, monkeypatch):
    """The stock batch-5 fixture, with ONLY llm replaced by the qualified binding (the stock 16K window/max_tokens overrides
    belong to the unqualified stock identity; the qualified identity is 32768)."""
    from kriya.config.config import load_config
    from kriya.core.model_runtime import clear_model_runtime_cache
    operator = tmp_path / "operator.yaml"; operator.write_text("{}\n", encoding="utf-8")
    config = load_config(str(operator))
    config.llm = qualified_llm()
    config.llm_chain = []
    config.autonomy.mode = "guardrails"
    config.autonomy.run_verification_enabled = False
    config.autonomy.web_lookup_enabled = False
    config.autonomy.spec_compliance_enabled = True
    config.engineering_triage.enabled = True
    config.skills.load_global = False
    config.skills.load_cwd = False
    config.paths.skills = str(tmp_path / "skills")
    config.paths.memory = str(tmp_path / "memory")
    clear_model_runtime_cache()
    identity_check(config, stock, "preflight_identity_batch5.json")
    return config
