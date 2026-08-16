import asyncio

import pytest

from kriya.core.events import EventSystem


@pytest.mark.asyncio
async def test_event_emitter_sync_handler():
    emitter = EventSystem()
    received = []
    
    def sync_handler(data):
        received.append(data)
        
    emitter.subscribe("test_event", sync_handler)
    await emitter.emit("test_event", {"payload": 123})
    
    assert len(received) == 1
    assert received[0] == {"payload": 123}

@pytest.mark.asyncio
async def test_event_emitter_async_handler():
    emitter = EventSystem()
    received = []
    
    async def async_handler(data):
        await asyncio.sleep(0.01)
        received.append(data)
        
    emitter.subscribe("test_event", async_handler)
    await emitter.emit("test_event", {"payload": 456})
    
    assert len(received) == 1
    assert received[0] == {"payload": 456}

@pytest.mark.asyncio
async def test_event_emitter_unsubscribe():
    emitter = EventSystem()
    received = []
    
    def handler(data):
        received.append(data)
        
    emitter.subscribe("test_event", handler)
    await emitter.emit("test_event", {"payload": 1})
    assert len(received) == 1
    
    emitter.unsubscribe("test_event", handler)
    await emitter.emit("test_event", {"payload": 2})
    assert len(received) == 1  # unchanged

@pytest.mark.asyncio
async def test_event_handler_errors():
    emitter = EventSystem()
    
    def broken_handler(data):
        raise ValueError("Something went wrong")
        
    emitter.subscribe("broken_event", broken_handler)
    
    # Verify the error propagates (or is logged/handled if design dictates)
    # Our implementation propagates the exception to help troubleshoot.
    with pytest.raises(ValueError, match="Something went wrong"):
        await emitter.emit("broken_event", {})


@pytest.mark.asyncio
async def test_event_handler_error_does_not_skip_later_handlers():
    """Regression test for a finding from the 2026-08-12 SME review: a
    broken handler previously re-raised immediately inside the loop, so any
    handler registered AFTER it for the same event never ran at all. Every
    handler must still get a chance to run - emit() still raises afterward
    (test_event_handler_errors above), just not before every subscriber has
    been notified."""
    emitter = EventSystem()
    received = []

    def broken_handler(data):
        raise ValueError("Something went wrong")

    def later_handler(data):
        received.append(data)

    emitter.subscribe("broken_event", broken_handler)
    emitter.subscribe("broken_event", later_handler)

    with pytest.raises(ValueError, match="Something went wrong"):
        await emitter.emit("broken_event", {"payload": 1})

    assert received == [{"payload": 1}]


@pytest.mark.asyncio
async def test_event_handler_error_raises_the_first_exception_not_the_last():
    emitter = EventSystem()

    def first_broken(data):
        raise ValueError("first")

    def second_broken(data):
        raise ValueError("second")

    emitter.subscribe("broken_event", first_broken)
    emitter.subscribe("broken_event", second_broken)

    with pytest.raises(ValueError, match="first"):
        await emitter.emit("broken_event", {})
