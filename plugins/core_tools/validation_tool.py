from typing import Any, Type

from pydantic import BaseModel, Field

from kriya.policy.filesystem import AuthorizedFileReader
from kriya.tools.tool import TOOL_MUTATION_REVIEW_UNAVAILABLE, BaseTool, ModelInvocationRefused, ModelToolInvocation
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

    def authorize_model_invocation(self, args: ValidationArgs, invocation: ModelToolInvocation) -> ValidationArgs:
        """S1: builds and runs the repository's tests - in human-in-the-loop nothing is built, tested or run before
        the human's review, and a planned step has no review subject, so it is refused there. Elsewhere the validator's
        own authority governs it (unchanged); each named file is a model-requested read (AuthorizedFileReader)."""
        if invocation.reviews_every_change:
            raise ModelInvocationRefused(
                TOOL_MUTATION_REVIEW_UNAVAILABLE,
                f"tool {self.name!r} builds and runs the project's tests, this run reviews every change before anything "
                "runs, and a planned step cannot ask you. Nothing was run.",
            )
        files = [invocation.resolve(f) for f in args.files]
        if files:
            reader = AuthorizedFileReader(invocation.roots[0], invocation.roots[1:])
            for target in files:
                reader.raise_if_denied(target)
        return args.model_copy(update={"files": files})

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
