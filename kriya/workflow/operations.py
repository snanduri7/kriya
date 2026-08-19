"""Explicit model-output operations used by generation and repair prompts."""
from dataclasses import dataclass
from enum import Enum
from typing import Any, Dict, Iterable, Optional


class CodeOperation(str, Enum):
    CREATE_FULL_FILE = "create_full_file"
    REPAIR_WITH_PATCH = "repair_with_patch"
    REPAIR_WITH_FULL_FILE = "repair_with_full_file"
    NO_CHANGE_ASSESSMENT = "no_change_assessment"


@dataclass(frozen=True)
class OperationContract:
    operation: CodeOperation
    permits_new_file: bool
    permits_content: bool
    permits_edits: bool
    terminal_without_validation: bool = False

    def validate_result(self, result: Dict[str, Any], *, file_exists: bool) -> Optional[str]:
        content = result.get("content")
        edits = result.get("edits") or []
        if content is not None and not self.permits_content:
            return f"{self.operation.value} does not permit full-file content"
        if edits and not self.permits_edits:
            return f"{self.operation.value} does not permit patch edits"
        if not file_exists and not self.permits_new_file:
            return f"{self.operation.value} cannot create a new file"
        if self.operation is CodeOperation.NO_CHANGE_ASSESSMENT and (content is not None or edits):
            return "no_change_assessment must not contain a write"
        return None


OPERATION_CONTRACTS = {
    CodeOperation.CREATE_FULL_FILE: OperationContract(
        CodeOperation.CREATE_FULL_FILE, permits_new_file=True,
        permits_content=True, permits_edits=False,
    ),
    CodeOperation.REPAIR_WITH_PATCH: OperationContract(
        CodeOperation.REPAIR_WITH_PATCH, permits_new_file=False,
        permits_content=False, permits_edits=True,
    ),
    CodeOperation.REPAIR_WITH_FULL_FILE: OperationContract(
        CodeOperation.REPAIR_WITH_FULL_FILE, permits_new_file=False,
        permits_content=True, permits_edits=False,
    ),
    CodeOperation.NO_CHANGE_ASSESSMENT: OperationContract(
        CodeOperation.NO_CHANGE_ASSESSMENT, permits_new_file=False,
        permits_content=False, permits_edits=False,
    ),
}


def operation_for_attempt(mode: str, *, has_prior_failure: bool) -> CodeOperation:
    if mode == "missing_files":
        return CodeOperation.CREATE_FULL_FILE
    if mode in ("targeted", "fallback_targeted"):
        return CodeOperation.REPAIR_WITH_PATCH
    return (
        CodeOperation.REPAIR_WITH_FULL_FILE
        if has_prior_failure else CodeOperation.CREATE_FULL_FILE
    )


def classify_result_operation(result: Dict[str, Any]) -> CodeOperation:
    if result.get("content") is not None:
        return CodeOperation.REPAIR_WITH_FULL_FILE
    if result.get("edits"):
        return CodeOperation.REPAIR_WITH_PATCH
    return CodeOperation.NO_CHANGE_ASSESSMENT


def all_results_are_no_change(results: Iterable[Dict[str, Any]]) -> bool:
    results = list(results)
    return bool(results) and all(
        classify_result_operation(result) is CodeOperation.NO_CHANGE_ASSESSMENT
        for result in results
    )
