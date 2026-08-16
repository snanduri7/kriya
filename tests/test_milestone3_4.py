import json
import os
import sqlite3
from unittest.mock import AsyncMock, patch

import pytest

from kriya.config import AppConfig
from kriya.core.kernel import Kernel
from kriya.core.llm import LLMClient
from kriya.core.trace import TraceLogger
from kriya.workflow.workflow import WorkflowEngine


def test_persistent_trace_logger(tmp_path):
    trace_db = tmp_path / "traces.db"
    logger = TraceLogger(str(trace_db))
    
    logger.log_run(
        run_id="run123",
        goal="Test goal repair",
        duration_sec=12.5,
        attempts=2,
        status="success",
        files_modified=["FileA.java", "FileB.java"]
    )
    
    # Check insertion
    conn = sqlite3.connect(str(trace_db))
    cursor = conn.cursor()
    cursor.execute("SELECT run_id, goal, duration_sec, attempts, status, files_modified FROM runs")
    row = cursor.fetchone()
    conn.close()
    
    assert row is not None
    assert row[0] == "run123"
    assert row[1] == "Test goal repair"
    assert row[2] == 12.5
    assert row[3] == 2
    assert row[4] == "success"
    assert row[5] == "FileA.java,FileB.java"

@pytest.mark.asyncio
async def test_staged_skill_accrual(tmp_path):
    from kriya.config import FallbackModelConfig
    cfg = AppConfig()
    cfg.autonomy.mode = "guardrails"
    cfg.paths.skills = str(tmp_path / "skills")
    cfg.paths.logs = str(tmp_path / "logs")
    cfg.llm_chain = [FallbackModelConfig(model="model-fallback", base_url="http://localhost", api_key="test")]
    cfg.autonomy.run_verification_enabled = False

    kernel = Kernel(config=cfg)
    llm = LLMClient(cfg)
    
    # Mock LLM complete to return a fixed code block and rule
    llm.complete = AsyncMock(side_effect=[
        "Plan description",
        "Design description",
        '[{"filepath": "sample.py", "content": "print(1)"}]',
        '[{"filepath": "sample.py", "content": "print(1)"}]',
        '[{"category": "Rules", "value": "Rule: Always use print with integer constants.", "quote": "SyntaxError: invalid syntax"}]',  # Extracted lesson
        "Approved review"
    ])
    
    we = WorkflowEngine(kernel, llm)
    
    # Force Quality Gates compile/test checks to fail on the first attempt so that retry_count > 0!
    # On the first retry, it should complete and extract lesson rules
    original_validate = "kriya.tools.validate.PolymorphicValidator"
    with patch(original_validate) as mock_validator:
        # First attempt compile fails, second attempt compile succeeds
        inst = mock_validator.return_value
        inst.stack = "python"
        inst.run_compile_check.side_effect = [
            {"success": False, "output": "SyntaxError: invalid syntax"},
            {"success": True, "output": ""}
        ]
        inst.run_tests.return_value = {"success": True, "output": ""}
        
        res = await we.run_generation_workflow(
            goal="Fix test",
            workspace_path=str(tmp_path)
        )
        
    assert res["quality_gates_passed"] is True

    # Verify structured knowledge facts are staged under staged_knowledge.json
    staged_knowledge_path = tmp_path / "skills" / "auto-test_staged_skill_accrual0" / "staged_knowledge.json"
    assert os.path.exists(staged_knowledge_path) is True
    with open(staged_knowledge_path, "r", encoding="utf-8") as f:
        staged_facts = json.load(f)
    assert any("Always use print with" in fact["value"] for fact in staged_facts)
    
    # Verify trace logger wrote SQLite log
    trace_db = tmp_path / "logs" / "traces.db"
    assert os.path.exists(trace_db) is True
