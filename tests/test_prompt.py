import pytest
from kriya.prompt import PromptEngine, PromptEngineError

def test_prompt_engine_defaults():
    pe = PromptEngine()
    
    # Test loading a default template
    source = pe.get_template_source("system_instructions")
    assert "production-grade AI Engineering Platform" in source
    
    # Test listing variables (system_instructions has none)
    vars_set = pe.get_template_variables("system_instructions")
    assert len(vars_set) == 0

def test_prompt_engine_variables_and_rendering():
    pe = PromptEngine()
    
    # Test checking variables
    vars_set = pe.get_template_variables("refactor")
    assert vars_set == {"filepath", "code_content", "guidelines"}
    
    # Test successful rendering
    rendered = pe.render("refactor", {
        "filepath": "main.py",
        "code_content": "print('hello')",
        "guidelines": "Add documentation"
    })
    
    assert "main.py" in rendered
    assert "print('hello')" in rendered
    assert "Add documentation" in rendered

def test_prompt_engine_missing_variable_error():
    pe = PromptEngine()
    
    with pytest.raises(PromptEngineError, match="Missing required variables"):
        pe.render("refactor", {
            "filepath": "main.py"
            # code_content and guidelines missing
        })

def test_prompt_engine_custom_template(tmp_path):
    # Setup custom template file
    template_dir = tmp_path / "templates"
    template_dir.mkdir()
    
    custom_file = template_dir / "my_custom.jinja"
    with open(custom_file, "w") as f:
        f.write("Hello {{ name }}! Welcome to {{ platform }}.")
        
    pe = PromptEngine(str(template_dir))
    
    vars_set = pe.get_template_variables("my_custom")
    assert vars_set == {"name", "platform"}
    
    rendered = pe.render("my_custom", {"name": "Alice", "platform": "Kriya"})
    assert rendered == "Hello Alice! Welcome to Kriya."
