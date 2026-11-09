"""QUALIFY-HELPER-SCRATCH-CLEANUP-001: the Scenario-04 qualification helper's measurement truth is decided before, and survives, the disposal of its own scratch directories.

MEASURED (qualification at 50cd023): three legs (java17, java17-witness, the read-back mutation self-check) produced NO report. Docker Desktop (macOS) held a directory that had carried read-only mask mounts
for 0.5-2.3 s after the container exited; ``tempfile.TemporaryDirectory.__exit__`` raised that PermissionError in the helper's ``cmd_runtime`` after the measurement and before the JSON report was printed
(the mutation self-check calls ``cmd_runtime``; ``cmd_guard`` had the same construct).

The contract pinned here, for each of the three control flows (runtime, mutants, guard): the report is identical with and without a failing disposal; the exit status follows the report; the warning
names the exact owned path and goes to stderr only (stdout stays one parseable JSON object); setup/use/measurement errors still propagate; only a filesystem failure of the disposal is caught; only the
path this invocation created is ever removed. No sleeps, no retries (the helper attempts the removal once).
"""
from __future__ import annotations

import importlib.util
import json
import os
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
DEMO_V2 = REPO_ROOT / "demo-v2"
REFERENCE = DEMO_V2 / "fixtures" / "spring-ignite-protocol" / "reference"
GOAL = DEMO_V2 / "scenarios" / "04-spring-ignite-protocol" / "goal.md"
SKILL = REPO_ROOT / "skills" / "ignite-java17"


def _helper():
    spec = importlib.util.spec_from_file_location("qualify_spring_ignite_under_test", DEMO_V2 / "scripts" / "qualify_spring_ignite.py")
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


@pytest.fixture()
def helper(tmp_path, monkeypatch):
    """The helper module, with every scratch directory it makes landing under tmp_path (never in the shared /tmp)."""
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path / "scratch-root"))
    (tmp_path / "scratch-root").mkdir()
    return _helper()


def _deny(module, monkeypatch):
    """What Docker Desktop does for the 0.5-2.3 s after a container with mask mounts exits: the removal of the owned scratch is refused."""
    attempts = []

    def denied(path):
        attempts.append(path)
        raise PermissionError(13, "Permission denied", path)

    monkeypatch.setattr(module, "_remove_scratch_tree", denied)
    return attempts


def _scratch(tmp_path) -> list:
    return sorted(os.listdir(tmp_path / "scratch-root"))


# --------------------------------------------------------------------------------------------------------------- the boundary itself

def test_a_failed_disposal_is_reported_on_stderr_with_the_exact_path_and_is_never_fatal(helper, tmp_path, monkeypatch, capsys):
    attempts = _deny(helper, monkeypatch)
    with helper._owned_scratch("qualify04-") as path:  # noqa: SLF001
        owned = path
        assert os.path.isdir(path)
    assert attempts == [owned]  # attempted exactly once: no retry loop
    captured = capsys.readouterr()
    assert captured.out == "" and owned in captured.err and "could not be removed" in captured.err
    assert os.path.isdir(owned)  # the residue is left in place for its owner to clean, not hidden


def test_the_normal_disposal_removes_exactly_the_owned_path_and_nothing_else(helper, tmp_path, capsys):
    root = tmp_path / "scratch-root"
    unrelated = root / "qualify04-from-another-process"  # a sibling another invocation owns: matched by the prefix and by any glob, but not this invocation's
    unrelated.mkdir()
    (unrelated / "keep").write_text("x")
    with helper._owned_scratch("qualify04-") as path:  # noqa: SLF001
        owned = path
    assert not os.path.lexists(owned) and (unrelated / "keep").read_text() == "x"
    captured = capsys.readouterr()
    assert captured.out == "" and captured.err == ""


def test_scratch_identities_are_unique_across_invocations_even_when_every_removal_fails(helper, monkeypatch):
    _deny(helper, monkeypatch)
    seen = []
    for _ in range(4):
        with helper._owned_scratch("qualify04-") as path:  # noqa: SLF001
            seen.append(path)
    assert len(set(seen)) == 4 and all(os.path.isdir(p) for p in seen)


def test_an_error_raised_by_the_use_propagates_and_a_failing_disposal_does_not_replace_it(helper, monkeypatch):
    _deny(helper, monkeypatch)
    with pytest.raises(ZeroDivisionError):
        with helper._owned_scratch("qualify04-"):  # noqa: SLF001
            raise ZeroDivisionError("the measurement itself failed")


def test_only_a_filesystem_failure_of_the_disposal_is_caught(helper, monkeypatch):
    def broken(path):
        raise ValueError("a coding error inside the disposal must not be swallowed")

    monkeypatch.setattr(helper, "_remove_scratch_tree", broken)
    with pytest.raises(ValueError, match="coding error"):
        with helper._owned_scratch("qualify04-"):  # noqa: SLF001
            pass


# ------------------------------------------------------------------------------------- flow 1: runtime (the leg that produced no report)

def _stub_validator(output_lines, **overrides):
    result = {"success": True, "returncode": 0, "timed_out": False, "output": "\n".join(output_lines),
              "runtime_artifacts": [], "egress": {"capability": "denied"}, "toolchain_identity": {"runtime_version": "17"}}
    result.update(overrides)

    class Validator:
        def __init__(self, workspace, autonomy_cfg=None):
            self.workspace, self.tree_binding = workspace, None

        def run_app_sequence(self, _commands, timeout=0):
            return dict(result)

    return Validator


def _runtime_args(tmp_path):
    seed = tmp_path / "m2-seed"
    seed.mkdir(exist_ok=True)
    (seed / "x").write_text("seed")
    return SimpleNamespace(repo=str(REPO_ROOT), fixture=str(REFERENCE), contained=True, cache_seed=str(seed), witness=False)


def _passing_output(helper):
    return ["Ignite node started OK", helper._expected_line(), "[VERIFICATION] PASS", "Ignite node stopped OK"]  # noqa: SLF001


def _runtime_report(helper, tmp_path, monkeypatch, validator):
    import kriya.tools.validate as validate

    monkeypatch.setattr(validate, "PolymorphicValidator", validator)
    monkeypatch.setenv("KRIYA_STATE_DIR", str(tmp_path / "state-for-test"))
    report = helper.cmd_runtime(_runtime_args(tmp_path))
    report.pop("elapsed_seconds", None)  # wall-clock
    return report


def test_runtime_leg_the_report_and_status_are_identical_when_the_disposal_fails(helper, tmp_path, monkeypatch, capsys):
    artifacts = [f"ignite/work/db/marshaller/{helper.ignite_type_id(helper.PROTOCOL_CLASS)}.classname0"]
    validator = _stub_validator(_passing_output(helper), runtime_artifacts=artifacts)
    reference = _runtime_report(helper, tmp_path, monkeypatch, validator)
    assert reference["ok"] is True and _scratch(tmp_path) == []
    attempts = _deny(helper, monkeypatch)
    capsys.readouterr()
    degraded = _runtime_report(helper, tmp_path, monkeypatch, validator)
    assert degraded == reference and degraded["ok"] is True  # the measurement is decided before the disposal; the status derived from it is unchanged
    captured = capsys.readouterr()
    assert captured.out == "" and len(attempts) == 1 and attempts[0] in captured.err and os.path.isdir(attempts[0])


def test_runtime_leg_a_failed_measurement_still_fails(helper, tmp_path, monkeypatch):
    validator = _stub_validator(["Ignite node started OK"], success=False, returncode=1)
    report = _runtime_report(helper, tmp_path, monkeypatch, validator)
    assert report["ok"] is False and report["checks"]["gate_success"] is False and _scratch(tmp_path) == []
    _deny(helper, monkeypatch)
    assert _runtime_report(helper, tmp_path, monkeypatch, validator) == report  # also when the disposal is refused


def test_runtime_leg_setup_errors_propagate_and_the_scratch_is_still_disposed(helper, tmp_path, monkeypatch):
    args = _runtime_args(tmp_path)
    args.cache_seed = str(tmp_path / "no-such-seed")  # setup failure inside the owned scratch
    import kriya.tools.validate as validate

    monkeypatch.setattr(validate, "PolymorphicValidator", _stub_validator([]))
    monkeypatch.setenv("KRIYA_STATE_DIR", str(tmp_path / "state-for-test"))
    with pytest.raises(FileNotFoundError):
        helper.cmd_runtime(args)
    assert _scratch(tmp_path) == []


# ------------------------------------------------------------------------------------------ flow 2: the mutation self-check (calls the runtime leg)

def _mutants_report(helper, tmp_path, monkeypatch):
    monkeypatch.setattr(helper, "_mvn_test", lambda project: {"passes": os.path.basename(project).startswith("unmutated"), "compile_error": False})
    monkeypatch.setattr(helper, "cmd_runtime", lambda args: {"ok": os.path.basename(args.fixture).startswith("unmutated")})
    return helper.cmd_mutants(SimpleNamespace(repo=str(REPO_ROOT), fixture=str(REFERENCE)))


def test_mutants_flow_the_report_is_identical_when_the_disposal_fails(helper, tmp_path, monkeypatch, capsys):
    reference = _mutants_report(helper, tmp_path, monkeypatch)
    assert reference["ok"] is True and reference["control_passes"] is True and reference["surviving_mutants"] == [] and _scratch(tmp_path) == []
    attempts = _deny(helper, monkeypatch)
    capsys.readouterr()
    degraded = _mutants_report(helper, tmp_path, monkeypatch)
    assert degraded == reference
    captured = capsys.readouterr()
    assert captured.out == "" and len(attempts) == 1 and attempts[0] in captured.err


def test_mutants_flow_a_surviving_mutant_still_fails(helper, tmp_path, monkeypatch):
    monkeypatch.setattr(helper, "_mvn_test", lambda project: {"passes": True, "compile_error": False})
    monkeypatch.setattr(helper, "cmd_runtime", lambda args: {"ok": True})  # nothing kills any mutant
    _deny(helper, monkeypatch)
    report = helper.cmd_mutants(SimpleNamespace(repo=str(REPO_ROOT), fixture=str(REFERENCE)))
    assert report["ok"] is False and report["surviving_mutants"]


# ------------------------------------------------------------------------------------------------------------------- flow 3: the guard

def _guard_args():
    return SimpleNamespace(repo=str(REPO_ROOT), goal=str(GOAL), skill=str(SKILL))


def test_guard_flow_the_report_is_identical_when_the_disposal_fails(helper, tmp_path, monkeypatch, capsys):
    reference = helper.cmd_guard(_guard_args())
    assert reference["ok"] is True and _scratch(tmp_path) == []
    attempts = _deny(helper, monkeypatch)
    capsys.readouterr()
    degraded = helper.cmd_guard(_guard_args())
    assert degraded == reference
    captured = capsys.readouterr()
    assert captured.out == "" and len(attempts) == 4 and len(set(attempts)) == 4  # four cases, four unique owned scratch paths
    assert all(path in captured.err for path in attempts)


# ---------------------------------------------------------------------------------------------- main(): stdout is one parseable JSON object, the status follows it

def test_main_prints_one_valid_json_object_on_stdout_and_exits_by_the_report_when_the_disposal_fails(helper, tmp_path, monkeypatch, capsys):
    attempts = _deny(helper, monkeypatch)
    monkeypatch.setattr(sys, "argv", ["qualify_spring_ignite.py", "guard", "--repo", str(REPO_ROOT), "--goal", str(GOAL), "--skill", str(SKILL)])
    status = helper.main()
    captured = capsys.readouterr()
    report = json.loads(captured.out)  # the whole of stdout is the report: a cleanup diagnostic there would not parse
    assert status == 0 and report["ok"] is True and attempts and all(path in captured.err for path in attempts)
    assert set(report) == {"extracted", "cases", "ok"}  # the existing report schema, unchanged


def test_the_helper_no_longer_uses_a_tempdirectory_anywhere():
    source = (DEMO_V2 / "scripts" / "qualify_spring_ignite.py").read_text(encoding="utf-8")
    assert "TemporaryDirectory(" not in source and source.count("_owned_scratch(") == 4  # the definition and the three sites
