"""W2-21: `kriya authority inspect` consults the SAME trust file, by the SAME resolution, as every trust-aware command.

MEASURED at 74e88e6 (the contract matrix in the Step-5 evidence directory): `authority inspect --trust-file <anything>` and `KRIYA_TRUST_FILE=<anything>` were ignored completely - an inside-
workspace path, a malformed file, a missing file and a valid external approval all printed exactly what no trust file printed (exit 0) - while `config` / `doctor` (load_config) refused the
inside-workspace path with a typed error, accepted a valid external approval and denied a malformed one. Now `authority_approval.checked_trust_file` is the one resolution (explicit flag,
else KRIYA_TRUST_FILE; inside the workspace refused), used by load_config and by inspect; an invalid external file is a typed refusal, a valid one is consulted, an explicit trust file is
authoritative (never replaced by the local store).
"""
import contextlib
import os

import pytest
import yaml
from click.testing import CliRunner

from kriya.cli import main
from kriya.config import authority_approval as aa

CODE = "TRUST_PATH_INSIDE_WORKSPACE"


class _Env:
    """A fake ``Environment`` (the Protocol): only KRIYA_TRUST_FILE is ever read here."""

    def __init__(self, trust_file=None):
        self._values = {"KRIYA_TRUST_FILE": trust_file} if trust_file else {}

    def variable(self, name, default=None):
        return self._values.get(name, default)

    def snapshot(self):
        return dict(self._values)

    def expand_user(self, path):
        return path

    def user_home(self):
        return "/nonexistent"


@contextlib.contextmanager
def _cwd(path):
    old = os.getcwd()
    os.chdir(path)
    try:
        yield
    finally:
        os.chdir(old)


@pytest.fixture(name="world")
def _world(tmp_path, monkeypatch):
    """A workspace whose config sets a SECURITY_AUTHORITY field (an MCP server), an authority home and a trust directory OUTSIDE it, a VALID external approval of that exact config."""
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "kriya.yaml").write_text(yaml.safe_dump({"mcp": {"demo": {"command": "echo"}}, "logging": {"file_enabled": False}}))
    ext = tmp_path / "ext"
    ext.mkdir()
    monkeypatch.setenv("KRIYA_AUTHORITY_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("KRIYA_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.delenv("KRIYA_TRUST_FILE", raising=False)
    world = {"ws": ws, "ext": ext, "valid": ext / "trust.json"}
    result = _invoke(world, "authority", "approve", "--confirm", "--out", str(world["valid"]))
    assert result.exit_code == 0 and world["valid"].exists(), result.output
    (ext / "bad.json").write_text("not json")
    (ext / "wrongschema.json").write_text('{"schema_version": 999}')
    return world


def _invoke(world, *args, trust=None, env=None):
    argv = ["--config", "kriya.yaml", *(["--trust-file", str(trust)] if trust is not None else []), *args]
    with _cwd(world["ws"]):
        return CliRunner().invoke(main, argv, env=env)


def _inspect(world, **kwargs):
    return _invoke(world, "authority", "inspect", **kwargs)


def _config(world, **kwargs):
    return _invoke(world, "config", **kwargs)


def _refused_inside(result, world):
    assert result.exit_code == 1 and isinstance(result.exception, SystemExit) and "Traceback" not in result.output
    assert f"[{CODE}]" in result.output and str(world["ws"]) in result.output


# ----------------------------------------------------------------------------------------------------------------------------------- the baseline stays

def test_without_a_trust_file_inspect_reports_the_local_store_exactly_as_before(world):
    result = _inspect(world)
    assert result.exit_code == 0 and "mcp.demo" in result.output
    assert "No local approval on file at" in result.output and "kriya authority approve' to grant it" in result.output and "Trust file" not in result.output


def test_without_a_trust_file_the_trust_aware_command_denies_the_unapproved_config(world):
    result = _config(world)
    assert result.exit_code == 1 and "Configuration-authority denied" in result.output


# --------------------------------------------------------------------------------------------------- inside the workspace: refused, by both, every way in

@pytest.mark.parametrize("how", ["flag", "env"])
def test_a_trust_file_inside_the_workspace_is_a_typed_refusal_for_inspect_and_for_a_trust_aware_command(world, how):
    inside = world["ws"] / "trust.json"
    kwargs = {"trust": inside} if how == "flag" else {"env": {"KRIYA_TRUST_FILE": str(inside)}}
    _refused_inside(_inspect(world, **kwargs), world)
    _refused_inside(_config(world, **kwargs), world)                       # the other trust-aware command agrees


def test_a_trust_file_symlinked_to_a_file_inside_the_workspace_is_also_refused_by_both(world):
    """MEASURED at 74e88e6: the validator looked at the containing directory only, so this loaded repository-controlled content as 'trusted'."""
    (world["ws"] / "target.json").write_text(world["valid"].read_text())                   # even a perfectly valid artifact, if it lives in the workspace, is not trust
    link = world["ext"] / "alias.json"
    os.symlink(world["ws"] / "target.json", link)
    _refused_inside(_inspect(world, trust=link), world)
    _refused_inside(_config(world, trust=link), world)


def test_the_refusal_names_the_path_as_the_operator_gave_it_not_the_resolved_one(world):
    """A pinned message contract (tests/golden/config/resolution_forms.json, protected_trust_relative): the real target is validated, the operator's spelling is reported."""
    for result in (_inspect(world, trust="trust.json"), _config(world, trust="trust.json")):
        assert result.exit_code == 1 and "trust-artifact path 'trust.json' resolves inside the workspace root" in result.output


def test_an_inside_workspace_trust_file_is_refused_even_when_a_valid_external_file_is_also_configured(world):
    """The flag wins over KRIYA_TRUST_FILE, and an unsafe explicit file is never replaced by the safer one."""
    _refused_inside(_inspect(world, trust=world["ws"] / "t.json", env={"KRIYA_TRUST_FILE": str(world["valid"])}), world)


# ------------------------------------------------------------------------------------------------------------------- a valid external file is consulted

@pytest.mark.parametrize("how", ["flag", "env"])
def test_a_valid_external_trust_file_is_consulted_and_inspect_agrees_with_the_trust_aware_command(world, how):
    kwargs = {"trust": world["valid"]} if how == "flag" else {"env": {"KRIYA_TRUST_FILE": str(world["valid"])}}
    inspected = _inspect(world, **kwargs)
    assert inspected.exit_code == 0 and f"Trust file {world['valid']} is CURRENT and covers all pending fields" in inspected.output
    assert "No local approval" not in inspected.output                      # the trust file, not the (absent) local store, answered
    assert _config(world, **kwargs).exit_code == 0                          # ... and the config really is authorized by it


def test_the_flag_wins_over_the_environment(world):
    result = _inspect(world, trust=world["ext"] / "bad.json", env={"KRIYA_TRUST_FILE": str(world["valid"])})
    assert result.exit_code == 1 and "could not parse approval artifact" in result.output


def test_a_trust_file_that_does_not_cover_the_current_config_is_reported_stale_and_the_command_denies(world):
    (world["ws"] / "kriya.yaml").write_text(yaml.safe_dump({"mcp": {"demo": {"command": "echo", "args": ["x"]}}, "logging": {"file_enabled": False}}))
    inspected = _inspect(world, trust=world["valid"])
    assert inspected.exit_code == 0 and "does NOT cover the current configuration" in inspected.output and "is CURRENT" not in inspected.output
    assert _config(world, trust=world["valid"]).exit_code == 1


def test_an_explicit_trust_file_is_authoritative_the_local_store_is_not_a_fallback(world):
    """A CURRENT local approval exists, but the explicit trust file does not cover the config: inspect reports the TRUST FILE's verdict, like load_config."""
    assert _invoke(world, "authority", "approve", "--confirm").exit_code == 0           # the local store now covers the config
    assert "is CURRENT" in _inspect(world).output                                           # control: without a trust file the local store answers
    stale = world["ext"] / "stale.json"
    stale.write_text("{}")                                                                  # an object with no schema -> typed refusal, NOT a fall-through to the (current) local store
    result = _inspect(world, trust=stale)
    assert result.exit_code == 1 and "unsupported approval schema_version" in result.output


# ------------------------------------------------------------------------------------------------- unreadable / invalid / missing external files

@pytest.mark.parametrize("name, fragment", [("bad.json", "could not parse approval artifact"), ("wrongschema.json", "unsupported approval schema_version")])
def test_an_invalid_external_trust_file_is_a_typed_refusal_and_the_trust_aware_command_denies(world, name, fragment):
    result = _inspect(world, trust=world["ext"] / name)
    assert result.exit_code == 1 and isinstance(result.exception, SystemExit) and "Traceback" not in result.output and fragment in result.output
    denied = _config(world, trust=world["ext"] / name)
    assert denied.exit_code == 1 and "Configuration-authority denied" in denied.output       # neither approves it


def test_a_non_utf8_external_trust_file_is_a_typed_refusal(world):
    (world["ext"] / "binary.json").write_bytes(b"\xff\xfe\x00bad")
    result = _inspect(world, trust=world["ext"] / "binary.json")
    assert result.exit_code == 1 and "Traceback" not in result.output and "could not parse approval artifact" in result.output


def test_a_missing_external_trust_file_is_reported_not_silently_ignored(world):
    missing = world["ext"] / "nope.json"
    inspected = _inspect(world, trust=missing)
    assert inspected.exit_code == 0 and f"No approval on file at the trust file {missing}" in inspected.output and "No local approval" not in inspected.output
    assert _config(world, trust=missing).exit_code == 1                                       # and it authorizes nothing


# ------------------------------------------------------------------------------------------------------------------------------ the one resolution

def test_checked_trust_file_is_the_one_resolution(tmp_path):
    ws, ext = tmp_path / "ws", tmp_path / "ext"
    ws.mkdir()
    ext.mkdir()
    env = _Env(str(ext / "env.json"))
    assert aa.checked_trust_file(str(ext / "flag.json"), str(ws), env) == str(ext / "flag.json")      # the flag wins
    assert aa.checked_trust_file(None, str(ws), env) == str(ext / "env.json")                          # else the environment
    assert aa.checked_trust_file(None, str(ws), _Env()) is None                           # else none
    with pytest.raises(aa.TrustPathInsideWorkspaceError):
        aa.checked_trust_file(str(ws / "t.json"), str(ws), env)                                         # inside: refused, whatever the environment says
    with pytest.raises(aa.TrustPathInsideWorkspaceError):
        aa.checked_trust_file(None, str(ws), _Env(str(ws / "sub" / "t.json")))


def test_load_config_and_inspect_call_the_same_resolution(world, monkeypatch):
    calls = []
    real = aa.checked_trust_file

    def spy(explicit, workspace_root, environment=None):
        calls.append(("inspect-or-load", explicit))
        return real(explicit, workspace_root, environment)
    monkeypatch.setattr(aa, "checked_trust_file", spy)
    _inspect(world, trust=world["valid"])
    _config(world, trust=world["valid"])
    assert len(calls) >= 2 and all(call[1] == str(world["valid"]) for call in calls)
