import pytest

from kriya.core.kernel import Kernel
from kriya.plugins.plugin import PluginManager


@pytest.mark.asyncio
async def test_plugin_discovery_and_lifecycle(tmp_path):
    # Setup mock plugin folder
    plugin_dir = tmp_path / "plugins"
    plugin_dir.mkdir()
    
    mock_plugin_pkg = plugin_dir / "my_mock_plugin"
    mock_plugin_pkg.mkdir()
    
    init_content = """
from kriya.plugins.plugin import BasePlugin

class TestPlugin(BasePlugin):
    @property
    def name(self) -> str:
        return "test_plugin"

    @property
    def version(self) -> str:
        return "2.1.0"

    async def initialize(self) -> None:
        self.kernel.registry.register("test_cat", "test_item", "plugin_val")

    async def shutdown(self) -> None:
        self.kernel.registry.unregister("test_cat", "test_item")
"""
    with open(mock_plugin_pkg / "__init__.py", "w") as f:
        f.write(init_content)
        
    kernel = Kernel()
    pm = PluginManager(kernel, str(plugin_dir))
    
    # Discover and Load
    pm.discover_and_load(enabled_plugins=[])
    
    loaded = pm.list_plugins()
    assert len(loaded) == 1
    assert loaded[0].name == "test_plugin"
    assert loaded[0].version == "2.1.0"
    
    # Initialize
    await pm.initialize_all()
    assert kernel.registry.get("test_cat", "test_item") == "plugin_val"
    
    # Shutdown
    await pm.shutdown_all()
    with pytest.raises(Exception):
        kernel.registry.get("test_cat", "test_item")
