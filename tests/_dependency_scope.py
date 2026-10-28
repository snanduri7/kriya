"""R3 test helper: a validator whose verification scope is held open for the whole test.

``PolymorphicValidator._maven_cache_dir`` / ``_dependency_cache_dir`` answer only inside a verification scope (a gate, a verification session, an authority run):
the dependency layer is ephemeral and belongs to that scope (kriya/tools/dependency_cache.py). Tests of the Maven/Gradle two-phase runners that call
``_run_maven_cmd`` / ``_run_gradle_cmd`` directly - with ``_run_cmd_with_timeout`` scripted - are about the runner's state machine, not the scope lifecycle (pinned in
tests/test_maven_cache_location.py), so they hold one scope open. The layer lives under the test's isolated state directory and is removed with it."""
import contextlib
import os
from pathlib import Path
from typing import Any

from kriya.tools.dependency_cache import create_ephemeral_cache


def hold_dependency_scope(validator: Any) -> Any:
    """Enter ``validator.dependency_cache_scope()`` and keep it open; returns the validator."""
    scope = validator.dependency_cache_scope()
    scope.__enter__()  # pylint: disable=unnecessary-dunder-call
    validator._held_dependency_scope = scope  # pylint: disable=protected-access  # a dropped context manager would close its generator, and the scope with it
    return validator


def ephemeral_cache(root: Any, tool: str = "maven") -> str:
    """A real ephemeral dependency directory (the one kind of cache a container may mount writable) under ``root`` - for tests that hand a profile to the
    backend directly, without a validator scope."""
    return create_ephemeral_cache(os.path.join(str(root), "ephemeral-state"), tool)


@contextlib.contextmanager
def null_dependency_scope(_validator: Any):
    """The ``dependency_cache_scope`` of a validator DOUBLE: ``run_authority_bundle`` enters the scope its validator offers; a double that scripts every command has no
    layer to manage."""
    yield


def ephemeral_cache_path(root: Any, tool: str = "maven") -> Path:
    return Path(ephemeral_cache(root, tool))
