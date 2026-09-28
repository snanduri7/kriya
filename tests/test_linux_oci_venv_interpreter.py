"""LINUX-OCI-VENV-INTERPRETER-001: a contained venv's bin/python links to the
container's interpreter, which need not exist on the host; the host-side
check must see the link, not follow it (hosted run 36368006232 on Linux:
every contained venv was judged failed and the tests ran on an interpreter
without the declared dependencies - "No module named 'pytest'").
"""
import os

from kriya.config.config import AutonomyConfig
from kriya.tools.validate import PolymorphicValidator

CONTAINER_ONLY_INTERPRETER = "/kriya-test-nonexistent/usr/local/bin/python3"


def _validator(tmp_path, *, contained):
    (tmp_path / "requirements.txt").write_text("six==1.16.0\n")
    return PolymorphicValidator(str(tmp_path), autonomy_cfg=AutonomyConfig(
        contained_execution_required=contained, containment_backend="oci" if contained else "none"))


def _fake_commands(validator, calls, link_target):
    def run(cmd, cwd, **kwargs):
        calls.append(cmd)
        if cmd[1:3] == ["-m", "venv"]:
            bin_dir = os.path.join(validator.workspace_path, ".kriya", "venv", "bin")
            os.makedirs(bin_dir, exist_ok=True)
            os.symlink(link_target, os.path.join(bin_dir, "python"))
        return {"returncode": 0, "stdout": "", "stderr": "", "timeout": False}
    validator._run_cmd_with_timeout = run


def test_a_contained_venv_linking_to_the_container_interpreter_is_used(tmp_path):
    validator = _validator(tmp_path, contained=True)
    calls = []
    _fake_commands(validator, calls, CONTAINER_ONLY_INTERPRETER)
    assert not os.path.exists(CONTAINER_ONLY_INTERPRETER)
    assert validator._ensure_project_venv(["-r", "requirements.txt"]) == (".kriya/venv/bin/python", None)
    assert [cmd[1:3] for cmd in calls] == [["-m", "venv"], ["-m", "pip"]]
    # A second resolution reuses the existing venv instead of recreating it.
    validator._venv_install_cache.clear()
    calls.clear()
    assert validator._ensure_project_venv(["-r", "requirements.txt"])[0] == ".kriya/venv/bin/python"
    assert [cmd[1:3] for cmd in calls] == [["-m", "pip"]]


def test_a_host_venv_with_a_dangling_interpreter_is_still_a_failed_venv(tmp_path):
    """Host mode keeps following the link: a host venv whose interpreter
    does not exist cannot run anything."""
    validator = _validator(tmp_path, contained=False)
    calls = []
    _fake_commands(validator, calls, CONTAINER_ONLY_INTERPRETER)
    assert validator._ensure_project_venv(["-r", "requirements.txt"]) == (None, None)
    assert [cmd[1:3] for cmd in calls] == [["-m", "venv"]]
