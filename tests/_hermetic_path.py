"""A PATH that holds exactly the executables a test means to expose (HOSTED-PATH-HERMETICITY-001).

A test that models "this tool is NOT installed" must not do it with ``PATH=/usr/bin:/bin``: a hosted runner image is allowed to ship git-lfs, Maven, zsh or any
other tool there (MEASURED, run 41758354007: ``/usr/bin/git-lfs`` made all 64 tests of tests/test_s19p1d_activation.py fail in their fixture's precondition, and a host
``mvn`` hid the "executable missing / not executable" conditions of tests/test_pom_validate_fail_closed.py). Nothing is uninstalled and no runner is special-cased:
the directory built here contains symlinks to the host's OWN copies of the named tools and nothing else, and the tools that must be unresolvable are asserted so.
"""
from __future__ import annotations

import shutil
from pathlib import Path
from typing import Sequence


def hermetic_path(directory: Path, tools: Sequence[str], *, absent: Sequence[str] = ()) -> str:
    """Create ``directory`` with a symlink per name in ``tools`` (resolved on the CURRENT PATH, so call this before the test replaces PATH) and return it as a PATH
    value. Every name in ``absent`` must NOT resolve on that PATH; a tool the host lacks is an error, never a silent omission."""
    directory.mkdir(parents=True, exist_ok=True)
    for tool in tools:
        found = shutil.which(tool)
        if found is None:
            raise RuntimeError(f"hermetic PATH: the host has no {tool!r} to expose")
        (directory / tool).symlink_to(found)
    path = str(directory)
    for name in absent:
        if shutil.which(name, path=path) is not None:
            raise RuntimeError(f"hermetic PATH: {name!r} must not resolve, but does under {path}")
    return path
