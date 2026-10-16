"""Kernel characterization scenarios and their golden evidence forms (full modularization E1).

Each scenario drives ONE terminal path of the real kernel (``WorkflowController`` -> ``WorkflowEngine
.run_generation_workflow`` -> ``attempt.run_attempt``) with only the model scripted, through the existing
harnesses: ``tests/state_machine/_scripted_run.py`` (legacy pipeline, real gates on a real git workspace),
``tests/_enforce_unit_harness.py`` (structured enforce loop; toolchain gates stubbed) and
``tests/_milestone_proof_harness.py`` (milestone DAG; model work replaced, real commits). A scenario returns
the observable outcome as a ``capture()`` form:

- ``evidence``: every attempt-evidence store the run wrote, as the ORDERED record kinds with ``attempt_number``,
  ``phase``, ``role``, ``unit_kind``, ``provenance``, the payload's key set and a digest of the normalized
  payload, and each named content digest (never raw content: model text, prompts and paths are digests only);
- ``run_records``: every ``RunRecord`` of the workspace, all fields, normalized;
- ``run_events``: the typed run events of each traces.db row (kind, attempt, source, authority, digest of details);
- ``result``: the ``WorkflowResult`` fields (controller paths) or the ``run_generation_workflow`` dict (direct
  path) / ``run_milestones`` dict (milestone paths), with free text replaced by digests.

Normalization replaces ONLY values MEASURED to vary between two identical runs (two runs in different tmp
directories, under different PYTHONHASHSEEDs): run ids, transaction/uuid hex ids, wall-clock timestamps, the tmp
workspace/state paths and the identities derived from them (workspace identity), and durations/elapsed seconds.
Each distinct volatile value becomes a token numbered by first appearance (``<run:1>``, ``<run:2>``) so
equality BETWEEN fields stays pinned. Git commit dates are fixed through the environment, so commit ids are
real values, not tokens. Content whose bytes embed a volatile value is re-digested after the same
substitution (blob text through ``EvidenceRun.blob``), so the digest stays sensitive and stable.
"""
from __future__ import annotations

import asyncio
import dataclasses
import enum
import hashlib
import json
import os
import re
import sqlite3
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional
from unittest.mock import MagicMock

# Imported at module import, never first inside a scenario: a kernel module first imported while a test has patched
# one of its dependencies would bind the patch object permanently (MEASURED in E1: workflow.py imported for the
# first time under a patched ``attempt.run_attempt`` bound the wrapper as its own ``run_attempt``).
import _enforce_unit_harness
import _milestone_proof_harness
import _scripted_run

import kriya.workflow.workflow_controller

PRE_IMPORTED = (_enforce_unit_harness, _milestone_proof_harness, _scripted_run, kriya.workflow.workflow_controller)

GOLDEN_DIR = Path(__file__).resolve().parents[1] / "golden" / "kernel"
WRITE_ENV = "KRIYA_WRITE_KERNEL_GOLDEN"   # a directory: the scenario forms are WRITTEN there, never compared
# Diagnosis aid for a failing comparison: the normalized content behind every content digest is written there
# (``<scenario>.content.json``; local only, never committed: it holds scripted model text and prompts).
CONTENT_ENV = "KRIYA_KERNEL_GOLDEN_CONTENT"

# Fixed git identity and dates: the workspace's base commit id is then a pure function of its content.
GIT_ENV = {
    "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.com", "GIT_COMMITTER_NAME": "t",
    "GIT_COMMITTER_EMAIL": "t@example.com", "GIT_AUTHOR_DATE": "2026-01-01T00:00:00+0000",
    "GIT_COMMITTER_DATE": "2026-01-01T00:00:00+0000",
}

# ----------------------------------------------------------------------------- normalization

_RUN_ID = re.compile(r"\d{8}T\d{6}-[0-9a-f]{8}")
_ISO_TIME = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:[+-]\d{2}:\d{2}|Z)?")
_HEX32 = re.compile(r"(?<![0-9a-f])[0-9a-f]{32}(?![0-9a-f])")
# Keys whose values are MEASURED to vary between two identical runs (wall clocks and durations); their values become
# "<clock>". Measured by generating every scenario twice in different tmp directories under different PYTHONHASHSEEDs
# and diffing every leaf of the forms and of the content behind every content digest (ISO timestamps are replaced
# wherever they occur by _ISO_TIME).
CLOCK_KEYS = frozenset({
    "created_at", "duration_seconds", "wall_seconds", "planner_wall_seconds", "architect_wall_seconds",
    "developer_wall_seconds", "reviewer_wall_seconds", "total_wall_seconds", "elapsed_seconds", "latency_seconds",
    "started_at_unix", "updated_at_unix",
})
# Byte/size counts of gate output MEASURED to vary with the tmp path length (pytest prints its rootdir) and with the
# toolchain's timing text; their values become "<size>".
# A retry prompt quotes that output, so its token estimate (request_tokens) and its segments' sizes vary the same way.
SIZE_KEYS = frozenset({"output_bytes", "request_tokens"})
# Decimal seconds inside free text (a budget message names the measured remaining time: "with 0.6s remaining").
_TEXT_SECONDS = re.compile(r"(?<![0-9.])\d+\.\d+s\b")
_TEXT_REMAINING = re.compile(r"\b((?:remaining|elapsed)_seconds)=[0-9.]+")
# A commit transaction id is "<run id>-<12 random hex>" (stage files are named after it).
_TRANSACTION = re.compile(r"(<run:\d+>-)([0-9a-f]{12})(?![0-9a-f])")
# Digests MEASURED to vary because Kriya computes them over gate output that embeds the toolchain's timings (the
# evidence fingerprint, the retry progress vector, the junit report file, a prompt segment): replaced by "<vdigest>".
# Not numbered: even their equality pattern is MEASURED to vary (two attempts' pytest timings sometimes coincide).
VOLATILE_DIGEST_KEYS = frozenset({"fingerprint_hash", "last_vector_digest"})
# The real pytest gate's own output (OS_TOOLCHAIN, not Kriya): its header names the host platform, Python and plugin
# versions and its summary the elapsed time. It is reduced to what the kernel's decisions depend on: the collected
# count, the FAILED/ERROR lines and the outcome counts.
_PYTEST_HEADER = re.compile(r"=+ test session starts =+\n")
_PYTEST_SUMMARY = re.compile(r"=+ ([^=\n]*?) in [0-9.]+s(?: \([0-9:]+\))? =+")
_JUNIT = re.compile(r"<\?xml[^>]*\?>\s*<testsuites.*?</testsuites>", re.S)
_JUNIT_CASE = re.compile(r'<testcase [^>]*?classname="([^"]*)" name="([^"]*)"[^>]*?(/>|>(.*?)</testcase>)', re.S)


def _reduce_pytest(text: str) -> str:
    out, position = [], 0
    for header in _PYTEST_HEADER.finditer(text):
        if header.start() < position:
            continue
        summary = _PYTEST_SUMMARY.search(text, header.end())
        end = summary.end() if summary else len(text)
        block = text[header.end():end]
        collected = re.search(r"collected (\d+) items?", block)
        failures = sorted(set(re.findall(r"^(?:FAILED|ERROR) [^\n]*", block, re.M)))
        out.append(text[position:header.start()])
        out.append(f"<pytest collected={collected.group(1) if collected else '?'} "
                   f"outcome={summary.group(1) if summary else '?'} {failures}>")
        position = end
    out.append(text[position:])
    return "".join(out)


def _reduce_junit(text: str) -> str:
    def cases(match):
        found = [(cls, name, "failure" if "<failure" in (body or "") else "error" if "<error" in (body or "")
                  else "skipped" if "<skipped" in (body or "") else "passed")
                 for cls, name, _tail, body in _JUNIT_CASE.findall(match.group(0))]
        return f"<junit {found}>"
    return _JUNIT.sub(cases, text)


class Normalizer:
    """Replaces volatile values by first-appearance tokens; one instance per scenario capture."""

    def __init__(self, literals: Mapping[str, str], volatile_keys: Iterable[str] = ()):
        self._volatile_keys = VOLATILE_DIGEST_KEYS | frozenset(volatile_keys)
        # literal value -> token prefix (longest first so a path is replaced before its parent)
        self._literals = sorted(((value, prefix) for value, prefix in literals.items() if value),
                                key=lambda item: -len(item[0]))
        self._tokens: Dict[str, str] = {}
        self._counts: Dict[str, int] = {}
        self.contents: List[Dict[str, Any]] = []

    def _token(self, prefix: str, value: str) -> str:
        key = f"{prefix}\0{value}"
        if key not in self._tokens:
            self._counts[prefix] = self._counts.get(prefix, 0) + 1
            self._tokens[key] = f"<{prefix}:{self._counts[prefix]}>"
        return self._tokens[key]

    def text(self, value: str) -> str:
        if "test session starts" in value:
            value = _reduce_pytest(value)
        if "<testsuites" in value:
            value = _reduce_junit(value)
        for literal, prefix in self._literals:
            if literal in value:
                value = value.replace(literal, self._token(prefix, literal))
        value = _RUN_ID.sub(lambda m: self._token("run", m.group(0)), value)
        value = _ISO_TIME.sub("<time>", value)
        value = _TEXT_SECONDS.sub("<t>s", value)
        value = _TEXT_REMAINING.sub(lambda m: f"{m.group(1)}=<t>", value)
        value = _TRANSACTION.sub(lambda m: m.group(1) + self._token("txn", m.group(2)), value)
        return _HEX32.sub(lambda m: self._token("hex", m.group(0)), value)

    def trace(self, value: str) -> str:
        """A traces.db row id (8 random hex characters, or a run id)."""
        return self.text(value) if _RUN_ID.fullmatch(value) else self._token("trace", value)

    def content(self, data: bytes) -> str:
        """A content blob: JSON content is normalized as a structure (its clock fields included), other text as text."""
        text = data.decode("utf-8", "replace")
        try:
            parsed = json.loads(text)
        except ValueError:
            return self.text(text)
        return json.dumps(self.value(parsed), sort_keys=True, separators=(",", ":"), ensure_ascii=False)

    def value(self, value: Any, key: str = "") -> Any:
        if key in CLOCK_KEYS and isinstance(value, (int, float, str)) and not isinstance(value, bool):
            return "<clock>"
        if key in SIZE_KEYS and isinstance(value, int) and not isinstance(value, bool):
            return "<size>"
        if key in self._volatile_keys and isinstance(value, str):
            return "<vdigest>"
        if isinstance(value, Mapping):
            volatile = set()
            if "changed_dimensions" in value:   # the retry progress vector: its digest covers the gate output
                volatile.add("digest")
            if str(value.get("path", "")).endswith(".xml"):   # a junit report file: its bytes carry timings
                volatile.add("sha256")
            if {"offset", "outcome", "chars"} <= set(value):   # a prompt segment: a retry prompt quotes the gate output
                volatile.add("digest")                          # (its exact text stays pinned by the request blob)
                sized = {"chars", "offset"}
            else:
                sized = set()
            normalized = [(self.text(str(k)), ("<vdigest>" if k in volatile and isinstance(v, str)
                                               else "<size>" if k in sized and isinstance(v, int)
                                               else self.value(v, str(k))))
                          for k, v in sorted(value.items(), key=lambda kv: str(kv[0]))]
            return dict(sorted(normalized))
        if isinstance(value, (list, tuple)):
            return [self.value(item) for item in value]
        if isinstance(value, enum.Enum):
            return self.value(value.value)
        if dataclasses.is_dataclass(value) and not isinstance(value, type):
            return self.value({f.name: getattr(value, f.name) for f in dataclasses.fields(value)})
        if hasattr(value, "model_dump"):
            return self.value(value.model_dump(mode="json"))
        if isinstance(value, str):
            return self.text(value)
        if isinstance(value, float):
            return round(value, 6)
        if value is None or isinstance(value, (bool, int)):
            return value
        return self.text(f"<{type(value).__name__}>")


def digest(data: Any) -> str:
    raw = data if isinstance(data, bytes) else json.dumps(data, sort_keys=True, separators=(",", ":"),
                                                          ensure_ascii=False, default=str).encode("utf-8")
    return "sha256:" + hashlib.sha256(raw).hexdigest()[:16]


_CODE = re.compile(r"^[A-Za-z0-9_.:/<>\-]{0,80}$")


def redact(value: Any, norm: Normalizer, key: str = "") -> Any:
    """Normalized structure with every free-text string (model text, prompts, messages) replaced by its digest;
    identifier-like strings (codes, enum values, relative file names, tokens) stay readable."""
    value = norm.value(value, key)

    def walk(item: Any) -> Any:
        if isinstance(item, dict):
            return {k: walk(v) for k, v in item.items()}
        if isinstance(item, list):
            return [walk(v) for v in item]
        if isinstance(item, str) and not _CODE.match(item):
            return {"text": digest(item.encode("utf-8")), "chars": len(item)}
        return item
    return walk(value)


# ----------------------------------------------------------------------------- capture

def _evidence(state_dir: str, norm: Normalizer) -> List[Dict[str, Any]]:
    from kriya.core.attempt_evidence import reader

    stores = []
    for run_id in reader.list_runs(state_dir):
        run = reader.open_run(state_dir, run_id)
        records = list(run.records())
        stores.append((records[0].get("t_mono_ms", 0) if records else 0, records[0].get("t_wall", "") if records else "",
                       run_id, run, records))
    out = []
    for _mono, _wall, run_id, run, records in sorted(stores, key=lambda s: (s[1], s[0], s[2])):
        rows = []
        for record in records:
            payload = norm.value(record.get("payload") or {})
            norm.contents.append({"seq": record.get("seq"), "kind": record["kind"], "name": "<payload>",
                                  "text": json.dumps(payload, sort_keys=True)})
            row = {
                "kind": record["kind"], "attempt": record.get("attempt_number"), "phase": record.get("phase"),
                "role": record.get("role"), "unit_kind": record.get("unit_kind"), "provenance": record.get("provenance"),
                "payload_keys": sorted((record.get("payload") or {}).keys()), "payload": digest(payload),
            }
            if record["kind"] == "mirror.event":
                row["event"] = (record.get("payload") or {}).get("kind")
            names = {}
            blobs = record.get("blobs") or {}
            for name, entry in sorted((record.get("content_digests") or {}).items()):
                ref = blobs.get(name)
                if ref:  # re-digest the exact blob after the same volatile-value substitution
                    text = norm.content(run.blob(ref))
                    names[name] = digest(text.encode("utf-8"))
                    norm.contents.append({"seq": record.get("seq"), "kind": record["kind"], "name": name, "text": text})
                else:
                    names[name] = norm.text(str(entry.get("digest") if isinstance(entry, Mapping) else entry))
            if names:
                row["content"] = names
            rows.append(row)
        seal = run.seal()
        out.append({"run": norm.text(run_id), "records": rows,
                    "sealed": seal is not None, "verification": run.verify().status})
    return out


def _run_records(workspace: str, norm: Normalizer) -> List[Dict[str, Any]]:
    from kriya.control.persistence import list_run_records

    records = sorted(list_run_records(workspace), key=lambda r: (r.created_at, r.run_id))
    return [norm.value(dataclasses.asdict(record)) for record in records]


def _run_events(cfg, norm: Normalizer) -> List[Dict[str, Any]]:
    from kriya.core.state_paths import trace_db_path

    path = trace_db_path(cfg)
    if not os.path.isfile(path):
        return []
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    try:
        rows = [dict(row) for row in conn.execute("SELECT * FROM runs ORDER BY timestamp, rowid")]
    finally:
        conn.close()
    out = []
    for row in rows:
        events = json.loads(row.get("run_events") or "[]")
        out.append({
            "run": norm.trace(str(row.get("run_id") or "")),
            "status": row.get("status"),
            "events": [{"kind": e.get("kind"), "attempt": e.get("attempt"), "source": e.get("source"),
                        "authority": e.get("authority"), "details": digest(norm.value(e.get("details") or {}))}
                       for e in events],
        })
    return out


def workflow_result_form(result: Any, norm: Normalizer) -> Dict[str, Any]:
    """Every WorkflowResult field; the legacy dict key by key (free text as digests)."""
    from kriya.workflow.workflow_types import WorkflowResult

    if isinstance(result, WorkflowResult):
        return {
            "type": "WorkflowResult",
            "fields": [f.name for f in dataclasses.fields(result)],
            "run_id": norm.text(result.run_id),
            "control_state": redact(result.control_state, norm),
            "route": redact(result.route, norm),
            "legacy_result": legacy_result_form(result.legacy_result, norm),
            "subtask_results": redact(list(result.subtask_results), norm),
            "decisions": redact(list(result.decisions), norm),
            "verification_report": redact(result.verification_report, norm),
        }
    return {"type": type(result).__name__, "legacy_result": legacy_result_form(result, norm)}


def legacy_result_form(result: Optional[Mapping[str, Any]], norm: Normalizer) -> Any:
    if result is None:
        return None
    return {"keys": sorted(result), "values": redact(dict(result), norm)}


@dataclasses.dataclass
class Outcome:
    result: Any
    workspace: str
    cfg: Any = None
    extra: Dict[str, Any] = dataclasses.field(default_factory=dict)
    # Keys MEASURED to be volatile in THIS scenario only (e.g. a config fingerprint over a config that names tmp paths).
    volatile_keys: tuple = ()
    # Values MEASURED to be volatile in THIS scenario only, replaced like the paths (value -> token prefix): an early
    # exit's result run_id is the 8-hex traces.db row id, which the run_events form tokenizes as "trace".
    literals: Dict[str, str] = dataclasses.field(default_factory=dict)


# Every scenario's terminal path, as the golden header states it (test_sm_kernel_golden checks each golden agrees).
TERMINAL_PATHS: Dict[str, str] = {
    "direct_success": "success (engine entry, no controller)",
    "legacy_success": "success",
    "legacy_fallback_success": "fallback transition then success",
    "legacy_gates_exhausted": "gate failed: quality_gates_exhausted",
    "legacy_repeated_failure_exhausted": "gate failed: quality_gates_exhausted (repeated identical failure)",
    "legacy_no_progress": "stopped early: no_progress",
    "legacy_budget_stop": "stopped early: generation_budget_exhausted (before any Developer call)",
    "legacy_final_review_refused": "final review refused after commit: final_review_refused",
    "legacy_approval_refused": "approval refused: human_rejected (no failure_category in the result dict)",
    "legacy_resume_after_failure": "resume: a failed run, then resume=True success",
    "enforce_success_with_verification_unit": "structured enforce success: one editor + one verification-only unit, HITL approval granted",
    "enforce_approval_refused": "structured enforce: HITL approval refused",
    "enforce_plan_invalid": "plan invalid: PLANNING_ERROR after structured plan repair exhausted",
    "enforce_verification_only_failed": "verification-only unit failed with no change possible (LR-R1-P4)",
    "milestone_chain_completed": "milestone DAG success (FakeEngine; real commits)",
    "milestone_rerun_reuses_completed": "milestone rerun: completed milestones reused (MATCH)",
    "milestone_resume_after_recovered_crash": "milestone resume after a crash mid-commit and --complete-partial recovery",
    # E3 characterization (stages 0-3), before the stages left run_generation_workflow
    "legacy_knowledge_gap": "stage 0 stop: knowledge_gap (unconfirmed KnowledgeGuard gap, before any model call)",
    "legacy_knowledge_gap_confirmed": "stage 0 gap confirmed: the safety constraints reach the prompts, success",
    "legacy_stage_context_success": "stage 1 context: a skill rule, a Graph RAG context and a learned reference reach the prompts, success",
    "legacy_planner_output_incomplete": "stage 2 stop: planner_output_incomplete (empty plan)",
    "legacy_planner_unauthorized_path": "stage 2 stop: planner_output_unauthorized_path (a planned path outside the workspace)",
    "legacy_planner_schema_invalid": "stage 2 stop: planner_output_schema_invalid after the bounded structured-plan repairs",
    "legacy_architect_file_list_rejected": "stage 3 stop: architect_file_list_rejected (every file-list entry escapes)",
    "legacy_stage2a_dependency_rejected": "stage 2A: a new design dependency rejected by the human raises ValueError",
}
GENERATED_FROM = "4eaf610"
# Scenarios added after E1 name the commit their golden was written from (every E1 golden stays GENERATED_FROM).
GENERATED_FROM_BY_SCENARIO: Dict[str, str] = {
    name: "bd3c6d8" for name in (
        "legacy_knowledge_gap", "legacy_knowledge_gap_confirmed", "legacy_stage_context_success",
        "legacy_planner_output_incomplete", "legacy_planner_unauthorized_path", "legacy_planner_schema_invalid",
        "legacy_architect_file_list_rejected", "legacy_stage2a_dependency_rejected")
}


def generated_from(name: str) -> str:
    return GENERATED_FROM_BY_SCENARIO.get(name, GENERATED_FROM)


def capture(outcome: Outcome, tmp_path: Path, name: str = "") -> Dict[str, Any]:
    from kriya.context.workspace_identity import workspace_identity
    from kriya.core.state_paths import ENV_STATE_DIR

    state_dir = os.environ[ENV_STATE_DIR]
    literals = {
        os.path.realpath(outcome.workspace): "workspace", outcome.workspace: "workspace",
        workspace_identity(outcome.workspace): "workspace_id",
        os.path.realpath(state_dir): "state_dir", state_dir: "state_dir",
        os.path.realpath(str(tmp_path)): "tmp", str(tmp_path): "tmp",
        **outcome.literals,
    }
    norm = Normalizer(literals, outcome.volatile_keys)
    doc = (SCENARIOS[name].__doc__ or "").strip() if name in SCENARIOS else ""
    form = {
        "_scenario": {
            "name": name, "terminal_path": TERMINAL_PATHS.get(name, ""), "doc": " ".join(doc.split()),
            "generated_from": generated_from(name),
            "normalized": "run ids, uuid/transaction ids, ISO times, tmp/state/workspace paths and workspace identity, "
                          "trace row ids -> first-appearance tokens; CLOCK_KEYS -> <clock>; timing-derived digests "
                          "(VOLATILE_DIGEST_KEYS, progress vector, junit file, prompt segments) -> <vdigest>; pytest "
                          "and junit gate output reduced to collected/outcome/FAILED lines; free text -> digests",
        },
        "result": workflow_result_form(outcome.result, norm),
        "run_records": _run_records(outcome.workspace, norm),
        "evidence": _evidence(state_dir, norm),
        "run_events": _run_events(outcome.cfg, norm) if outcome.cfg is not None else [],
    }
    if outcome.extra:
        form["extra"] = redact(outcome.extra, norm)
    form_contents[id(form)] = norm.contents
    return form


form_contents: Dict[int, List[Dict[str, Any]]] = {}


def render(form: Mapping[str, Any]) -> str:
    return json.dumps(form, indent=1, sort_keys=True, ensure_ascii=False) + "\n"


# ----------------------------------------------------------------------------- scripted legacy scenarios

BASE = "def add(a, b):\n    return a + b\n\n\ndef sub(a, b):\n    return a - b\n"
GOOD = BASE + "\n\ndef mul(a, b):\n    return a * b\n"
TESTS = ("from calc import add, sub\n\n\ndef test_add():\n    assert add(1, 2) == 3\n\n\n"
         "def test_sub():\n    assert sub(3, 2) == 1\n")
GOAL = "Add a function mul(a, b) returning a * b to calc.py."
BROKEN = BASE.replace("return a + b", 'raise ValueError("add unavailable")') + "\n\ndef mul(a, b):\n    return a * b\n"
REVIEWER = "You are the Kriya Reviewer Agent"


def _oscillating(n: int) -> str:
    if n % 2:
        add_body, sub_body = 'raise ValueError("add unavailable")', "return a - b"
    else:
        add_body, sub_body = "return a + b", 'raise KeyError("sub unavailable")'
    return (f"# attempt {n}\ndef add(a, b):\n    {add_body}\n\n\ndef sub(a, b):\n    {sub_body}\n\n\n"
            "def mul(a, b):\n    return a * b\n")


def _scripted(tmp_path: Path, developer: Callable[[str, int, str], str], *, chain: bool = True,
              configure: Optional[Callable[[Any], None]] = None, reviewer_error: bool = False,
              approve: bool = True, controller: bool = True, run_kwargs: Optional[Dict[str, Any]] = None,
              workspace: Optional[str] = None) -> Outcome:
    from _scripted_run import engine, git_workspace, script

    if workspace is None:
        workspace = git_workspace(tmp_path, {"calc.py": BASE, "test_calc.py": TESTS})
    eng, llm, cfg = engine(chain=chain, configure=configure)
    record = script(llm, plan="Step 1: add mul", target="calc.py", developer=developer)
    if reviewer_error:
        scripted = llm.complete

        async def complete(system_prompt, user_prompt, *args, **kwargs):
            if (system_prompt or "").startswith(REVIEWER):
                raise RuntimeError("scripted reviewer backend unavailable")
            return await scripted(system_prompt, user_prompt, *args, **kwargs)
        llm.complete = complete
    kwargs = dict(goal=GOAL, workspace_path=workspace, approval_callback=MagicMock(return_value=approve),
                  **(run_kwargs or {}))
    if controller:
        from kriya.workflow.workflow_controller import WorkflowController

        result = asyncio.run(WorkflowController(eng).execute(migration_mode="legacy", **kwargs))
    else:
        result = asyncio.run(eng.run_generation_workflow(**kwargs))
    return Outcome(result=result, workspace=workspace, cfg=cfg, extra={"developer_calls": record.developer_calls})


def _tiny_budget(cfg) -> None:
    cfg.autonomy.generation_time_budget_seconds = 1


SCENARIOS: Dict[str, Callable[[Path], Outcome]] = {}


def scenario(name: str):
    def register(fn):
        SCENARIOS[name] = fn
        return fn
    return register


@scenario("direct_success")
def direct_success(tmp_path: Path) -> Outcome:
    """The _scripted_run entry exactly as the state-machine tier uses it: the engine called directly."""
    return _scripted(tmp_path, lambda model, n, text: GOOD, controller=False)


@scenario("legacy_success")
def legacy_success(tmp_path: Path) -> Outcome:
    return _scripted(tmp_path, lambda model, n, text: GOOD)


@scenario("legacy_fallback_success")
def legacy_fallback_success(tmp_path: Path) -> Outcome:
    from _scripted_run import FALLBACK
    return _scripted(tmp_path, lambda model, n, text: GOOD if model == FALLBACK else _oscillating(n))


@scenario("legacy_gates_exhausted")
def legacy_gates_exhausted(tmp_path: Path) -> Outcome:
    return _scripted(tmp_path, lambda model, n, text: _oscillating(n), chain=False)


@scenario("legacy_budget_stop")
def legacy_budget_stop(tmp_path: Path) -> Outcome:
    return _scripted(tmp_path, lambda model, n, text: GOOD, configure=_tiny_budget)


@scenario("legacy_final_review_refused")
def legacy_final_review_refused(tmp_path: Path) -> Outcome:
    return _scripted(tmp_path, lambda model, n, text: GOOD, reviewer_error=True)


@scenario("legacy_approval_refused")
def legacy_approval_refused(tmp_path: Path) -> Outcome:
    return _scripted(tmp_path, lambda model, n, text: GOOD, approve=False)


@scenario("legacy_repeated_failure_exhausted")
def legacy_repeated_failure_exhausted(tmp_path: Path) -> Outcome:
    """Identical failing bytes every attempt through the REAL pytest gate. MEASURED: attempts 3, 4, 6, 7 are
    REPEATED_VECTOR with no changed dimension, but each strategy transition (full_set -> targeted -> full_set) changes
    the action dimension and resets the consecutive no-progress count, so the full-set budget ends the run
    (quality_gates_exhausted, 7 Developer calls) before the PRD-026 bound; the constant-output twin
    (legacy_no_progress) reaches no_progress after 4. Characterized as is (by design of PRD-026, not a defect)."""
    return _scripted(tmp_path, lambda model, n, text: BROKEN, chain=False)


@scenario("legacy_no_progress")
def legacy_no_progress(tmp_path: Path) -> Outcome:
    """The PRD-026 terminal: identical candidates failing with an identical (constant) compile output."""
    from unittest.mock import patch

    constant = {"success": False, "output": "Build error: dependency resolution failed."}
    with patch("kriya.tools.validate.PolymorphicValidator.run_compile_check", return_value=constant):
        return _scripted(tmp_path, lambda model, n, text: BROKEN, chain=False)


def _role_script(llm, developer: Callable[[str, int, str], str], record: List[str]) -> None:
    """The _scripted_run answers dispatched by ROLE (system prompt) instead of call index, for runs whose call
    sequence differs (a resumed run skips the planner and architect it restores from checkpoints)."""
    from _protocol_responses import sentinel, wants_structured
    from _scripted_run import PRIMARY

    async def complete(system_prompt, user_prompt, *args, **kwargs):
        system_prompt = system_prompt or ""
        text = f"{system_prompt}\n{user_prompt}"
        if system_prompt.startswith("You are the Kriya Planner Agent"):
            return "Step 1: add mul"
        if system_prompt.startswith("You are the Kriya Architect Agent"):
            return json.dumps({"files": ["calc.py"]})
        if not system_prompt.startswith(("You are the Kriya Developer Agent", "You are the Kriya File List Planner")):
            return json.dumps({"verdict": "PASS", "requirements": []})
        model = kwargs.get("model_override") or PRIMARY
        record.append(model)
        content = developer(model, len(record), text)
        if wants_structured(system_prompt):
            return sentinel("calc.py", analysis="repair calc.py", content=content)
        if "FILE CONTENT:" in text:
            return f"FIX ANALYSIS: repair calc.py\nFILE CONTENT:\n{content}"
        return json.dumps([{"filepath": "calc.py", "content": content}])
    llm.complete = complete


@scenario("legacy_resume_after_failure")
def legacy_resume_after_failure(tmp_path: Path) -> Outcome:
    """Run 1 exhausts its gates and leaves its stage checkpoints; run 2 (resume=True) restores them and succeeds."""
    from _scripted_run import engine, git_workspace

    from kriya.workflow.workflow_controller import WorkflowController

    workspace = git_workspace(tmp_path, {"calc.py": BASE, "test_calc.py": TESTS})
    calls: Dict[str, List[str]] = {"first": [], "resumed": []}
    results = []
    for phase, developer, resume in (("first", lambda m, n, t: _oscillating(n), False),
                                     ("resumed", lambda m, n, t: GOOD, True)):
        eng, llm, cfg = engine(chain=False)
        _role_script(llm, developer, calls[phase])
        results.append(asyncio.run(WorkflowController(eng).execute(
            goal=GOAL, workspace_path=workspace, approval_callback=MagicMock(return_value=True),
            migration_mode="legacy", resume=resume)))
    return Outcome(result=results[-1], workspace=workspace, cfg=cfg,
                   extra={"developer_calls": calls, "first_result": workflow_result_form_marker(results[0])})


def workflow_result_form_marker(result: Any) -> Dict[str, Any]:
    legacy = getattr(result, "legacy_result", None) or {}
    return {"quality_gates_passed": legacy.get("quality_gates_passed"), "failure_category": legacy.get("failure_category")}


# ----------------------------------------------------------------------------- structured enforce scenarios

# Both enforce harnesses set paths.skills/paths.memory under tmp_path, so the RunRecord's config fingerprint (over the
# whole config) is MEASURED to differ per tmp directory.
ENFORCE_VOLATILE = ("effective_config_fingerprint",)
ENFORCE_SERVICE = "shop/service.py"
ENFORCE_BASE = "def page_size():\n    return 5\n"
ENFORCE_PRODUCED = "PAGE_SIZE = 5\n\n\ndef page_size():\n    return PAGE_SIZE\n"


def _enforce_plan(*, verification_unit: bool, invalid: bool = False):
    from kriya.workflow.plan_schema import EngineeringPlan

    subtasks = [{"id": "s1", "description": f"edit {ENFORCE_SERVICE}", "execution_method": "model",
                 "planned_files": [{"path": ENFORCE_SERVICE, "action": "modify"}],
                 "depends_on": ["missing-unit"] if invalid else [], "provides": ["p_s1"],
                 "relevant_global_invariant_ids": ["gi1"],
                 "verification": [{"type": "tool", "tool_name": "compile", "description": "compile"}]}]
    if verification_unit:
        subtasks.append({"id": "sv", "description": "verify the application", "execution_method": "model",
                         "execution_role": "verification", "planned_files": [], "depends_on": ["s1"],
                         "requires": ["p_s1"], "relevant_global_invariant_ids": ["gi1"],
                         "verification": [{"type": "tool", "tool_name": "compile", "description": "build"}]})
    return EngineeringPlan.model_validate({"plan_id": "p", "kind": "task", "global_invariants": [{"id": "gi1", "statement": "x"}],
                                           "acceptance_criteria": [], "subtasks": subtasks})


def _enforce(tmp_path: Path, **kwargs) -> Outcome:
    import _enforce_unit_harness as harness

    from kriya.config import AppConfig

    run = harness.run_enforce(tmp_path, units={"s1": (ENFORCE_SERVICE, ENFORCE_PRODUCED)},
                              base_files={ENFORCE_SERVICE: ENFORCE_BASE, "shop/__init__.py": ""}, **kwargs)
    return Outcome(result=run.result, workspace=str(run.workspace), cfg=AppConfig(), volatile_keys=ENFORCE_VOLATILE,
                   extra={"approvals": [[unit, files] for unit, files, _reason in run.approvals],
                          "gate_outcome_types": [o.get("type") for o in run.gate_outcomes],
                          "event_kinds": [e.kind for e in run.events]})


@scenario("enforce_success_with_verification_unit")
def enforce_success_with_verification_unit(tmp_path: Path) -> Outcome:
    return _enforce(tmp_path, plan=_enforce_plan(verification_unit=True))


@scenario("enforce_approval_refused")
def enforce_approval_refused(tmp_path: Path) -> Outcome:
    return _enforce(tmp_path, plan=_enforce_plan(verification_unit=False), answer=False)


@scenario("enforce_plan_invalid")
def enforce_plan_invalid(tmp_path: Path) -> Outcome:
    return _enforce(tmp_path, plan=_enforce_plan(verification_unit=False, invalid=True))


@scenario("enforce_verification_only_failed")
def enforce_verification_only_failed(tmp_path: Path) -> Outcome:
    """The LR-R1-P4 reproducer's run (tests/test_lr_r1_p4_verification_only_retry_reproducer.py::_enforce): a
    verification-only unit whose test gate fails with no mutation possible - one verification, no retry, owner
    reopened once, terminal no_progress."""
    from test_lr_r1_p4_verification_only_retry_reproducer import _enforce as p4_enforce

    from kriya.config import AppConfig

    workspace, events, result, model_calls, test_gate_runs = p4_enforce(tmp_path)
    return Outcome(result=result, workspace=str(workspace), cfg=AppConfig(), volatile_keys=ENFORCE_VOLATILE,
                   extra={"event_kinds": [e.kind for e in events], "test_gate_runs": list(test_gate_runs),
                          "model_calls": [[unit, first] for unit, first in model_calls]})


# ----------------------------------------------------------------------------- milestone scenarios

def _milestones(tmp_path: Path, drive: Callable[[Path], Any]) -> Outcome:
    import _milestone_proof_harness as mh

    workspace = mh._workspace(tmp_path)
    result, state = drive(workspace)
    return Outcome(result=result, workspace=str(workspace),
                   extra={"completed": list(state.completed_milestone_ids)})


@scenario("milestone_chain_completed")
def milestone_chain_completed(tmp_path: Path) -> Outcome:
    import _milestone_proof_harness as mh

    def drive(workspace):
        engine = mh.FakeEngine(mh.CHAIN, mh.CHAIN_OUTPUTS)
        result, state = mh._run(workspace, mh.CHAIN, engine)
        result = dict(result, engine_calls=list(engine.calls))
        return result, state
    return _milestones(tmp_path, drive)


@scenario("milestone_rerun_reuses_completed")
def milestone_rerun_reuses_completed(tmp_path: Path) -> Outcome:
    import _milestone_proof_harness as mh

    def drive(workspace):
        mh._run(workspace, mh.CHAIN, mh.FakeEngine(mh.CHAIN, mh.CHAIN_OUTPUTS))
        engine = mh.FakeEngine(mh.CHAIN, mh.CHAIN_OUTPUTS)
        result, state = mh._run(workspace, mh.CHAIN, engine)
        return dict(result, engine_calls=list(engine.calls)), state
    return _milestones(tmp_path, drive)


@scenario("milestone_resume_after_recovered_crash")
def milestone_resume_after_recovered_crash(tmp_path: Path) -> Outcome:
    """M2's commit killed between its two file replaces (a real child process, os._exit), the run recovered with
    --complete-partial, then the chain rerun: M2 is reused from the reconstructed completion."""
    import _milestone_proof_harness as mh

    from kriya.control.recovery import recover_workspace

    def drive(workspace):
        mh._crash_mid_commit(workspace, "stage2")
        report = recover_workspace(str(workspace), complete_partial=True)
        engine = mh.FakeEngine(mh.CHAIN, mh.MID_COMMIT_OUTPUTS)
        result, state = mh._run(workspace, mh.CHAIN, engine)
        return dict(result, engine_calls=list(engine.calls), recovery_rolled_forward=report.rolled_forward,
                    recovery_errors=list(report.errors)), state
    return _milestones(tmp_path, drive)


# ----------------------------------------------------------------------------- stages 0-3 (E3 characterization)
# Written before E3 moved KnowledgeGuard/analysis/planning/architecture out of run_generation_workflow, from the commit
# in GENERATED_FROM_BY_SCENARIO. Each run is ISOLATED from the repository's ./skills and ./memory (the E1 scenarios use
# the AppConfig defaults, so they read them) and runs the KnowledgeGuard offline: its verdict, the skill set and the
# graph context are then a function of the scenario alone.

STAGE_LIBRARY_GOAL = GOAL + " Use mylib==1.2.3 for it."   # a versioned library with no skill: an offline KnowledgeGuard gap
STAGE_SKILL = "calc-conventions"
STAGE_SKILL_RULE = "Name every arithmetic helper with a verb."
STAGE_GRAPH_CONTEXT = "\n\n=== Scripted Graph RAG Context ===\ncalc.py: def add(a, b), def sub(a, b)\n"
STAGE_REFERENCE = "Learned: calc helpers return plain numbers."


def _escaping_plan() -> str:
    """A complete structured plan whose one planned file escapes the workspace (PlannedFile path authority)."""
    structured = {
        "global_invariants": [{"id": "gi1", "statement": "keep calc.py small"}],
        "subtasks": [{"id": "s1", "description": "add mul", "execution_method": "model",
                      "execution_role": "implementation", "depends_on": [],
                      "planned_files": [{"path": "../outside/calc.py", "action": "modify"}],
                      "provides": ["mul"], "requires": [], "relevant_global_invariant_ids": ["gi1"],
                      "acceptance_criteria_ids": ["ac1"], "verification": []}],
        "acceptance_criteria": [{"id": "ac1", "description": "tests pass", "method": "judgment"}],
        "extension_points": [], "refactor_baseline": None,
    }
    return "Plan: add mul\n```json\n" + json.dumps(structured) + "\n```"


def _schema_invalid_plan() -> str:
    """A complete structured plan whose TOOL subtask names no tool_name (the PLANNER-ROBUST-001 G1 shape)."""
    structured = json.loads(_escaping_plan().split("```json\n", 1)[1].rsplit("\n```", 1)[0])
    structured["subtasks"][0]["planned_files"] = [{"path": "calc.py", "action": "modify"}]
    structured["subtasks"].append({
        "id": "s2", "description": "verify", "execution_method": "tool", "execution_role": "verification",
        "depends_on": ["s1"], "planned_files": [], "provides": [], "requires": ["mul"],
        "relevant_global_invariant_ids": ["gi1"], "acceptance_criteria_ids": ["ac1"],
        "verification": [{"type": "tool", "description": "run tests", "tool_name": "test"}]})
    return "Plan: add mul\n```json\n" + json.dumps(structured) + "\n```"


def _stage_role_script(llm, *, planner: Callable[[int], str], architect: Callable[[int], str]) -> Dict[str, List[str]]:
    """Answers by ROLE (system prompt): the Planner's and Architect's n-th answers, the Developer always GOOD (the
    production sentinel protocol), every other role a neutral PASS. Records each role's call count and each
    request's text (digested by the capture after normalization: the convention prompt the stages built reaches every
    role's request)."""
    from _protocol_responses import sentinel, wants_structured

    seen: Dict[str, List[str]] = {"planner": [], "architect": [], "developer": []}

    async def complete(system_prompt, user_prompt, *args, **kwargs):
        system_prompt = system_prompt or ""
        if system_prompt.startswith("You are the Kriya Planner Agent"):
            seen["planner"].append(user_prompt)
            return planner(len(seen["planner"]))
        if system_prompt.startswith("You are the Kriya Architect Agent"):
            seen["architect"].append(user_prompt)
            return architect(len(seen["architect"]))
        if not system_prompt.startswith(_scripted_run.DEVELOPER_PROMPTS):
            return json.dumps({"verdict": "PASS", "requirements": []})
        seen["developer"].append(user_prompt)
        if wants_structured(system_prompt):
            return sentinel("calc.py", analysis="add mul to calc.py", content=GOOD)
        return json.dumps([{"filepath": "calc.py", "content": GOOD}])
    llm.complete = complete
    return seen


def _stage_run(tmp_path: Path, *, goal: str = GOAL, planner: Callable[[int], str] = lambda n: "Step 1: add mul",
               architect: Callable[[int], str] = lambda n: json.dumps({"files": ["calc.py"]}),
               configure: Optional[Callable[[Any], None]] = None, approve: bool = True, skills: bool = False,
               run_kwargs: Optional[Dict[str, Any]] = None, patches: Iterable[Any] = ()) -> Outcome:
    """One controller (legacy) run of the scenario, isolated; ``extra`` records what the result dict and the E1 form do
    not show: the step callbacks, every traces.db row's status/failure_category/attempts, the stage checkpoints left in
    the workspace, and the scripted roles' call counts and prompt digests."""
    import contextlib

    from _scripted_run import engine, git_workspace

    from kriya.control.checkpoint import list_checkpoints
    from kriya.core.state_paths import trace_db_path
    from kriya.workflow.workflow_controller import WorkflowController

    workspace = tmp_path / "ws"
    workspace.mkdir()
    git_workspace(workspace, {"calc.py": BASE, "test_calc.py": TESTS})
    skills_dir = tmp_path / "skills"
    if skills:
        skill = skills_dir / STAGE_SKILL
        skill.mkdir(parents=True)
        (skill / "skill.yaml").write_text(f"name: {STAGE_SKILL}\ndescription: calc conventions\nsupported_versions: \"*\"\n"
                                          "verified: true\ntags:\n  - calc\n")
        (skill / "rules.txt").write_text(f"{STAGE_SKILL_RULE}\n")
        (skill / "instructions.md").write_text("Keep calc.py free of I/O.\n")

    def isolated(cfg):
        cfg.paths.skills = str(skills_dir)
        cfg.paths.memory = str(tmp_path / "memory")
        cfg.skills.load_global = False
        cfg.skills.load_cwd = False
        cfg.knowledge.offline_mode = True
        if configure is not None:
            configure(cfg)
    eng, llm, cfg = engine(chain=False, configure=isolated)
    seen = _stage_role_script(llm, planner=planner, architect=architect)
    steps: List[Any] = []
    kwargs = dict(goal=goal, workspace_path=str(workspace), approval_callback=MagicMock(return_value=approve),
                  step_callback=lambda name, text: steps.append([name, str(text)]),
                  migration_mode="legacy", **(run_kwargs or {}))
    with contextlib.ExitStack() as stack:
        for item in patches:
            stack.enter_context(item)
        try:
            result: Any = asyncio.run(WorkflowController(eng).execute(**kwargs))
        except Exception as error:   # a stage that raises (Stage 2A rejection): the exception IS the outcome
            result = {"raised": type(error).__name__, "message": str(error)}
    rows, trace_ids = [], {}
    if os.path.isfile(trace_db_path(cfg)):
        conn = sqlite3.connect(trace_db_path(cfg))
        conn.row_factory = sqlite3.Row
        try:
            for r in conn.execute("SELECT * FROM runs ORDER BY timestamp, rowid"):
                rows.append({"status": r["status"], "failure_category": r["failure_category"], "attempts": r["attempts"],
                             "files_modified": r["files_modified"]})
                trace_ids[str(r["run_id"])] = "trace"

        finally:
            conn.close()
    checkpoints = [{"stage": c.get("stage"), "keys": sorted(c)} for c in list_checkpoints(str(workspace))]
    # MEASURED volatile: the RunRecord's config fingerprint (the config names the tmp skills/memory directories) and
    # the 8-hex traces.db row id an early exit returns as its run_id.
    return Outcome(result=result, workspace=str(workspace), cfg=cfg, volatile_keys=("effective_config_fingerprint",),
                   literals=trace_ids,
                   extra={"steps": steps, "trace_rows": rows, "checkpoints": checkpoints,
                          "model_calls": {role: len(calls) for role, calls in seen.items()}, "prompts": seen})


@scenario("legacy_knowledge_gap")
def legacy_knowledge_gap(tmp_path: Path) -> Outcome:
    """Stage 0: an unconfirmed KnowledgeGuard gap (a versioned library with no skill, offline) stops the unit before
    any model call: status knowledge_gap, a knowledge_gap traces.db row, no checkpoint."""
    return _stage_run(tmp_path, goal=STAGE_LIBRARY_GOAL)


@scenario("legacy_knowledge_gap_confirmed")
def legacy_knowledge_gap_confirmed(tmp_path: Path) -> Outcome:
    """Stage 0/1: the same gap confirmed (knowledge_risk_confirmed=True) runs on to success; the KnowledgeGuard safety
    constraints are in the convention prompt every Planner/Architect/Developer request carries (prompt digests)."""
    return _stage_run(tmp_path, goal=STAGE_LIBRARY_GOAL, run_kwargs={"knowledge_risk_confirmed": True})


@scenario("legacy_stage_context_success")
def legacy_stage_context_success(tmp_path: Path) -> Outcome:
    """Stage 1: a relevant skill (its rule), a Graph RAG context (the retrieval scripted through the
    ``kriya.workflow.workflow.retrieve_graph_context`` patch point, the index file present) and a learned reference
    all reach the prompts (digests), and the run succeeds; the active_skills evidence names the skill."""
    from unittest.mock import AsyncMock, patch

    from kriya.workflow.graph_retrieval import GraphRetrievalResult

    retrieval = GraphRetrievalResult(matched=True, matched_files=["calc.py"], graph_rag_context=STAGE_GRAPH_CONTEXT,
                                     expansion_seed_reason="scripted", expansion_seed_files=["calc.py"])

    def index(cfg):
        os.makedirs(cfg.paths.memory, exist_ok=True)
        open(os.path.join(cfg.paths.memory, "vector_index.db"), "wb").close()
    return _stage_run(tmp_path, skills=True, configure=index,
                      run_kwargs={"reference_context": STAGE_REFERENCE},
                      patches=[patch("kriya.workflow.workflow.retrieve_graph_context", AsyncMock(return_value=retrieval))])


@scenario("legacy_planner_output_incomplete")
def legacy_planner_output_incomplete(tmp_path: Path) -> Outcome:
    """Stage 2: an empty Planner response is incomplete_truncated: planner_output_incomplete before the Architect, a
    plan checkpoint left behind."""
    return _stage_run(tmp_path, planner=lambda n: "")


@scenario("legacy_planner_unauthorized_path")
def legacy_planner_unauthorized_path(tmp_path: Path) -> Outcome:
    """Stage 2: a complete structured plan naming a path outside the workspace: planner_output_unauthorized_path, never
    repaired."""
    return _stage_run(tmp_path, planner=lambda n: _escaping_plan())


@scenario("legacy_planner_schema_invalid")
def legacy_planner_schema_invalid(tmp_path: Path) -> Outcome:
    """Stage 2: a schema-invalid structured plan, still invalid after every bounded repair:
    planner_output_schema_invalid with the repair events."""
    return _stage_run(tmp_path, planner=lambda n: _schema_invalid_plan())


@scenario("legacy_architect_file_list_rejected")
def legacy_architect_file_list_rejected(tmp_path: Path) -> Outcome:
    """Stage 3: every Architect file-list entry escapes the workspace: architect_file_list_rejected before any
    Developer call, a design checkpoint left behind."""
    return _stage_run(tmp_path, architect=lambda n: json.dumps({"files": ["../outside/evil.py"]}))


@scenario("legacy_stage2a_dependency_rejected")
def legacy_stage2a_dependency_rejected(tmp_path: Path) -> Outcome:
    """Stage 2A: the design names a library the goal did not (a new KnowledgeGuard gap) and the human rejects it: the
    run raises ValueError before any Developer call."""
    design = "Use mylib==1.2.3 in calc.py.\n```json\n" + json.dumps({"files": ["calc.py"]}) + "\n```"
    return _stage_run(tmp_path, architect=lambda n: design, approve=False)
