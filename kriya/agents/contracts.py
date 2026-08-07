"""Structured-output contracts for agent stages whose result is consumed
programmatically downstream, not just read as prose by a human/the Reviewer.
Deliberately narrow: only fields something else actually parses belong here -
free-text rationale/design reasoning stays plain text in the agent's own
response and is never forced through a schema.

First contract, shared by two call sites: a validated list of file paths.
ArchitectAgent.run_with_file_list() uses it for the design's authoritative
file list - the one part of a design IncompleteGenerationError's completeness
check and the Developer's upfront "required files" prompt block both depend
on being accurate, previously recovered by extract_expected_files() (a
blanket regex over the ENTIRE design's prose matching any "word.ext"-shaped
token, with no way to distinguish a real requirement from an incidental
mention like "similar to Foo.java elsewhere in the codebase"). Generalized
2026-08-07 to DeveloperAgent.run_generation()'s own Step 1 (the "which files
do I need to write" query, structurally the identical problem - previously
_extract_json_value()+_normalize_file_entries()'s hand-rolled parsing, no
schema at all) - see kriya/workflow/workflow.py and kriya/agents/agent.py for
both call sites' own older, heuristic fallbacks, kept as this module's
fallback of last resort in each, not removed, since local models don't get
schema-constrained decoding from LLMClient today (kriya/core/llm.py's
json_mode only guarantees SOME valid JSON, not any particular shape) and a
malformed response must degrade, never crash the run.
"""
import json
import re
from typing import List, Optional, Tuple

from pydantic import BaseModel, ValidationError, field_validator


class FileList(BaseModel):
    """A validated list of workspace-relative file paths - the authoritative
    set of files a design calls for (ArchitectAgent), or the set a Developer
    completion says it needs to create/modify (DeveloperAgent's Step 1)."""

    files: List[str]

    @field_validator("files")
    @classmethod
    def _non_empty(cls, v: List[str]) -> List[str]:
        if not v:
            raise ValueError("files list must not be empty - a generation run always touches at least one file")
        return v

    @field_validator("files")
    @classmethod
    def _sane_workspace_relative_paths(cls, v: List[str]) -> List[str]:
        cleaned = []
        for raw in v:
            path = (raw or "").strip()
            if not path:
                raise ValueError("file path must not be blank")
            if path.startswith("/") or path.startswith("\\") or re.match(r"^[A-Za-z]:[\\/]", path):
                raise ValueError(f"file path must be workspace-relative, not absolute: {path!r}")
            if ".." in path.replace("\\", "/").split("/"):
                raise ValueError(f"file path must not contain '..' path-traversal segments: {path!r}")
            cleaned.append(path)
        return cleaned


# Takes the LAST fenced ```json ... ``` (or bare ```...```) block in the text
# specifically - a design occasionally includes a smaller illustrative JSON
# snippet earlier (e.g. a sample config payload) before the real file-list
# block, which is always meant to be the final thing in the response per
# ArchitectAgent's own system prompt.
_FENCED_JSON_BLOCK = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.DOTALL)
# Last-resort match for a response that emits the JSON object without ever
# wrapping it in a fence at all (DeveloperAgent's Step 1 is asked to return
# exactly this shape, no fence) - anchored on the "files" key specifically so
# it doesn't accidentally grab an unrelated brace-delimited snippet.
_BARE_FILES_OBJECT = re.compile(r'\{[^{}]*"files"\s*:\s*\[[^\]]*\][^{}]*\}', re.DOTALL)


def parse_file_list(text: str) -> Tuple[Optional[List[str]], Optional[str]]:
    """Extracts and validates a {"files": [...]} JSON block from a completion
    - a full Architect design (the file list is a trailing block inside a
    much larger prose response) or a Developer Step-1 response (asked to be
    exactly this shape, nothing else).

    Returns (files, None) on success or (None, error_message) on any failure
    - never raises, so callers can decide how to degrade (retry, fall back to
    a heuristic) without wrapping every call in a try/except for a
    json.JSONDecodeError or a pydantic ValidationError."""
    if not text or not text.strip():
        return None, "text is empty"

    candidates = _FENCED_JSON_BLOCK.findall(text) or _BARE_FILES_OBJECT.findall(text)
    if not candidates:
        return None, "no JSON file-list block found in the text"

    # Last block wins - see the illustrative-snippet-before-the-real-list case above.
    try:
        parsed = json.loads(candidates[-1])
    except json.JSONDecodeError as e:
        return None, f"file-list JSON block did not parse: {e}"

    try:
        return FileList.model_validate(parsed).files, None
    except ValidationError as e:
        return None, f"file-list JSON block failed schema validation: {e}"
