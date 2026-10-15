"""KUP ``project.initialize``: Project Setup for a folder that has no configuration yet (PRODUCT-UX-COMPLETION).

The desktop renderer never writes ``kriya.yaml``. This is the ONE place it can ask Kriya to create one, and what it creates is not renderer text: the request carries
only the folder, and the file is Kriya's own recommended profile, built from the packaged defaults (models, endpoints) plus a small, fixed set of settings, then
validated by the real ``AppConfig`` schema before a byte is written.

Why a profile at all (MEASURED against the packaged defaults): the defaults leave the governed workflow in shadow mode (``workflow_controller.enabled: false``); this
profile turns the enforced workflow on and pins the local-model settings the demo was validated with. A folder with no ``kriya.yaml`` is already runnable on the
defaults, so Setup is a recommendation, not a prerequisite.

What it never does: approve anything (the protected settings it writes go through the ordinary configuration approval), overwrite an existing configuration, write
outside the folder, follow a symlink, start a process, or write a partial file (the document is staged and hard-linked into place, so it appears complete or not at all).
"""
from __future__ import annotations

import os
import uuid
from typing import Any, Dict

import yaml

import kriya.config.config as config_module
from kriya.config.config import AppConfig
from kriya.core import model_runtime
from kriya.kup import policy
from kriya.kup.inspect import envelope, live_consistency
from kriya.kup.store import SnapshotError
from kriya.kup.workspace_ops import validated_workspace

CONFIG_FILENAMES = ("kriya.yaml", "kriya.yml")
PROFILE_NAME = "local-models-governed"
HEADER = ("# Kriya project configuration - created by Project Setup.\n"
          "# Recommended profile for local models: the governed (enforced) workflow, changes held for your approval, no live web lookup.\n"
          "# Settings Kriya treats as operator-controlled (model server addresses, the workflow mode) need your approval before a run; change this file and it will ask again.\n")

# Fixed settings of the profile (everything else - models, endpoints, token limits - comes from the packaged defaults, never from the request).
_AUTONOMY = {"mode": "human-in-the-loop", "web_lookup_enabled": False, "web_lookup_auto_approve": False, "run_verification_enabled": True,
             "self_correction_loop_enabled": False, "generation_time_budget_seconds": 900}
_WORKFLOW = {"enabled": True, "mode": "enforce"}
_CONTEXT_WINDOW_TOKENS = 32768


def _packaged_defaults() -> Dict[str, Any]:
    with open(os.path.join(os.path.dirname(os.path.abspath(config_module.__file__)), "default_config.yaml"), "r", encoding="utf-8") as handle:
        return yaml.safe_load(handle)


def recommended_profile() -> Dict[str, Any]:
    """The profile as a plain dict, validated by the real schema. Pure: reads the packaged defaults only."""
    defaults = _packaged_defaults()
    llm = dict(defaults["llm"])
    # the per-request context window is a provider's request field: the runtime adapter writes it (INF-001), generic setup never names it
    llm["extra_body"] = model_runtime.with_context_window(llm.get("extra_body"), _CONTEXT_WINDOW_TOKENS)
    profile: Dict[str, Any] = {"workflow_controller": dict(_WORKFLOW), "autonomy": dict(_AUTONOMY), "llm": llm}
    if defaults.get("llm_chain"):
        profile["llm_chain"] = [dict(item) for item in defaults["llm_chain"]]
    if defaults.get("embedding"):
        profile["embedding"] = dict(defaults["embedding"])
    AppConfig.model_validate(profile)  # a profile the schema rejects is a defect here, never a file on disk
    return profile


def _refuse(message: str, reason: str) -> SnapshotError:
    return SnapshotError(policy.INVALID_REQUEST, message, reason=reason)


def initialize(cfg: Any, request: Dict[str, Any], request_id: str) -> Dict[str, Any]:
    _requested, workspace = validated_workspace(request)
    for name in CONFIG_FILENAMES:
        if os.path.lexists(os.path.join(workspace, name)):  # lexists: a dangling symlink named kriya.yaml is "there" too
            raise _refuse(f"this folder already has {name}; Project Setup never overwrites a configuration", "already_configured")
    profile = recommended_profile()
    text = HEADER + yaml.safe_dump(profile, sort_keys=False, default_flow_style=False, allow_unicode=True)
    target = os.path.join(workspace, CONFIG_FILENAMES[0])
    staging = f"{target}.staging-{os.getpid()}-{uuid.uuid4().hex[:8]}"
    try:
        fd = os.open(staging, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o644)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
        os.link(staging, target)  # fails with FileExistsError when anything is there, however it got there: the file appears complete or not at all
    except FileExistsError as error:
        raise _refuse("a configuration appeared in this folder while it was being created; nothing was overwritten", "already_configured") from error
    except OSError as error:
        raise SnapshotError(policy.WORKSPACE_UNAVAILABLE, f"the configuration could not be created in this folder: {error.strerror or error}", reason="unwritable") from error
    finally:
        try:
            os.unlink(staging)
        except FileNotFoundError:
            pass
    llm = profile["llm"]
    data = {
        "workspace": workspace, "outcome": "created", "file": CONFIG_FILENAMES[0], "profile": PROFILE_NAME,
        "summary": {"model": llm.get("model"), "fallback_models": [str(item.get("model")) for item in profile.get("llm_chain", [])],
                    "workflow": "enforce", "changes": "held for your approval", "web_lookup": "off"},
        "next": "configuration approval is required before a run: review it with configuration.approval_status",
    }
    return envelope("project.initialize", request_id, data=data, source=None, consistency=live_consistency())
