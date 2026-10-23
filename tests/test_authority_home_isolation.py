"""D2 prerequisite: no test touches the real ~/.kriya/authority. tests/conftest.py gives every test its own authority home through
KRIYA_AUTHORITY_HOME; a CLI/waiter subprocess a test starts inherits it through the environment."""
import os
import subprocess
import sys

from kriya.config import authority_approval
from kriya.config.environment import authority_home, kriya_home

_PRINT_HOME = "from kriya.config.environment import authority_home; print(authority_home())"


def _real_default_home():
    # the home a test would hit with no isolation; resolved from the account database, not from $HOME/KRIYA_* variables
    import pwd

    return os.path.realpath(os.path.join(pwd.getpwuid(os.getuid()).pw_dir, ".kriya", "authority"))


def test_the_authority_home_of_a_test_is_inside_the_test_sandbox(tmp_path_factory):
    home = os.path.realpath(authority_home())
    assert home != _real_default_home()
    assert os.path.commonpath([home, os.path.realpath(str(tmp_path_factory.getbasetemp()))]) == os.path.realpath(
        str(tmp_path_factory.getbasetemp())), f"{home} is outside the pytest base temp"
    assert os.path.realpath(authority_home()) != os.path.realpath(kriya_home("authority"))


def test_each_test_gets_its_own_authority_home_and_it_stays_empty_until_written(tmp_path):
    home = authority_home()
    assert not os.path.exists(home) or os.listdir(home) == []


def test_a_real_approval_write_lands_in_the_sandbox_and_not_in_the_real_home(tmp_path):
    workspace = tmp_path / "ws"
    workspace.mkdir()
    real_before = sorted(os.listdir(_real_default_home())) if os.path.isdir(_real_default_home()) else None
    _artifact, path = authority_approval.grant_local_approval([], {}, str(workspace))
    assert os.path.isfile(path)
    assert os.path.realpath(path).startswith(os.path.realpath(authority_home()) + os.sep)
    assert not os.path.realpath(path).startswith(_real_default_home() + os.sep)
    real_after = sorted(os.listdir(_real_default_home())) if os.path.isdir(_real_default_home()) else None
    assert real_after == real_before, "the real authority home changed during the test"


def test_a_child_process_inherits_the_isolated_authority_home():
    out = subprocess.run([sys.executable, "-I", "-c", _PRINT_HOME], capture_output=True, text=True, check=True,
                         env=dict(os.environ)).stdout.strip()
    assert out == authority_home()
    assert os.path.realpath(out) != _real_default_home()
