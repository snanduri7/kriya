import json
import logging
import os
from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# =====================================================================
# 1. Base Memory Provider Contract
# =====================================================================

class BaseMemoryProvider(ABC):
    """Abstract interface that all Memory Providers must implement."""

    @abstractmethod
    def read(self, category: str, key: str) -> Optional[Dict[str, Any]]:
        """Read data stored under a specific category and key."""
        pass

    @abstractmethod
    def write(self, category: str, key: str, data: Dict[str, Any]) -> None:
        """Write data under a specific category and key."""
        pass

    @abstractmethod
    def delete(self, category: str, key: str) -> None:
        """Delete data stored under a category and key."""
        pass


class LocalMemoryProvider(BaseMemoryProvider):
    """Concrete Memory Provider that persists context context to JSON files on local disk."""

    def __init__(self, memory_dir: str) -> None:
        self.memory_dir = os.path.abspath(memory_dir)

    def _get_path(self, category: str, key: str) -> str:
        # Create category folder structure
        folder = os.path.join(self.memory_dir, category)
        os.makedirs(folder, exist_ok=True)
        # Prevent path traversals
        safe_key = "".join([c if c.isalnum() or c in "-_" else "_" for c in key])
        return os.path.join(folder, f"{safe_key}.json")

    def read(self, category: str, key: str) -> Optional[Dict[str, Any]]:
        path = self._get_path(category, key)
        if not os.path.exists(path):
            return None
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            logger.error(f"Failed to read local memory for category '{category}' key '{key}': {e}")
            return None

    def write(self, category: str, key: str, data: Dict[str, Any]) -> None:
        path = self._get_path(category, key)
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, default=str)
        except Exception as e:
            logger.error(f"Failed to write local memory for category '{category}' key '{key}': {e}")

    def delete(self, category: str, key: str) -> None:
        path = self._get_path(category, key)
        if os.path.exists(path):
            try:
                os.remove(path)
            except Exception as e:
                logger.error(f"Failed to delete local memory for category '{category}' key '{key}': {e}")


# =====================================================================
# 2. Central Memory Engine
# =====================================================================

class MemoryEngine:
    """Orchestrates different contexts (conversation, repository, project state)."""

    def __init__(self, provider: BaseMemoryProvider) -> None:
        self.provider = provider

    # --- Conversation Memory ---
    def get_conversation(self, session_id: str) -> List[Dict[str, Any]]:
        """Retrieve conversation logs for a session."""
        data = self.provider.read("conversation", session_id)
        if not data:
            return []
        return data.get("messages", [])

    def add_message(self, session_id: str, role: str, content: str) -> None:
        """Add a single message to a conversation log."""
        messages = self.get_conversation(session_id)
        messages.append({"role": role, "content": content})
        self.provider.write("conversation", session_id, {"messages": messages})

    def clear_conversation(self, session_id: str) -> None:
        """Clear conversation logs for a session."""
        self.provider.delete("conversation", session_id)

    # --- Repository Memory ---
    def get_repository_index(self, repo_path: str) -> Optional[Dict[str, Any]]:
        """Retrieve cached analysis metrics for a repository."""
        key = self._hash_path(repo_path)
        return self.provider.read("repository", key)

    def save_repository_index(self, repo_path: str, index_data: Dict[str, Any]) -> None:
        """Cache analysis metrics for a repository."""
        key = self._hash_path(repo_path)
        self.provider.write("repository", key, {"path": repo_path, "index": index_data})

    # --- Working Memory ---
    def get_working_state(self, task_id: str) -> Dict[str, Any]:
        """Retrieve active workspace workflow/agent execution steps."""
        data = self.provider.read("working", task_id) or {}
        return data.get("state", {})

    def save_working_state(self, task_id: str, state: Dict[str, Any]) -> None:
        """Cache workflow/agent execution step states."""
        self.provider.write("working", task_id, {"state": state})

    # --- User Preferences ---
    def get_preferences(self) -> Dict[str, Any]:
        """Retrieve globally persisted user configurations."""
        data = self.provider.read("preferences", "global") or {}
        return data.get("settings", {})

    def save_preferences(self, settings: Dict[str, Any]) -> None:
        """Persist global user configurations."""
        self.provider.write("preferences", "global", {"settings": settings})

    def _hash_path(self, path: str) -> str:
        # Generate simple safe identifier for path
        import hashlib
        return hashlib.md5(os.path.abspath(path).encode("utf-8")).hexdigest()
