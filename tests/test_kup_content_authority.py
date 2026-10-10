"""The content-visibility authority (Run Cockpit M3): ``evidence.attempt_recorder.content_visibility`` is Kriya configuration under
SECURITY_AUTHORITY (the blanket `evidence` subtree), default off. A repository can never switch it on; the UI has no way to ask for more."""
from __future__ import annotations

import contextlib
import os

import pytest
import yaml

from kriya.config.authority import ConfigAuthorityError
from kriya.config.config import load_config


@contextlib.contextmanager
def _cwd(path):
    old = os.getcwd()
    os.makedirs(path, exist_ok=True)
    os.chdir(path)
    try:
        yield
    finally:
        os.chdir(old)


def _repo_config(tmp_path, visibility):
    workspace = tmp_path / "ws"
    workspace.mkdir()
    (workspace / "kriya.yaml").write_text(yaml.dump({"evidence": {"attempt_recorder": {"content_visibility": visibility}}}), encoding="utf-8")
    return workspace


@pytest.mark.parametrize("visibility", ["on", "off"])
def test_a_repository_cannot_set_content_visibility_at_all(tmp_path, visibility):
    with _cwd(_repo_config(tmp_path, visibility)), pytest.raises(ConfigAuthorityError) as error:
        load_config()
    assert any(v.field_path.startswith("evidence.attempt_recorder.content_visibility") or v.field_path.startswith("evidence") for v in error.value.violations)


def test_without_a_repository_override_the_loaded_default_is_off(tmp_path):
    with _cwd(tmp_path / "plain"):
        assert load_config().evidence.attempt_recorder.content_visibility == "off"


def test_an_unknown_visibility_value_is_rejected_not_coerced(tmp_path):
    from kriya.config.config import AppConfig
    with pytest.raises(ValueError):
        AppConfig.model_validate({"evidence": {"attempt_recorder": {"content_visibility": "yes"}}})
