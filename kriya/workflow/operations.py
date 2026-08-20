"""Explicit model-output operations used by generation and repair prompts.

The operation selected by the workflow is an executable contract, not merely
prompt wording.  Keeping classification and transition policy here gives every
generation path (including Planner reuse and batch JSON fallback) the same
rules before any bytes are written.
"""
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
        if content is not None and edits:
            return f"{self.operation.value} must contain exactly one write shape"
        if content is not None and not self.permits_content:
            return f"{self.operation.value} does not permit full-file content"
        if edits and not self.permits_edits:
            return f"{self.operation.value} does not permit patch edits"
        if not file_exists and not self.permits_new_file:
            return f"{self.operation.value} cannot create a new file"
        if self.operation is CodeOperation.CREATE_FULL_FILE and file_exists:
            return "create_full_file cannot overwrite an existing file"
        if self.operation in (
            CodeOperation.CREATE_FULL_FILE,
            CodeOperation.REPAIR_WITH_FULL_FILE,
        ) and content is None:
            return f"{self.operation.value} requires full-file content"
        if self.operation is CodeOperation.REPAIR_WITH_PATCH and not edits:
            return "repair_with_patch requires at least one patch edit"
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


def operation_for_file(
    attempt_operation: CodeOperation, *, file_exists: bool,
) -> CodeOperation:
    """Resolve a batch-level operation to the contract for one concrete file.

    Any batch may contain both new and existing files: initial generation can
    update a repository file, while a repair can add a newly-required companion
    file. New files need CREATE semantics; existing files must never be silently
    treated as creations (which would discard revision/overwrite protections).
    """
    if not file_exists:
        return CodeOperation.CREATE_FULL_FILE
    if attempt_operation is CodeOperation.CREATE_FULL_FILE and file_exists:
        return CodeOperation.REPAIR_WITH_FULL_FILE
    return attempt_operation


def classify_result_operation(
    result: Dict[str, Any], *, file_exists: bool = True,
) -> CodeOperation:
    if result.get("content") is not None:
        return (
            CodeOperation.REPAIR_WITH_FULL_FILE
            if file_exists else CodeOperation.CREATE_FULL_FILE
        )
    if result.get("edits"):
        return CodeOperation.REPAIR_WITH_PATCH
    return CodeOperation.NO_CHANGE_ASSESSMENT


def validate_operation_result(
    result: Dict[str, Any], *, expected: CodeOperation, file_exists: bool,
) -> tuple[CodeOperation, Optional[str]]:
    """Validate a model result and its transition from the requested operation.

    Patch and full-file *repairs* may safely fall back to each other when the
    target already exists; both may also return an explicit no-change assessment.
    Those are observable transitions rather than accidental parser fall-throughs.
    Creation has no write fallback: an absent file must be returned as a complete
    file, and an existing file is never accepted under the creation contract.
    """
    actual = classify_result_operation(result, file_exists=file_exists)
    if result.get("protocol_error"):
        return actual, f"malformed repair response: {result['protocol_error']}"
    allowed = {expected}
    if expected is CodeOperation.REPAIR_WITH_PATCH:
        allowed.update({
            CodeOperation.REPAIR_WITH_FULL_FILE,
            CodeOperation.NO_CHANGE_ASSESSMENT,
        })
    elif expected is CodeOperation.REPAIR_WITH_FULL_FILE:
        allowed.update({
            CodeOperation.REPAIR_WITH_PATCH,
            CodeOperation.NO_CHANGE_ASSESSMENT,
        })

    if actual not in allowed:
        return actual, (
            f"requested {expected.value}, but the response has {actual.value} shape"
        )
    return actual, OPERATION_CONTRACTS[actual].validate_result(
        result, file_exists=file_exists,
    )


def all_results_are_no_change(results: Iterable[Dict[str, Any]]) -> bool:
    results = list(results)
    return bool(results) and all(
        classify_result_operation(result) is CodeOperation.NO_CHANGE_ASSESSMENT
        for result in results
    )
