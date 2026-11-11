"""TEST-KERNEL-GOLDEN-ENFORCE-FIVE-FAIL-AT-F384061-001: the gate that authorizes a live Scenario-A run is a checked-in
file with mandatory tiers. MEASURED 2026-11-11: the ad-hoc focused gates of the R1/R2/R4 slices never ran
tests/state_machine; five enforce kernel goldens were red on the clean tree while a gate was reported clean.

These tests fail when a mandatory tier is dropped from demo-v2/scripts/pre_live_focused_gate.sh, when a file it names no
longer exists, or when a failing tier could still exit 0."""
import re
import stat
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
GATE = REPO / "demo-v2" / "scripts" / "pre_live_focused_gate.sh"
SCRIPT = GATE.read_text(encoding="utf-8")
# the executable lines only: a tier named in a comment does not count
CODE = "\n".join(line for line in SCRIPT.splitlines() if not line.lstrip().startswith("#"))
COMMANDS = re.sub(r"\\\n\s*", " ", CODE)


def _tier(name: str) -> str:
    (command,) = re.findall(rf"^run_tier {re.escape(name)} (.+)$", COMMANDS, flags=re.MULTILINE)
    return command


def test_the_gate_is_executable_and_runs_the_whole_state_machine_tier():
    assert GATE.stat().st_mode & stat.S_IXUSR
    command = _tier("state_machine")
    # the marker over the whole tests directory: tests/state_machine AND every STATE_MACHINE_TIER_FILES member
    assert re.search(r"-m state_machine tests$", command), command
    assert " -k " not in command and "--deselect" not in command and "--ignore" not in command


def test_the_state_machine_marker_covers_the_kernel_goldens():
    """The tier the gate selects is the one the goldens live in (pytestmark in the golden module, tests/conftest.py for the rest)."""
    golden = (REPO / "tests" / "state_machine" / "test_sm_kernel_golden.py").read_text(encoding="utf-8")
    assert "pytestmark = pytest.mark.state_machine" in golden
    assert "STATE_MACHINE_TIER_FILES" in (REPO / "tests" / "conftest.py").read_text(encoding="utf-8")


def test_the_architecture_and_census_tier_and_the_recovery_tier_name_their_mandatory_members():
    architecture = _tier("architecture_and_censuses")
    for member in ("tests/architecture", "tests/test_s19p1b_residuals.py", "tests/test_platform_architecture_guard.py",
                   "tests/test_prd012_network_inventory.py", "tests/test_strict_doubles.py", "tests/test_backlog_registry.py",
                   "tests/test_pre_live_focused_gate_001.py"):
        assert member in architecture.split(), member
    recovery = _tier("enforce_recovery_controls")
    for member in ("tests/test_enforce_terminal_requirement_truth_001.py", "tests/test_enforce_unit_advisory_review_acceptance_001.py",
                   "tests/test_enforce_recovery_convergence_001.py", "tests/test_shared_decision_authority_001.py",
                   "tests/test_recovery_may_change_001.py", "tests/test_backend_final_closure_005_verification_owner_reopen.py",
                   "tests/test_workflow_controller_enforce.py", "tests/test_lr_r1_m1_reason_codes.py", "tests/test_demo_v2_spring_ignite.py"):
        assert member in recovery.split(), member


def test_every_path_the_gate_names_exists():
    named = sorted(set(re.findall(r"\btests/[A-Za-z0-9_./]+", COMMANDS)))
    assert len(named) >= 25
    missing = [path for path in named if not (REPO / path).exists()]
    assert missing == []


def test_static_checks_cover_the_whole_tree():
    assert _tier("ruff") == ".venv/bin/ruff check ."
    assert _tier("pylint") == ".venv/bin/pylint kriya plugins/core_tools tests"


def test_a_failing_tier_fails_the_gate_and_nothing_excuses_a_failure():
    assert '[ "$rc" -eq 0 ] || status=1' in CODE and CODE.rstrip().endswith('exit "$status"')
    assert re.findall(r"^run_tier (\S+)", COMMANDS, flags=re.MULTILINE) == [
        "state_machine", "architecture_and_censuses", "enforce_recovery_controls", "ruff", "pylint"]
    for excuse in ("|| true\n", "--lf", "-x ", "xfail", "--maxfail", "KNOWN", "baseline_ok"):
        assert excuse not in CODE.replace("ulimit -n 256 2>/dev/null || true\n", ""), excuse
