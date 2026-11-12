"""A scripted docker CLI + daemon double for the registry-proxy start path (CONTAINMENT-PROXY-START-TIMEOUT-ORPHAN-001).

It models what Scenario-A run 8 measured (Docker Desktop 4.38.0 backend log, 2026-11-12): ``docker create`` answers at once, the
backend HOLDS the start request for ``start_hold`` seconds, and the held request is still served after the client gave up - against
whatever network state exists at that moment. Time is virtual: the double owns the clock the module under test reads.
"""
from __future__ import annotations

import subprocess
import types
from typing import Any, Dict, List, Optional, Tuple


class FakeDocker:
    def __init__(self, *, start_hold: float = 0.4, start_outcome: str = "running", start_error: Optional[str] = None,
                 ready: bool = True, rm_container_fails: bool = False) -> None:
        self.now = 1000.0
        self.start_hold = start_hold
        self.start_outcome = start_outcome            # the state the backend reaches when the held start is served
        self.start_error = start_error                # an immediate daemon refusal of the start call (stderr, exit 1)
        self.ready = ready
        self.rm_container_fails = rm_container_fails
        self.horizon = 3600.0                         # a wait longer than this is a test failure, not a hang
        self.networks: set = set()
        self.containers: Dict[str, Dict[str, Any]] = {}
        self.pending: List[Tuple[float, str]] = []    # (virtual time the backend serves the start, container)
        self.calls: List[Tuple[float, Tuple[str, ...]]] = []

    # --- the clock the module reads ---------------------------------------------------------------------------------------------
    def clock(self) -> types.SimpleNamespace:
        return types.SimpleNamespace(monotonic=lambda: self.now, sleep=self.advance, time=lambda: self.now)

    def advance(self, seconds: float) -> None:
        self.now += seconds
        if self.now > 1000.0 + self.horizon:
            raise AssertionError(f"FakeDocker: the code under test is still waiting after {self.horizon:g} virtual seconds (unbounded wait)")
        self._serve_pending()

    def _serve_pending(self) -> None:
        for due, name in list(self.pending):
            if due > self.now:
                continue
            self.pending.remove((due, name))
            container = self.containers.get(name)
            if container is None:
                continue
            if container["network"] not in self.networks:      # run 8: "network kriya-acq-net-... not found", exit code 128
                container.update(error=f"network {container['network']} not found", exit_code=128)
            else:
                container["status"] = self.start_outcome
                if self.start_outcome != "running":
                    container.update(exit_code=139)

    # --- subprocess.run ---------------------------------------------------------------------------------------------------------
    def run(self, argv: List[str], **kwargs: Any) -> subprocess.CompletedProcess:
        self._serve_pending()
        args = tuple(argv[1:])
        self.calls.append((self.now, args))
        timeout = kwargs.get("timeout")
        verb = args[0]
        if verb == "network" and args[1] == "create":
            self.networks.add(args[-1])
            return self._done(argv)
        if verb == "network" and args[1] == "rm":
            name = args[-1]
            if any(c["network"] == name and c["status"] == "running" for c in self.containers.values()):
                return self._done(argv, 1, stderr=f"error while removing network: network {name} has active endpoints")
            self.networks.discard(name)
            return self._done(argv)
        if verb in ("run", "create"):
            name = args[args.index("--name") + 1]
            self.containers[name] = {"status": "created", "network": args[args.index("--network") + 1], "error": "", "exit_code": 0}
            if verb == "create":
                return self._done(argv, stdout="1bcdf989e6fd\n")
            return self._start(argv, name, timeout)
        if verb == "start":
            return self._start(argv, args[-1], timeout)
        if verb == "inspect":
            name = next((a for a in args[1:] if a.startswith("kriya-")), args[-1])   # the name comes before or after --format
            container = self.containers.get(name)
            if container is None:
                return self._done(argv, 1, stderr=f"Error response from daemon: No such container: {name}")
            if "IPAddress" in " ".join(args):
                return self._done(argv, stdout="172.19.0.2\n")
            return self._done(argv, stdout=f"{container['status']}|{container['exit_code']}|{container['error']}\n")
        if verb == "exec":
            name = args[1]
            container = self.containers.get(name)
            if container is None or container["status"] != "running":
                return self._done(argv, 1, stderr="container is not running")
            if "nc" in args:
                return self._done(argv, 0 if self.ready else 1)
            return self._done(argv, stdout="")
        if verb == "rm":
            name = args[-1]
            if self.rm_container_fails and "proxy" in name:
                return self._done(argv, 1, stderr="Error response from daemon: removal of container is already in progress")
            self.containers.pop(name, None)
            return self._done(argv)
        raise AssertionError(f"FakeDocker: unscripted docker call {args!r}")

    def _start(self, argv: List[str], name: str, timeout: Optional[float]) -> subprocess.CompletedProcess:
        if self.start_error is not None:
            return self._done(argv, 1, stderr=self.start_error)
        if timeout is not None and self.start_hold > timeout:
            self.pending.append((self.now + self.start_hold, name))   # the backend still serves it later
            self.advance(timeout)
            raise subprocess.TimeoutExpired(argv, timeout)
        self.pending.append((self.now + self.start_hold, name))
        self.advance(self.start_hold)
        return self._done(argv, stdout="1bcdf989e6fd\n")

    @staticmethod
    def _done(argv: List[str], returncode: int = 0, stdout: str = "", stderr: str = "") -> subprocess.CompletedProcess:
        return subprocess.CompletedProcess(argv, returncode, stdout=stdout, stderr=stderr)

    # --- what a test asserts on -------------------------------------------------------------------------------------------------
    def verbs(self) -> List[str]:
        return [" ".join(args[:2]) if args[0] == "network" else args[0] for _, args in self.calls]

    def at(self, *prefix: str) -> List[float]:
        return [t - 1000.0 for t, args in self.calls if args[:len(prefix)] == prefix]

    def proxy(self) -> Optional[Dict[str, Any]]:
        return next((c for name, c in self.containers.items() if "proxy" in name), None)


def registry_profile(workspace: str):
    from kriya.tools.containment import ContainmentProfile, NetworkAuthority, TrustClass

    return ContainmentProfile(
        trust_class=TrustClass.UNTRUSTED_EXECUTION, workspace_path=workspace,
        network=NetworkAuthority.DEPENDENCY_REGISTRY_ONLY, network_destinations=("repo.maven.apache.org",),
    )


def install(monkeypatch, fake: FakeDocker) -> None:
    """Route the module's docker calls and its clock to ``fake``; the image/capability preflights (their own tests) are stubbed."""
    from kriya.tools import containment_oci

    monkeypatch.setattr(containment_oci, "subprocess", types.SimpleNamespace(
        run=fake.run, TimeoutExpired=subprocess.TimeoutExpired, CompletedProcess=subprocess.CompletedProcess))
    monkeypatch.setattr(containment_oci, "time", fake.clock())
    monkeypatch.setattr(containment_oci, "_ensure_image", lambda *a, **k: None)
    monkeypatch.setattr(containment_oci, "_check_net_admin_available", lambda *a, **k: None)
