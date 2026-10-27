"""TEST-ONLY mount-aware containment stand-in for semantic tests (S7, owner decision 2026-10-25).

It consumes the ACTUAL containment profile the production command construction produced and translates the container-visible paths of the command to the host
paths backing them, so a semantic test can run the contained command flow (the project ``.kriya/venv`` bind mount, ``/kriya/workspace/...`` arguments) without Docker. The
production contained command construction stays authoritative: this module restores no host command branch and builds no command of its own.

Mapping, taken from the profile (never invented here):
  - the workspace mount            /kriya/workspace              <- profile.workspace_path
  - every ``MountSpec``            spec.container_path           <- spec.host_path
  - the host-visible temp mount    /kriya/tmp/host               <- profile.temp_path
A command token that IS a mapped container path, or starts with it plus ``/`` (also the workspace-relative spelling of a mount under the workspace, and the value of a
``--flag=value`` token), is rewritten to the host path by a launcher prefix; the command then runs as an ordinary host process with Kriya's environment.

It does NOT prove, and no test may use it to claim: filesystem isolation, read-only enforcement, network isolation, authority masking, resource limits, an environment
allowlist, or Docker/image/lifecycle behaviour. Tests asserting those properties are real-OCI or containment-contract tests (seam N / the docker xdist group).
Network: the stand-in has none, and a suite must not depend on one. The only dependency acquisition the semantic tests trigger is the contained flow's
``<venv>/bin/python -m pip install -q pytest``: it is satisfied from the TEST interpreter's own installed pytest closure (a per-process directory of symlinks to exactly those
distributions, made importable in the venv by a ``.pth`` file), and an install of anything else fails exactly like an unreachable registry. Nothing else about pip is modelled.
Symlink emulation of a mount is NOT used (``python -m venv`` refuses a symlinked target). The Maven image/cache/toolchain are NOT simulated: real Maven builds belong to the
real-OCI tier.

It cannot become a production backend: it lives under tests/, no module under kriya/ names it, ``resolve_containment_backend`` does not know it, and the census fixture in
tests/conftest.py registers it for ONE test and removes it after (tests/test_s7_containment_census.py pins all of that).
"""
from __future__ import annotations

import atexit
import json
import os
import secrets
import shutil
import sys
import tempfile
from typing import Dict, List, Optional, Tuple

from kriya.tools.containment import (
    LAUNCHER_REACHED_MARKER,
    TARGET_NOT_STARTED_MARKER,
    ContainmentProfile,
    FiniteLaunchStage,
    PreparedContainment,
)

NAME = "containment-test-standin"
CONTAINER_WORKSPACE = "/kriya/workspace"      # pinned equal to kriya.tools.containment_oci._CONTAINER_WORKSPACE by tests/test_s7_containment_census.py
CONTAINER_TEMP_HOST = "/kriya/tmp/host"

# The launcher: rewrite the tokens that name a mapped container path, then replace this process with the real command (so exit codes, signals and stdio are the command's). A
# ``<python> -m pip install <pkg>...`` is never run: it is satisfied offline when every package is in the installable set, and fails like an unreachable registry otherwise.
# The finite-launch stage (kriya.tools.containment.FiniteLaunchStage) is modelled honestly and minimally: the launcher's own ``os.execvp`` is the point where this process is replaced
# by the target, so an OSError raised THERE is the launcher's own record that the target never started. It writes the per-run record Kriya reads and exits 126/127 like a failed
# exec; it never inspects the exit status of a command that did start (``os.execvp`` does not return when the target starts).
_LAUNCHER = (
    "import json, os, re, subprocess, sys\n"
    "config = json.loads(sys.argv[1])\n"
    "pairs = sorted(config['mapping'].items(), key=lambda kv: -len(kv[0]))\n"
    "def fix(path):\n"
    "    for src, dst in pairs:\n"
    "        if path == src or path.startswith(src + '/'):\n"
    "            return dst + path[len(src):]\n"
    "    return path\n"
    "def fix_token(token):\n"
    "    if token.startswith('-') and '=' in token:\n"
    "        flag, _, value = token.partition('=')\n"
    "        return flag + '=' + fix(value)\n"
    "    return fix(token)\n"
    "args = sys.argv[2:]\n"
    "record = None\n"
    "if args[:1] == ['--kriya-finite-launch']:\n"
    "    record, reached, args = args[1], args[2], args[3:]\n"
    "    sys.stderr.write('\\n' + reached + '\\n')\n"
    "    sys.stderr.flush()\n"
    "argv = [fix_token(t) for t in args]\n"
    "if argv[1:4] == ['-m', 'pip', 'install']:\n"
    "    wanted = [re.split(r'[<>=!~\\[; ]', a)[0].lower().replace('_', '-') for a in argv[4:] if not a.startswith('-')]\n"
    "    missing = [w for w in wanted if w not in config['installable']]\n"
    "    if missing or not wanted:\n"
    "        sys.stderr.write('ERROR: Could not find a version that satisfies the requirement ' + ' '.join(missing or argv[4:]) + ' (the test stand-in has no network)\\n')\n"
    "        sys.exit(1)\n"
    "    purelib = subprocess.run([argv[0], '-c', 'import sysconfig; print(sysconfig.get_paths()[\"purelib\"])'], capture_output=True, text=True, check=True).stdout.strip()\n"
    "    with open(os.path.join(purelib, 'kriya_standin_offline.pth'), 'w', encoding='utf-8') as pth:\n"
    "        pth.write(config['site'] + '\\n')\n"
    "    sys.exit(0)\n"
    "try:\n"
    "    os.execvp(argv[0], argv)\n"
    "except OSError as exc:\n"
    "    if record is None:\n"
    "        raise\n"
    "    sys.stderr.write('kriya-standin: cannot execute ' + argv[0] + ': ' + str(exc) + '\\n\\n' + record + '\\n')\n"
    "    sys.exit(126 if isinstance(exc, PermissionError) else 127)\n"
)

_OFFLINE: Optional[Tuple[str, List[str]]] = None


def _offline_site() -> Tuple[str, List[str]]:
    """(directory, canonical distribution names): symlinks to the test interpreter's pytest and every distribution it requires, built once per process."""
    global _OFFLINE  # pylint: disable=global-statement
    if _OFFLINE is not None:
        return _OFFLINE
    from importlib import metadata

    from packaging.requirements import Requirement
    from packaging.utils import canonicalize_name

    closure: Dict[str, "metadata.Distribution"] = {}
    todo = ["pytest"]
    while todo:
        name = canonicalize_name(todo.pop())
        if name in closure:
            continue
        dist = metadata.distribution(name)
        closure[name] = dist
        for text in dist.requires or ():
            requirement = Requirement(text)
            if requirement.marker is None or requirement.marker.evaluate({"extra": ""}):
                todo.append(requirement.name)
    site = tempfile.mkdtemp(prefix="kriya-standin-site-")
    atexit.register(shutil.rmtree, site, ignore_errors=True)
    for dist in closure.values():
        root = str(dist.locate_file(""))
        for entry in dist.files or ():
            top = entry.parts[0]
            if top in ("..", "__pycache__") or os.path.lexists(os.path.join(site, top)):
                continue
            os.symlink(os.path.join(root, top), os.path.join(site, top))
    _OFFLINE = (site, sorted(closure))
    return _OFFLINE


def path_mapping(profile: ContainmentProfile) -> Dict[str, str]:
    """container-visible path (absolute, and workspace-relative for mounts under the workspace) -> host backing path, from the profile alone."""
    mapping: Dict[str, str] = {}
    if profile.mount_workspace:
        mapping[CONTAINER_WORKSPACE] = profile.workspace_path
    for spec in profile.additional_mounts or ():
        mapping[spec.container_path] = spec.host_path
        if spec.container_path.startswith(CONTAINER_WORKSPACE + "/"):
            mapping[spec.container_path[len(CONTAINER_WORKSPACE) + 1:]] = spec.host_path    # the relative spelling the contained commands use (cwd is the workspace)
    if profile.temp_path:
        mapping[CONTAINER_TEMP_HOST] = profile.temp_path
    return mapping


class MountAwareStandIn:
    name = NAME

    def __init__(self) -> None:
        self.prepared_profiles: List[ContainmentProfile] = []
        self.prepared_commands: List[List[str]] = []

    def prepare(self, profile: ContainmentProfile, command: List[str]) -> PreparedContainment:
        self.prepared_profiles.append(profile)
        self.prepared_commands.append(list(command))
        site, installable = _offline_site()
        config = {"mapping": path_mapping(profile), "site": site, "installable": installable}
        prefix = [sys.executable, "-c", _LAUNCHER, json.dumps(config)]
        nonce = secrets.token_hex(16)
        stage = FiniteLaunchStage(
            prefix=("--kriya-finite-launch", f"{TARGET_NOT_STARTED_MARKER}{nonce}", f"{LAUNCHER_REACHED_MARKER}{nonce}"),
            marker=f"{TARGET_NOT_STARTED_MARKER}{nonce}", reached=f"{LAUNCHER_REACHED_MARKER}{nonce}",
        )
        return PreparedContainment(env=None, preexec_fn=None, backend_name=self.name, command_prefix=prefix, cleanup=None, finite_launch=stage)
