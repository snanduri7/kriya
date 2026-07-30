import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

class ComponentRegistryError(Exception):
    """Base exception for component registry errors."""
    pass

class ComponentRegistry:
    """Registry for dynamically discovering and loading Kriya capabilities."""
    
    def __init__(self) -> None:
        # Format: {category: {name: component_instance_or_class}}
        self._registry: Dict[str, Dict[str, Any]] = {}
        # Format: {category: {name: metadata_dict}}
        self._metadata: Dict[str, Dict[str, Dict[str, Any]]] = {}

    def register(self, category: str, name: str, component: Any, metadata: Optional[Dict[str, Any]] = None) -> None:
        """Register a component under a specific category and name."""
        if not category or not name:
            raise ComponentRegistryError("Category and name must be non-empty strings.")
        
        category = category.lower()
        name = name.lower()
        
        if category not in self._registry:
            self._registry[category] = {}
            self._metadata[category] = {}
            
        if name in self._registry[category]:
            logger.warning(f"Overwriting component '{name}' in category '{category}'")
            
        self._registry[category][name] = component
        self._metadata[category][name] = metadata or {}
        logger.info(f"Registered component '{name}' under category '{category}'")

    def get(self, category: str, name: str) -> Any:
        """Retrieve a component by category and name."""
        category = category.lower()
        name = name.lower()
        if category not in self._registry or name not in self._registry[category]:
            raise ComponentRegistryError(f"Component '{name}' in category '{category}' not found.")
        return self._registry[category][name]

    def get_metadata(self, category: str, name: str) -> Dict[str, Any]:
        """Retrieve metadata for a registered component."""
        category = category.lower()
        name = name.lower()
        if category not in self._metadata or name not in self._metadata[category]:
            raise ComponentRegistryError(f"Metadata for component '{name}' in category '{category}' not found.")
        return self._metadata[category][name]

    def list_components(self, category: str) -> List[str]:
        """List all component names registered under a category."""
        return list(self._registry.get(category.lower(), {}).keys())

    def list_categories(self) -> List[str]:
        """List all registered categories."""
        return list(self._registry.keys())

    def unregister(self, category: str, name: str) -> None:
        """Unregister a component."""
        category = category.lower()
        name = name.lower()
        if category in self._registry and name in self._registry[category]:
            del self._registry[category][name]
            del self._metadata[category][name]
            logger.info(f"Unregistered component '{name}' from category '{category}'")
        else:
            raise ComponentRegistryError(f"Component '{name}' in category '{category}' not found.")
