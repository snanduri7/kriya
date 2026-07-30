import os
from unittest.mock import AsyncMock, MagicMock

import pytest

from kriya.agents.agent import ReviewerAgent


@pytest.mark.asyncio
async def test_folder_review_prompt_assembly(tmp_path):
    # Setup test files
    f1 = tmp_path / "hello.py"
    f1.write_text("print('hello')\n")
    f2 = tmp_path / "Service.java"
    f2.write_text("class Service {}\n")
    
    # Mock ReviewerAgent run
    llm = MagicMock()
    llm.complete = AsyncMock(return_value="Mock review output")
    reviewer = ReviewerAgent("reviewer", llm)
    
    # Replicate CLI scan logic
    files_to_review = []
    ignore_dirs = {".git", ".venv", "venv", "node_modules", "__pycache__"}
    for root, dirs, files in os.walk(str(tmp_path)):
        dirs[:] = [d for d in dirs if d not in ignore_dirs and not d.startswith(".")]
        for file in files:
            _, ext = os.path.splitext(file)
            if ext.lower() in {".py", ".java", ".xml"}:
                full = os.path.join(root, file)
                rel = os.path.relpath(full, str(tmp_path))
                files_to_review.append((rel, full))
                
    assert len(files_to_review) == 2
    
    review_prompt = ""
    for rel, full in files_to_review:
        with open(full, "r", encoding="utf-8") as f:
            content = f.read()
        review_prompt += f"\n=== File: {rel} ===\n{content}\n"
        
    assert "=== File: hello.py ===" in review_prompt
    assert "=== File: Service.java ===" in review_prompt
    
    report = await reviewer.run(review_prompt)
    assert report == "Mock review output"
