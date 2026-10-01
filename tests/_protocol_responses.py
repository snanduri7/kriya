"""Scripted Developer answers in whichever response protocol the prompt asks for.

Test scripts are written in the historical legacy shape (FIX ANALYSIS / SEARCH /
REPLACE / FILE CONTENT / NO CHANGE NEEDED, or raw content). When the system
prompt carries the structured protocol contract (the production default,
FILE-INTEGRITY-CONTRACT-001), the same intent is rendered as sentinel blocks.
The intent is read by the real legacy parser, so a malformed scripted answer
is passed through unchanged and stays malformed under either protocol.
Imported by bare name (tests/ is on sys.path), like _plugin_test_support.
"""
import re

from kriya.agents.response_protocol import (
    EDITS,
    FILE,
    INVALID,
    NO_CHANGE,
    SENTINEL_PREFIX,
    parse_legacy_repair,
    structured_contract,
)

_LEGACY_MARKERS = ("FIX ANALYSIS:", "SEARCH:", "REPLACE:", "FILE CONTENT:", "NO CHANGE NEEDED")
# The contract line present exactly when analysis is required, i.e. when the
# legacy protocol would have parsed the answer with parse_legacy_repair
# (agent.py: analysis_required=repair_protocol); taken from the contract
# itself so it cannot drift from the prompt.
_ANALYSIS_REQUIRED_LINE = structured_contract(
    "x", analysis_required=True, allow_edit=False, allow_file=True, allow_no_change=False).splitlines()[1]


def wants_structured(system_prompt: str) -> bool:
    return "<<<KRIYA:" in (system_prompt or "")


def sentinel(filepath: str, *, analysis: str = "", content=None, edits=(), no_change: bool = False) -> str:
    """Render one intent as a structured-protocol response."""
    parts = [analysis] if analysis else []
    if no_change:
        parts.append(f'<<<KRIYA:NO_CHANGE path="{filepath}">>>')
    elif content is not None:
        body = content[:-1] if content.endswith("\n") else content
        end = "<<<KRIYA:END_FILE>>>" if content.endswith("\n") or not content else "<<<KRIYA:END_FILE no_final_newline>>>"
        parts.append(f'<<<KRIYA:FILE path="{filepath}">>>' + (f"\n{body}" if content else "") + f"\n{end}")
    else:
        pairs = "".join(f"<<<KRIYA:SEARCH>>>\n{search}\n<<<KRIYA:REPLACE>>>\n" + (f"{replace}\n" if replace else "")
                        for search, replace in edits)
        parts.append(f'<<<KRIYA:EDIT path="{filepath}">>>\n{pairs}<<<KRIYA:END_EDIT>>>')
    return "\n".join(parts) + "\n"


def as_requested(answer: str, system_prompt: str, filepath: str = None) -> str:
    """``answer`` (legacy-shaped) in the protocol ``system_prompt`` asks for;
    ``filepath`` defaults to the path the structured contract names."""
    if not wants_structured(system_prompt) or SENTINEL_PREFIX in answer:
        return answer  # already a sentinel answer (valid or not): never rewritten
    if filepath is None:
        filepath = re.search(r'path="([^"]+)"', system_prompt).group(1)
    if any(marker in answer for marker in _LEGACY_MARKERS) or _ANALYSIS_REQUIRED_LINE in system_prompt:
        # A repair request: legacy read it with the marker parser, so a raw
        # answer there was INVALID (no FIX ANALYSIS) and stays unchanged here.
        parsed = parse_legacy_repair(answer, filepath, patch_allowed=True)
    else:
        # Exactly what the legacy raw-content path reads (outer fence and
        # the multi-file JSON envelope unwrapped), so the rendered intent is
        # the one the scripted answer always had.
        from kriya.agents.agent import DeveloperAgent

        parsed = DeveloperAgent.parse_file_payload(answer, filepath)
    if parsed.kind == INVALID:
        return answer
    if parsed.kind == NO_CHANGE:
        return sentinel(filepath, analysis=parsed.analysis or "", no_change=True)
    if parsed.kind == EDITS:
        return sentinel(filepath, analysis=parsed.analysis or "", edits=parsed.edits)
    assert parsed.kind == FILE
    return sentinel(filepath, analysis=parsed.analysis or "", content=parsed.content)
