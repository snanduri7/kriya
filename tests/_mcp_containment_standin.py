"""TEST-ONLY containment stand-in for MCP semantic tests (D2 S5, owner decision 2026-10-25).

It models the CONTRACT of an isolating backend - ``prepare(profile, command)`` returns a launch prefix the server is started THROUGH, and the server
then sees only the profile's resolved environment - so the contained branch of kriya/mcp/mcp.py (the canonical decision, the isolating-backend check, the
prepared prefix, ``containment_active``) can be exercised deterministically without Docker. It is NOT evidence that real isolation occurred: the "container"
is a plain host process started through a Python launcher. Genuine production-isolation proof lives in tests/test_tool003_p2_oci_enforcement.py and
tests/test_s5_mcp_real_oci.py (real OCI).

It cannot become a production backend: it lives under tests/, no module under kriya/ names it, ``resolve_containment_backend`` does not know it, and
the ``mcp_standin`` fixture (tests/conftest.py) registers it for ONE test and removes it after; tests/test_s5_mcp_containment.py pins all of that.
"""
from __future__ import annotations

import json
import sys
from typing import Any, List, Optional

from kriya.config.config import AppConfig, AutonomyConfig
from kriya.tools.containment import BackendUnavailableError, ContainmentProfile, PreparedContainment

STANDIN_BACKEND_NAME = "mcp-test-standin"

# the launch prefix: replace this process with the real server, whose environment is EXACTLY the profile's resolved one
_LAUNCHER = "import json, os, sys\nenv = json.loads(sys.argv[1])\nos.execvpe(sys.argv[2], sys.argv[2:], env)"


class ContainmentStandIn:
    name = STANDIN_BACKEND_NAME

    def __init__(self, *, should_fail: bool = False) -> None:
        self.should_fail = should_fail
        self.prepared_profiles: List[ContainmentProfile] = []
        self.prepared_commands: List[List[str]] = []
        self.cleaned = 0

    def prepare(self, profile: ContainmentProfile, command: List[str]) -> PreparedContainment:
        if self.should_fail:
            raise BackendUnavailableError("the test stand-in is configured to be unavailable")
        self.prepared_profiles.append(profile)
        self.prepared_commands.append(list(command))
        prefix = [sys.executable, "-c", _LAUNCHER, json.dumps(dict(profile.resolved_env or {}))]
        return PreparedContainment(env=None, preexec_fn=None, backend_name=self.name, command_prefix=prefix, cleanup=self._cleanup)

    def _cleanup(self) -> None:
        self.cleaned += 1


class NoPrefixBackend:
    """An isolating-by-the-owner's-definition backend that prepares nothing to launch through: the shape of a backend that would let a server run on the
    host while containment is reported. Used only to prove that case is refused."""

    name = "mcp-test-no-prefix"

    def __init__(self) -> None:
        self.cleaned = 0

    def prepare(self, profile: ContainmentProfile, command: List[str]) -> PreparedContainment:
        del profile, command
        return PreparedContainment(env=None, preexec_fn=None, backend_name=self.name, command_prefix=None, cleanup=self._cleanup)

    def _cleanup(self) -> None:
        self.cleaned += 1


# --- the helpers the converted MCP semantic tests use: the contained branch, configured the way the final contract will configure it ---------------

CURRENT: Optional[ContainmentStandIn] = None   # the stand-in of the running test; set by the ``mcp_standin`` fixture (tests/conftest.py), cleared after it


def current() -> ContainmentStandIn:
    assert CURRENT is not None, "this test needs the `mcp_standin` fixture (usefixtures)"
    return CURRENT


def client_kwargs() -> dict:
    """MCPClient keyword arguments for the contained branch (a client constructed directly is handed its backend)."""
    return {"containment_required": True, "containment_backend": current()}


def autonomy(**fields: Any) -> AutonomyConfig:
    """The MCP mandate on, through the stand-in: what a semantic MCP test configures instead of a host-spawning default."""
    return AutonomyConfig(mcp_contained_execution_required=True, containment_backend=STANDIN_BACKEND_NAME, **fields)


def app_config(**fields: Any) -> AppConfig:
    """An AppConfig whose MCP servers start through the stand-in (any other ``AppConfig`` field as usual)."""
    autonomy_fields = fields.pop("autonomy_fields", {})
    return AppConfig(autonomy=autonomy(**autonomy_fields), **fields)
