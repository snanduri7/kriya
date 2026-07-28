import pytest
from unittest.mock import AsyncMock, MagicMock
from kriya.config import AppConfig
from kriya.core.llm import LLMClient
from kriya.agents.agent import PlannerAgent, ArchitectAgent, DeveloperAgent, ReviewerAgent

@pytest.mark.asyncio
async def test_base_agent_complete():
    cfg = AppConfig()
    llm = LLMClient(cfg)
    
    # Mock complete method
    llm.complete = AsyncMock(return_value="Mock response text")
    
    planner = PlannerAgent("planner", llm)
    res = await planner.run("Plan a math library")
    
    assert res == "Mock response text"
    llm.complete.assert_called_once_with(planner.system_prompt, "Plan a math library", stream_callback=None)

@pytest.mark.asyncio
async def test_developer_agent_json_parsing():
    cfg = AppConfig()
    llm = LLMClient(cfg)
    
    # Mock code generation response
    json_response = """
    [
      {
        "filepath": "math_lib.py",
        "content": "def add(a, b): return a + b"
      }
    ]
    """
    llm.complete = AsyncMock(return_value=json_response)
    
    dev = DeveloperAgent("developer", llm)
    files = await dev.run_generation("Goal: Add math lib", "Design specs", "Existing code")
    
    assert len(files) == 1
    assert files[0]["filepath"] == "math_lib.py"
    assert files[0]["content"] == "def add(a, b): return a + b"

@pytest.mark.asyncio
async def test_developer_agent_nested_json_parsing():
    cfg = AppConfig()
    llm = LLMClient(cfg)
    
    # Mock code generation response wrapped in a dictionary object (common in json_mode)
    json_response = """
    {
      "files": [
        {
          "filepath": "math_lib.py",
          "content": "def add(a, b): return a + b"
        }
      ]
    }
    """
    llm.complete = AsyncMock(return_value=json_response)
    
    dev = DeveloperAgent("developer", llm)
    files = await dev.run_generation("Goal: Add math lib", "Design specs", "Existing code")
    
    assert len(files) == 1
    assert files[0]["filepath"] == "math_lib.py"
    assert files[0]["content"] == "def add(a, b): return a + b"
