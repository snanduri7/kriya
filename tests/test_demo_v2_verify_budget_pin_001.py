"""DEMO-VERIFY-BUDGET-PIN-001: the Scenario-04 idle verifier compares the rendered configuration's run-wide generation budget with the scenario's ONE expected value, not with a literal of its own.

The verifier (`demo-v2/scripts/verify_demo.sh --scenario 04 --phase idle`) used to assert `generation_time_budget_seconds == 900`, the first calibration; after Run 1 (900 s) and Run 2 (1500 s) both ended on the budget the owner
set 2400 s, and the verifier then failed the intended configuration. The fix reads the expected value from the existing scenario seam, `fixtures/spring-ignite-protocol/fixture.env`
(`EXPECTED_GENERATION_BUDGET_SECONDS`), which the verifier already loads for the scenario's other pins; the scenario template must equal it (pinned in tests/test_demo_v2_spring_ignite.py).

These tests run the verifier's OWN configuration check (extracted from the script, only `check`/`pass`/`fail` are the script's own three lines) against a rendered configuration: the matching budget passes, a mismatched budget is
rejected (the negative control), an unset expectation is rejected (fail closed), and the script carries no numeric budget literal.
"""
from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
DEMO_V2 = REPO / "demo-v2"
SCRIPT = DEMO_V2 / "scripts" / "verify_demo.sh"
FIXTURE_ENV = DEMO_V2 / "fixtures" / "spring-ignite-protocol" / "fixture.env"
TEMPLATE = DEMO_V2 / "scenarios" / "04-spring-ignite-protocol" / "config" / "kriya.template.yaml"
PYTHON = REPO / ".venv" / "bin" / "python"


def _expected_budget() -> int:
    for line in FIXTURE_ENV.read_text(encoding="utf-8").splitlines():
        if line.startswith("EXPECTED_GENERATION_BUDGET_SECONDS="):
            return int(line.split("=", 1)[1])
    raise AssertionError("fixture.env has no EXPECTED_GENERATION_BUDGET_SECONDS")


def _config_check_block() -> str:
    """The verifier's `check "the configuration: ..." "$K_PY" -I -c "..."` statement, verbatim."""
    lines = SCRIPT.read_text(encoding="utf-8").splitlines()
    start = next(i for i, line in enumerate(lines) if line.lstrip().startswith('check "the configuration: public lookup off'))
    end = next(i for i in range(start, len(lines)) if lines[i].rstrip().endswith("base_url']\""))
    return "\n".join(lines[start:end + 1])


def _render(tmp_path: Path, work: Path, budget_line: str | None = None) -> None:
    text = TEMPLATE.read_text(encoding="utf-8").replace("@WORK@", str(work))
    if budget_line is not None:
        text = re.sub(r"generation_time_budget_seconds: \d+", budget_line, text)
    (work / "config").mkdir(parents=True, exist_ok=True)
    (work / "config" / "kriya.yaml").write_text(text, encoding="utf-8")


def _run_check(work: Path, expected: str) -> str:
    """PASS or FAIL, as the verifier's own `check` would report this statement."""
    harness = (
        'pass() { echo "PASS  $*"; }\nfail() { echo "FAIL  $*"; }\n'
        'check() { local label="$1"; shift; if "$@" >/dev/null 2>&1; then pass "$label"; else fail "$label"; fi; }\n'
        f'K_PY={PYTHON}\nW04={work}\n'
        + (f"EXPECTED_GENERATION_BUDGET_SECONDS={expected}\n" if expected != "<unset>" else "")
        + _config_check_block() + "\n"
    )
    done = subprocess.run(["bash", "-c", harness], capture_output=True, text=True, check=True, env={"PATH": "/usr/bin:/bin", "HOME": str(work)})
    return done.stdout.strip().splitlines()[-1]


@pytest.fixture()
def work(tmp_path: Path) -> Path:
    w = tmp_path / "scenario-04"
    (w / "skills").mkdir(parents=True)
    shutil.copytree(REPO / "skills" / "ignite-java17", w / "skills" / "ignite-java17")
    return w


def test_the_scenario_template_and_the_rendered_configuration_pass_the_verifiers_own_check_at_the_expected_budget(work, tmp_path):
    _render(tmp_path, work)
    assert _run_check(work, str(_expected_budget())).startswith("PASS")


def test_negative_control_a_mismatched_budget_is_rejected(work, tmp_path):
    expected = _expected_budget()
    for wrong in (900, 1500, expected - 1, expected + 1):
        _render(tmp_path, work, f"generation_time_budget_seconds: {wrong}")
        assert _run_check(work, str(expected)).startswith("FAIL"), wrong  # the verifier would have passed any of these under a literal that happened to equal them


def test_the_verifier_follows_the_scenarios_expected_value_not_a_number_of_its_own(work, tmp_path):
    _render(tmp_path, work, "generation_time_budget_seconds: 123")
    assert _run_check(work, "123").startswith("PASS")      # the expectation is read, not baked in
    assert _run_check(work, "2400" if _expected_budget() != 2400 else "2401").startswith("FAIL")


def test_an_unset_or_empty_expectation_fails_closed(work, tmp_path):
    _render(tmp_path, work)
    assert _run_check(work, "<unset>").startswith("FAIL")
    assert _run_check(work, "").startswith("FAIL")


def test_the_verifier_carries_no_numeric_budget_literal_and_the_template_equals_the_scenarios_expected_value():
    text = SCRIPT.read_text(encoding="utf-8")
    assert re.search(r"generation_time_budget_seconds'\] == \$EXPECTED_GENERATION_BUDGET_SECONDS", text)
    assert not re.search(r"generation_time_budget_seconds'\]\s*==\s*\d", text)
    match = re.search(r"^\s*generation_time_budget_seconds: (\d+)", TEMPLATE.read_text(encoding="utf-8"), re.M)
    assert match is not None and int(match.group(1)) == _expected_budget()
