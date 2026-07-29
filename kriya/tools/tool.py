import logging
from abc import ABC, abstractmethod
from typing import Type, Any

logger = logging.getLogger(__name__)

# Let's import Pydantic components
from pydantic import BaseModel, ValidationError

class ToolExecutionError(Exception):
    """Base class for exceptions raised during tool execution."""
    pass

class BaseTool(ABC):
    """Base class that all Kriya tools must implement to fit into the framework."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Unique identifier name of the tool."""
        pass

    @property
    @abstractmethod
    def description(self) -> str:
        """Detailed description of the tool and its behavior."""
        pass

    @property
    @abstractmethod
    def arguments_schema(self) -> Type[BaseModel]:
        """Pydantic model class representing arguments structure and validation."""
        pass

    @property
    def requires_confirmation(self) -> bool:
        """Whether callers (e.g. the CLI) should prompt for confirmation before executing this tool."""
        return False

    async def execute(self, **kwargs: Any) -> Any:
        """Validates inputs against arguments_schema and calls _run."""
        try:
            # Validate input using Pydantic
            validated = self.arguments_schema(**kwargs)
            logger.info(f"Executing tool '{self.name}' with arguments: {validated.model_dump()}")
            return await self._run(validated)
        except ValidationError as ve:
            logger.error(f"Validation failed for tool '{self.name}': {ve}")
            raise ToolExecutionError(f"Argument validation failed: {ve.errors()}") from ve
        except Exception as e:
            logger.error(f"Error during execution of tool '{self.name}': {e}", exc_info=True)
            raise ToolExecutionError(f"Tool execution failed: {e}") from e

    @abstractmethod
    async def _run(self, args: Any) -> Any:
        """Internal execution logic implementation."""
        pass
