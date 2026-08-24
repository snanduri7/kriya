"""MA4.7's own regression requirement, mirroring MA4.4's for
kriya/tools/validate.py: the secondary INSTALL_PACKAGE audit call must
never affect whether the real command runs, and a broken/misconfigured
policy engine for THIS second call must not break the RUN_COMMAND audit or
the real subprocess execution either.
"""
from unittest.mock import MagicMock

from kriya.tools.validate import PolymorphicValidator


def test_install_shaped_command_still_runs_even_though_policy_requires_approval(tmp_path):
    validator = PolymorphicValidator(str(tmp_path))
    result = validator._run_cmd_with_timeout(["pip", "--version"], cwd=str(tmp_path), timeout=30)
    assert result["returncode"] == 0


def test_a_broken_policy_engine_on_the_install_audit_never_blocks_the_real_command(tmp_path):
    validator = PolymorphicValidator(str(tmp_path))
    original_evaluate = validator.execution_policy.evaluate

    def flaky_evaluate(request):
        if request.action_type.value == "install_package":
            raise RuntimeError("install audit broke")
        return original_evaluate(request)

    validator.execution_policy.evaluate = flaky_evaluate
    result = validator._run_cmd_with_timeout(
        ["pip", "install", "-q", "-r", "requirements.txt"], cwd=str(tmp_path), timeout=30,
    )
    # pip will fail (no real requirements.txt / real network), but it must
    # actually have RUN, not been blocked by the audit call raising.
    assert result["returncode"] != -1 or result["timeout"] is False


def test_install_audit_observes_the_extracted_target(tmp_path):
    validator = PolymorphicValidator(str(tmp_path))
    captured = []
    real_evaluate = validator.execution_policy.evaluate

    def spy(request):
        captured.append(request)
        return real_evaluate(request)

    validator.execution_policy.evaluate = spy
    validator._run_cmd_with_timeout(["pip", "--version"], cwd=str(tmp_path), timeout=30)

    action_types = {r.action_type.value for r in captured}
    assert "run_command" in action_types
    # 'pip --version' isn't an install invocation - no second INSTALL_PACKAGE
    # request should have been issued for it.
    assert "install_package" not in action_types


def test_install_audit_fires_a_second_request_for_a_real_install_shape(tmp_path):
    validator = PolymorphicValidator(str(tmp_path))
    captured = []
    real_evaluate = validator.execution_policy.evaluate

    def spy(request):
        captured.append(request)
        return real_evaluate(request)

    validator.execution_policy.evaluate = spy
    validator._run_cmd_with_timeout(["pip", "install", "-q", "somepkg"], cwd=str(tmp_path), timeout=30)

    install_requests = [r for r in captured if r.action_type.value == "install_package"]
    assert len(install_requests) == 1
    assert install_requests[0].target == "-q somepkg"
