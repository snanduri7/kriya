# Kriya Coding Standards and Architecture Guide

## Naming Conventions
- **Classes**: Use PascalCase (e.g., `Kernel`, `ComponentRegistry`).
- **Functions/Methods**: Use snake_case (e.g., `start`, `register`).
- **Variables**: Use snake_case (e.g., `config`, `_running`).
- **Constants**: Use UPPERCASE_WITH_UNDERSCORES (not explicitly shown in samples).

## Packaging and Imports
- Organize imports into three sections: standard library, third-party libraries, and local application/library-specific imports.
- Separate each section with a blank line.
- Within each section, sort imports alphabetically.
- Use absolute imports for clarity.

## Component Layouts
- Each component should have its own file or module if it grows large enough to warrant separation (e.g., `kernel.py`, `registry.py`).
- Classes and functions should be well-documented using docstrings.
- Follow a consistent structure within classes: constants, fields, methods (public first, then private).

## Logging
- Use the `logging` module for logging messages.
- Configure loggers at the module level with `logger = logging.getLogger(__name__)`.
- Use appropriate log levels (`info`, `warning`, `error`, etc.) based on the severity of the message.