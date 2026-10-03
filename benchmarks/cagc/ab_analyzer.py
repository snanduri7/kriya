"""CAGC A/B evidence analyzer (KRIYA_CAGC v0.7 §13): reads completed A/B run
evidence and reports per-run facts and the arm comparison.

Evidence tooling only: it has no production execution authority, imports
nothing from ``kriya``, never writes inside the evidence tree, and reads only
COMPLETED run directories (``<root>/<arm>/evidence/<task>.<label>/done``
present) - a run still being written is skipped and listed as incomplete.
Deterministic: the same inputs give byte-identical output (sorted keys and
rows, no clock).

Judges are classified from untouched-base results (``base_judges.json``):
DISCRIMINATING (the base fails and the expected fixed result passes),
NON_DISCRIMINATING (the untouched base already passes) or AMBIGUOUS
(anything else). Only DISCRIMINATING judges count toward judge-verified
success; a passing non-discriminating judge is a regression check, never
independent proof that the requested change was achieved.

usage: python ab_analyzer.py <ab-root> <gold_targets.json> <base_judges.json> <task_profiles.json> <out-dir>
"""
from __future__ import annotations

import json
import os
import re
import statistics
import sys
from typing import Any, Dict, Iterable, List, Optional, Tuple

ARMS = ("A", "B")
DISCRIMINATING, NON_DISCRIMINATING, AMBIGUOUS = "DISCRIMINATING", "NON_DISCRIMINATING", "AMBIGUOUS"
_RUN_DIR = re.compile(r"^(?P<task>.+)\.(?P<label>r\d+)$")
_WALL = re.compile(r"generate exit=(?P<exit>-?\d+) wall_seconds=(?P<seconds>\d+)")
# Role caps (KRIYA_CAGC §8.2): an applicable task may cost B at most A + the role cap.
ROLE_CAPS = {"planner": 250, "architect": 250, "developer": 200, "reviewer": 200}


# -- judges ------------------------------------------------------------------


def classify_judge(base: Optional[str], fixed: Optional[str]) -> str:
    """``base`` / ``fixed``: the judge on the untouched base and on the expected
    fixed tree, each "PASS", "FAIL" or None (not measured)."""
    if base == "PASS":
        return NON_DISCRIMINATING
    if base == "FAIL" and fixed == "PASS":
        return DISCRIMINATING
    return AMBIGUOUS


# -- one run -----------------------------------------------------------------


def _read_json(path: str, default: Any) -> Any:
    if not os.path.exists(path):
        return default
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


def _read_lines(path: str) -> List[Dict[str, Any]]:
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def _read_text(path: str) -> str:
    if not os.path.exists(path):
        return ""
    with open(path, encoding="utf-8", errors="replace") as handle:
        return handle.read()


_CONTROL = ".kriya/control/"


def _metadata_member(name: str) -> bool:
    """A macOS AppleDouble entry (``._name``) the archiver adds: never data."""
    return os.path.basename(name.rstrip("/")).startswith("._")


def _untracked_files(path: str) -> List[str]:
    """Members of the run's untracked-files archive (none when absent or
    empty), archiver metadata excluded."""
    import tarfile

    if not os.path.exists(path) or os.path.getsize(path) == 0:
        return []
    with tarfile.open(path) as archive:
        return [name for name in archive.getnames() if not _metadata_member(name)]


def _archived_control(path: str) -> Dict[str, List[Dict[str, Any]]]:
    """The run's own Kriya control records from its untracked-files archive:
    the workspace's .kriya/ is untracked and wiped before each replicate, so
    at the end of a run it holds exactly that run's records. Keyed by the
    path under .kriya/control/ (JSON-lines files only)."""
    import tarfile

    records: Dict[str, List[Dict[str, Any]]] = {}
    if not os.path.exists(path) or os.path.getsize(path) == 0:
        return records
    with tarfile.open(path) as archive:
        for member in archive.getmembers():
            if member.isfile() and member.name.startswith(_CONTROL) and member.name.endswith(".jsonl") \
                    and not _metadata_member(member.name):
                text = archive.extractfile(member).read().decode("utf-8", "replace")
                records[member.name[len(_CONTROL):]] = [json.loads(line) for line in text.splitlines() if line.strip()]
    return records


def _belongs_to(record: Dict[str, Any], run_id: Optional[str]) -> bool:
    owner = record.get("run_id") or (record.get("_run_record") or {}).get("run_id")
    return run_id is None or owner is None or owner == run_id


def _events(rows: Iterable[Dict[str, Any]]) -> List[Tuple[str, Dict[str, Any]]]:
    """(kind, event) for every run event of every captured trace row, in row
    then event order."""
    out = []
    for row in rows:
        raw = row.get("run_events") or "[]"
        for event in json.loads(raw) if isinstance(raw, str) else raw:
            out.append((event.get("kind", ""), event))
    return out


def _planned_files(diagnostics: List[Dict[str, Any]]) -> Tuple[List[str], int]:
    """The planned paths of the last approved plan and how many planning
    attempts preceded it (repairs)."""
    planned: List[str] = []
    attempts = 0
    for record in diagnostics:
        plan = record.get("approved_plan") or {}
        if plan.get("subtasks"):
            planned = sorted({f["path"] for s in plan["subtasks"] for f in s.get("planned_files", [])})
        attempts = max(attempts, int(record.get("attempt") or 0))
    return planned, attempts


def analyze_run(run_dir: str, arm: str, task: str, label: str, gold_targets: List[str]) -> Dict[str, Any]:
    rows = _read_json(os.path.join(run_dir, "traces.json"), [])
    enforce = next((r for r in rows if str(r.get("run_id", "")).endswith(".enforce")), None)
    run_id = str(enforce["run_id"])[: -len(".enforce")] if enforce else (rows[0]["run_id"] if rows else None)
    # Control records: the run's own archive when it holds them (complete),
    # else the driver's captured tail; only this run's records either way.
    archived = _archived_control(os.path.join(run_dir, "untracked.tar"))
    control = os.path.join(run_dir, "kriya", "control")
    if archived:
        decisions = archived.get("decisions.jsonl", [])
        diagnostics = [r for name in sorted(archived) if name.startswith("planning-diagnostics/")
                       for r in archived[name]]
    else:
        decisions = _read_lines(os.path.join(control, "decisions.jsonl"))
        diagnostics = []
        diag_dir = os.path.join(control, "planning-diagnostics")
        if os.path.isdir(diag_dir):
            for name in sorted(os.listdir(diag_dir)):
                diagnostics += _read_lines(os.path.join(diag_dir, name))
    decisions = [d for d in decisions if _belongs_to(d, run_id)]
    diagnostics = [d for d in diagnostics if _belongs_to(d, run_id)]
    events = _events(rows)
    if enforce is not None:
        status, failure = enforce.get("status"), enforce.get("failure_category")
    elif rows:
        status, failure = rows[-1].get("status"), rows[-1].get("failure_category")
    else:
        status, failure = None, None
    wall = _WALL.search(_read_text(os.path.join(run_dir, "run.txt")))
    success = status == "success" and wall is not None and wall.group("exit") == "0"

    planned, planning_attempts = _planned_files(diagnostics)
    repairs = max([int(d.get("repair_attempts") or 0) for d in decisions
                   if d.get("type") == "structured_plan_validation"] or [planning_attempts])
    developer_retries = 0
    for row in rows:
        started = sum(1 for kind, _ in _events([row]) if kind == "attempt.started")
        developer_retries += max(0, started - 1)

    guidance = [dict(e.get("details") or {}) for kind, e in events if kind == "capability.guidance"] + \
        [{k: v for k, v in d.items() if not k.startswith("_") and k not in ("timestamp", "type")}
         for d in decisions if d.get("type") == "capability.guidance"]
    compositions = [e.get("details") or {} for kind, e in events if kind == "developer.prompt_composition"]
    t0 = any(t.get("tier") == "member_exact"
             for kind, e in events if kind == "context.known_target_package"
             for t in (e.get("details") or {}).get("tiers", []))
    role_rows = [row for kind, e in events if kind == "model.role_metrics"
                 for row in (e.get("details") or {}).get("rows", [])]
    roles: Dict[str, Dict[str, float]] = {}
    for row in role_rows:
        agg = roles.setdefault(row.get("role", "?"), {"calls": 0, "prompt_tokens": 0, "latency_seconds": 0.0})
        agg["calls"] += int(row.get("calls") or 0)
        agg["prompt_tokens"] += int(row.get("prompt_tokens") or 0)
        agg["latency_seconds"] = round(agg["latency_seconds"] + float(row.get("latency_seconds") or 0), 3)
    runtimes = sorted({str(row.get("runtime_digest")) for row in role_rows if row.get("runtime_digest")})
    judge = _read_text(os.path.join(run_dir, "judge.result"))
    judge_result = "SOLVED" if ": SOLVED" in judge else "NOT_SOLVED" if "NOT_SOLVED" in judge else None
    diff_present = bool(_read_text(os.path.join(run_dir, "workspace.diff")).strip()) or \
        any(not name.startswith(".kriya") for name in _untracked_files(os.path.join(run_dir, "untracked.tar")))
    return {
        "task": task, "arm": arm, "replicate": label, "run_id": run_id,
        "outcome": "SUCCESS" if success else "FAILURE", "status": status, "failure_category": failure,
        "wall_seconds": int(wall.group("seconds")) if wall else None,
        "planned_files": planned,
        "correct_targets": bool(gold_targets) and set(gold_targets) <= set(planned),
        "exact_t0_present": t0,
        "planner_repairs": repairs, "developer_retries": developer_retries,
        "guidance": guidance,
        "selected_capabilities": sorted({c for g in guidance for c in g.get("selected_capability_ids", [])}),
        "rendered_rules": sorted({r for g in guidance for r in g.get("rule_ids", [])}),
        "cap_drops": sorted({r for g in guidance for r in g.get("dropped_by_cap_rule_ids", [])}),
        "fit_drops": sorted({r for g in guidance for r in g.get("dropped_by_fit_rule_ids", [])}),
        "developer_prompt_eval_tokens": sum(int(c.get("prompt_tokens_reported") or 0) for c in compositions),
        "developer_prefill_seconds": round(sum(float(c.get("prefill_seconds") or 0) for c in compositions), 3),
        "roles": roles, "runtime_digests": runtimes,
        "judge": judge_result, "workspace_diff_present": diff_present,
    }


# -- the matrix ----------------------------------------------------------------


def collect(root: str, gold: Dict[str, List[str]]) -> Tuple[List[Dict[str, Any]], List[str]]:
    runs, incomplete = [], []
    for arm in ARMS:
        evidence = os.path.join(root, arm, "evidence")
        if not os.path.isdir(evidence):
            continue
        for name in sorted(os.listdir(evidence)):
            match = _RUN_DIR.match(name)
            path = os.path.join(evidence, name)
            if not match or not os.path.isdir(path):
                continue
            if not os.path.exists(os.path.join(path, "done")):
                incomplete.append(f"{arm}/{name}")
                continue
            runs.append(analyze_run(path, arm, match["task"], match["label"], gold.get(match["task"], [])))
    runs.sort(key=lambda r: (r["task"], r["replicate"], r["arm"]))
    return runs, sorted(incomplete)


def _rate(hits: int, total: int) -> Optional[float]:
    return round(hits / total, 4) if total else None


def summarize(runs: List[Dict[str, Any]], judges: Dict[str, str], profiles: Dict[str, Any]) -> Dict[str, Any]:
    arms: Dict[str, Any] = {}
    for arm in ARMS:
        mine = [r for r in runs if r["arm"] == arm]
        discriminating = [r for r in mine if judges.get(r["task"]) == DISCRIMINATING]
        arms[arm] = {
            "runs": len(mine),
            "kriya_success": sum(r["outcome"] == "SUCCESS" for r in mine),
            "kriya_success_rate": _rate(sum(r["outcome"] == "SUCCESS" for r in mine), len(mine)),
            "judge_verified_success": sum(r["outcome"] == "SUCCESS" and r["judge"] == "SOLVED"
                                          for r in discriminating),
            "judge_verified_success_rate": _rate(
                sum(r["outcome"] == "SUCCESS" and r["judge"] == "SOLVED" for r in discriminating),
                len(discriminating)),
            "judge_verified_denominator": len(discriminating),
            "false_success": sum(r["outcome"] == "SUCCESS" and r["judge"] == "NOT_SOLVED" for r in mine),
            "success_not_independently_verifiable": sum(
                r["outcome"] == "SUCCESS" and judges.get(r["task"]) != DISCRIMINATING and r["judge"] == "SOLVED"
                for r in mine),
            "correct_target_rate": _rate(sum(r["correct_targets"] for r in mine), len(mine)),
            "exact_t0_rate": _rate(sum(r["exact_t0_present"] for r in mine), len(mine)),
            "planner_repairs": _distribution(r["planner_repairs"] for r in mine),
            "developer_retries": _distribution(r["developer_retries"] for r in mine),
        }
    guidance_events = [(r, g) for r in runs if r["arm"] == "B" for g in r["guidance"]]
    developer_tokens = [g.get("estimated_tokens", 0) for _r, g in guidance_events if g.get("role") == "developer"]
    planner = [g for _r, g in guidance_events if g.get("role") == "planner"]
    leakage = []
    for run, event in guidance_events:
        forbidden = set(profiles.get(run["task"], {}).get("forbidden_capabilities", []))
        leaked = {c.split("@")[0] for c in event.get("selected_capability_ids", [])} & forbidden
        if leaked:
            leakage.append({"task": run["task"], "replicate": run["replicate"], "request": event.get("request"),
                            "capabilities": sorted(leaked)})
    per_task = {}
    for task in sorted({r["task"] for r in runs}):
        a = [r for r in runs if r["task"] == task and r["arm"] == "A"]
        b = [r for r in runs if r["task"] == task and r["arm"] == "B"]
        applicable = any(r["rendered_rules"] for r in b)
        mean = lambda rs, key: round(statistics.mean(r[key] for r in rs), 1) if rs else None  # noqa: E731
        a_tokens, b_tokens = mean(a, "developer_prompt_eval_tokens"), mean(b, "developer_prompt_eval_tokens")
        per_task[task] = {
            "judge": judges.get(task, AMBIGUOUS), "guidance_applicable": applicable,
            "developer_prompt_eval_tokens": {"A": a_tokens, "B": b_tokens,
                                             "delta": round(b_tokens - a_tokens, 1)
                                             if a_tokens is not None and b_tokens is not None else None},
            "developer_prefill_seconds": {"A": mean(a, "developer_prefill_seconds"),
                                          "B": mean(b, "developer_prefill_seconds")},
            "prompt_gate": _prompt_gate(a_tokens, b_tokens, applicable),
        }
    return {
        "arms": arms,
        "judges": dict(sorted(judges.items())),
        "guidance": {
            "developer_median_estimated_tokens": statistics.median(developer_tokens) if developer_tokens else None,
            "requests": len(guidance_events),
            "requests_with_fit_drop": sum(bool(g.get("dropped_by_fit_rule_ids")) for _r, g in guidance_events),
            "requests_with_cap_drop": sum(bool(g.get("dropped_by_cap_rule_ids")) for _r, g in guidance_events),
            "planner_requests": len(planner),
            "planner_fit_drop_rate": _rate(sum(bool(g.get("dropped_by_fit_rule_ids")) for g in planner),
                                           len(planner)),
            "leakage": leakage,
        },
        "per_task": per_task,
        "runtime_digests": {arm: sorted({d for r in runs if r["arm"] == arm for d in r["runtime_digests"]})
                            for arm in ARMS},
    }


def _prompt_gate(a: Optional[float], b: Optional[float], applicable: bool) -> Optional[str]:
    if a is None or b is None:
        return None
    bound = a + (ROLE_CAPS["developer"] if applicable else 0)
    return "PASS" if b <= bound else "FAIL"


def _distribution(values: Iterable[int]) -> Dict[str, int]:
    out: Dict[str, int] = {}
    for value in values:
        out[str(value)] = out.get(str(value), 0) + 1
    return dict(sorted(out.items(), key=lambda item: int(item[0])))


def markdown(runs: List[Dict[str, Any]], summary: Dict[str, Any], incomplete: List[str]) -> str:
    lines = ["| task | arm | rep | run id | outcome | s | targets | T0 | repairs | retries | rules sent | fit drops "
             "| dev prompt tokens | dev prefill s | judge | diff |",
             "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for r in runs:
        lines.append("| {task} | {arm} | {replicate} | {run_id} | {outcome} | {wall_seconds} | {ct} | {t0} | "
                     "{planner_repairs} | {developer_retries} | {rules} | {fit} | {developer_prompt_eval_tokens} | "
                     "{developer_prefill_seconds} | {judge} | {diff} |".format(
                         ct="yes" if r["correct_targets"] else "no", t0="yes" if r["exact_t0_present"] else "no",
                         rules=", ".join(r["rendered_rules"]) or "-", fit=", ".join(r["fit_drops"]) or "-",
                         diff="yes" if r["workspace_diff_present"] else "no", **r))
    if incomplete:
        lines += ["", "Incomplete (skipped): " + ", ".join(incomplete)]
    lines += ["", "```json", json.dumps(summary, indent=2, sort_keys=True), "```", ""]
    return "\n".join(lines)


def main(argv: List[str]) -> int:
    root, gold_path, judges_path, profiles_path, out = argv[1:6]
    gold = _read_json(gold_path, {})
    base = _read_json(judges_path, {})
    judges = {task: classify_judge(v.get("base"), v.get("fixed")) for task, v in base.items()}
    profiles = _read_json(profiles_path, {})
    runs, incomplete = collect(root, gold)
    summary = summarize(runs, judges, profiles)
    os.makedirs(out, exist_ok=True)
    with open(os.path.join(out, "runs.json"), "w", encoding="utf-8") as handle:
        json.dump({"runs": runs, "incomplete": incomplete}, handle, indent=2, sort_keys=True)
    with open(os.path.join(out, "summary.json"), "w", encoding="utf-8") as handle:
        json.dump(summary, handle, indent=2, sort_keys=True)
    with open(os.path.join(out, "report.md"), "w", encoding="utf-8") as handle:
        handle.write(markdown(runs, summary, incomplete))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
