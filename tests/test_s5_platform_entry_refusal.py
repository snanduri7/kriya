"""S5 (OI-101, PLAT-WINDOWS-ENTRY-REFUSAL): an unsupported platform is refused, typed, at every entry, before anything
reads the workspace or applies sensitive-path policy.

Support policy (handover/PLATFORM_ARCHITECTURE.md §13): Linux and macOS are supported; Windows before P5 is
RUNTIME_NOT_SUPPORTED (parity is a future feature, owner decision: not implemented here). Measured defect: nothing
refused the Windows family (or any platform the policy does not name) at entry - `kriya` loaded the workspace's
kriya.yaml and ran commands that apply sensitive-path policy with no lock/spawn port involved, and the KUP server served.

Characterization FIRST (S1 method). The platform is simulated through the EXISTING seam (kriya.platform.services
compose()/override(); the platform name it records since 7ae8746), never by patching sys.platform. Every entry runs in
a FRESH interpreter with an audit hook (an audit hook cannot be removed, so it is never installed in an xdist worker),
so "nothing was read" is observed, not assumed.
"""
import json
import os
import subprocess
import sys

import pytest

FLIP = pytest.mark.xfail(strict=True, raises=AssertionError,
                         reason="S5 expected-to-flip: the measured missing entry refusal (removed by the correction commit)")
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WINDOWS_NAMES = ("win32", "cygwin", "msys")
UNKNOWN_NAMES = ("freebsd14", "sunos5", "emscripten", "")

_RUNNER = r'''
import json, os, sys
entry, family_name, platform_name, out_path, *argv = sys.argv[1:]
from kriya.platform import services
import kriya.cli, kriya.kup.serve, kriya.mcp.server
watched = tuple(p for p in (os.environ["S5_WATCH"],) if p)
events = []
def hook(event, args):
    if event in ("open", "os.listdir", "os.scandir", "os.mkdir", "os.remove", "os.rename", "os.chdir", "subprocess.Popen",
                 "sqlite3.connect", "shutil.rmtree", "os.walk"):
        target = str(args[0]) if args else ""
        if event == "subprocess.Popen" or target.startswith(watched):
            events.append([event, target])
family = family_name if family_name != "-" else services.host_family(platform_name)
code = None
with services.override(services.compose(family, platform_name)):
    sys.addaudithook(hook)
    try:
        if entry == "cli":
            kriya.cli.main.main(args=argv, prog_name="kriya", standalone_mode=True)
        elif entry == "kup":
            code = kriya.kup.serve.run(None, None)
        elif entry == "mcp":
            kriya.mcp.server.main()
    except SystemExit as stop:
        code = stop.code
with open(out_path, "w") as handle:
    json.dump({"code": code, "events": events}, handle)
'''


def _entry(tmp_path, entry, platform_name, *argv, family="-"):
    ws = tmp_path / "ws"
    ws.mkdir(exist_ok=True)
    (ws / "kriya.yaml").write_text("llm:\n  model: m\n")
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    out = tmp_path / "out.json"
    runner = tmp_path / "runner.py"
    runner.write_text(_RUNNER)
    env = {k: v for k, v in os.environ.items() if not k.startswith("KRIYA_")}
    env.update(PYTHONPATH=REPO_ROOT, HOME=str(home), S5_WATCH=str(tmp_path), KRIYA_STATE_DIR=str(home / "state"),
               KRIYA_LOG_DIR=str(home / "logs"), KRIYA_AUTHORITY_HOME=str(home / "auth"), KRIYA_MODEL_RUNTIME_PROBE="0",
               KRIYA_QUALIFICATION_HOME=str(home / "q"), KRIYA_CERTIFICATION_HOME=str(home / "c"))
    try:
        done = subprocess.run([sys.executable, str(runner), entry, family, platform_name, str(out), *argv], cwd=str(ws),
                              env=env, capture_output=True, text=True, timeout=60, stdin=subprocess.DEVNULL, check=False)
    except subprocess.TimeoutExpired:
        return {"code": "timeout", "events": [], "stderr": "", "stdout": ""}
    result = json.loads(out.read_text()) if out.exists() else {"code": "no-result", "events": []}
    result.update(stderr=done.stderr, stdout=done.stdout)
    return result


# =========================================================================== governed behaviour that must NOT change

@pytest.mark.parametrize("platform_name", ["darwin", "linux"])
def test_a_supported_platform_runs_the_cli_unchanged(tmp_path, platform_name):
    result = _entry(tmp_path, "cli", platform_name, "config")
    assert result["code"] in (0, None), result["stderr"]
    assert json.loads(result["stdout"])["llm"]["model"] == "m"            # the workspace configuration WAS read
    assert any(event == "open" and target.endswith("kriya.yaml") for event, target in result["events"])


@pytest.mark.parametrize("platform_name", ["darwin", "linux"])
def test_a_supported_platform_serves_kup(tmp_path, platform_name):
    result = _entry(tmp_path, "kup", platform_name)
    assert result["code"] == 0, result["stderr"]                         # EOF on stdin: served nothing, clean exit


def test_the_host_family_mapping_is_unchanged():
    from kriya.platform import services

    assert [services.host_family(n) for n in ("win32", "cygwin", "msys", "darwin", "linux", "freebsd14", "")] == [
        "windows", "windows", "windows", "posix", "posix", "posix", "posix"]
    assert services.platform_services().identity()["family"] == services.host_family()


# =========================================================================== EXPECTED TO FLIP (the measured defect)

def _assert_refused(result, code_name):
    assert result["code"] == 69, (result["code"], result["stderr"][-400:])
    assert code_name in result["stderr"] and "not supported" in result["stderr"], result["stderr"]
    assert result["events"] == [], result["events"]                       # nothing read, listed, written or spawned
    assert result["stdout"] == ""


@FLIP
@pytest.mark.parametrize("platform_name", WINDOWS_NAMES)
@pytest.mark.parametrize("argv", [("config",), ("authority", "inspect"), ("runs", "status"), ("version",),
                                  ("tools", "execute", "filesystem", '{"operation": "read", "path": "kriya.yaml"}')],
                         ids=["config", "authority", "runs", "version", "tools-read"])
def test_the_windows_family_is_refused_at_cli_entry_before_anything_is_read(tmp_path, platform_name, argv):
    _assert_refused(_entry(tmp_path, "cli", platform_name, *argv), "PLATFORM_WINDOWS_UNSUPPORTED")


@FLIP
@pytest.mark.parametrize("platform_name", UNKNOWN_NAMES)
def test_a_platform_the_support_policy_does_not_name_is_refused_at_cli_entry(tmp_path, platform_name):
    _assert_refused(_entry(tmp_path, "cli", platform_name, "config"), "PLATFORM_UNKNOWN_UNSUPPORTED")


@FLIP
def test_the_refusal_comes_before_option_parsing(tmp_path):
    """--config is a click.Path(exists=True) (stats the file while parsing) and --version is eager: both come after."""
    result = _entry(tmp_path, "cli", "win32", "--config", str(tmp_path / "ws" / "kriya.yaml"), "config")
    _assert_refused(result, "PLATFORM_WINDOWS_UNSUPPORTED")
    _assert_refused(_entry(tmp_path, "cli", "win32", "--version"), "PLATFORM_WINDOWS_UNSUPPORTED")


@FLIP
@pytest.mark.parametrize("platform_name", ["win32", "freebsd14"])
def test_the_kup_server_refuses_at_entry(tmp_path, platform_name):
    code = "PLATFORM_WINDOWS_UNSUPPORTED" if platform_name == "win32" else "PLATFORM_UNKNOWN_UNSUPPORTED"
    _assert_refused(_entry(tmp_path, "kup", platform_name), code)


@FLIP
def test_the_kup_stdio_command_refuses_at_entry(tmp_path):
    _assert_refused(_entry(tmp_path, "cli", "win32", "kup-stdio"), "PLATFORM_WINDOWS_UNSUPPORTED")


@FLIP
def test_the_mcp_server_entry_refuses(tmp_path):
    _assert_refused(_entry(tmp_path, "mcp", "win32"), "PLATFORM_WINDOWS_UNSUPPORTED")


@FLIP
def test_a_windows_family_services_object_refuses_whatever_platform_name_it_carries(tmp_path):
    """The family decides first: Windows services composed on a POSIX test host (platform 'darwin') still refuse."""
    _assert_refused(_entry(tmp_path, "cli", "darwin", "config", family="windows"), "PLATFORM_WINDOWS_UNSUPPORTED")
