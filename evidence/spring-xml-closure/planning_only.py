"""DIAGNOSTIC: the real `kriya generate` through structured planning (Planner
calls, bounded repair, normalization, validation) - stopped at the first
Developer call. Records every Planner call's admission, duration and usage.
usage: planning_only.py <out.json> <kriya args...>"""
import json
import sys
import time
from unittest.mock import patch

from kriya.agents.agent import PlannerAgent
from kriya.workflow.workflow import WorkflowEngine

OUT = sys.argv[1]
calls = []


class Stop(BaseException):
    pass


real_run = PlannerAgent.run


async def run(self, prompt, *args, **kwargs):
    started = time.monotonic()
    rec = {"prompt_chars": len(prompt)}
    try:
        text = await real_run(self, prompt, *args, **kwargs)
        rec.update(ok=True)
        return text
    except Exception as error:
        rec.update(ok=False, error=f"{type(error).__name__}: {str(error)[:300]}")
        raise
    finally:
        rec["seconds"] = round(time.monotonic() - started, 1)
        m = self.llm.last_call_metrics or {}
        rec.update(model=m.get("model"), prompt_tokens=m.get("prompt_tokens"),
                   completion_tokens=m.get("completion_tokens"), tokens_estimated=m.get("tokens_estimated"))
        calls.append(rec)


async def developer(self, *args, **kwargs):
    raise Stop()


from kriya.cli import main  # noqa: E402 - imported after the patch targets are defined

outcome = "unknown"
with patch.object(PlannerAgent, "run", run), patch.object(WorkflowEngine, "run_generation_workflow", developer):
    try:
        main.main(args=sys.argv[2:], standalone_mode=False)
        outcome = "returned"
    except Stop:
        outcome = "reached_developer"
    except SystemExit as e:
        outcome = f"exit {e.code}"
json.dump({"outcome": outcome, "planner_calls": calls}, open(OUT, "w"))
print(outcome, json.dumps(calls)[:400])
