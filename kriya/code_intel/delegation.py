"""Thin-delegate relation of a Java member: one owner of the fact "this member's value is computed by that project method".

BACKEND-READINESS-V2 (T2): the goal named ``Element.absUrl``; its declaration only ends in ``return StringUtil.resolve(...)``,
so the behaviour the goal describes lives in the callee. The Planner's GOAL-NAMED FACTS (``workflow/goal_facts.py``) and the
Developer's context package (``packing.py``) must see the same relationship, so both read it from here. Deterministic: the LAST
``return`` of the member whose expression calls ``Type.method(`` on a project type that declares ``method`` in another file.
Evidence only; it never says what to change. Java only.
"""
from __future__ import annotations

import re
from typing import Any, List, Optional, Tuple

_RETURN_RE = re.compile(r"\breturn\s+([^;]+);")
_STATIC_CALL_RE = re.compile(r"\b([A-Z]\w*)\.([a-z_]\w*)\s*\(")


def blank_comments_and_strings(code: str) -> str:
    """``code`` with every comment and string/char literal replaced by blanks of equal length (not a Java lexer: text blocks
    and unicode escapes are not understood; a miss only means the member is not read as a delegate)."""
    out: List[str] = []
    index, size = 0, len(code)
    while index < size:
        ch = code[index]
        nxt = code[index + 1] if index + 1 < size else ""
        if ch == "/" and nxt == "/":
            end = code.find("\n", index)
            end = size if end == -1 else end
            out.append(" " * (end - index))
            index = end
        elif ch == "/" and nxt == "*":
            end = code.find("*/", index + 2)
            end = size if end == -1 else end + 2
            out.append("".join(c if c == "\n" else " " for c in code[index:end]))
            index = end
        elif ch in "\"'":
            end = index + 1
            while end < size and code[end] != ch:
                end += 2 if code[end] == "\\" else 1
            end = min(end + 1, size)
            out.append(ch + " " * max(end - index - 2, 0) + (ch if end - index >= 2 else ""))
            index = end
        else:
            out.append(ch)
            index += 1
    return "".join(out)


def delegate_call(member_text: str) -> Optional[Tuple[str, str]]:
    """(callee type, callee method) of the member's last ``return`` expression, or None."""
    returns = _RETURN_RE.findall(blank_comments_and_strings(member_text))
    call = _STATIC_CALL_RE.search(returns[-1]) if returns else None
    return (call.group(1), call.group(2)) if call else None


def delegate_targets(view: Any, symbol: Any, member_text: str) -> List[Any]:
    """The callable declarations (in other files) of the method the member's last ``return`` delegates to, in declaration
    order. Empty when the member is not a thin delegate or the callee is not a project method."""
    call = delegate_call(member_text)
    if call is None:
        return []
    callee_type, callee = call
    return sorted((t for t in view.find_symbol(f"{callee_type}.{callee}") if t.is_callable and t.path != symbol.path),
                  key=lambda t: (t.path, t.declaration.start_line))
