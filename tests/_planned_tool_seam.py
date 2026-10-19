"""S18 test-only registration seam for planned-tool MECHANICS tests.

Production rule (kriya/tools/tool.py::resolve_planned_tool_governance): a planned-call declaration is honoured only for the
shipped tools in ``_SHIPPED_PLANNED_DECLARATIONS`` (the core_tools tools and MCPTool), and only when the declaring property is
defined on that exact class in its shipped file. A test double is never one of them, so without this seam every planned call
to a double is refused (TOOL_MODEL_AUTHORITY_UNDECLARED).

Some tests are about the executor's mechanics, not about which tools are plannable: status mapping, the PolicyDeniedError
reason-code capture, "at most one call, never retried", B3's mode gate, the evidence unit of a TOOL subtask. Those tests keep
their own deterministic double and admit it here, for the duration of one test (monkeypatch undoes it), by adding the double's
OWN class and the file that defines its ``planned_governance`` to the table. The double must declare ``planned_governance``
itself; the production predicate (exact class, property defined on the class, file match) still runs unchanged.

Why this does not weaken the production rule: the table is module state, the seam is a test-only monkeypatch, and the rule is
pinned on its own WITHOUT the seam (tests/test_s18_planned_tool_governance.py: an undeclared tool, a self-declared non-shipped
tool, a subclass of a shipped tool and a garbage declaration are each unplannable at the catalogue, at validation and at the
executor). Never use it in a test whose subject is the governance decision itself.
"""
import os

from kriya.tools import tool as tool_contract


def admit_planned_test_tool(monkeypatch, cls) -> None:
    declared = cls.__dict__.get("planned_governance")
    code = getattr(getattr(declared, "fget", declared), "__code__", None)
    assert code is not None, f"{cls.__qualname__} must declare planned_governance itself to be admitted"
    monkeypatch.setitem(tool_contract._SHIPPED_PLANNED_DECLARATIONS, cls.__qualname__, os.path.realpath(code.co_filename))
