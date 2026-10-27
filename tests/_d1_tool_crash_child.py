"""Child process of tests/test_d1_tool_crash.py: runs the REAL enforce loop over a one-TOOL-subtask plan and dies with os._exit(137) at the requested window (no finalizer, no
cleanup: a real kill). usage: _d1_tool_crash_child.py <workspace> <effects-log> <window>"""
import asyncio
import os
import pathlib
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from unittest.mock import AsyncMock, patch  # noqa: E402

import test_e12_enforce_characterization as T  # noqa: E402

from kriya.repository import atomic_write  # noqa: E402
from kriya.workflow.plan_schema import ExecutionMethod, Subtask  # noqa: E402
from kriya.workflow.workflow_types import SubtaskResult, SubtaskStatus  # noqa: E402

workspace, effects_log, window = pathlib.Path(sys.argv[1]), sys.argv[2], sys.argv[3]
state = {"armed": False}


def log(line):
    with open(effects_log, "a", encoding="utf-8") as handle:
        handle.write(line + "\n")


async def effect(**kwargs):
    if window == "before_effect":
        os._exit(137)                                  # durable STARTED on disk, effect not begun
    log("EFFECT_BEGIN")
    if window == "during_effect":
        os._exit(137)                                  # the effect is in flight
    log("EFFECT_DONE")
    return SubtaskResult(subtask_id="t1", status=SubtaskStatus.COMPLETED, execution_method="tool", tool_output="out")


real_save = __import__("kriya.control.persistence", fromlist=["save_control_state"]).save_control_state


def save_control_state(ws, cs, *, durable=False):
    if window == "after_durable_completed" and cs.subtask_states.get("t1") == "completed" and cs.subtask_states.get("s2") == "in_progress":
        os._exit(137)                                  # t1 is durably COMPLETED on disk; the run dies when the NEXT subtask starts
    if durable and cs.subtask_states.get("t1") == "completed":
        if window == "after_effect_before_completed":
            os._exit(137)                              # the effect finished, COMPLETED never written
        if window == "during_completed_persistence":
            state["armed"] = True                      # die inside the strict writer, after the temp file was written, before the replace
    return real_save(ws, cs, durable=durable)


real_fsync = os.fsync


def fsync(fd):
    if state["armed"]:
        os._exit(137)
    return real_fsync(fd)


async def main():
    tool = Subtask(id="t1", description="run a command", execution_method=ExecutionMethod.TOOL, tool_name="shell", tool_arguments={"command": "true"})
    plan = T._plan_of(tool, T._unit("s2", "b.py", depends_on=["t1"]))
    with patch(f"{T.C}.subtask_executor.execute", new=AsyncMock(side_effect=effect)), patch(f"{T.C}.save_control_state", new=save_control_state), \
            patch.object(os, "fsync", fsync), patch.object(atomic_write, "open", open, create=True):
        await T._enforce(T._enforce_engine(workspace), workspace, plan)


asyncio.run(main())
sys.exit(0)
