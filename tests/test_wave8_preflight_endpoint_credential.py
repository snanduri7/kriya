"""WAVE8-PREFLIGHT-ENDPOINT-CREDENTIAL-001 (OI-156): a credential inside the configured endpoint URL never reaches a persisted or served
record. The runtime keeps the original ``llm.base_url`` in memory; the preflight (-> the 0600 launch record and ``execution.preflight``) and
the qualification refusal detail carry only the endpoint as it may be SHOWN (scheme, host, port, path).

Measured defect (2026-11-06, Wave-8 reconciliation): ``_model_identity`` put ``llm.base_url`` raw into ``preflight.model.endpoint``; a URL with
userinfo reached the launch record. The qualification case ``context_capacity`` recorded the raw URL in its "non-local endpoint" refusal.
"""
import json
import os

import pytest

from kriya.application import run as run_module
from kriya.config import AppConfig
from kriya.context.workspace_identity import canonical_workspace, workspace_identity
from kriya.control.launch import LaunchSession

SECRET = "pw-WAVE8-endpoint-secret"
URL = f"http://svc-user:{SECRET}@localhost:11434/v1?api_key={SECRET}#frag"
LID = "20261106T101010-abcdef01"


def _cfg():
    cfg = AppConfig()
    cfg.llm.base_url = URL
    return cfg


def test_the_preflight_identity_shows_the_endpoint_without_credentials_and_the_runtime_keeps_the_original():
    cfg = _cfg()
    identity = run_module._model_identity(cfg)  # noqa: SLF001 - the preflight's own builder
    assert identity["endpoint"] == "http://localhost:11434/v1"
    assert SECRET not in json.dumps(identity) and "svc-user" not in json.dumps(identity)
    assert cfg.llm.base_url == URL, "the original stays in memory for the client"


def test_the_launch_record_on_disk_holds_no_credential_from_the_endpoint(tmp_path):
    folder = tmp_path / "proj"
    folder.mkdir()
    ws = canonical_workspace(str(folder))
    session = LaunchSession(ws, workspace_identity(ws), LID, "goal", {"ready": True, "model": run_module._model_identity(_cfg())}, {})  # noqa: SLF001
    session.open()
    stored = [os.path.join(root, name) for root, _, names in os.walk(ws) for name in names if LID in name]
    from kriya.control.persistence import load_launch_record

    record = load_launch_record(ws, LID)
    assert record is not None and record["preflight"]["model"]["endpoint"] == "http://localhost:11434/v1"
    assert SECRET not in json.dumps(record)
    assert all(SECRET not in open(path, encoding="utf-8", errors="replace").read() for path in stored)


@pytest.mark.parametrize("url", ["http://localhost:11434/v1", "https://api.internal:8443/v1/", "http://[::1]:11434/v1"])
def test_an_endpoint_without_credentials_is_shown_unchanged(url):
    cfg = AppConfig()
    cfg.llm.base_url = url
    assert run_module._model_identity(cfg)["endpoint"] == url  # noqa: SLF001


@pytest.mark.parametrize("case", ["case_context_capacity", "case_over_context_refusal"])
def test_the_qualification_refusal_for_a_non_local_endpoint_does_not_record_the_credential(case):
    """core/model_qualification.py: the "only probed on a local endpoint" refusal detail is stored in the qualification record."""
    import asyncio

    from kriya.core import model_qualification as mq

    remote = f"https://svc-user:{SECRET}@models.example.invalid:8443/v1?api_key={SECRET}"
    result = asyncio.run(getattr(mq, case)(None, "m", {"context_window": 8192, "base_url": remote}))
    detail = json.dumps(result.detail if hasattr(result, "detail") else result.__dict__, default=str)
    assert "only probed on a local endpoint" in detail or "local endpoint" in detail
    assert SECRET not in detail and "svc-user" not in detail
    assert "https://models.example.invalid:8443/v1" in detail


def test_mutation_restoring_the_raw_endpoint_fails_the_identity_assertion(monkeypatch):
    original = run_module.safe_endpoint
    monkeypatch.setattr(run_module, "safe_endpoint", lambda url, *a, **k: url)
    assert SECRET in json.dumps(run_module._model_identity(_cfg()))  # noqa: SLF001
    monkeypatch.setattr(run_module, "safe_endpoint", original)
    assert SECRET not in json.dumps(run_module._model_identity(_cfg()))  # noqa: SLF001
