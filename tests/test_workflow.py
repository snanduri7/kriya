import os
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from kriya.config import AppConfig
from kriya.core.kernel import Kernel
from kriya.core.llm import LLMClient
from kriya.workflow.workflow import WorkflowEngine

@pytest.mark.asyncio
async def test_workflow_successful_run(tmp_path):
    cfg = AppConfig()
    kernel = Kernel(config=cfg)
    llm = LLMClient(cfg)
    
    llm.complete = AsyncMock(side_effect=[
        "Step 1: Write code",
        "Design: Write math.py",
        '[{"filepath": "math.py", "content": "def add(a,b):\\n    return a+b"}]',
        "Review: Approved"
    ])

    we = WorkflowEngine(kernel, llm)
    
    res = await we.run_generation_workflow(
        goal="Create math library",
        workspace_path=str(tmp_path)
    )
    
    assert res["quality_gates_passed"] is True
    assert "math.py" in res["files"]
    assert os.path.exists(os.path.join(tmp_path, "math.py"))
    assert res["review"] == "Review: Approved"

@pytest.mark.asyncio
async def test_workflow_syntax_error_auto_debugging_loop(tmp_path):
    cfg = AppConfig()
    kernel = Kernel(config=cfg)
    llm = LLMClient(cfg)
    
    llm.complete = AsyncMock(side_effect=[
        "Step 1: Write code",
        "Design: Write math.py",
        '[{"filepath": "math.py", "content": "def add(a,b)\\n    return a+b"}]',
        '[{"filepath": "math.py", "content": "def add(a,b):\\n    return a+b"}]',
        "Review: Approved"
    ])

    we = WorkflowEngine(kernel, llm)
    
    res = await we.run_generation_workflow(
        goal="Create math library with auto-debugging",
        workspace_path=str(tmp_path)
    )
    
    assert res["quality_gates_passed"] is True
    assert "math.py" in res["files"]
    
    # Check that file was rewritten with correct code
    with open(os.path.join(tmp_path, "math.py"), "r") as f:
        content = f.read()
    assert "def add(a,b):" in content


@pytest.mark.asyncio
async def test_workflow_fallback_chain(tmp_path):
    from kriya.config import FallbackModelConfig
    cfg = AppConfig()
    cfg.llm_chain = [
        FallbackModelConfig(model="fallback-1"),
        FallbackModelConfig(model="fallback-2")
    ]
    kernel = Kernel(config=cfg)
    llm = LLMClient(cfg)
    
    model_overrides = []
    
    async def mock_complete(*args, **kwargs):
        model_overrides.append(kwargs.get("model_override"))
        if len(model_overrides) == 1:
            return "Step 1: Write code"
        elif len(model_overrides) == 2:
            return "Design: Write math.py"
        elif len(model_overrides) == 3:
            return '[{"filepath": "math.py", "content": "def add(a,b)\\n    return a+b"}]'
        elif len(model_overrides) == 4:
            return '[{"filepath": "math.py", "content": "def add(a,b):\\n    return a+b"}]'
        elif len(model_overrides) == 5:
            return "Avoid missing colon in function definition."
        else:
            return "Review: Approved"
            
    llm.complete = mock_complete
    
    we = WorkflowEngine(kernel, llm)
    cfg.paths.skills = str(tmp_path / "skills")
    
    res = await we.run_generation_workflow(
        goal="Create math library with fallback chain",
        workspace_path=str(tmp_path)
    )
    
    assert res["quality_gates_passed"] is True
    assert "fallback-1" in model_overrides
    
    repo_slug = os.path.basename(tmp_path).lower().strip(".")
    if not repo_slug:
         repo_slug = "root"
    rules_file = os.path.join(tmp_path, "skills", f"auto-{repo_slug}", "staged_rules.txt")
    assert os.path.exists(rules_file)
    with open(rules_file, "r", encoding="utf-8") as f:
        rules_content = f.read()
    assert "Avoid missing colon in function definition." in rules_content
