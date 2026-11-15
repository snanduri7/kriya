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


# TEST-DOCKER-PIN-INERT-OFF-LOCK-001 (2026-11-15): pytest 9.2.0 + pytest-xdist 3.8.0 leave the xdist_group marker without its scheduling suffix, so the docker
# single-worker pin is silently off. The worker refuses to start such a run.
class _Item:
    def __init__(self, nodeid, groups):
        self.nodeid, self._groups = nodeid, groups

    def iter_markers(self, name=None):
        return [pytest.mark.xdist_group(group).mark for group in self._groups]


class _Config:
    def __init__(self, worker, loadgroup):
        self._loadgroup = loadgroup
        if worker:
            self.workerinput = {}

    def getoption(self, name, default=None):
        return self._loadgroup if name == "loadgroup" else default


def test_a_docker_grouped_item_without_the_scheduling_suffix_is_reported():
    from conftest import ungrouped_docker_ids

    items = [_Item("a.py::t@docker", ["docker"]), _Item("a.py::u", ["docker"]), _Item("a.py::v", []), _Item("a.py::w@other", ["other"])]
    assert ungrouped_docker_ids(items) == ["a.py::u"]


def test_a_worker_refuses_a_loadgroup_run_whose_docker_pin_is_inert_and_nothing_else():
    from conftest import pytest_docker_pin_is_effective

    inert, pinned = [_Item("a.py::u", ["docker"])], [_Item("a.py::t@docker", ["docker"])]
    with pytest.raises(pytest.UsageError, match="pin is inert.*requirements.txt"):
        pytest_docker_pin_is_effective(_Config(worker=True, loadgroup=True), inert)
    pytest_docker_pin_is_effective(_Config(worker=True, loadgroup=True), pinned)
    pytest_docker_pin_is_effective(_Config(worker=False, loadgroup=True), inert)  # the controller and a serial run add no suffix
    pytest_docker_pin_is_effective(_Config(worker=True, loadgroup=False), inert)  # another distribution mode schedules nothing on it
