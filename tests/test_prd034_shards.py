"""PRD-034 static sharding of the canonical population (scripts/certification_shards.py): the partition, the merge audit, the docker guard and the whole pipeline on a sample.

The claim under test: cutting the audited census into disjoint shards that run side by side changes WHERE a test runs, never WHAT runs -- and every way that could silently stop being
true (a node in two shards, a node in none, a missing or duplicated result, a shard whose exit status disagrees with its junit, a container-mutating call from an ordinary shard) is a
named, tested merge-audit failure.
"""
import importlib.util
import json
import os
import random
import subprocess
import sys
import xml.etree.ElementTree as ElementTree
from pathlib import Path

import pytest

TESTS = Path(__file__).resolve().parent
ROOT = TESTS.parent
spec = importlib.util.spec_from_file_location("certification_shards", ROOT / "scripts" / "certification_shards.py")
shards = importlib.util.module_from_spec(spec)
spec.loader.exec_module(shards)


def _record(nodeid, *, docker=False, markers=()):
    return {"id": nodeid, "markers": list(markers), "skip_reasons": [], "docker": docker, "requires_platform": None}


def _census(extra=()):
    records = [_record(f"tests/test_f{index}.py::test_{n}") for index in range(8) for n in range(index + 2)]
    records += [_record("tests/test_docker_user.py::test_a", docker=True), _record("tests/test_docker_user.py::test_b", docker=True),
                _record("tests/test_tool.py::test_t", markers=("real_toolchain",)), _record("tests/test_measured.py::test_m1"), _record("tests/test_measured.py::test_m2")]
    return {"selection": "not live_model", "args": [], "count": len(records) + len(extra), "tests": records + list(extra)}


MEASURED = {"tests/test_measured.py": "container user measured by docker events"}
TIMINGS = {f"tests/test_f{index}.py": float((index + 1) * 100) for index in range(8)}


def _plan(census=None, count=3):
    return shards.plan(census or _census(), ordinary_shards=count, exclusive_files=MEASURED, timings=TIMINGS)


# --- the partition -----------------------------------------------------------------------------------------------------------------------

def test_the_plan_is_a_pure_function_of_the_census_and_its_inputs_not_of_collection_order():
    census = _census()
    reference = _plan(census)
    shuffled = dict(census, tests=random.Random(5).sample(census["tests"], len(census["tests"])))
    assert _plan(shuffled) == reference and _plan(census) == reference


def test_union_equals_the_census_exactly_and_no_node_is_in_two_shards():
    census, plan = _census(), _plan()
    assert shards.check_partition(plan, census) == []
    flat = [nodeid for shard in plan["shards"] for nodeid in shard["ids"]]
    assert sorted(flat) == sorted(record["id"] for record in census["tests"]) and len(flat) == len(set(flat))


def test_every_tier_test_and_every_measured_container_user_is_in_the_exclusive_shard_and_nothing_else_is():
    plan = _plan()
    exclusive = next(shard for shard in plan["shards"] if shard["id"] == shards.EXCLUSIVE)
    assert sorted(exclusive["ids"]) == sorted(["tests/test_docker_user.py::test_a", "tests/test_docker_user.py::test_b", "tests/test_tool.py::test_t",
                                               "tests/test_measured.py::test_m1", "tests/test_measured.py::test_m2"])
    assert set(plan["exclusive_reasons"]) == {"tests/test_docker_user.py", "tests/test_tool.py", "tests/test_measured.py"}


def test_ordinary_files_are_never_split_across_shards_and_the_load_is_balanced_by_the_recorded_timings():
    plan = _plan()
    owner = {}
    for shard in plan["shards"]:
        for nodeid in shard["ids"]:
            assert owner.setdefault(nodeid.split("::")[0], shard["id"]) == shard["id"], nodeid
    weights = [shard["weight_seconds"] for shard in plan["shards"] if shard["kind"] == "ordinary"]
    assert max(weights) - min(weights) <= max(TIMINGS.values())  # longest-processing-time packing: spread never exceeds the largest item


def test_the_partition_check_names_each_way_a_node_can_be_lost_duplicated_or_invented():
    census, plan = _census(), _plan()
    broken = json.loads(json.dumps(plan))
    victim = broken["shards"][1]["ids"][0]
    broken["shards"][1]["ids"].remove(victim)
    assert any("missing from every shard" in problem for problem in shards.check_partition(broken, census))
    broken = json.loads(json.dumps(plan))
    broken["shards"][2]["ids"].append(plan["shards"][1]["ids"][0])
    assert any("is in both" in problem for problem in shards.check_partition(broken, census))
    broken = json.loads(json.dumps(plan))
    broken["shards"][2]["ids"].append("tests/test_invented.py::test_x")
    assert any("not in the census" in problem for problem in shards.check_partition(broken, census))
    assert any("different census" in problem for problem in shards.check_partition(plan, _census(extra=[_record("tests/test_new.py::test_n")])))


def test_the_committed_exclusive_inputs_name_real_files_with_a_measured_reason_and_the_timing_input_is_digestable():
    exclusive = json.loads((TESTS / "certification" / "shard_exclusive_files.json").read_text())["files"]
    assert exclusive and all((ROOT / path).is_file() and reason for path, reason in exclusive.items())
    timings = json.loads((TESTS / "certification" / "shard_timings.json").read_text())["files"]
    assert timings and all(isinstance(value, float) for value in timings.values())


# --- the merge audit -----------------------------------------------------------------------------------------------------------------------

def _junit(path, nodeids, *, fail=(), skip=(), duplicate=()):
    from _certification_collection import _junit_key

    suite = ElementTree.Element("testsuite", {"name": "pytest", "tests": str(len(nodeids))})
    for nodeid in list(nodeids) + list(duplicate):
        classname, name = _junit_key(nodeid)
        case = ElementTree.SubElement(suite, "testcase", {"classname": classname, "name": name, "time": "0.01"})
        if nodeid in fail:
            ElementTree.SubElement(case, "failure", {"message": "boom"})
        if nodeid in skip:
            ElementTree.SubElement(case, "skipped", {"message": "s"})
    ElementTree.ElementTree(ElementTree.Element("testsuites")).write(path)
    root = ElementTree.Element("testsuites")
    root.append(suite)
    ElementTree.ElementTree(root).write(path, encoding="utf-8", xml_declaration=True)


def _lay_out(tmp_path, *, census=None, plan=None):
    census = census or _census()
    plan = plan or _plan(census)
    out, pytest_dir = tmp_path / "shards", tmp_path / "pytest"
    census_path = tmp_path / "selected.json"
    census_path.write_text(json.dumps(census))
    shards.write_plan(plan, str(out), inputs={"census": str(census_path)})
    by_id = {record["id"]: record for record in census["tests"]}
    for shard in plan["shards"]:
        directory = pytest_dir / "shards" / shard["id"]
        directory.mkdir(parents=True)
        _junit(str(directory / "junit.xml"), shard["ids"])
        (directory / "collection-census.json").write_text(json.dumps({"tests": [by_id[i] for i in shard["ids"]]}))
        (directory / "progress.json").write_text(json.dumps({"finished": True, "done": len(shard["ids"]), "total": len(shard["ids"])}))
        (directory / "skips.json").write_text(json.dumps({"certification_mode": True, "skipped": 0, "platform": "linux", "unexpected": 0, "platform_pending": [], "skips": []}))
        (directory / "exit-status.txt").write_text("0\n")
    return plan, str(out), str(pytest_dir), pytest_dir / "shards"


def test_a_clean_run_merges_with_the_census_as_its_population_and_every_node_once(tmp_path):
    plan, out, pytest_dir, _ = _lay_out(tmp_path)
    audit = shards.merge(out, pytest_dir)
    assert audit["ok"] and audit["problems"] == [] and audit["totals"]["tests"] == _census()["count"]
    assert audit["tiers_census"] == audit["tiers_ran"] and audit["tiers_census"]["docker"] == 2
    merged = json.loads((Path(pytest_dir) / "collection-census.json").read_text())
    assert merged == _census()  # the ONE authoritative census, not a re-derivation from the shards
    assert len(list(ElementTree.parse(Path(pytest_dir) / "junit.xml").getroot().iter("testcase"))) == _census()["count"]


@pytest.mark.parametrize("damage, expected", [
    ("drop_result", "results do not match the plan"),
    ("duplicate_result", "more than one terminal result"),
    ("unplanned_result", "results do not match the plan"),
    ("exit_contradicts_junit", "contradicts its junit"),
    ("not_finished", "progress file says"),
    ("docker_mutation", "container-mutating docker call"),
    ("wrong_census", "collected"),
    ("shard_vanished", "did not finish"),
    ("tier_reclassified", "tier counts"),
])
def test_the_merge_audit_fails_for_each_way_the_shards_could_stop_being_the_population(tmp_path, damage, expected):
    plan, out, pytest_dir, shard_root = _lay_out(tmp_path)
    ordinary = next(shard for shard in plan["shards"] if shard["id"] == "ordinary-1")
    directory = shard_root / "ordinary-1"
    ids = ordinary["ids"]
    if damage == "drop_result":
        _junit(str(directory / "junit.xml"), ids[1:])
    elif damage == "duplicate_result":
        _junit(str(directory / "junit.xml"), ids, duplicate=[ids[0]])
    elif damage == "unplanned_result":
        _junit(str(directory / "junit.xml"), ids + ["tests/test_f0.py::test_unplanned"])
    elif damage == "exit_contradicts_junit":
        (directory / "exit-status.txt").write_text("0\n")
        _junit(str(directory / "junit.xml"), ids, fail=[ids[0]])
    elif damage == "not_finished":
        (directory / "progress.json").write_text(json.dumps({"finished": False, "done": 3}))
    elif damage == "docker_mutation":
        (directory / "docker-shim.jsonl").write_text(json.dumps({"refused": True, "test": "tests/test_f1.py::test_0", "argv": "run --rm img"}) + "\n")
    elif damage == "wrong_census":
        (directory / "collection-census.json").write_text(json.dumps({"tests": [{"id": i} for i in ids[1:]]}))
    elif damage == "shard_vanished":
        (directory / "junit.xml").unlink()
    elif damage == "tier_reclassified":  # same ids, but this shard's run saw the Docker tests as ordinary (e.g. the daemon was absent): the tier counts of what ran differ
        exclusive = shard_root / "exclusive" / "collection-census.json"
        document = json.loads(exclusive.read_text())
        document["tests"] = [dict(record, docker=False) for record in document["tests"]]
        exclusive.write_text(json.dumps(document))
    audit = shards.merge(out, pytest_dir)
    assert not audit["ok"] and any(expected in problem for problem in audit["problems"]), audit["problems"]


def test_a_failed_node_fails_the_merge_even_though_every_audit_check_holds(tmp_path):
    plan, out, pytest_dir, shard_root = _lay_out(tmp_path)
    target = plan["shards"][1]["ids"][0]
    _junit(str(shard_root / "ordinary-1" / "junit.xml"), plan["shards"][1]["ids"], fail=[target])
    (shard_root / "ordinary-1" / "exit-status.txt").write_text("1\n")
    audit = shards.merge(out, pytest_dir)
    assert audit["problems"] == [] and audit["totals"]["failures"] == 1 and not audit["ok"]  # the verdict comes from the merged junit, never from a shard's exit status


def test_skips_merge_by_union_and_a_shard_outside_certification_mode_turns_the_mode_off(tmp_path):
    plan, out, pytest_dir, shard_root = _lay_out(tmp_path)
    (shard_root / "ordinary-2" / "skips.json").write_text(json.dumps({"certification_mode": False, "skipped": 1, "platform": "linux", "unexpected": 1, "platform_pending": [],
                                                                      "skips": [{"nodeid": "tests/test_f1.py::test_0", "reason": "r"}]}))
    shards.merge(out, pytest_dir)
    merged = json.loads((Path(pytest_dir) / "skips.json").read_text())
    assert merged["skipped"] == 1 and merged["unexpected"] == 1 and merged["certification_mode"] is False


# --- progress aggregation and the docker guard ---------------------------------------------------------------------------------------------

def _snap(total, done, **extra):
    base = {"pid": 1, "total": total, "done": done, "passed": done, "failed": 0, "errors": 0, "skipped": 0, "failed_nodes": [], "current": None, "current_tiers": [],
            "current_started": None, "longest_node_s": 1.0, "finished": False, "write_errors": 0}
    return {**base, **extra}


def test_the_aggregate_progress_sums_the_shards_and_attributes_the_current_node_to_the_exclusive_shard():
    document = shards.aggregate_snapshot({"exclusive": _snap(10, 4, current="tests/x.py::t", current_tiers=["docker"], current_started=5.0, failed=1, failed_nodes=["call:tests/x.py::t"]),
                                          "ordinary-1": _snap(30, 30, finished=True, current="tests/o.py::t")}, now=100.0, pid=7, started=0.0, history=[])
    assert (document["total"], document["done"], document["failed"]) == (40, 34, 1)
    assert document["current"] == "tests/x.py::t" and document["current_shard"] == "exclusive" and document["finished"] is False
    assert document["failed_nodes"] == ["exclusive:call:tests/x.py::t"] and set(document["shards"]) == {"exclusive", "ordinary-1"}
    done = shards.aggregate_snapshot({"exclusive": _snap(1, 1, finished=True), "ordinary-1": _snap(2, 2, finished=True)}, now=100.0, pid=7, started=0.0, history=[])
    assert done["finished"] is True


def _fake_docker(tmp_path):
    real = tmp_path / "real-docker"
    real.write_text('#!/bin/sh\necho "REAL docker $*"\n')
    real.chmod(0o755)
    return real


@pytest.mark.parametrize("argv, refused", [
    (["ps", "-a", "--filter", "name=kriya-"], False), (["info"], False), (["version", "--format", "x"], False), (["image", "ls"], False), (["inspect", "x"], False),
    (["run", "--rm", "img"], True), (["create", "img"], True), (["rm", "-f", "c"], True), (["build", "."], True), (["image", "rm", "x"], True), (["network", "create", "n"], True),
    (["volume", "rm", "v"], True), (["exec", "c", "sh"], True), (["something-new"], True),
])
def test_the_ordinary_shard_docker_guard_passes_reads_and_refuses_everything_that_could_touch_a_container(tmp_path, argv, refused):
    shim = shards.write_docker_shim(str(tmp_path / "shim"), str(_fake_docker(tmp_path)))
    log = tmp_path / "log.jsonl"
    result = subprocess.run([shim, *argv], capture_output=True, text=True, check=False, env={**os.environ, "KRIYA_DOCKER_SHIM_LOG": str(log), "PYTEST_CURRENT_TEST": "tests/t.py::t (call)"})
    rows = [json.loads(line) for line in log.read_text().splitlines()]
    if refused:
        assert result.returncode == 125 and "REAL docker" not in result.stdout
        assert rows[-1]["refused"] is True and rows[-1]["test"] == "tests/t.py::t (call)" and rows[-1]["argv"].startswith(argv[0])
    else:
        assert result.returncode == 0 and result.stdout.startswith("REAL docker " + argv[0]) and rows[-1]["refused"] is False


# --- the whole pipeline on a real sample, and the shell wiring ----------------------------------------------------------------------------

SAMPLES = ["tests/certification/sample_shard_a.py", "tests/certification/sample_shard_b.py"]


def _pytest(*args, cwd=ROOT):
    return subprocess.run([sys.executable, "-m", "pytest", "-o", "addopts=", "-m", "not live_model", "-p", "no:cacheprovider", "-q", *args], cwd=str(cwd), capture_output=True, text=True,
                          timeout=300, env={**os.environ, "KRIYA_CERTIFICATION": ""}, check=False)


def test_a_real_sample_is_planned_run_as_separate_pytest_processes_and_merges_to_exactly_the_census(tmp_path):
    census_path = tmp_path / "selected.json"
    assert _pytest(*SAMPLES, "--collect-only", f"--collection-census={census_path}").returncode == 0
    census = json.loads(census_path.read_text())
    assert census["count"] == 6
    exclusive_file = tmp_path / "exclusive.json"
    timings_file = tmp_path / "timings.json"
    exclusive_file.write_text(json.dumps({"files": {}}))
    timings_file.write_text(json.dumps({"files": {}}))
    planned = subprocess.run([sys.executable, str(ROOT / "scripts" / "certification_shards.py"), "plan", str(census_path), str(tmp_path / "shards"), "--ordinary", "2",
                              "--exclusive-files", str(exclusive_file), "--timings", str(timings_file)], capture_output=True, text=True, check=False)
    assert planned.returncode == 0, planned.stdout + planned.stderr
    document = json.loads((tmp_path / "shards" / "plan.json").read_text())
    assert [shard["id"] for shard in document["shards"]] == ["exclusive", "ordinary-1", "ordinary-2"]
    assert [shard["count"] for shard in document["shards"]] == [1, 3, 2] or sorted(shard["count"] for shard in document["shards"]) == [1, 2, 3]
    for shard in document["shards"]:
        directory = tmp_path / "pytest" / "shards" / shard["id"]
        directory.mkdir(parents=True)
        done = _pytest(*SAMPLES, f"--census-ids={shard['ids_file']}", f"--certification-report={directory}", f"--junitxml={directory / 'junit.xml'}",
                       f"--collection-census={directory / 'collection-census.json'}")
        (directory / "exit-status.txt").write_text(f"{done.returncode}\n")
        assert done.returncode == 0, done.stdout + done.stderr
    merged = subprocess.run([sys.executable, str(ROOT / "scripts" / "certification_shards.py"), "merge", str(tmp_path / "shards"), str(tmp_path / "pytest")], capture_output=True, text=True, check=False)
    assert merged.returncode == 0, merged.stdout + merged.stderr
    assert merged.stdout.splitlines()[-1].startswith("[shards] PASS:")
    audit = json.loads((tmp_path / "pytest" / "shard-audit.json").read_text())
    assert audit["ok"] and audit["totals"] == {"tests": 6, "failures": 0, "errors": 0, "skipped": 0} and audit["tiers_ran"] == audit["tiers_census"]
    ids = [record["id"] for record in json.loads((tmp_path / "pytest" / "collection-census.json").read_text())["tests"]]
    assert sorted(ids) == sorted(record["id"] for record in census["tests"])


def test_certify_sh_hands_the_sharded_run_to_the_runner_and_derives_the_verdict_only_from_its_merge_audit():
    script = (ROOT / "scripts" / "certify.sh").read_text(encoding="utf-8")
    subprocess.run(["bash", "-n", str(ROOT / "scripts" / "certify.sh")], check=True)
    sharded = script.split("PRD-034 static sharding", 1)[1].split("# --- what the canonical run actually fetched", 1)[0]
    assert "certification_shards.py run" in sharded and '"$OUT/collection/selected.json"' in sharded and '--tmp-root "$CERT_TMP"' in sharded
    # the only PASS of the sharded branch is under the runner's exit status, which is 0 only when the merge audit holds and nothing failed
    assert sharded.count("stage pytest PASS") == 1 and 'if [ "$run_status" = "0" ]; then stage pytest PASS' in sharded
    assert 'CERT_TOPOLOGY:-sharded' in script and 'kill "$SHARD_RUN_PID"' in script


def test_the_runner_terminates_its_shards_when_it_is_interrupted(tmp_path):
    census_path = tmp_path / "selected.json"
    assert _pytest("tests/certification/sample_shard_slow.py", "--collect-only", f"--collection-census={census_path}").returncode == 0
    for name in ("exclusive.json", "timings.json"):
        (tmp_path / name).write_text(json.dumps({"files": {}}))
    import signal
    import time

    runner = subprocess.Popen([sys.executable, str(ROOT / "scripts" / "certification_shards.py"), "run", str(census_path), str(tmp_path / "shards"), str(tmp_path / "pytest"), "--ordinary", "1",
                               "--exclusive-files", str(tmp_path / "exclusive.json"), "--timings", str(tmp_path / "timings.json"), "--tmp-root", str(tmp_path / "tmp"),
                               "--target", "tests/certification/sample_shard_slow.py", "--no-certification"], cwd=str(ROOT), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    progress = tmp_path / "pytest" / "shards" / "ordinary-1" / "progress.json"
    deadline = time.time() + 60
    while time.time() < deadline and not (progress.exists() and json.loads(progress.read_text()).get("pid")):
        time.sleep(0.2)
    pid = json.loads(progress.read_text())["pid"]
    runner.send_signal(signal.SIGTERM)
    assert runner.wait(timeout=60) == 130
    for _ in range(100):
        try:
            os.kill(pid, 0)
        except OSError:
            break
        time.sleep(0.1)
    else:
        pytest.fail("a shard process survived its runner")
