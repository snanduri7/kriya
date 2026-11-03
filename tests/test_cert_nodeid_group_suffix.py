"""CERT-NODEID-GROUP-SUFFIX-001 (run 4): the census/tier-audit node id must be the pytest id, whatever ``@`` its parameters contain.

``base_nodeid`` cut every id at its last ``@`` for any test carrying an xdist_group marker, in EVERY mode -- but xdist appends ``@<group>`` only under
``--dist loadgroup``. In the serial canonical run the cut removed a real part of the id (``...[...@Test...]``) so 8 Java tests looked "missing" from the executed set.
"""
import json
import subprocess
import sys
from pathlib import Path

import pytest
from conftest import base_nodeid, strip_group_suffix

ROOT = Path(__file__).resolve().parent.parent
SAMPLE = ROOT / "tests" / "certification" / "sample_group_ids.py"


@pytest.mark.parametrize(
    "nodeid, groups, expected",
    [
        ("a.py::t[x]@docker", {"docker"}, "a.py::t[x]"),  # the suffix xdist appended
        ("a.py::t[@Test-x]@docker", {"docker"}, "a.py::t[@Test-x]"),  # an @ inside the parameter id survives
        ("a.py::t[@Test-x]", {"docker"}, "a.py::t[@Test-x]"),  # no suffix present (serial run)
        ("a.py::t[ends@docker]", {"docker"}, "a.py::t[ends@docker]"),  # looks like a suffix but the id closes with ``]``
        ("a.py::t[x]@alpha_named", {"named", "alpha"}, "a.py::t[x]"),  # several groups: sorted, joined by ``_``
        ("a.py::t[x]@other", {"docker"}, "a.py::t[x]@other"),  # a different group name is not ours to strip
        ("a.py::t[x]@docker", set(), "a.py::t[x]@docker"),  # no marker, nothing to strip
        ("a.py::t[x]@docker@docker", {"docker"}, "a.py::t[x]@docker"),  # only the exact trailing suffix, once
    ],
)
def test_only_the_exact_trailing_group_suffix_is_stripped(nodeid, groups, expected):
    assert strip_group_suffix(nodeid, groups) == expected


def _census(tmp_path, *mode):
    out = tmp_path / "census.json"
    completed = subprocess.run(
        [sys.executable, "-m", "pytest", str(SAMPLE), "-o", "addopts=", "-p", "no:cacheprovider", "-q", "--collect-only", f"--collection-census={out}", *mode],
        cwd=str(ROOT), capture_output=True, text=True, timeout=300,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    return sorted(record["id"] for record in json.loads(out.read_text())["tests"])


def test_the_census_ids_are_identical_serial_and_under_loadgroup_and_none_is_truncated(tmp_path):
    serial = _census(tmp_path, "-p", "no:xdist")
    loadgroup = _census(tmp_path, "-n", "2", "--dist", "loadgroup")
    assert serial == loadgroup
    assert len(serial) == len(set(serial)) == 6
    assert any(i.endswith("[@Test-annotation]") for i in serial), serial  # the shape that was truncated in run 4
    assert any(i.endswith("[ends@grp]") for i in serial), serial  # an id that ends like a suffix
    assert all("@" not in i.rsplit("]", 1)[-1] for i in serial)  # no scheduling suffix leaked into any id


def test_an_item_without_a_config_keeps_its_node_id():
    from types import SimpleNamespace

    assert base_nodeid(SimpleNamespace(nodeid="a.py::t[@x]@docker")) == "a.py::t[@x]@docker"  # nothing can have appended a suffix without xdist's config
