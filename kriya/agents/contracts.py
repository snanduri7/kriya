"""Structured-output contracts for agent stages whose result is consumed
programmatically downstream, not just read as prose by a human/the Reviewer.
Deliberately narrow: only fields something else actually parses belong here -
free-text rationale/design reasoning stays plain text in the agent's own
response and is never forced through a schema.

First (and currently only) contract: the Architect's file list. Everything
else in a design - interface sketches, minimalism reasoning, the build-
manifest rule - stays prose. The file list is different: it's the one part
of the design IncompleteGenerationError's completeness check and the
Developer's upfront "required files" prompt block both depend on being
accurate, and it used to be recovered by extract_expected_files() - a blanket
regex over the ENTIRE design's prose matching any "word.ext"-shaped token,
with no way to distinguish a real requirement from an incidental mention
(e.g. "similar to Foo.java elsewhere in the codebase" would match), returning
bare basenames that needed a second, separately fragile regex pass
(_resolve_file_paths_from_design) to recover a real path. See
kriya/workflow/workflow.py for both - kept as this module's fallback of last
resort, not removed, since local models don't get schema-constrained
decoding from LLMClient today (kriya/core/llm.py's json_mode only guarantees
SOME valid JSON, not any particular shape) and a malformed response must
degrade, never crash the run.
"""
import json
import re
from typing import List, Optional, Tuple

from pydantic import BaseModel, ValidationError, field_validator


class ArchitectFileList(BaseModel):
    """The authoritative list of file paths an Architect design calls for."""

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


# Takes the LAST fenced ```json ... ``` (or bare ```...```) block in the
# design specifically - a design occasionally includes a smaller
# illustrative JSON snippet earlier (e.g. a sample config payload) before
# the real file-list block, which is always meant to be the final thing in
# the response per ArchitectAgent's own system prompt.
_FENCED_JSON_BLOCK = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.DOTALL)
# Last-resort match for a model that emits the JSON object without ever
# wrapping it in a fence at all - anchored on the "files" key specifically
# so it doesn't accidentally grab an unrelated brace-delimited snippet.
_BARE_FILES_OBJECT = re.compile(r'\{[^{}]*"files"\s*:\s*\[[^\]]*\][^{}]*\}', re.DOTALL)


def parse_architect_file_list(design: str) -> Tuple[Optional[List[str]], Optional[str]]:
    """Extracts and validates the Architect's trailing JSON file-list block.

    Returns (files, None) on success or (None, error_message) on any failure
    - never raises, so callers can decide how to degrade (retry, fall back to
    a heuristic) without wrapping every call in a try/except for a
    json.JSONDecodeError or a pydantic ValidationError."""
    if not design or not design.strip():
        return None, "design is empty"

    candidates = _FENCED_JSON_BLOCK.findall(design) or _BARE_FILES_OBJECT.findall(design)
    if not candidates:
        return None, "no JSON file-list block found in the design"

    # Last block wins - see the illustrative-snippet-before-the-real-list case above.
    try:
        parsed = json.loads(candidates[-1])
    except json.JSONDecodeError as e:
        return None, f"file-list JSON block did not parse: {e}"

    try:
        return ArchitectFileList.model_validate(parsed).files, None
    except ValidationError as e:
        return None, f"file-list JSON block failed schema validation: {e}"
