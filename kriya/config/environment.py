"""The ONE reader of the process environment and the user's home directory for Kriya (full modularization D3; MODULE-CONTRACTS §3).

Every ``KRIYA_*`` variable, every Kriya home under ``~/.kriya`` and every other environment value Kriya derives a location or a switch
from is read here, through an ``Environment``; no other module reads ``os.environ``/``os.getenv``, calls ``Path.home()`` or expands ``~``
(``tests/architecture/test_direct_access_owners.py``; ``tools/sandbox.py`` and ``mcp/mcp.py`` build CHILD environments and stay owners of
that). ``ProcessEnvironment`` reads ``os.environ`` at CALL TIME on every call and caches nothing, so a test's ``monkeypatch.setenv`` (the
suite's ``tests/conftest.py`` isolation of ``KRIYA_STATE_DIR``/``KRIYA_LOG_DIR``/qualification and certification homes) takes effect
immediately, exactly as the direct reads it replaces did.

The per-home methods reproduce the expressions of the modules that own each home byte for byte, including their differences (some
expand ``~`` in an override, some do not; every one treats an empty value as unset): they are moved reads, not a new policy. Where a
caller realpaths, refuses a relative value or anchors it, it still does so itself (state_paths, logging_setup, authority_approval...).

This module imports nothing from Kriya (it is the leaf every layer may read: DEPENDENCY-RULES §1, CONFIG is L1).
"""
from __future__ import annotations

import os
from typing import Dict, Optional, Protocol, runtime_checkable

# The name of the Kriya directory: the user's ``~/.kriya`` (the homes below) and, beneath a workspace, ``<workspace>/.kriya`` (the
# directory STATE keeps its control records in; CONFIG's paths.state rule refers to it: kriya/config/resolution.py).
KRIYA_DIRECTORY_NAME = ".kriya"


def kriya_install_dir() -> str:
    """The Kriya checkout (or installation) root: the directory the packaged defaults' relative paths (``./plugins``, ``./skills``, ``./memory``)
    are resolved against (kriya/config/resolution.py). Derived from this file's location, so it is where Kriya is, never where a workspace is."""
    return os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))

# The closed vocabulary of variables Kriya reads (the owning modules keep their historical constant names as aliases of these).
STATE_DIR = "KRIYA_STATE_DIR"
LOG_DIR = "KRIYA_LOG_DIR"
AUTHORITY_HOME = "KRIYA_AUTHORITY_HOME"
TRUST_FILE = "KRIYA_TRUST_FILE"
MCP_APPROVAL_HOME = "KRIYA_MCP_APPROVAL_HOME"
STATIC_ANALYSIS_HOME = "KRIYA_STATIC_ANALYSIS_HOME"
QUALIFICATION_HOME = "KRIYA_QUALIFICATION_HOME"
CERTIFICATION_HOME = "KRIYA_CERTIFICATION_HOME"
ADJUDICATION_HOME = "KRIYA_ADJUDICATION_HOME"
MODEL_RUNTIME_PROBE = "KRIYA_MODEL_RUNTIME_PROBE"
EXECUTION_ENVIRONMENT_PROBE = "KRIYA_EXECUTION_ENVIRONMENT_PROBE"
OCI_IMAGES = ("KRIYA_OCI_IMAGE", "KRIYA_OCI_MAVEN_IMAGE", "KRIYA_OCI_GRADLE_IMAGE", "KRIYA_OCI_PYTHON_IMAGE")
TOOLCHAIN_VARIABLES = ("PATH", "HOME", "GRADLE_USER_HOME")
VARIABLES = frozenset({STATE_DIR, LOG_DIR, AUTHORITY_HOME, TRUST_FILE, MCP_APPROVAL_HOME, STATIC_ANALYSIS_HOME, QUALIFICATION_HOME,
                       CERTIFICATION_HOME, ADJUDICATION_HOME, MODEL_RUNTIME_PROBE, EXECUTION_ENVIRONMENT_PROBE, *OCI_IMAGES,
                       *TOOLCHAIN_VARIABLES})

_SWITCH_OFF = ("0", "false", "no", "off")


class UnknownEnvironmentVariable(KeyError):
    """A variable outside ``VARIABLES``: the vocabulary is closed (fail closed, never a silent read of an unlisted name)."""


@runtime_checkable
class Environment(Protocol):
    """Where Kriya's homes and switches come from. ``process_environment()`` is the real one; tests and ``ConfigService`` inject a fake."""

    def variable(self, name: str, default: Optional[str] = None) -> Optional[str]: ...
    def snapshot(self) -> Dict[str, str]: ...
    def expand_user(self, path: str) -> str: ...
    def user_home(self) -> str: ...


class ProcessEnvironment:
    """The process's environment and home, read at call time (never cached)."""

    def variable(self, name: str, default: Optional[str] = None) -> Optional[str]:
        """``os.environ.get(name, default)`` for a name of the closed vocabulary."""
        if name not in VARIABLES:
            raise UnknownEnvironmentVariable(name)
        return os.environ.get(name, default)

    def snapshot(self) -> Dict[str, str]:
        """A copy of the whole environment, for a CHILD process built from it (``dict(os.environ)``)."""
        return dict(os.environ)

    def expand_user(self, path: str) -> str:
        """``os.path.expanduser(path)``: ``~`` is the home of this environment."""
        return os.path.expanduser(path)

    def user_home(self) -> str:
        return os.path.expanduser("~")


_PROCESS = ProcessEnvironment()


def process_environment() -> Environment:
    """The real environment. Stateless: one instance serves every caller, and every read happens at call time."""
    return _PROCESS


def _env(environment: Optional[Environment]) -> Environment:
    return environment if environment is not None else _PROCESS


def user_home(environment: Optional[Environment] = None) -> str:
    """``~`` of the environment (``os.path.expanduser("~")``)."""
    return _env(environment).user_home()


def kriya_home(*parts: str, environment: Optional[Environment] = None) -> str:
    """``~/.kriya/<parts>`` of the environment's home (not realpath'd: each owner decides)."""
    return os.path.join(_env(environment).user_home(), KRIYA_DIRECTORY_NAME, *parts)


# ------------------------------------------------------------------ the homes (MODULE-CONTRACTS §3), each exactly its owner's expression

def state_dir(environment: Optional[Environment] = None) -> Optional[str]:
    """``KRIYA_STATE_DIR`` as set (None when unset; an empty value is returned as is and treated as unset by every caller)."""
    return _env(environment).variable(STATE_DIR)


def default_state_dir(environment: Optional[Environment] = None) -> str:
    """``~/.kriya/state``."""
    return kriya_home("state", environment=environment)


def log_dir(environment: Optional[Environment] = None) -> Optional[str]:
    """``KRIYA_LOG_DIR`` as set."""
    return _env(environment).variable(LOG_DIR)


def default_log_dir(environment: Optional[Environment] = None) -> str:
    """``~/.kriya/logs``."""
    return kriya_home("logs", environment=environment)


def authority_home(environment: Optional[Environment] = None) -> str:
    """``KRIYA_AUTHORITY_HOME`` (not expanded), else ``~/.kriya/authority``."""
    override = _env(environment).variable(AUTHORITY_HOME)
    return override if override else kriya_home("authority", environment=environment)


def mcp_approval_home(environment: Optional[Environment] = None) -> str:
    """``KRIYA_MCP_APPROVAL_HOME`` (not expanded), else ``~/.kriya/mcp_approvals``."""
    override = _env(environment).variable(MCP_APPROVAL_HOME)
    return override if override else kriya_home("mcp_approvals", environment=environment)


def static_analysis_home(environment: Optional[Environment] = None) -> str:
    """``KRIYA_STATIC_ANALYSIS_HOME`` (not expanded), else ``~/.kriya/static_analysis/waivers``."""
    return _env(environment).variable(STATIC_ANALYSIS_HOME) or kriya_home("static_analysis", "waivers", environment=environment)


def qualification_home(environment: Optional[Environment] = None) -> str:
    """``KRIYA_QUALIFICATION_HOME`` with ``~`` expanded, else ``~/.kriya/qualifications``."""
    env = _env(environment)
    configured = env.variable(QUALIFICATION_HOME)
    return env.expand_user(configured) if configured else kriya_home("qualifications", environment=environment)


def certification_home(environment: Optional[Environment] = None) -> str:
    """``KRIYA_CERTIFICATION_HOME`` with ``~`` expanded, else ``~/.kriya/certifications``."""
    env = _env(environment)
    configured = env.variable(CERTIFICATION_HOME)
    return env.expand_user(configured) if configured else kriya_home("certifications", environment=environment)


def adjudication_home(environment: Optional[Environment] = None) -> str:
    """``KRIYA_ADJUDICATION_HOME`` (not expanded), else ``~/.kriya/adjudications``."""
    return _env(environment).variable(ADJUDICATION_HOME) or kriya_home("adjudications", environment=environment)


def trust_file(environment: Optional[Environment] = None) -> Optional[str]:
    """``KRIYA_TRUST_FILE`` as set."""
    return _env(environment).variable(TRUST_FILE)


def _switch(name: str, environment: Optional[Environment]) -> bool:
    return _env(environment).variable(name, "1").strip().lower() not in _SWITCH_OFF


def model_runtime_probe_enabled(environment: Optional[Environment] = None) -> bool:
    """``KRIYA_MODEL_RUNTIME_PROBE``: on unless 0/false/no/off (unset = on)."""
    return _switch(MODEL_RUNTIME_PROBE, environment)


def execution_environment_probe_enabled(environment: Optional[Environment] = None) -> bool:
    """``KRIYA_EXECUTION_ENVIRONMENT_PROBE``: on unless 0/false/no/off (unset = on)."""
    return _switch(EXECUTION_ENVIRONMENT_PROBE, environment)


def toolchain(name: str, default: Optional[str] = None, environment: Optional[Environment] = None) -> Optional[str]:
    """``PATH``, ``HOME`` or ``GRADLE_USER_HOME`` as set (``os.environ.get(name, default)``)."""
    if name not in TOOLCHAIN_VARIABLES:
        raise UnknownEnvironmentVariable(name)
    return _env(environment).variable(name, default)


def oci_image(name: str, default: str, environment: Optional[Environment] = None) -> Optional[str]:
    """One of the four ``KRIYA_OCI_*_IMAGE`` overrides (``os.environ.get(name, default)``)."""
    if name not in OCI_IMAGES:
        raise UnknownEnvironmentVariable(name)
    return _env(environment).variable(name, default)


def child_environment(environment: Optional[Environment] = None) -> Dict[str, str]:
    """A copy of the whole environment for a child process (git with a scratch index, a JDK-pinned build, the language server)."""
    return _env(environment).snapshot()


def expand_user(path: str, environment: Optional[Environment] = None) -> str:
    """``~`` expansion of a path value (a configured path, a protected source file, a leak-check input)."""
    return _env(environment).expand_user(path)


__all__ = ["ADJUDICATION_HOME", "AUTHORITY_HOME", "CERTIFICATION_HOME", "EXECUTION_ENVIRONMENT_PROBE", "Environment", "KRIYA_DIRECTORY_NAME",
           "LOG_DIR", "MCP_APPROVAL_HOME", "MODEL_RUNTIME_PROBE", "OCI_IMAGES", "ProcessEnvironment", "QUALIFICATION_HOME", "STATE_DIR",
           "STATIC_ANALYSIS_HOME", "TOOLCHAIN_VARIABLES", "TRUST_FILE", "UnknownEnvironmentVariable", "VARIABLES", "adjudication_home",
           "authority_home", "certification_home", "child_environment", "default_log_dir", "default_state_dir", "execution_environment_probe_enabled",
           "expand_user", "kriya_home", "kriya_install_dir", "log_dir", "mcp_approval_home", "model_runtime_probe_enabled", "oci_image", "process_environment",
           "qualification_home", "state_dir", "static_analysis_home", "toolchain", "trust_file", "user_home"]
