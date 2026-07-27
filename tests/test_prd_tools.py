import os
import sys
import pytest

# Add workspace root to sys.path to allow importing from plugins
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from plugins.core_tools import FilesystemTool, SearchTool, ASTTool, FilesystemArgs, SearchArgs, ASTArgs

@pytest.mark.asyncio
async def test_filesystem_chunked_read(tmp_path):
    file_path = tmp_path / "large_file.txt"
    file_path.write_text("line1\nline2\nline3\nline4\nline5\n")
    
    tool = FilesystemTool()
    
    # Read entire file
    args_all = FilesystemArgs(operation="read", path=str(file_path))
    content_all = await tool.execute(**args_all.model_dump())
    assert content_all == "line1\nline2\nline3\nline4\nline5\n"
    
    # Read chunk (lines 2 to 4)
    args_chunk = FilesystemArgs(operation="read", path=str(file_path), start_line=2, end_line=4)
    content_chunk = await tool.execute(**args_chunk.model_dump())
    assert content_chunk == "line2\nline3\nline4\n"

@pytest.mark.asyncio
async def test_native_search_tool(tmp_path):
    file1 = tmp_path / "app.py"
    file1.write_text("import os\n\ndef add(a, b):\n    return a + b\n")
    
    tool = SearchTool()
    args = SearchArgs(pattern="def add", path=str(tmp_path), file_glob="*.py")
    results = await tool.execute(**args.model_dump())
    
    assert "app.py:3: def add(a, b):" in results

@pytest.mark.asyncio
async def test_ast_tool_python_java_xml(tmp_path):
    tool = ASTTool()
    
    # 1. Test Python parsing
    py_file = tmp_path / "hello.py"
    py_file.write_text("class Calculator:\n    def evaluate(self):\n        pass\n\ndef hello():\n    pass")
    res_py = await tool.execute(**ASTArgs(file_path=str(py_file)).model_dump())
    assert "Class: Calculator" in res_py
    assert "hello" in res_py
    
    # 2. Test Java Parsing
    java_file = tmp_path / "UserService.java"
    java_file.write_text("package com.kriya.service;\n\nimport org.springframework.stereotype.Service;\n\n@Service\npublic class UserService {\n    public void saveUser() {}\n}")
    res_java = await tool.execute(**ASTArgs(file_path=str(java_file)).model_dump())
    assert "Java Package: com.kriya.service" in res_java
    assert "class UserService" in res_java
    assert "Spring Annotations: Service" in res_java
    
    # 3. Test XML Parsing
    xml_file = tmp_path / "beans.xml"
    xml_file.write_text('<?xml version="1.0" encoding="UTF-8"?>\n<beans>\n    <bean id="userController" class="com.kriya.controller.UserController"/>\n</beans>')
    res_xml = await tool.execute(**ASTArgs(file_path=str(xml_file)).model_dump())
    assert "Spring XML Bean definitions found: userController" in res_xml
