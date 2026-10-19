from typing import Any, Type

from pydantic import BaseModel, Field

from kriya.tools.tool import (
    TOOL_OPERATOR_ONLY,
    BaseTool,
    ModelInvocationRefused,
    ModelToolInvocation,
    ToolGovernance,
)
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
    def planned_governance(self) -> ToolGovernance:
        """S18 (OI-127, Option B): builds and runs the project's tests (PolymorphicValidator(".") = the process folder, with
        a default AutonomyConfig: S18-PREFLIGHT.md R40); only an operator may run it."""
        return ToolGovernance.OPERATOR_ONLY

    @property
    def description(self) -> str:
        return "Compiles source code and runs tests locally on Python, Java, or Ruby project workspaces."

    @property
    def arguments_schema(self) -> Type[BaseModel]:
        return ValidationArgs

    def authorize_model_invocation(self, args: ValidationArgs, invocation: ModelToolInvocation) -> ValidationArgs:
        """S18 (OI-127, Option B): OPERATOR_ONLY - a planned call is refused in every mode (it built the process folder
        with a default, uncontained configuration: S18-PREFLIGHT R40). execute_model_authored's governance recheck
        answers first; this is the tool's own second wall for any caller that reaches it directly."""
        raise ModelInvocationRefused(TOOL_OPERATOR_ONLY, f"tool {self.name!r} is operator-only; a planned step never "
                                                         "builds or tests anything. Nothing was run.")

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
