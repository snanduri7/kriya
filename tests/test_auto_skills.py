import os
from unittest.mock import MagicMock, patch

import pytest

from kriya.analyzer.analyzer import RepositoryAnalyzer
from kriya.config import AppConfig
from kriya.core.kernel import Kernel
from kriya.core.llm import LLMClient
from kriya.workflow.workflow import WorkflowEngine


@pytest.mark.asyncio
async def test_auto_skills_generation_and_injection(tmp_path):
    # Setup codebase folder
    src_dir = tmp_path / "myapp"
    src_dir.mkdir()
    (src_dir / "index.py").write_text("class MyService: pass\n")
    
    cfg = AppConfig()
    cfg.paths.memory = str(tmp_path / "memory")
    cfg.paths.skills = str(tmp_path / "skills")
    cfg.autonomy.run_verification_enabled = False
    
    analyzer = RepositoryAnalyzer(str(src_dir))
    
    # 1. Mock LLMClient.complete at class level
    mock_responses = [
        # Call 1: extract_conventions
        '{"description":"Test Conventions","instructions":"# Code Styles","rules":["Rule X: Use synch"]}',
        # Call 2: Plan
        "Plan: write code",
        # Call 3: Design
        "Design: class struct",
        # Call 4: Developer code write
        '[{"filepath": "out.py", "content": "x = 1"}]',
        # Call 5: Review
        "Review: Approved"
    ]
    
    captured_prompts = []
    
    async def mock_complete(self, system_prompt, user_prompt, stream_callback=None, **kwargs):
        captured_prompts.append(user_prompt)
        return mock_responses.pop(0)

    # 2. Patch both embedding HTTP request and LLMClient.complete
    with patch("httpx.AsyncClient.post") as mock_post, \
         patch("kriya.core.llm.LLMClient.complete", new=mock_complete):
         
        # Mock embedding post responses
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "data": [
                {"embedding": [0.1] * 384}
            ]
        }
        mock_post.return_value = mock_response
        
        # Run indexing (which triggers extract conventions and saves skill files)
        await analyzer.index_repository(cfg)
        
        # Verify skill files were written to disk
        auto_skill_dir = os.path.join(cfg.paths.skills, "auto-myapp")
        assert os.path.exists(os.path.join(auto_skill_dir, "skill.yaml"))
        assert os.path.exists(os.path.join(auto_skill_dir, "instructions.md"))
        assert os.path.exists(os.path.join(auto_skill_dir, "rules.txt"))
        
        # Verify rules.txt contents
        with open(os.path.join(auto_skill_dir, "rules.txt"), "r") as f:
            rules_content = f.read()
        assert "Rule X: Use synch" in rules_content
        
        # Run workflow engine and check prompt injection
        kernel = Kernel(config=cfg)
        llm = LLMClient(cfg)
        we = WorkflowEngine(kernel, llm)
        
        await we.run_generation_workflow(
            goal="Add a new service",
            workspace_path=str(src_dir)
        )
        
        # Assert that the custom rule was loaded and injected into Planner prompt
        # Call 0 in mock_complete was index_repository, Call 1 is Planner prompt
        assert len(captured_prompts) > 1
        assert "Rule X: Use synch" in captured_prompts[1]
        assert "Code Styles" in captured_prompts[1]
