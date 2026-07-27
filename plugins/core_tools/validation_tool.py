from typing import Type, Any
from pydantic import BaseModel, Field
from kriya.tools.tool import BaseTool, ToolExecutionError
from kriya.tools.validate import PolymorphicValidator

class ValidationArgs(BaseModel):
    files: list[str] = Field(default_factory=list, description="List of file paths relative to workspace to compile check.")
    run_tests: bool = Field(default=True, description="Whether to execute the workspace test suite.")

class ValidationTool(BaseTool):
    """Macro tool to compile-check files and run tests polymorphically for Python, Java, and Ruby."""

    @property
    def name(self) -> str:
        return "validate_refactor"

    @property
    def description(self) -> str:
        return "Compiles source code and runs tests locally on Python, Java, or Ruby project workspaces."

    @property
    def arguments_schema(self) -> Type[BaseModel]:
        return ValidationArgs

    async def _run(self, args: ValidationArgs) -> Any:
        workspace_path = "."
        validator = PolymorphicValidator(workspace_path)
        
        # 1. Run Compile Check
        compile_res = validator.run_compile_check(args.files or [])
        if not compile_res["success"]:
            return f"COMPILATION FAILURE:\n{compile_res['output']}"
            
        # 2. Run Test Validation
        if args.run_tests:
            test_res = validator.run_tests()
            if not test_res["success"]:
                return f"TEST FAILURE:\n{test_res['output']}"
            return f"VALIDATION SUCCESS:\n{compile_res['output']}\n{test_res['output']}"
            
        return f"VALIDATION SUCCESS:\n{compile_res['output']}"
