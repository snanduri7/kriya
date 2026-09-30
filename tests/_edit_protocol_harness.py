"""Synthetic reproducer for CONTEXT-EDIT-PROTOCOL-001 (no benchmark code).

A brownfield repair whose single target is a module far larger than the
Developer's context budget, so attempt 1 shows it only as a skeleton. The
goal names one exact line inside an elided function body. The Developer is
scripted at the transport seam (LLMClient._request_once), so every request
Kriya builds - prompt text, operation contract, retry context - is the real
one; the gates are stubbed green, so only the edit protocol decides."""
import asyncio
import json
import subprocess
from dataclasses import dataclass, field
from typing import Callable, List
from unittest.mock import AsyncMock, patch

from kriya.config import AppConfig
from kriya.core import model_runtime
from kriya.core.kernel import Kernel
from kriya.core.llm import LLMClient
from kriya.workflow.state import GenerationState
from kriya.workflow.workflow import WorkflowEngine

TARGET = "pkg/resolver.py"
# The line the goal names; it sits inside the body of `resolve_member_call`.
NAMED_LINE = "callee = read_text(name_node, source)"
NAMED_LINE_FIX = "callee = bare_name(name_node, source)"


def _filler(index: int) -> str:
    body = "".join(f"    value_{index}_{k} = compute_{k}(value_{index}_{k - 1 if k else 0})\n" for k in range(12))
    return f"def helper_{index}(node, source):\n    value_{index}_0 = node\n{body}    return value_{index}_11\n\n\n"


TARGET_SOURCE = (
    '"""A call resolver."""\n\n\n'
    + "".join(_filler(i) for i in range(70))
    + "def resolve_member_call(node, source, index):\n"
    + "    name_node = node.child_by_field_name('name')\n"
    + "    if name_node is None:\n"
    + "        return None\n"
    + f"    {NAMED_LINE}\n"
    + "    return index.get(callee)\n\n\n"
    + "".join(_filler(i) for i in range(70, 140))
)

# A target whose named line sits inside one member far too large to show
# whole: only a local window can cover it, and that window can grow.
BIG_MEMBER_SOURCE = (
    '"""A call resolver."""\n\n\n'
    + "def resolve_member_call(node, source, index):\n"
    + "    name_node = node.child_by_field_name('name')\n"
    + "".join(f"    step_{k} = transform_{k}(name_node, source, {k})\n" for k in range(600))
    + f"    {NAMED_LINE}\n"
    + "".join(f"    after_{k} = settle_{k}(callee, index, {k})\n" for k in range(600))
    + "    return index.get(callee)\n"
)
# A real line of the big member far from the named line.
FAR_LINE = "step_20 = transform_20(name_node, source, 20)"
FAR_EDIT = (
    "FIX ANALYSIS: the early transform reads the raw name.\n"
    f"SEARCH:\n    {FAR_LINE}\nREPLACE:\n    step_20 = transform_20(bare_name(name_node, source), source, 20)\n"
)
# The canonical shape of a rejected answer: a real line of the file quoted
# together with a fabricated one, so the block as a whole is not in the file.
REAL_PLUS_FABRICATED_EDIT = (
    "FIX ANALYSIS: the early transform reads the raw name.\n"
    f"SEARCH:\n    {FAR_LINE}\n    # the generic fallback reads raw text here\n"
    f"REPLACE:\n    step_20 = transform_20(bare_name(name_node, source), source, 20)\n"
)
# A target small enough to be shown whole: genuine full-file authority.
SMALL_SOURCE = (
    "def resolve_member_call(node, source, index):\n"
    "    name_node = node.child_by_field_name('name')\n"
    f"    {NAMED_LINE}\n"
    "    return index.get(callee)\n"
)
# A goal naming no code at all: nothing in it can localize an edit.
UNLOCALIZED_GOAL = "Member calls with type arguments never resolve; make them resolve to the declared member."

GOAL = (
    "A member call whose name node carries type arguments never resolves. "
    f"In `{TARGET}`, the line `{NAMED_LINE}` takes the node text verbatim, so the index "
    "lookup misses. Read the bare name instead."
)

FABRICATED_EDIT = (
    "FIX ANALYSIS: the member call reads the raw node text.\n"
    "SEARCH:\n    callee = name_node.text.decode()\nREPLACE:\n    callee = bare_name(name_node, source)\n"
)
CORRECT_EDIT = (
    "FIX ANALYSIS: the member call reads the raw node text.\n"
    f"SEARCH:\n    {NAMED_LINE}\nREPLACE:\n    {NAMED_LINE_FIX}\n"
)
FULL_FILE = "FIX ANALYSIS: rewrite.\nFILE CONTENT:\n" + TARGET_SOURCE.replace(NAMED_LINE, NAMED_LINE_FIX)


@dataclass
class EditRun:
    result: dict
    developer: List[tuple] = field(default_factory=list)  # (system, user) per Developer generation request
    events: list = field(default_factory=list)

    def kinds(self, kind):
        return [event for event in self.events if event.kind == kind]


def _git(workspace, *args):
    subprocess.run(["git", *args], cwd=workspace, check=True, capture_output=True)


def make_workspace(tmp_path, source=TARGET_SOURCE):
    workspace = tmp_path / "ws"
    (workspace / "pkg").mkdir(parents=True)
    (workspace / "pkg" / "__init__.py").write_text("")
    (workspace / TARGET).write_text(source)
    _git(workspace, "init", "-q")
    _git(workspace, "-c", "user.email=t@t", "-c", "user.name=t", "add", "-A")
    _git(workspace, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "base")
    return workspace


# The production operator profile's measured capabilities (PRD-036 config).
PRODUCTION_CAPABILITIES = {"native_tool_calls": True, "json_mode": True, "reliable_multiline_json": False,
                           "streaming": True, "max_tool_argument_chars": 8192,
                           "preferred_edit_protocol": "small_native_tools"}


def make_config(tmp_path, window=32768, capabilities=None, max_tokens=None):
    cfg = AppConfig()
    if max_tokens is not None:
        cfg.llm.max_tokens = max_tokens
    if capabilities is not None:
        cfg.llm.capabilities = type(cfg.llm.capabilities)(**capabilities)
    cfg.llm.model = "dev-model"
    cfg.llm.context_window = window
    cfg.llm.extra_body = {}
    cfg.llm_chain = []
    cfg.autonomy.mode = "guardrails"
    cfg.autonomy.run_verification_enabled = False
    cfg.autonomy.spec_compliance_enabled = False
    cfg.paths.skills = str(tmp_path / "skills")
    cfg.paths.memory = str(tmp_path / "memory")
    cfg.logging.file_enabled = False
    cfg.logging.run_file_enabled = False
    return cfg


def run_edit_protocol(tmp_path, monkeypatch, developer_answers: List[str], *, window=32768,
                      probe: Callable = None, goal: str = GOAL, capabilities=PRODUCTION_CAPABILITIES,
                      source: str = TARGET_SOURCE, max_tokens=None) -> EditRun:
    """One real direct run of the brownfield repair. The Developer gives the
    scripted answers in order, then keeps giving the last one."""
    if probe is not None:
        monkeypatch.setattr(model_runtime, "probe_model_runtime", probe)
    model_runtime.clear_model_runtime_cache()
    cfg = make_config(tmp_path, window, capabilities, max_tokens)
    workspace = make_workspace(tmp_path, source)
    answers = list(developer_answers)
    run = EditRun(result={})
    real_record = GenerationState.record_event

    async def transport(llm, client, model, system_prompt, user_prompt, *args, **kwargs):
        del llm, client, model, args, kwargs
        first = (system_prompt or "").splitlines()[0] if system_prompt else ""
        if "File List Planner" in first:
            content = json.dumps({"files": [TARGET]})
        elif "Developer Agent" in first:
            run.developer.append((system_prompt, user_prompt))
            content = answers.pop(0) if len(answers) > 1 else answers[0]
        else:
            content = "Review: Approved"
        # A plausible provider count (about 3.5 bytes per token): an
        # identified runtime reporting far fewer tokens than the prompt can
        # tokenize to is PROVIDER_PROMPT_TRUNCATED.
        prompt_tokens = len(((system_prompt or "") + (user_prompt or "")).encode("utf-8")) * 2 // 7
        return {"content": content, "reasoning_chars": 0, "prompt_tokens": prompt_tokens, "completion_tokens": 5,
                "finish_reason": "stop", "provider_metadata": {}}

    def record_spy(state, event):
        run.events.append(event)
        return real_record(state, event)

    engine = WorkflowEngine(Kernel(config=cfg), LLMClient(cfg))
    with patch.object(LLMClient, "_request_once", new=transport), \
         patch.object(GenerationState, "record_event", new=record_spy), \
         patch("kriya.tools.validate.PolymorphicValidator.run_compile_check",
               new=lambda *a, **k: {"success": True, "output": "ok"}), \
         patch("kriya.tools.validate.PolymorphicValidator.run_tests",
               new=lambda *a, **k: {"success": True, "output": "ok"}):
        run.result = asyncio.run(engine.run_generation_workflow(
            goal=goal, workspace_path=str(workspace),
            predetermined_plan=f"Repair {TARGET}", predetermined_design="",
            predetermined_architect_files=[TARGET],
            approval_callback=AsyncMock(return_value=True)))
    run.workspace, run.config = workspace, cfg
    return run
