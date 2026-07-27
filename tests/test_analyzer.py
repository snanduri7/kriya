import os
import pytest
from kriya.analyzer.analyzer import RepositoryAnalyzer, RepositoryModel

def test_analyzer_python_project(tmp_path):
    # Setup dummy Python project structure
    project_dir = tmp_path / "my_python_app"
    project_dir.mkdir()
    
    # Create files
    src_dir = project_dir / "src"
    src_dir.mkdir()
    (src_dir / "app.py").write_text("from fastapi import FastAPI\napp = FastAPI()")
    (src_dir / "models.py").write_text("class User: pass")
    
    # Requirements
    (project_dir / "requirements.txt").write_text("fastapi>=0.95.0\nuvicorn\npytest")
    
    # Analyze
    analyzer = RepositoryAnalyzer(str(project_dir))
    model = analyzer.analyze()
    
    assert isinstance(model, RepositoryModel)
    assert model.languages == {"Python": 100.0}
    assert "FastAPI" in model.frameworks
    assert "pytest" in model.testing_frameworks
    assert "Flat/Modular Package layout" in model.architecture or "Standard Source" in model.architecture

def test_analyzer_node_project(tmp_path):
    # Setup dummy Node project
    project_dir = tmp_path / "my_node_app"
    project_dir.mkdir()
    
    # Create controllers/views directory to check MVC architecture
    (project_dir / "controllers").mkdir()
    (project_dir / "models").mkdir()
    (project_dir / "views").mkdir()
    
    (project_dir / "controllers" / "user.js").write_text("console.log('user controller');")
    
    package_json = """{
        "dependencies": {
            "express": "^4.18.2",
            "react": "^18.2.0"
        },
        "devDependencies": {
            "jest": "^29.5.0"
        }
    }"""
    (project_dir / "package.json").write_text(package_json)
    
    analyzer = RepositoryAnalyzer(str(project_dir))
    model = analyzer.analyze()
    
    assert "JavaScript" in model.languages
    assert "Express" in model.frameworks
    assert "React" in model.frameworks
    assert "jest" in model.testing_frameworks
    assert "MVC (Model-View-Controller)" in model.architecture

def test_chunk_file_syntactically():
    from kriya.analyzer.analyzer import chunk_file_syntactically
    
    # 1. Test short file (no chunking)
    content_short = "def hello():\n    print('hello')"
    chunks_short = chunk_file_syntactically(content_short, max_lines=10)
    assert len(chunks_short) == 1
    assert chunks_short[0]["text"] == content_short
    
    # 2. Test large file with class boundaries alignment
    lines = [f"x = {i}" for i in range(12)]
    lines.insert(8, "class MyNewClass:")
    lines.insert(9, "    def run(self): pass")
    
    content_large = "\n".join(lines)
    # chunk size 10, overlap 5. Should find "class MyNewClass:" at index 8 and align there.
    chunks_large = chunk_file_syntactically(content_large, max_lines=10, overlap=5)
    
    assert len(chunks_large) > 1
    # Check that second chunk starts with class header
    assert chunks_large[1]["text"].startswith("class MyNewClass:")
