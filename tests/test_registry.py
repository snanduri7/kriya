import pytest

from kriya.core.registry import ComponentRegistry, ComponentRegistryError


def test_registry_registration():
    registry = ComponentRegistry()
    
    # Register component
    my_comp = {"status": "ok"}
    registry.register("tool", "my_tool", my_comp, {"version": "1.0"})
    
    # Retrieve
    assert registry.get("tool", "my_tool") == my_comp
    assert registry.get_metadata("tool", "my_tool") == {"version": "1.0"}
    
    # Case insensitivity
    assert registry.get("TOOL", "MY_TOOL") == my_comp
    
    # List
    assert "my_tool" in registry.list_components("tool")
    assert "tool" in registry.list_categories()

def test_registry_unregister():
    registry = ComponentRegistry()
    registry.register("tool", "my_tool", "something")
    assert "my_tool" in registry.list_components("tool")
    
    registry.unregister("tool", "my_tool")
    assert "my_tool" not in registry.list_components("tool")
    
    with pytest.raises(ComponentRegistryError):
        registry.get("tool", "my_tool")

def test_registry_missing_error():
    registry = ComponentRegistry()
    with pytest.raises(ComponentRegistryError):
        registry.get("tool", "does_not_exist")

def test_registry_invalid_args():
    registry = ComponentRegistry()
    with pytest.raises(ComponentRegistryError):
        registry.register("", "name", "val")
    with pytest.raises(ComponentRegistryError):
        registry.register("cat", "", "val")
