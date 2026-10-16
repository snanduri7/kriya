"""CONFIG's one environment reader (full modularization D3; MODULE-CONTRACTS §3 ``Environment``).

* Every Kriya home is redirected by a FAKE ``Environment`` handed to its owner's function: nothing reads the process environment or the
  real home behind it.
* The real ``ProcessEnvironment`` reads at CALL time: a variable changed between two calls is seen by the second (the suite's
  ``tests/conftest.py`` isolation depends on it).
* Each home is read from exactly its documented variable (a per-home table: renaming any one variable fails its row).
* The direct-access tripwire's env/home tables (tests/architecture/test_direct_access_owners.py) hold no Kriya-home reader any more.
"""
from __future__ import annotations

import os
from typing import Dict, Optional

import pytest

from kriya.config import environment as env
from kriya.config.config import AppConfig


class FakeEnvironment:
    """An ``Environment`` with its own variables and home; it never touches ``os.environ``."""

    def __init__(self, home: str, variables: Optional[Dict[str, str]] = None) -> None:
        self.home = home
        self.variables = dict(variables or {})
        self.reads = []

    def variable(self, name: str, default: Optional[str] = None) -> Optional[str]:
        if name not in env.VARIABLES:
            raise env.UnknownEnvironmentVariable(name)
        self.reads.append(name)
        return self.variables.get(name, default)

    def snapshot(self) -> Dict[str, str]:
        return dict(self.variables)

    def expand_user(self, path: str) -> str:
        return self.home + path[1:] if path == "~" or path.startswith("~/") else path

    def user_home(self) -> str:
        return self.home


def _homes(fake, workspace: str) -> Dict[str, str]:
    """Every Kriya home, asked of its OWNER module with the fake environment injected."""
    from kriya.config import authority_approval
    from kriya.core import logging_setup, model_certification, model_qualification, state_paths
    from kriya.mcp import invocation_approval
    from kriya.metrics import adjudication
    from kriya.static_analysis import waivers

    return {
        "state": state_paths.resolve_state_directory(AppConfig(), fake)[0],
        "state_default": state_paths.default_state_directory(fake),
        "logs": logging_setup.resolve_log_directory(AppConfig(), fake)[0],
        "logs_default": logging_setup.default_log_directory(fake),
        "authority": authority_approval._authority_home_dir(fake),
        "mcp_approvals": invocation_approval._approval_home_dir(fake),
        "waivers": os.path.dirname(waivers.waiver_store_path(None, workspace, fake)),
        "qualifications": model_qualification.qualification_home(fake),
        "certifications": model_certification.certification_home(fake),
        "adjudications": os.path.dirname(adjudication.store_path(fake)),
    }


DEFAULT_HOMES = {"state": ("state",), "state_default": ("state",), "logs": ("logs",), "logs_default": ("logs",), "authority": ("authority",),
                 "mcp_approvals": ("mcp_approvals",), "waivers": ("static_analysis", "waivers"), "qualifications": ("qualifications",),
                 "certifications": ("certifications",), "adjudications": ("adjudications",)}
# home -> the ONE variable that overrides it (MODULE-CONTRACTS §3)
HOME_VARIABLES = {"state": "KRIYA_STATE_DIR", "logs": "KRIYA_LOG_DIR", "authority": "KRIYA_AUTHORITY_HOME",
                  "mcp_approvals": "KRIYA_MCP_APPROVAL_HOME", "waivers": "KRIYA_STATIC_ANALYSIS_HOME", "qualifications": "KRIYA_QUALIFICATION_HOME",
                  "certifications": "KRIYA_CERTIFICATION_HOME", "adjudications": "KRIYA_ADJUDICATION_HOME"}


def test_a_fake_environment_redirects_every_home_and_the_process_is_never_read(tmp_path, monkeypatch):
    fake_home = os.path.realpath(str(tmp_path / "fake-home"))
    os.makedirs(fake_home)
    for name in HOME_VARIABLES.values():  # the PROCESS points every home somewhere else: none of it may leak through
        monkeypatch.setenv(name, str(tmp_path / "process" / name))
    monkeypatch.setenv("HOME", str(tmp_path / "process-home"))
    fake = FakeEnvironment(fake_home)
    homes = _homes(fake, str(tmp_path / "ws"))
    assert homes == {name: os.path.join(fake_home, ".kriya", *parts) for name, parts in DEFAULT_HOMES.items()}
    assert set(fake.reads) >= set(HOME_VARIABLES.values()), "every home consulted the fake's variable"


@pytest.mark.parametrize("home", sorted(HOME_VARIABLES))
def test_each_home_is_overridden_by_exactly_its_documented_variable(home, tmp_path):
    fake_home = os.path.realpath(str(tmp_path / "fake-home"))
    target = os.path.realpath(str(tmp_path / "override" / home))
    variable = HOME_VARIABLES[home]
    others = {name: str(tmp_path / "wrong" / name) for name in HOME_VARIABLES.values() if name != variable}
    redirected = _homes(FakeEnvironment(fake_home, {variable: target}), str(tmp_path / "ws"))[home]
    assert redirected == target, f"{home} must follow {variable}"
    untouched = _homes(FakeEnvironment(fake_home, others), str(tmp_path / "ws"))[home]
    assert untouched == os.path.join(fake_home, ".kriya", *DEFAULT_HOMES[home]), f"{home} must follow no other variable"


@pytest.mark.parametrize("home", sorted(HOME_VARIABLES))
def test_the_process_environment_supplies_each_home_from_its_documented_variable(home, tmp_path, monkeypatch):
    """The REAL reader (no fake): setting the documented variable in the process is what moves the home."""
    target = os.path.realpath(str(tmp_path / "process-override" / home))
    monkeypatch.setenv(HOME_VARIABLES[home], target)
    assert _homes(None, str(tmp_path / "ws"))[home] == target


def test_reads_at_call_time(tmp_path, monkeypatch):
    """No value is cached anywhere on the way: a variable changed between two calls is seen by the second, for every home and switch."""
    real = env.process_environment()
    for name in sorted(env.VARIABLES):
        monkeypatch.setenv(name, "first-" + name)
        assert real.variable(name) == "first-" + name
        monkeypatch.setenv(name, "second-" + name)
        assert real.variable(name) == "second-" + name, f"{name} was cached"
        monkeypatch.delenv(name)
        assert real.variable(name, "default") == "default"
    monkeypatch.setenv("HOME", str(tmp_path / "h1"))
    assert real.user_home() == str(tmp_path / "h1") and real.expand_user("~/x") == str(tmp_path / "h1" / "x")
    monkeypatch.setenv("HOME", str(tmp_path / "h2"))
    assert real.user_home() == str(tmp_path / "h2") and env.kriya_home("state") == str(tmp_path / "h2" / ".kriya" / "state")
    monkeypatch.setenv("KRIYA_STATE_DIR", str(tmp_path / "s1"))
    from kriya.core.state_paths import resolve_state_directory

    assert resolve_state_directory(AppConfig())[0] == os.path.realpath(str(tmp_path / "s1"))
    monkeypatch.setenv("KRIYA_STATE_DIR", str(tmp_path / "s2"))
    assert resolve_state_directory(AppConfig())[0] == os.path.realpath(str(tmp_path / "s2"))
    monkeypatch.setenv("KRIYA_MODEL_RUNTIME_PROBE", "1")
    assert env.model_runtime_probe_enabled() is True
    monkeypatch.setenv("KRIYA_MODEL_RUNTIME_PROBE", "off")
    assert env.model_runtime_probe_enabled() is False


def test_the_suite_isolation_reaches_every_reader():
    """tests/conftest.py sets KRIYA_STATE_DIR/KRIYA_LOG_DIR and the qualification/certification homes per test or session: they arrive."""
    from kriya.core import logging_setup, model_certification, model_qualification, state_paths

    assert state_paths.resolve_state_directory(AppConfig()) == (os.path.realpath(os.environ["KRIYA_STATE_DIR"]), "env")
    assert logging_setup.resolve_log_directory(AppConfig()) == (os.path.realpath(os.environ["KRIYA_LOG_DIR"]), "env")
    assert model_qualification.qualification_home() == os.path.realpath(os.environ["KRIYA_QUALIFICATION_HOME"])
    assert model_certification.certification_home() == os.path.realpath(os.environ["KRIYA_CERTIFICATION_HOME"])
    assert env.model_runtime_probe_enabled() is False, "conftest disables the model-runtime probe for mocked tests"


def test_the_owners_historical_variable_names_are_the_readers_names():
    from kriya.config import authority_approval
    from kriya.core import (
        execution_environment,
        logging_setup,
        model_certification,
        model_qualification,
        model_runtime,
        state_paths,
    )
    from kriya.mcp import invocation_approval
    from kriya.metrics import adjudication
    from kriya.static_analysis import waivers

    assert (state_paths.ENV_STATE_DIR, logging_setup.ENV_LOG_DIR, authority_approval.ENV_HOME_OVERRIDE, authority_approval.ENV_TRUST_FILE,
            invocation_approval.ENV_HOME_OVERRIDE, waivers.ENV_HOME_OVERRIDE, model_qualification.QUALIFICATION_HOME_ENV,
            model_certification.CERTIFICATION_HOME_ENV, adjudication.ENV_HOME_OVERRIDE, model_runtime.PROBE_ENV_VAR,
            execution_environment.PROBE_ENV_VAR) == (
        env.STATE_DIR, env.LOG_DIR, env.AUTHORITY_HOME, env.TRUST_FILE, env.MCP_APPROVAL_HOME, env.STATIC_ANALYSIS_HOME, env.QUALIFICATION_HOME,
        env.CERTIFICATION_HOME, env.ADJUDICATION_HOME, env.MODEL_RUNTIME_PROBE, env.EXECUTION_ENVIRONMENT_PROBE)


def test_the_vocabulary_is_closed():
    with pytest.raises(env.UnknownEnvironmentVariable):
        env.process_environment().variable("AWS_SECRET_ACCESS_KEY")
    with pytest.raises(env.UnknownEnvironmentVariable):
        env.toolchain("KRIYA_STATE_DIR")
    with pytest.raises(env.UnknownEnvironmentVariable):
        env.oci_image("PATH", "x")


def test_a_child_environment_is_a_fresh_copy_of_the_process_environment(monkeypatch):
    monkeypatch.setenv("KRIYA_D3_PROBE", "1")
    child = env.child_environment()
    assert child == dict(os.environ) and child is not os.environ
    child["KRIYA_D3_PROBE"] = "changed"
    assert os.environ["KRIYA_D3_PROBE"] == "1", "a child's environment never writes back"


@pytest.mark.parametrize("value, expected", [(None, True), ("", True), ("1", True), ("0", False), (" False ", False), ("no", False),
                                             ("OFF", False), ("yes", True)])
def test_the_probe_switches_keep_their_on_unless_off_rule(value, expected, tmp_path):
    variables = {} if value is None else {"KRIYA_MODEL_RUNTIME_PROBE": value, "KRIYA_EXECUTION_ENVIRONMENT_PROBE": value}
    fake = FakeEnvironment(str(tmp_path), variables)
    assert env.model_runtime_probe_enabled(fake) is expected and env.execution_environment_probe_enabled(fake) is expected


def test_no_module_but_the_reader_reads_a_kriya_home_from_the_environment():
    """The A2 env/home tables shrink to the owners: only the PLATFORM toolchain probe's PATH remains (L1), no home site at all."""
    from tests.architecture import test_direct_access_owners as direct

    assert set(direct.ALLOWLIST["os_environ"]) == {"kriya.platform.toolchain_locator"}
    assert direct.ALLOWLIST["home"] == {}
    assert "kriya.config.environment" in direct.TABLES["os_environ"].owner_files
    assert "kriya.config.environment" in direct.TABLES["home"].owner_files
