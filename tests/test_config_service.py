"""``ConfigService`` (full modularization D3; MODULE-CONTRACTS §3): a request's configuration resolved from its ``RequestContext``.

Its outcomes are pinned on every terminal path (loaded, approved, refused by SEC-009, unreadable), its environment is injected (a fake
trust file reaches ``load_config``), it stands in the request's folder only under CONTEXT's one working-directory lock (the same object
the KUP catalog operations hold), and the services that used to be HANDED a resolution by the KUP adapter (``load_in``, ``state``,
``trust_file``) now resolve through it. The byte-identical preservation is tests/contracts/test_config_resolution_contract.py.
"""
from __future__ import annotations

import os
from typing import Dict, Optional

import pytest
import yaml

from kriya.config.service import (
    CONFIG_AUTHORITY_REFUSED,
    CONFIG_LOAD_FAILED,
    NOT_SUPPORTED_SCOPE,
    ConfigScopeNotSupported,
    ConfigService,
    ResolvedConfiguration,
)
from kriya.context.model import RequestContext, ScopeRef
from kriya.context.working_directory import WORKING_DIRECTORY_LOCK

PROTECTED = {"llm": {"base_url": "http://operator:pass@127.0.0.1:11434/v1"}}


class FakeEnvironment:
    def __init__(self, home: str, variables: Optional[Dict[str, str]] = None) -> None:
        self.home, self.variables = home, dict(variables or {})

    def variable(self, name: str, default: Optional[str] = None) -> Optional[str]:
        return self.variables.get(name, default)

    def snapshot(self) -> Dict[str, str]:
        return dict(self.variables)

    def expand_user(self, path: str) -> str:
        return self.home + path[1:] if path.startswith("~") else path

    def user_home(self) -> str:
        return self.home


@pytest.fixture
def folders(tmp_path, monkeypatch):
    monkeypatch.setenv("KRIYA_AUTHORITY_HOME", str(tmp_path / "authority"))
    monkeypatch.delenv("KRIYA_TRUST_FILE", raising=False)
    made = {}
    for name, content in {"plain": {"llm": {"model": "m-plain"}}, "protected": PROTECTED, "refused": {"execution_policy": {"enabled": True}},
                          "malformed": "llm: [unclosed\n", "empty": None}.items():
        folder = tmp_path / name
        folder.mkdir()
        if content is not None:
            (folder / "kriya.yaml").write_text(content if isinstance(content, str) else yaml.safe_dump(content))
        made[name] = str(folder)
    return made


def _resolve(folder: str, service: Optional[ConfigService] = None) -> ResolvedConfiguration:
    return (service or ConfigService()).resolve(RequestContext.local(folder))


def test_a_loaded_folder_has_its_configuration_and_no_blocker(folders):
    resolved = _resolve(folders["plain"])
    assert resolved.app is not None and resolved.app.llm.model == "m-plain"
    assert (resolved.failure, resolved.authority.as_dict()) == (None, {"status": "no_findings", "fields": []})
    assert resolved.config_source == "auto_discovered_cwd" and os.path.samefile(resolved.config_path, os.path.join(folders["plain"], "kriya.yaml"))
    assert resolved.as_load_in() == (resolved.state, resolved.app, {"status": "no_findings", "fields": []}, None)


def test_an_unapproved_protected_field_is_the_typed_authority_blocker_with_the_field(folders):
    resolved = _resolve(folders["protected"])
    assert resolved.app is None and resolved.state is not None
    assert resolved.failure["code"] == CONFIG_AUTHORITY_REFUSED and "llm.base_url" in resolved.failure["message"]
    assert resolved.authority.status == "refused"
    assert [f["field"] for f in resolved.authority.fields] == ["llm.base_url"]
    assert set(resolved.authority.fields[0]) == {"field", "source", "classification", "reason"}


def test_an_approved_protected_field_loads_and_reports_the_approved_fields(folders):
    from kriya.config.authority_approval import grant_local_approval

    state = _resolve(folders["protected"]).state
    grant_local_approval(state.violations, state.config_dict, state.workspace_root)
    resolved = _resolve(folders["protected"])
    assert resolved.app is not None and resolved.failure is None
    assert resolved.authority.as_dict() == {"status": "approved", "fields": ["llm.base_url"]}


def test_an_unreadable_folder_is_the_typed_load_blocker_never_a_traceback(folders):
    resolved = _resolve(folders["malformed"])
    assert (resolved.app, resolved.state) == (None, None)
    assert resolved.failure["code"] == CONFIG_LOAD_FAILED and resolved.failure["message"].startswith("ValueError: Failed to load configuration")
    assert resolved.authority.as_dict() == {"status": "failed", "fields": []}


def test_the_injected_environment_supplies_the_trust_file(folders, tmp_path, monkeypatch):
    """A fake environment's KRIYA_TRUST_FILE (inside the folder: refused) is the one load_config consults, not the process's (unset)."""
    inside = os.path.join(folders["protected"], "trust.json")
    service = ConfigService(FakeEnvironment(str(tmp_path / "fake-home"), {"KRIYA_TRUST_FILE": inside}))
    assert service.trust_file() == inside
    resolved = _resolve(folders["protected"], service)
    assert resolved.failure["code"] == CONFIG_LOAD_FAILED and "TrustPathInsideWorkspaceError" in resolved.failure["message"]
    assert _resolve(folders["protected"]).failure["code"] == CONFIG_AUTHORITY_REFUSED, "the process environment has no trust file"
    monkeypatch.setenv("KRIYA_LOG_DIR", str(tmp_path / "process-logs"))
    fake_logs = ConfigService(FakeEnvironment(str(tmp_path / "fake-home"), {"KRIYA_LOG_DIR": str(tmp_path / "fake-logs")}))
    assert _resolve(folders["plain"], fake_logs).log_directory() == (os.path.realpath(str(tmp_path / "fake-logs")), "env")


def test_an_explicit_trust_file_argument_is_the_one_load_config_consults_even_with_no_environment_trust_file(folders):
    """Found by the D3 orchestrator mutant (trust_file dropped on the way to load_config survived every test): the argument, not only the
    environment, must reach the authority check - a trust file inside the folder is refused, the same folder without one is the authority blocker."""
    inside = os.path.join(folders["protected"], "trust.json")
    explicit = ConfigService().resolve(RequestContext.local(folders["protected"]), trust_file=inside)
    assert explicit.failure["code"] == CONFIG_LOAD_FAILED and "TrustPathInsideWorkspaceError" in explicit.failure["message"]
    assert _resolve(folders["protected"]).failure["code"] == CONFIG_AUTHORITY_REFUSED, "control: no argument and no environment trust file"


def test_the_folder_is_entered_only_under_the_one_lock_and_left_afterwards(folders, monkeypatch):
    from kriya.config import config as config_module
    from kriya.kup import execution

    assert execution._CWD_LOCK is WORKING_DIRECTORY_LOCK, "the KUP skill discovery and the configuration resolution share ONE lock"
    seen = {}
    real = config_module.resolve_config_state

    def spy(config_path=None):
        seen.update(locked=WORKING_DIRECTORY_LOCK.locked(), cwd=os.getcwd())
        return real(config_path)

    monkeypatch.setattr(config_module, "resolve_config_state", spy)
    before = os.getcwd()
    _resolve(folders["plain"])
    assert seen["locked"] is True and os.path.samefile(seen["cwd"], folders["plain"])
    assert os.getcwd() == before and not WORKING_DIRECTORY_LOCK.locked()


def test_a_request_without_a_workspace_resolves_in_place(folders, monkeypatch):
    monkeypatch.chdir(folders["plain"])
    resolved = ConfigService().resolve(RequestContext.local(None))
    assert resolved.app.llm.model == "m-plain"


def test_a_scope_this_resolution_does_not_layer_is_refused_never_ignored(folders):
    with pytest.raises(ConfigScopeNotSupported) as refused:
        ConfigService().resolve(RequestContext.local(folders["plain"]), scopes=(ScopeRef("organization", "acme"),))
    assert refused.value.reason_code == NOT_SUPPORTED_SCOPE
    ok = ConfigService().resolve(RequestContext.local(folders["plain"]), scopes=(ScopeRef("project", "p"), ScopeRef("user", "local")))
    assert ok.app is not None


def test_the_kup_load_in_is_the_service_resolution(folders):
    from kriya.kup.execution import _load_in

    for name in ("plain", "protected", "malformed"):
        legacy, resolved = _load_in(folders[name]), _resolve(folders[name])
        assert legacy[1:] == resolved.as_load_in()[1:], name
        assert (legacy[0] is None) == (resolved.state is None)


def test_the_authority_port_reports_the_service_authority(folders):
    from kriya.policy.authority import AuthorityPort

    ctx = RequestContext.local(folders["protected"])
    assert AuthorityPort().config_authority(ctx) == ConfigService().resolve(ctx).authority
    assert AuthorityPort().config_authority(ctx).status == "refused"


def test_the_approval_resolves_compares_and_writes_under_one_hold(folders, monkeypatch):
    """configuration.approve: the working directory cannot change between the comparison and the write (the lock is held, the folder
    entered, when the approval is written)."""
    from kriya.application.configuration import ConfigurationService
    from kriya.config import authority_approval

    status = ConfigurationService().approval_status(RequestContext.local(folders["protected"]), folders["protected"])
    real, seen = authority_approval.grant_local_approval, {}

    def spy(*args, **kwargs):
        seen.update(locked=WORKING_DIRECTORY_LOCK.locked(), cwd=os.getcwd())
        return real(*args, **kwargs)

    monkeypatch.setattr(authority_approval, "grant_local_approval", spy)
    answer = ConfigurationService().approve(RequestContext.local(folders["protected"]), status["fingerprint"])
    assert answer["outcome"] == "approved"
    assert seen["locked"] is True and os.path.samefile(seen["cwd"], folders["protected"])


def test_the_unreadable_folder_is_the_typed_refusal_of_the_configuration_service(folders):
    from kriya.application.configuration import ConfigurationService
    from kriya.application.services import ApplicationRefusal

    with pytest.raises(ApplicationRefusal) as refused:
        ConfigurationService().approval_status(RequestContext.local(folders["malformed"]), folders["malformed"])
    assert refused.value.reason_code == CONFIG_LOAD_FAILED
    assert refused.value.message.startswith("the configuration cannot be read: ValueError: Failed to load configuration")


def test_the_client_contract_no_longer_carries_the_resolution_mechanisms():
    from kriya.client.contract import LOCAL_ONLY_PARAMETERS

    assert not any(param in ("load_in", "state", "trust_file") for (_port, _method, param) in LOCAL_ONLY_PARAMETERS)
    assert "D3" not in set(LOCAL_ONLY_PARAMETERS.values())
    assert {LOCAL_ONLY_PARAMETERS[("runs", "start", "cfg")], LOCAL_ONLY_PARAMETERS[("history", "list", "cfg")]} == {"O2", "O1"}


def test_the_members_still_to_come_name_real_open_slices():
    from kriya.config.service import ARRIVES_LATER
    from tests.architecture.test_import_direction import DONE_SLICES, SLICES

    assert set(ARRIVES_LATER) == {"fingerprint", "state_dir"}
    assert all(slice_id in SLICES and slice_id not in DONE_SLICES for slice_id in ARRIVES_LATER.values())
    assert not any(hasattr(ResolvedConfiguration, name) for name in ARRIVES_LATER), "a member arriving later must not exist as a stub"
