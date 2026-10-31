"""The scope of a wait on a human decision (W3-7, GENERATION-BUDGET-COUNTS-HUMAN-WAIT-001; MODULE-CONTRACTS layer 0).

Code that asks a person (an approval callback, a skill-gap prompt, a web-lookup review) enters ``human_wait()`` around the call AND the await of the answer. What the scope
does is the run owner's business - ``kriya.control.run_coordinator`` registers the provider that pauses the run's generation budget - so the authority and workflow layers depend
on this leaf, never on the run coordinator (DEPENDENCY-RULES: AUTHORITY must not import STATE). With no provider registered (a bare library import, a unit test of an
unrelated module) the scope is a no-op.
"""
from contextlib import nullcontext
from typing import Callable, ContextManager, Optional

_provider: Optional[Callable[[], ContextManager[None]]] = None


def register_human_wait_scope(provider: Optional[Callable[[], ContextManager[None]]]) -> None:
    """Install the provider of the scope (the run coordinator, at import). ``None`` removes it."""
    global _provider
    _provider = provider


def human_wait() -> ContextManager[None]:
    """The scope to enter while a human decides; a no-op context when no provider is registered."""
    provider = _provider
    return provider() if provider is not None else nullcontext()


__all__ = ["human_wait", "register_human_wait_scope"]
