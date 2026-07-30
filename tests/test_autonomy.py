import os
from unittest.mock import AsyncMock

import pytest

from kriya.config import AppConfig
from kriya.core.kernel import Kernel
from kriya.core.llm import LLMClient
from kriya.workflow.workflow import WorkflowEngine


@pytest.mark.asyncio
async def test_autonomy_sensitive_paths_escalation(tmp_path):
    cfg = AppConfig()
    # Configure human-in-the-loop and register custom sensitive path rules
    cfg.autonomy.mode = "guardrails"
    cfg.autonomy.sensitive_paths = [r".*\.env$", r".*secrets.*"]
    
    kernel = Kernel(config=cfg)
    llm = LLMClient(cfg)
    
    llm.complete = AsyncMock(side_effect=[
        "Step 1: Write sensitive file",
        "Design: write database.env",
        '[{"filepath": "database.env", "content": "DB_PASSWORD=admin"}]',
        "Review: Approved"
    ])

    we = WorkflowEngine(kernel, llm)
    
    # Callback verifying that it was triggered with the correct reason
    approval_called = False
    async def mock_approval_callback(files, reason):
        nonlocal approval_called
        approval_called = True
        assert "Sensitive path matched" in reason
        return False # Deny code write

    res = await we.run_generation_workflow(
        goal="Add database env settings",
        workspace_path=str(tmp_path),
        approval_callback=mock_approval_callback
    )
    
    # Assert human callback was triggered and execution was blocked/aborted
    assert approval_called is True
    assert res["quality_gates_passed"] is False
    assert not os.path.exists(os.path.join(tmp_path, "database.env"))

@pytest.mark.asyncio
async def test_autonomy_risk_threshold_escalation(tmp_path):
    cfg = AppConfig()
    cfg.autonomy.mode = "guardrails"
    cfg.autonomy.risk_threshold_lines = 10 # low threshold
    
    kernel = Kernel(config=cfg)
    llm = LLMClient(cfg)
    
    # Create content exceeding 10 lines
    content = "\\n".join([f"x = {i}" for i in range(20)])
    
    llm.complete = AsyncMock(side_effect=[
        "Step 1: Write large code",
        "Design: write large.py",
        f'[{{"filepath": "large.py", "content": "{content}"}}]',
        "Review: Approved"
    ])

    we = WorkflowEngine(kernel, llm)
    
    approval_called = False
    async def mock_approval_callback(files, reason):
        nonlocal approval_called
        approval_called = True
        assert "Risk threshold exceeded" in reason
        return True # Approve code write

    res = await we.run_generation_workflow(
        goal="Add large python file",
        workspace_path=str(tmp_path),
        approval_callback=mock_approval_callback
    )
    
    assert approval_called is True
    assert res["quality_gates_passed"] is True
    assert os.path.exists(os.path.join(tmp_path, "large.py"))
