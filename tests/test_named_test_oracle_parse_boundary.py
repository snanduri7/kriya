"""The named-test oracle parses CANDIDATE-CONTROLLED configuration bytes (pyproject.toml, setup.cfg, tox.ini) to decide whether the
candidate changed the pytest trust surface. Owner decision (counter-evidence packet): a malformed file is the candidate's malformed
content - it keeps the existing "compared by its bytes" result and is never an internal Kriya error just because parser mechanics
raise AttributeError/IndexError; a genuine coding error in a projection is not read as "malformed" - it propagates.

The projection is total over every PARSED shape (a non-table `project` stays visible in the view, so an edit to it is still a
change); the catch is narrowed to what parsing untrusted bytes can raise."""
import hashlib

import pytest

from kriya.workflow import named_test_oracle as oracle
from kriya.workflow.named_test_oracle import _pyproject, _side_digest

PYPROJECT = _pyproject(root=True)
INI = oracle._ini_section("pytest")  # pylint: disable=protected-access


def _digest(data: bytes, projection):
    return _side_digest(data, projection)


@pytest.mark.parametrize("data", [
    b"not [valid toml",                      # a syntax error
    b"\xff\xfe[tool.pytest]\n",              # not text
    b"[project]\ndependencies = [\n",        # unterminated
], ids=["syntax", "undecodable", "unterminated"])
def test_a_malformed_pyproject_is_compared_by_its_bytes(data):
    assert _digest(data, PYPROJECT) == "unparseable:" + hashlib.sha256(data).hexdigest()


@pytest.mark.parametrize("data", [b"[pytest\naddopts = x\n", b"no section header\naddopts = x\n", b"\xff\xfe"],
                         ids=["bad-section", "no-header", "undecodable"])
def test_a_malformed_ini_is_compared_by_its_bytes(data):
    assert _digest(data, INI) == "unparseable:" + hashlib.sha256(data).hexdigest()


@pytest.mark.parametrize("shape", ['project = "x"', "project = 3", "project = []", "tool = []", 'tool = "x"'])
def test_a_well_formed_toml_with_an_unexpected_shape_is_a_view_not_a_crash(shape):
    """The parser-mechanics case: `project` is a string, so `.get` on it would raise AttributeError. That is the candidate's odd
    content, not an internal error - and not 'unparseable' either: the view keeps the odd value, so editing it changes the digest."""
    digest = _digest(shape.encode(), PYPROJECT)
    assert digest.startswith("view:")
    other = shape.replace("x", "y").replace("3", "4").replace("[]", "[1]")
    assert _digest(other.encode(), PYPROJECT) != digest


def test_control_a_valid_pyproject_is_a_view_of_the_pytest_and_dependency_tables():
    data = b'[project]\ndependencies = ["a"]\n[tool.pytest.ini_options]\naddopts = "-q"\n'
    assert _digest(data, PYPROJECT).startswith("view:")
    changed = data.replace(b'"a"', b'"b"')
    assert _digest(changed, PYPROJECT) != _digest(data, PYPROJECT)


@pytest.mark.parametrize("error", [AttributeError("'NoneType' object has no attribute 'get'"), NameError("x"), KeyError("k")],
                         ids=["AttributeError", "NameError", "KeyError"])
def test_a_coding_error_in_a_projection_is_not_read_as_malformed_candidate_content(error):
    def broken(_data):
        raise error
    with pytest.raises(type(error)) as raised:
        _side_digest(b"anything", broken)
    assert raised.value is error
