"""D2 EXEC-LAYER S2: the canonical external-execution containment decision and every consumer of it.

The ruling: S2 builds ONE decision owner (typed refusals, every consumer routed through it, nothing duplicated, a complete tripwire) and KEEPS THE OLD
PACKAGED DEFAULTS OPERATIONAL: ``(contained_execution_required False, backend "none")`` is "host execution permitted" and refuses nothing. The
migration later activates mandatory containment AT THE OWNER, and every consumer must follow with no second hunt. This file proves both halves:

A. the owner's decision table and typed refusals;
B. the old defaults: no consumer is contained, nothing is refused;
C. the activation proof: ONE patched function (``_containment_is_mandatory``) turns every routed consumer contained, with no change at any consumer;
D. mandatory containment with a backend that cannot isolate is a typed refusal at each consumer that must refuse, and a reason where one degrades;
E. identity digests keep their bytes (a routing change must not make every stored digest stale).
"""
from __future__ import annotations

import asyncio
import json
import os
from types import SimpleNamespace

import pytest
from _plugin_test_support import load_core_tools_module

from kriya.capabilities.pip import PipBuildAdapter
from kriya.config import AppConfig
from kriya.static_analysis.service import execution_context, settings_digest
from kriya.tools import containment
from kriya.tools.containment import (
    CONTAINMENT_BACKEND_PROVIDES_NO_ISOLATION,
    CONTAINMENT_BACKEND_UNKNOWN,
    CONTAINMENT_NOT_SUPPORTED_FOR_SUBJECT,
    CONTAINMENT_REQUIRED,
    HOST_EXECUTION_PERMITTED,
    BackendUnavailableError,
    ContainmentSetupError,
    ExternalExecutionRefused,
    NullContainmentBackend,
    backend_provides_isolation,
    decide_external_execution,
)
from kriya.tools.lsp import JdtlsClient
from kriya.tools.process import ProcessResult
from kriya.tools.tool import ToolExecutionError
from kriya.tools.validate import PolymorphicValidator
from kriya.workflow.baseline_policy import baseline_environment_identity
from kriya.workflow.egress_authority import describe_run_egress
from kriya.workflow.resume_fingerprints import toolchain_fingerprint

ShellTool = load_core_tools_module().ShellTool


def _autonomy(required: bool = False, backend: str = "none"):
    return SimpleNamespace(contained_execution_required=required, containment_backend=backend)


def _config(required: bool = False, backend: str = "none") -> AppConfig:
    cfg = AppConfig()
    cfg.autonomy.contained_execution_required = required
    cfg.autonomy.containment_backend = backend
    return cfg


@pytest.fixture
def activated(monkeypatch):
    """Mandatory containment activated at the ONE place the owner defines it. No consumer is touched."""
    monkeypatch.setattr(containment, "_containment_is_mandatory", lambda autonomy, subject: True)


# ------------------------------------------------------------------ A. the decision table and the typed refusals

@pytest.mark.parametrize("required, backend, contained, reason, refusal", [
    (False, "none", False, HOST_EXECUTION_PERMITTED, None),                                   # the old packaged defaults: operational
    (False, "oci", False, HOST_EXECUTION_PERMITTED, None),                                    # a configured backend without the mandate
    (True, "oci", True, CONTAINMENT_REQUIRED, None),
    (True, "none", True, CONTAINMENT_REQUIRED, CONTAINMENT_BACKEND_PROVIDES_NO_ISOLATION),    # mandatory, nothing to isolate with
    (True, "no-such-backend", True, CONTAINMENT_REQUIRED, CONTAINMENT_BACKEND_UNKNOWN),       # a typo is never a silent host fallback
])
def test_the_decision_table(required, backend, contained, reason, refusal):
    decision = decide_external_execution(_autonomy(required, backend), subject="t")
    assert (decision.contained, decision.reason_code, decision.backend_name) == (contained, reason, backend)
    got = decision.refusal()
    assert (got.reason_code if got is not None else None) == refusal
    if refusal is None:
        assert decision.backend() is None if not contained else decision.backend() is not None
    else:
        with pytest.raises(ExternalExecutionRefused) as raised:
            decision.backend()
        assert raised.value.reason_code == refusal and raised.value.subject == "t" and refusal in str(raised.value)


def test_a_refusal_is_a_backend_unavailable_error_so_every_existing_handler_treats_it_as_one():
    refusal = decide_external_execution(_autonomy(True, "none"), subject="ShellTool").refusal()
    assert isinstance(refusal, (BackendUnavailableError, ContainmentSetupError))


def test_a_consumer_without_a_contained_mode_is_refused_only_when_containment_is_mandatory():
    decide_external_execution(_autonomy(False), subject="JdtlsClient").refuse_unsupported("no contained mode")   # permitted: returns
    with pytest.raises(ExternalExecutionRefused) as raised:
        decide_external_execution(_autonomy(True, "oci"), subject="JdtlsClient").refuse_unsupported("no contained mode")
    assert raised.value.reason_code == CONTAINMENT_NOT_SUPPORTED_FOR_SUBJECT


def test_configured_backend_resolves_without_the_mandate_and_unknown_names_still_fail_closed():
    assert decide_external_execution(_autonomy(False, "none"), subject="t").configured_backend().name == "none"
    assert decide_external_execution(_autonomy(False, "oci"), subject="t").configured_backend().name == "oci"
    with pytest.raises(BackendUnavailableError):
        decide_external_execution(_autonomy(False, "typo"), subject="t").configured_backend()


def test_the_isolation_fact_is_defined_once():
    assert backend_provides_isolation(NullContainmentBackend()) is False
    assert backend_provides_isolation(containment.DummyContainmentBackend()) is True


# ------------------------------------------------------------------ B. the old packaged defaults stay operational

def test_the_packaged_defaults_are_host_permitted_and_no_consumer_is_contained_or_refused(tmp_path):
    cfg = AppConfig()
    assert (cfg.autonomy.contained_execution_required, cfg.autonomy.containment_backend) == (False, "none")
    assert PolymorphicValidator(str(tmp_path), autonomy_cfg=cfg.autonomy).build_containment_profile_and_backend() == (None, None)
    assert execution_context(cfg, str(tmp_path)).contained is False
    verification = next(d for d in describe_run_egress(cfg) if d.channel == "verification_execution")
    assert verification.allowed is True and verification.capability.value == "unrestricted"
    assert "containment not required" in toolchain_fingerprint(str(tmp_path), cfg.autonomy).basis
    assert json.loads(baseline_environment_identity(str(tmp_path), cfg.autonomy))["execution"] != "contained"


def test_jdtls_is_not_refused_under_the_old_defaults(tmp_path):
    client = JdtlsClient(str(tmp_path), "/no/such/jdtls", AppConfig().autonomy)
    with pytest.raises(FileNotFoundError):                       # it got as far as trying to spawn: the owner did not refuse
        asyncio.run(client.start())


# ------------------------------------------------------------------ C. the activation proof: ONE function, every consumer follows

def test_activating_the_mandate_at_the_owner_makes_the_validator_contained(activated, tmp_path):
    (tmp_path / "app.py").write_text("print(1)\n")
    profile, backend = PolymorphicValidator(str(tmp_path), autonomy_cfg=_config(False, "oci").autonomy).build_containment_profile_and_backend()
    assert profile is not None and backend.name == "oci"


@pytest.mark.asyncio
async def test_activating_the_mandate_at_the_owner_makes_shell_tool_run_through_the_isolating_backend(activated, tmp_path, monkeypatch):
    seen = {}

    async def run_async(self, command, **kwargs):      # pylint: disable=unused-argument
        seen.update(kwargs)
        return ProcessResult(returncode=0, stdout="", stderr="", timeout=False)

    monkeypatch.setattr("kriya.tools.process.ProcessController.run_async", run_async)
    monkeypatch.chdir(tmp_path)
    await ShellTool(autonomy_cfg=_config(False, "oci").autonomy).execute(command="echo hi")
    assert seen["containment_backend"].name == "oci" and seen["containment_profile"] is not None


def test_activating_the_mandate_at_the_owner_takes_the_pip_capability_onto_its_contained_path(activated, tmp_path):
    (tmp_path / "m.py").write_text("x = 1\n")
    calls = []
    validator = SimpleNamespace(autonomy_cfg=_config(False, "oci").autonomy, workspace_path=str(tmp_path),
                                _run_cmd_with_timeout=lambda cmd, **kw: calls.append(cmd) or {"returncode": 0, "stdout": "", "stderr": ""},
                                _validation_result=lambda ok, output, res: {"success": ok, "output": output})
    PipBuildAdapter().compile(validator, ["m.py"], deadline=None)
    assert calls and "py_compile" in " ".join(calls[0])


def test_the_pip_capability_stays_in_process_under_the_old_defaults(tmp_path):
    (tmp_path / "m.py").write_text("x = 1\n")
    calls = []
    validator = SimpleNamespace(autonomy_cfg=_config().autonomy, workspace_path=str(tmp_path),
                                _run_cmd_with_timeout=lambda cmd, **kw: calls.append(cmd), _validation_result=lambda *a: {})
    PipBuildAdapter().compile(validator, ["m.py"], deadline=None)
    assert calls == []


def test_activating_the_mandate_at_the_owner_contains_static_analysis(activated, tmp_path):
    context = execution_context(_config(False, "oci"), str(tmp_path))
    assert context.contained is True and context.containment_backend.name == "oci"


def test_activating_the_mandate_at_the_owner_changes_the_egress_report(activated):
    verification = next(d for d in describe_run_egress(_config(False, "oci")) if d.channel == "verification_execution")
    assert verification.allowed is False and verification.capability.value == "denied"
    assert verification.containment == {"required": True, "backend": "oci"}


def test_activating_the_mandate_at_the_owner_changes_the_toolchain_identity_path(activated, tmp_path):
    assert "containment not required" not in toolchain_fingerprint(str(tmp_path), _config(False, "oci").autonomy).basis
    assert json.loads(baseline_environment_identity(str(tmp_path), _config(False, "oci").autonomy))["execution"] == "contained"


def test_activating_the_mandate_at_the_owner_stops_jdtls_before_anything_exists(activated, tmp_path):
    client = JdtlsClient(str(tmp_path), "/no/such/jdtls", _config(False, "oci").autonomy)
    with pytest.raises(ExternalExecutionRefused) as raised:
        asyncio.run(client.start())
    assert raised.value.reason_code == CONTAINMENT_NOT_SUPPORTED_FOR_SUBJECT
    assert client._data_dir is None and client.process is None                       # pylint: disable=protected-access


# ------------------------------------------------------------------ D. mandatory containment that cannot isolate: typed refusal / a reason

def test_the_validator_refuses_a_mandatory_run_whose_backend_provides_no_isolation_before_it_builds_anything(tmp_path):
    validator = PolymorphicValidator(str(tmp_path), autonomy_cfg=_config(True, "none").autonomy)
    with pytest.raises(ExternalExecutionRefused) as raised:
        validator.build_containment_profile_and_backend()
    assert raised.value.reason_code == CONTAINMENT_BACKEND_PROVIDES_NO_ISOLATION and raised.value.subject == "PolymorphicValidator"


@pytest.mark.asyncio
async def test_shell_tool_refuses_a_mandatory_command_instead_of_running_it_on_the_host(tmp_path, monkeypatch):
    """Before S2 a mandatory run with backend "none" and an unrestricted network passed the Null backend and ran on the HOST (a gap of the first attempt's
    census too); now it is the owner's typed refusal and nothing is spawned."""
    spawned = []

    async def run_async(self, command, **kwargs):      # pylint: disable=unused-argument
        spawned.append(command)
        return ProcessResult(returncode=0, stdout="", stderr="", timeout=False)

    monkeypatch.setattr("kriya.tools.process.ProcessController.run_async", run_async)
    monkeypatch.chdir(tmp_path)
    cfg = _config(True, "none")
    cfg.autonomy.shell_network = "unrestricted"
    with pytest.raises(ToolExecutionError) as raised:             # the tool framework wraps every tool failure; the typed refusal is its cause
        await ShellTool(autonomy_cfg=cfg.autonomy).execute(command="echo hi")
    assert isinstance(raised.value.__cause__, ExternalExecutionRefused)
    assert raised.value.__cause__.reason_code == CONTAINMENT_BACKEND_PROVIDES_NO_ISOLATION and CONTAINMENT_BACKEND_PROVIDES_NO_ISOLATION in str(raised.value)
    assert spawned == []
    assert os.listdir(tmp_path) == []


def test_static_analysis_degrades_with_a_reason_it_does_not_raise(tmp_path):
    """The provider refuses (never falls back to the host) on its own terms; the context carries the backend the owner resolved."""
    context = execution_context(_config(True, "none"), str(tmp_path))
    assert context.contained is True and not backend_provides_isolation(context.containment_backend)


# ------------------------------------------------------------------ E. identity digests keep their bytes

@pytest.mark.parametrize("required, backend", [(False, "none"), (False, "oci"), (True, "oci"), (True, "none")])
def test_the_static_analysis_settings_digest_is_byte_identical_to_the_pre_routing_formula(required, backend):
    from kriya.static_analysis.service import canonical_digest
    cfg = _config(required, backend)
    legacy = canonical_digest({
        "static_analysis": cfg.static_analysis.model_dump(mode="json"),
        "contained_execution_required": cfg.autonomy.contained_execution_required,
        "containment_backend": cfg.autonomy.containment_backend,
        "egress_policy": cfg.autonomy.egress_policy,
        "runtime_profile": cfg.runtime_profile,
    })
    assert settings_digest(cfg) == legacy
