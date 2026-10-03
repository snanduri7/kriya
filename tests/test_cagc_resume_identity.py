"""CAGC-0 resume identity (KRIYA_CAGC v0.7 §10.3): the guidance registry is
package source, so the existing kriya_runtime_fingerprint covers it - a rule
text change invalidates dependent resume stages with no new field."""
import os
import shutil
from unittest.mock import patch

import kriya
import kriya.capabilities.guidance.registry as registry_module
from kriya.workflow.resume_fingerprints import kriya_runtime_fingerprint

_PACKAGE = os.path.dirname(os.path.abspath(kriya.__file__))


def _fingerprint_of(package_dir):
    with patch.object(kriya, "__file__", os.path.join(package_dir, "__init__.py")):
        return kriya_runtime_fingerprint.__wrapped__()


def test_the_registry_modules_are_package_source():
    guidance_dir = os.path.dirname(os.path.abspath(registry_module.__file__))
    assert os.path.commonpath([guidance_dir, _PACKAGE]) == _PACKAGE
    for name in ("registry.py", "selection.py", "render.py", "facts.py", "model.py", "__init__.py"):
        assert os.path.isfile(os.path.join(guidance_dir, name))


def test_changing_a_rule_text_changes_the_runtime_fingerprint(tmp_path):
    copy = tmp_path / "kriya"
    shutil.copytree(_PACKAGE, copy, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    before = _fingerprint_of(str(copy))
    assert before == _fingerprint_of(str(copy)), "the fingerprint is deterministic"
    registry = copy / "capabilities" / "guidance" / "registry.py"
    text = registry.read_text(encoding="utf-8")
    assert "Follow the file's existing import style" in text
    registry.write_text(text.replace("Follow the file's existing import style", "Follow the existing import style"),
                        encoding="utf-8")
    after = _fingerprint_of(str(copy))
    assert after.value != before.value
