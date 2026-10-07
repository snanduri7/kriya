"""POST-REG-R2 PRIMARY CODING PROFILE COMPARISON: pre-run identity check for one arm (canonical GR-R1 checks + auto-skill,
read-only qualification view, per-role qualification, config digest). NO model call (no inference, no embedding).

Every binding is checked with Kriya's own functions from the installed 56ae8d3 build; any mismatch exits 1.
usage: venv-postreg2/bin/python postreg2_prerun_check.py <a|b> <out.json>"""
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
ARM = sys.argv[1]
NAME = f"postreg2-{ARM}"
PREP = json.loads((D / "evidence/postreg2" / f"prepare-{ARM}.json").read_text())
WS = D / f"ws-{NAME}" / "gr1-graphify"
GOAL = HOME / "kriya-live-validation/graphify-canonical-853aa43/goal.txt"
P = HOME / "kriya-wt/p3d/handover/evidence/gr0/graphify_prospective"
CONTRACT, SUITE, APPROVAL = (P / "graphify_requirements_PROPOSED_unapproved.json", P / "kriya_acceptance_graphify.py",
                             P / "graphify_approval_APPROVED_owner.json")
CONFIG = D / "config" / f"{NAME}.yaml"
CONFIG_SHA256 = PREP["config"]["sha256"]
SKILL = D / f"skills-{NAME}" / "auto-gr1-graphify"
SKILL_FILES = {"instructions.md": "ea34cb4278ed29f0227a5de3ce1faa67359613699639233b041e29f9b9df62d1",
               "rules.txt": "4b167e8021312bdbbe30d7e077f7711a96668c9eedfc29ceffb774838ba7d814",
               "skill.yaml": "43d0527b2e8f42fe1eb73ee647a63df48c2c659befe6ce2591876fb2f09b7164"}
SKILL_AGGREGATE = "b4a91e1aaefc8653c635c35c636d373bb7c06a0fc2eebd0674daabff6e122a9f"
VIEW = D / f"qual-view-{NAME}"
VIEW_FILES = {line.split(" view=")[1].split()[0].rsplit("/", 1)[1]: line.split("view_sha256=")[1].split()[0]
              for line in (D / f"qual-view-{NAME}.MANIFEST.txt").read_text().splitlines() if line}
_STATUS = json.loads((D / "evidence/postreg2" / f"model-status-{ARM}.json").read_text())
EXPECTED_MODELS = {role: sorted({r["model"] for r in rows}) for role, rows in _STATUS.items() if isinstance(rows, list)}
SMOKE = D / "evidence/postreg2" / f"smoke-{ARM}" / "smoke.json"
EXPECT = {
    "kriya_commit": "56ae8d3d6afa9edb2a39be995dbb3c4c8239eb02",
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
    checks["CONFIG = QUALIFIED MATCHED PROFILE"] = facts["config_sha256"] == CONFIG_SHA256
    files = sorted(p.name for p in SKILL.rglob("*") if p.is_file())
    checks["AUTO-SKILL FILES"] = files == sorted(SKILL_FILES) and all(sha(SKILL / n) == h for n, h in SKILL_FILES.items())
    # Byte order of the frozen MANIFEST_canonical.txt: `LC_ALL=C sort -k2` counts leading blanks, so the three
    # "<sha>  <path>" lines sort before "DIR <path>".
    lines = [f"{SKILL_FILES[n]}  auto-gr1-graphify/{n}" for n in sorted(SKILL_FILES)] + ["DIR auto-gr1-graphify/examples"]
    manifest = "".join(line + "\n" for line in lines)
    facts["auto_skill_aggregate"] = hashlib.sha256(manifest.encode()).hexdigest()
    checks["AUTO-SKILL DIGEST"] = facts["auto_skill_aggregate"] == SKILL_AGGREGATE and (SKILL / "examples").is_dir()
    checks["QUALIFICATION VIEW READ ONLY"] = (not os.access(VIEW, os.W_OK) and sorted(p.name for p in VIEW.iterdir()) == sorted(VIEW_FILES)
                                             and all(sha(VIEW / n) == h and not os.access(VIEW / n, os.W_OK) for n, h in VIEW_FILES.items()))
    checks["QUALIFICATION HOME = VIEW"] = os.environ.get("KRIYA_QUALIFICATION_HOME") == str(VIEW)
    status = subprocess.run(["kriya", "--config", str(CONFIG), "model", "status", "--json"], cwd=str(WS),
                            capture_output=True, text=True, check=False)
    try:
        rows = {role: {r["model"]: r["status"] for r in entries} for role, entries in json.loads(status.stdout).items()
                if isinstance(entries, list)}
    except ValueError:
        rows = {}
    facts["model_status"] = rows
    checks["ROLE MODELS AS MATCHED"] = {role: sorted(models) for role, models in rows.items()} == {
        role: sorted(models) for role, models in EXPECTED_MODELS.items()}
    statuses = json.loads(status.stdout) if status.stdout.strip().startswith("{") else {}
    rows_all = [r for entries in statuses.values() if isinstance(entries, list) for r in entries]
    facts["role_identities"] = [{"model": r["model"], "fingerprint": r["fingerprint_digest"],
                                 "settings": r["inference_settings"]["digest"], "status": r["status"]} for r in rows_all]
    checks["EVERY ROLE IDENTITY QUALIFIED"] = bool(rows_all) and all(r["status"] == "QUALIFIED" for r in rows_all)
    checks["ROLE IDENTITIES = PREPARED"] = sorted((r["model"], r["fingerprint_digest"]) for r in rows_all) == sorted(
        (r["model"], r["fingerprint_digest"]) for entries in _STATUS.values() if isinstance(entries, list) for r in entries)
    smoke = json.loads(SMOKE.read_text()) if SMOKE.exists() else {}
    facts["reg_r1_smoke"] = {k: smoke.get(k) for k in ("verdict", "authority", "blocking", "level2_counts")}
    checks["REG-R1 SMOKE NO REGRESSION"] = (smoke.get("verdict") == "NO REGRESSION" and smoke.get("authority") ==
                                           "pytest_per_test" and smoke.get("kriya", {}).get("commit") == EXPECT["kriya_commit"])
    checks["WORKSPACE STILL CLEAN (after status)"] = git("status", "--porcelain", "--ignored") == "" and not (WS / ".kriya").exists()
    report = {"checks": checks, "facts": facts, "all_pass": all(checks.values())}
    Path(out).write_text(json.dumps(report, indent=1))
    for name, ok in checks.items():
        print(f"{name:32} {'MATCH' if ok else 'MISMATCH'}")
    print("ALL PASS" if report["all_pass"] else "STOP BEFORE MODEL CALL")
    return 0 if report["all_pass"] else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[2]))
