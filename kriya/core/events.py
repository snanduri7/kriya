import inspect
import logging
from typing import Any, Awaitable, Callable, Dict, List, Optional, Union

logger = logging.getLogger(__name__)

EventHandler = Callable[[Dict[str, Any]], Union[None, Awaitable[None]]]

class EventSystem:
    """Asynchronous and Synchronous Event Emitter for Kriya components."""
    
    def __init__(self) -> None:
        self._handlers: Dict[str, List[EventHandler]] = {}

    def subscribe(self, event_name: str, handler: EventHandler) -> None:
        """Subscribe a handler to an event."""
        event_name = event_name.lower()
        if event_name not in self._handlers:
            self._handlers[event_name] = []
        if handler not in self._handlers[event_name]:
            self._handlers[event_name].append(handler)
            handler_name = getattr(handler, "__name__", str(handler))
            logger.debug(f"Subscribed handler '{handler_name}' to event '{event_name}'")

    def unsubscribe(self, event_name: str, handler: EventHandler) -> None:
        """Unsubscribe a handler from an event."""
        event_name = event_name.lower()
        if event_name in self._handlers and handler in self._handlers[event_name]:
            self._handlers[event_name].remove(handler)
            logger.debug(f"Unsubscribed handler from event '{event_name}'")

    async def emit(self, event_name: str, data: Dict[str, Any]) -> None:
        """Emit an event, notifying all subscribers. Handles both sync and async handlers.

        Every subscribed handler runs regardless of an earlier one raising -
        a broken handler previously stopped the loop outright (re-raised
        immediately), silently skipping every handler registered after it
        for the same event. Still raises afterward (the first exception
        encountered) so a caller relying on emit() propagating a failure -
        see test_event_handler_errors - keeps seeing one; this only fixes
        fault ISOLATION between handlers, not whether emit() can raise at all."""
        event_name = event_name.lower()
        handlers = self._handlers.get(event_name, [])
        if not handlers:
            return

        first_exception: Optional[Exception] = None
        for handler in handlers:
            try:
                if inspect.iscoroutinefunction(handler):
                    await handler(data)
                else:
                    handler(data)
            except Exception as e:
                logger.error(f"Error in event handler for '{event_name}': {e}", exc_info=True)
                if first_exception is None:
                    first_exception = e
        if first_exception is not None:
            raise first_exception
