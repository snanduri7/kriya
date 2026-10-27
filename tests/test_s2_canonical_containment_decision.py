"""D2 EXEC-LAYER S2 (rewritten by S7, the packaged-default migration): the canonical external-execution containment decision and every consumer of it.

The final contract (owner decision 2026-10-25): there is NO operator opt-out. ``_containment_is_mandatory`` is unconditional, so every consumer follows the ONE owner: a
project-controlled workload runs in an isolating backend or is refused typed BEFORE a process starts. The legacy ``HOST_EXECUTION_PERMITTED`` state no longer exists
(the constant is gone; the host rows of the old decision table, the "old defaults stay operational" proofs and the "activate the mandate" patch are replaced by the
default-configuration proofs below). This file proves:

A. the owner's decision table and typed refusals - the mandate configuration fields are NOT read (forged False still contained);
B. the packaged defaults ARE the secure contract: every consumer is contained or refused, nothing is host-permitted;
C. each routed consumer follows the owner with the packaged defaults (no patch, no per-consumer change);
D. mandatory containment with a backend that cannot isolate is a typed refusal at each consumer that must refuse, and a reason where one degrades;
E. identity digests keep their formula (a routing change must not make every stored digest stale; the VALUES are the new secure-default identity).
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


def _autonomy(backend: str = "oci", forged_flag: bool = False):
    """A forged/legacy autonomy object: the (removed) opt-out flags say False. The owner must not read them."""
    return SimpleNamespace(contained_execution_required=forged_flag, mcp_contained_execution_required=forged_flag, containment_backend=backend)


def _config(backend: str = "oci") -> AppConfig:
    cfg = AppConfig()
    cfg.autonomy.containment_backend = backend
    return cfg


# ------------------------------------------------------------------ A. the decision table and the typed refusals

@pytest.mark.parametrize("backend, refusal", [
    ("oci", None),
    ("none", CONTAINMENT_BACKEND_PROVIDES_NO_ISOLATION),    # nothing to isolate with: refused, never a host run
    ("no-such-backend", CONTAINMENT_BACKEND_UNKNOWN),       # a typo is never a silent host fallback
])
def test_the_decision_table(backend, refusal):
    decision = decide_external_execution(_autonomy(backend), subject="t")             # forged flags say False: the owner does not read them
    assert (decision.contained, decision.reason_code, decision.backend_name) == (True, CONTAINMENT_REQUIRED, backend)
    got = decision.refusal()
    assert (got.reason_code if got is not None else None) == refusal
    if refusal is None:
        assert decision.backend() is not None
    else:
        with pytest.raises(ExternalExecutionRefused) as raised:
            decision.backend()
        assert raised.value.reason_code == refusal and raised.value.subject == "t" and refusal in str(raised.value)


def test_a_refusal_is_a_backend_unavailable_error_so_every_existing_handler_treats_it_as_one():
    refusal = decide_external_execution(_autonomy("none"), subject="ShellTool").refusal()
    assert isinstance(refusal, (BackendUnavailableError, ContainmentSetupError))


@pytest.mark.parametrize("backend", ["oci", "none"])
def test_a_consumer_without_a_contained_mode_is_always_refused(backend):
    with pytest.raises(ExternalExecutionRefused) as raised:
        decide_external_execution(_autonomy(backend), subject="JdtlsClient").refuse_unsupported("no contained mode")
    assert raised.value.reason_code == CONTAINMENT_NOT_SUPPORTED_FOR_SUBJECT


def test_configured_backend_resolves_without_the_mandate_and_unknown_names_still_fail_closed():
    assert decide_external_execution(_autonomy("none"), subject="t").configured_backend().name == "none"
    assert decide_external_execution(_autonomy("oci"), subject="t").configured_backend().name == "oci"
    with pytest.raises(BackendUnavailableError):
        decide_external_execution(_autonomy("typo"), subject="t").configured_backend()


def test_the_isolation_fact_is_defined_once():
    assert backend_provides_isolation(NullContainmentBackend()) is False
    assert backend_provides_isolation(containment.DummyContainmentBackend()) is True


def test_the_legacy_host_state_does_not_exist_any_more():
    assert not hasattr(containment, "HOST_EXECUTION_PERMITTED")
    assert "HOST_EXECUTION_PERMITTED" not in containment.EXTERNAL_EXECUTION_REFUSAL_CODES
    owner = containment._containment_is_mandatory                                                            # pylint: disable=protected-access
    assert owner(_autonomy("oci", forged_flag=False), "PolymorphicValidator") is True
    assert owner(object(), "MCP server 'x'") is True                                                          # reads nothing, so nothing can open a host path


# ------------------------------------------------------------------ B. the packaged defaults ARE the secure contract

def test_the_packaged_defaults_are_the_secure_contract_and_no_consumer_is_host_permitted(tmp_path):
    cfg = AppConfig()
    assert (cfg.autonomy.contained_execution_required, cfg.autonomy.mcp_contained_execution_required, cfg.autonomy.containment_backend) == (True, True, "oci")
    profile, backend = PolymorphicValidator(str(tmp_path), autonomy_cfg=cfg.autonomy).build_containment_profile_and_backend()
    assert profile is not None and backend is not None
    context = execution_context(cfg, str(tmp_path))
    # S8: the context carries no host/contained flag any more (contained is the only mode); the owner's decision is the fact
    assert containment.decide_external_execution(cfg.autonomy, subject="static_analysis").contained is True and context.containment_backend is not None
    verification = next(d for d in describe_run_egress(cfg) if d.channel == "verification_execution")
    assert verification.allowed is False and verification.containment == {"required": True, "backend": "oci"}
    assert "containment not required" not in toolchain_fingerprint(str(tmp_path), cfg.autonomy).basis
    assert json.loads(baseline_environment_identity(str(tmp_path), cfg.autonomy))["execution"] == "contained"


def test_jdtls_is_refused_with_the_packaged_defaults_before_anything_exists(tmp_path):
    client = JdtlsClient(str(tmp_path), "/no/such/jdtls", AppConfig().autonomy)
    with pytest.raises(ExternalExecutionRefused) as raised:
        asyncio.run(client.start())
    assert raised.value.reason_code == CONTAINMENT_NOT_SUPPORTED_FOR_SUBJECT
    assert getattr(client, "_data_dir", None) is None and getattr(client, "process", None) is None  # nothing was created (the machinery that held them is gone, S8-ORPHAN-HOST-CODE-001)


# ------------------------------------------------------------------ C. every routed consumer follows the owner with the packaged defaults

def test_the_validator_is_contained_by_default(tmp_path):
    (tmp_path / "app.py").write_text("print(1)\n")
    profile, backend = PolymorphicValidator(str(tmp_path), autonomy_cfg=_config().autonomy).build_containment_profile_and_backend()
    assert profile is not None and backend.name == "oci"


@pytest.mark.asyncio
async def test_shell_tool_runs_through_the_isolating_backend_by_default(tmp_path, monkeypatch):
    seen = {}

    async def run_async(self, command, **kwargs):      # pylint: disable=unused-argument
        seen.update(kwargs)
        return ProcessResult(returncode=0, stdout="", stderr="", timeout=False)

    monkeypatch.setattr("kriya.tools.process.ProcessController.run_async", run_async)
    monkeypatch.chdir(tmp_path)
    await ShellTool(autonomy_cfg=_config().autonomy).execute(command="echo hi")
    assert seen["containment_backend"].name == "oci" and seen["containment_profile"] is not None


def test_the_pip_capability_takes_its_contained_path_by_default(tmp_path):
    (tmp_path / "m.py").write_text("x = 1\n")
    calls = []
    validator = SimpleNamespace(autonomy_cfg=_config().autonomy, workspace_path=str(tmp_path),
                                _run_cmd_with_timeout=lambda cmd, **kw: calls.append(cmd) or {"returncode": 0, "stdout": "", "stderr": ""},
                                _validation_result=lambda ok, output, res: {"success": ok, "output": output})
    PipBuildAdapter().compile(validator, ["m.py"], deadline=None)
    assert calls and "py_compile" in " ".join(calls[0])


def test_static_analysis_is_contained_by_default(tmp_path):
    context = execution_context(_config(), str(tmp_path))
    assert containment.decide_external_execution(_config().autonomy, subject="static_analysis").contained is True and context.containment_backend.name == "oci"


# ------------------------------------------------------------------ D. mandatory containment that cannot isolate: typed refusal / a reason

def test_the_validator_refuses_a_mandatory_run_whose_backend_provides_no_isolation_before_it_builds_anything(tmp_path):
    validator = PolymorphicValidator(str(tmp_path), autonomy_cfg=_config("none").autonomy)
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
    cfg = _config("none")
    cfg.autonomy.shell_network = "unrestricted"
    with pytest.raises(ToolExecutionError) as raised:             # the tool framework wraps every tool failure; the typed refusal is its cause
        await ShellTool(autonomy_cfg=cfg.autonomy).execute(command="echo hi")
    assert isinstance(raised.value.__cause__, ExternalExecutionRefused)
    assert raised.value.__cause__.reason_code == CONTAINMENT_BACKEND_PROVIDES_NO_ISOLATION and CONTAINMENT_BACKEND_PROVIDES_NO_ISOLATION in str(raised.value)
    assert spawned == []
    assert os.listdir(tmp_path) == []


def test_static_analysis_degrades_with_a_reason_it_does_not_raise(tmp_path):
    """The provider refuses (never falls back to the host) on its own terms; the context carries the backend the owner resolved."""
    context = execution_context(_config("none"), str(tmp_path))
    assert containment.decide_external_execution(_config("none").autonomy, subject="static_analysis").contained is True and not backend_provides_isolation(context.containment_backend)


# ------------------------------------------------------------------ E. identity digests keep their bytes

@pytest.mark.parametrize("backend", ["oci", "none"])
def test_the_static_analysis_settings_digest_keeps_its_formula(backend):
    """The digest formula (field names and their combination) is unchanged by the routing; its VALUES are the secure-default identity (contained true)."""
    from kriya.static_analysis.service import canonical_digest
    cfg = _config(backend)
    legacy = canonical_digest({
        "static_analysis": cfg.static_analysis.model_dump(mode="json"),
        "contained_execution_required": cfg.autonomy.contained_execution_required,
        "containment_backend": cfg.autonomy.containment_backend,
        "egress_policy": cfg.autonomy.egress_policy,
        "runtime_profile": cfg.runtime_profile,
    })
    assert settings_digest(cfg) == legacy
