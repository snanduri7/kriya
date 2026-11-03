#!/usr/bin/env python3
"""PRD-034: deterministic static sharding of the canonical certification population, and the merge that proves the shards are the population.

The canonical command is `pytest -o addopts= -m "not live_model"` over ONE authoritative census (stage `collection`, selected.json). The serial run took 4h58m (c3-run4); the
population does not change when it is cut into disjoint shards that run side by side, so long as:

* the cut is a pure function of the census, the explicit shard count and two digest-recorded input files (no timing from the run itself, no live rebalancing);
* every test of the Docker / JVM / Maven / real-toolchain / Semgrep tiers, and every file measured to start containers, is in ONE exclusive shard, because their leak checks list
  every kriya container on the daemon (two containers-users at once see each other's);
* the merge re-derives everything from the shards' own artifacts: the union of the shards equals the census, no node is in two shards, every node has exactly one terminal
  result, the tier counts of what ran equal the tier counts of the census. A shard's exit status is never a verdict: PASS is derived from the merged junit only.

usage:
  certification_shards.py plan CENSUS.json OUT_DIR --ordinary N --exclusive-files FILE --timings FILE
  certification_shards.py run CENSUS.json OUT_DIR PYTEST_DIR --ordinary N --exclusive-files FILE --timings FILE --tmp-root DIR [--target PATH]... [--no-certification]
  certification_shards.py merge OUT_DIR PYTEST_DIR
  certification_shards.py aggregate PYTEST_DIR [--expect N] [--once]   (keeps PYTEST_DIR/progress.json = the sum of the shards' progress files)
  certification_shards.py terminate PYTEST_DIR                 (SIGTERM the shards that have not finished)
  certification_shards.py docker-shim DIR REAL_DOCKER          (writes DIR/docker: refuses container-mutating subcommands, logs every call)
"""
from __future__ import annotations

import copy
import hashlib
import json
import os
import signal
import stat
import subprocess
import sys
import threading
import time
import xml.etree.ElementTree as ElementTree
from typing import Any, Dict, List, Mapping, Optional, Sequence

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "tests"))

from _certification_collection import _junit_key, tiers_of  # noqa: E402  (path set above)

PLAN_VERSION = 1
EXCLUSIVE = "exclusive"
SHARD_FILES = ("junit.xml", "collection-census.json", "progress.json", "skips.json", "exit-status.txt")
# docker subcommands that only READ (a leak check lists containers; a doctor reads the daemon). Everything else is refused in an ordinary shard (fail closed).
DOCKER_READ_ONLY = {"ps": None, "info": None, "version": None, "images": None, "inspect": None, "--version": None, "-v": None,
                    "image": {"ls", "list", "inspect"}, "container": {"ls", "list", "inspect", "ps"},
                    "network": {"ls", "list", "inspect"}, "volume": {"ls", "list", "inspect"}}


def _digest(payload: Any) -> str:
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def _file_digest(path: str) -> str:
    with open(path, "rb") as handle:
        return hashlib.sha256(handle.read()).hexdigest()


def _load(path: str) -> Any:
    with open(path, "r", encoding="utf-8") as handle:
        return json.load(handle)


def _write(path: str, payload: Any) -> None:
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, sort_keys=True)
        handle.write("\n")


def file_of(nodeid: str) -> str:
    return nodeid.split("::", 1)[0]


# --------------------------------------------------------------------------------------------------------------------------------------------- plan

def plan(census: Mapping[str, Any], *, ordinary_shards: int, exclusive_files: Mapping[str, str], timings: Mapping[str, float]) -> Dict[str, Any]:
    """The assignment, a pure function of its arguments. Ordinary files are cut file-granular (module/class fixtures never straddle shards) by longest-processing-time packing."""
    if ordinary_shards < 1:
        raise ValueError("at least one ordinary shard")
    records = census["tests"]
    ids = [record["id"] for record in records]
    if len(ids) != len(set(ids)):
        raise ValueError("the census itself contains a duplicate node id")
    exclusive_ids: List[str] = []
    by_file: Dict[str, List[str]] = {}
    reasons: Dict[str, str] = {}
    for record in records:
        path = file_of(record["id"])
        tiers = tiers_of(record)
        if tiers or path in exclusive_files:
            exclusive_ids.append(record["id"])
            reasons[path] = ("tier " + "/".join(tiers)) if tiers else f"measured container user: {exclusive_files[path]}"
        else:
            by_file.setdefault(path, []).append(record["id"])
    weights = {path: float(timings.get(path, 0.5 * len(members))) for path, members in by_file.items()}
    loads = [0.0] * ordinary_shards
    assigned: List[List[str]] = [[] for _ in range(ordinary_shards)]
    for path in sorted(by_file, key=lambda name: (-weights[name], name)):
        target = min(range(ordinary_shards), key=lambda index: (loads[index], index))
        assigned[target].extend(by_file[path])
        loads[target] += weights[path]
    shards = [{"id": EXCLUSIVE, "kind": EXCLUSIVE, "ids": sorted(exclusive_ids), "weight_seconds": None}]
    shards += [{"id": f"ordinary-{index + 1}", "kind": "ordinary", "ids": sorted(members), "weight_seconds": round(loads[index], 1)}
               for index, members in enumerate(assigned)]
    for shard in shards:
        shard["count"] = len(shard["ids"])
    return {"version": PLAN_VERSION, "census_digest": _digest(sorted(ids)), "census_count": len(ids), "ordinary_shards": ordinary_shards,
            "exclusive_files": dict(sorted(exclusive_files.items())), "exclusive_reasons": dict(sorted(reasons.items())), "shards": shards}


def check_partition(shard_plan: Mapping[str, Any], census: Mapping[str, Any]) -> List[str]:
    """Union == census exactly, pairwise disjoint, no empty shard."""
    problems: List[str] = []
    expected = [record["id"] for record in census["tests"]]
    seen: Dict[str, str] = {}
    for shard in shard_plan["shards"]:
        if not shard["ids"]:
            problems.append(f"shard {shard['id']} is empty")
        for nodeid in shard["ids"]:
            if nodeid in seen:
                problems.append(f"{nodeid} is in both {seen[nodeid]} and {shard['id']}")
            seen[nodeid] = shard["id"]
    problems += [f"missing from every shard: {nodeid}" for nodeid in sorted(set(expected) - set(seen))[:20]]
    problems += [f"in a shard but not in the census: {nodeid}" for nodeid in sorted(set(seen) - set(expected))[:20]]
    if shard_plan["census_digest"] != _digest(sorted(expected)):
        problems.append("the plan was made from a different census (digest mismatch)")
    return problems


def write_plan(shard_plan: Mapping[str, Any], out_dir: str, *, inputs: Mapping[str, str]) -> Dict[str, Any]:
    from _census_selection import write_ids

    os.makedirs(out_dir, exist_ok=True)
    document = {key: value for key, value in shard_plan.items() if key != "shards"}
    document["inputs"] = dict(inputs)
    document["shards"] = []
    for shard in shard_plan["shards"]:
        ids_path = os.path.join(out_dir, f"{shard['id']}.ids.json")
        write_ids(ids_path, shard["ids"])
        document["shards"].append({key: value for key, value in shard.items() if key != "ids"} | {"ids_file": ids_path, "ids_digest": _digest(shard["ids"])})
    document["plan_digest"] = _digest(document)
    _write(os.path.join(out_dir, "plan.json"), document)
    return document


# --------------------------------------------------------------------------------------------------------------------------------------------- merge

def _shard_ids(document: Mapping[str, Any], shard: Mapping[str, Any]) -> List[str]:
    from _census_selection import read_ids

    return read_ids(shard["ids_file"])


def _count_tiers(records: Sequence[Mapping[str, Any]]) -> Dict[str, int]:
    counts: Dict[str, int] = {}
    for record in records:
        for tier in tiers_of(record):
            counts[tier] = counts.get(tier, 0) + 1
    return dict(sorted(counts.items()))


def _cases(path: str) -> List[ElementTree.Element]:
    return list(ElementTree.parse(path).getroot().iter("testcase"))


def _status(case: ElementTree.Element) -> str:
    status = "passed"
    for child in case:
        if child.tag in ("failure", "error"):
            return "failed"
        if child.tag == "skipped":
            status = "skipped"
    return status


def merge(out_dir: str, pytest_dir: str) -> Dict[str, Any]:
    """Audit the shards against the plan and write the merged artifacts the downstream stages already read (junit.xml, collection-census.json, skips.json, progress.json)."""
    document = _load(os.path.join(out_dir, "plan.json"))
    authoritative = _load(document["inputs"]["census"])
    problems: List[str] = []
    probe = dict(document)
    probe["shards"] = [dict(shard, ids=_shard_ids(document, shard)) for shard in document["shards"]]
    problems += check_partition(probe, authoritative)
    shards_report: Dict[str, Any] = {}
    merged_cases: List[ElementTree.Element] = []
    skip_payloads: List[Mapping[str, Any]] = []
    executed_records: List[Mapping[str, Any]] = []
    for shard in probe["shards"]:
        directory = os.path.join(pytest_dir, "shards", shard["id"])
        report: Dict[str, Any] = {"planned": len(shard["ids"])}
        shards_report[shard["id"]] = report
        missing_files = [name for name in SHARD_FILES if not os.path.exists(os.path.join(directory, name))]
        if missing_files:
            problems.append(f"shard {shard['id']}: no {', '.join(missing_files)} (the shard did not finish)")
            continue
        own_census = _load(os.path.join(directory, "collection-census.json"))
        own_ids = [record["id"] for record in own_census["tests"]]
        if sorted(own_ids) != sorted(shard["ids"]):
            problems.append(f"shard {shard['id']}: collected {len(own_ids)} != planned {len(shard['ids'])} (symmetric difference "
                            f"{len(set(own_ids) ^ set(shard['ids']))})")
        cases = _cases(os.path.join(directory, "junit.xml"))
        keys = [(case.get("classname", ""), case.get("name", "")) for case in cases]
        wanted = {_junit_key(nodeid) for nodeid in shard["ids"]}
        if len(keys) != len(set(keys)):
            problems.append(f"shard {shard['id']}: {len(keys) - len(set(keys))} node(s) have more than one terminal result")
        if set(keys) != wanted:
            problems.append(f"shard {shard['id']}: results do not match the plan: {len(wanted - set(keys))} missing, {len(set(keys) - wanted)} unplanned")
        statuses = [_status(case) for case in cases]
        exit_code = open(os.path.join(directory, "exit-status.txt"), encoding="utf-8").read().strip()
        failing = sum(1 for status in statuses if status == "failed")
        # the exit status is a CONSISTENCY check on the shard, never the verdict (0 iff nothing failed or errored)
        if (exit_code == "0") != (failing == 0):
            problems.append(f"shard {shard['id']}: exit status {exit_code} contradicts its junit ({failing} failed/errored)")
        progress = _load(os.path.join(directory, "progress.json"))
        if not progress.get("finished") or progress.get("done") != len(shard["ids"]):
            problems.append(f"shard {shard['id']}: its progress file says done {progress.get('done')}/{len(shard['ids'])}, finished={progress.get('finished')}")
        shim_log = os.path.join(directory, "docker-shim.jsonl")
        refused = [json.loads(line) for line in open(shim_log, encoding="utf-8")] if os.path.exists(shim_log) else []
        refused = [row for row in refused if row.get("refused")]
        if refused:
            problems.append(f"shard {shard['id']}: {len(refused)} container-mutating docker call(s) in an ordinary shard, first from {refused[0].get('test')}: "
                            f"{refused[0].get('argv')}")
        report.update(results=len(cases), passed=statuses.count("passed"), failed=failing, skipped=statuses.count("skipped"), exit_status=exit_code,
                      docker_refusals=len(refused))
        merged_cases += [copy.deepcopy(case) for case in cases]
        skip_payloads.append(_load(os.path.join(directory, "skips.json")))
        executed_records += own_census["tests"]  # what the shard itself collected and classified, not what the plan says it should have
    census_tiers, ran_tiers = _count_tiers(authoritative["tests"]), _count_tiers(executed_records)
    if census_tiers != ran_tiers:
        problems.append(f"tier counts of what ran {ran_tiers} != tier counts of the census {census_tiers}")
    totals = {"tests": len(merged_cases), "failures": 0, "errors": 0, "skipped": 0}
    for case in merged_cases:
        if any(child.tag == "failure" for child in case):
            totals["failures"] += 1
        elif any(child.tag == "error" for child in case):
            totals["errors"] += 1
        elif any(child.tag == "skipped" for child in case):
            totals["skipped"] += 1
    suite = ElementTree.Element("testsuite", {"name": "pytest-sharded", **{key: str(value) for key, value in totals.items()},
                                              "time": f"{sum(float(case.get('time', 0) or 0) for case in merged_cases):.3f}"})
    suite.extend(merged_cases)
    root = ElementTree.Element("testsuites")
    root.append(suite)
    os.makedirs(pytest_dir, exist_ok=True)
    ElementTree.ElementTree(root).write(os.path.join(pytest_dir, "junit.xml"), encoding="utf-8", xml_declaration=True)
    _write(os.path.join(pytest_dir, "collection-census.json"), authoritative)  # the ONE census, byte-for-byte what the collection stage audited
    _write(os.path.join(pytest_dir, "skips.json"), _merge_skips(skip_payloads))
    passed = totals["tests"] - totals["failures"] - totals["errors"] - totals["skipped"]
    verdict_ok = not problems and totals["failures"] == 0 and totals["errors"] == 0
    summary = (f"{totals['failures']} failed, {passed} passed, {totals['skipped']} skipped, {totals['errors']} errors across {len(probe['shards'])} shards "
               f"(merge audit {'OK' if not problems else 'FAILED: ' + str(len(problems)) + ' problem(s)'})")
    audit = {"ok": verdict_ok, "problems": problems, "shards": shards_report, "totals": totals, "tiers_census": census_tiers, "tiers_ran": ran_tiers,
             "plan_digest": document["plan_digest"], "summary": summary}
    _write(os.path.join(pytest_dir, "shard-audit.json"), audit)
    return audit


def _merge_skips(payloads: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
    skips = sorted((item for payload in payloads for item in payload.get("skips", [])), key=lambda item: item["nodeid"])
    pending = sorted((item for payload in payloads for item in payload.get("platform_pending", [])), key=lambda item: item["nodeid"])
    modes = {payload.get("certification_mode") for payload in payloads}
    return {"certification_mode": modes == {True}, "skipped": len(skips), "platform": payloads[0].get("platform") if payloads else sys.platform,
            "unexpected": sum(int(payload.get("unexpected", 0)) for payload in payloads), "platform_pending": pending, "skips": skips}


# --------------------------------------------------------------------------------------------------------------------------------------------- run

def pytest_command(extra: Sequence[str]) -> List[str]:
    executable = os.path.join(os.path.dirname(sys.executable), "pytest")
    return ([executable] if os.path.exists(executable) else [sys.executable, "-m", "pytest"]) + ["-o", "addopts=", "-m", "not live_model", "-p", "no:cacheprovider", "-q",
                                                                                                  "--certification", *extra]


def run(census_path: str, out_dir: str, pytest_dir: str, *, ordinary: int, exclusive_files: str, timings: str, tmp_root: str, targets: Sequence[str] = (),
        certification: bool = True) -> int:
    """Plan, run every shard as its own pytest process side by side, keep one aggregate progress file, merge and audit. -> 0 only when the audit holds and nothing failed."""
    census = _load(census_path)
    document = plan(census, ordinary_shards=ordinary, exclusive_files=_load(exclusive_files)["files"], timings=_load(timings)["files"])
    problems = check_partition(document, census)
    if problems:
        print("[shards] PARTITION PROBLEM: " + "; ".join(problems[:5]))
        return 2
    written = write_plan(document, out_dir, inputs={"census": os.path.abspath(census_path), "census_sha256": _file_digest(census_path), "exclusive_files": exclusive_files,
                                                    "exclusive_files_sha256": _file_digest(exclusive_files), "timings": timings, "timings_sha256": _file_digest(timings)})
    summary = "plan " + written["plan_digest"][:16] + ": " + ", ".join(f"{s['id']}={s['count']}" for s in written["shards"])
    print("[shards] " + summary, flush=True)
    os.makedirs(os.path.join(pytest_dir, "shards"), exist_ok=True)
    lock_dir, shim_dir = os.path.join(tmp_root, "locks"), os.path.join(tmp_root, "docker-shim")
    os.makedirs(lock_dir, exist_ok=True)
    real_docker = _which("docker")
    if real_docker:
        write_docker_shim(shim_dir, real_docker)
    commands = [f"topology: sharded; {summary}", f"shared by every shard: KRIYA_CERT_LOCK_DIR={lock_dir} and the frozen Maven / PyPI servers (their logs name shard + node)"]
    processes: Dict[str, subprocess.Popen] = {}
    handles = []
    for index, shard in enumerate(written["shards"], start=1):
        shard_dir, shard_tmp = os.path.join(pytest_dir, "shards", shard["id"]), os.path.join(tmp_root, f"s{index}")
        os.makedirs(shard_dir, exist_ok=True)
        os.makedirs(shard_tmp, exist_ok=True)
        command = pytest_command(list(targets) + [f"--certification-report={shard_dir}", f"--census-ids={shard['ids_file']}",
                                                  f"--collection-census={os.path.join(shard_dir, 'collection-census.json')}", f"--junitxml={os.path.join(shard_dir, 'junit.xml')}",
                                                  "--durations=50"])
        if not certification:
            command.remove("--certification")
        environment = {**os.environ, "TMPDIR": shard_tmp, "KRIYA_CERT_LOCK_DIR": lock_dir}
        if shard["kind"] != EXCLUSIVE and real_docker:
            environment.update(PATH=shim_dir + os.pathsep + environment.get("PATH", ""), KRIYA_DOCKER_SHIM_LOG=os.path.join(shard_dir, "docker-shim.jsonl"))
        commands.append(f"{shard['id']}: TMPDIR={shard_tmp}{' PATH=<docker guard>:$PATH' if shard['kind'] != EXCLUSIVE else ''} {' '.join(command)}")
        handle = open(os.path.join(shard_dir, "pytest.txt"), "w", encoding="utf-8")  # noqa: SIM115 - closed after the shard exits
        handles.append(handle)
        processes[shard["id"]] = subprocess.Popen(command, stdout=handle, stderr=subprocess.STDOUT, env=environment, cwd=os.getcwd())  # noqa: S603
    with open(os.path.join(pytest_dir, "command.txt"), "w", encoding="utf-8") as handle:
        handle.write("\n".join(commands) + "\n")

    def _terminate(*_args):
        for process in processes.values():
            if process.poll() is None:
                process.terminate()
        raise SystemExit(130)

    signal.signal(signal.SIGTERM, _terminate)
    signal.signal(signal.SIGINT, _terminate)
    stop = threading.Event()
    watcher = threading.Thread(target=aggregate, args=(pytest_dir,), kwargs={"expect": len(processes), "stop": stop}, daemon=True)
    watcher.start()
    for shard_id, process in processes.items():
        status = process.wait()
        with open(os.path.join(pytest_dir, "shards", shard_id, "exit-status.txt"), "w", encoding="utf-8") as handle:
            handle.write(f"{status}\n")
    stop.set()
    watcher.join(timeout=30)
    aggregate(pytest_dir, expect=len(processes), once=True)  # the final snapshot: finished=True with the true totals (the loop stops between two samples)
    for handle in handles:
        handle.close()
    audit = merge(out_dir, pytest_dir)
    lines = []
    for shard_id in processes:
        try:
            tail = open(os.path.join(pytest_dir, "shards", shard_id, "pytest.txt"), encoding="utf-8").read().strip().splitlines()[-1]
        except (OSError, IndexError):
            tail = "(no output)"
        lines.append(f"[{shard_id}] {tail}")
    lines += [f"  - {problem}" for problem in audit["problems"][:20]]
    lines.append(f"[shards] {'PASS' if audit['ok'] else 'FAIL'}: {audit['summary']}")
    with open(os.path.join(os.path.dirname(pytest_dir), "pytest.txt"), "w", encoding="utf-8") as handle:
        handle.write("\n".join(lines) + "\n")
    print("\n".join(lines[-1:]))
    return 0 if audit["ok"] else 1


def _which(name: str) -> Optional[str]:
    import shutil

    return shutil.which(name)


# ------------------------------------------------------------------------------------------------------------------------------------------ progress

def aggregate_snapshot(shards: Mapping[str, Mapping[str, Any]], *, now: float, pid: int, started: float, history: List[List[float]]) -> Dict[str, Any]:
    """One progress document over the shard documents, in the schema `tests/_certification_progress.py` reads. `current` is the EXCLUSIVE shard's node: it is the only shard
    that starts containers or reaches the frozen repositories, so the Maven/PyPI servers attribute a request to it."""
    total = sum(item["total"] for item in shards.values())
    done = sum(item["done"] for item in shards.values())
    exclusive = shards.get(EXCLUSIVE, {})
    if not history or now - history[-1][0] >= 30.0:
        history.append([now, done])
    finished = all(item.get("finished") for item in shards.values())
    return {"pid": pid, "started": started, "updated": now, "total": total, "done": done,
            "passed": sum(item["passed"] for item in shards.values()), "failed": sum(item["failed"] for item in shards.values()),
            "errors": sum(item["errors"] for item in shards.values()), "skipped": sum(item["skipped"] for item in shards.values()),
            "failed_nodes": [f"{name}:{node}" for name, item in sorted(shards.items()) for node in item.get("failed_nodes", [])][:50],
            "current": exclusive.get("current"), "current_shard": EXCLUSIVE, "current_tiers": exclusive.get("current_tiers", []),
            "current_started": exclusive.get("current_started"), "longest_node_s": max((item["longest_node_s"] for item in shards.values()), default=0.0),
            "history": history[-1440:], "finished": finished, "exit_status": None,
            "write_errors": sum(item.get("write_errors", 0) for item in shards.values()),
            "shards": {name: {key: item.get(key) for key in ("pid", "total", "done", "passed", "failed", "errors", "skipped", "current", "finished")}
                       for name, item in sorted(shards.items())}}


def aggregate(pytest_dir: str, *, expect: int = 1, once: bool = False, interval: float = 10.0, stop: Optional[threading.Event] = None) -> int:
    from _certification_progress import _atomic_write, read_progress

    started, history = time.time(), []
    while not (stop is not None and stop.is_set()):
        shards = {}
        for name in sorted(os.listdir(os.path.join(pytest_dir, "shards"))) if os.path.isdir(os.path.join(pytest_dir, "shards")) else []:
            snapshot = read_progress(os.path.join(pytest_dir, "shards", name))
            if snapshot is not None:
                shards[name] = snapshot
        if len(shards) >= expect:  # never declare "finished" before every planned shard has started reporting
            document = aggregate_snapshot(shards, now=time.time(), pid=os.getpid(), started=started, history=history)
            _atomic_write(os.path.join(pytest_dir, "progress.json"), document)
            if document["finished"] or once:
                return 0
        elif once:
            return 1
        time.sleep(interval) if stop is None else stop.wait(interval)
    return 0


def terminate(pytest_dir: str) -> int:
    """SIGTERM every shard whose progress file says it has not finished (certify.sh's exit trap: an interrupted run must not leave orphan pytest processes)."""
    from _certification_progress import read_progress

    root = os.path.join(pytest_dir, "shards")
    for name in sorted(os.listdir(root)) if os.path.isdir(root) else []:
        snapshot = read_progress(os.path.join(root, name))
        if snapshot and not snapshot.get("finished"):
            try:
                os.kill(int(snapshot["pid"]), signal.SIGTERM)
            except OSError:
                pass
    return 0


# ----------------------------------------------------------------------------------------------------------------------------------------- docker shim

SHIM_TEMPLATE = r'''#!/bin/sh
# PRD-034 ordinary-shard guard: the Docker daemon belongs to the exclusive shard. Read-only subcommands pass through; every other one is REFUSED (exit 125) and logged with the
# test that made it, so a mis-classified test is a deterministic failure and a merge-audit problem, never a flaky cross-shard leak check.
REAL=%(real)s
LOG="${KRIYA_DOCKER_SHIM_LOG:-/dev/null}"
sub="$1"
second="$2"
case "$sub" in
%(cases)s
esac
printf '{"refused": true, "test": "%%s", "argv": "%%s"}\n' "$PYTEST_CURRENT_TEST" "$*" >> "$LOG"
echo "docker $*: refused - this shard is not the exclusive Docker shard (certification_shards.py docker-shim)" >&2
exit 125
'''


def write_docker_shim(directory: str, real_docker: str) -> str:
    cases: List[str] = []
    for sub, seconds in DOCKER_READ_ONLY.items():
        if seconds is None:
            cases.append(f'  {sub}) printf \'{{"refused": false}}\\n\' >> "$LOG"; exec "$REAL" "$@" ;;')
        else:
            pattern = "|".join(sorted(seconds))
            cases.append(f'  {sub}) case "$second" in {pattern}) printf \'{{"refused": false}}\\n\' >> "$LOG"; exec "$REAL" "$@" ;; esac ;;')
    os.makedirs(directory, exist_ok=True)
    path = os.path.join(directory, "docker")
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(SHIM_TEMPLATE % {"real": json.dumps(real_docker), "cases": "\n".join(cases)})
    os.chmod(path, os.stat(path).st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return path


# -------------------------------------------------------------------------------------------------------------------------------------------- main

def _option(argv: Sequence[str], name: str) -> Optional[str]:
    return argv[argv.index(name) + 1] if name in argv else None


def main(argv: Sequence[str]) -> int:
    if len(argv) >= 7 and argv[1] == "plan":
        census_path, out_dir = argv[2], argv[3]
        exclusive_doc, timing_doc = _load(_option(argv, "--exclusive-files")), _load(_option(argv, "--timings"))
        census = _load(census_path)
        document = plan(census, ordinary_shards=int(_option(argv, "--ordinary") or 3), exclusive_files=exclusive_doc["files"], timings=timing_doc["files"])
        problems = check_partition(document, census)
        if problems:
            print("[shards] PARTITION PROBLEM: " + "; ".join(problems[:5]), file=sys.stderr)
            return 1
        written = write_plan(document, out_dir, inputs={"census": os.path.abspath(census_path), "census_sha256": _file_digest(census_path),
                                                        "exclusive_files": _option(argv, "--exclusive-files") or "", "exclusive_files_sha256": _file_digest(_option(argv, "--exclusive-files") or ""),
                                                        "timings": _option(argv, "--timings") or "", "timings_sha256": _file_digest(_option(argv, "--timings") or "")})
        print("[shards] plan " + written["plan_digest"][:16] + ": " + ", ".join(f"{s['id']}={s['count']}" for s in written["shards"]))
        return 0
    if len(argv) >= 6 and argv[1] == "run":
        return run(argv[2], argv[3], argv[4], ordinary=int(_option(argv, "--ordinary") or 3), exclusive_files=_option(argv, "--exclusive-files") or "",
                   timings=_option(argv, "--timings") or "", tmp_root=_option(argv, "--tmp-root") or os.environ.get("TMPDIR", "/tmp"),
                   targets=[argv[i + 1] for i, arg in enumerate(argv) if arg == "--target"], certification="--no-certification" not in argv)
    if len(argv) == 4 and argv[1] == "merge":
        audit = merge(argv[2], argv[3])
        print("".join(f"  - {problem}\n" for problem in audit["problems"][:20]) + f"[shards] {'PASS' if audit['ok'] else 'FAIL'}: {audit['summary']}")
        return 0 if audit["ok"] else 1
    if len(argv) >= 3 and argv[1] == "aggregate":
        return aggregate(argv[2], expect=int(_option(argv, "--expect") or 1), once="--once" in argv)
    if len(argv) == 3 and argv[1] == "terminate":
        return terminate(argv[2])
    if len(argv) == 4 and argv[1] == "docker-shim":
        print(write_docker_shim(argv[2], argv[3]))
        return 0
    print(__doc__, file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
