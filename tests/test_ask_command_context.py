from unittest.mock import AsyncMock, patch

from click.testing import CliRunner

from kriya.cli import main


def test_ask_command_injects_files_context(tmp_path):
    # Setup mock workspace files
    src_dir = tmp_path / "src" / "main" / "java" / "com" / "example"
    src_dir.mkdir(parents=True)
    
    app_java = src_dir / "App.java"
    app_java.write_text(
        "package com.example;\n"
        "public class App {\n"
        "    public static void main(String[] args) {\n"
        "        System.out.println(\"Hello World\");\n"
        "    }\n"
        "}"
    )
    
    pom_xml = tmp_path / "pom.xml"
    pom_xml.write_text(
        "<project>\n"
        "    <artifactId>spring-hello</artifactId>\n"
        "</project>"
    )

    async def mock_impl(*args, **kwargs):
        cb = kwargs.get("stream_callback")
        if cb:
            cb("Answer: execute mvn compile.")
        return "Answer: execute mvn compile."
        
    mock_complete = AsyncMock(side_effect=mock_impl)
    
    runner = CliRunner()
    
    with patch("os.getcwd", return_value=str(tmp_path)), \
         patch("kriya.core.llm.LLMClient.complete", new=mock_complete):
         
        res = runner.invoke(main, ["ask", "How to build?"])
        
        assert res.exit_code == 0
        assert "execute mvn compile" in res.output
        
        # Verify that mock_complete was called and args[1] (user prompt)
        # contained the file content of pom.xml and App.java
        mock_complete.assert_called_once()
        args = mock_complete.call_args[0]
        
        user_prompt = args[1]
        assert "=== Key Files Context ===" in user_prompt
        assert "spring-hello" in user_prompt
        assert "public static void main" in user_prompt
        assert "App.java" in user_prompt
        assert "pom.xml" in user_prompt
