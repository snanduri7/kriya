"""demo-v2/ is a repository-owned source asset: its contracts must stay complete and consistent with its manifest.

Static only: no model, no network, no fixture materialisation (that is `demo-v2/scripts/prepare_demo.sh --check-build`).
"""
from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path
from typing import Dict, List

import pytest
import yaml

DEMO_V2 = Path(__file__).resolve().parent.parent / "demo-v2"

REQUIRED_HEADINGS = (
    "ID", "TITLE", "PURPOSE", "STATUS", "PRECONDITIONS", "PRODUCT HEAD/TAG COMPATIBILITY", "MODEL REQUIREMENT",
    "NETWORK REQUIREMENT", "RESET", "RUN", "EXPECTED EVENTS", "EXPECTED EVIDENCE", "SUCCESS CRITERIA", "FAILURE DIAGNOSIS",
)
MAX_FILE_BYTES = 64 * 1024  # demo-v2 holds contracts and small templates; a clone, a log or a binary does not belong


def missing_headings(text: str) -> List[str]:
    present = {m.group(1).strip() for m in re.finditer(r"^## (.+)$", text, flags=re.MULTILINE)}
    return [h for h in REQUIRED_HEADINGS if h not in present]


def _sections(text: str) -> Dict[str, str]:
    parts = re.split(r"^## (.+)$", text, flags=re.MULTILINE)
    return {parts[i].strip(): parts[i + 1] for i in range(1, len(parts) - 1, 2)}


def _manifest() -> dict:
    return yaml.safe_load((DEMO_V2 / "demo-manifest.yaml").read_text(encoding="utf-8"))


def _fixture_env(path: Path) -> Dict[str, str]:
    out: Dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip() and not line.startswith("#"):
            key, _, value = line.partition("=")
            out[key] = value
    return out


def test_every_scenario_declares_every_mandatory_field() -> None:
    for scenario in _manifest()["scenarios"]:
        text = (DEMO_V2 / scenario["dir"] / "SCENARIO.md").read_text(encoding="utf-8")
        assert missing_headings(text) == [], scenario["id"]
        sections = _sections(text)
        for heading in REQUIRED_HEADINGS:
            assert sections[heading].strip(), f"{scenario['id']}: section '{heading}' is empty"


def test_missing_field_is_detected_negative_control() -> None:
    text = (DEMO_V2 / "scenarios" / "01-normal-coding" / "SCENARIO.md").read_text(encoding="utf-8")
    assert missing_headings(text) == []
    assert missing_headings(text.replace("## FAILURE DIAGNOSIS", "## Notes")) == ["FAILURE DIAGNOSIS"]


def test_manifest_matches_the_files_and_the_scenario_status_text() -> None:
    manifest = _manifest()
    assert manifest["product_baseline"]["tag"] == "kriya-v1.2.0-pre-modularization"
    assert "BACKEND SOLVER READINESS: FAIL" in manifest["solver_readiness_statement"]
    assert "SOLVER REPEATABILITY: NOT ESTABLISHED" in manifest["solver_readiness_statement"]
    ids = [s["id"] for s in manifest["scenarios"]]
    assert ids == ["00-greenfield-hello-world", "01-normal-coding", "02-knowledge-skill", "03-controlled-repair"]
    for scenario in manifest["scenarios"]:
        directory = DEMO_V2 / scenario["dir"]
        assert directory.is_dir() and directory.name == scenario["id"]
        status = _sections((directory / "SCENARIO.md").read_text(encoding="utf-8"))["STATUS"]
        # The manifest's status and the scenario's own words must not disagree about what has been measured.
        keyword = {"REHEARSED_ONCE_PASS": "REHEARSED ONCE, PASS", "REHEARSED_ONCE_PASS_WITH_CAVEATS": "REHEARSED ONCE FROM EMPTY STATE, ISOLATED-TASK REHEARSAL PASS, WITH CAVEATS",
                   "REHEARSED_PASS_REPRODUCIBLE_CONTROLLED": "REHEARSED, PASS, REPRODUCIBLE", "NOT_YET_REHEARSED": "NOT YET REHEARSED"}[scenario["status"]]
        assert keyword in status, scenario["id"]
        if scenario["expected"]:
            assert (DEMO_V2 / scenario["expected"]).is_file()
        for fixture in scenario["fixtures"]:
            assert any(f["id"] == fixture for f in manifest["fixtures"]), fixture
    for script in manifest["scripts"].values():
        assert (DEMO_V2 / script).is_file() and os.access(DEMO_V2 / script, os.X_OK), script


def test_the_controlled_scenario_is_labelled_controlled_everywhere_it_is_described() -> None:
    manifest = _manifest()
    controlled = [s for s in manifest["scenarios"] if s["controlled"]]
    assert [s["id"] for s in controlled] == ["03-controlled-repair"]
    text = (DEMO_V2 / controlled[0]["dir"] / "SCENARIO.md").read_text(encoding="utf-8")
    assert "CONTROLLED DETERMINISTIC MODEL-RESPONSE SCENARIO" in text
    assert "CONTROLLED / SCRIPTED" in text
    assert "NOT live-model behaviour" in text
    assert "scripted stub" in (DEMO_V2 / "README.md").read_text(encoding="utf-8")


def test_fixture_identity_is_complete_and_well_formed() -> None:
    env = _fixture_env(DEMO_V2 / "fixtures" / "brownfield-spring" / "fixture.env")
    for key in ("FIXTURE_ID", "FIXTURE_SOURCE_DEFAULT", "FIXTURE_COMMIT", "FIXTURE_TARGET_FILE", "FIXTURE_TARGET_SHA256",
                "FIXTURE_BASELINE_TESTS", "FIXTURE_METHOD_START_REGEX", "FIXTURE_FIX_REGEX", "FIXTURE_UPSTREAM", "FIXTURE_LICENSE"):
        assert env.get(key), key
    assert re.fullmatch(r"[0-9a-f]{40}", env["FIXTURE_COMMIT"])
    assert re.fullmatch(r"[0-9a-f]{64}", env["FIXTURE_TARGET_SHA256"])
    assert env["FIXTURE_LICENSE"] == "UNVERIFIED"  # the fixture is therefore not vendored
    re.compile(env["FIXTURE_METHOD_START_REGEX"])
    re.compile(env["FIXTURE_FIX_REGEX"])
    # The scoped-method check exists because create() in the same file already persists: the fix regex must not be file-wide-safe.
    assert not re.search(env["FIXTURE_FIX_REGEX"], "driverDO.setDeleted(true);")
    assert re.search(env["FIXTURE_FIX_REGEX"], "driver = driverRepository.save(driverDO);")


def _tracked_demo_files() -> list[Path]:
    """What is COMMITTED under demo-v2/ (git's index), not whatever is on disk: an editor's swap file or bytecode is a by-product of working here, not an asset."""
    listed = subprocess.run(["git", "ls-files", "-z", "--", str(DEMO_V2)], cwd=DEMO_V2.parent, capture_output=True, text=True, check=True, timeout=30).stdout
    return [DEMO_V2.parent / name for name in listed.split("\0") if name]


def test_no_editor_swap_file_is_tracked() -> None:
    # 0b92510 committed the owner's live vim swap file; the test below caught it (binary content) and was wrongly dismissed as owner clutter.
    swap = [p.name for p in _tracked_demo_files() if p.name.endswith((".swp", ".swo", "~")) or (p.name.startswith(".") and ".sw" in p.suffix)]
    assert swap == []


def test_nothing_machine_specific_or_heavy_is_committed() -> None:
    for path in _tracked_demo_files():
        if not path.is_file():  # tracked but absent from the working tree (a pending deletion) is not an asset on disk
            continue
        assert path.stat().st_size <= MAX_FILE_BYTES, f"{path} is too large for a contract/template"
        text = path.read_text(encoding="utf-8")
        assert "/Users/" not in text, f"{path} hard-codes a home directory"
        assert "api_key: \"sk-" not in text and "BEGIN PRIVATE KEY" not in text


@pytest.mark.parametrize("script", sorted(p.name for p in (DEMO_V2 / "scripts").glob("*.sh")))
def test_scripts_parse_as_bash(script: str) -> None:
    result = subprocess.run(["bash", "-n", str(DEMO_V2 / "scripts" / script)], capture_output=True, text=True, check=False, timeout=30)
    assert result.returncode == 0, result.stderr


def test_verify_refuses_a_work_area_inside_the_repository(tmp_path: Path) -> None:
    inside = DEMO_V2.parent / "demo-v2-work-should-not-exist"
    result = subprocess.run(["bash", str(DEMO_V2 / "scripts" / "verify_demo.sh"), "--scenario", "01", "--phase", "pre"],
                            capture_output=True, text=True, check=False, timeout=60, env={**os.environ, "DEMO_V2_WORK": str(inside)})
    assert result.returncode != 0
    assert "inside the repository" in result.stderr
    assert not inside.exists()


def test_an_unknown_scenario_refuses_to_prepare_or_reset(tmp_path: Path) -> None:
    # a temp work area: scenario 02 is a real, resettable scenario now, so nothing here may default to the owner's work area
    for script in ("prepare_demo.sh", "reset_demo.sh"):
        result = subprocess.run(["bash", str(DEMO_V2 / "scripts" / script), "--scenario", "04"], capture_output=True, text=True, check=False, timeout=60,
                                env={**os.environ, "DEMO_V2_WORK": str(tmp_path / "work")})
        assert result.returncode != 0, script
        assert "unknown scenario" in result.stderr


def _verify_with_work(work: Path):
    return subprocess.run(["bash", str(DEMO_V2 / "scripts" / "verify_demo.sh"), "--scenario", "01", "--phase", "pre"], capture_output=True, text=True, check=False, timeout=60,
                          env={**os.environ, "DEMO_V2_WORK": str(work)})


def test_a_work_area_inside_the_repository_is_refused_even_when_its_parent_does_not_exist_or_is_a_symlink(tmp_path: Path) -> None:
    # review finding: with a missing parent the old check fell back to "/<name>" and accepted a path inside the repository
    missing_parent = DEMO_V2.parent / "ui" / "demo-v2-missing-parent" / "work"
    result = _verify_with_work(missing_parent)
    assert result.returncode != 0 and "inside the repository" in result.stderr and not missing_parent.parent.exists()
    link = tmp_path / "link"
    link.symlink_to(DEMO_V2.parent / "ui")
    result = _verify_with_work(link / "demo-v2-via-symlink")
    assert result.returncode != 0 and "inside the repository" in result.stderr
    assert not (DEMO_V2.parent / "ui" / "demo-v2-via-symlink").exists()


def test_reset_and_prepare_refuse_a_non_empty_directory_demo_v2_did_not_create(tmp_path: Path) -> None:
    work = tmp_path / "somebody-elses-files"
    work.mkdir()
    (work / "precious.txt").write_text("keep me")
    for script in ("reset_demo.sh", "prepare_demo.sh"):
        result = subprocess.run(["bash", str(DEMO_V2 / "scripts" / script), "--scenario", "03"], capture_output=True, text=True, check=False, timeout=60,
                                env={**os.environ, "DEMO_V2_WORK": str(work)})
        assert result.returncode != 0 and "not empty and was not created by demo-v2" in result.stderr, script
    assert (work / "precious.txt").read_text() == "keep me" and sorted(p.name for p in work.iterdir()) == ["precious.txt"]
