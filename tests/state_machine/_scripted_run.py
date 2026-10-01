"""Deterministic scripted-model harness for the state-machine tier.

Drives the real run_generation_workflow() against a real git workspace with
a scripted LLMClient: the Planner and Architect get fixed answers, every
non-Developer role a neutral PASS, and each Developer call is answered by
the ``developer`` callable (model, call index, prompt text) -> file content.
Only the model is scripted; every retry, recovery, fallback, gate and commit
decision is Kriya's own. Trajectories run in seconds and assert typed run
events from traces.db, never log wording.
"""
import asyncio
import json
import os
import sqlite3
import subprocess
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional
from unittest.mock import MagicMock

from _protocol_responses import sentinel, wants_structured

from kriya.config import AppConfig
from kriya.config.config import FallbackModelConfig
from kriya.core.kernel import Kernel
from kriya.core.llm import LLMClient
from kriya.core.state_paths import trace_db_path
from kriya.workflow.workflow import WorkflowEngine

PRIMARY = "primary-coder:30b"
FALLBACK = "fallback-coder:35b"
DEVELOPER_PROMPTS = ("You are the Kriya Developer Agent", "You are the Kriya File List Planner")


def git_workspace(root, files: Dict[str, str]) -> str:
    for name, content in files.items():
        (root / name).write_text(content)
    for argv in (["init", "-q"], ["config", "user.email", "t@example.com"], ["config", "user.name", "t"],
                 ["add", "-A"], ["commit", "-q", "-m", "initial"]):
        subprocess.run(["git", *argv], cwd=str(root), check=True)
    return str(root)


def engine(*, chain: bool = True, configure: Optional[Callable[[AppConfig], None]] = None):
    cfg = AppConfig()
    cfg.llm.model = PRIMARY
    cfg.autonomy.run_verification_enabled = False
    cfg.llm_chain = [FallbackModelConfig(model=FALLBACK, base_url=cfg.llm.base_url)] if chain else []
    if configure is not None:
        configure(cfg)
    llm = LLMClient(cfg)
    return WorkflowEngine(Kernel(config=cfg), llm), llm, cfg


@dataclass
class Script:
    """What the scripted model saw: each Developer call's model, in order."""
    developer_calls: List[str] = field(default_factory=list)


def script(llm, *, plan: str, target: str, developer: Callable[[str, int, str], str]) -> Script:
    record = Script()
    calls = {"n": 0}

    async def complete(system_prompt, user_prompt, *args, **kwargs):
        calls["n"] += 1
        text = f"{system_prompt}\n{user_prompt}"
        if calls["n"] == 1:
            return plan
        if calls["n"] == 2:
            return json.dumps({"files": [target]})
        if not system_prompt.startswith(DEVELOPER_PROMPTS):
            return json.dumps({"verdict": "PASS", "requirements": []})
        model = kwargs.get("model_override") or PRIMARY
        record.developer_calls.append(model)
        content = developer(model, len(record.developer_calls), text)
        if wants_structured(system_prompt):  # the production protocol: a sentinel FILE block
            return sentinel(target, analysis=f"repair {target}", content=content)
        if "FILE CONTENT:" in text:  # a legacy repair request: the marker response shape
            return f"FIX ANALYSIS: repair {target}\nFILE CONTENT:\n{content}"
        return json.dumps([{"filepath": target, "content": content}])

    llm.complete = complete
    return record


def run(eng, workspace: str, goal: str) -> dict:
    return asyncio.run(eng.run_generation_workflow(
        goal=goal, workspace_path=workspace, approval_callback=MagicMock(return_value=True),
    ))


def run_events(cfg) -> List[dict]:
    """The latest run's typed run events, from traces.db."""
    conn = sqlite3.connect(trace_db_path(cfg))
    conn.row_factory = sqlite3.Row
    row = dict(conn.execute("SELECT * FROM runs ORDER BY timestamp DESC LIMIT 1").fetchone())
    conn.close()
    return json.loads(row["run_events"] or "[]")


def kinds(events: List[dict], kind: str) -> List[dict]:
    return [event for event in events if event["kind"] == kind]


def read(workspace: str, name: str) -> str:
    with open(os.path.join(workspace, name)) as handle:
        return handle.read()
