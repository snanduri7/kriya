from kriya.policy.execution import ExecutionPolicy, extract_install_package_target
from kriya.policy.model import ActionRequest, ActionType, PolicyDecision


def test_registry_package_requires_approval():
    policy = ExecutionPolicy()
    for target in ("requests==2.31.0", "-r requirements.txt", "left-pad", "--path vendor/bundle"):
        result = policy.evaluate(ActionRequest(action_type=ActionType.INSTALL_PACKAGE, target=target))
        assert result.decision == PolicyDecision.REQUIRE_APPROVAL, target
        assert result.reason_code == "PACKAGE_INSTALL_REQUIRES_APPROVAL"
        assert result.requires_approval is True


def test_url_or_git_source_is_denied():
    policy = ExecutionPolicy()
    for target in (
        "git+https://github.com/evil/pkg.git",
        "https://example.com/pkg.tar.gz",
        "git@github.com:evil/pkg.git",
        "ssh://git@github.com/evil/pkg.git",
    ):
        result = policy.evaluate(ActionRequest(action_type=ActionType.INSTALL_PACKAGE, target=target))
        assert result.decision == PolicyDecision.DENY, target
        assert result.reason_code == "UNKNOWN_PACKAGE_SOURCE_DENIED"


def test_package_supply_chain_stage_ignores_other_action_types():
    policy = ExecutionPolicy()
    result = policy.evaluate(ActionRequest(action_type=ActionType.RUN_COMMAND, command=("mvn", "test")))
    assert result.reason_code not in ("PACKAGE_INSTALL_REQUIRES_APPROVAL", "UNKNOWN_PACKAGE_SOURCE_DENIED")


def test_extract_install_package_target_detects_pip():
    assert extract_install_package_target(("pip", "install", "-q", "-r", "requirements.txt")) == "-q -r requirements.txt"
    assert extract_install_package_target(("pip3", "install", "requests")) == "requests"


def test_extract_install_package_target_detects_venv_qualified_pip():
    """Real shape kriya/tools/validate.py's _ensure_project_venv actually
    invokes: [venv_python, '-m', 'pip', 'install', ...]."""
    result = extract_install_package_target((
        "/repo/.kriya/venv/bin/python", "-m", "pip", "install", "-q", "-r", "requirements.txt", "pytest",
    ))
    assert result == "-q -r requirements.txt pytest"


def test_extract_install_package_target_detects_other_package_managers():
    assert extract_install_package_target(("npm", "install", "left-pad")) == "left-pad"
    assert extract_install_package_target(("bundle", "install", "--path", "vendor/bundle")) == "--path vendor/bundle"
    assert extract_install_package_target(("gem", "install", "rails")) == "rails"
    assert extract_install_package_target(("cargo", "add", "serde")) == "serde"


def test_extract_install_package_target_returns_none_for_non_install_commands():
    assert extract_install_package_target(("mvn", "clean", "compile")) is None
    assert extract_install_package_target(("pytest", "-x")) is None
    assert extract_install_package_target(("git", "status")) is None


def test_extract_install_package_target_returns_none_when_no_trailing_arguments():
    """A bare 'bundle install' (installs everything from Gemfile, no single
    named target) isn't itself suspicious - nothing to label."""
    assert extract_install_package_target(("bundle", "install")) is None
    assert extract_install_package_target(("pip", "install")) is None
