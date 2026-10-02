"""Developer prompt cost accounting (Code Intelligence R1 slice 2, item 7).

Observational only: what a Developer request spent its prompt on, so local
prefill cost can be measured and the smallest sufficient context pursued.
Sections are recognized by the markers Kriya itself renders
(``build_known_target_context`` and the Code Intelligence package), counted
with ``estimate_tokens`` and labeled estimated; the total and the prefill
time are the provider's own report when it gives them.
"""
from __future__ import annotations

import re
from typing import Any, Dict, Mapping, Optional

from kriya.workflow.context_budget import estimate_tokens

_OWNER_BLOCK_RE = re.compile(r"^=== EXISTING OWNER \((?P<label>[^\n]*)\): [^\n]* ===\n", re.MULTILINE)
_PACKAGE_SECTION_RE = re.compile(r"^#### (?P<title>[^\n]*)\n", re.MULTILINE)
# Package section title prefix -> tier.
_SECTION_TIERS = (
    ("Enclosing declarations", "t0_header"),
    ("Other members of the enclosing type", "sibling_signatures"),
    ("Collaborator", "t1"),
    ("Linked tests", "t2"),
    ("Linked configuration", "t3"),
)


def _blocks(text: str, pattern: "re.Pattern[str]"):
    matches = list(pattern.finditer(text))
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        yield match, text[match.end():end]


def prompt_composition(code_context: str, skills_prompt: str, *, prompt_tokens_reported: Optional[int],
                       provider_metadata: Optional[Mapping[str, Any]] = None,
                       prefix_reuse: Optional[Mapping[str, Any]] = None) -> Dict[str, Any]:
    tiers = {"t0_member": 0, "t0_header": 0, "sibling_signatures": 0, "t1": 0, "t2": 0, "t3": 0}
    for match, body in _blocks(code_context or "", _OWNER_BLOCK_RE):
        if "tier=member_exact" in match.group("label"):
            tiers["t0_member"] += estimate_tokens(body)
            continue
        for section, section_body in _blocks(body, _PACKAGE_SECTION_RE):
            title = section.group("title")
            tier = next((name for prefix, name in _SECTION_TIERS if title.startswith(prefix)), None)
            if tier is not None:
                tiers[tier] += estimate_tokens(section_body)
    metadata = provider_metadata or {}
    reuse = prefix_reuse or {}
    prefill_ms = metadata.get("prompt_eval_ms")
    return {
        **{f"{name}_tokens": count for name, count in tiers.items()},
        "skills_tokens": estimate_tokens(skills_prompt or ""),
        "code_context_tokens": estimate_tokens(code_context or ""),
        "prompt_tokens_reported": prompt_tokens_reported,
        "prefill_seconds": round(prefill_ms / 1000.0, 3) if isinstance(prefill_ms, (int, float)) else None,
        "load_seconds": (round(metadata["load_ms"] / 1000.0, 3)
                         if isinstance(metadata.get("load_ms"), (int, float)) else None),
        # Shared with the previous request to the same model (what an
        # inference server's KV cache can reuse) and where it first differed.
        "prefix_shared_tokens": (reuse["prefix_shared_chars"] // 4  # estimate_tokens' ratio
                                 if isinstance(reuse.get("prefix_shared_chars"), int) else None),
        "prefix_break": reuse.get("prefix_break"),
        "token_counts": "estimated (len/4) per section; total as reported by the provider",
    }
