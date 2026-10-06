# Coding Standards and Architecture Rules

## Naming Conventions
- Use snake_case for all Python identifiers (variables, functions, modules)
- Class names should use PascalCase
- Constants should be UPPERCASE_SNAKE_CASE
- All service classes must have 'Service' suffix

## Packaging and Structure
- Project uses setuptools with pyproject.toml for configuration
- Modules are organized in a flat structure under the 'graphify' root
- Tools and scripts are placed in dedicated 'tools' and 'scripts' directories
- Tests are in a separate 'tests' directory following standard Python test conventions

## Imports
- All imports should be explicit and at the top of the file
- Use relative imports within the package
- External dependencies should be imported after standard library imports

## Component Layout
- Core logic is in the main 'graphify' module
- Platform-specific artifacts are generated via 'tools/skillgen'
- Configuration files like 'platforms.toml' define build-time platform manifests
- The 'tools' directory contains auxiliary scripts and generation tools

## Documentation
- README.md provides project description and usage information
- Documentation is in the 'docs' directory
- Inline comments should be clear and concise

## Testing
- Unit tests are written using pytest framework
- Tests follow standard Python naming conventions (test_*.py)
- Test files are located in the 'tests' directory