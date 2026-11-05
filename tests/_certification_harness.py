"""PRD-035 harness helpers shared by the live matrix (tests/test_live_prd035_certification.py) and its offline controls
(tests/test_prd035_harness_controls.py).

The C6 spy observes which model each Developer request goes to. The production identity runs through the runtime adapter its
configuration names (``llm.inference_runtime``; ``ollama_native`` for ``kriya-ollama-native/3``), so the spy wraps THAT adapter's
class, exactly as ``tests/test_live_prd032_chaos.py::_malformed_first_developer`` does; it never assumes the packaged ``ollama``
adapter (a spy registered over ``ollama`` sees no request of a native-bound configuration).
"""
from contextlib import contextmanager
from typing import Any, Callable, Dict, Iterator, List, Optional
from unittest.mock import patch

from _chaos_harness import role_of

from kriya.core.inference_runtime import register_runtime_adapter, runtime_adapter
from kriya.core.model_runtime import clear_model_runtime_cache


def fail_first_compiles(fail_while: Callable[[], bool]) -> Any:
    """Patch the compile gate: a failure while ``fail_while()`` holds."""
    from kriya.tools.validate import PolymorphicValidator

    real = PolymorphicValidator.run_compile_check

    def gate(self, *args, **kwargs):
        if fail_while():
            return {"success": False, "output": "calc.py:1: error: injected compile failure (certification C5/C6)"}
        return real(self, *args, **kwargs)

    return patch.object(PolymorphicValidator, "run_compile_check", gate)


def fails_until_developer_reaches(models: List[str], model: str) -> Callable[[], bool]:
    """The C6 release condition: the injected compile failure holds until the most recent Developer request went to ``model``."""
    return lambda: not models or models[-1] != model


def developer_model_spy(base: type, models: List[str]) -> Any:
    """``base`` (the configured adapter's class) recording the model of every Developer request in ``models``."""

    class _DeveloperModelSpy(base):  # type: ignore[valid-type, misc]
        async def complete(self, client, request):
            if role_of(request) == "developer":
                models.append(request.model)
            return await super().complete(client, request)

    return _DeveloperModelSpy()


def configured_runtime_names(config: Any) -> List[Optional[str]]:
    """The runtime adapters the configuration runs through: the primary's and every fallback's (None: the packaged default)."""
    names: List[Optional[str]] = [config.llm.inference_runtime or None]
    for entry in config.llm_chain:
        if (entry.inference_runtime or None) not in names:
            names.append(entry.inference_runtime or None)
    return names


@contextmanager
def developer_model_spy_installed(config: Any, models: List[str]) -> Iterator[List[str]]:
    """Wrap every adapter ``config`` names with the Developer-model spy; the packaged adapters come back on exit."""
    packaged: Dict[Optional[str], Any] = {name: runtime_adapter(name) for name in configured_runtime_names(config)}
    try:
        for adapter in packaged.values():
            register_runtime_adapter(developer_model_spy(type(adapter), models))
        clear_model_runtime_cache()
        yield models
    finally:
        for adapter in packaged.values():
            register_runtime_adapter(adapter)
        clear_model_runtime_cache()
