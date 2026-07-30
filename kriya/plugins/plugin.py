import importlib
import logging
import os
import sys
from abc import ABC, abstractmethod
from typing import Dict, List

from kriya.core.kernel import Kernel

logger = logging.getLogger(__name__)


class BasePlugin(ABC):
    """Abstract base class that all Kriya plugins must implement."""
    
    def __init__(self, kernel: Kernel) -> None:
        self.kernel = kernel

    @property
    @abstractmethod
    def name(self) -> str:
        """The name of the plugin."""
        pass

    @property
    @abstractmethod
    def version(self) -> str:
        """The version of the plugin."""
        pass

    async def initialize(self) -> None:
        """Initialize the plugin. Register components and listeners here."""
        pass

    async def shutdown(self) -> None:
        """Cleanup resources on shutdown."""
        pass

class PluginManager:
    """Discovers, loads, and manages the lifecycle of Kriya plugins."""
    
    def __init__(self, kernel: Kernel, plugin_dir: str) -> None:
        self.kernel = kernel
        self.plugin_dir = os.path.abspath(plugin_dir)
        self._plugins: Dict[str, BasePlugin] = {}

    def discover_and_load(self, enabled_plugins: List[str]) -> None:
        """Discovers and dynamically imports enabled plugins from the plugin directory."""
        if not os.path.exists(self.plugin_dir):
            logger.warning(f"Plugin directory '{self.plugin_dir}' does not exist.")
            return

        # Ensure plugin dir is in path
        if self.plugin_dir not in sys.path:
            sys.path.insert(0, self.plugin_dir)

        for folder in os.listdir(self.plugin_dir):
            path = os.path.join(self.plugin_dir, folder)
            if not os.path.isdir(path) or folder.startswith("_") or folder.startswith("."):
                continue
            
            # Skip if enabled_plugins list is specified and the plugin folder is not in it
            if enabled_plugins and folder not in enabled_plugins:
                logger.debug(f"Plugin '{folder}' is not in enabled list. Skipping.")
                continue

            # Look for __init__.py to load it as a package module
            init_file = os.path.join(path, "__init__.py")
            if not os.path.exists(init_file):
                logger.warning(f"Plugin folder '{folder}' is missing '__init__.py'. Skipping.")
                continue

            try:
                # Reload module if already imported to prevent cache issues in tests
                if folder in sys.modules:
                    del sys.modules[folder]
                    
                module = importlib.import_module(folder)
                
                plugin_found = False
                for attr_name in dir(module):
                    attr = getattr(module, attr_name)
                    if (
                        isinstance(attr, type)
                        and issubclass(attr, BasePlugin)
                        and attr is not BasePlugin
                    ):
                        plugin_instance = attr(self.kernel)
                        name = plugin_instance.name
                        
                        if name in self._plugins:
                            logger.warning(f"Plugin name collision: '{name}' already registered.")
                            continue
                            
                        self._plugins[name] = plugin_instance
                        plugin_found = True
                        logger.info(f"Loaded plugin '{name}' v{plugin_instance.version} from {path}")
                
                if not plugin_found:
                    logger.warning(f"Module '{folder}' loaded, but no subclass of BasePlugin was found.")
                    
            except Exception as e:
                logger.error(f"Failed to load plugin from '{folder}': {e}", exc_info=True)
                raise e

    async def initialize_all(self) -> None:
        """Initialize all loaded plugins."""
        for name, plugin in self._plugins.items():
            try:
                logger.info(f"Initializing plugin '{name}'...")
                await plugin.initialize()
                await self.kernel.events.emit("plugin_initialized", {"plugin_name": name, "plugin": plugin})
            except Exception as e:
                logger.error(f"Failed to initialize plugin '{name}': {e}", exc_info=True)
                raise e

    async def shutdown_all(self) -> None:
        """Shutdown all loaded plugins."""
        for name, plugin in list(self._plugins.items()):
            try:
                logger.info(f"Shutting down plugin '{name}'...")
                await plugin.shutdown()
                await self.kernel.events.emit("plugin_shutdown", {"plugin_name": name, "plugin": plugin})
            except Exception as e:
                logger.error(f"Error during plugin '{name}' shutdown: {e}", exc_info=True)

    def get_plugin(self, name: str) -> BasePlugin:
        """Retrieve a loaded plugin by name."""
        if name not in self._plugins:
            raise KeyError(f"Plugin '{name}' not found.")
        return self._plugins[name]

    def list_plugins(self) -> List[BasePlugin]:
        """List all loaded plugins."""
        return list(self._plugins.values())
