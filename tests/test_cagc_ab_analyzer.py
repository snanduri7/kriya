"""CAGC A/B evidence analyzer (benchmarks/cagc/ab_analyzer.py): fixture tests
only - synthetic evidence trees, never live A/B data."""
import hashlib
import importlib.util
import io
import json
import os
import pathlib
import tarfile

import pytest

_PATH = pathlib.Path(__file__).resolve().parents[1] / "benchmarks" / "cagc" / "ab_analyzer.py"
_spec = importlib.util.spec_from_file_location("ab_analyzer", _PATH)
ab = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ab)

GOLD = {"java-x": ["src/X.java"], "python-y": ["pkg/y.py"]}
PROFILES = {"java-x": {"forbidden_capabilities": ["spring", "python", "pip", "gradle"]},
            "python-y": {"forbidden_capabilities": ["java", "spring", "spring_xml", "spring_config", "maven", "gradle"]}}


def _guidance(role, rules=(), selected=("java@1",), fit=(), cap=(), tokens=0, request=None):
    return {"role": role, "operation": "edit", "context": "existing", "selected_capability_ids": list(selected),
            "rendered_capability_ids": list(selected) if rules else [], "rule_ids": list(rules),
            "dropped_by_cap_rule_ids": list(cap), "dropped_by_fit_rule_ids": list(fit),
            "estimated_tokens": tokens, "digest": "d", "request": request or role}


def _run(root, arm, task, label, *, status="success", exit_code=0, attempts=1, guidance=(), planned=("src/X.java",),
         repairs=0, t0=True, prompt_tokens=1000, prefill=2.0, judge="SOLVED", diff="x", untracked=(), done=True,
         planner_guidance=()):
    run_dir = root / arm / "evidence" / f"{task}.{label}"
    (run_dir / "kriya" / "control" / "planning-diagnostics").mkdir(parents=True)
    events = [{"kind": "attempt.started"} for _ in range(attempts)]
    events += [{"kind": "capability.guidance", "details": g} for g in guidance]
    events.append({"kind": "developer.prompt_composition",
                   "details": {"prompt_tokens_reported": prompt_tokens, "prefill_seconds": prefill}})
    if t0:
        events.append({"kind": "context.known_target_package",
                       "details": {"tiers": [{"path": "src/X.java", "tier": "member_exact"}]}})
    events.append({"kind": "model.role_metrics", "details": {"rows": [
        {"role": "developer", "calls": attempts, "prompt_tokens": prompt_tokens, "latency_seconds": 3.5,
         "runtime_digest": "0769fe62"}]}})
    rows = [{"run_id": "sub1", "status": status, "failure_category": None, "run_events": json.dumps(events)},
            {"run_id": f"20261003T{label}.enforce", "status": status,
             "failure_category": None if status == "success" else "failed", "run_events": "[]"}]
    (run_dir / "traces.json").write_text(json.dumps(rows))
    decisions = [{"type": "structured_plan_validation", "repair_attempts": repairs, "_workspace_id": "w"}]
    decisions += [{"type": "capability.guidance", "timestamp": "t", "_run_record": {}, **g} for g in planner_guidance]
    (run_dir / "kriya" / "control" / "decisions.jsonl").write_text("\n".join(json.dumps(d) for d in decisions))
    (run_dir / "kriya" / "control" / "planning-diagnostics" / "p.jsonl").write_text(json.dumps(
        {"attempt": repairs, "approved_plan": {"subtasks": [{"planned_files": [{"path": p} for p in planned]}]}}))
    (run_dir / "run.txt").write_text(f"analyze exit=0 seconds=5\ngenerate exit={exit_code} wall_seconds=600\n")
    if judge:
        (run_dir / "judge.result").write_text(f"JUDGE {arm} {task} {label}: {judge}\n")
    (run_dir / "workspace.diff").write_text(diff)
    with tarfile.open(run_dir / "untracked.tar", "w") as archive:
        for name in untracked:
            data = b"new"
            info = tarfile.TarInfo(name)
            info.size = len(data)
            archive.addfile(info, io.BytesIO(data))
    if done:
        (run_dir / "done").write_text("")
    return run_dir


@pytest.mark.parametrize("base, fixed, expected", [
    ("FAIL", "PASS", ab.DISCRIMINATING), ("PASS", "PASS", ab.NON_DISCRIMINATING), ("PASS", None, ab.NON_DISCRIMINATING),
    ("FAIL", "FAIL", ab.AMBIGUOUS), ("FAIL", None, ab.AMBIGUOUS), (None, None, ab.AMBIGUOUS)])
def test_judge_classification(base, fixed, expected):
    assert ab.classify_judge(base, fixed) == expected


def test_one_run_is_read_completely(tmp_path):
    _run(tmp_path, "B", "java-x", "r2", attempts=3, repairs=1, guidance=[
        _guidance("developer", rules=["java.dev.import_style"], tokens=67),
        _guidance("reviewer", tokens=0, fit=["spring.review.transactional_self_invocation"])],
        planner_guidance=[_guidance("planner", rules=["maven.plan.existing_topology_preserved"], tokens=58)])
    runs, incomplete = ab.collect(str(tmp_path), GOLD)
    assert incomplete == []
    [run] = runs
    assert (run["task"], run["arm"], run["replicate"], run["run_id"]) == ("java-x", "B", "r2", "20261003Tr2")
    assert run["outcome"] == "SUCCESS" and run["wall_seconds"] == 600
    assert run["gold_target_recall"] is True and run["exact_target_set_match"] is True
    assert run["extra_targets"] == [] and run["exact_t0_present"] is True
    assert run["planner_repairs"] == 1 and run["developer_retries"] == 2
    assert run["rendered_rules"] == ["java.dev.import_style", "maven.plan.existing_topology_preserved"]
    assert run["fit_drops"] == ["spring.review.transactional_self_invocation"] and run["cap_drops"] == []
    assert len(run["guidance"]) == 3 and run["selected_capabilities"] == ["java@1"]
    assert run["roles"]["developer"] == {"calls": 3, "prompt_tokens": 1000, "latency_seconds": 3.5}
    assert run["runtime_digests"] == ["0769fe62"] and run["judge"] == "SOLVED" and run["workspace_diff_present"]


def test_a_failed_generate_or_failed_status_is_not_success(tmp_path):
    _run(tmp_path, "A", "java-x", "r1", status="failed", exit_code=1)
    _run(tmp_path, "A", "java-x", "r4", status="success", exit_code=1)
    runs, _ = ab.collect(str(tmp_path), GOLD)
    assert [r["outcome"] for r in runs] == ["FAILURE", "FAILURE"]


def test_incomplete_runs_are_skipped_never_read(tmp_path):
    _run(tmp_path, "A", "java-x", "r1")
    _run(tmp_path, "B", "java-x", "r2", done=False)
    runs, incomplete = ab.collect(str(tmp_path), GOLD)
    assert [(r["arm"], r["replicate"]) for r in runs] == [("A", "r1")] and incomplete == ["B/java-x.r2"]


def test_workspace_change_from_diff_or_untracked_files(tmp_path):
    _run(tmp_path, "A", "java-x", "r1", diff="", untracked=(".kriya/run.lock", ".kriya/worktree/A.java"))
    _run(tmp_path, "A", "java-x", "r4", diff="", untracked=("src/New.java",))
    runs, _ = ab.collect(str(tmp_path), GOLD)
    assert [r["workspace_diff_present"] for r in runs] == [False, True]  # Kriya's own .kriya/ is not a change


def test_control_records_come_from_the_runs_own_archive(tmp_path):
    """The driver's decision tail can miss lines (the ledger is wiped with
    .kriya/ before a replicate); the archive holds the run's whole record."""
    run_dir = _run(tmp_path, "B", "java-x", "r3", repairs=0)
    archived = [{"type": "structured_plan_validation", "repair_attempts": 2, "run_id": "20261003Tr3"},
                {"type": "structured_plan_validation", "repair_attempts": 9, "run_id": "another-run"},
                {"type": "capability.guidance", "run_id": "20261003Tr3", **_guidance("planner", rules=["m.plan.r"])}]
    diagnostics = {"attempt": 2, "approved_plan": {"plan_id": "20261003Tr3",
                                                   "subtasks": [{"planned_files": [{"path": "src/X.java"}]}]}}
    apple_double = "\x00\x05\x16\x07Mac OS X"  # the AppleDouble entries macOS tar adds beside each file
    with tarfile.open(run_dir / "untracked.tar", "w") as archive:
        for name, text in ((".kriya/control/decisions.jsonl", "\n".join(json.dumps(d) for d in archived)),
                           (".kriya/control/._decisions.jsonl", apple_double),
                           (".kriya/control/planning-diagnostics/p.jsonl", json.dumps(diagnostics)),
                           ("src/._X.java", apple_double)):
            data = text.encode()
            info = tarfile.TarInfo(name)
            info.size = len(data)
            archive.addfile(info, io.BytesIO(data))
    [run] = ab.collect(str(tmp_path), GOLD)[0]
    assert run["planner_repairs"] == 2 and run["rendered_rules"] == ["m.plan.r"]
    assert run["gold_target_recall"] is True and run["workspace_diff_present"] is True  # the fixture's diff text


def test_only_discriminating_judges_verify_success(tmp_path):
    for arm, label in (("A", "r1"), ("B", "r2")):
        _run(tmp_path, arm, "java-x", label, judge="SOLVED")                 # non-discriminating judge
        _run(tmp_path, arm, "python-y", label, planned=("pkg/y.py",), judge="SOLVED")  # discriminating judge
    runs, _ = ab.collect(str(tmp_path), GOLD)
    judges = {"java-x": ab.NON_DISCRIMINATING, "python-y": ab.DISCRIMINATING}
    summary = ab.summarize(runs, judges, PROFILES)
    for arm in ("A", "B"):
        s = summary["arms"][arm]
        assert s["kriya_success"] == 2 and s["judge_verified_success"] == 1 and s["judge_verified_denominator"] == 1
        assert s["success_not_independently_verifiable"] == 1 and s["false_success"] == 0


def test_false_success_is_success_with_a_failing_judge(tmp_path):
    _run(tmp_path, "B", "python-y", "r2", planned=("pkg/y.py",), judge="NOT_SOLVED")
    _run(tmp_path, "B", "python-y", "r3", planned=("pkg/y.py",), status="failed", exit_code=1, judge="NOT_SOLVED")
    runs, _ = ab.collect(str(tmp_path), GOLD)
    summary = ab.summarize(runs, {"python-y": ab.DISCRIMINATING}, PROFILES)
    assert summary["arms"]["B"]["false_success"] == 1 and summary["arms"]["B"]["judge_verified_success"] == 0


def test_leakage_and_drop_frequencies(tmp_path):
    _run(tmp_path, "B", "python-y", "r2", planned=("pkg/y.py",), guidance=[
        _guidance("developer", selected=("python@1",)),
        _guidance("developer", selected=("python@1", "java@1"), rules=["java.dev.import_style"], tokens=67)],
        planner_guidance=[_guidance("planner", selected=("pip@1",), fit=["x.plan.y"])])
    runs, _ = ab.collect(str(tmp_path), GOLD)
    guidance = ab.summarize(runs, {}, PROFILES)["guidance"]
    assert guidance["leakage"] == [{"task": "python-y", "replicate": "r2", "request": "developer",
                                    "capabilities": ["java"]}]
    assert guidance["requests"] == 3 and guidance["requests_with_fit_drop"] == 1
    assert guidance["planner_requests"] == 1 and guidance["planner_fit_drop_rate"] == 1.0
    assert guidance["developer_median_estimated_tokens"] == 33.5


def test_prompt_gate_allows_the_role_cap_only_where_guidance_applies(tmp_path):
    _run(tmp_path, "A", "java-x", "r1", prompt_tokens=1000)
    _run(tmp_path, "B", "java-x", "r2", prompt_tokens=1150, guidance=[_guidance("developer", rules=["r.dev.a"])])
    _run(tmp_path, "A", "python-y", "r1", planned=("pkg/y.py",), prompt_tokens=1000)
    _run(tmp_path, "B", "python-y", "r2", planned=("pkg/y.py",), prompt_tokens=1001)
    runs, _ = ab.collect(str(tmp_path), GOLD)
    per_task = ab.summarize(runs, {}, PROFILES)["per_task"]
    assert per_task["java-x"]["guidance_applicable"] and per_task["java-x"]["prompt_gate"] == "PASS"
    assert not per_task["python-y"]["guidance_applicable"] and per_task["python-y"]["prompt_gate"] == "FAIL"
    assert per_task["python-y"]["developer_prompt_eval_tokens"] == {"A": 1000, "B": 1001, "delta": 1}


def _tree_digest(root):
    digest = hashlib.sha256()
    for path in sorted(pathlib.Path(root).rglob("*")):
        digest.update(str(path.relative_to(root)).encode())
        if path.is_file():
            digest.update(path.read_bytes())
    return digest.hexdigest()


def test_main_is_deterministic_and_never_writes_evidence(tmp_path):
    root = tmp_path / "ab"
    _run(root, "A", "java-x", "r1")
    _run(root, "B", "java-x", "r2", guidance=[_guidance("developer", rules=["java.dev.import_style"], tokens=67)])
    inputs = tmp_path / "in"
    inputs.mkdir()
    for name, data in (("gold.json", GOLD), ("judges.json", {"java-x": {"base": "FAIL", "fixed": "PASS"}}),
                       ("profiles.json", PROFILES)):
        (inputs / name).write_text(json.dumps(data))
    before = _tree_digest(root)
    outputs = []
    for out in ("out1", "out2"):
        assert ab.main(["ab", str(root), str(inputs / "gold.json"), str(inputs / "judges.json"),
                        str(inputs / "profiles.json"), str(tmp_path / out)]) == 0
        outputs.append({name: (tmp_path / out / name).read_bytes() for name in ("runs.json", "summary.json",
                                                                               "report.md")})
    assert outputs[0] == outputs[1]
    assert _tree_digest(root) == before
    summary = json.loads(outputs[0]["summary.json"])
    assert summary["judges"] == {"java-x": ab.DISCRIMINATING} and summary["arms"]["B"]["judge_verified_success"] == 1
    assert os.path.getsize(tmp_path / "out1" / "report.md") > 0


def test_tasks_without_known_gold_are_excluded_from_the_localization_denominator(tmp_path):
    """The first report divided gold-target hits by every run of the arm, so a
    task with no known gold counted as a miss (0.50 instead of 10/12)."""
    _run(tmp_path, "A", "java-x", "r1", planned=("src/X.java", "src/test/XTest.java"))   # gold, hit
    _run(tmp_path, "A", "python-y", "r1", planned=("pkg/other.py",))                     # gold, miss
    _run(tmp_path, "A", "spring-z", "r1", planned=("src/A.java", "conf/a.xml"))          # no gold
    _run(tmp_path, "A", "spring-z", "r4", planned=("src/A.java",))                       # no gold
    runs, _ = ab.collect(str(tmp_path), GOLD)
    unknown = [r for r in runs if r["task"] == "spring-z"]
    assert all(r["gold_target_recall"] is None and r["exact_target_set_match"] is None
               and r["extra_targets"] is None for r in unknown)
    arm = ab.summarize(runs, {}, PROFILES)["arms"]["A"]
    assert arm["gold_target_recall"] == {"hits": 1, "eligible": 2, "rate": 0.5}
    assert "correct_target_rate" not in arm
    report = ab.markdown(runs, ab.summarize(runs, {}, PROFILES), [])
    assert "| spring-z | A | r1 |" in report and "| n/a |" in report


@pytest.mark.parametrize("planned, recall, exact, extra", [
    (("src/X.java",), True, True, []),
    (("src/X.java", "src/test/java/XTest.java", "README.md"), True, True, []),   # tests/docs: outside gold scope
    (("src/X.java", "src/Y.java"), True, False, ["src/Y.java"]),
    (("src/Y.java",), False, False, ["src/Y.java"]),
    ((), False, False, [])])
def test_localization_is_measured_in_the_gold_scope(planned, recall, exact, extra):
    assert ab.localization(list(planned), ["src/X.java"]) == {
        "gold_target_recall": recall, "exact_target_set_match": exact, "extra_targets": extra}
    assert ab.localization(list(planned), []) == {
        "gold_target_recall": None, "exact_target_set_match": None, "extra_targets": None}


def test_exact_match_and_extra_target_rates_count_eligible_runs_only(tmp_path):
    _run(tmp_path, "B", "java-x", "r2", planned=("src/X.java",))
    _run(tmp_path, "B", "java-x", "r3", planned=("src/X.java", "src/Extra.java"))
    _run(tmp_path, "B", "spring-z", "r2", planned=("src/A.java",))
    runs, _ = ab.collect(str(tmp_path), GOLD)
    arm = ab.summarize(runs, {}, PROFILES)["arms"]["B"]
    assert arm["exact_target_set_match"] == {"hits": 1, "eligible": 2, "rate": 0.5}
    assert arm["extra_target_rate"] == {"hits": 1, "eligible": 2, "rate": 0.5}


def _plan(*subtasks):
    return [{"approved_plan": {"subtasks": [
        {"planned_files": [{"path": path, "action": action} for path, action in files]} for files in subtasks]}}]


def test_multi_existing_target_subtasks_count_only_existing_targets():
    """PROTOCOL_v2 amendment 2: a subtask owning more than one EXISTING target
    (action modify/delete), the shape behind matrix-40's B 2/20 vs A 0/20."""
    assert ab.multi_existing_target_subtasks(_plan([("a", "modify"), ("b", "modify")], [("c", "modify")])) == 1
    assert ab.multi_existing_target_subtasks(_plan([("a", "modify"), ("b", "create")])) == 0
    assert ab.multi_existing_target_subtasks(_plan([("a", "delete"), ("b", "modify")])) == 1
    assert ab.multi_existing_target_subtasks([{"attempt": 1}]) is None


def _package(files, tiers, omitted):
    return [("context.known_target_package", {"details": {
        "known_target_files": files, "tiers": [{"path": p, "tier": t} for p, t in tiers],
        "omitted": [dict(path=p, reason=r, **({"member_id": m} if m else {})) for p, r, m in omitted]}})]


def test_capacity_refusal_and_starvation_suspect_are_kept_apart():
    """PROTOCOL_v2 amendment 3: minimum_authority_unfit is a capacity outcome;
    a planned target shown in no tier and omitted budget_exhausted without it
    is a starvation suspect for deterministic replay."""
    capacity = ab.known_target_outcomes(_package(
        ["x", "y"], [("x", "member_exact")], [("y", "minimum_authority_unfit", None), ("y", "budget_exhausted", None)]))
    assert capacity == {"capacity_refusals": [{"path": "y", "shown": False}], "starvation_suspects": []}
    starved = ab.known_target_outcomes(_package(["x", "y"], [("x", "member_exact")], [("y", "budget_exhausted", None)]))
    assert starved == {"capacity_refusals": [], "starvation_suspects": [{"path": "y"}]}
    excerpt = ab.known_target_outcomes(_package(["x", "y"], [("x", "member_exact"), ("y", "skeleton")],
                                                [("y", "minimum_authority_unfit", None), ("y", "body_elided", None)]))
    assert excerpt["capacity_refusals"] == [{"path": "y", "shown": True}] and excerpt["starvation_suspects"] == []
    member_only = ab.known_target_outcomes(_package(["x"], [("x", "member_exact")], [("x", "budget_exhausted", "X.m")]))
    assert member_only == {"capacity_refusals": [], "starvation_suspects": []}


def test_descriptive_metrics_per_arm(tmp_path):
    run_dir = _run(tmp_path, "B", "java-x", "r3")
    (run_dir / "kriya" / "control" / "planning-diagnostics" / "p.jsonl").write_text(json.dumps(
        _plan([("src/X.java", "modify"), ("src/Y.xml", "modify")])[0]))
    _run(tmp_path, "B", "java-x", "r2")
    runs, _ = ab.collect(str(tmp_path), GOLD)
    arm = ab.summarize(runs, {}, PROFILES)["arms"]["B"]
    assert arm["multi_existing_target_subtask_rate"] == {"hits": 1, "eligible": 2, "rate": 0.5}
    assert arm["minimum_authority_unfit"] == {"runs": 0, "refusals": 0, "rate": 0.0}
    assert arm["starvation_suspects"] == {"runs": 0, "omissions": 0}


def test_only_a_target_left_with_nothing_shown_is_a_starvation_suspect():
    """A member-level omission is about one member, not the target's minimum;
    a target that is shown in some tier was not deprived of everything."""
    member_level = ab.known_target_outcomes(_package(["x"], [], [("x", "budget_exhausted", "X.m")]))
    assert member_level["starvation_suspects"] == []
    shown = ab.known_target_outcomes(_package(["x"], [("x", "signatures")], [("x", "budget_exhausted", None)]))
    assert shown["starvation_suspects"] == []
