import os
import pytest
from kriya.skills.skill import SkillEngine, Skill

def test_skills_lifecycle(tmp_path):
    skills_dir = tmp_path / "skills"
    skills_dir.mkdir()
    
    se = SkillEngine(str(skills_dir))
    
    # 1. Create skill skeleton
    path = se.create_skill_skeleton("FastAPI App")
    assert os.path.exists(path)
    assert os.path.exists(os.path.join(path, "skill.yaml"))
    assert os.path.exists(os.path.join(path, "instructions.md"))
    
    # Let's add rules and examples to the created skeleton folder
    with open(os.path.join(path, "rules.txt"), "w") as f:
        f.write("Always use async def\nNo sync dependencies")
    
    with open(os.path.join(path, "examples", "main.py"), "w") as f:
        f.write("# FastAPI example")
        
    # 2. Discover and Load
    se_load = SkillEngine(str(skills_dir))
    se_load.discover_and_load()
    
    skills = se_load.list_skills()
    assert len(skills) == 1
    
    skill = se_load.get_skill("FastAPI App")
    assert isinstance(skill, Skill)
    assert skill.name == "FastAPI App"
    assert "FastAPI App" in skill.description
    assert skill.rules == ["Always use async def", "No sync dependencies"]
    assert skill.examples == {"main.py": "# FastAPI example"}
    assert "fastapi-app" in skill.tags
    
    # Test find by tag
    tagged = se_load.find_skills_by_tag("fastapi-app")
    assert len(tagged) == 1
    assert tagged[0].name == "FastAPI App"
