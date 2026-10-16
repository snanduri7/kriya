"""Demo V2 owner start state: the reset is idempotent, the idle check sees what it must see, and the owner runbook keeps its promises. No model, no network.

Every script runs against a temporary work area (DEMO_V2_WORK), never the owner's. Scenario 01's fixture source is not vendored, so the all-scenarios cases skip where it is absent.
"""
from __future__ import annotations

import hashlib
import os
import re
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
DEMO_V2 = ROOT / "demo-v2"
SCRIPTS = DEMO_V2 / "scripts"
RUNBOOK = DEMO_V2 / "OWNER_RUNBOOK.md"
SPRING_SRC = Path(os.environ.get("DEMO_V2_BASELINE_SRC", Path.home() / "kriya-live-validation/graphify-poc/spring-boot-application-example"))
HAS_SPRING = (SPRING_SRC / ".git").is_dir()


def _env(work: Path) -> dict:
    return {**os.environ, "DEMO_V2_WORK": str(work)}


def _run(script: str, work: Path, *args: str, timeout: int = 300) -> subprocess.CompletedProcess:
    return subprocess.run(["bash", str(SCRIPTS / script), *args], capture_output=True, text=True, check=False, timeout=timeout, env=_env(work))


def _fingerprint(work: Path) -> dict:
    """Content, not time: every file under the work area (the workspace's .git excluded) with its sha256, the workspaces' tree hashes and their `git status`."""
    files = {}
    for path in sorted(work.rglob("*")):
        rel = path.relative_to(work)
        if path.is_file() and ".git" not in rel.parts and rel.parts[:1] != ("scenario-01",):  # scenario 01 is a full clone: its tree hash below stands for it
            files[str(rel)] = hashlib.sha256(path.read_bytes()).hexdigest()
    trees = {}
    for workspace in sorted(work.glob("scenario-*/workspace")):
        if not (workspace / ".git").exists():
            # scenario 00 is the GREENFIELD start: a brand-new empty folder with no git repository; its fingerprint is its (empty) listing
            trees[str(workspace.relative_to(work))] = ("not a repository", "\n".join(sorted(p.name for p in workspace.iterdir())))
            continue
        trees[str(workspace.relative_to(work))] = (
            subprocess.run(["git", "-C", str(workspace), "rev-parse", "HEAD^{tree}"], capture_output=True, text=True, check=True).stdout.strip(),
            subprocess.run(["git", "-C", str(workspace), "status", "--short"], capture_output=True, text=True, check=True).stdout,
        )
    return {"files": files, "trees": trees}


@pytest.mark.parametrize("scenario", ["02", "03"])
def test_reset_is_idempotent_for_one_scenario(tmp_path: Path, scenario: str) -> None:
    work = tmp_path / "work"
    first = _run("reset_demo.sh", work, "--scenario", scenario)
    assert first.returncode == 0, first.stderr + first.stdout
    state_one = _fingerprint(work)
    second = _run("reset_demo.sh", work, "--scenario", scenario)
    assert second.returncode == 0, second.stderr + second.stdout
    assert _fingerprint(work) == state_one
    assert state_one["trees"], "nothing was materialised: the comparison would be vacuous"


def test_reset_really_discards_what_a_run_left_behind(tmp_path: Path) -> None:
    # negative control for the idempotence test: a change in the workspace and in the skills must be gone after the reset (a no-op reset would pass the test above)
    work = tmp_path / "work"
    assert _run("reset_demo.sh", work, "--scenario", "02").returncode == 0
    clean = _fingerprint(work)
    (work / "scenario-02" / "workspace" / "outline.py").write_text("print('a candidate from a previous run')\n")
    skill_yaml = next((work / "scenario-02" / "skills").glob("*/skill.yaml"))
    skill_yaml.write_text(skill_yaml.read_text() + "verification_gap_acknowledged: true\n")
    (work / "scenario-02" / "state" / "left-over").write_text("x")
    assert _fingerprint(work) != clean
    assert _run("reset_demo.sh", work, "--scenario", "02").returncode == 0
    assert _fingerprint(work) == clean


@pytest.mark.skipif(not HAS_SPRING, reason="scenario 01's fixture source is not available on this machine")
def test_reset_all_is_idempotent_and_leaves_no_service(tmp_path: Path) -> None:
    work = tmp_path / "work"
    first = _run("reset_demo.sh", work, "all")
    assert first.returncode == 0, first.stderr + first.stdout
    state_one = _fingerprint(work)
    assert sorted(state_one["trees"]) == ["scenario-00/workspace", "scenario-01/workspace", "scenario-02/workspace", "scenario-03/workspace", "scenario-04/workspace"]
    assert state_one["trees"]["scenario-00/workspace"] == ("not a repository", ""), "scenario 00 must be a brand-new EMPTY folder"
    second = _run("reset_demo.sh", work, "all")
    assert second.returncode == 0, second.stderr + second.stdout
    assert _fingerprint(work) == state_one
    assert "no demo service is running" in second.stdout


def test_the_snapshot_copy_is_the_recorded_snapshot_and_the_repository_copy_is_never_written(tmp_path: Path) -> None:
    work = tmp_path / "work"
    env = {line.split("=", 1)[0]: line.split("=", 1)[1] for line in (DEMO_V2 / "fixtures/knowledge-skill/fixture.env").read_text().splitlines() if "=" in line and not line.startswith("#")}
    snapshot = ROOT / env["SNAPSHOT_DIR"]
    before = {str(p.relative_to(snapshot)): p.read_bytes() for p in snapshot.rglob("*") if p.is_file()}
    assert _run("reset_demo.sh", work, "--scenario", "02").returncode == 0
    copy = work / "scenario-02" / "skills"
    assert {str(p.relative_to(copy)): p.read_bytes() for p in copy.rglob("*") if p.is_file()} == before
    assert len(before) == int(env["SNAPSHOT_FILES"])
    # a write to the work copy cannot reach the repository's snapshot
    next(copy.glob("*/skill.yaml")).write_text("tampered\n")
    assert {str(p.relative_to(snapshot)): p.read_bytes() for p in snapshot.rglob("*") if p.is_file()} == before
    assert "PRESERVED GOVERNED ACQUISITION SNAPSHOT" in (work / "scenario-02" / "SNAPSHOT-LABEL.txt").read_text()


def _idle_failures(work: Path, scenario: str) -> list:
    result = _run("verify_demo.sh", work, "--scenario", scenario, "--phase", "idle")
    return [line for line in result.stdout.splitlines() if line.startswith("FAIL")], result


def test_idle_check_passes_on_a_fresh_prepare_and_sees_each_kind_of_stale_state(tmp_path: Path) -> None:
    work = tmp_path / "work"
    for scenario in ("02", "03"):
        assert _run("prepare_demo.sh", work, "--scenario", scenario, "--no-services").returncode == 0
    for scenario in ("02", "03"):
        failures, result = _idle_failures(work, scenario)
        # on a developer machine the product tree may be dirty (that check is about the checkout, not the owner state); nothing else may fail
        assert [f for f in failures if "product tracked tree is clean" not in f] == [], result.stdout
        assert "no operator approval is in place" in result.stdout
    # negative controls: each stale condition must turn exactly its own check red
    ws02 = work / "scenario-02" / "workspace"
    (ws02 / "outline.py").write_text("x = 1\n")
    failures, _ = _idle_failures(work, "02")
    assert any("workspace is not clean" in f for f in failures) and any("differs from the pristine hash" in f for f in failures) and any("fixture baseline" in f for f in failures)
    assert _run("reset_demo.sh", work, "--scenario", "02").returncode == 0
    next((work / "scenario-02" / "skills").glob("*/rules.txt")).write_text("a rule somebody added\n")
    failures, _ = _idle_failures(work, "02")
    assert any("does not have the snapshot digest" in f for f in failures)


def test_idle_check_sees_a_running_stub(tmp_path: Path) -> None:
    work = tmp_path / "work"
    assert _run("prepare_demo.sh", work, "--scenario", "03", "--no-services").returncode == 0
    try:
        assert _run("stub_ctl.sh", work, "start").returncode == 0
        failures, _ = _idle_failures(work, "03")
        assert any("stub is NOT running" in f for f in failures)
    finally:
        _run("stub_ctl.sh", work, "stop")
    failures, result = _idle_failures(work, "03")
    assert [f for f in failures if "product tracked tree is clean" not in f] == [], result.stdout


def test_idle_check_sees_a_stale_operator_approval_and_reset_revokes_it(tmp_path: Path) -> None:
    # the approval is stored in the operator's HOME keyed by the workspace identity; this test grants one for a TEMPORARY workspace only and always revokes it
    work = tmp_path / "work"
    assert _run("prepare_demo.sh", work, "--scenario", "02", "--no-services").returncode == 0
    workspace = work / "scenario-02" / "workspace"
    kriya = str(ROOT / ".venv" / "bin" / "kriya")
    try:
        granted = subprocess.run([kriya, "authority", "approve", "--confirm"], cwd=workspace, capture_output=True, text=True, check=False, timeout=120)
        assert granted.returncode == 0, granted.stdout + granted.stderr
        failures, result = _idle_failures(work, "02")
        assert any("stale operator approval" in f for f in failures), result.stdout
        # a bare reset revokes it but leaves the workspace unconfigured: the idle check must say THAT, not guess about approvals
        assert _run("reset_demo.sh", work, "--scenario", "02").returncode == 0
        failures, result = _idle_failures(work, "02")
        assert any("no kriya.yaml is placed" in f for f in failures) and not any("stale operator approval" in f for f in failures), result.stdout
        # prepare = reset + place the configuration: back to the start state, approval gone
        assert _run("prepare_demo.sh", work, "--scenario", "02", "--no-services").returncode == 0
        failures, result = _idle_failures(work, "02")
        assert [f for f in failures if "product tracked tree is clean" not in f] == [], result.stdout
        assert "no operator approval is in place" in result.stdout
    finally:
        subprocess.run([kriya, "authority", "revoke"], cwd=workspace, capture_output=True, text=True, check=False, timeout=120)


def test_prepare_no_services_never_starts_the_stub(tmp_path: Path) -> None:
    work = tmp_path / "work"
    assert _run("prepare_demo.sh", work, "--scenario", "03", "--no-services").returncode == 0
    assert _run("stub_ctl.sh", work, "status").returncode != 0


# ---- the owner runbook ----------------------------------------------------------------------------------------------------------

def _runbook() -> str:
    return RUNBOOK.read_text(encoding="utf-8")


def test_the_runbook_has_every_section_the_owner_asked_for() -> None:
    text = _runbook()
    for heading in ("A. Prerequisites", "B. Prepare / reset (one command)", "C. Launch the Kriya GUI", "D. Scenario A", "E. Scenario B", "F. Scenario C", "G. What you should see",
                    "H. Approximate runtime", "I. Reset between scenarios", "J. Return the whole demo to a clean state"):
        assert re.search(rf"^## {re.escape(heading)}", text, flags=re.M), heading


def test_every_script_the_runbook_names_exists_and_every_flag_it_uses_is_real() -> None:
    text = _runbook()
    named = set(re.findall(r"scripts/([A-Za-z0-9_]+\.(?:sh|py))", text))
    assert {"prepare_demo.sh", "reset_demo.sh", "verify_demo.sh", "launch_gui.sh", "stub_ctl.sh"} <= named
    for name in named:
        assert (SCRIPTS / name).is_file(), name
        if name.endswith(".sh"):
            assert os.access(SCRIPTS / name, os.X_OK), name
    for command in re.findall(r"^(?:\$ )?(?:\"\$REPO\"/|\$REPO/)?demo-v2/scripts/(?:prepare|reset|verify)_demo\.sh[^\n]*$", text, flags=re.M):
        for flag in re.findall(r"--[a-z-]+", command):
            assert flag in ("--scenario", "--phase", "--check-build", "--no-services"), command
        for value in re.findall(r"--phase (\S+)", command):
            assert value in ("idle", "pre", "post"), command


def test_the_runbook_labels_what_must_be_labelled_and_claims_nothing_it_cannot() -> None:
    text = _runbook()
    assert "PRESERVED GOVERNED ACQUISITION SNAPSHOT" in text
    assert "CONTROLLED DETERMINISTIC MODEL-RESPONSE SCENARIO" in text
    assert "In the measured no-skill run the same task failed on current API knowledge. In the skill-enabled offline run the acquired rules were supplied and the task completed successfully." in text
    assert "stochastic" in text
    for forbidden in ("skill caused", "caused the success", "freshly acquired during", "production-ready", "backend-ready", "solver-qualified"):
        assert forbidden not in text, forbidden
    assert "OPTIONAL" in text and "live acquisition" in text
    # the scenario order the owner asked for
    assert text.index("D. Scenario A") < text.index("E. Scenario B") < text.index("F. Scenario C")


def test_the_scenario_b_instructions_do_not_put_the_answer_in_the_goal_or_ask_for_a_gui_run_the_desktop_cannot_do() -> None:
    text = _runbook()
    section = text[text.index("## E. Scenario B"):text.index("## F. Scenario C")]
    assert "terminal" in section and "Skills" in section
    # a desktop launch cannot acknowledge a knowledge-risk gap (kriya/control/launch.py): the offline task is a terminal run
    assert "desktop cannot" in section or "desktop declines" in section
    goal = (DEMO_V2 / "scenarios/02-knowledge-skill/goal.md").read_text(encoding="utf-8")
    for token in ("encoding=", "utf16", "Parser(", "Language(", "root_node", "start_byte"):
        assert token not in goal
