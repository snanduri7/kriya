import pytest

from kriya.core.kernel import Kernel


@pytest.mark.asyncio
async def test_kernel_lifecycle_events():
    kernel = Kernel()
    events_fired = []
    
    def on_starting(data):
        events_fired.append("starting")
        assert data["kernel"] == kernel
        
    def on_started(data):
        events_fired.append("started")
        
    def on_stopping(data):
        events_fired.append("stopping")
        
    def on_stopped(data):
        events_fired.append("stopped")
        
    kernel.events.subscribe("kernel_starting", on_starting)
    kernel.events.subscribe("kernel_started", on_started)
    kernel.events.subscribe("kernel_stopping", on_stopping)
    kernel.events.subscribe("kernel_stopped", on_stopped)
    
    await kernel.start()
    assert events_fired == ["starting", "started"]
    
    await kernel.stop()
    assert events_fired == ["starting", "started", "stopping", "stopped"]
