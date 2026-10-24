"""Q0: a configuration's identity does not depend on the directory its Kriya checkout is in.

MEASURED (2026-10-24): the packaged defaults' ``plugins.directory``, ``paths.skills`` and ``paths.memory`` resolve to absolute paths under the Kriya
checkout at load (kriya/config/resolution.py), and ``compute_config_fingerprint`` hashed the whole dump, so the SAME repository and configuration had
a different fingerprint in every checkout directory. Seven committed goldens (the KUP ``execution.preflight`` configuration fingerprint and five
runtime-verification traces) passed only in the directory they were recorded in - which also meant they failed in the rebuild worktree.

The identity layer (``kriya.control.checkpoint``) now projects a path that lies INSIDE the checkout to ``<kriya-install>/<repo-relative>`` before it
is hashed; the live configuration keeps its absolute paths. The invariant, each half with a negative control:

- the same repository/configuration at a different checkout root -> the same identity (through the real loader, two roots);
- a different repo-relative target -> a different identity;
- a different external absolute path -> a different identity (a location outside the checkout is real identity);
- a look-alike sibling directory is not "inside" the checkout.

The resume-validity identity (``resume_fingerprints``: the configuration bucket and the skills directory) hashes the same dump and the same directory,
so it uses the same projection from the same owner.
"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path

import pytest

from kriya.config import config as config_module
from kriya.config import environment as environment_module
from kriya.config import resolution as resolution_module
from kriya.control import checkpoint as checkpoint_module
from kriya.control.checkpoint import (
    CHECKOUT_IDENTITY_TOKEN,
    canonical_identity_config,
    compute_config_fingerprint,
    identity_path,
)
from kriya.workflow import resume_fingerprints
from kriya.workflow.resume_fingerprints import skills_fingerprint, split_config_by_owner


def _use_checkout(monkeypatch, root: Path) -> str:
    """Pretend the Kriya checkout is ``root``: the one definition and the two modules that imported its name."""
    root.mkdir(parents=True, exist_ok=True)
    real = os.path.realpath(root)
    for module in (environment_module, resolution_module, checkpoint_module):
        monkeypatch.setattr(module, "kriya_install_dir", lambda real=real: real)
    return real


def _loaded(monkeypatch, tmp_path: Path, checkout: Path) -> dict:
    """The effective configuration of a plain folder (no kriya.yaml: the packaged defaults) loaded by the real loader, as if Kriya lived at ``checkout``."""
    _use_checkout(monkeypatch, checkout)
    workspace = tmp_path / "plain-folder"
    workspace.mkdir(exist_ok=True)
    monkeypatch.chdir(workspace)
    return config_module.load_config().model_dump()


# ------------------------------------------------------------------ the projection

@pytest.mark.parametrize("path, expected", [
    ("/co/skills", f"{CHECKOUT_IDENTITY_TOKEN}/skills"),
    ("/co/a/b/../c", f"{CHECKOUT_IDENTITY_TOKEN}/a/c"),     # normalized, never trusted as spelled
    ("/co", CHECKOUT_IDENTITY_TOKEN),                        # the checkout root itself
    ("/co-other/skills", "/co-other/skills"),                # a look-alike sibling is NOT inside the checkout
    ("/co/../elsewhere/skills", "/co/../elsewhere/skills"),  # leaves the checkout by a dot-dot: outside, so unchanged (never read as inside)
    ("/opt/skills", "/opt/skills"),                          # an external absolute path stays as it is: real identity
    ("skills", "skills"),                                    # not absolute
    ("./skills", "./skills"),
    ("", ""),
])
def test_identity_path_projects_only_what_lies_inside_the_checkout(path, expected):
    assert identity_path(path, install_dir="/co") == expected


@pytest.mark.parametrize("value", [None, 3, True, 1.5, ["/co/skills"], {"k": "/co/skills"}])
def test_identity_path_leaves_every_non_string_untouched(value):
    assert identity_path(value, install_dir="/co") == value


def test_the_projection_returns_a_new_structure_and_leaves_the_live_configuration_absolute():
    live = {"paths": {"skills": "/co/skills", "memory": "/opt/mem"}, "plugins": {"directory": "/co/plugins", "enabled": ["/co/plugins/x"]},
            "evidence": {"observational": "/co/ignored"}}
    projected = canonical_identity_config(live, install_dir="/co")
    assert projected == {"paths": {"skills": f"{CHECKOUT_IDENTITY_TOKEN}/skills", "memory": "/opt/mem"},
                         "plugins": {"directory": f"{CHECKOUT_IDENTITY_TOKEN}/plugins", "enabled": [f"{CHECKOUT_IDENTITY_TOKEN}/plugins/x"]}}
    assert live["paths"]["skills"] == "/co/skills" and live["plugins"]["enabled"] == ["/co/plugins/x"] and "evidence" in live


def test_a_symlink_alias_of_the_checkout_is_the_same_checkout_and_an_outside_symlink_is_not(tmp_path):
    """Containment is decided by filesystem identity (``path_relation``), not by how the path is spelled: the same directory reached through a symlink
    alias of the checkout is inside it; a link inside the checkout that points OUT of it is outside."""
    real = tmp_path / "real-checkout"
    (real / "skills").mkdir(parents=True)
    alias = tmp_path / "alias-of-the-checkout"
    alias.symlink_to(real)
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    (real / "points-out").symlink_to(outside)
    assert identity_path(str(alias / "skills"), install_dir=str(real)) == f"{CHECKOUT_IDENTITY_TOKEN}/skills"
    assert identity_path(str(real / "skills"), install_dir=str(alias)) == f"{CHECKOUT_IDENTITY_TOKEN}/skills"
    assert identity_path(str(real / "points-out"), install_dir=str(real)) == str(real / "points-out")   # reaches outside: real identity, unchanged


# ------------------------------------------------------------------ the invariant, through the real loader

def test_the_same_configuration_at_two_checkout_roots_has_the_same_fingerprint_while_the_runtime_paths_stay_absolute(monkeypatch, tmp_path):
    root_a, root_b = tmp_path / "a-checkout", tmp_path / "a-much-longer-checkout-name-b"
    dump_a = _loaded(monkeypatch, tmp_path, root_a)
    fingerprint_a = compute_config_fingerprint(dump_a)
    dump_b = _loaded(monkeypatch, tmp_path, root_b)
    fingerprint_b = compute_config_fingerprint(dump_b)
    # the runtime keeps real absolute paths, and they differ (this is the premise of the defect)...
    assert dump_a["paths"]["skills"] == os.path.realpath(root_a / "skills") and dump_b["paths"]["skills"] == os.path.realpath(root_b / "skills")
    assert dump_a["plugins"]["directory"] == os.path.realpath(root_a / "plugins") and dump_a["paths"]["memory"] == os.path.realpath(root_a / "memory")
    assert dump_a != dump_b
    # ...but the identity does not
    assert fingerprint_a == fingerprint_b


def test_negative_control_without_the_projection_the_two_roots_differ(monkeypatch, tmp_path):
    """The old behaviour, reproduced: hashing the dump as it stands gives different fingerprints at different roots. If this stops holding, the test
    above no longer proves the projection does anything."""
    import json
    dump_a = _loaded(monkeypatch, tmp_path, tmp_path / "a-checkout")
    dump_b = _loaded(monkeypatch, tmp_path, tmp_path / "a-much-longer-checkout-name-b")

    def raw(dump):
        return hashlib.sha256(json.dumps(checkpoint_module.without_observational_config(dump), sort_keys=True, default=str).encode()).hexdigest()

    assert raw(dump_a) != raw(dump_b)


@pytest.mark.parametrize("field", [("paths", "skills"), ("paths", "memory"), ("plugins", "directory")])
def test_a_different_repo_relative_target_is_a_different_identity(monkeypatch, tmp_path, field):
    root = tmp_path / "checkout"
    dump = _loaded(monkeypatch, tmp_path, root)
    baseline = compute_config_fingerprint(dump)
    section, key = field
    dump[section][key] = os.path.realpath(root / "somewhere-else")
    assert compute_config_fingerprint(dump) != baseline


def test_a_different_external_absolute_path_is_a_different_identity_and_differs_from_the_in_checkout_default(monkeypatch, tmp_path):
    dump = _loaded(monkeypatch, tmp_path, tmp_path / "checkout")
    default = compute_config_fingerprint(dump)
    dump["paths"]["skills"] = "/opt/skills-one"
    external_one = compute_config_fingerprint(dump)
    dump["paths"]["skills"] = "/opt/skills-two"
    external_two = compute_config_fingerprint(dump)
    assert len({default, external_one, external_two}) == 3


def test_a_lookalike_sibling_of_the_checkout_is_not_confused_with_the_checkout(monkeypatch, tmp_path):
    root = tmp_path / "checkout"
    dump = _loaded(monkeypatch, tmp_path, root)
    default = compute_config_fingerprint(dump)
    dump["paths"]["skills"] = os.path.realpath(root) + "-sibling/skills"
    assert compute_config_fingerprint(dump) != default


# ------------------------------------------------------------------ the resume-validity identity uses the same projection

def test_the_resume_configuration_bucket_is_the_same_at_two_checkout_roots(monkeypatch, tmp_path):
    bucket_a = split_config_by_owner(_loaded(monkeypatch, tmp_path, tmp_path / "a-checkout"))["config"]
    bucket_b = split_config_by_owner(_loaded(monkeypatch, tmp_path, tmp_path / "a-much-longer-checkout-name-b"))["config"]
    assert bucket_a == bucket_b
    assert bucket_a["paths"]["skills"] == f"{CHECKOUT_IDENTITY_TOKEN}/skills"


def test_the_resume_configuration_bucket_still_distinguishes_an_external_path(monkeypatch, tmp_path):
    dump = _loaded(monkeypatch, tmp_path, tmp_path / "checkout")
    inside = split_config_by_owner(dump)["config"]
    dump["paths"]["skills"] = "/opt/skills-one"
    assert split_config_by_owner(dump)["config"] != inside


def _skills(root: Path, relative: str, content: str) -> str:
    directory = root / relative
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "skill.md").write_text(content, encoding="utf-8")
    return str(directory)


def test_the_skills_fingerprint_is_the_same_at_two_checkout_roots_and_still_sees_content_and_target(monkeypatch, tmp_path):
    root_a, root_b = tmp_path / "a-checkout", tmp_path / "a-much-longer-checkout-name-b"
    _use_checkout(monkeypatch, root_a)
    at_a = skills_fingerprint([_skills(root_a, "skills", "same")])
    _use_checkout(monkeypatch, root_b)
    at_b = skills_fingerprint([_skills(root_b, "skills", "same")])
    assert at_a.value == at_b.value                                                    # same repository, other root
    changed_content = skills_fingerprint([_skills(root_b, "skills", "different")])
    other_target = skills_fingerprint([_skills(root_b, "skills-alt", "same")])
    external = skills_fingerprint([_skills(tmp_path / "outside", "skills", "same")])
    assert len({at_b.value, changed_content.value, other_target.value, external.value}) == 4


# ------------------------------------------------------------------ one owner, one definition

def test_the_install_root_has_one_definition_and_the_resume_identity_asks_the_same_projection():
    import inspect
    assert resolution_module.kriya_install_dir is environment_module.kriya_install_dir
    assert "canonical_identity_config" in inspect.getsource(resume_fingerprints.split_config_by_owner)
    assert "identity_path" in inspect.getsource(resume_fingerprints.skills_fingerprint)
    producer = Path(resolution_module.__file__).read_text(encoding="utf-8")
    assert "KRIYA_INSTALL_DIR = kriya_install_dir()" in producer and '"..", ".."' not in producer, "the producer must not recompute the checkout root"
