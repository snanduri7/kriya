"""GR-R1 canonical Graphify live run: pre-run identity check. NO model call (no inference, no embedding).

Every binding is checked with Kriya's own functions from the installed a049c02 build; any mismatch exits 1.
usage: venv-gr1/bin/python gr1_prerun_check.py <out.json>"""
import hashlib
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import kriya
from kriya.build_info import version_report
from kriya.workflow import acceptance_oracle as ao
from kriya.workflow.acceptance_approval import load_approval, runner_contract_digest
from kriya.workflow.checkpoint import compute_workspace_content_hash
from kriya.workflow.requirement_contract import load_requirement_contract
from kriya.workflow.requirements import goal_identity

HOME = Path.home()
D = HOME / "kriya-m1-live"
WS = D / "ws" / "gr1-graphify"
GOAL = HOME / "kriya-live-validation/graphify-canonical-853aa43/goal.txt"
P = HOME / "kriya-wt/p3d/handover/evidence/gr0/graphify_prospective"
CONTRACT, SUITE, APPROVAL = (P / "graphify_requirements_PROPOSED_unapproved.json", P / "kriya_acceptance_graphify.py",
                             P / "graphify_approval_APPROVED_owner.json")
CONFIG = D / "config/gr1-graphify.yaml"
EXPECT = {
    "kriya_commit": "a049c026f7c23b975eb437411105cf81f018a1ab",
    "graphify_head": "67f99bd0059dd1bac9e44382907ef9f10098b39f",
    "graphify_tree": "975f0667afa68908d7ab758551cb8cec6a9456f2",
    "content_hash": "bba7689c625508feb81b3e8c024c8ec52ea0f844db0462dd789d5bb803c7600b",
    "goal_file_sha256": "f96bf5a3400abdb3eddf4a6d39d14f7022cde66cf1621525f3edac759bfcb823",
    "contract_sha256": "addc635a909fa91be765f43b2e18143668b58d68416871614803a563c382ce6b",
    "acceptance_sha256": "d5afe6db35e92a7b8fbf0b1eceb474df0200bef1d3dd953d11248473d22a1dda",
    "approval_sha256": "5f9cc2a9df55579afc5928d94b236bb423c4108c7ddd37faf276c10841e7e5be",
    "requirement_set_sha256": "429613291934b2c6f924c43c0c452b0cfe1a74a826d7b80e3fb549b220ee5332",
    "runner_contract_sha256": "806f453647de48b218b4a1ec7e46a9e2a99817203986a0959d94f8d09b7db259",
}


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def git(*args):
    return subprocess.run(["git", "-C", str(WS), *args], capture_output=True, text=True, check=False).stdout.strip()


def main(out):
    checks, facts = {}, {}
    info = version_report()
    facts["kriya_install"] = os.path.dirname(kriya.__file__)
    checks["KRIYA REVISION"] = info.get("commit") == EXPECT["kriya_commit"] and info.get("dirty") is False
    checks["GRAPHIFY HEAD"] = git("rev-parse", "HEAD") == EXPECT["graphify_head"] and git(
        "rev-parse", "HEAD^{tree}") == EXPECT["graphify_tree"]
    status = git("status", "--porcelain", "--ignored")
    facts["git_status"] = status
    checks["WORKSPACE CLEAN"] = status == "" and compute_workspace_content_hash(str(WS)) == EXPECT["content_hash"]
    checks["UNTRACKED .kriya ABSENT"] = not (WS / ".kriya").exists()
    checks["MAINTAINER FIX OBJECT ABSENT"] = subprocess.run(
        ["git", "-C", str(WS), "cat-file", "-e", "5d09dce42cc2945c9521360026c16845268ab77a"],
        capture_output=True, check=False).returncode != 0
    approval_doc = json.loads(APPROVAL.read_text())
    goal = GOAL.read_text(encoding="utf-8")
    facts["goal_identity"] = goal_identity(goal)
    checks["GOAL FILE SHA"] = sha(GOAL) == EXPECT["goal_file_sha256"]
    checks["GOAL BINDING"] = all(e["goal_sha256"] == goal_identity(goal) for e in approval_doc["approvals"])
    checks["REQUIREMENT CONTRACT SHA"] = sha(CONTRACT) == EXPECT["contract_sha256"]
    checks["ACCEPTANCE SHA"] = sha(SUITE) == EXPECT["acceptance_sha256"]
    checks["B3 APPROVAL SHA"] = sha(APPROVAL) == EXPECT["approval_sha256"]
    checks["B3 FORMAT /2"] = approval_doc["format"] == "kriya.acceptance_approval/2" and sorted(
        e["requirement_id"] for e in approval_doc["approvals"]) == ["REQ-1", "REQ-3"]
    with tempfile.TemporaryDirectory() as scratch:
        contract = load_requirement_contract(str(CONTRACT), goal, state_root=scratch, workspace=str(WS))
        requirements = contract.requirement_set
        acceptance = ao.load_acceptance(str(SUITE), requirements, scratch)
        facts.update(requirement_ids=list(requirements.ids), requirement_set=requirements.digest,
                     acceptance_digest=acceptance.digest, runner=runner_contract_digest(acceptance),
                     cases={rid: sorted(acceptance.identities_for(rid)) for rid in requirements.ids})
        checks["REQUIREMENT SET SHA"] = requirements.digest == EXPECT["requirement_set_sha256"] and list(
            requirements.ids) == ["REQ-1", "REQ-2", "REQ-3"]
        checks["ACCEPTANCE DIGEST"] = acceptance.digest == EXPECT["acceptance_sha256"]
        checks["RUNNER CONTRACT"] = runner_contract_digest(acceptance) == EXPECT["runner_contract_sha256"]
        checks["BASE REVISION"] = all(e["base_revision"] == git("rev-parse", "HEAD") for e in approval_doc["approvals"])
        try:
            loaded = load_approval(str(APPROVAL), requirements, acceptance, state_root=scratch, workspace=str(WS),
                                   base_revision=git("rev-parse", "HEAD"))
            checks["B3 APPROVAL LOADS"] = sorted(loaded.entries) == ["REQ-1", "REQ-3"]
        except Exception as error:  # noqa: BLE001 - reported as a failed check, never swallowed
            facts["b3_refusal"] = str(error)
            checks["B3 APPROVAL LOADS"] = False
    authority = subprocess.run(["kriya", "--config", str(CONFIG), "authority", "inspect"], cwd=str(WS),
                               capture_output=True, text=True, check=False)
    facts["authority_inspect"] = (authority.stdout + authority.stderr)[-3000:]
    checks["SEC-009 CURRENT"] = "CURRENT and covers all pending fields" in authority.stdout
    checks["WORKSPACE STILL CLEAN"] = git("status", "--porcelain", "--ignored") == "" and not (WS / ".kriya").exists()
    facts["config_sha256"] = sha(CONFIG)
    report = {"checks": checks, "facts": facts, "all_pass": all(checks.values())}
    Path(out).write_text(json.dumps(report, indent=1))
    for name, ok in checks.items():
        print(f"{name:32} {'MATCH' if ok else 'MISMATCH'}")
    print("ALL PASS" if report["all_pass"] else "STOP BEFORE MODEL CALL")
    return 0 if report["all_pass"] else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1]))
