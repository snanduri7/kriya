#!/usr/bin/env python3
"""PRD-034 private-fixture certification gate (F9b): the smallest explicit gate for a mandatory test whose fixture may NOT be redistributed to a hosted runner.

Hosted Linux certification records such a test as EXTERNAL_PRIVATE_FIXTURE_REQUIRED (``@pytest.mark.external_private_fixture``) and its summary reads
HOSTED_PASS_PRIVATE_FIXTURE_REQUIRED at best - never CERTIFIED. This script supplies the other half:

  run     on the owner's machine, with the private fixture bundle: verify the bundle and the fixture identity, run exactly the gated test node(s) from a clean checkout of the
          exact Kriya revision, and write ONE non-source evidence record. Nothing of the fixture is copied into the record: only digests, ids, outcomes and timestamps.
  verify  fail-closed combination: the record must be internally consistent (its evidence digest), match the contract (tests/certification/private_fixtures.json), bind to the
          SAME Kriya revision/tree/config as the hosted certification, discharge exactly the hosted EXTERNAL_PRIVATE_FIXTURE_REQUIRED entries, and the hosted run must otherwise
          have passed. Only then is the final verdict CERTIFIED.

The fixture is never pushed, uploaded, attached as an artifact/release asset or stored as a secret by anything here; ``run`` unpacks the bundle into a temporary directory and
deletes it afterwards.
"""
from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import os
import platform
import shutil
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ElementTree
from typing import Any, Dict, List, Mapping, Optional, Sequence

RECORD_CONTRACT = "kriya.private-fixture-evidence.v1"
FINAL_CONTRACT = "kriya.final-certification.v1"
CONTRACT_PATH = os.path.join("tests", "certification", "private_fixtures.json")
# The files whose bytes are the executable/configuration identity of the gated scenario (read from the git revision, not the working tree).
CONFIG_FILES = ("pyproject.toml", "requirements.txt", "demo-v2/fixtures/brownfield-spring/fixture.env", "demo-v2/scenarios/01-normal-coding/config/kriya.template.yaml",
                "tests/certification/private_fixtures.json")
HOSTED_PASS_STATUS = "HOSTED_PASS_PRIVATE_FIXTURE_REQUIRED"


class GateError(Exception):
    """A fail-closed refusal with the reason."""


def _root() -> str:
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sha256_file(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _git(root: str, *args: str, check: bool = True) -> str:
    done = subprocess.run(["git", "-C", root, *args], capture_output=True, text=True, check=False, timeout=120)
    if check and done.returncode != 0:
        raise GateError(f"git {' '.join(args)} failed: {done.stderr.strip()[:300]}")
    return done.stdout


def _now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def load_contract(root: Optional[str] = None, revision: Optional[str] = None) -> Dict[str, Any]:
    """The private-fixture contract, from the working tree or (when ``revision`` is given) from that git revision."""
    root = root or _root()
    try:
        text = _git(root, "show", f"{revision}:{CONTRACT_PATH}") if revision else open(os.path.join(root, CONTRACT_PATH), encoding="utf-8").read()
        contract = json.loads(text)
    except (OSError, ValueError) as error:
        raise GateError(f"private-fixture contract unreadable: {error}") from error
    if contract.get("contract") != "kriya.private-fixture-gates.v1" or not isinstance(contract.get("gates"), dict) or not contract["gates"]:
        raise GateError("private-fixture contract malformed")
    return contract


def kriya_identity(root: str, revision: str = "HEAD") -> Dict[str, Any]:
    """Kriya's revision, tree and configuration identity as recorded in git for ``revision`` (so a verifier can recompute it from the revision alone)."""
    head = _git(root, "rev-parse", f"{revision}^{{commit}}").strip()
    tree = _git(root, "rev-parse", f"{head}^{{tree}}").strip()
    files = {}
    for name in CONFIG_FILES:
        files[name] = _sha256_bytes(subprocess.run(["git", "-C", root, "show", f"{head}:{name}"], capture_output=True, check=True, timeout=60).stdout)
    config = _sha256_bytes(json.dumps(files, sort_keys=True).encode())
    return {"head": head, "tree": tree, "config_files": files, "config_identity": config}


def _evidence_digest(record: Mapping[str, Any]) -> str:
    body = {key: value for key, value in record.items() if key != "evidence_digest"}
    return _sha256_bytes(json.dumps(body, sort_keys=True, separators=(",", ":")).encode())


def materialize_fixture(bundle: str, gate: Mapping[str, Any], destination: str) -> Dict[str, str]:
    """Verify the frozen bundle and unpack it; fail closed on any identity difference. Returns the verified digests (never content)."""
    if not os.path.isfile(bundle):
        raise GateError(f"fixture bundle not found: {bundle}")
    actual = _sha256_file(bundle)
    if actual != gate["bundle_sha256"]:
        raise GateError(f"bundle sha256 {actual} != required {gate['bundle_sha256']}")
    verified = subprocess.run(["git", "bundle", "verify", bundle], capture_output=True, text=True, check=False, timeout=120)
    if verified.returncode != 0:
        raise GateError(f"git bundle verify failed: {verified.stderr.strip()[:300]}")
    clone = subprocess.run(["git", "clone", "-q", "--no-hardlinks", bundle, destination], capture_output=True, text=True, check=False, timeout=300)
    if clone.returncode != 0:
        raise GateError(f"cloning the bundle failed: {clone.stderr.strip()[:300]}")
    commit = gate["fixture_commit"]
    if subprocess.run(["git", "-C", destination, "cat-file", "-e", f"{commit}^{{commit}}"], capture_output=True, check=False, timeout=60).returncode != 0:
        raise GateError(f"fixture commit {commit} is not in the bundle")
    tree = _git(destination, "rev-parse", f"{commit}^{{tree}}").strip()
    if tree != gate["fixture_tree"]:
        raise GateError(f"fixture tree {tree} != required {gate['fixture_tree']}")
    target = subprocess.run(["git", "-C", destination, "show", f"{commit}:{gate['target_file']}"], capture_output=True, check=False, timeout=60)
    if target.returncode != 0 or _sha256_bytes(target.stdout) != gate["target_sha256"]:
        raise GateError(f"target file {gate['target_file']} digest differs from the required {gate['target_sha256']}")
    return {"bundle_sha256": actual, "fixture_commit": commit, "fixture_tree": tree, "target_file": gate["target_file"], "target_sha256": gate["target_sha256"]}


def _junit_outcomes(path: str) -> Dict[str, str]:
    outcomes: Dict[str, str] = {}
    for case in ElementTree.parse(path).getroot().iter("testcase"):
        status = "passed"
        for child in case:
            if child.tag in ("failure", "error"):
                status = "failed"
            elif child.tag == "skipped" and status == "passed":
                status = "skipped"
        outcomes[f"{case.get('classname', '')}::{case.get('name', '')}"] = status
    return outcomes


def _outcome_of(outcomes: Mapping[str, str], nodeid: str) -> str:
    path, _sep, name = nodeid.partition("::")
    classname = path[:-3].replace("/", ".") if path.endswith(".py") else path.replace("/", ".")
    return outcomes.get(f"{classname}::{name}", "missing")


def run_gate(gate_id: str, bundle: str, root: Optional[str] = None, python: Optional[str] = None) -> Dict[str, Any]:
    """Run the gated node(s) against the verified fixture from ``root`` (which must be a clean checkout) and return the evidence record."""
    root = root or _root()
    gate = load_contract(root)["gates"].get(gate_id)
    if gate is None:
        raise GateError(f"unknown gate {gate_id}")
    if _git(root, "status", "--porcelain", "--untracked-files=no").strip():
        raise GateError("the Kriya checkout is dirty: private evidence binds to an exact revision")
    identity = kriya_identity(root)
    started = _now()
    scratch = tempfile.mkdtemp(prefix="kriya-private-gate-")
    try:
        fixture = os.path.join(scratch, "fixture")
        verified = materialize_fixture(bundle, gate, fixture)
        junit = os.path.join(scratch, "junit.xml")
        env = {**os.environ, "DEMO_V2_BASELINE_SRC": fixture, "KRIYA_CERTIFICATION": "", "TMPDIR": scratch}
        command = [python or sys.executable, "-m", "pytest", "-p", "no:cacheprovider", "-o", "addopts=", "-q", f"--rootdir={root}", f"--junitxml={junit}", *gate["nodeids"]]
        done = subprocess.run(command, cwd=root, env=env, capture_output=True, text=True, check=False, timeout=1800)
        outcomes = _junit_outcomes(junit) if os.path.isfile(junit) else {}
        results = {nodeid: _outcome_of(outcomes, nodeid) for nodeid in gate["nodeids"]}
        finished = _now()
        tail = "\n".join((done.stdout or "").strip().splitlines()[-1:])  # the pytest summary line only (no source)
        record: Dict[str, Any] = {
            "contract": RECORD_CONTRACT, "gate": gate_id,
            "kriya": {"head": identity["head"], "tree": identity["tree"], "config_identity": identity["config_identity"], "config_files": identity["config_files"]},
            "executable": {"python": platform.python_version(), "implementation": platform.python_implementation(), "platform": sys.platform, "machine": platform.machine(),
                           "command": "pytest -o addopts= " + " ".join(gate["nodeids"])},
            "fixture": verified, "results": results, "pytest_exit_code": done.returncode, "pytest_summary": tail,
            "started": started, "finished": finished,
        }
        record["evidence_digest"] = _evidence_digest(record)
        return record
    finally:
        shutil.rmtree(scratch, ignore_errors=True)


def _load_json(path: str, what: str) -> Any:
    try:
        with open(path, encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, ValueError) as error:
        raise GateError(f"{what} unreadable: {error}") from error


def verify_binding(hosted_dir: str, record: Mapping[str, Any], root: Optional[str] = None) -> Dict[str, Any]:
    """The fail-closed combination. Returns the final verdict document; ``status`` is CERTIFIED only when every check passes."""
    root = root or _root()
    problems: List[str] = []

    def need(condition: bool, message: str) -> None:
        if not condition:
            problems.append(message)

    summary = _load_json(os.path.join(hosted_dir, "certification-summary.json"), "hosted certification summary")
    release = _load_json(os.path.join(hosted_dir, "release-identity.json"), "hosted release identity")
    environment = _load_json(os.path.join(hosted_dir, "environment.json"), "hosted environment")
    need(summary.get("status") == HOSTED_PASS_STATUS, f"hosted status is {summary.get('status')!r}, not {HOSTED_PASS_STATUS} (every hosted requirement must have passed)")
    need(not summary.get("problems"), f"hosted problems: {summary.get('problems')}")
    pending = summary.get("private_fixture_pending") or []
    need(bool(pending), "hosted run records no EXTERNAL_PRIVATE_FIXTURE_REQUIRED entry: nothing to discharge")

    need(record.get("contract") == RECORD_CONTRACT, "evidence record has the wrong contract id")
    need(isinstance(record.get("evidence_digest"), str) and record.get("evidence_digest") == _evidence_digest(record), "evidence digest does not match the record (tampered or incomplete)")
    kriya = record.get("kriya") or {}
    hosted_revision = ((release.get("kriya") or {}).get("revision"))
    need(((release.get("kriya") or {}).get("dirty")) is False and bool(hosted_revision), "hosted release identity is not pinned to a clean revision")
    need(environment.get("git_revision") == hosted_revision, "hosted environment revision differs from the hosted release identity")
    need(kriya.get("head") == hosted_revision, f"private record Kriya head {kriya.get('head')} != hosted revision {hosted_revision}")
    try:
        expected = kriya_identity(root, hosted_revision) if hosted_revision else {}
    except (GateError, subprocess.SubprocessError, OSError) as error:
        expected = {}
        problems.append(f"cannot recompute the Kriya identity of {hosted_revision}: {error}")
    if expected:
        need(kriya.get("tree") == expected["tree"], "private record Kriya tree differs from the tree of the hosted revision")
        need(kriya.get("config_identity") == expected["config_identity"], "private record config identity differs from the hosted revision's")
        need(kriya.get("config_files") == expected["config_files"], "private record config file digests differ from the hosted revision's")
    try:
        gates = load_contract(root, hosted_revision)["gates"] if hosted_revision else {}
    except GateError as error:
        gates = {}
        problems.append(str(error))
    gate = gates.get(record.get("gate"))
    need(gate is not None, f"gate {record.get('gate')!r} is not in the contract of the hosted revision")
    if gate is not None:
        fixture = record.get("fixture") or {}
        for key, field in (("fixture_commit", "fixture_commit"), ("fixture_tree", "fixture_tree"), ("bundle_sha256", "bundle_sha256"), ("target_file", "target_file"), ("target_sha256", "target_sha256")):
            need(fixture.get(key) == gate[field], f"fixture {key} {fixture.get(key)!r} != contract {gate[field]!r}")
        results = record.get("results") or {}
        need(sorted(results) == sorted(gate["nodeids"]), "recorded node ids differ from the contract's")
        need(all(results.get(node) == "passed" for node in gate["nodeids"]), f"scenario result is not PASSED for every node: {results}")
        need(record.get("pytest_exit_code") == 0, f"private pytest exit code {record.get('pytest_exit_code')} != 0")
        mine = sorted(item["id"] for item in pending if item.get("gate") == record.get("gate"))
        need(mine == sorted(gate["nodeids"]), f"hosted EXTERNAL_PRIVATE_FIXTURE_REQUIRED entries {mine} != the gate's nodes {sorted(gate['nodeids'])}")
        need(all(item.get("gate") == record.get("gate") for item in pending), "hosted run has a pending private fixture for a gate this record does not discharge")
    for key in ("started", "finished"):
        need(isinstance(record.get(key), str) and bool(record.get(key)), f"record is missing {key}")
    return {"contract": FINAL_CONTRACT, "status": "NOT_CERTIFIED" if problems else "CERTIFIED", "problems": problems, "hosted_revision": hosted_revision,
            "hosted_summary_sha256": _sha256_file(os.path.join(hosted_dir, "certification-summary.json")),
            "private_evidence_digest": record.get("evidence_digest"), "gate": record.get("gate")}


def main(argv: Sequence[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run", help="run the gated node(s) against the private fixture and write the evidence record")
    run.add_argument("--gate", default="PRD034-F9B-SCENARIO01")
    run.add_argument("--bundle", required=True)
    run.add_argument("--out", required=True)
    run.add_argument("--python")
    verify = sub.add_parser("verify", help="bind a private evidence record to a hosted certification and give the final verdict")
    verify.add_argument("--hosted", required=True, help="the hosted certification output directory (certification-out/ci)")
    verify.add_argument("--record", required=True)
    verify.add_argument("--out")
    args = parser.parse_args(list(argv))
    try:
        if args.command == "run":
            record = run_gate(args.gate, args.bundle, python=args.python)
            with open(args.out, "w", encoding="utf-8") as handle:
                json.dump(record, handle, indent=2, sort_keys=True)
                handle.write("\n")
            ok = all(value == "passed" for value in record["results"].values()) and record["pytest_exit_code"] == 0
            print(f"[private-gate] {record['gate']}: {'PASS' if ok else 'FAIL'} {json.dumps(record['results'], sort_keys=True)} evidence {record['evidence_digest']}")
            return 0 if ok else 1
        record = _load_json(args.record, "private evidence record")
        verdict = verify_binding(args.hosted, record)
        if args.out:
            with open(args.out, "w", encoding="utf-8") as handle:
                json.dump(verdict, handle, indent=2, sort_keys=True)
                handle.write("\n")
        print(f"[private-gate] final: {verdict['status']}" + "".join(f"\n  - {problem}" for problem in verdict["problems"]))
        return 0 if verdict["status"] == "CERTIFIED" else 1
    except GateError as error:
        print(f"[private-gate] REFUSED: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
