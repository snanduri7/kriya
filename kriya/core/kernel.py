import logging
from typing import Any, Optional
from kriya.core.registry import ComponentRegistry
from kriya.core.events import EventSystem

logger = logging.getLogger(__name__)

class Kernel:
    """The central coordinator of the Kriya AI Engineering Platform."""
    
    def __init__(self, config: Optional[Any] = None) -> None:
        self.registry = ComponentRegistry()
        self.events = EventSystem()
        self.config = config
        self._running = False
        
        from kriya.mcp import MCPManager
        self.mcp = MCPManager(self)

    async def start(self) -> None:
        """Start the kernel lifecycle."""
        if self._running:
            logger.warning("Kernel is already running.")
            return

        logger.info("Starting Kriya Kernel...")
        await self.events.emit("kernel_starting", {"kernel": self})
        
        # Start MCP Servers if configured
        if self.config and hasattr(self.config, "mcp") and self.config.mcp:
            logger.info("Initializing MCP Manager and starting servers...")
            await self.mcp.start_all(self.config.mcp)
            
        self._running = True
        
        await self.events.emit("kernel_started", {"kernel": self})
        logger.info("Kriya Kernel started successfully.")

    async def stop(self) -> None:
        """Stop the kernel lifecycle."""
        if not self._running:
            logger.warning("Kernel is not running.")
            return

        logger.info("Stopping Kriya Kernel...")
        await self.events.emit("kernel_stopping", {"kernel": self})
        
        # Shutdown MCP Servers
        logger.info("Stopping MCP servers...")
        await self.mcp.shutdown_all()
        
        self._running = False
        
        await self.events.emit("kernel_stopped", {"kernel": self})
        logger.info("Kriya Kernel stopped.")
